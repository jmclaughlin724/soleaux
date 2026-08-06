#!/usr/bin/env python3
"""Apply deterministic PR #38 runtime-admission and revalidation fixes."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


compatibility = read("native/daemon/ipc/src/compatibility.rs")
probe_start = compatibility.index(
    '#[derive(Debug, Clone, Deserialize)]\n#[serde(rename_all = "camelCase")]\nstruct ProbeEvidence'
)
probe_end = compatibility.index(
    '#[derive(Debug, Clone, Serialize, PartialEq, Eq)]', probe_start
)
compatibility = compatibility[:probe_start] + compatibility[probe_end:]

canonical_start = compatibility.index("fn canonical_probe_sha256")
canonical_end = compatibility.index(
    "pub fn validate_client_capability_matrix", canonical_start
)
compatibility = compatibility[:canonical_start] + compatibility[canonical_end:]

external_start = compatibility.index("    if !version.mutation_eligible {")
external_end = compatibility.index("\n#[allow(clippy::too_many_arguments)]", external_start)
external = r'''    let passed_signals = capabilities
        .get("soleauxProbe")
        .and_then(|value| value.get("passedSignals"))
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    Ok(read_only_decision(
        ClientCompatibilityState::Unprobed,
        Some(platform.id.clone()),
        matrix_sha256,
        version.mutation_eligible,
        Some(platform.version_policy.clone()),
        unique_sorted(&version.required_binary_signals),
        unique_sorted(&passed_signals),
        "external probe reports are archival evidence only; runtime writes require a daemon-trusted admission receipt verifier",
    ))
}
'''
compatibility = compatibility[:external_start] + external + compatibility[external_end:]

compatibility = replace_once(
    compatibility,
    "    let mut write_eligible = 0usize;\n",
    "",
    "write-eligible counter",
)
compatibility = replace_once(
    compatibility,
    '''            if version.mutation_eligible {
                write_eligible += 1;
                if platform.id != "generic_mcp_host" || platform.version_policy != "exact" {
                    bail!("only the exact generic MCP host probe may be mutation eligible");
                }
            }
''',
    '''            if version.mutation_eligible {
                bail!(
                    "external client matrix entries cannot be mutation eligible until a daemon-trusted receipt verifier is implemented"
                );
            }
''',
    "external mutation eligibility",
)
compatibility = replace_once(
    compatibility,
    '''    if write_eligible != 1 {
        bail!("exactly one capability matrix entry must be mutation eligible");
    }
''',
    "",
    "write-entry cardinality",
)

test_start = compatibility.index("#[cfg(test)]\nmod tests {")
compatibility_tests = r'''#[cfg(test)]
mod tests {
    use super::*;

    const PROTOCOL: &str = "soleaux.client/v1";

    #[test]
    fn embedded_matrix_is_valid_and_has_no_external_write_entry() {
        validate_client_capability_matrix().expect("valid matrix");
        assert!(is_lower_hex_digest(&client_capability_matrix_sha256()));
        let summary = client_capability_matrix_summary().expect("summary");
        assert_eq!(summary["publicToolCeiling"], 12);
        assert_eq!(summary["productionClaimAllowed"], false);
        assert_eq!(summary["platforms"].as_array().map(Vec::len), Some(6));
        assert_eq!(summary["writeEligible"].as_array().map(Vec::len), Some(0));
    }

    #[test]
    fn exact_internal_cli_is_the_only_write_capable_path() {
        let decision = evaluate_client_compatibility(
            ClientKind::Cli,
            env!("CARGO_PKG_VERSION"),
            PROTOCOL,
            PROTOCOL,
            &json!({}),
            &json!({}),
        )
        .expect("internal CLI decision");
        assert_eq!(decision.state, ClientCompatibilityState::Verified);
        assert!(decision.write_capable);
        assert_eq!(decision.platform.as_deref(), Some("soleaux_cli"));
    }

    #[test]
    fn caller_generated_generic_probe_never_grants_runtime_writes() {
        let capabilities = json!({
            "soleauxProbe":{
                "schemaVersion":CLIENT_CAPABILITY_PROBE_SCHEMA_VERSION,
                "platform":"generic_mcp_host",
                "clientVersion":"mcp-2025-11-25",
                "matrixSha256":client_capability_matrix_sha256(),
                "status":"pass",
                "mutationEligible":true,
                "passedSignals":[
                    "initialize",
                    "tools_list",
                    "context_compile",
                    "registry_registration",
                    "read_write_binding",
                    "tool_ceiling"
                ],
                "evidenceSha256":"a".repeat(64),
            }
        });
        let decision = evaluate_client_compatibility(
            ClientKind::Adapter,
            "mcp-2025-11-25",
            PROTOCOL,
            PROTOCOL,
            &capabilities,
            &json!({"platform":"generic_mcp_host"}),
        )
        .expect("generic host decision");
        assert_eq!(decision.state, ClientCompatibilityState::Unprobed);
        assert!(!decision.write_capable);
        assert!(decision.reason.contains("daemon-trusted"));
    }

    #[test]
    fn vendor_clients_unknown_versions_and_kind_mismatches_remain_read_only() {
        let metadata = json!({"platform":"claude_code"});
        let exact = evaluate_client_compatibility(
            ClientKind::Adapter,
            "2.1.223",
            PROTOCOL,
            PROTOCOL,
            &json!({"soleauxProbe":{"status":"pass"}}),
            &metadata,
        )
        .expect("Claude decision");
        assert_eq!(exact.state, ClientCompatibilityState::Unprobed);
        assert!(!exact.write_capable);

        let unknown = evaluate_client_compatibility(
            ClientKind::Adapter,
            "999.0.0",
            PROTOCOL,
            PROTOCOL,
            &json!({}),
            &metadata,
        )
        .expect("unknown decision");
        assert_eq!(unknown.state, ClientCompatibilityState::Unprobed);
        assert!(!unknown.write_capable);

        let mismatch = evaluate_client_compatibility(
            ClientKind::Desktop,
            "2.1.223",
            PROTOCOL,
            PROTOCOL,
            &json!({}),
            &metadata,
        )
        .expect("kind mismatch");
        assert_eq!(mismatch.state, ClientCompatibilityState::Unsupported);
        assert!(!mismatch.write_capable);
    }
}
'''
compatibility = compatibility[:test_start] + compatibility_tests
write("native/daemon/ipc/src/compatibility.rs", compatibility)

registry = read("native/daemon/ipc/src/registry.rs")
registry = replace_once(
    registry,
    '''    CanonicalEntityInput, ClientAccessMode, ClientKind, ClientRegistrationPayload,
    ClientWorkspaceBindingPayload, LOCKED_CONTEXT_PACKET_SHA256, LOCKED_PROFILE_SHA256,
''',
    '''    CanonicalEntityInput, CanonicalRecord, ClientAccessMode, ClientCompatibilityState,
    ClientKind, ClientRegistrationPayload, ClientWorkspaceBindingPayload,
    LOCKED_CONTEXT_PACKET_SHA256, LOCKED_PROFILE_SHA256,
''',
    "registry imports",
)

heartbeat_start = registry.index("pub(crate) fn heartbeat_client(")
heartbeat_end = registry.index("\npub(crate) fn list_clients(", heartbeat_start)
heartbeat = r'''pub(crate) fn heartbeat_client(
    state: &StateStore,
    client_id: Uuid,
    ttl_ms: u64,
    capabilities: Option<Value>,
) -> Result<Value> {
    validate_ttl(ttl_ms)?;
    if let Some(capabilities) = &capabilities {
        validate_json_field(capabilities, "client capabilities")?;
    }
    let (client, compatibility) =
        revalidate_client(state, client_id, capabilities, Some(ttl_ms), true)?;
    let bindings = state.registry_bindings(false, None, REGISTRY_PAGE_LIMIT_DEFAULT, unix_ms())?;
    let client_bindings = bindings
        .items
        .into_iter()
        .filter(|binding| binding.payload.client_id == client_id)
        .collect::<Vec<_>>();
    let (bindings, binding_count, bindings_truncated) = bounded_children(client_bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-heartbeat/v1",
        "client":client,
        "bindings":bindings,
        "bindingCount":binding_count,
        "bindingsTruncated":bindings_truncated,
        "writeCapable":compatibility.write_capable,
        "compatibilityState":compatibility.state,
        "compatibility":compatibility,
        "productionClaimAllowed":false,
    }))
}
'''
registry = registry[:heartbeat_start] + heartbeat + registry[heartbeat_end:]

registry = replace_once(
    registry,
    '''    validate_json_field(&capabilities, "binding capabilities")?;
    validate_json_field(&metadata, "binding metadata")?;
    let now = unix_ms();
''',
    '''    validate_json_field(&capabilities, "binding capabilities")?;
    validate_json_field(&metadata, "binding metadata")?;
    let (_client, compatibility) = revalidate_client(state, client_id, None, None, false)?;
    if access_mode == ClientAccessMode::ReadWrite && !compatibility.write_capable {
        bail!(
            "read-write binding requires a currently verified daemon-trusted client compatibility decision"
        );
    }
    let now = unix_ms();
''',
    "binding revalidation",
)

helper_marker = "fn validate_json_field(value: &Value, label: &str) -> Result<()> {"
helper = r'''fn revalidate_client(
    state: &StateStore,
    client_id: Uuid,
    capabilities: Option<Value>,
    ttl_ms: Option<u64>,
    touch_last_seen: bool,
) -> Result<(
    CanonicalRecord<ClientRegistrationPayload>,
    crate::compatibility::CompatibilityDecision,
)> {
    let existing = state
        .get::<ClientRegistrationPayload>(client_id)?
        .context("client registration does not exist")?;
    let now = unix_ms();
    if existing.tombstoned_at_unix_ms.is_some()
        || existing.state != "connected"
        || existing
            .expires_at_unix_ms
            .is_some_and(|expires| expires <= now)
    {
        bail!("client registration is not active");
    }

    let mut payload = existing.payload.clone();
    if let Some(capabilities) = capabilities {
        payload.capabilities = capabilities;
    }
    let compatibility = evaluate_client_compatibility(
        payload.client_kind,
        &payload.client_version,
        &payload.protocol_version,
        CLIENT_PROTOCOL_VERSION,
        &payload.capabilities,
        &payload.metadata,
    )?;
    let previous_capabilities = existing.payload.capabilities.clone();
    let previous_state = payload.compatibility_state;
    let previous_write_capable = payload.write_capable;
    payload.compatibility_state = compatibility.state;
    payload.write_capable = compatibility.write_capable;
    payload.connection_state = "connected".to_string();
    if touch_last_seen {
        payload.last_seen_at_unix_ms = now;
    }
    let expires_at_unix_ms = ttl_ms
        .map(|ttl| expiration(now, ttl))
        .transpose()?
        .or(existing.expires_at_unix_ms);
    let changed = touch_last_seen
        || ttl_ms.is_some()
        || payload.capabilities != previous_capabilities
        || previous_state != payload.compatibility_state
        || previous_write_capable != payload.write_capable;
    if !changed {
        return Ok((existing, compatibility));
    }

    let origin_platform = existing
        .origin_platform
        .clone()
        .context("client registration omitted its origin platform")?;
    let native_id = existing
        .native_id
        .clone()
        .context("client registration omitted its native identity")?;
    let input = CanonicalEntityInput {
        id: Some(existing.id),
        workspace_id: existing.workspace_id,
        parent_id: existing.parent_id,
        origin_platform: Some(origin_platform),
        native_id: Some(native_id),
        state: "connected".to_string(),
        sensitivity: existing.sensitivity,
        idempotency_key: existing.idempotency_key.clone(),
        expected_revision: None,
        expires_at_unix_ms,
        payload,
    };
    let result = state.registry_register_client(input)?;
    Ok((result.client, compatibility))
}

'''
registry = replace_once(
    registry,
    helper_marker,
    helper + helper_marker,
    "client revalidation helper",
)

if "mod compatibility_regression_tests" not in registry:
    registry += r'''

#[cfg(test)]
mod compatibility_regression_tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn bind_revalidates_and_downgrades_a_stale_external_client() {
        let directory = tempdir().expect("tempdir");
        let workspace_path = directory.path().join("workspace");
        fs::create_dir_all(&workspace_path).expect("workspace");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace = register_workspace(
            &state,
            workspace_path.to_str().expect("utf8"),
            Some("fixture".to_string()),
            WorkspaceTrustState::Trusted,
            json!({}),
        )
        .expect("workspace registration");
        let workspace_id = Uuid::parse_str(
            workspace["workspace"]["id"]
                .as_str()
                .expect("workspace id"),
        )
        .expect("workspace uuid");

        let now = unix_ms();
        let payload = ClientRegistrationPayload {
            client_kind: ClientKind::Adapter,
            instance_id: "stale-external".to_string(),
            display_name: "stale external".to_string(),
            client_version: "mcp-2025-11-25".to_string(),
            protocol_version: CLIENT_PROTOCOL_VERSION.to_string(),
            connection_state: "connected".to_string(),
            compatibility_state: ClientCompatibilityState::Verified,
            write_capable: true,
            last_seen_at_unix_ms: now,
            capabilities: json!({
                "soleauxProbe":{
                    "status":"pass",
                    "passedSignals":[
                        "initialize",
                        "tools_list",
                        "context_compile",
                        "registry_registration",
                        "read_write_binding",
                        "tool_ceiling"
                    ]
                }
            }),
            metadata: json!({"platform":"generic_mcp_host"}),
        };
        let mut input = CanonicalEntityInput::active(payload);
        input.state = "connected".to_string();
        input.origin_platform = Some(CLIENT_ORIGIN.to_string());
        input.native_id = Some("adapter:stale-external".to_string());
        input.idempotency_key = Some("client:adapter:stale-external".to_string());
        input.expires_at_unix_ms = Some(expiration(now, 60_000).expect("expiration"));
        let stale = state
            .registry_register_client(input)
            .expect("stale cached registration")
            .client;
        assert!(stale.payload.write_capable);

        let error = bind_client_workspace(
            &state,
            stale.id,
            workspace_id,
            ClientAccessMode::ReadWrite,
            json!({}),
            json!({}),
        )
        .expect_err("external stale compatibility must be rejected");
        assert!(format!("{error:#}").contains("daemon-trusted"));

        let refreshed = state
            .get::<ClientRegistrationPayload>(stale.id)
            .expect("read")
            .expect("client");
        assert_eq!(
            refreshed.payload.compatibility_state,
            ClientCompatibilityState::Unprobed
        );
        assert!(!refreshed.payload.write_capable);

        let read_only = bind_client_workspace(
            &state,
            stale.id,
            workspace_id,
            ClientAccessMode::ReadOnly,
            json!({}),
            json!({}),
        )
        .expect("read-only binding");
        assert_eq!(read_only["binding"]["payload"]["accessMode"], "read_only");
    }
}
'''
write("native/daemon/ipc/src/registry.rs", registry)

matrix_path = ROOT / "native/contracts/client-capability-matrix-v1.json"
matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
for platform in matrix["platforms"]:
    for version in platform["versions"]:
        version["mutationEligible"] = False
matrix_path.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

print("security track v2 applied")
