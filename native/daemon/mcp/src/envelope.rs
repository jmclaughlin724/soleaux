//! Closed Soleaux MCP response envelope (`soleaux.mcp/v2`).

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use soleaux_redaction::{redact_json_in_place, redact_text};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

pub const ENVELOPE_SCHEMA_VERSION: &str = "soleaux.mcp/v2";
pub const PRODUCT_VERSION: &str = "0.4.0-dev.5";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ToolEnvelopeV2 {
    pub schema_version: String,
    pub product_version: String,
    pub request_id: String,
    pub workspace_id: Option<String>,
    pub snapshot_id: Option<String>,
    pub workspace: String,
    pub status: String,
    pub data: Value,
    pub rows: Option<Vec<Value>>,
    pub evidence: Vec<Value>,
    pub coverage: Option<Value>,
    pub warnings: Vec<String>,
    pub next_cursor: Option<String>,
    pub suggested_next_requests: Vec<Value>,
    pub error: Option<Value>,
    pub source: String,
    pub engine: String,
    pub engine_version: String,
    pub trust: String,
    pub provenance: Value,
    pub cache_status: String,
    pub truncated: bool,
    pub continuation_cursor: Option<String>,
    pub sensitivity: String,
    pub duration_us: u64,
}

#[derive(Debug, Clone)]
pub struct SuccessMetadata {
    pub source: String,
    pub engine: String,
    pub trust: String,
    pub cache_status: String,
    pub snapshot_id: Option<String>,
    pub coverage: Option<Value>,
    pub evidence: Vec<Value>,
    pub warnings: Vec<String>,
    pub next_cursor: Option<String>,
    pub suggested_next_requests: Vec<Value>,
    pub truncated: bool,
    pub continuation_cursor: Option<String>,
    pub sensitivity: String,
}

impl SuccessMetadata {
    pub fn repository(source: impl Into<String>, engine: impl Into<String>) -> Self {
        Self {
            source: source.into(),
            engine: engine.into(),
            trust: "verified_repository_metadata".to_string(),
            cache_status: "read".to_string(),
            snapshot_id: None,
            coverage: None,
            evidence: Vec::new(),
            warnings: Vec::new(),
            next_cursor: None,
            suggested_next_requests: Vec::new(),
            truncated: false,
            continuation_cursor: None,
            sensitivity: "internal".to_string(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ToolError {
    pub error_type: String,
    pub message: String,
    pub retryable: bool,
    pub details: Value,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct EvidenceRange<'a> {
    pub path: Option<&'a str>,
    pub start_line: Option<u64>,
    pub end_line: Option<u64>,
    pub start_byte: Option<u64>,
    pub end_byte: Option<u64>,
}

impl ToolEnvelopeV2 {
    pub fn success(
        workspace_id: Uuid,
        workspace: &str,
        data: Value,
        rows: Option<Vec<Value>>,
        duration_us: u64,
        metadata: SuccessMetadata,
    ) -> Self {
        let request_id = Uuid::now_v7().to_string();
        let snapshot_id = metadata.snapshot_id.clone();
        let provenance = provenance(
            &metadata.source,
            &metadata.engine,
            Some(workspace_id),
            snapshot_id.as_deref(),
            None,
            None,
            "none",
        );
        let mut envelope = Self {
            schema_version: ENVELOPE_SCHEMA_VERSION.to_string(),
            product_version: PRODUCT_VERSION.to_string(),
            request_id,
            workspace_id: Some(workspace_id.to_string()),
            snapshot_id,
            workspace: workspace.to_string(),
            status: "ok".to_string(),
            data,
            rows,
            evidence: metadata.evidence,
            coverage: metadata.coverage,
            warnings: metadata.warnings,
            next_cursor: metadata.next_cursor,
            suggested_next_requests: metadata.suggested_next_requests,
            error: None,
            source: metadata.source,
            engine: metadata.engine,
            engine_version: PRODUCT_VERSION.to_string(),
            trust: metadata.trust,
            provenance,
            cache_status: metadata.cache_status,
            truncated: metadata.truncated,
            continuation_cursor: metadata.continuation_cursor,
            sensitivity: metadata.sensitivity,
            duration_us,
        };
        envelope.redact_in_place();
        envelope
    }

    pub fn error(
        workspace_id: Uuid,
        workspace: &str,
        source: impl Into<String>,
        error: ToolError,
        duration_us: u64,
    ) -> Self {
        let source = source.into();
        let mut envelope = Self {
            schema_version: ENVELOPE_SCHEMA_VERSION.to_string(),
            product_version: PRODUCT_VERSION.to_string(),
            request_id: Uuid::now_v7().to_string(),
            workspace_id: Some(workspace_id.to_string()),
            snapshot_id: None,
            workspace: workspace.to_string(),
            status: "error".to_string(),
            data: Value::Null,
            rows: None,
            evidence: Vec::new(),
            coverage: None,
            warnings: Vec::new(),
            next_cursor: None,
            suggested_next_requests: Vec::new(),
            error: Some(json!({
                "error_type": error.error_type,
                "message": error.message,
                "retryable": error.retryable,
                "details": error.details,
            })),
            source: source.clone(),
            engine: "soleaux-native-public-mcp".to_string(),
            engine_version: PRODUCT_VERSION.to_string(),
            trust: "unavailable".to_string(),
            provenance: provenance(
                &source,
                "soleaux-native-public-mcp",
                Some(workspace_id),
                None,
                None,
                None,
                "none",
            ),
            cache_status: "bypass".to_string(),
            truncated: false,
            continuation_cursor: None,
            sensitivity: "internal".to_string(),
            duration_us,
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
}

pub fn provenance(
    provider: &str,
    engine: &str,
    workspace_id: Option<Uuid>,
    snapshot_id: Option<&str>,
    path: Option<&str>,
    content_hash: Option<&str>,
    range_encoding: &str,
) -> Value {
    json!({
        "provider": provider,
        "engine": engine,
        "engine_version": PRODUCT_VERSION,
        "range_encoding": range_encoding,
        "provider_version": PRODUCT_VERSION,
        "grammar_version": Value::Null,
        "workspace_id": workspace_id.map(|value| value.to_string()),
        "snapshot_id": snapshot_id,
        "catalog_generation": Value::Null,
        "path": path,
        "content_hash": content_hash,
        "source_range_hash": Value::Null,
        "generated_at_unix_ms": unix_ms(),
    })
}

pub fn gap(
    code: &str,
    message: &str,
    severity: &str,
    retryable: bool,
    table: Option<&str>,
    path: Option<&str>,
) -> Value {
    json!({
        "code": truncate(code, 128),
        "message": truncate(message, 1024),
        "severity": severity,
        "retryable": retryable,
        "table": table,
        "path": path,
    })
}

pub fn coverage(
    complete: bool,
    requested_paths: Vec<String>,
    observed_paths: Vec<String>,
    excluded_paths: Vec<Value>,
    engines: Vec<String>,
    gaps: Vec<Value>,
    catalog_generation: Option<u64>,
) -> Value {
    json!({
        "complete": complete,
        "requested_paths": requested_paths,
        "observed_paths": observed_paths,
        "excluded_paths": excluded_paths,
        "engines": engines,
        "gaps": gaps,
        "catalog_generation": catalog_generation,
    })
}

pub fn evidence(
    evidence_id: impl Into<String>,
    kind: impl Into<String>,
    summary: impl Into<String>,
    trust: &str,
    provenance: Value,
    range: EvidenceRange<'_>,
) -> Value {
    json!({
        "evidence_id": evidence_id.into(),
        "kind": kind.into(),
        "summary": truncate(&summary.into(), 2048),
        "trust": trust,
        "provenance": provenance,
        "path": range.path,
        "start_line": range.start_line,
        "end_line": range.end_line,
        "start_byte": range.start_byte,
        "end_byte": range.end_byte,
    })
}

fn truncate(value: &str, maximum: usize) -> String {
    if value.len() <= maximum {
        return value.to_string();
    }
    let mut end = maximum;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_string()
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

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
