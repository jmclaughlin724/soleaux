use anyhow::{Context, Result, bail};
use serde_json::json;
use soleaux_state::{
    CanonicalEntityInput, EntityLinkInput, OperationLeaseOutcome, RelationshipKind, SessionPayload,
    StateStore, TurnPayload,
};
use std::{env, fs, path::PathBuf};
use uuid::Uuid;

fn main() -> Result<()> {
    let output = env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .context("usage: state_smoke <output-directory>")?;
    fs::create_dir_all(&output)
        .with_context(|| format!("creating smoke output {}", output.display()))?;
    let database = output.join("canonical-state.sqlite3");
    let backup = output.join("canonical-state.backup.sqlite3");
    let restored = output.join("canonical-state.restored.sqlite3");
    let report_path = output.join("canonical-state-smoke.json");
    let store = StateStore::open(&database)?;
    let workspace_id = Uuid::now_v7();
    let lineage_root_id = Uuid::now_v7();

    let mut session_input = CanonicalEntityInput::active(SessionPayload {
        platform: "codex".to_string(),
        native_session_id: Some("smoke-session".to_string()),
        title: "Canonical state smoke".to_string(),
        parent_session_id: None,
        lineage_root_id,
        session_state: "active".to_string(),
        repository_ref: json!({"commit":"smoke","worktree":"main"}),
        model: Some("smoke-model".to_string()),
        metadata: json!({"source":"compiled-example"}),
    });
    session_input.workspace_id = Some(workspace_id);
    session_input.origin_platform = Some("codex".to_string());
    session_input.native_id = Some("smoke-session".to_string());
    session_input.idempotency_key = Some("smoke-session-import".to_string());
    let session = store.put(session_input)?;

    let mut turn_input = CanonicalEntityInput::active(TurnPayload {
        session_id: session.id,
        ordinal: 1,
        actor: "assistant".to_string(),
        native_turn_id: Some("smoke-turn".to_string()),
        turn_state: "completed".to_string(),
        usage: json!({"inputTokens":10,"outputTokens":20}),
        metadata: json!({}),
    });
    turn_input.workspace_id = Some(workspace_id);
    turn_input.parent_id = Some(session.id);
    turn_input.idempotency_key = Some("smoke-turn-import".to_string());
    let turn = store.put(turn_input)?;
    store.link(EntityLinkInput {
        source_id: session.id,
        relationship: RelationshipKind::Contains,
        target_id: turn.id,
        metadata: json!({"ordinal":1}),
    })?;

    let acquired = store.acquire_operation(
        "smoke:operation",
        "smoke-request-hash",
        "canonical.smoke",
        Some(workspace_id),
        "state-smoke",
        30_000,
    )?;
    let OperationLeaseOutcome::Acquired(lease) = acquired else {
        bail!("new smoke operation was not acquired");
    };
    let completed = store.complete_operation(
        &lease.operation_key,
        lease.lease_id.context("acquired lease omitted lease id")?,
        lease.owner_id.as_deref().context("acquired lease omitted owner")?,
        json!({"status":"complete","sessionId":session.id}),
    )?;
    let replay = store.acquire_operation(
        "smoke:operation",
        "smoke-request-hash",
        "canonical.smoke",
        Some(workspace_id),
        "state-smoke-replay",
        30_000,
    )?;
    let OperationLeaseOutcome::Replayed(replayed_result) = replay else {
        bail!("completed smoke operation did not replay");
    };

    let backup_manifest = store.backup_to(&backup)?;
    let integrity = store.integrity_report()?;
    if integrity.integrity != "ok"
        || integrity.foreign_key_violations != 0
        || !integrity.audit_chain_valid
    {
        bail!("canonical state smoke integrity failed");
    }
    let restore_manifest = StateStore::restore_backup(&backup, &restored)?;
    let restored_store = StateStore::open(&restored)?;
    let restored_session = restored_store
        .get::<SessionPayload>(session.id)?
        .context("restored session is missing")?;
    let snapshot = restored_store.export_snapshot()?;
    let restored_integrity = restored_store.integrity_report()?;
    if restored_integrity.integrity != "ok"
        || restored_integrity.foreign_key_violations != 0
        || !restored_integrity.audit_chain_valid
    {
        bail!("restored canonical state smoke integrity failed");
    }

    let report = json!({
        "schemaVersion":"soleaux.canonical-state-smoke/v1",
        "databaseSchemaVersion":integrity.schema_version,
        "workspaceId":workspace_id,
        "sessionId":session.id,
        "turnId":turn.id,
        "sessionRevision":session.revision,
        "operationState":completed.state,
        "operationAttempt":completed.attempt,
        "replayedResult":replayed_result,
        "entityCount":integrity.entity_count,
        "linkCount":integrity.link_count,
        "operationCount":integrity.operation_count,
        "auditChainValid":integrity.audit_chain_valid,
        "backup":backup_manifest,
        "restore":restore_manifest,
        "restoredSessionTitle":restored_session.payload.title,
        "snapshotEntityCount":snapshot.entities.len(),
        "snapshotAuditCount":snapshot.audit.len(),
        "restoredIntegrity":restored_integrity.integrity,
        "status":"pass",
    });
    fs::write(&report_path, serde_json::to_vec_pretty(&report)?)
        .with_context(|| format!("writing smoke report {}", report_path.display()))?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
