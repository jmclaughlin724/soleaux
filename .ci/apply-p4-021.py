#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"{label}: start marker missing")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{label}: end marker missing")
    return text[:start_index] + replacement + text[end_index:]


# Keep the shared crate usable from the native workspace and the standalone
# telemetry daemon without adding a second implementation.
path = "native/Cargo.toml"
text = read(path)
text = replace_once(
    text,
    '  "daemon/intelligence",\n',
    '  "daemon/intelligence",\n  "daemon/redaction",\n',
    "native workspace redaction member",
)
write(path, text)

for path in [
    "native/daemon/intelligence/Cargo.toml",
    "native/daemon/mcp/Cargo.toml",
]:
    text = read(path)
    text = replace_once(
        text,
        "soleaux-storage = { path = \"../storage\" }\n",
        "soleaux-redaction = { path = \"../redaction\" }\nsoleaux-storage = { path = \"../storage\" }\n",
        f"{path} redaction dependency",
    )
    write(path, text)

path = "telemetry/daemon/Cargo.toml"
text = read(path)
text = replace_once(
    text,
    'serde_json = "1"\n',
    'serde_json = "1"\nsoleaux-redaction = { path = "../../native/daemon/redaction" }\n',
    "telemetry redaction dependency",
)
write(path, text)

# Fix a conservative portability edge in the newly added source before cargo
# formatting and compilation.
path = "native/daemon/redaction/src/lib.rs"
text = read(path)
text = replace_once(
    text,
    ".trim_end_matches([',', ';'])",
    ".trim_end_matches(|character| matches!(character, ',' | ';'))",
    "portable trim pattern",
)
write(path, text)

# Replace the legacy context redactor with the shared implementation.
path = "native/daemon/intelligence/src/context.rs"
text = read(path)
text = replace_once(
    text,
    "use serde_json::{Value, json};\n",
    "use serde_json::{Value, json};\nuse soleaux_redaction::redact_text;\n",
    "context redaction import",
)
text = replace_between(
    text,
    "pub fn redact_sensitive_text",
    "#[cfg(test)]",
    '''pub fn redact_sensitive_text(source: &str) -> (String, usize) {
    let redacted = redact_text(source);
    (redacted.value, redacted.count)
}

fn redact_sensitive_source(source: &str) -> (String, usize) {
    redact_sensitive_text(source)
}

''',
    "context legacy redactor",
)
write(path, text)

# Context Packet V2 must use the same prefix/header/URL/JWT/PEM logic even
# when the containing variable has an innocuous name.
path = "native/daemon/intelligence/src/context_v2.rs"
text = read(path)
text = replace_once(
    text,
    "use sha2::{Digest, Sha256};\n",
    "use sha2::{Digest, Sha256};\nuse soleaux_redaction::redact_text;\n",
    "context v2 redaction import",
)
text = replace_between(
    text,
    "fn redact_secret_like",
    "#[cfg(test)]",
    '''fn redact_secret_like(source: &str) -> (String, usize) {
    let redacted = redact_text(source);
    (redacted.value, redacted.count)
}

''',
    "context v2 legacy redactor",
)
text = replace_once(
    text,
    "    #[test]\n    fn duplicate_reference_uris_fail_closed() {\n",
    '''    #[test]
    fn context_v2_redacts_vendor_tokens_without_sensitive_variable_names() {
        let leaked = "ghp_abcdefghijklmnopqrstuvwxyz1234567890";
        let source = format!("export const harmless = '{leaked}';");
        let (redacted, count) = redact_secret_like(&source);
        assert_eq!(count, 1);
        assert!(!redacted.contains(leaked));
        assert!(redacted.contains("[REDACTED]"));
    }

    #[test]
    fn duplicate_reference_uris_fail_closed() {
''',
    "context v2 vendor-token regression",
)
write(path, text)

# Redact the entire public MCP envelope as a defense-in-depth final boundary.
path = "native/daemon/mcp/src/envelope.rs"
text = read(path)
text = replace_once(
    text,
    "use serde_json::{Value, json};\n",
    "use serde_json::{Value, json};\nuse soleaux_redaction::{redact_json_in_place, redact_text};\n",
    "MCP envelope redaction import",
)

success_start = text.index("    pub fn success(")
success_end = text.index("    pub fn error(", success_start)
success_region = text[success_start:success_end]
success_region = replace_once(
    success_region,
    "        Self {\n",
    "        let mut envelope = Self {\n",
    "success envelope construction",
)
success_region = replace_once(
    success_region,
    "            duration_us,\n        }\n    }\n\n",
    "            duration_us,\n        };\n        envelope.redact_in_place();\n        envelope\n    }\n\n",
    "success envelope finalization",
)
text = text[:success_start] + success_region + text[success_end:]

error_start = text.index("    pub fn error(")
error_end = text.index("}\n\npub fn provenance", error_start)
error_region = text[error_start:error_end]
error_region = replace_once(
    error_region,
    "        Self {\n",
    "        let mut envelope = Self {\n",
    "error envelope construction",
)
error_region = replace_once(
    error_region,
    "            duration_us,\n        }\n    }\n",
    '''            duration_us,
        };
        envelope.redact_in_place();
        envelope
    }

    fn redact_in_place(&mut self) {
        let mut count = redact_json_in_place(&mut self.data);
        if let Some(rows) = &mut self.rows {
            for row in rows {
                count = count.saturating_add(redact_json_in_place(row));
            }
        }
        for value in &mut self.evidence {
            count = count.saturating_add(redact_json_in_place(value));
        }
        if let Some(value) = &mut self.coverage {
            count = count.saturating_add(redact_json_in_place(value));
        }
        for warning in &mut self.warnings {
            let redacted = redact_text(warning);
            *warning = redacted.value;
            count = count.saturating_add(redacted.count);
        }
        for request in &mut self.suggested_next_requests {
            count = count.saturating_add(redact_json_in_place(request));
        }
        if let Some(value) = &mut self.error {
            count = count.saturating_add(redact_json_in_place(value));
        }
        count = count.saturating_add(redact_json_in_place(&mut self.provenance));
        if count > 0 {
            self.warnings.push(format!(
                "Soleaux redacted {count} secret-bearing value(s) at the public MCP boundary."
            ));
        }
    }
''',
    "error envelope finalization and shared boundary",
)
text = text[:error_start] + error_region + text[error_end:]

text += '''

#[cfg(test)]
mod redaction_tests {
    use super::*;

    #[test]
    fn public_envelopes_redact_nested_secrets_and_error_text() {
        let metadata = SuccessMetadata::repository("test", "test");
        let envelope = ToolEnvelopeV2::success(
            Uuid::from_u128(1),
            "/workspace",
            json!({
                "accessToken": "live-access-token-value",
                "message": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                "nested": [{"ordinary": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"}]
            }),
            Some(vec![json!({"cookie": "session=secret-cookie"})]),
            1,
            metadata,
        );
        let encoded = serde_json::to_string(&envelope).expect("serialize envelope");
        assert!(!encoded.contains("live-access-token-value"));
        assert!(!encoded.contains("abcdefghijklmnopqrstuvwxyz123456"));
        assert!(!encoded.contains("secret-cookie"));
        assert!(encoded.contains("[REDACTED]"));

        let envelope = ToolEnvelopeV2::error(
            Uuid::from_u128(1),
            "/workspace",
            "test",
            ToolError {
                error_type: "provider_error".to_string(),
                message: "failed with sk-proj-abcdefghijklmnopqrstuvwxyz1234567890".to_string(),
                retryable: false,
                details: json!({"authorization": "Basic c3VwZXItc2VjcmV0LXZhbHVl"}),
            },
            1,
        );
        let encoded = serde_json::to_string(&envelope).expect("serialize error envelope");
        assert!(!encoded.contains("sk-proj-"));
        assert!(!encoded.contains("c3VwZXItc2VjcmV0LXZhbHVl"));
    }
}
'''
write(path, text)

# Direct JSON-RPC errors bypass ToolEnvelopeV2, so they receive the same text
# redactor before serialization.
path = "native/daemon/mcp/src/lib.rs"
text = read(path)
text = replace_once(
    text,
    "use soleaux_intelligence::{\n",
    "use soleaux_intelligence::{\n",
    "stable intelligence import anchor",
)
text = replace_once(
    text,
    "use soleaux_storage::{IndexedFileRecord, Store, SymbolHit, SymbolRecord};\n",
    "use soleaux_redaction::redact_text;\nuse soleaux_storage::{IndexedFileRecord, Store, SymbolHit, SymbolRecord};\n",
    "MCP direct error redaction import",
)
text = replace_once(
    text,
    '''fn json_rpc_error(id: Value, code: i64, message: impl Into<String>) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message.into()}})
}
''',
    '''fn json_rpc_error(id: Value, code: i64, message: impl Into<String>) -> Value {
    let message = message.into();
    let redacted = redact_text(&message);
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":redacted.value}})
}
''',
    "direct JSON-RPC error redaction",
)
write(path, text)

# Apply the same native implementation before telemetry values are retained or
# returned to dashboard/mobile consumers.
path = "telemetry/daemon/src/main.rs"
text = read(path)
text = replace_once(
    text,
    "use serde_json::json;\n",
    "use serde_json::json;\nuse soleaux_redaction::{REDACTED, is_sensitive_key, redact_json_in_place, redact_text};\n",
    "telemetry redaction import",
)
text = replace_once(
    text,
    "async fn health() -> impl IntoResponse { Json(HealthResponse { status: \"ok\", service: \"soleaux-daemon\", protocol_version: 2 }) }\n",
    '''fn redact_string(value: &mut String) -> usize {
    let redacted = redact_text(value);
    *value = redacted.value;
    redacted.count
}

fn redact_optional_string(value: &mut Option<String>) -> usize {
    value.as_mut().map(redact_string).unwrap_or(0)
}

fn redact_metadata(metadata: &mut HashMap<String, serde_json::Value>) -> usize {
    let mut count = 0usize;
    for (key, value) in metadata {
        if is_sensitive_key(key)
            && !matches!(value, serde_json::Value::Null | serde_json::Value::Bool(_) | serde_json::Value::Number(_))
        {
            *value = serde_json::Value::String(REDACTED.to_string());
            count = count.saturating_add(1);
        } else {
            count = count.saturating_add(redact_json_in_place(value));
        }
    }
    count
}

fn sanitize_session_input(input: &mut RegisterSession) -> usize {
    let mut count = redact_optional_string(&mut input.display_name);
    count = count.saturating_add(redact_optional_string(&mut input.working_directory));
    count = count.saturating_add(redact_optional_string(&mut input.repository_root));
    count = count.saturating_add(redact_optional_string(&mut input.branch));
    count.saturating_add(redact_optional_string(&mut input.model_id))
}

fn sanitize_usage_event(event: &mut UsageEvent) -> usize {
    let mut count = redact_string(&mut event.model_id);
    count = count.saturating_add(redact_string(&mut event.source));
    count = count.saturating_add(redact_optional_string(&mut event.performance.error_code));
    if let Some(metadata) = &mut event.metadata {
        count = count.saturating_add(redact_metadata(metadata));
    }
    count
}

fn sanitize_quota(quota: &mut QuotaWindow) -> usize {
    let mut count = redact_optional_string(&mut quota.plan_id);
    count = count.saturating_add(redact_string(&mut quota.label));
    count = count.saturating_add(redact_string(&mut quota.kind));
    count = count.saturating_add(redact_string(&mut quota.metric));
    count.saturating_add(redact_string(&mut quota.source))
}

fn sanitize_mcp_event(event: &mut McpToolCallEvent) -> usize {
    let mut count = redact_string(&mut event.operation);
    count = count.saturating_add(redact_string(&mut event.backend));
    count = count.saturating_add(redact_string(&mut event.tool_name));
    if let Some(error_type) = &mut event.error_type {
        count = count.saturating_add(redact_string(error_type));
    }
    count.saturating_add(redact_string(&mut event.at))
}

async fn health() -> impl IntoResponse { Json(HealthResponse { status: "ok", service: "soleaux-daemon", protocol_version: 2 }) }
''',
    "telemetry sanitization helpers",
)
text = replace_once(
    text,
    "async fn register_session(State(state): State<AppState>, Json(input): Json<RegisterSession>) -> impl IntoResponse {\n    let session = Session {\n",
    "async fn register_session(State(state): State<AppState>, Json(mut input): Json<RegisterSession>) -> impl IntoResponse {\n    sanitize_session_input(&mut input);\n    let session = Session {\n",
    "session input sanitization",
)
text = replace_once(
    text,
    "async fn record_usage_event(State(state): State<AppState>, Json(mut event): Json<UsageEvent>) -> impl IntoResponse {\n    if event.occurred_at == 0",
    "async fn record_usage_event(State(state): State<AppState>, Json(mut event): Json<UsageEvent>) -> impl IntoResponse {\n    sanitize_usage_event(&mut event);\n    if event.occurred_at == 0",
    "usage event sanitization",
)
text = replace_once(
    text,
    "async fn record_quota(State(state): State<AppState>, Json(mut quota): Json<QuotaWindow>) -> impl IntoResponse {\n    if quota.observed_at == 0",
    "async fn record_quota(State(state): State<AppState>, Json(mut quota): Json<QuotaWindow>) -> impl IntoResponse {\n    sanitize_quota(&mut quota);\n    if quota.observed_at == 0",
    "quota sanitization",
)
text = replace_once(
    text,
    "async fn record_mcp_event(State(state): State<AppState>, Json(event): Json<McpToolCallEvent>) -> impl IntoResponse {\n    let mut events",
    "async fn record_mcp_event(State(state): State<AppState>, Json(mut event): Json<McpToolCallEvent>) -> impl IntoResponse {\n    sanitize_mcp_event(&mut event);\n    let mut events",
    "MCP event sanitization",
)
text = replace_once(
    text,
    "async fn register_mcp_backend(State(state): State<AppState>, Json(input): Json<RegisterMcpBackend>) -> impl IntoResponse {\n    state.mcp_backends.write().await.insert(input.backend.clone());\n    (StatusCode::CREATED, Json(input))\n}\n",
    '''async fn register_mcp_backend(State(state): State<AppState>, Json(mut input): Json<RegisterMcpBackend>) -> impl IntoResponse {
    redact_string(&mut input.backend);
    state.mcp_backends.write().await.insert(input.backend.clone());
    (StatusCode::CREATED, Json(input))
}
''',
    "MCP backend sanitization",
)
last_brace = text.rfind("}\n")
if last_brace < 0:
    raise SystemExit("telemetry test module closing brace missing")
text = text[:last_brace] + '''
    #[test]
    fn telemetry_redacts_nested_metadata_and_secret_bearing_fields() {
        let mut metadata = HashMap::from([
            ("accessToken".to_string(), json!("live-access-token-value")),
            (
                "nested".to_string(),
                json!({"ordinary": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"}),
            ),
            ("input_tokens".to_string(), json!(512)),
        ]);
        let count = redact_metadata(&mut metadata);
        assert_eq!(metadata["accessToken"], REDACTED);
        assert_eq!(metadata["nested"]["ordinary"], REDACTED);
        assert_eq!(metadata["input_tokens"], 512);
        assert_eq!(count, 2);

        let mut value = "postgres://alice:password@localhost/app".to_string();
        assert_eq!(redact_string(&mut value), 1);
        assert!(!value.contains("alice:password"));
    }
'''+ text[last_brace:]
write(path, text)
