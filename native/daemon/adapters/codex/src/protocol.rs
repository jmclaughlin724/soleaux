//! Typed wire surface for the Codex app-server JSON-RPC protocol.
//!
//! Derived by hand from the vendored JSON Schemas under `schema/json` at tag
//! `rust-v0.146.1` and validated against them in tests: every method string
//! must exist in the vendored `ClientRequest`/`ServerRequest`/
//! `ServerNotification` unions, every serialized parameter key must be a
//! declared schema property, and every schema-required key must be present.
//! On the wire the `jsonrpc` header is omitted; frames are newline-delimited
//! JSON. Incoming types tolerate unknown fields so vendor additions inside the
//! pinned version never break parsing; outgoing types serialize only fields
//! the schema declares.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const METHOD_INITIALIZE: &str = "initialize";
pub const METHOD_INITIALIZED: &str = "initialized";
pub const METHOD_THREAD_START: &str = "thread/start";
pub const METHOD_THREAD_RESUME: &str = "thread/resume";
pub const METHOD_THREAD_FORK: &str = "thread/fork";
pub const METHOD_THREAD_LIST: &str = "thread/list";
pub const METHOD_THREAD_LOADED_LIST: &str = "thread/loaded/list";
pub const METHOD_THREAD_READ: &str = "thread/read";
pub const METHOD_THREAD_ARCHIVE: &str = "thread/archive";
pub const METHOD_THREAD_COMPACT_START: &str = "thread/compact/start";
pub const METHOD_TURN_START: &str = "turn/start";
pub const METHOD_TURN_STEER: &str = "turn/steer";
pub const METHOD_TURN_INTERRUPT: &str = "turn/interrupt";

pub const METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL: &str = "item/commandExecution/requestApproval";
pub const METHOD_FILE_CHANGE_REQUEST_APPROVAL: &str = "item/fileChange/requestApproval";

pub const NOTIFICATION_THREAD_STARTED: &str = "thread/started";
pub const NOTIFICATION_THREAD_ARCHIVED: &str = "thread/archived";
pub const NOTIFICATION_THREAD_COMPACTED: &str = "thread/compacted";
pub const NOTIFICATION_TURN_STARTED: &str = "turn/started";
pub const NOTIFICATION_TURN_COMPLETED: &str = "turn/completed";
pub const NOTIFICATION_ITEM_COMPLETED: &str = "item/completed";
pub const NOTIFICATION_ERROR: &str = "error";

/// JSON-RPC error code for a server request the adapter does not support.
pub const UNSUPPORTED_SERVER_REQUEST_CODE: i64 = -32601;

/// Methods safe mode may issue: the connection handshake and pure reads.
/// Every method not listed here is treated as mutating and refused.
const READ_ONLY_METHODS: &[&str] = &[
    METHOD_INITIALIZE,
    METHOD_THREAD_LIST,
    METHOD_THREAD_LOADED_LIST,
    METHOD_THREAD_READ,
];

pub fn method_is_read_only(method: &str) -> bool {
    READ_ONLY_METHODS.contains(&method)
}

/// A JSON-RPC request id: the adapter issues integers, but the server may use
/// strings for its own requests.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum RequestId {
    Number(i64),
    Text(String),
}

/// One incoming frame, classified by shape: a `method` with an `id` is a
/// server request, a `method` alone is a notification, and an `id` alone is a
/// response.
#[derive(Debug)]
pub enum IncomingMessage {
    Response {
        id: RequestId,
        result: Result<Value, RpcError>,
    },
    ServerRequest {
        id: RequestId,
        request: CodexServerRequest,
    },
    Notification(CodexNotification),
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
    #[serde(default)]
    pub data: Option<Value>,
}

pub fn parse_incoming(line: &str) -> Result<IncomingMessage, String> {
    let value: Value =
        serde_json::from_str(line).map_err(|error| format!("frame is not valid JSON: {error}"))?;
    let id = value.get("id").map(|id| {
        serde_json::from_value::<RequestId>(id.clone())
            .map_err(|error| format!("frame id is malformed: {error}"))
    });
    let method = value.get("method").and_then(Value::as_str);
    match (method, id) {
        (Some(method), Some(id)) => Ok(IncomingMessage::ServerRequest {
            id: id?,
            request: CodexServerRequest::parse(
                method,
                value.get("params").cloned().unwrap_or(Value::Null),
            ),
        }),
        (Some(method), None) => Ok(IncomingMessage::Notification(CodexNotification::parse(
            method,
            value.get("params").cloned().unwrap_or(Value::Null),
        ))),
        (None, Some(id)) => {
            let id = id?;
            if let Some(error) = value.get("error") {
                let error: RpcError = serde_json::from_value(error.clone())
                    .map_err(|error| format!("frame error object is malformed: {error}"))?;
                Ok(IncomingMessage::Response {
                    id,
                    result: Err(error),
                })
            } else if let Some(result) = value.get("result") {
                Ok(IncomingMessage::Response {
                    id,
                    result: Ok(result.clone()),
                })
            } else {
                Err("response frame carries neither result nor error".to_string())
            }
        }
        (None, None) => Err("frame carries neither method nor id".to_string()),
    }
}

pub fn encode_request(id: i64, method: &str, params: &Value) -> Result<String, String> {
    serde_json::to_string(&serde_json::json!({
        "id": id,
        "method": method,
        "params": params,
    }))
    .map_err(|error| format!("encoding {method} request: {error}"))
}

pub fn encode_notification(method: &str) -> String {
    format!("{{\"method\":{}}}", serde_json::json!(method))
}

pub fn encode_response(id: &RequestId, result: &Value) -> Result<String, String> {
    serde_json::to_string(&serde_json::json!({"id": id, "result": result}))
        .map_err(|error| format!("encoding response: {error}"))
}

pub fn encode_error_response(id: &RequestId, code: i64, message: &str) -> Result<String, String> {
    serde_json::to_string(
        &serde_json::json!({"id": id, "error": {"code": code, "message": message}}),
    )
    .map_err(|error| format!("encoding error response: {error}"))
}

// --- initialize -------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ClientInfo {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    pub version: String,
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeCapabilities {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub experimental_api: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opt_out_notification_methods: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeParams {
    pub client_info: ClientInfo,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<InitializeCapabilities>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct InitializeResponse {
    pub user_agent: String,
    pub codex_home: String,
    pub platform_family: String,
    pub platform_os: String,
}

// --- threads ----------------------------------------------------------------

/// A stored Codex thread, reduced to the fields the adapter consumes. The
/// vendored `Thread` schema requires more; unknown fields are ignored and
/// absent fields degrade to `None` rather than failing the frame.
#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Thread {
    pub id: String,
    #[serde(default)]
    pub cli_version: Option<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub ephemeral: bool,
    #[serde(default)]
    pub preview: Option<String>,
    #[serde(default)]
    pub created_at: Option<i64>,
    #[serde(default)]
    pub updated_at: Option<i64>,
    #[serde(default)]
    pub status: Option<Value>,
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadStartParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approval_policy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sandbox: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ephemeral: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadResumeParams {
    pub thread_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approval_policy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sandbox: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadForkParams {
    pub thread_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_turn_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ephemeral: Option<bool>,
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadListParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cursor: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub archived: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadReadParams {
    pub thread_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub include_turns: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadIdParams {
    pub thread_id: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ThreadResponse {
    pub thread: Thread,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ThreadListResponse {
    pub data: Vec<Thread>,
    #[serde(default)]
    pub next_cursor: Option<String>,
}

// --- turns ------------------------------------------------------------------

/// User input items for `turn/start` and `turn/steer`. Only the text form is
/// supported; images and other item kinds stay outside the adapter surface.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum UserInput {
    Text { text: String },
}

impl UserInput {
    pub fn text(text: impl Into<String>) -> Self {
        Self::Text { text: text.into() }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum TurnStatus {
    Completed,
    Interrupted,
    Failed,
    InProgress,
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnError {
    pub message: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnSummary {
    pub id: String,
    #[serde(default)]
    pub status: Option<TurnStatus>,
    #[serde(default)]
    pub started_at: Option<i64>,
    #[serde(default)]
    pub completed_at: Option<i64>,
    #[serde(default)]
    pub error: Option<TurnError>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnStartParams {
    pub thread_id: String,
    pub input: Vec<UserInput>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approval_policy: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnSteerParams {
    pub thread_id: String,
    pub expected_turn_id: String,
    pub input: Vec<UserInput>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnInterruptParams {
    pub thread_id: String,
    pub turn_id: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnStartResponse {
    pub turn: TurnSummary,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnSteerResponse {
    pub turn_id: String,
}

// --- notifications ----------------------------------------------------------

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnNotification {
    pub thread_id: String,
    pub turn: TurnSummary,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ItemCompletedNotification {
    pub thread_id: String,
    pub turn_id: String,
    pub item: Value,
    #[serde(default)]
    pub completed_at_ms: Option<i64>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ThreadTurnRef {
    pub thread_id: String,
    pub turn_id: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ErrorNotification {
    #[serde(default)]
    pub thread_id: Option<String>,
    #[serde(default)]
    pub turn_id: Option<String>,
    pub error: Value,
    #[serde(default)]
    pub will_retry: bool,
}

/// One server notification. Methods outside the typed set are preserved as
/// `Other` so consumers observe the full stream; a malformed payload for a
/// typed method also degrades to `Other` rather than dropping the frame.
#[derive(Debug, Clone, PartialEq)]
pub enum CodexNotification {
    ThreadStarted { thread: Thread },
    TurnStarted(TurnNotification),
    TurnCompleted(TurnNotification),
    ItemCompleted(ItemCompletedNotification),
    ThreadCompacted(ThreadTurnRef),
    ThreadArchived { thread_id: String },
    Error(ErrorNotification),
    Other { method: String, params: Value },
}

impl CodexNotification {
    fn parse(method: &str, params: Value) -> Self {
        fn typed<T, F>(method: &str, params: Value, wrap: F) -> CodexNotification
        where
            T: serde::de::DeserializeOwned,
            F: FnOnce(T) -> CodexNotification,
        {
            match serde_json::from_value::<T>(params.clone()) {
                Ok(parsed) => wrap(parsed),
                Err(_) => CodexNotification::Other {
                    method: method.to_string(),
                    params,
                },
            }
        }
        match method {
            NOTIFICATION_THREAD_STARTED => typed(method, params, |response: ThreadResponse| {
                CodexNotification::ThreadStarted {
                    thread: response.thread,
                }
            }),
            NOTIFICATION_TURN_STARTED => typed(method, params, CodexNotification::TurnStarted),
            NOTIFICATION_TURN_COMPLETED => typed(method, params, CodexNotification::TurnCompleted),
            NOTIFICATION_ITEM_COMPLETED => typed(method, params, CodexNotification::ItemCompleted),
            NOTIFICATION_THREAD_COMPACTED => {
                typed(method, params, CodexNotification::ThreadCompacted)
            }
            NOTIFICATION_THREAD_ARCHIVED => {
                #[derive(Deserialize)]
                #[serde(rename_all = "camelCase")]
                struct Params {
                    thread_id: String,
                }
                typed(method, params, |parsed: Params| {
                    CodexNotification::ThreadArchived {
                        thread_id: parsed.thread_id,
                    }
                })
            }
            NOTIFICATION_ERROR => typed(method, params, CodexNotification::Error),
            _ => CodexNotification::Other {
                method: method.to_string(),
                params,
            },
        }
    }
}

// --- server requests (approvals) --------------------------------------------

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CommandExecutionApprovalParams {
    pub item_id: String,
    pub thread_id: String,
    pub turn_id: String,
    pub started_at_ms: i64,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub command: Option<Value>,
    #[serde(default)]
    pub cwd: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct FileChangeApprovalParams {
    pub item_id: String,
    pub thread_id: String,
    pub turn_id: String,
    pub started_at_ms: i64,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub grant_root: Option<String>,
}

/// One server-to-client request. Unsupported methods are preserved so the
/// client can answer them with a JSON-RPC error instead of leaving the server
/// waiting.
#[derive(Debug, Clone, PartialEq)]
pub enum CodexServerRequest {
    CommandExecutionApproval(CommandExecutionApprovalParams),
    FileChangeApproval(FileChangeApprovalParams),
    Unsupported { method: String, params: Value },
}

impl CodexServerRequest {
    fn parse(method: &str, params: Value) -> Self {
        match method {
            METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL => {
                match serde_json::from_value(params.clone()) {
                    Ok(parsed) => Self::CommandExecutionApproval(parsed),
                    Err(_) => Self::Unsupported {
                        method: method.to_string(),
                        params,
                    },
                }
            }
            METHOD_FILE_CHANGE_REQUEST_APPROVAL => match serde_json::from_value(params.clone()) {
                Ok(parsed) => Self::FileChangeApproval(parsed),
                Err(_) => Self::Unsupported {
                    method: method.to_string(),
                    params,
                },
            },
            _ => Self::Unsupported {
                method: method.to_string(),
                params,
            },
        }
    }

    pub fn is_approval(&self) -> bool {
        matches!(
            self,
            Self::CommandExecutionApproval(_) | Self::FileChangeApproval(_)
        )
    }

    pub fn method(&self) -> &str {
        match self {
            Self::CommandExecutionApproval(_) => METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL,
            Self::FileChangeApproval(_) => METHOD_FILE_CHANGE_REQUEST_APPROVAL,
            Self::Unsupported { method, .. } => method,
        }
    }
}

/// The decision strings shared by both approval response schemas.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ApprovalDecision {
    Accept,
    AcceptForSession,
    Decline,
    Cancel,
}

impl ApprovalDecision {
    /// The fail-closed decision: deny and interrupt the requesting turn.
    pub const FAIL_CLOSED: Self = Self::Cancel;
}

pub fn approval_response_body(decision: ApprovalDecision) -> Value {
    serde_json::json!({ "decision": decision })
}
