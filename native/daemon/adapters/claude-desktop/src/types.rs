//! Desktop export document model: defensive parsing and rendering of the
//! `conversations.json` payload inside a user's account-data export.
//!
//! The export format is documentation-pinned, not schema-pinned, so parsing
//! is defensive in both directions: identity fields (`uuid`, `sender`,
//! `chat_messages`) must be present and well-formed, while every unknown
//! field is tolerated and preserved verbatim so a canonical round trip loses
//! nothing. File-level damage fails the whole parse; a damaged conversation
//! refuses only that conversation, keeping import all-or-nothing per
//! conversation.

use serde_json::{Map, Value};

/// Upper bound on conversations per export file. Real exports stay far
/// below this; the cap refuses pathological inputs before allocation.
pub(crate) const MAX_CONVERSATIONS_PER_EXPORT: usize = 100_000;

/// Upper bound on chat messages per conversation, for the same reason.
pub(crate) const MAX_MESSAGES_PER_CONVERSATION: usize = 100_000;

/// Typed refusal reasons for every workflow in this crate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DesktopAdapterError {
    /// The export file is not valid JSON.
    InvalidJson { detail: String },
    /// The export structure is damaged at `location` (JSONPath-style).
    Malformed { location: String, detail: String },
    /// The conversation was previously imported into another workspace.
    WorkspaceMismatch { conversation_uuid: String },
    /// Existing canonical state contradicts the export; importing would
    /// corrupt the session, so nothing was written.
    SessionDiverged {
        conversation_uuid: String,
        detail: String,
    },
    /// The canonical session cannot be represented as a Desktop export.
    NotExportable { session_id: String, detail: String },
    /// A connector materialization request is invalid.
    InvalidConnector { detail: String },
    /// A user-authorized export file could not be read or written.
    Io { path: String, detail: String },
    /// The canonical store failed; the import stopped without completing.
    Store { detail: String },
}

impl std::fmt::Display for DesktopAdapterError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidJson { detail } => {
                write!(formatter, "the export file is not valid JSON: {detail}")
            }
            Self::Malformed { location, detail } => {
                write!(formatter, "malformed export at {location}: {detail}")
            }
            Self::WorkspaceMismatch { conversation_uuid } => write!(
                formatter,
                "conversation {conversation_uuid} was previously imported into a different workspace"
            ),
            Self::SessionDiverged {
                conversation_uuid,
                detail,
            } => write!(
                formatter,
                "conversation {conversation_uuid} diverges from existing canonical state: {detail}"
            ),
            Self::NotExportable { session_id, detail } => {
                write!(
                    formatter,
                    "session {session_id} is not exportable: {detail}"
                )
            }
            Self::InvalidConnector { detail } => {
                write!(formatter, "invalid connector materialization: {detail}")
            }
            Self::Io { path, detail } => {
                write!(formatter, "export file {path}: {detail}")
            }
            Self::Store { detail } => {
                write!(formatter, "canonical store failure: {detail}")
            }
        }
    }
}

impl std::error::Error for DesktopAdapterError {}

/// The two documented chat-message senders.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DesktopSender {
    Human,
    Assistant,
}

impl DesktopSender {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Human => "human",
            Self::Assistant => "assistant",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "human" => Some(Self::Human),
            "assistant" => Some(Self::Assistant),
            _ => None,
        }
    }
}

/// One chat message, with the exported object preserved verbatim.
#[derive(Debug, Clone, PartialEq)]
pub struct DesktopChatMessage {
    pub uuid: String,
    pub sender: DesktopSender,
    /// Full message object exactly as exported, unknown fields included.
    pub raw: Map<String, Value>,
}

/// One conversation, with its envelope preserved verbatim.
#[derive(Debug, Clone, PartialEq)]
pub struct DesktopConversation {
    pub uuid: String,
    pub name: String,
    pub chat_messages: Vec<DesktopChatMessage>,
    /// Conversation object exactly as exported minus `chat_messages`,
    /// unknown fields included.
    pub envelope: Map<String, Value>,
}

/// One parsed slot of the export file, valid or refused, in file order.
#[derive(Debug, Clone, PartialEq)]
pub struct ParsedEntry {
    pub index: usize,
    pub conversation: Result<DesktopConversation, DesktopAdapterError>,
}

/// A parsed export file. File-level damage never produces this; damage
/// inside one conversation refuses only that entry.
#[derive(Debug, Clone, PartialEq)]
pub struct ParsedExport {
    pub entries: Vec<ParsedEntry>,
}

impl ParsedExport {
    /// The conversations that parsed cleanly, in file order.
    pub fn valid(&self) -> impl Iterator<Item = &DesktopConversation> {
        self.entries
            .iter()
            .filter_map(|entry| entry.conversation.as_ref().ok())
    }
}

fn malformed(location: String, detail: impl Into<String>) -> DesktopAdapterError {
    DesktopAdapterError::Malformed {
        location,
        detail: detail.into(),
    }
}

fn required_string(
    object: &Map<String, Value>,
    field: &str,
    location: &str,
) -> Result<String, DesktopAdapterError> {
    match object.get(field) {
        Some(Value::String(value)) if !value.trim().is_empty() => Ok(value.clone()),
        Some(Value::String(_)) => Err(malformed(
            format!("{location}.{field}"),
            "required string is empty",
        )),
        Some(other) => Err(malformed(
            format!("{location}.{field}"),
            format!("expected a string, found {}", type_name(other)),
        )),
        None => Err(malformed(
            format!("{location}.{field}"),
            "required field is missing",
        )),
    }
}

fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "an array",
        Value::Object(_) => "an object",
    }
}

/// Parse an export's `conversations.json` bytes. Invalid JSON, a non-array
/// root, or a conversation count past the cap fail the whole parse; a
/// damaged conversation (including a duplicated conversation identity)
/// refuses only its entry.
pub fn parse_export(bytes: &[u8]) -> Result<ParsedExport, DesktopAdapterError> {
    let document: Value =
        serde_json::from_slice(bytes).map_err(|error| DesktopAdapterError::InvalidJson {
            detail: error.to_string(),
        })?;
    let Value::Array(conversations) = document else {
        return Err(malformed(
            "$".to_string(),
            format!(
                "expected an array of conversations, found {}",
                type_name(&document)
            ),
        ));
    };
    if conversations.len() > MAX_CONVERSATIONS_PER_EXPORT {
        return Err(malformed(
            "$".to_string(),
            format!(
                "{} conversations exceed the supported maximum of {MAX_CONVERSATIONS_PER_EXPORT}",
                conversations.len()
            ),
        ));
    }
    let mut seen_uuids: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut entries = Vec::with_capacity(conversations.len());
    for (index, conversation) in conversations.into_iter().enumerate() {
        let parsed = parse_conversation(index, conversation).and_then(|conversation| {
            if seen_uuids.insert(conversation.uuid.clone()) {
                Ok(conversation)
            } else {
                Err(malformed(
                    format!("$[{index}].uuid"),
                    format!("conversation {} appears more than once", conversation.uuid),
                ))
            }
        });
        entries.push(ParsedEntry {
            index,
            conversation: parsed,
        });
    }
    Ok(ParsedExport { entries })
}

fn parse_conversation(
    index: usize,
    conversation: Value,
) -> Result<DesktopConversation, DesktopAdapterError> {
    let location = format!("$[{index}]");
    let Value::Object(object) = conversation else {
        return Err(malformed(
            location,
            format!("expected an object, found {}", type_name(&conversation)),
        ));
    };
    let uuid = required_string(&object, "uuid", &location)?;
    let name = match object.get("name") {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(value)) => value.clone(),
        Some(other) => {
            return Err(malformed(
                format!("{location}.name"),
                format!("expected a string, found {}", type_name(other)),
            ));
        }
    };
    let raw_messages = match object.get("chat_messages") {
        Some(Value::Array(messages)) => messages.clone(),
        Some(other) => {
            return Err(malformed(
                format!("{location}.chat_messages"),
                format!("expected an array, found {}", type_name(other)),
            ));
        }
        None => {
            return Err(malformed(
                format!("{location}.chat_messages"),
                "required field is missing",
            ));
        }
    };
    if raw_messages.len() > MAX_MESSAGES_PER_CONVERSATION {
        return Err(malformed(
            format!("{location}.chat_messages"),
            format!(
                "{} messages exceed the supported maximum of {MAX_MESSAGES_PER_CONVERSATION}",
                raw_messages.len()
            ),
        ));
    }
    let mut seen_message_uuids: std::collections::BTreeSet<String> =
        std::collections::BTreeSet::new();
    let mut chat_messages = Vec::with_capacity(raw_messages.len());
    for (message_index, message) in raw_messages.into_iter().enumerate() {
        let message_location = format!("{location}.chat_messages[{message_index}]");
        let Value::Object(raw) = message else {
            return Err(malformed(
                message_location,
                format!("expected an object, found {}", type_name(&message)),
            ));
        };
        let message_uuid = required_string(&raw, "uuid", &message_location)?;
        if !seen_message_uuids.insert(message_uuid.clone()) {
            return Err(malformed(
                format!("{message_location}.uuid"),
                format!("message {message_uuid} appears more than once"),
            ));
        }
        let sender_value = required_string(&raw, "sender", &message_location)?;
        let Some(sender) = DesktopSender::parse(&sender_value) else {
            return Err(malformed(
                format!("{message_location}.sender"),
                format!("expected \"human\" or \"assistant\", found {sender_value:?}"),
            ));
        };
        if let Some(text) = raw.get("text")
            && !matches!(text, Value::String(_) | Value::Null)
        {
            return Err(malformed(
                format!("{message_location}.text"),
                format!("expected a string, found {}", type_name(text)),
            ));
        }
        if let Some(content) = raw.get("content")
            && !matches!(content, Value::Array(_) | Value::Null)
        {
            return Err(malformed(
                format!("{message_location}.content"),
                format!("expected an array, found {}", type_name(content)),
            ));
        }
        chat_messages.push(DesktopChatMessage {
            uuid: message_uuid,
            sender,
            raw,
        });
    }
    let mut envelope = object;
    envelope.remove("chat_messages");
    Ok(DesktopConversation {
        uuid,
        name,
        chat_messages,
        envelope,
    })
}

/// Render conversations back into the export-file document shape. Each
/// conversation's preserved envelope and message objects are emitted
/// verbatim, so a parse → render round trip is lossless.
pub fn render_conversations(conversations: &[DesktopConversation]) -> Value {
    Value::Array(
        conversations
            .iter()
            .map(|conversation| {
                let mut object = conversation.envelope.clone();
                object.insert(
                    "chat_messages".to_string(),
                    Value::Array(
                        conversation
                            .chat_messages
                            .iter()
                            .map(|message| Value::Object(message.raw.clone()))
                            .collect(),
                    ),
                );
                Value::Object(object)
            })
            .collect(),
    )
}
