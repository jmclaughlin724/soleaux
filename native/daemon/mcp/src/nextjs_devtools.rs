//! Capability-driven Next.js DevTools integration.
//!
//! The discovery seed is the `next-devtools` gateway backend registered in
//! `soleaux.toml`; nothing is assumed about the backend beyond that
//! registration. The probe is spawn-free and degrades truthfully with a
//! recorded reason at every missing capability: registration, enablement,
//! transport, executable, authentication, indexed applications, and the
//! version gate against the embedded matrix pins. Only an explicit
//! [`attach_runtime_evidence`] call opens the backend session, and the session
//! itself is capability-driven: `init` and `nextjs_index` run only when the
//! backend advertises them, and every runtime call surfaced afterwards comes
//! from the advertisement in the `nextjs_index` response, never from an
//! assumed universal tool list. No code path performs a network fetch; the
//! backend is a locally configured command.

use crate::gateway::{self, GatewaySession, GatewayTransport};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use soleaux_intelligence::nextjs::NextIndex;
use soleaux_intelligence::nextjs_oxc::VERSION_GATE_FULL;
use std::path::Path;

pub const NEXT_DEVTOOLS_BACKEND: &str = "next-devtools";
pub const DEVTOOLS_INIT_TOOL: &str = "init";
pub const DEVTOOLS_INDEX_TOOL: &str = "nextjs_index";

/// Spawn-free capability probe over the gateway registration seed.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DevtoolsCapability {
    /// Where discovery is seeded from.
    pub seed: String,
    pub backend: Option<String>,
    pub registered: bool,
    pub enabled: bool,
    pub transport_supported: bool,
    pub executable_available: bool,
    pub authenticated: bool,
    /// `full` when every indexed application passes the matrix version gate,
    /// `safe` otherwise.
    pub version_gate_mode: String,
    pub capable: bool,
    /// The first missing capability, recorded truthfully.
    pub reason: Option<String>,
}

pub fn probe_devtools(root: &Path, index: &NextIndex) -> DevtoolsCapability {
    let seed = format!("soleaux.toml:[mcp.{NEXT_DEVTOOLS_BACKEND}]");
    let mut capability = DevtoolsCapability {
        seed,
        backend: None,
        registered: false,
        enabled: false,
        transport_supported: false,
        executable_available: false,
        authenticated: false,
        version_gate_mode: version_gate_mode(index).to_string(),
        capable: false,
        reason: None,
    };
    let status = match gateway::backend_status(root) {
        Ok(statuses) => statuses
            .into_iter()
            .find(|status| status.backend.name == NEXT_DEVTOOLS_BACKEND),
        Err(error) => {
            capability.reason = Some(format!("gateway discovery failed: {error}"));
            return capability;
        }
    };
    let Some(status) = status else {
        capability.reason = Some(format!(
            "the {NEXT_DEVTOOLS_BACKEND} backend is not registered in soleaux.toml"
        ));
        return capability;
    };
    capability.backend = Some(status.backend.name.clone());
    capability.registered = true;
    capability.enabled = status.backend.enabled;
    capability.transport_supported = status.backend.transport == GatewayTransport::Stdio;
    capability.executable_available = status.available;
    capability.authenticated = status.authenticated;
    capability.reason = if !capability.enabled {
        Some(format!(
            "the {NEXT_DEVTOOLS_BACKEND} backend is disabled by workspace configuration"
        ))
    } else if !capability.transport_supported {
        Some(format!(
            "the {NEXT_DEVTOOLS_BACKEND} backend is not a stdio backend; capability sessions require stdio"
        ))
    } else if !capability.executable_available {
        Some(format!(
            "the configured {NEXT_DEVTOOLS_BACKEND} executable is unavailable"
        ))
    } else if !capability.authenticated {
        Some(format!(
            "the {NEXT_DEVTOOLS_BACKEND} backend requires CLI-mediated login"
        ))
    } else if index.applications.is_empty() {
        Some("the workspace contains no indexed Next.js application".into())
    } else if capability.version_gate_mode != VERSION_GATE_FULL {
        Some(version_gate_reason(index))
    } else {
        None
    };
    capability.capable = capability.reason.is_none();
    capability
}

fn version_gate_mode(index: &NextIndex) -> &'static str {
    if !index.version_gates.is_empty()
        && index
            .version_gates
            .iter()
            .all(|gate| gate.mode == VERSION_GATE_FULL)
    {
        "full"
    } else {
        "safe"
    }
}

fn version_gate_reason(index: &NextIndex) -> String {
    let unpinned = index
        .version_gates
        .iter()
        .filter(|gate| gate.mode != VERSION_GATE_FULL)
        .map(|gate| gate.app_root.as_str())
        .collect::<Vec<_>>();
    if unpinned.is_empty() {
        "no application passed the matrix version gate".to_string()
    } else {
        format!(
            "safe mode: the Next.js version of {} is not pinned by the embedded matrix",
            unpinned.join(", ")
        )
    }
}

/// Runtime evidence discovered through one capability-driven backend session.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DevtoolsRuntime {
    /// Tools the backend advertised through `tools/list`.
    pub advertised_tools: Vec<String>,
    pub init_completed: bool,
    /// Running development servers reported by `nextjs_index`.
    pub servers: Vec<Value>,
    /// Runtime calls advertised per server inside the `nextjs_index` response.
    pub advertised_runtime_calls: Vec<String>,
    /// The parsed `nextjs_index` payload, when the backend returned one.
    pub index_payload: Option<Value>,
    pub degraded: bool,
    pub reason: Option<String>,
}

impl DevtoolsRuntime {
    fn degraded(advertised_tools: Vec<String>, init_completed: bool, reason: String) -> Self {
        Self {
            advertised_tools,
            init_completed,
            servers: Vec::new(),
            advertised_runtime_calls: Vec::new(),
            index_payload: None,
            degraded: true,
            reason: Some(reason),
        }
    }
}

/// Open the backend session and run `init` → `nextjs_index`, then read the
/// advertised runtime calls out of the response. Transport and protocol
/// faults are errors; missing advertisements are truthful degradations.
pub async fn discover_runtime(root: &Path) -> anyhow::Result<DevtoolsRuntime> {
    let mut session = GatewaySession::open(root, NEXT_DEVTOOLS_BACKEND).await?;
    let advertised_tools = session.advertised_tools().to_vec();
    for required in [DEVTOOLS_INIT_TOOL, DEVTOOLS_INDEX_TOOL] {
        if !session.advertises(required) {
            session.close().await;
            return Ok(DevtoolsRuntime::degraded(
                advertised_tools,
                false,
                format!(
                    "the {NEXT_DEVTOOLS_BACKEND} backend does not advertise the {required} tool"
                ),
            ));
        }
    }
    let init = session.call(DEVTOOLS_INIT_TOOL, Value::Object(Default::default()));
    if let Err(error) = init.await {
        session.close().await;
        return Ok(DevtoolsRuntime::degraded(
            advertised_tools,
            false,
            format!("the advertised {DEVTOOLS_INIT_TOOL} call failed: {error}"),
        ));
    }
    let index_result = session
        .call(DEVTOOLS_INDEX_TOOL, Value::Object(Default::default()))
        .await;
    session.close().await;
    let index_result = match index_result {
        Ok(result) => result,
        Err(error) => {
            return Ok(DevtoolsRuntime::degraded(
                advertised_tools,
                true,
                format!("the advertised {DEVTOOLS_INDEX_TOOL} call failed: {error}"),
            ));
        }
    };
    let payload = tool_result_payload(&index_result);
    let servers = payload.as_ref().map(index_servers).unwrap_or_default();
    let advertised_runtime_calls = servers
        .iter()
        .flat_map(|server| server.get("tools").and_then(Value::as_array).into_iter())
        .flatten()
        .filter_map(|tool| {
            tool.get("name")
                .and_then(Value::as_str)
                .or_else(|| tool.as_str())
        })
        .map(str::to_string)
        .collect();
    Ok(DevtoolsRuntime {
        advertised_tools,
        init_completed: true,
        servers,
        advertised_runtime_calls,
        index_payload: payload,
        degraded: false,
        reason: None,
    })
}

/// The JSON payload of one MCP tool result: `structuredContent` when present,
/// otherwise the first text content block parsed as JSON.
fn tool_result_payload(result: &Value) -> Option<Value> {
    if let Some(structured) = result.get("structuredContent") {
        return Some(structured.clone());
    }
    let text = result
        .get("content")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|item| item.get("type").and_then(Value::as_str) == Some("text"))
        .and_then(|item| item.get("text"))
        .and_then(Value::as_str)?;
    serde_json::from_str(text).ok()
}

fn index_servers(payload: &Value) -> Vec<Value> {
    payload
        .get("servers")
        .and_then(Value::as_array)
        .cloned()
        .or_else(|| payload.as_array().cloned())
        .unwrap_or_default()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DevtoolsAttachment {
    pub capability: DevtoolsCapability,
    pub attached: bool,
    pub degradation_reason: Option<String>,
    pub runtime: Option<DevtoolsRuntime>,
}

/// Probe, then attach runtime evidence to the static index when every
/// capability condition holds and the backend reports a running server.
/// This is the only code path that flips `runtime_evidence_attached`.
pub async fn attach_runtime_evidence(root: &Path, index: &mut NextIndex) -> DevtoolsAttachment {
    let capability = probe_devtools(root, index);
    if !capability.capable {
        let reason = capability.reason.clone();
        return DevtoolsAttachment {
            capability,
            attached: false,
            degradation_reason: reason,
            runtime: None,
        };
    }
    match discover_runtime(root).await {
        Err(error) => DevtoolsAttachment {
            capability,
            attached: false,
            degradation_reason: Some(format!("the backend session failed: {error}")),
            runtime: None,
        },
        Ok(runtime) if runtime.degraded => {
            let reason = runtime.reason.clone();
            DevtoolsAttachment {
                capability,
                attached: false,
                degradation_reason: reason,
                runtime: Some(runtime),
            }
        }
        Ok(runtime) if runtime.servers.is_empty() => DevtoolsAttachment {
            capability,
            attached: false,
            degradation_reason: Some(
                "init and nextjs_index completed but no running development server was reported"
                    .into(),
            ),
            runtime: Some(runtime),
        },
        Ok(runtime) => {
            index.runtime_evidence_attached = true;
            DevtoolsAttachment {
                capability,
                attached: true,
                degradation_reason: None,
                runtime: Some(runtime),
            }
        }
    }
}

/// Build the static index for `root`, then run the full capability-driven
/// attachment flow. This is the explicit entry point the CLI exposes.
pub async fn runtime_report(root: &Path) -> anyhow::Result<(NextIndex, DevtoolsAttachment)> {
    let mut index = soleaux_intelligence::nextjs::index_nextjs(root)?;
    let attachment = attach_runtime_evidence(root, &mut index).await;
    Ok((index, attachment))
}

#[cfg(test)]
mod tests {
    use super::*;
    use soleaux_intelligence::nextjs::index_nextjs;
    use std::fs;
    use std::path::PathBuf;
    use tempfile::tempdir;

    fn write_pinned_next_app(root: &Path) {
        fs::create_dir_all(root.join("app")).expect("app dir");
        fs::write(root.join("next.config.mjs"), "export default {};").expect("config");
        fs::write(
            root.join("app/page.tsx"),
            "export default function Page() { return null; }",
        )
        .expect("page");
        fs::write(
            root.join("package.json"),
            "{\"name\":\"fixture\",\"dependencies\":{\"next\":\"16.3.0-preview.6\"}}",
        )
        .expect("manifest");
    }

    fn write_backend_config(root: &Path, command: &[&str]) {
        let rendered = command
            .iter()
            .map(|part| format!("\"{part}\""))
            .collect::<Vec<_>>()
            .join(", ");
        fs::write(
            root.join("soleaux.toml"),
            format!("[mcp.{NEXT_DEVTOOLS_BACKEND}]\ncommand = [{rendered}]\n"),
        )
        .expect("configuration");
    }

    /// A scripted stdio MCP backend. The session protocol is line-ordered and
    /// deterministic (initialize, initialized notification, tools/list, then
    /// calls with sequential ids), so the fake responds by line number and
    /// never parses its input.
    #[cfg(unix)]
    fn write_fake_backend(root: &Path, tools_line: &str, index_line: &str) -> PathBuf {
        let script = root.join("fake-next-devtools.sh");
        let body = format!(
            "#!/bin/sh\nn=0\nwhile IFS= read -r _line; do\n  n=$((n+1))\n  case $n in\n    1) printf '%s\\n' '{{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{{\"tools\":{{}}}},\"serverInfo\":{{\"name\":\"scripted-next-devtools\",\"version\":\"0.4.0\"}}}}}}' ;;\n    2) : ;;\n    3) printf '%s\\n' '{tools}' ;;\n    4) printf '%s\\n' '{{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{{\"content\":[{{\"type\":\"text\",\"text\":\"knowledge base initialized\"}}]}}}}' ;;\n    5) printf '%s\\n' '{index}' ;;\n  esac\ndone\n",
            tools = tools_line,
            index = index_line,
        );
        fs::write(&script, body).expect("fake backend script");
        script
    }

    #[cfg(unix)]
    const FULL_TOOLS_LINE: &str = r#"{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"init"},{"name":"nextjs_index"},{"name":"nextjs_docs"},{"name":"browser_eval"}]}}"#;

    #[cfg(unix)]
    const ONE_SERVER_INDEX_LINE: &str = r#"{"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text","text":"{\"servers\":[{\"port\":3000,\"url\":\"http://localhost:3000\",\"tools\":[{\"name\":\"get_routes\"},{\"name\":\"get_errors\"},{\"name\":\"compile_route\"}]}]}"}]}}"#;

    #[cfg(unix)]
    const ZERO_SERVER_INDEX_LINE: &str = r#"{"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text","text":"{\"servers\":[]}"}]}}"#;

    #[test]
    fn probe_reports_missing_registration_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        let index = index_nextjs(directory.path()).expect("index");
        let capability = probe_devtools(directory.path(), &index);
        assert!(!capability.registered);
        assert!(!capability.capable);
        assert!(
            capability
                .reason
                .as_deref()
                .is_some_and(|reason| reason.contains("not registered"))
        );
    }

    #[test]
    fn probe_reports_disabled_backend_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        fs::write(
            directory.path().join("soleaux.toml"),
            format!("[mcp.{NEXT_DEVTOOLS_BACKEND}]\ncommand = [\"/bin/sh\"]\nenabled = false\n"),
        )
        .expect("configuration");
        let index = index_nextjs(directory.path()).expect("index");
        let capability = probe_devtools(directory.path(), &index);
        assert!(capability.registered);
        assert!(!capability.enabled);
        assert!(!capability.capable);
        assert!(
            capability
                .reason
                .as_deref()
                .is_some_and(|reason| reason.contains("disabled"))
        );
    }

    #[test]
    fn probe_reports_unavailable_executable_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        write_backend_config(directory.path(), &["/nonexistent/next-devtools-mcp"]);
        let index = index_nextjs(directory.path()).expect("index");
        let capability = probe_devtools(directory.path(), &index);
        assert!(capability.registered);
        assert!(!capability.executable_available);
        assert!(!capability.capable);
        assert!(
            capability
                .reason
                .as_deref()
                .is_some_and(|reason| reason.contains("unavailable"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn probe_blocks_safe_mode_version_gate_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        // Replace the pinned manifest with an unpinned Next.js version.
        fs::write(
            directory.path().join("package.json"),
            "{\"name\":\"fixture\",\"dependencies\":{\"next\":\"15.5.0\"}}",
        )
        .expect("manifest");
        write_backend_config(directory.path(), &["/bin/sh"]);
        let index = index_nextjs(directory.path()).expect("index");
        let capability = probe_devtools(directory.path(), &index);
        assert!(capability.registered);
        assert!(capability.executable_available);
        assert_eq!(capability.version_gate_mode, "safe");
        assert!(!capability.capable);
        assert!(
            capability
                .reason
                .as_deref()
                .is_some_and(|reason| reason.contains("safe mode"))
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn scripted_backend_advertised_call_path_attaches_runtime_evidence() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        let script = write_fake_backend(directory.path(), FULL_TOOLS_LINE, ONE_SERVER_INDEX_LINE);
        write_backend_config(
            directory.path(),
            &["/bin/sh", script.to_str().expect("utf8 path")],
        );
        let mut index = index_nextjs(directory.path()).expect("index");
        assert!(!index.runtime_evidence_attached);
        let attachment = attach_runtime_evidence(directory.path(), &mut index).await;
        assert!(attachment.capability.capable, "{:?}", attachment.capability);
        assert!(attachment.attached, "{:?}", attachment.degradation_reason);
        assert!(index.runtime_evidence_attached);
        let runtime = attachment.runtime.expect("runtime evidence");
        assert!(runtime.init_completed);
        assert_eq!(
            runtime.advertised_tools,
            vec!["init", "nextjs_index", "nextjs_docs", "browser_eval"]
        );
        assert_eq!(runtime.servers.len(), 1);
        assert_eq!(
            runtime.advertised_runtime_calls,
            vec!["get_routes", "get_errors", "compile_route"]
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn scripted_backend_without_nextjs_index_degrades_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        let tools_line = r#"{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"init"},{"name":"nextjs_docs"}]}}"#;
        let script = write_fake_backend(directory.path(), tools_line, ZERO_SERVER_INDEX_LINE);
        write_backend_config(
            directory.path(),
            &["/bin/sh", script.to_str().expect("utf8 path")],
        );
        let mut index = index_nextjs(directory.path()).expect("index");
        let attachment = attach_runtime_evidence(directory.path(), &mut index).await;
        assert!(attachment.capability.capable);
        assert!(!attachment.attached);
        assert!(!index.runtime_evidence_attached);
        assert!(
            attachment
                .degradation_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("does not advertise the nextjs_index tool")),
            "{:?}",
            attachment.degradation_reason
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn scripted_backend_with_zero_servers_degrades_truthfully() {
        let directory = tempdir().expect("tempdir");
        write_pinned_next_app(directory.path());
        let script = write_fake_backend(directory.path(), FULL_TOOLS_LINE, ZERO_SERVER_INDEX_LINE);
        write_backend_config(
            directory.path(),
            &["/bin/sh", script.to_str().expect("utf8 path")],
        );
        let mut index = index_nextjs(directory.path()).expect("index");
        let attachment = attach_runtime_evidence(directory.path(), &mut index).await;
        assert!(attachment.capability.capable);
        assert!(!attachment.attached);
        assert!(!index.runtime_evidence_attached);
        assert!(
            attachment
                .degradation_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("no running development server")),
            "{:?}",
            attachment.degradation_reason
        );
        let runtime = attachment.runtime.expect("runtime evidence");
        assert!(runtime.init_completed);
        assert!(runtime.servers.is_empty());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn gateway_session_refuses_unadvertised_calls() {
        let directory = tempdir().expect("tempdir");
        let tools_line = r#"{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"init"}]}}"#;
        let script = write_fake_backend(directory.path(), tools_line, ZERO_SERVER_INDEX_LINE);
        write_backend_config(
            directory.path(),
            &["/bin/sh", script.to_str().expect("utf8 path")],
        );
        let mut session = GatewaySession::open(directory.path(), NEXT_DEVTOOLS_BACKEND)
            .await
            .expect("session");
        assert!(session.advertises("init"));
        let error = session
            .call("nextjs_index", Value::Object(Default::default()))
            .await
            .expect_err("unadvertised call must be refused");
        assert!(error.to_string().contains("does not advertise"));
        session.close().await;
    }
}
