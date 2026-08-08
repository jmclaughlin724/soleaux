//! Host wire protocol between this Rust host and the Node SDK harness.
//!
//! Soleaux owns this protocol; it is not a vendor surface. Frames are
//! newline-delimited JSON over the harness process's stdio, mirroring the
//! Codex adapter's JSONL framing. The harness speaks first with `hello`;
//! store calls and permission requests are harness-initiated requests the
//! host answers, and session commands are host-initiated requests the
//! harness answers. Incoming types tolerate unknown fields so a harness
//! addition inside the pinned protocol version never breaks parsing.

use crate::store::SessionKey;
use serde::Deserialize;
use serde_json::{Value, json};

/// Version tag both sides must present; a mismatch refuses the connection.
pub const HOST_PROTOCOL_VERSION: &str = "soleaux.claude-host/v1";

/// The harness's opening frame: which SDK it actually loaded.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct HelloFrame {
    pub protocol: String,
    #[serde(default)]
    pub sdk_package: Option<String>,
    #[serde(default)]
    pub sdk_version: Option<String>,
    #[serde(default)]
    pub harness_version: Option<String>,
}

/// One `SessionStore` call forwarded by the harness.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct StoreRequestFrame {
    pub id: i64,
    #[serde(flatten)]
    pub op: StoreOp,
}

/// The `SessionStore` surface the host serves. `delete` is parsed so the
/// refusal is typed rather than a protocol error: the SDK never deletes from
/// the store, and the host owns retention.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum StoreOp {
    Append {
        key: SessionKey,
        entries: Vec<Value>,
    },
    Load {
        key: SessionKey,
    },
    ListSessions {
        #[serde(rename = "projectKey")]
        project_key: String,
    },
    ListSubkeys {
        #[serde(rename = "projectKey")]
        project_key: String,
        #[serde(rename = "sessionId")]
        session_id: String,
    },
    Delete {
        key: SessionKey,
    },
}

/// A forwarded SDK observation: an iterator message, a hook invocation, or a
/// system message such as `compact_boundary` or `mirror_error`.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct EventFrame {
    pub event: String,
    #[serde(default)]
    pub hook: Option<String>,
    #[serde(default)]
    pub payload: Value,
}

/// A `canUseTool` round-trip: the harness blocks the tool call until the host
/// answers with a `permission_decision` frame for the same `id`.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PermissionRequestFrame {
    pub id: i64,
    #[serde(default)]
    pub request: Value,
}

/// The harness's answer to a host-initiated session command.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ResponseFrame {
    pub id: i64,
    pub ok: bool,
    #[serde(default)]
    pub result: Value,
    #[serde(default)]
    pub error: Option<String>,
}

/// One incoming harness frame, classified by its `type` tag.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HarnessFrame {
    Hello(HelloFrame),
    Store(StoreRequestFrame),
    Event(EventFrame),
    PermissionRequest(PermissionRequestFrame),
    Response(ResponseFrame),
}

pub fn parse_harness_frame(line: &str) -> Result<HarnessFrame, String> {
    serde_json::from_str(line).map_err(|error| format!("harness frame is malformed: {error}"))
}

pub fn encode_hello_ack(mode: &str, refusal: Option<&str>) -> String {
    json!({
        "type": "hello_ack",
        "protocol": HOST_PROTOCOL_VERSION,
        "pinnedSdkVersion": crate::version::PINNED_CLAUDE_CODE_VERSION,
        "mode": mode,
        "refusal": refusal,
    })
    .to_string()
}

pub fn encode_store_result(id: i64, result: Result<Value, &str>) -> String {
    match result {
        Ok(result) => json!({"type": "store_result", "id": id, "ok": true, "result": result}),
        Err(error) => json!({"type": "store_result", "id": id, "ok": false, "error": error}),
    }
    .to_string()
}

pub fn encode_request(id: i64, op: &str, params: &Value) -> String {
    json!({"type": "request", "id": id, "op": op, "params": params}).to_string()
}

pub fn encode_permission_decision(id: i64, decision: &Value) -> String {
    json!({"type": "permission_decision", "id": id, "decision": decision}).to_string()
}
