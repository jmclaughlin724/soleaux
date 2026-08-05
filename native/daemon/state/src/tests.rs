use super::*;
use serde_json::json;
use std::{
    sync::{Arc, Barrier, Mutex},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tempfile::tempdir;
use uuid::Uuid;

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

fn session_payload(lineage_root_id: Uuid, title: &str) -> SessionPayload {
    SessionPayload {
        platform: "codex".to_string(),
        native_session_id: Some("native-session-1".to_string()),
        title: title.to_string(),
        parent_session_id: None,
        lineage_root_id,
        session_state: "active".to_string(),
        repository_ref: json!({"commit":"abc123","worktree":"main"}),
        model: Some("fixture-model".to_string()),
        metadata: json!({"fixture":true}),
    }
}

#[test]
fn canonical_graph_is_typed_revisioned_linked_and_idempotent() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let workspace_id = Uuid::now_v7();
    let lineage_root_id = Uuid::now_v7();

    let mut input = CanonicalEntityInput::active(session_payload(lineage_root_id, "Primary"));
    input.workspace_id = Some(workspace_id);
    input.origin_platform = Some("codex".to_string());
    input.native_id = Some("native-session-1".to_string());
    input.idempotency_key = Some("session-import-1".to_string());
    let session = store.put(input.clone()).expect("session");
    assert_eq!(session.kind, EntityKind::Session);
    assert_eq!(session.revision, 1);

    let replay = store.put(input).expect("idempotent replay");
    assert_eq!(replay, session);

    let mut collision = CanonicalEntityInput::active(session_payload(lineage_root_id, "Changed"));
    collision.workspace_id = Some(workspace_id);
    collision.origin_platform = Some("codex".to_string());
    collision.native_id = Some("native-session-1".to_string());
    collision.idempotency_key = Some("session-import-1".to_string());
    let error = store.put(collision).expect_err("idempotency collision");
    assert!(format!("{error:#}").contains("idempotency collision"));

    let mut update = CanonicalEntityInput::active(session_payload(lineage_root_id, "Renamed"));
    update.id = Some(session.id);
    update.workspace_id = Some(workspace_id);
    update.origin_platform = Some("codex".to_string());
    update.native_id = Some("native-session-1".to_string());
    update.idempotency_key = Some("session-import-1".to_string());
    update.expected_revision = Some(1);
    let updated = store.put(update).expect("updated session");
    assert_eq!(updated.revision, 2);
    assert_eq!(updated.payload.title, "Renamed");

    let mut turn_input = CanonicalEntityInput::active(TurnPayload {
        session_id: session.id,
        ordinal: 1,
        actor: "user".to_string(),
        native_turn_id: Some("turn-1".to_string()),
        turn_state: "completed".to_string(),
        usage: json!({"inputTokens":12}),
        metadata: json!({}),
    });
    turn_input.workspace_id = Some(workspace_id);
    turn_input.parent_id = Some(session.id);
    let turn = store.put(turn_input).expect("turn");

    let link = store
        .link(EntityLinkInput {
            source_id: session.id,
            relationship: RelationshipKind::Contains,
            target_id: turn.id,
            metadata: json!({"ordinal":1}),
        })
        .expect("link");
    assert_eq!(link.relationship, RelationshipKind::Contains);
    assert_eq!(store.links_from(session.id).expect("links").len(), 1);

    let fetched = store
        .get::<SessionPayload>(session.id)
        .expect("read")
        .expect("session exists");
    assert_eq!(fetched.revision, 2);
    assert!(store.verify_audit_chain().expect("audit chain"));
    assert!(store.audit_after(0, 100).expect("audit").len() >= 4);
    assert_eq!(EntityKind::ALL.len(), 20);
}

#[test]
fn native_identity_upsert_is_serialized_and_reuses_the_canonical_record() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let payload = WorkspacePayload {
        canonical_path: directory.path().to_string_lossy().to_string(),
        path_hash: "a".repeat(64),
        display_name: "Fixture".to_string(),
        trust_state: WorkspaceTrustState::Trusted,
        profile_digest: LOCKED_PROFILE_SHA256.to_string(),
        context_digest: LOCKED_CONTEXT_PACKET_SHA256.to_string(),
        public_tool_ceiling: PUBLIC_TOOL_CEILING,
        production_claim_allowed: false,
        metadata: json!({"revision":1}),
    };
    let mut input = CanonicalEntityInput::active(payload.clone());
    input.state = "registered".to_string();
    input.origin_platform = Some("soleaux.workspace".to_string());
    input.native_id = Some(payload.path_hash.clone());
    input.idempotency_key = Some(format!("workspace:{}", payload.path_hash));
    let first = store.upsert_native(input).expect("first upsert");
    assert_eq!(first.revision, 1);

    let mut changed = payload;
    changed.metadata = json!({"revision":2});
    let mut input = CanonicalEntityInput::active(changed.clone());
    input.state = "registered".to_string();
    input.origin_platform = Some("soleaux.workspace".to_string());
    input.native_id = Some(changed.path_hash.clone());
    input.idempotency_key = Some(format!("workspace:{}", changed.path_hash));
    let second = store.upsert_native(input).expect("second upsert");
    assert_eq!(second.id, first.id);
    assert_eq!(second.revision, 2);
    assert_eq!(second.payload.metadata, json!({"revision":2}));

    let fetched = store
        .get_by_native::<WorkspacePayload>("soleaux.workspace", &changed.path_hash)
        .expect("native lookup")
        .expect("workspace");
    assert_eq!(fetched.id, first.id);
    assert_eq!(
        store
            .list_all::<WorkspacePayload>(10, false)
            .expect("list")
            .len(),
        1
    );
}

#[test]
fn adapter_cursors_and_retention_use_optimistic_revisions_and_tombstones() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let workspace_id = Uuid::now_v7();

    let cursor = store
        .put_adapter_cursor(AdapterCursorInput {
            adapter: "opencode".to_string(),
            scope: "workspace:fixture".to_string(),
            cursor: "cursor-1".to_string(),
            etag: Some("etag-1".to_string()),
            watermark: None,
            expected_revision: None,
            metadata: json!({"version":"1"}),
        })
        .expect("cursor");
    assert_eq!(cursor.revision, 1);
    let updated_cursor = store
        .put_adapter_cursor(AdapterCursorInput {
            adapter: "opencode".to_string(),
            scope: "workspace:fixture".to_string(),
            cursor: "cursor-2".to_string(),
            etag: Some("etag-2".to_string()),
            watermark: Some("42".to_string()),
            expected_revision: Some(1),
            metadata: json!({"version":"1"}),
        })
        .expect("cursor update");
    assert_eq!(updated_cursor.revision, 2);

    let mut memory_input = CanonicalEntityInput::active(MemoryClaimPayload {
        claim_type: "decision".to_string(),
        subject: "database".to_string(),
        content: "Use one serialized writer".to_string(),
        memory_state: "validated".to_string(),
        confidence: 0.95,
        evidence_uris: vec!["soleaux://audit/fixture".to_string()],
        supersedes_id: None,
        source_session_id: None,
        metadata: json!({}),
    });
    memory_input.workspace_id = Some(workspace_id);
    memory_input.expires_at_unix_ms = Some(now_ms().saturating_sub(1));
    let memory = store.put(memory_input).expect("memory");

    let tombstones = store
        .apply_retention(now_ms(), 100)
        .expect("apply retention");
    assert_eq!(tombstones.len(), 1);
    assert_eq!(tombstones[0].entity_id, memory.id);
    let tombstoned = store
        .get::<MemoryClaimPayload>(memory.id)
        .expect("read")
        .expect("tombstoned record remains");
    assert!(tombstoned.tombstoned_at_unix_ms.is_some());

    let purged = store
        .purge_tombstones(now_ms().saturating_add(1), 100)
        .expect("purge");
    assert_eq!(purged, 1);
    assert!(
        store
            .get::<MemoryClaimPayload>(memory.id)
            .expect("read")
            .is_none()
    );
    assert!(store.verify_audit_chain().expect("audit chain"));
}

#[test]
fn operation_leases_have_one_winner_recovery_and_exact_result_replay() {
    let directory = tempdir().expect("tempdir");
    let store = Arc::new(
        StateStore::open(directory.path().join("state.sqlite3")).expect("canonical store"),
    );
    let barrier = Arc::new(Barrier::new(8));
    let outcomes = Arc::new(Mutex::new(Vec::new()));
    let mut threads = Vec::new();
    for index in 0..8 {
        let store = Arc::clone(&store);
        let barrier = Arc::clone(&barrier);
        let outcomes = Arc::clone(&outcomes);
        threads.push(thread::spawn(move || {
            barrier.wait();
            let outcome = store
                .acquire_operation(
                    "run:fixture",
                    "request-hash",
                    "agent.run",
                    None,
                    format!("worker-{index}"),
                    30_000,
                )
                .expect("acquire");
            outcomes.lock().expect("outcomes").push(outcome);
        }));
    }
    for worker in threads {
        worker.join().expect("worker");
    }
    let outcomes = outcomes.lock().expect("outcomes");
    let acquired = outcomes
        .iter()
        .filter_map(|outcome| match outcome {
            OperationLeaseOutcome::Acquired(lease) => Some(lease.clone()),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(acquired.len(), 1);
    assert_eq!(
        outcomes
            .iter()
            .filter(|outcome| matches!(outcome, OperationLeaseOutcome::InFlight(_)))
            .count(),
        7
    );
    let lease = &acquired[0];
    let lease_id = lease.lease_id.expect("lease id");
    let owner_id = lease.owner_id.as_deref().expect("owner");
    let completed = store
        .complete_operation(
            &lease.operation_key,
            lease_id,
            owner_id,
            json!({"status":"complete","receipt":"one"}),
        )
        .expect("complete");
    assert_eq!(completed.state, "completed");
    let replay = store
        .acquire_operation(
            "run:fixture",
            "request-hash",
            "agent.run",
            None,
            "replay-worker",
            30_000,
        )
        .expect("replay");
    assert_eq!(
        replay,
        OperationLeaseOutcome::Replayed(json!({"status":"complete","receipt":"one"}))
    );

    let expiring = store
        .acquire_operation(
            "run:expiring",
            "expiring-hash",
            "agent.run",
            None,
            "worker",
            1,
        )
        .expect("expiring lease");
    assert!(matches!(expiring, OperationLeaseOutcome::Acquired(_)));
    thread::sleep(Duration::from_millis(3));
    let recovered = store
        .recover_expired_operations(now_ms(), 100)
        .expect("recover");
    assert_eq!(recovered.len(), 1);
    assert_eq!(recovered[0].state, "abandoned");
    let reacquired = store
        .acquire_operation(
            "run:expiring",
            "expiring-hash",
            "agent.run",
            None,
            "recovery-worker",
            30_000,
        )
        .expect("reacquire");
    let OperationLeaseOutcome::Acquired(reacquired) = reacquired else {
        panic!("expected recovered operation to be acquirable");
    };
    assert_eq!(reacquired.attempt, 2);
    assert!(store.verify_audit_chain().expect("audit chain"));
}

#[test]
fn backup_restore_export_and_repair_preserve_integrity() {
    let directory = tempdir().expect("tempdir");
    let database = directory.path().join("state.sqlite3");
    let backup = directory.path().join("backup.sqlite3");
    let restored = directory.path().join("restored.sqlite3");
    let store = StateStore::open(&database).expect("store");
    let lineage_root_id = Uuid::now_v7();
    let session = store
        .put(CanonicalEntityInput::active(session_payload(
            lineage_root_id,
            "Backup fixture",
        )))
        .expect("session");
    store
        .append_audit(
            "fixture.custom",
            None,
            Some(session.id),
            json!({"verified":true}),
        )
        .expect("audit");
    let manifest = store.backup_to(&backup).expect("backup");
    assert!(manifest.byte_length > 0);
    assert_eq!(manifest.blake3.len(), 64);
    let report = store.integrity_report().expect("integrity");
    assert_eq!(report.integrity, "ok");
    assert_eq!(report.foreign_key_violations, 0);
    assert!(report.audit_chain_valid);
    assert_eq!(report.entity_count, 1);
    let repaired = store.repair().expect("repair");
    assert_eq!(repaired.integrity, "ok");

    let restore_manifest = StateStore::restore_backup(&backup, &restored).expect("restore");
    assert_eq!(restore_manifest.schema_version, SCHEMA_VERSION);
    let restored_store = StateStore::open(&restored).expect("restored store");
    let restored_session = restored_store
        .get::<SessionPayload>(session.id)
        .expect("read")
        .expect("session restored");
    assert_eq!(restored_session.payload.title, "Backup fixture");
    let snapshot = restored_store.export_snapshot().expect("snapshot");
    assert_eq!(snapshot.schema_version, SCHEMA_VERSION);
    assert_eq!(snapshot.entities.len(), 1);
    assert!(snapshot.audit.len() >= 2);
    assert!(restored_store.verify_audit_chain().expect("audit chain"));
}

#[test]
fn newer_canonical_schema_is_refused() {
    let directory = tempdir().expect("tempdir");
    let database = directory.path().join("future.sqlite3");
    let connection = rusqlite::Connection::open(&database).expect("database");
    connection
        .pragma_update(None, "user_version", SCHEMA_VERSION + 1)
        .expect("future schema");
    drop(connection);
    let error = StateStore::open(&database).expect_err("future schema must fail closed");
    assert!(format!("{error:#}").contains("newer than supported"));
}
