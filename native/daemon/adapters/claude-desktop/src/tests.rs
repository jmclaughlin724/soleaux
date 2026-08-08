//! Adapter tests: defensive export parsing, all-or-nothing import through
//! native-identity upserts, lossless round trips, the read-only source
//! proof, and the documentation-contract pin against the embedded matrix.

use crate::types::{DesktopAdapterError, parse_export, render_conversations};
use crate::{
    CLAUDE_DESKTOP_PLATFORM_ID, ClaudeDesktopAdapter, IMPORT_ORIGIN, MATRIX_VERSION, WRITE_POLICY,
    format_unix_ms_utc, local_connector_materialization, read_export_file, soleaux_local_connector,
};
use serde_json::{Value, json};
use soleaux_state::{
    CanonicalEntityInput, CanonicalRecord, MessagePayload, REGISTRY_PAGE_LIMIT_MAX, SessionPayload,
    StateStore, TurnPayload,
};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const CONVERSATION_ONE: &str = "9c5f2b1e-6f0a-4c3d-8e21-5a4b7c9d0e1f";
const CONVERSATION_TWO: &str = "0d1e2f3a-4b5c-4d6e-8f7a-9b0c1d2e3f4a";

fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join(name)
}

fn representative_bytes() -> Vec<u8> {
    std::fs::read(fixture_path("conversations-representative.json")).expect("fixture reads")
}

fn open_store(directory: &Path) -> StateStore {
    StateStore::open(directory.join("state.sqlite3")).expect("state store opens")
}

fn session_turns(state: &StateStore, session_id: Uuid) -> Vec<CanonicalRecord<TurnPayload>> {
    let mut turns = Vec::new();
    let mut after_ordinal = None;
    loop {
        let page = state
            .turn_page(session_id, after_ordinal, REGISTRY_PAGE_LIMIT_MAX)
            .expect("turn page");
        turns.extend(page.items);
        if !page.truncated {
            return turns;
        }
        after_ordinal = page.next_ordinal;
    }
}

fn turn_message(state: &StateStore, turn_id: Uuid) -> CanonicalRecord<MessagePayload> {
    let (messages, _, _) = state
        .child_page::<MessagePayload>(turn_id, None, REGISTRY_PAGE_LIMIT_MAX)
        .expect("message page");
    assert_eq!(
        messages.len(),
        1,
        "imported turns carry exactly one message"
    );
    messages.into_iter().next().expect("message")
}

// --- defensive parsing ----------------------------------------------------

#[test]
fn representative_fixture_parses_with_unknown_fields_preserved() {
    let export = parse_export(&representative_bytes()).expect("fixture parses");
    assert_eq!(export.entries.len(), 2);
    let conversations: Vec<_> = export.valid().collect();
    assert_eq!(conversations.len(), 2);

    let first = conversations[0];
    assert_eq!(first.uuid, CONVERSATION_ONE);
    assert_eq!(first.name, "Plan the release checklist");
    assert_eq!(first.chat_messages.len(), 3);
    assert_eq!(first.chat_messages[0].sender.as_str(), "human");
    assert_eq!(first.chat_messages[1].sender.as_str(), "assistant");
    assert!(
        first.envelope.contains_key("summary"),
        "unknown conversation fields must be preserved"
    );
    assert!(
        !first.envelope.contains_key("chat_messages"),
        "the envelope excludes the message array"
    );
    assert_eq!(
        first.chat_messages[1].raw["content"][0]["type"],
        json!("thinking"),
        "unknown content block kinds must be preserved"
    );
    assert!(
        !first.chat_messages[2].raw.contains_key("text"),
        "a message without a text field stays without one"
    );

    let second = conversations[1];
    assert_eq!(second.uuid, CONVERSATION_TWO);
    assert_eq!(second.name, "");
    assert!(second.chat_messages.is_empty());
}

#[test]
fn truncated_export_file_refuses_with_typed_errors() {
    let bytes =
        read_export_file(&fixture_path("conversations-truncated.json")).expect("fixture reads");
    let error = parse_export(&bytes).expect_err("truncated JSON must refuse");
    assert!(matches!(error, DesktopAdapterError::InvalidJson { .. }));

    let missing = read_export_file(&fixture_path("does-not-exist.json"))
        .expect_err("a missing user file surfaces a typed io error");
    assert!(matches!(missing, DesktopAdapterError::Io { .. }));
}

#[test]
fn file_level_damage_refuses_wholesale_and_entry_damage_refuses_per_conversation() {
    let root = parse_export(b"{\"conversations\": []}").expect_err("non-array root refuses");
    assert!(
        matches!(&root, DesktopAdapterError::Malformed { location, .. } if location == "$"),
        "unexpected error: {root}"
    );

    let damaged = json!([
        {"uuid": "conv-ok", "name": "fine", "chat_messages": []},
        {"name": "no uuid", "chat_messages": []},
        {"uuid": "conv-bad-sender", "chat_messages": [
            {"uuid": "m1", "sender": "system", "text": "x"}
        ]},
        {"uuid": "conv-no-messages-key", "name": "cut"},
        {"uuid": "conv-ok", "name": "duplicate identity", "chat_messages": []},
        {"uuid": "conv-duplicate-message", "chat_messages": [
            {"uuid": "m2", "sender": "human", "text": "a"},
            {"uuid": "m2", "sender": "human", "text": "b"}
        ]},
        "not an object"
    ]);
    let export = parse_export(damaged.to_string().as_bytes()).expect("file level parses");
    let refused: Vec<_> = export
        .entries
        .iter()
        .filter_map(|entry| {
            entry
                .conversation
                .as_ref()
                .err()
                .map(|error| (entry.index, error))
        })
        .collect();
    assert_eq!(export.valid().count(), 1);
    assert_eq!(refused.len(), 6);
    for (index, expected) in [
        (1, ".uuid"),
        (2, ".sender"),
        (3, ".chat_messages"),
        (4, ".uuid"),
        (5, ".uuid"),
        (6, "$[6]"),
    ] {
        let (_, error) = refused
            .iter()
            .find(|(refused_index, _)| *refused_index == index)
            .expect("refused entry");
        match error {
            DesktopAdapterError::Malformed { location, .. } => {
                assert!(location.contains(expected), "{index}: {location}");
            }
            other => panic!("expected a malformed refusal, got {other}"),
        }
    }
}

// --- import ---------------------------------------------------------------

#[test]
fn import_creates_canonical_entities_with_recorded_origin() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();
    let export = parse_export(&representative_bytes()).expect("fixture parses");

    let report = adapter.import_export(workspace_id, &export);
    assert!(report.refused.is_empty(), "refused: {:?}", report.refused);
    assert_eq!(report.imported.len(), 2);
    assert!(report.imported.iter().all(|imported| imported.created));

    let session = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("imported session exists");
    assert_eq!(
        session.origin_platform.as_deref(),
        Some(CLAUDE_DESKTOP_PLATFORM_ID)
    );
    assert_eq!(session.native_id.as_deref(), Some(CONVERSATION_ONE));
    assert_eq!(session.workspace_id, Some(workspace_id));
    assert_eq!(session.payload.platform, CLAUDE_DESKTOP_PLATFORM_ID);
    assert_eq!(
        session.payload.native_session_id.as_deref(),
        Some(CONVERSATION_ONE)
    );
    assert_eq!(session.payload.session_state, "active");
    assert_eq!(session.payload.title, "Plan the release checklist");
    assert_eq!(
        session.payload.metadata["importedFrom"],
        json!(IMPORT_ORIGIN)
    );
    assert_eq!(
        session.payload.metadata["conversation"]["summary"],
        json!("unknown-field: newer exports may add fields; the parser must tolerate them"),
        "the conversation envelope is recorded with the origin"
    );
    // A new canonical session, never a native resume: its lineage starts here.
    assert_eq!(session.payload.lineage_root_id, session.id);
    assert_eq!(session.payload.parent_session_id, None);

    let turns = session_turns(&state, session.id);
    assert_eq!(turns.len(), 3);
    for (index, turn) in turns.iter().enumerate() {
        assert_eq!(turn.payload.ordinal, index as u64);
        assert_eq!(
            turn.origin_platform.as_deref(),
            Some(CLAUDE_DESKTOP_PLATFORM_ID)
        );
        assert_eq!(
            turn.idempotency_key.as_deref(),
            Some(format!("turn:{}:{index}", session.id).as_str()),
            "imported turns claim the canonical append ordinal key"
        );
        let message = turn_message(&state, turn.id);
        assert_eq!(message.native_id, turn.native_id);
        assert_eq!(message.payload.role, turn.payload.actor);
        assert_eq!(
            message.payload.metadata["chatMessage"]["uuid"],
            json!(turn.native_id.as_deref().expect("native id")),
            "the exported message object is preserved verbatim"
        );
    }
    assert_eq!(turns[0].payload.actor, "human");
    assert_eq!(turns[1].payload.actor, "assistant");

    let empty = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_TWO)
        .expect("lookup")
        .expect("empty conversation still imports");
    assert_eq!(
        empty.payload.title, CONVERSATION_TWO,
        "an unnamed conversation falls back to its identity"
    );
    assert!(session_turns(&state, empty.id).is_empty());
}

#[test]
fn reimport_replays_into_the_same_rows_without_duplicates() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();
    let export = parse_export(&representative_bytes()).expect("fixture parses");

    let first = adapter.import_export(workspace_id, &export);
    let second = adapter.import_export(workspace_id, &export);
    assert!(second.refused.is_empty(), "refused: {:?}", second.refused);
    assert_eq!(first.imported.len(), second.imported.len());
    for (initial, replay) in first.imported.iter().zip(&second.imported) {
        assert_eq!(
            initial.session_id, replay.session_id,
            "re-import maps to the same session"
        );
        assert!(initial.created);
        assert!(
            !replay.created,
            "the second import replays instead of creating"
        );
    }

    let session = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("session exists");
    assert_eq!(session.revision, 1, "an identical re-import bumps nothing");
    let turns = session_turns(&state, session.id);
    assert_eq!(turns.len(), 3, "re-import must not duplicate turns");
    for turn in &turns {
        assert_eq!(turn.revision, 1);
        assert_eq!(turn_message(&state, turn.id).revision, 1);
    }
}

#[test]
fn a_conversation_imported_elsewhere_refuses_the_other_workspace() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let export = parse_export(&representative_bytes()).expect("fixture parses");
    let home = Uuid::now_v7();
    let other = Uuid::now_v7();

    assert!(adapter.import_export(home, &export).refused.is_empty());
    let report = adapter.import_export(other, &export);
    assert!(report.imported.is_empty());
    assert_eq!(report.refused.len(), 2);
    for refused in &report.refused {
        assert!(
            matches!(
                &refused.error,
                DesktopAdapterError::WorkspaceMismatch { .. }
            ),
            "unexpected refusal: {}",
            refused.error
        );
    }
    let session = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("session exists");
    assert_eq!(
        session.workspace_id,
        Some(home),
        "the refusal wrote nothing"
    );
    assert_eq!(session.revision, 1);
}

#[test]
fn a_malformed_conversation_refuses_all_or_nothing_while_valid_ones_import() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();

    let mut document: Value =
        serde_json::from_slice(&representative_bytes()).expect("fixture is json");
    document[0]["chat_messages"][1]["sender"] = json!("system");
    let export = parse_export(document.to_string().as_bytes()).expect("file level parses");

    let report = adapter.import_export(workspace_id, &export);
    assert_eq!(report.imported.len(), 1);
    assert_eq!(report.imported[0].conversation_uuid, CONVERSATION_TWO);
    assert_eq!(report.refused.len(), 1);
    assert!(matches!(
        &report.refused[0].error,
        DesktopAdapterError::Malformed { location, .. } if location.contains(".sender")
    ));
    assert!(
        state
            .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
            .expect("lookup")
            .is_none(),
        "a conversation with one malformed message imports nothing at all"
    );
}

#[test]
fn divergent_canonical_state_refuses_before_any_write() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();
    let export = parse_export(&representative_bytes()).expect("fixture parses");
    assert!(
        adapter
            .import_export(workspace_id, &export)
            .refused
            .is_empty()
    );
    let session = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("session exists");

    // A turn appended outside the import now occupies ordinal 3.
    let mut foreign = CanonicalEntityInput::active(TurnPayload {
        session_id: session.id,
        ordinal: 3,
        actor: "assistant".to_string(),
        native_turn_id: None,
        turn_state: "recorded".to_string(),
        usage: json!({}),
        metadata: json!({}),
    });
    foreign.workspace_id = Some(workspace_id);
    foreign.parent_id = Some(session.id);
    state.put(foreign).expect("foreign turn appends");

    // A newer export grew by one message that would claim that ordinal.
    let mut document: Value =
        serde_json::from_slice(&representative_bytes()).expect("fixture is json");
    document[0]["chat_messages"]
        .as_array_mut()
        .expect("messages")
        .push(json!({
            "uuid": "b6d4f2a0-3e5c-4b9d-1f4a-6c8e0d2f3b5a",
            "text": "One more follow-up.",
            "sender": "human",
            "attachments": [],
            "files": []
        }));
    let grown = parse_export(document.to_string().as_bytes()).expect("file level parses");

    let report = adapter.import_export(workspace_id, &grown);
    let refusal = report
        .refused
        .iter()
        .find(|refused| refused.conversation_uuid.as_deref() == Some(CONVERSATION_ONE))
        .expect("the grown conversation is refused");
    assert!(
        matches!(&refusal.error, DesktopAdapterError::SessionDiverged { detail, .. }
            if detail.contains("ordinal 3")),
        "unexpected refusal: {}",
        refusal.error
    );
    assert_eq!(
        session_turns(&state, session.id).len(),
        4,
        "the refused conversation wrote nothing: three imported turns plus the foreign one"
    );
    assert!(
        state
            .get_by_native::<MessagePayload>(
                CLAUDE_DESKTOP_PLATFORM_ID,
                "b6d4f2a0-3e5c-4b9d-1f4a-6c8e0d2f3b5a",
            )
            .expect("lookup")
            .is_none(),
        "no partial message import"
    );
}

// --- round trips ----------------------------------------------------------

#[test]
fn import_then_export_reproduces_the_desktop_document_losslessly() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();
    let export = parse_export(&representative_bytes()).expect("fixture parses");
    let report = adapter.import_export(workspace_id, &export);

    let fixture: Value = serde_json::from_slice(&representative_bytes()).expect("fixture is json");
    for (index, imported) in report.imported.iter().enumerate() {
        let conversation = adapter
            .export_session(imported.session_id)
            .expect("exports");
        let rendered = render_conversations(std::slice::from_ref(&conversation));
        assert_eq!(
            rendered[0], fixture[index],
            "conversation {index} must round trip losslessly in value space"
        );
    }
}

#[test]
fn canonical_sessions_round_trip_through_a_user_authorized_export_file() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();
    let export = parse_export(&representative_bytes()).expect("fixture parses");
    let report = adapter.import_export(workspace_id, &export);
    let session_id = report.imported[0].session_id;

    let destination = directory.path().join("exported-conversation.json");
    adapter
        .write_export_file(session_id, &destination)
        .expect("writes the user-authorized file");

    let second_directory = tempfile::TempDir::new().expect("tempdir");
    let second_state = open_store(second_directory.path());
    let second_adapter = ClaudeDesktopAdapter::new(second_state.clone());
    let second_workspace = Uuid::now_v7();
    let reimported = second_adapter
        .import_export_file(second_workspace, &destination)
        .expect("the exported file parses");
    assert!(
        reimported.refused.is_empty(),
        "refused: {:?}",
        reimported.refused
    );
    assert_eq!(reimported.imported.len(), 1);

    let original = state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("original session");
    let round_tripped = second_state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, CONVERSATION_ONE)
        .expect("lookup")
        .expect("round-tripped session");
    assert_ne!(
        original.id, round_tripped.id,
        "a fresh canonical session, not a resume"
    );
    assert_eq!(original.payload.title, round_tripped.payload.title);
    assert_eq!(original.payload.metadata, round_tripped.payload.metadata);
    assert_eq!(
        session_turns(&state, original.id).len(),
        session_turns(&second_state, round_tripped.id).len()
    );
}

#[test]
fn natively_authored_sessions_export_and_reimport() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();

    let session_id = Uuid::now_v7();
    let mut session = CanonicalEntityInput::active(SessionPayload {
        platform: "soleaux".to_string(),
        native_session_id: None,
        title: "native authoring".to_string(),
        parent_session_id: None,
        lineage_root_id: session_id,
        session_state: "active".to_string(),
        repository_ref: Value::Null,
        model: None,
        metadata: json!({}),
    });
    session.id = Some(session_id);
    session.workspace_id = Some(workspace_id);
    state.put(session).expect("session");
    for (ordinal, (role, text)) in [("user", "hello"), ("assistant", "hi there")]
        .into_iter()
        .enumerate()
    {
        let mut turn = CanonicalEntityInput::active(TurnPayload {
            session_id,
            ordinal: ordinal as u64,
            actor: role.to_string(),
            native_turn_id: None,
            turn_state: "recorded".to_string(),
            usage: json!({}),
            metadata: json!({}),
        });
        turn.workspace_id = Some(workspace_id);
        turn.parent_id = Some(session_id);
        let turn = state.put(turn).expect("turn");
        let mut message = CanonicalEntityInput::active(MessagePayload {
            session_id,
            turn_id: turn.id,
            role: role.to_string(),
            native_message_id: None,
            model: None,
            message_state: "recorded".to_string(),
            metadata: json!({"text": text}),
        });
        message.workspace_id = Some(workspace_id);
        message.parent_id = Some(turn.id);
        state.put(message).expect("message");
    }

    let conversation = adapter.export_session(session_id).expect("exports");
    assert_eq!(conversation.uuid, session_id.to_string());
    assert_eq!(conversation.name, "native authoring");
    assert_eq!(conversation.chat_messages.len(), 2);
    assert_eq!(conversation.chat_messages[0].sender.as_str(), "human");
    assert_eq!(conversation.chat_messages[1].sender.as_str(), "assistant");
    assert_eq!(conversation.chat_messages[0].raw["text"], json!("hello"));
    let created_at = conversation.envelope["created_at"].as_str().expect("stamp");
    assert!(
        created_at.len() == 24 && created_at.ends_with('Z') && created_at.contains('T'),
        "expected an RFC 3339 UTC stamp, got {created_at:?}"
    );

    let destination = directory.path().join("native-session.json");
    adapter
        .write_export_file(session_id, &destination)
        .expect("writes");
    let second_directory = tempfile::TempDir::new().expect("tempdir");
    let second_state = open_store(second_directory.path());
    let second_adapter = ClaudeDesktopAdapter::new(second_state.clone());
    let report = second_adapter
        .import_export_file(Uuid::now_v7(), &destination)
        .expect("parses");
    assert!(report.refused.is_empty(), "refused: {:?}", report.refused);
    let imported = second_state
        .get_by_native::<SessionPayload>(CLAUDE_DESKTOP_PLATFORM_ID, &session_id.to_string())
        .expect("lookup")
        .expect("imported");
    assert_eq!(session_turns(&second_state, imported.id).len(), 2);
}

#[test]
fn sessions_without_a_desktop_shape_refuse_export() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = open_store(directory.path());
    let adapter = ClaudeDesktopAdapter::new(state.clone());
    let workspace_id = Uuid::now_v7();

    let missing = adapter
        .export_session(Uuid::now_v7())
        .expect_err("a missing session refuses");
    assert!(matches!(missing, DesktopAdapterError::NotExportable { .. }));

    let session_id = Uuid::now_v7();
    let mut session = CanonicalEntityInput::active(SessionPayload {
        platform: "soleaux".to_string(),
        native_session_id: None,
        title: "tool session".to_string(),
        parent_session_id: None,
        lineage_root_id: session_id,
        session_state: "active".to_string(),
        repository_ref: Value::Null,
        model: None,
        metadata: json!({}),
    });
    session.id = Some(session_id);
    session.workspace_id = Some(workspace_id);
    state.put(session).expect("session");
    let mut turn = CanonicalEntityInput::active(TurnPayload {
        session_id,
        ordinal: 0,
        actor: "tool".to_string(),
        native_turn_id: None,
        turn_state: "recorded".to_string(),
        usage: json!({}),
        metadata: json!({}),
    });
    turn.workspace_id = Some(workspace_id);
    turn.parent_id = Some(session_id);
    let turn = state.put(turn).expect("turn");

    let empty_turn = adapter
        .export_session(session_id)
        .expect_err("a turn without a message refuses");
    assert!(matches!(
        &empty_turn,
        DesktopAdapterError::NotExportable { detail, .. } if detail.contains("no message")
    ));

    let mut message = CanonicalEntityInput::active(MessagePayload {
        session_id,
        turn_id: turn.id,
        role: "tool".to_string(),
        native_message_id: None,
        model: None,
        message_state: "recorded".to_string(),
        metadata: json!({}),
    });
    message.workspace_id = Some(workspace_id);
    message.parent_id = Some(turn.id);
    state.put(message).expect("message");
    let bad_role = adapter
        .export_session(session_id)
        .expect_err("an unmappable role refuses");
    assert!(matches!(
        &bad_role,
        DesktopAdapterError::NotExportable { detail, .. } if detail.contains("no documented sender")
    ));
}

// --- timestamps -----------------------------------------------------------

#[test]
fn rfc3339_formatting_matches_reference_vectors() {
    for (unix_ms, expected) in [
        (0_i64, "1970-01-01T00:00:00.000Z"),
        (1_700_000_000_000, "2023-11-14T22:13:20.000Z"),
        (1_754_611_200_123, "2025-08-08T00:00:00.123Z"),
        (951_782_400_000, "2000-02-29T00:00:00.000Z"),
        (4_102_444_799_999, "2099-12-31T23:59:59.999Z"),
    ] {
        assert_eq!(format_unix_ms_utc(unix_ms), expected);
    }
}

// --- connector materialization -------------------------------------------

#[test]
fn connector_materialization_is_a_value_the_user_applies_themselves() {
    let snippet = soleaux_local_connector("/work/repo").expect("materializes");
    assert_eq!(
        snippet,
        json!({
            "mcpServers": {
                "soleaux": {"command": "soleaux", "args": ["serve", "/work/repo"]}
            }
        })
    );
    for refused in [
        local_connector_materialization("", "soleaux", &[]),
        local_connector_materialization("bad name", "soleaux", &[]),
        local_connector_materialization("soleaux", "  ", &[]),
        soleaux_local_connector(""),
    ] {
        assert!(matches!(
            refused.expect_err("must refuse"),
            DesktopAdapterError::InvalidConnector { .. }
        ));
    }
}

// --- locked documentation contract ---------------------------------------

#[test]
fn matrix_pins_a_permanently_read_only_documentation_contract() {
    let matrix: Value =
        serde_json::from_str(soleaux_ipc::CLIENT_CAPABILITY_MATRIX_JSON).expect("matrix parses");
    let platform = matrix["platforms"]
        .as_array()
        .expect("platforms")
        .iter()
        .find(|platform| platform["id"] == CLAUDE_DESKTOP_PLATFORM_ID)
        .expect("matrix has a claude_desktop platform");
    assert_eq!(platform["probeMode"], json!("documentation_contract"));
    let versions = platform["versions"].as_array().expect("versions");
    assert_eq!(versions.len(), 1, "claude desktop pins exactly one version");
    assert_eq!(versions[0]["version"], json!(MATRIX_VERSION));
    assert_eq!(versions[0]["mutationEligible"], json!(false));
    let capabilities = &platform["capabilities"];
    assert_eq!(capabilities["writePolicy"], json!(WRITE_POLICY));
    for (list, member) in [
        ("exportImport", "account_data_export"),
        ("exportImport", "chat_history_export"),
        ("localConfiguration", "supported_local_connectors"),
        ("unsupportedDirectSurface", "hosted_session_crud"),
        ("unsupportedDirectSurface", "hosted_memory_crud"),
    ] {
        assert!(
            capabilities[list]
                .as_array()
                .expect(list)
                .contains(&json!(member)),
            "matrix {list} lost {member}"
        );
    }
}

// --- read-only source proof ----------------------------------------------

/// The adapter must have no code path that takes a Desktop store location:
/// filesystem access is confined to the user-authorized `files.rs` surface,
/// and no source names a Desktop database or configuration store.
#[test]
fn no_code_path_reaches_a_desktop_store() {
    let source_directory = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    let filesystem_tokens = ["std::fs", "fs::", "File::", "OpenOptions", "read_dir"];
    // Assembled at runtime so this test's own source never matches itself.
    let store_markers: Vec<String> = vec![
        ["Application", "Support"].join(" "),
        ["claude", "desktop", "config"].join("_"),
        ["App", "Data"].join(""),
        ["Index", "edDB"].join(""),
        ["Local", "Storage"].join(" "),
        ["Coo", "kies"].join(""),
        [".config/", "Claude"].join(""),
    ];

    let mut scanned = 0;
    for entry in std::fs::read_dir(&source_directory).expect("src directory") {
        let path = entry.expect("entry").path();
        if path.extension().and_then(|extension| extension.to_str()) != Some("rs") {
            continue;
        }
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .expect("file name")
            .to_string();
        let source = std::fs::read_to_string(&path).expect("source reads");
        scanned += 1;
        for marker in &store_markers {
            assert!(
                !source.contains(marker.as_str()),
                "{name} names a Desktop store location: {marker:?}"
            );
        }
        if name == "files.rs" || name == "tests.rs" {
            continue;
        }
        for token in filesystem_tokens {
            assert!(
                !source.contains(token),
                "{name} reaches the filesystem outside the user-authorized surface: {token:?}"
            );
        }
    }
    assert!(
        scanned >= 8,
        "the scan must cover the crate sources, saw {scanned}"
    );
}
