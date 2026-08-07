use super::*;
use serde_json::json;
use std::{
    sync::{
        Arc, Barrier, Mutex,
        atomic::{AtomicBool, Ordering},
    },
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

fn registry_workspace_input(
    canonical_path: &str,
    trust_state: WorkspaceTrustState,
    expires_at_unix_ms: Option<i64>,
) -> CanonicalEntityInput<WorkspacePayload> {
    let path_hash = blake3::hash(canonical_path.as_bytes()).to_hex().to_string();
    let payload = WorkspacePayload {
        canonical_path: canonical_path.to_string(),
        path_hash: path_hash.clone(),
        display_name: format!("Workspace {canonical_path}"),
        trust_state,
        profile_digest: LOCKED_PROFILE_SHA256.to_string(),
        context_digest: LOCKED_CONTEXT_PACKET_SHA256.to_string(),
        public_tool_ceiling: PUBLIC_TOOL_CEILING,
        production_claim_allowed: false,
        metadata: json!({"fixture":true}),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.state = "registered".to_string();
    input.origin_platform = Some("soleaux.workspace".to_string());
    input.native_id = Some(path_hash.clone());
    input.idempotency_key = Some(format!("workspace:{path_hash}"));
    input.expires_at_unix_ms = expires_at_unix_ms;
    input
}

fn registry_client_input(
    kind: ClientKind,
    instance_id: &str,
    display_name: &str,
    compatibility_state: ClientCompatibilityState,
    expires_at_unix_ms: i64,
    metadata: serde_json::Value,
) -> CanonicalEntityInput<ClientRegistrationPayload> {
    let native_id = format!("{}:{instance_id}", kind.as_str());
    let payload = ClientRegistrationPayload {
        client_kind: kind,
        instance_id: instance_id.to_string(),
        display_name: display_name.to_string(),
        client_version: if compatibility_state == ClientCompatibilityState::Verified {
            "0.4.0-dev.5".to_string()
        } else {
            "unprobed-fixture".to_string()
        },
        protocol_version: "soleaux.client/v1".to_string(),
        connection_state: "connected".to_string(),
        compatibility_state,
        write_capable: compatibility_state == ClientCompatibilityState::Verified,
        last_seen_at_unix_ms: now_ms(),
        capabilities: json!({"registry":true}),
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.state = "connected".to_string();
    input.origin_platform = Some("soleaux.client".to_string());
    input.native_id = Some(native_id.clone());
    input.idempotency_key = Some(format!("client:{native_id}"));
    input.expires_at_unix_ms = Some(expires_at_unix_ms);
    input
}

fn registry_binding_input(
    client_id: Uuid,
    workspace_id: Uuid,
    access_mode: ClientAccessMode,
) -> CanonicalEntityInput<ClientWorkspaceBindingPayload> {
    let native_id = format!("{client_id}:{workspace_id}");
    let now = now_ms();
    let payload = ClientWorkspaceBindingPayload {
        client_id,
        workspace_id,
        access_mode,
        binding_state: "bound".to_string(),
        attached_at_unix_ms: now,
        last_seen_at_unix_ms: now,
        admission: None,
        capabilities: json!({"context":true}),
        metadata: json!({"fixture":true}),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.workspace_id = Some(workspace_id);
    input.parent_id = Some(client_id);
    input.state = "bound".to_string();
    input.origin_platform = Some("soleaux.client-workspace".to_string());
    input.native_id = Some(native_id.clone());
    input.idempotency_key = Some(format!("binding:{native_id}"));
    input
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

#[test]
fn canonical_schema_one_migrates_to_registry_schema_two() {
    let directory = tempdir().expect("tempdir");
    let database = directory.path().join("schema-v1.sqlite3");
    {
        let store = StateStore::open(&database).expect("create current store");
        let root = Uuid::now_v7();
        store
            .put(CanonicalEntityInput::active(session_payload(
                root,
                "migration fixture",
            )))
            .expect("seed v1-compatible entity");
    }
    thread::sleep(Duration::from_millis(10));
    let connection = rusqlite::Connection::open(&database).expect("database");
    connection
        .pragma_update(None, "user_version", 1)
        .expect("mark as schema one");
    drop(connection);

    let reopened = StateStore::open(&database).expect("migrate schema one");
    assert_eq!(
        reopened
            .integrity_report()
            .expect("integrity")
            .schema_version,
        2
    );
    assert_eq!(
        reopened.export_snapshot().expect("snapshot").entities.len(),
        1
    );
}

#[test]
fn registry_revives_native_identities_and_enforces_trust_and_compatibility() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("registry.sqlite3")).expect("store");
    let expires = now_ms() + 60_000;
    let workspace_path = directory.path().join("workspace");
    let workspace_path = workspace_path.to_str().expect("UTF-8 path");
    let workspace = store
        .registry_register_workspace(registry_workspace_input(
            workspace_path,
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("workspace")
        .workspace;
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "registry-revival",
            "Verified CLI",
            ClientCompatibilityState::Verified,
            expires,
            json!({"revision":1}),
        ))
        .expect("client")
        .client;
    let binding = store
        .registry_bind_client_workspace(registry_binding_input(
            client.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect("read-write binding");

    let downgraded = store
        .registry_register_workspace(registry_workspace_input(
            workspace_path,
            WorkspaceTrustState::ReadOnly,
            None,
        ))
        .expect("trust downgrade");
    assert_eq!(downgraded.downgraded_bindings.len(), 1);
    assert_eq!(
        downgraded.downgraded_bindings[0].payload.access_mode,
        ClientAccessMode::ReadOnly
    );
    let error = store
        .registry_bind_client_workspace(registry_binding_input(
            client.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect_err("read-only workspace must reject write access");
    assert!(format!("{error:#}").contains("trusted workspace"));

    let forgotten = store
        .registry_forget_workspace(workspace.id, "fixture", "test")
        .expect("forget workspace");
    assert_eq!(forgotten.binding_ids, vec![binding.id]);
    let revived_workspace = store
        .registry_register_workspace(registry_workspace_input(
            workspace_path,
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("revive workspace")
        .workspace;
    assert_eq!(revived_workspace.id, workspace.id);
    assert!(revived_workspace.tombstoned_at_unix_ms.is_none());

    let revived_binding = store
        .registry_bind_client_workspace(registry_binding_input(
            client.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect("revive binding");
    assert_eq!(revived_binding.id, binding.id);

    let disconnected = store
        .registry_disconnect_client(client.id, "fixture", "test")
        .expect("disconnect client");
    assert_eq!(disconnected.binding_ids, vec![binding.id]);
    let revived_client = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "registry-revival",
            "Verified CLI revived",
            ClientCompatibilityState::Verified,
            expires + 1_000,
            json!({"revision":2}),
        ))
        .expect("revive client")
        .client;
    assert_eq!(revived_client.id, client.id);
    assert!(revived_client.tombstoned_at_unix_ms.is_none());

    let unprobed = store
        .registry_register_client(registry_client_input(
            ClientKind::Desktop,
            "unprobed-desktop",
            "Unprobed Desktop",
            ClientCompatibilityState::Unprobed,
            expires,
            json!({}),
        ))
        .expect("unprobed client")
        .client;
    let error = store
        .registry_bind_client_workspace(registry_binding_input(
            unprobed.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect_err("unprobed client must remain read-only");
    assert!(format!("{error:#}").contains("verified client compatibility matrix"));
}

#[test]
fn registry_binding_admission_elevates_until_expiry_then_downgrades() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("admission.sqlite3")).expect("store");
    let now = now_ms();
    let workspace = store
        .registry_register_workspace(registry_workspace_input(
            "/fixtures/admitted-workspace",
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("workspace")
        .workspace;
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Adapter,
            "admitted-adapter",
            "Admitted adapter",
            ClientCompatibilityState::Unprobed,
            now + 60_000,
            json!({"platform":"generic_mcp_host"}),
        ))
        .expect("client")
        .client;
    assert!(!client.payload.write_capable);

    let admission = ClientBindingAdmission {
        receipt_matrix_sha256: "a".repeat(64),
        probe_evidence_sha256: "b".repeat(64),
        issued_at_unix_ms: now,
        expires_at_unix_ms: now + 250,
        key_version: 1,
    };

    let mut expired_input =
        registry_binding_input(client.id, workspace.id, ClientAccessMode::ReadWrite);
    expired_input.payload.admission = Some(ClientBindingAdmission {
        issued_at_unix_ms: now - 10_000,
        expires_at_unix_ms: now - 5_000,
        ..admission.clone()
    });
    let error = store
        .registry_bind_client_workspace(expired_input)
        .expect_err("an expired admission never elevates");
    assert!(format!("{error:#}").contains("verified client compatibility matrix"));

    let mut admitted_input =
        registry_binding_input(client.id, workspace.id, ClientAccessMode::ReadWrite);
    admitted_input.payload.admission = Some(admission.clone());
    let binding = store
        .registry_bind_client_workspace(admitted_input)
        .expect("admitted read-write binding");
    assert_eq!(binding.payload.access_mode, ClientAccessMode::ReadWrite);

    let refreshed = store
        .registry_heartbeat_client(client.id, 60_000, None)
        .expect("heartbeat while admitted");
    assert_eq!(
        refreshed.bindings[0].payload.access_mode,
        ClientAccessMode::ReadWrite
    );

    thread::sleep(Duration::from_millis(300));
    let downgraded = store
        .registry_heartbeat_client(client.id, 60_000, None)
        .expect("heartbeat after admission expiry");
    assert_eq!(
        downgraded.bindings[0].payload.access_mode,
        ClientAccessMode::ReadOnly
    );
    assert!(downgraded.bindings[0].payload.admission.is_some());
}

#[test]
fn registry_pages_filter_before_limiting_and_use_stable_cursors() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("registry-pages.sqlite3")).expect("store");
    let now = now_ms();
    for ordinal in 0..40 {
        let expires = if ordinal < 32 { now - 1 } else { now + 60_000 };
        store
            .registry_register_client(registry_client_input(
                ClientKind::Adapter,
                &format!("client-{ordinal:02}"),
                &format!("Client {ordinal:02}"),
                ClientCompatibilityState::Unprobed,
                expires,
                json!({"ordinal":ordinal}),
            ))
            .expect("register client");
    }
    let first = store
        .registry_clients(false, None, 4, now)
        .expect("first page");
    assert_eq!(first.items.len(), 4);
    assert!(first.truncated);
    let second = store
        .registry_clients(false, first.next_cursor, 4, now)
        .expect("second page");
    assert_eq!(second.items.len(), 4);
    assert!(!second.truncated);
    assert!(second.next_cursor.is_none());
    let ids = first
        .items
        .iter()
        .chain(second.items.iter())
        .map(|record| record.id)
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), 8);
}

#[test]
fn registry_pages_remain_readable_during_concurrent_purge() {
    let directory = tempdir().expect("tempdir");
    let store = Arc::new(
        StateStore::open(directory.path().join("registry-snapshot.sqlite3")).expect("store"),
    );
    let now = now_ms();
    let mut client_ids = Vec::new();
    for ordinal in 0..96 {
        client_ids.push(
            store
                .registry_register_client(registry_client_input(
                    ClientKind::Adapter,
                    &format!("snapshot-{ordinal:03}"),
                    "Snapshot adapter",
                    ClientCompatibilityState::Unprobed,
                    now + 60_000,
                    json!({"ordinal":ordinal}),
                ))
                .expect("register client")
                .client
                .id,
        );
    }

    let barrier = Arc::new(Barrier::new(2));
    let done = Arc::new(AtomicBool::new(false));
    let reader_store = Arc::clone(&store);
    let reader_barrier = Arc::clone(&barrier);
    let reader_done = Arc::clone(&done);
    let reader = thread::spawn(move || {
        reader_barrier.wait();
        let mut reads = 0usize;
        while !reader_done.load(Ordering::Acquire) || reads < 64 {
            let page = reader_store
                .registry_clients(false, None, REGISTRY_PAGE_LIMIT_MAX, now)
                .expect("snapshot-consistent registry page");
            assert!(page.items.iter().all(|record| {
                record.state == "connected"
                    && record.tombstoned_at_unix_ms.is_none()
                    && record
                        .expires_at_unix_ms
                        .is_none_or(|expires| expires > now)
            }));
            reads = reads.saturating_add(1);
            thread::yield_now();
        }
    });

    barrier.wait();
    for client_id in client_ids {
        store
            .registry_disconnect_client(client_id, "snapshot-race", "test")
            .expect("disconnect client");
        store
            .purge_tombstones(now_ms().saturating_add(1_000), REGISTRY_PAGE_LIMIT_MAX)
            .expect("purge client");
        thread::yield_now();
    }
    done.store(true, Ordering::Release);
    reader.join().expect("reader");
}

#[test]
fn registry_cascades_are_atomic_against_concurrent_binding_creation() {
    let directory = tempdir().expect("tempdir");
    let store =
        Arc::new(StateStore::open(directory.path().join("registry-races.sqlite3")).expect("store"));
    for ordinal in 0..24 {
        let workspace = store
            .registry_register_workspace(registry_workspace_input(
                &format!("/fixture/workspace-{ordinal}"),
                WorkspaceTrustState::Trusted,
                None,
            ))
            .expect("workspace")
            .workspace;
        let client = store
            .registry_register_client(registry_client_input(
                ClientKind::Cli,
                &format!("race-{ordinal}"),
                "Race CLI",
                ClientCompatibilityState::Verified,
                now_ms() + 60_000,
                json!({}),
            ))
            .expect("client")
            .client;
        let client_id = client.id;
        let workspace_id = workspace.id;
        let barrier = Arc::new(Barrier::new(3));
        let bind_store = Arc::clone(&store);
        let bind_barrier = Arc::clone(&barrier);
        let bind = thread::spawn(move || {
            bind_barrier.wait();
            bind_store.registry_bind_client_workspace(registry_binding_input(
                client_id,
                workspace_id,
                ClientAccessMode::ReadWrite,
            ))
        });
        let cascade_store = Arc::clone(&store);
        let cascade_barrier = Arc::clone(&barrier);
        let cascade = thread::spawn(move || {
            cascade_barrier.wait();
            if ordinal % 2 == 0 {
                cascade_store.registry_forget_workspace(workspace_id, "race", "test")
            } else {
                cascade_store.registry_disconnect_client(client_id, "race", "test")
            }
        });
        barrier.wait();
        let _ = bind.join().expect("bind thread");
        cascade.join().expect("cascade thread").expect("cascade");
        let live = store
            .registry_bindings(false, None, REGISTRY_PAGE_LIMIT_MAX, now_ms())
            .expect("bindings");
        assert!(live.items.iter().all(|record| {
            record.payload.client_id != client_id && record.payload.workspace_id != workspace_id
        }));
    }
}

#[test]
fn registry_heartbeat_never_reverts_a_concurrent_registration_refresh() {
    let directory = tempdir().expect("tempdir");
    let store = Arc::new(
        StateStore::open(directory.path().join("registry-heartbeat.sqlite3")).expect("store"),
    );
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "heartbeat-race",
            "Old display",
            ClientCompatibilityState::Verified,
            now_ms() + 60_000,
            json!({"generation":1}),
        ))
        .expect("client")
        .client;
    let barrier = Arc::new(Barrier::new(3));
    let heartbeat_store = Arc::clone(&store);
    let heartbeat_barrier = Arc::clone(&barrier);
    let heartbeat = thread::spawn(move || {
        heartbeat_barrier.wait();
        heartbeat_store.registry_heartbeat_client(
            client.id,
            60_000,
            Some(json!({"registry":true,"heartbeat":true})),
        )
    });
    let refresh_store = Arc::clone(&store);
    let refresh_barrier = Arc::clone(&barrier);
    let refresh = thread::spawn(move || {
        refresh_barrier.wait();
        refresh_store.registry_register_client(registry_client_input(
            ClientKind::Cli,
            "heartbeat-race",
            "New display",
            ClientCompatibilityState::Verified,
            now_ms() + 120_000,
            json!({"generation":2}),
        ))
    });
    barrier.wait();
    heartbeat
        .join()
        .expect("heartbeat thread")
        .expect("heartbeat");
    refresh.join().expect("refresh thread").expect("refresh");
    let current = store
        .get_by_native::<ClientRegistrationPayload>("soleaux.client", "cli:heartbeat-race")
        .expect("read client")
        .expect("client exists");
    assert_eq!(current.payload.display_name, "New display");
    assert_eq!(current.payload.metadata, json!({"generation":2}));
}

#[test]
fn expired_clients_must_register_again_before_heartbeat() {
    let directory = tempdir().expect("tempdir");
    let store =
        StateStore::open(directory.path().join("expired-heartbeat.sqlite3")).expect("store");
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Desktop,
            "expired-heartbeat",
            "Expired desktop",
            ClientCompatibilityState::Verified,
            now_ms() - 1,
            json!({}),
        ))
        .expect("register expired fixture")
        .client;
    let error = store
        .registry_heartbeat_client(client.id, 60_000, None)
        .expect_err("expired client must not revive by heartbeat");
    assert!(format!("{error:#}").contains("not active"));

    let registered = store
        .registry_register_client(registry_client_input(
            ClientKind::Desktop,
            "expired-heartbeat",
            "Re-registered desktop",
            ClientCompatibilityState::Verified,
            now_ms() + 60_000,
            json!({"reregistered":true}),
        ))
        .expect("re-register client")
        .client;
    assert_eq!(registered.id, client.id);
    store
        .registry_heartbeat_client(client.id, 60_000, None)
        .expect("heartbeat after registration");
}

#[test]
fn retention_batches_are_unique_and_respect_the_requested_limit() {
    let directory = tempdir().expect("tempdir");
    let store =
        StateStore::open(directory.path().join("bounded-retention.sqlite3")).expect("store");
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "bounded-retention",
            "Bounded retention CLI",
            ClientCompatibilityState::Verified,
            now_ms() + 60_000,
            json!({}),
        ))
        .expect("client")
        .client;
    let mut expected = std::collections::BTreeSet::from([client.id]);
    for ordinal in 0..2 {
        let workspace = store
            .registry_register_workspace(registry_workspace_input(
                &format!("/fixture/bounded-retention-{ordinal}"),
                WorkspaceTrustState::Trusted,
                None,
            ))
            .expect("workspace")
            .workspace;
        let binding = store
            .registry_bind_client_workspace(registry_binding_input(
                client.id,
                workspace.id,
                ClientAccessMode::ReadWrite,
            ))
            .expect("binding");
        expected.insert(binding.id);
    }
    store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "bounded-retention",
            "Bounded retention CLI",
            ClientCompatibilityState::Verified,
            now_ms() - 1,
            json!({}),
        ))
        .expect("expire client and bindings");

    let mut observed = std::collections::BTreeSet::new();
    for _ in 0..4 {
        let batch = store.apply_retention(now_ms(), 2).expect("retention batch");
        assert!(batch.len() <= 2);
        let batch_ids = batch
            .iter()
            .map(|record| record.entity_id)
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(batch_ids.len(), batch.len());
        for id in batch_ids {
            assert!(observed.insert(id), "retention emitted a duplicate id");
        }
        if observed == expected {
            break;
        }
    }
    assert_eq!(observed, expected);
}

#[test]
fn retention_cascades_and_purges_registry_children_before_parents() {
    let directory = tempdir().expect("tempdir");
    let store =
        StateStore::open(directory.path().join("registry-retention.sqlite3")).expect("store");
    let workspace_path = "/fixture/retention";
    let workspace = store
        .registry_register_workspace(registry_workspace_input(
            workspace_path,
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("workspace")
        .workspace;
    let client = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "retention",
            "Retention CLI",
            ClientCompatibilityState::Verified,
            now_ms() + 60_000,
            json!({}),
        ))
        .expect("client")
        .client;
    let binding = store
        .registry_bind_client_workspace(registry_binding_input(
            client.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect("binding");

    store
        .registry_register_workspace(registry_workspace_input(
            workspace_path,
            WorkspaceTrustState::Trusted,
            Some(now_ms() - 1),
        ))
        .expect("expire workspace");
    store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "retention",
            "Retention CLI",
            ClientCompatibilityState::Verified,
            now_ms() - 1,
            json!({}),
        ))
        .expect("expire client");

    let tombstones = store.apply_retention(now_ms(), 10).expect("retention");
    let ids = tombstones
        .iter()
        .map(|record| record.entity_id)
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), tombstones.len());
    assert!(ids.contains(&workspace.id));
    assert!(ids.contains(&client.id));
    assert!(ids.contains(&binding.id));
    assert_eq!(
        store.purge_tombstones(now_ms() + 1_000, 10).expect("purge"),
        3
    );
    assert!(
        store
            .get_serialized(workspace.id)
            .expect("workspace")
            .is_none()
    );
    assert!(store.get_serialized(client.id).expect("client").is_none());
    assert!(store.get_serialized(binding.id).expect("binding").is_none());
}

#[test]
fn registry_client_revalidation_rejects_stale_revision() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let expires = now_ms() + 60_000;
    let initial = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "revision-fixture",
            "Revision fixture",
            ClientCompatibilityState::Verified,
            expires,
            json!({"sequence":"initial"}),
        ))
        .expect("initial client");
    let mut first = registry_client_input(
        ClientKind::Cli,
        "revision-fixture",
        "Revision fixture",
        ClientCompatibilityState::Verified,
        expires,
        json!({"sequence":"first"}),
    );
    first.id = Some(initial.client.id);
    first.expected_revision = Some(initial.client.revision);
    let mut stale = registry_client_input(
        ClientKind::Cli,
        "revision-fixture",
        "Revision fixture",
        ClientCompatibilityState::Verified,
        expires,
        json!({"sequence":"stale"}),
    );
    stale.id = Some(initial.client.id);
    stale.expected_revision = Some(initial.client.revision);
    let updated = store
        .registry_register_client(first)
        .expect("first revalidation");
    assert_eq!(updated.client.revision, initial.client.revision + 1);
    let error = store
        .registry_register_client(stale)
        .expect_err("stale revalidation must fail closed");
    assert!(format!("{error:#}").contains("revision conflict"));
}

#[test]
fn registry_client_revalidation_returns_owned_bindings_without_global_paging() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let expires = now_ms() + 60_000;
    let workspace = store
        .registry_register_workspace(registry_workspace_input(
            "/tmp/soleaux-registry-binding-page",
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("workspace")
        .workspace;
    for index in 0..27 {
        let filler = store
            .registry_register_client(registry_client_input(
                ClientKind::Adapter,
                &format!("filler-{index}"),
                &format!("Filler {index}"),
                ClientCompatibilityState::Unprobed,
                expires,
                json!({"index":index}),
            ))
            .expect("filler client")
            .client;
        store
            .registry_bind_client_workspace(registry_binding_input(
                filler.id,
                workspace.id,
                ClientAccessMode::ReadOnly,
            ))
            .expect("filler binding");
    }
    let target = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "target-client",
            "Target client",
            ClientCompatibilityState::Verified,
            expires,
            json!({"target":true}),
        ))
        .expect("target client")
        .client;
    let target_binding = store
        .registry_bind_client_workspace(registry_binding_input(
            target.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect("target binding");
    let mut revalidation = registry_client_input(
        ClientKind::Cli,
        "target-client",
        "Target client",
        ClientCompatibilityState::Verified,
        expires,
        json!({"target":true,"revalidated":true}),
    );
    revalidation.id = Some(target.id);
    revalidation.expected_revision = Some(target.revision);
    let result = store
        .registry_register_client(revalidation)
        .expect("target revalidation");
    assert_eq!(result.bindings.len(), 1);
    assert_eq!(result.bindings[0].id, target_binding.id);
    assert_eq!(result.bindings[0].payload.client_id, target.id);
}
