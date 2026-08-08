//! Hand-derived serde types for the OpenCode `1.18.14` OpenAPI surface.
//!
//! Generation tooling would be disproportionate for the operation subset this
//! adapter uses (the vendored document declares 162 paths and 472 schemas),
//! so each type below is derived by hand and held to the document by the
//! conformance tests in `tests.rs`: every serialized field name must exist in
//! the vendored schema, and every spec-required property must be represented.
//! Vendor-owned response types tolerate unknown fields — plugins may extend
//! payloads — while unknown event kinds are preserved, never dropped.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// `GET /global/health` response.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HealthInfo {
    pub healthy: bool,
    pub version: String,
}

/// `#/components/schemas/Session`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Session {
    pub id: String,
    pub slug: String,
    #[serde(rename = "projectID")]
    pub project_id: String,
    #[serde(rename = "workspaceID", default)]
    pub workspace_id: Option<String>,
    pub directory: String,
    #[serde(default)]
    pub path: Option<String>,
    #[serde(rename = "parentID", default)]
    pub parent_id: Option<String>,
    pub title: String,
    pub version: String,
    pub time: SessionTime,
    #[serde(default)]
    pub agent: Option<String>,
    #[serde(default)]
    pub revert: Option<SessionRevert>,
    #[serde(default)]
    pub metadata: Option<Map<String, Value>>,
}

/// `Session.time`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SessionTime {
    pub created: i64,
    pub updated: i64,
    #[serde(default)]
    pub compacting: Option<i64>,
    #[serde(default)]
    pub archived: Option<f64>,
}

/// `Session.revert`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SessionRevert {
    #[serde(rename = "messageID")]
    pub message_id: String,
    #[serde(rename = "partID", default)]
    pub part_id: Option<String>,
    #[serde(default)]
    pub snapshot: Option<String>,
    #[serde(default)]
    pub diff: Option<String>,
}

/// One `GET /session/{sessionID}/message` element: message info plus parts.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MessageEnvelope {
    pub info: MessageInfo,
    pub parts: Vec<Part>,
}

/// The fields shared by both arms of `#/components/schemas/Message`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MessageInfo {
    pub id: String,
    #[serde(rename = "sessionID")]
    pub session_id: String,
    pub role: String,
    pub agent: String,
    pub time: Value,
}

/// One `#/components/schemas/Part`; the union is kept open on purpose.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Part {
    pub id: String,
    #[serde(rename = "sessionID")]
    pub session_id: String,
    #[serde(rename = "messageID")]
    pub message_id: String,
    #[serde(rename = "type")]
    pub part_type: String,
}

/// `#/components/schemas/PermissionRequest`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PermissionRequest {
    pub id: String,
    #[serde(rename = "sessionID")]
    pub session_id: String,
    pub permission: String,
    pub patterns: Vec<String>,
    pub metadata: Map<String, Value>,
    pub always: Vec<String>,
    #[serde(default)]
    pub tool: Option<PermissionTool>,
}

/// `PermissionRequest.tool`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PermissionTool {
    #[serde(rename = "messageID")]
    pub message_id: String,
    #[serde(rename = "callID")]
    pub call_id: String,
}

/// The three documented permission replies.
#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum PermissionReply {
    Once,
    Always,
    Reject,
}

/// `POST /session` body (subset: lineage, title, and metadata).
#[derive(Debug, Clone, Default, Serialize, PartialEq)]
pub struct CreateSessionRequest {
    #[serde(rename = "parentID", skip_serializing_if = "Option::is_none")]
    pub parent_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Map<String, Value>>,
}

/// `POST /session/{sessionID}/summarize` body.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SummarizeRequest {
    #[serde(rename = "providerID")]
    pub provider_id: String,
    #[serde(rename = "modelID")]
    pub model_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub auto: Option<bool>,
}

/// `POST /session/{sessionID}/revert` body.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct RevertRequest {
    #[serde(rename = "messageID")]
    pub message_id: String,
    #[serde(rename = "partID", skip_serializing_if = "Option::is_none")]
    pub part_id: Option<String>,
}

/// `GET /config` subset: the plugin roster this adapter must stay compatible
/// with.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OpencodeConfig {
    #[serde(default)]
    pub plugin: Vec<PluginSpec>,
}

/// One `Config.plugin` element: a bare specifier or `[specifier, options]`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum PluginSpec {
    Specifier(String),
    WithOptions(String, Map<String, Value>),
}

impl PluginSpec {
    pub fn specifier(&self) -> &str {
        match self {
            Self::Specifier(specifier) | Self::WithOptions(specifier, _) => specifier,
        }
    }
}

/// One `/event` stream element. Kinds the adapter acts on are typed; every
/// other kind — including kinds added by plugins — is preserved as
/// [`Event::Unknown`] so the stream never fails on forward-compatible input.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    ServerConnected {
        id: String,
    },
    SessionCreated {
        id: String,
        session: Session,
    },
    SessionUpdated {
        id: String,
        session: Session,
    },
    SessionDeleted {
        id: String,
        session: Session,
    },
    SessionIdle {
        id: String,
        session_id: String,
    },
    SessionCompacted {
        id: String,
        session_id: String,
    },
    SessionError {
        id: String,
        session_id: Option<String>,
        error: Value,
    },
    MessageUpdated {
        id: String,
        session_id: String,
    },
    PermissionAsked {
        id: String,
        request: PermissionRequest,
    },
    PermissionReplied {
        id: String,
        session_id: String,
        request_id: String,
        reply: String,
    },
    PluginAdded {
        id: String,
        plugin_id: String,
    },
    Unknown {
        id: Option<String>,
        event_type: String,
        raw: Value,
    },
}

impl Event {
    /// The `evt_…` identity, absent only on malformed unknown frames.
    pub fn event_id(&self) -> Option<&str> {
        match self {
            Self::ServerConnected { id }
            | Self::SessionCreated { id, .. }
            | Self::SessionUpdated { id, .. }
            | Self::SessionDeleted { id, .. }
            | Self::SessionIdle { id, .. }
            | Self::SessionCompacted { id, .. }
            | Self::SessionError { id, .. }
            | Self::MessageUpdated { id, .. }
            | Self::PermissionAsked { id, .. }
            | Self::PermissionReplied { id, .. }
            | Self::PluginAdded { id, .. } => Some(id),
            Self::Unknown { id, .. } => id.as_deref(),
        }
    }

    pub fn event_type(&self) -> &str {
        match self {
            Self::ServerConnected { .. } => "server.connected",
            Self::SessionCreated { .. } => "session.created",
            Self::SessionUpdated { .. } => "session.updated",
            Self::SessionDeleted { .. } => "session.deleted",
            Self::SessionIdle { .. } => "session.idle",
            Self::SessionCompacted { .. } => "session.compacted",
            Self::SessionError { .. } => "session.error",
            Self::MessageUpdated { .. } => "message.updated",
            Self::PermissionAsked { .. } => "permission.asked",
            Self::PermissionReplied { .. } => "permission.replied",
            Self::PluginAdded { .. } => "plugin.added",
            Self::Unknown { event_type, .. } => event_type,
        }
    }

    /// Parse one event JSON object. Unknown kinds and kind-specific decode
    /// failures both land in [`Event::Unknown`] with the raw value preserved;
    /// only a frame without a string `type` is an error.
    pub fn from_value(value: Value) -> anyhow::Result<Self> {
        let event_type = value
            .get("type")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("event frame has no string `type` field"))?
            .to_string();
        let id = value
            .get("id")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let properties = value.get("properties").cloned().unwrap_or(Value::Null);
        let parsed = match event_type.as_str() {
            "server.connected" => id.clone().map(|id| Self::ServerConnected { id }),
            "session.created" | "session.updated" | "session.deleted" => {
                let session: Option<Session> = properties
                    .get("info")
                    .cloned()
                    .and_then(|info| serde_json::from_value(info).ok());
                match (id.clone(), session) {
                    (Some(id), Some(session)) => Some(match event_type.as_str() {
                        "session.created" => Self::SessionCreated { id, session },
                        "session.updated" => Self::SessionUpdated { id, session },
                        _ => Self::SessionDeleted { id, session },
                    }),
                    _ => None,
                }
            }
            "session.idle" | "session.compacted" => {
                let session_id = properties
                    .get("sessionID")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                match (id.clone(), session_id) {
                    (Some(id), Some(session_id)) if event_type == "session.idle" => {
                        Some(Self::SessionIdle { id, session_id })
                    }
                    (Some(id), Some(session_id)) => Some(Self::SessionCompacted { id, session_id }),
                    _ => None,
                }
            }
            "session.error" => id.clone().map(|id| Self::SessionError {
                id,
                session_id: properties
                    .get("sessionID")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned),
                error: properties.get("error").cloned().unwrap_or(Value::Null),
            }),
            "message.updated" => {
                let session_id = properties
                    .get("sessionID")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                match (id.clone(), session_id) {
                    (Some(id), Some(session_id)) => Some(Self::MessageUpdated { id, session_id }),
                    _ => None,
                }
            }
            "permission.asked" => {
                let request: Option<PermissionRequest> =
                    serde_json::from_value(properties.clone()).ok();
                match (id.clone(), request) {
                    (Some(id), Some(request)) => Some(Self::PermissionAsked { id, request }),
                    _ => None,
                }
            }
            "permission.replied" => {
                let session_id = properties.get("sessionID").and_then(Value::as_str);
                let request_id = properties.get("requestID").and_then(Value::as_str);
                let reply = properties.get("reply").and_then(Value::as_str);
                match (id.clone(), session_id, request_id, reply) {
                    (Some(id), Some(session_id), Some(request_id), Some(reply)) => {
                        Some(Self::PermissionReplied {
                            id,
                            session_id: session_id.to_string(),
                            request_id: request_id.to_string(),
                            reply: reply.to_string(),
                        })
                    }
                    _ => None,
                }
            }
            "plugin.added" => {
                let plugin_id = properties
                    .get("id")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                match (id.clone(), plugin_id) {
                    (Some(id), Some(plugin_id)) => Some(Self::PluginAdded { id, plugin_id }),
                    _ => None,
                }
            }
            _ => None,
        };
        Ok(parsed.unwrap_or(Self::Unknown {
            id,
            event_type,
            raw: value,
        }))
    }
}

/// One `/global/event` element: an [`Event`] plus its origin scope.
#[derive(Debug, Clone, PartialEq)]
pub struct GlobalEvent {
    pub directory: String,
    pub project: Option<String>,
    pub workspace: Option<String>,
    pub payload: Event,
}

impl GlobalEvent {
    pub fn from_value(value: Value) -> anyhow::Result<Self> {
        let directory = value
            .get("directory")
            .and_then(Value::as_str)
            .ok_or_else(|| anyhow::anyhow!("global event frame has no string `directory` field"))?
            .to_string();
        let project = value
            .get("project")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let workspace = value
            .get("workspace")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let payload = value
            .get("payload")
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("global event frame has no `payload` field"))?;
        Ok(Self {
            directory,
            project,
            workspace,
            payload: Event::from_value(payload)?,
        })
    }
}
