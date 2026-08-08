//! Export direction: one canonical session → a Desktop-shaped conversation
//! the user takes to Desktop through its supported flows.
//!
//! A session that was imported from a Desktop export reproduces its
//! preserved envelope and message objects verbatim, so import → export is
//! lossless. A natively authored canonical session is constructed into the
//! same shape when its roles map onto the documented senders; anything else
//! refuses with a typed error instead of exporting a lossy approximation.

use crate::CLAUDE_DESKTOP_PLATFORM_ID;
use crate::types::{DesktopAdapterError, DesktopChatMessage, DesktopConversation, DesktopSender};
use serde_json::{Map, Value, json};
use soleaux_state::{
    CanonicalRecord, MessagePayload, REGISTRY_PAGE_LIMIT_MAX, SessionPayload, StateStore,
    TurnPayload,
};
use uuid::Uuid;

fn store_error(error: anyhow::Error) -> DesktopAdapterError {
    DesktopAdapterError::Store {
        detail: format!("{error:#}"),
    }
}

fn not_exportable(session_id: Uuid, detail: impl Into<String>) -> DesktopAdapterError {
    DesktopAdapterError::NotExportable {
        session_id: session_id.to_string(),
        detail: detail.into(),
    }
}

fn sender_for_role(role: &str) -> Option<DesktopSender> {
    match role {
        "human" | "user" => Some(DesktopSender::Human),
        "assistant" => Some(DesktopSender::Assistant),
        _ => None,
    }
}

pub(crate) fn export_session(
    state: &StateStore,
    session_id: Uuid,
) -> Result<DesktopConversation, DesktopAdapterError> {
    let session = state
        .get::<SessionPayload>(session_id)
        .map_err(store_error)?
        .ok_or_else(|| not_exportable(session_id, "session does not exist"))?;
    if session.tombstoned_at_unix_ms.is_some() {
        return Err(not_exportable(session_id, "session is tombstoned"));
    }
    let conversation_uuid = session
        .native_id
        .clone()
        .filter(|_| session.origin_platform.as_deref() == Some(CLAUDE_DESKTOP_PLATFORM_ID))
        .unwrap_or_else(|| session.id.to_string());
    let envelope = envelope_for(&session, &conversation_uuid);
    let name = match envelope.get("name") {
        Some(Value::String(name)) => name.clone(),
        _ => session.payload.title.clone(),
    };

    let mut chat_messages = Vec::new();
    let mut after_ordinal = None;
    loop {
        let page = state
            .turn_page(session.id, after_ordinal, REGISTRY_PAGE_LIMIT_MAX)
            .map_err(store_error)?;
        for turn in &page.items {
            let messages = turn_messages(state, turn.id)?;
            if messages.is_empty() {
                return Err(not_exportable(
                    session.id,
                    format!(
                        "turn ordinal {} has no message to represent",
                        turn.payload.ordinal
                    ),
                ));
            }
            for message in messages {
                chat_messages.push(chat_message_for(&session, turn, &message)?);
            }
        }
        if !page.truncated {
            break;
        }
        after_ordinal = page.next_ordinal;
    }

    Ok(DesktopConversation {
        uuid: conversation_uuid,
        name,
        chat_messages,
        envelope,
    })
}

/// The imported envelope verbatim when present, otherwise a constructed one.
fn envelope_for(
    session: &CanonicalRecord<SessionPayload>,
    conversation_uuid: &str,
) -> Map<String, Value> {
    if let Some(Value::Object(envelope)) = session.payload.metadata.get("conversation") {
        return envelope.clone();
    }
    let mut envelope = Map::new();
    envelope.insert("uuid".to_string(), json!(conversation_uuid));
    envelope.insert("name".to_string(), json!(session.payload.title));
    envelope.insert(
        "created_at".to_string(),
        json!(format_unix_ms_utc(session.created_at_unix_ms)),
    );
    envelope.insert(
        "updated_at".to_string(),
        json!(format_unix_ms_utc(session.updated_at_unix_ms)),
    );
    envelope
}

fn turn_messages(
    state: &StateStore,
    turn_id: Uuid,
) -> Result<Vec<CanonicalRecord<MessagePayload>>, DesktopAdapterError> {
    let mut messages = Vec::new();
    let mut cursor = None;
    loop {
        let (items, next_cursor, truncated) = state
            .child_page::<MessagePayload>(turn_id, cursor, REGISTRY_PAGE_LIMIT_MAX)
            .map_err(store_error)?;
        messages.extend(items);
        if !truncated {
            return Ok(messages);
        }
        cursor = next_cursor;
    }
}

fn chat_message_for(
    session: &CanonicalRecord<SessionPayload>,
    turn: &CanonicalRecord<TurnPayload>,
    message: &CanonicalRecord<MessagePayload>,
) -> Result<DesktopChatMessage, DesktopAdapterError> {
    let message_uuid = message
        .payload
        .native_message_id
        .clone()
        .unwrap_or_else(|| message.id.to_string());
    if let Some(Value::Object(raw)) = message.payload.metadata.get("chatMessage") {
        let sender = raw
            .get("sender")
            .and_then(Value::as_str)
            .and_then(DesktopSender::parse)
            .or_else(|| sender_for_role(&message.payload.role))
            .ok_or_else(|| {
                not_exportable(
                    session.id,
                    format!("message {message_uuid} preserves no representable sender"),
                )
            })?;
        return Ok(DesktopChatMessage {
            uuid: message_uuid,
            sender,
            raw: raw.clone(),
        });
    }
    let sender = sender_for_role(&message.payload.role).ok_or_else(|| {
        not_exportable(
            session.id,
            format!(
                "turn ordinal {} role {:?} has no documented sender",
                turn.payload.ordinal, message.payload.role
            ),
        )
    })?;
    let text = match message.payload.metadata.get("text") {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    };
    let mut raw = Map::new();
    raw.insert("uuid".to_string(), json!(message_uuid));
    raw.insert("text".to_string(), json!(text));
    raw.insert(
        "content".to_string(),
        json!([{"type": "text", "text": text}]),
    );
    raw.insert("sender".to_string(), json!(sender.as_str()));
    raw.insert(
        "created_at".to_string(),
        json!(format_unix_ms_utc(message.created_at_unix_ms)),
    );
    raw.insert(
        "updated_at".to_string(),
        json!(format_unix_ms_utc(message.updated_at_unix_ms)),
    );
    raw.insert("attachments".to_string(), json!([]));
    raw.insert("files".to_string(), json!([]));
    Ok(DesktopChatMessage {
        uuid: message_uuid,
        sender,
        raw,
    })
}

/// Format a unix-millisecond instant as an RFC 3339 UTC timestamp with
/// millisecond precision, the shape Desktop exports use.
pub fn format_unix_ms_utc(unix_ms: i64) -> String {
    let seconds = unix_ms.div_euclid(1000);
    let millisecond = unix_ms.rem_euclid(1000);
    let days = seconds.div_euclid(86_400);
    let second_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = second_of_day / 3600;
    let minute = second_of_day % 3600 / 60;
    let second = second_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millisecond:03}Z")
}

/// Proleptic-Gregorian date for a day count since 1970-01-01 (Howard
/// Hinnant's `civil_from_days`).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month as u32, day as u32)
}
