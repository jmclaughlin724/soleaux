//! Canonical session/history service: same-platform create, list, read,
//! archive, resume, fork, lineage, and ordinal-ordered turn/message history.
//! Cross-platform continuation is a signed handoff (P5-019), never a
//! session-state transition, and no operation here writes vendor stores.

use crate::registry::{bounded_children, bounded_response, validate_json_field, validate_text};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_state::{
    CanonicalEntityInput, CanonicalRecord, MessagePayload, REGISTRY_PAGE_LIMIT_MAX,
    SESSION_STATE_ACTIVE, SESSION_STATE_ARCHIVED, SessionPayload, StateStore, TurnPayload,
    WorkspacePayload, validate_session_transition,
};
use uuid::Uuid;

pub(crate) const SESSION_SCHEMA_VERSION: &str = "soleaux.session/v1";
const LINEAGE_MAX_DEPTH: usize = 64;
const TURN_APPEND_MAX_ATTEMPTS: usize = 8;

fn validate_limit(limit: usize) -> Result<()> {
    if limit == 0 || limit > REGISTRY_PAGE_LIMIT_MAX {
        bail!("session page limit must be between 1 and {REGISTRY_PAGE_LIMIT_MAX}");
    }
    Ok(())
}

fn live_session(state: &StateStore, session_id: Uuid) -> Result<CanonicalRecord<SessionPayload>> {
    let session = state
        .get::<SessionPayload>(session_id)?
        .context("session does not exist")?;
    if session.tombstoned_at_unix_ms.is_some() {
        bail!("session is tombstoned");
    }
    Ok(session)
}

fn active_session(state: &StateStore, session_id: Uuid) -> Result<CanonicalRecord<SessionPayload>> {
    let session = live_session(state, session_id)?;
    if session.payload.session_state != SESSION_STATE_ACTIVE {
        bail!("session is not active");
    }
    Ok(session)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn create_session(
    state: &StateStore,
    workspace_id: Uuid,
    platform: &str,
    native_session_id: Option<String>,
    title: &str,
    repository_ref: Value,
    model: Option<String>,
    metadata: Value,
) -> Result<Value> {
    validate_text(platform, "session platform")?;
    validate_text(title, "session title")?;
    validate_json_field(&repository_ref, "session repository reference")?;
    validate_json_field(&metadata, "session metadata")?;
    let workspace = state
        .get::<WorkspacePayload>(workspace_id)?
        .context("workspace is not registered")?;
    if workspace.tombstoned_at_unix_ms.is_some() {
        bail!("workspace is tombstoned");
    }

    let session_id = Uuid::now_v7();
    let payload = SessionPayload {
        platform: platform.to_string(),
        native_session_id: native_session_id.clone(),
        title: title.to_string(),
        parent_session_id: None,
        lineage_root_id: session_id,
        session_state: SESSION_STATE_ACTIVE.to_string(),
        repository_ref,
        model,
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.id = Some(session_id);
    input.workspace_id = Some(workspace_id);
    let session = if let Some(native_session_id) = native_session_id {
        input.origin_platform = Some(platform.to_string());
        input.native_id = Some(native_session_id);
        state.upsert_native(input)?
    } else {
        state.put(input)?
    };
    bounded_response(json!({
        "schemaVersion": SESSION_SCHEMA_VERSION,
        "session": session,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn list_sessions(
    state: &StateStore,
    workspace_id: Option<Uuid>,
    include_archived: bool,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    validate_limit(limit)?;
    let page = state.session_page(workspace_id, include_archived, cursor, limit)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.session-list/v1",
        "sessions": page.items,
        "nextCursor": page.next_cursor,
        "truncated": page.truncated,
        "limit": limit,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn read_session(
    state: &StateStore,
    session_id: Uuid,
    after_ordinal: Option<u64>,
    turn_limit: usize,
) -> Result<Value> {
    validate_limit(turn_limit)?;
    let session = live_session(state, session_id)?;
    let turns = state.turn_page(session_id, after_ordinal, turn_limit)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.session-read/v1",
        "session": session,
        "turns": turns.items,
        "nextOrdinal": turns.next_ordinal,
        "turnsTruncated": turns.truncated,
        "productionClaimAllowed": false,
    }))
}

fn transition_session(state: &StateStore, session_id: Uuid, to_state: &str) -> Result<Value> {
    let session = live_session(state, session_id)?;
    validate_session_transition(&session.payload.session_state, to_state)?;
    let mut payload = session.payload.clone();
    payload.session_state = to_state.to_string();
    let input = CanonicalEntityInput {
        id: Some(session.id),
        workspace_id: session.workspace_id,
        parent_id: session.parent_id,
        origin_platform: session.origin_platform.clone(),
        native_id: session.native_id.clone(),
        state: to_state.to_string(),
        sensitivity: session.sensitivity,
        idempotency_key: session.idempotency_key.clone(),
        expected_revision: Some(session.revision),
        expires_at_unix_ms: session.expires_at_unix_ms,
        payload,
    };
    let session = state.put(input)?;
    bounded_response(json!({
        "schemaVersion": SESSION_SCHEMA_VERSION,
        "session": session,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn archive_session(state: &StateStore, session_id: Uuid) -> Result<Value> {
    transition_session(state, session_id, SESSION_STATE_ARCHIVED)
}

pub(crate) fn resume_session(state: &StateStore, session_id: Uuid) -> Result<Value> {
    transition_session(state, session_id, SESSION_STATE_ACTIVE)
}

pub(crate) fn fork_session(
    state: &StateStore,
    session_id: Uuid,
    title: Option<String>,
) -> Result<Value> {
    let source = live_session(state, session_id)?;
    let title = title.unwrap_or_else(|| source.payload.title.clone());
    validate_text(&title, "session title")?;
    let fork_id = Uuid::now_v7();
    let payload = SessionPayload {
        platform: source.payload.platform.clone(),
        native_session_id: None,
        title,
        parent_session_id: Some(source.id),
        lineage_root_id: source.payload.lineage_root_id,
        session_state: SESSION_STATE_ACTIVE.to_string(),
        repository_ref: source.payload.repository_ref.clone(),
        model: source.payload.model.clone(),
        metadata: source.payload.metadata.clone(),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.id = Some(fork_id);
    input.workspace_id = source.workspace_id;
    let fork = state.put(input)?;
    bounded_response(json!({
        "schemaVersion": SESSION_SCHEMA_VERSION,
        "session": fork,
        "forkedFrom": source.id,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn session_lineage(state: &StateStore, session_id: Uuid) -> Result<Value> {
    let mut chain = Vec::new();
    let mut current = Some(live_session(state, session_id)?);
    while let Some(session) = current {
        let parent_session_id = session.payload.parent_session_id;
        chain.push(session);
        if chain.len() > LINEAGE_MAX_DEPTH {
            bail!("session lineage exceeds the supported depth of {LINEAGE_MAX_DEPTH}");
        }
        current = match parent_session_id {
            Some(parent_id) => Some(
                state
                    .get::<SessionPayload>(parent_id)?
                    .context("session lineage parent does not exist")?,
            ),
            None => None,
        };
    }
    let (chain, total, truncated) = bounded_children(chain)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.session-lineage/v1",
        "lineage": chain,
        "lineageCount": total,
        "lineageTruncated": truncated,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn append_turn(
    state: &StateStore,
    session_id: Uuid,
    actor: &str,
    native_turn_id: Option<String>,
    usage: Value,
    metadata: Value,
) -> Result<Value> {
    validate_text(actor, "turn actor")?;
    validate_json_field(&usage, "turn usage")?;
    validate_json_field(&metadata, "turn metadata")?;
    let session = active_session(state, session_id)?;

    // The (kind, workspace, idempotency-key) unique index makes the ordinal
    // race-free: a concurrent append claims the same key, the put collides,
    // and this loop re-reads the next free ordinal.
    let mut last_error = None;
    for _ in 0..TURN_APPEND_MAX_ATTEMPTS {
        let ordinal = state.next_turn_ordinal(session_id)?;
        let payload = TurnPayload {
            session_id,
            ordinal,
            actor: actor.to_string(),
            native_turn_id: native_turn_id.clone(),
            turn_state: "recorded".to_string(),
            usage: usage.clone(),
            metadata: metadata.clone(),
        };
        let mut input = CanonicalEntityInput::active(payload);
        input.workspace_id = session.workspace_id;
        input.parent_id = Some(session_id);
        input.idempotency_key = Some(format!("turn:{session_id}:{ordinal}"));
        match state.put(input) {
            Ok(turn) => {
                return bounded_response(json!({
                    "schemaVersion": "soleaux.turn/v1",
                    "turn": turn,
                    "productionClaimAllowed": false,
                }));
            }
            Err(error) if error.to_string().contains("idempotency collision") => {
                last_error = Some(error);
            }
            Err(error) => return Err(error),
        }
    }
    Err(last_error.unwrap_or_else(|| {
        anyhow::anyhow!(
            "turn append could not claim an ordinal after {TURN_APPEND_MAX_ATTEMPTS} attempts"
        )
    }))
}

pub(crate) fn list_turns(
    state: &StateStore,
    session_id: Uuid,
    after_ordinal: Option<u64>,
    limit: usize,
) -> Result<Value> {
    validate_limit(limit)?;
    live_session(state, session_id)?;
    let page = state.turn_page(session_id, after_ordinal, limit)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.turn-list/v1",
        "turns": page.items,
        "nextOrdinal": page.next_ordinal,
        "truncated": page.truncated,
        "limit": limit,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn append_message(
    state: &StateStore,
    turn_id: Uuid,
    role: &str,
    native_message_id: Option<String>,
    model: Option<String>,
    metadata: Value,
) -> Result<Value> {
    validate_text(role, "message role")?;
    validate_json_field(&metadata, "message metadata")?;
    let turn = state
        .get::<TurnPayload>(turn_id)?
        .context("turn does not exist")?;
    if turn.tombstoned_at_unix_ms.is_some() {
        bail!("turn is tombstoned");
    }
    let session = active_session(state, turn.payload.session_id)?;
    let payload = MessagePayload {
        session_id: session.id,
        turn_id,
        role: role.to_string(),
        native_message_id: native_message_id.clone(),
        model,
        message_state: "recorded".to_string(),
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.workspace_id = session.workspace_id;
    input.parent_id = Some(turn_id);
    let message = if let Some(native_message_id) = native_message_id {
        input.origin_platform = Some(session.payload.platform.clone());
        input.native_id = Some(native_message_id);
        state.upsert_native(input)?
    } else {
        state.put(input)?
    };
    bounded_response(json!({
        "schemaVersion": "soleaux.message/v1",
        "message": message,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn list_messages(
    state: &StateStore,
    turn_id: Uuid,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    validate_limit(limit)?;
    let (items, next_cursor, truncated) =
        state.child_page::<MessagePayload>(turn_id, cursor, limit)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.message-list/v1",
        "messages": items,
        "nextCursor": next_cursor,
        "truncated": truncated,
        "limit": limit,
        "productionClaimAllowed": false,
    }))
}
