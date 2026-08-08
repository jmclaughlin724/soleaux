//! Import direction: Desktop export → canonical sessions, turns, and
//! messages through native-identity upserts.
//!
//! Every conversation imports all-or-nothing: the whole plan is validated
//! against existing canonical state before the first write, so a refusal
//! leaves the store untouched. Idempotence comes from the store's
//! `(kind, origin_platform, native_id)` identity — a re-import replays into
//! the same canonical rows instead of duplicating them. The import creates a
//! NEW canonical session with its Desktop origin recorded in the session
//! metadata; it never claims a native resume (GAP-016).

use crate::CLAUDE_DESKTOP_PLATFORM_ID;
use crate::types::{DesktopAdapterError, DesktopChatMessage, DesktopConversation, ParsedExport};
use serde_json::{Value, json};
use soleaux_state::{
    CanonicalEntityInput, CanonicalRecord, MessagePayload, REGISTRY_PAGE_LIMIT_MAX,
    SESSION_STATE_ACTIVE, SessionPayload, StateStore, TurnPayload,
};
use std::collections::BTreeMap;
use uuid::Uuid;

/// Recorded in every imported session's metadata as `importedFrom`.
pub const IMPORT_ORIGIN: &str = "claude_desktop_account_data_export";

/// Schema version of the metadata this importer writes.
pub const IMPORT_METADATA_SCHEMA_VERSION: &str = "soleaux.claude-desktop-import/v1";

/// One conversation imported into the canonical store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportedConversation {
    pub conversation_uuid: String,
    pub session_id: Uuid,
    pub turns: usize,
    /// False when the conversation was already imported and this run
    /// replayed or updated the existing canonical rows.
    pub created: bool,
}

/// One conversation refused with a typed error and zero writes.
#[derive(Debug, Clone, PartialEq)]
pub struct RefusedConversation {
    pub index: usize,
    pub conversation_uuid: Option<String>,
    pub error: DesktopAdapterError,
}

/// Outcome of importing one export file.
#[derive(Debug, Clone, PartialEq)]
pub struct ImportReport {
    pub imported: Vec<ImportedConversation>,
    pub refused: Vec<RefusedConversation>,
}

fn store_error(error: anyhow::Error) -> DesktopAdapterError {
    DesktopAdapterError::Store {
        detail: format!("{error:#}"),
    }
}

fn diverged(conversation: &DesktopConversation, detail: impl Into<String>) -> DesktopAdapterError {
    DesktopAdapterError::SessionDiverged {
        conversation_uuid: conversation.uuid.clone(),
        detail: detail.into(),
    }
}

pub(crate) fn import_export(
    state: &StateStore,
    workspace_id: Uuid,
    export: &ParsedExport,
) -> ImportReport {
    let mut imported = Vec::new();
    let mut refused = Vec::new();
    for entry in &export.entries {
        match &entry.conversation {
            Ok(conversation) => match import_conversation(state, workspace_id, conversation) {
                Ok(outcome) => imported.push(outcome),
                Err(error) => refused.push(RefusedConversation {
                    index: entry.index,
                    conversation_uuid: Some(conversation.uuid.clone()),
                    error,
                }),
            },
            Err(error) => refused.push(RefusedConversation {
                index: entry.index,
                conversation_uuid: None,
                error: error.clone(),
            }),
        }
    }
    ImportReport { imported, refused }
}

pub(crate) fn import_conversation(
    state: &StateStore,
    workspace_id: Uuid,
    conversation: &DesktopConversation,
) -> Result<ImportedConversation, DesktopAdapterError> {
    let existing = preflight(state, workspace_id, conversation)?;

    // Write phase. Pre-flight proved every planned row either does not exist
    // or already carries the planned identity, so from here only
    // infrastructure failures remain. A concurrent writer racing this
    // single-conversation import could still surface as a store error; the
    // daemon owns writes, so imports are not raced in practice.
    let created = existing.is_none();
    let session = upsert_session(state, workspace_id, conversation, existing)?;
    for (index, message) in conversation.chat_messages.iter().enumerate() {
        let turn = upsert_turn(state, workspace_id, session.id, index, message)?;
        upsert_message(state, workspace_id, session.id, turn.id, message)?;
    }
    Ok(ImportedConversation {
        conversation_uuid: conversation.uuid.clone(),
        session_id: session.id,
        turns: conversation.chat_messages.len(),
        created,
    })
}

/// Validate the whole conversation against existing canonical state. Returns
/// the previously imported session, if any. No writes happen here.
fn preflight(
    state: &StateStore,
    workspace_id: Uuid,
    conversation: &DesktopConversation,
) -> Result<Option<CanonicalRecord<SessionPayload>>, DesktopAdapterError> {
    let existing = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, &conversation.uuid)
        .map_err(store_error)?;
    let occupied = match &existing {
        Some(session) => {
            if session.workspace_id != Some(workspace_id) {
                return Err(DesktopAdapterError::WorkspaceMismatch {
                    conversation_uuid: conversation.uuid.clone(),
                });
            }
            if session.tombstoned_at_unix_ms.is_some() {
                return Err(diverged(
                    conversation,
                    "the previously imported session is tombstoned",
                ));
            }
            existing_turn_ordinals(state, session.id)?
        }
        None => BTreeMap::new(),
    };
    for (index, message) in conversation.chat_messages.iter().enumerate() {
        let ordinal = index as u64;
        if let Some(native_id) = occupied.get(&ordinal)
            && native_id.as_deref() != Some(message.uuid.as_str())
        {
            return Err(diverged(
                conversation,
                format!("turn ordinal {ordinal} is already occupied by another turn"),
            ));
        }
        let turn = state
            .get_by_native::<TurnPayload>(CLAUDE_DESKTOP_PLATFORM_ID, &message.uuid)
            .map_err(store_error)?;
        if let Some(turn) = &turn {
            let session_id = existing.as_ref().map(|session| session.id);
            if turn.tombstoned_at_unix_ms.is_some()
                || turn.workspace_id != Some(workspace_id)
                || turn.parent_id != session_id
                || turn.payload.ordinal != ordinal
            {
                return Err(diverged(
                    conversation,
                    format!(
                        "message {} maps to a conflicting canonical turn",
                        message.uuid
                    ),
                ));
            }
        }
        let canonical_message = state
            .get_by_native::<MessagePayload>(CLAUDE_DESKTOP_PLATFORM_ID, &message.uuid)
            .map_err(store_error)?;
        if let Some(canonical_message) = canonical_message {
            let turn_id = turn.as_ref().map(|turn| turn.id);
            if canonical_message.tombstoned_at_unix_ms.is_some()
                || canonical_message.workspace_id != Some(workspace_id)
                || turn_id.is_none()
                || canonical_message.parent_id != turn_id
            {
                return Err(diverged(
                    conversation,
                    format!(
                        "message {} maps to a conflicting canonical message",
                        message.uuid
                    ),
                ));
            }
        }
    }
    Ok(existing)
}

fn existing_turn_ordinals(
    state: &StateStore,
    session_id: Uuid,
) -> Result<BTreeMap<u64, Option<String>>, DesktopAdapterError> {
    let mut occupied = BTreeMap::new();
    let mut after_ordinal = None;
    loop {
        let page = state
            .turn_page(session_id, after_ordinal, REGISTRY_PAGE_LIMIT_MAX)
            .map_err(store_error)?;
        for turn in &page.items {
            occupied.insert(turn.payload.ordinal, turn.native_id.clone());
        }
        if !page.truncated {
            return Ok(occupied);
        }
        after_ordinal = page.next_ordinal;
    }
}

fn upsert_session(
    state: &StateStore,
    workspace_id: Uuid,
    conversation: &DesktopConversation,
    existing: Option<CanonicalRecord<SessionPayload>>,
) -> Result<CanonicalRecord<SessionPayload>, DesktopAdapterError> {
    let title = if conversation.name.trim().is_empty() {
        conversation.uuid.clone()
    } else {
        conversation.name.clone()
    };
    let metadata = json!({
        "schemaVersion": IMPORT_METADATA_SCHEMA_VERSION,
        "importedFrom": IMPORT_ORIGIN,
        "conversation": Value::Object(conversation.envelope.clone()),
    });
    // A new import is a NEW canonical session that records its origin; a
    // re-import preserves the existing identity, lineage, and lifecycle
    // state so replay never resurrects an archived or renamed session
    // transitionlessly.
    let (session_id, entity_state, session_state, lineage_root_id, parent_session_id, model) =
        match &existing {
            Some(session) => (
                session.id,
                session.state.clone(),
                session.payload.session_state.clone(),
                session.payload.lineage_root_id,
                session.payload.parent_session_id,
                session.payload.model.clone(),
            ),
            None => {
                let session_id = Uuid::now_v7();
                (
                    session_id,
                    SESSION_STATE_ACTIVE.to_string(),
                    SESSION_STATE_ACTIVE.to_string(),
                    session_id,
                    None,
                    None,
                )
            }
        };
    let payload = SessionPayload {
        platform: CLAUDE_DESKTOP_PLATFORM_ID.to_string(),
        native_session_id: Some(conversation.uuid.clone()),
        title,
        parent_session_id,
        lineage_root_id,
        session_state,
        repository_ref: Value::Null,
        model,
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.id = Some(session_id);
    input.workspace_id = Some(workspace_id);
    input.origin_platform = Some(CLAUDE_DESKTOP_PLATFORM_ID.to_string());
    input.native_id = Some(conversation.uuid.clone());
    input.state = entity_state;
    if let Some(session) = &existing {
        input.sensitivity = session.sensitivity;
    }
    state.upsert_native(input).map_err(store_error)
}

fn upsert_turn(
    state: &StateStore,
    workspace_id: Uuid,
    session_id: Uuid,
    index: usize,
    message: &DesktopChatMessage,
) -> Result<CanonicalRecord<TurnPayload>, DesktopAdapterError> {
    let ordinal = index as u64;
    let payload = TurnPayload {
        session_id,
        ordinal,
        actor: message.sender.as_str().to_string(),
        native_turn_id: Some(message.uuid.clone()),
        turn_state: "recorded".to_string(),
        usage: json!({}),
        metadata: json!({}),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.workspace_id = Some(workspace_id);
    input.parent_id = Some(session_id);
    input.origin_platform = Some(CLAUDE_DESKTOP_PLATFORM_ID.to_string());
    input.native_id = Some(message.uuid.clone());
    // The same key the canonical append surface claims, so imported turns
    // and appended turns can never silently share an ordinal.
    input.idempotency_key = Some(format!("turn:{session_id}:{ordinal}"));
    state.upsert_native(input).map_err(store_error)
}

fn upsert_message(
    state: &StateStore,
    workspace_id: Uuid,
    session_id: Uuid,
    turn_id: Uuid,
    message: &DesktopChatMessage,
) -> Result<CanonicalRecord<MessagePayload>, DesktopAdapterError> {
    let payload = MessagePayload {
        session_id,
        turn_id,
        role: message.sender.as_str().to_string(),
        native_message_id: Some(message.uuid.clone()),
        model: None,
        message_state: "recorded".to_string(),
        metadata: json!({
            "schemaVersion": IMPORT_METADATA_SCHEMA_VERSION,
            "chatMessage": Value::Object(message.raw.clone()),
        }),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.workspace_id = Some(workspace_id);
    input.parent_id = Some(turn_id);
    input.origin_platform = Some(CLAUDE_DESKTOP_PLATFORM_ID.to_string());
    input.native_id = Some(message.uuid.clone());
    state.upsert_native(input).map_err(store_error)
}
