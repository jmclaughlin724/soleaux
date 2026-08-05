use crate::model::*;
use anyhow::{Context, Result, bail};
use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Row, Transaction, TransactionBehavior, params,
    types::Type,
};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub const SCHEMA_VERSION: i64 = 1;

const ENTITY_SELECT: &str =
    "SELECT id, kind, workspace_id, parent_id, origin_platform, native_id, state, sensitivity,
            revision, payload_json, payload_hash, idempotency_key, expires_at_unix_ms,
            created_at_unix_ms, updated_at_unix_ms, tombstoned_at_unix_ms
     FROM canonical_entities";
const OPERATION_SELECT: &str =
    "SELECT operation_key, request_hash, operation_kind, workspace_id, state, lease_id,
            owner_id, attempt, lease_expires_at_unix_ms, result_json, error_json,
            created_at_unix_ms, updated_at_unix_ms
     FROM operation_leases";

#[derive(Debug, Clone)]
pub(crate) struct SerializedEntityInput {
    pub id: Option<Uuid>,
    pub kind: EntityKind,
    pub workspace_id: Option<Uuid>,
    pub parent_id: Option<Uuid>,
    pub origin_platform: Option<String>,
    pub native_id: Option<String>,
    pub state: String,
    pub sensitivity: Sensitivity,
    pub idempotency_key: Option<String>,
    pub expected_revision: Option<u64>,
    pub expires_at_unix_ms: Option<i64>,
    pub payload: Value,
    pub payload_hash: String,
}

pub(crate) fn open_writer(path: &Path) -> Result<Connection> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
    )
    .with_context(|| format!("opening canonical SQLite writer {}", path.display()))?;
    configure(&connection)?;
    Ok(connection)
}

pub(crate) fn open_reader(path: &Path) -> Result<Connection> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .with_context(|| format!("opening canonical SQLite reader {}", path.display()))?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    Ok(connection)
}

fn configure(connection: &Connection) -> Result<()> {
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "FULL")?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.pragma_update(None, "trusted_schema", "OFF")?;
    connection.pragma_update(None, "wal_autocheckpoint", 1_000_i64)?;
    Ok(())
}

pub(crate) fn migrate(connection: &mut Connection) -> Result<()> {
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if version > SCHEMA_VERSION {
        bail!("Soleaux canonical schema {version} is newer than supported schema {SCHEMA_VERSION}");
    }
    if version == 0 {
        connection.execute_batch(
            r#"
            BEGIN IMMEDIATE;
            CREATE TABLE canonical_entities (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                workspace_id TEXT,
                workspace_key TEXT NOT NULL,
                parent_id TEXT,
                origin_platform TEXT,
                native_id TEXT,
                state TEXT NOT NULL,
                sensitivity TEXT NOT NULL CHECK(sensitivity IN ('public', 'internal', 'confidential', 'secret')),
                revision INTEGER NOT NULL CHECK(revision >= 1),
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                idempotency_key TEXT,
                expires_at_unix_ms INTEGER,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                tombstoned_at_unix_ms INTEGER,
                FOREIGN KEY(parent_id) REFERENCES canonical_entities(id) ON DELETE RESTRICT
            );
            CREATE INDEX canonical_entities_kind_workspace
                ON canonical_entities(kind, workspace_key, updated_at_unix_ms, id);
            CREATE INDEX canonical_entities_parent
                ON canonical_entities(parent_id, kind, updated_at_unix_ms);
            CREATE INDEX canonical_entities_expiry
                ON canonical_entities(expires_at_unix_ms, tombstoned_at_unix_ms);
            CREATE UNIQUE INDEX canonical_entities_idempotency
                ON canonical_entities(kind, workspace_key, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            CREATE UNIQUE INDEX canonical_entities_native_identity
                ON canonical_entities(kind, origin_platform, native_id)
                WHERE origin_platform IS NOT NULL AND native_id IS NOT NULL;

            CREATE TABLE entity_links (
                source_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                target_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY(source_id, relationship, target_id),
                FOREIGN KEY(source_id) REFERENCES canonical_entities(id) ON DELETE CASCADE,
                FOREIGN KEY(target_id) REFERENCES canonical_entities(id) ON DELETE CASCADE
            );
            CREATE INDEX entity_links_target
                ON entity_links(target_id, relationship, source_id);

            CREATE TABLE adapter_cursors (
                adapter TEXT NOT NULL,
                scope TEXT NOT NULL,
                cursor TEXT NOT NULL,
                etag TEXT,
                watermark TEXT,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                metadata_json TEXT NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY(adapter, scope)
            );

            CREATE TABLE retention_policies (
                id TEXT PRIMARY KEY,
                workspace_id TEXT,
                workspace_key TEXT NOT NULL,
                entity_kind TEXT,
                kind_key TEXT NOT NULL,
                retain_for_ms INTEGER NOT NULL CHECK(retain_for_ms >= 0),
                tombstone_grace_ms INTEGER NOT NULL CHECK(tombstone_grace_ms >= 0),
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                revision INTEGER NOT NULL CHECK(revision >= 1),
                metadata_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX retention_policies_scope
                ON retention_policies(workspace_key, kind_key);

            CREATE TABLE tombstones (
                entity_id TEXT PRIMARY KEY,
                entity_kind TEXT NOT NULL,
                workspace_id TEXT,
                payload_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                tombstoned_at_unix_ms INTEGER NOT NULL,
                purged_at_unix_ms INTEGER
            );
            CREATE INDEX tombstones_lifecycle
                ON tombstones(tombstoned_at_unix_ms, purged_at_unix_ms);

            CREATE TABLE operation_leases (
                operation_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                operation_kind TEXT NOT NULL,
                workspace_id TEXT,
                state TEXT NOT NULL CHECK(state IN ('leased', 'completed', 'failed', 'cancelled', 'abandoned')),
                lease_id TEXT,
                owner_id TEXT,
                attempt INTEGER NOT NULL CHECK(attempt >= 1),
                lease_expires_at_unix_ms INTEGER,
                result_json TEXT,
                error_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL
            );
            CREATE INDEX operation_leases_recovery
                ON operation_leases(state, lease_expires_at_unix_ms, updated_at_unix_ms);

            CREATE TABLE state_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                workspace_id TEXT,
                entity_id TEXT,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                previous_event_hash TEXT,
                event_hash TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );
            CREATE INDEX state_audit_workspace
                ON state_audit(workspace_id, sequence);
            CREATE INDEX state_audit_entity
                ON state_audit(entity_id, sequence);

            PRAGMA user_version = 1;
            COMMIT;
            "#,
        )?;
    }
    Ok(())
}

pub(crate) fn put_entity(
    connection: &mut Connection,
    input: &SerializedEntityInput,
) -> Result<SerializedEntityRecord> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let workspace_key = workspace_key(input.workspace_id);

    if let Some(idempotency_key) = &input.idempotency_key {
        let existing = transaction
            .query_row(
                &format!(
                    "{ENTITY_SELECT} WHERE kind = ?1 AND workspace_key = ?2 AND idempotency_key = ?3"
                ),
                params![input.kind.as_str(), workspace_key, idempotency_key],
                entity_from_row,
            )
            .optional()?;
        if let Some(existing) = existing {
            if entity_replay_matches(&existing, input) {
                transaction.commit()?;
                return Ok(existing);
            }
            if input.id != Some(existing.id) {
                bail!("canonical idempotency collision: immutable request differs");
            }
        }
    }

    let id = input.id.unwrap_or_else(Uuid::now_v7);
    let existing = transaction
        .query_row(
            &format!("{ENTITY_SELECT} WHERE id = ?1"),
            [id.to_string()],
            entity_from_row,
        )
        .optional()?;
    if let Some(parent_id) = input.parent_id {
        ensure_live_entity(&transaction, parent_id, "parent")?;
    }
    let now = unix_ms();
    let record = if let Some(existing) = existing {
        if existing.kind != input.kind
            || existing.workspace_id != input.workspace_id
            || existing.parent_id != input.parent_id
            || existing.origin_platform != input.origin_platform
            || existing.native_id != input.native_id
        {
            bail!("canonical entity immutable identity cannot be changed");
        }
        if existing.tombstoned_at_unix_ms.is_some() {
            bail!("tombstoned canonical entity cannot be updated");
        }
        if entity_replay_matches(&existing, input) {
            transaction.commit()?;
            return Ok(existing);
        }
        let expected = input
            .expected_revision
            .context("canonical entity update requires expected_revision")?;
        if expected != existing.revision {
            bail!(
                "canonical entity revision conflict: expected {expected}, current {}",
                existing.revision
            );
        }
        let revision = existing
            .revision
            .checked_add(1)
            .context("canonical entity revision overflow")?;
        transaction.execute(
            "UPDATE canonical_entities
             SET state = ?2, sensitivity = ?3, revision = ?4, payload_json = ?5,
                 payload_hash = ?6, idempotency_key = ?7, expires_at_unix_ms = ?8,
                 updated_at_unix_ms = ?9
             WHERE id = ?1 AND revision = ?10 AND tombstoned_at_unix_ms IS NULL",
            params![
                id.to_string(),
                input.state,
                input.sensitivity.as_str(),
                i64_from_u64(revision, "entity revision")?,
                serde_json::to_string(&input.payload)?,
                input.payload_hash,
                input.idempotency_key,
                input.expires_at_unix_ms,
                now,
                i64_from_u64(existing.revision, "entity revision")?,
            ],
        )?;
        append_audit_tx(
            &transaction,
            "canonical.entity.updated",
            input.workspace_id,
            Some(id),
            json!({
                "kind":input.kind,
                "revision":revision,
                "payloadHash":input.payload_hash,
                "state":input.state,
            }),
        )?;
        read_entity_tx(&transaction, id)?
    } else {
        if input.expected_revision.is_some() {
            bail!("new canonical entity cannot declare expected_revision");
        }
        transaction.execute(
            "INSERT INTO canonical_entities(
                id, kind, workspace_id, workspace_key, parent_id, origin_platform, native_id,
                state, sensitivity, revision, payload_json, payload_hash, idempotency_key,
                expires_at_unix_ms, created_at_unix_ms, updated_at_unix_ms,
                tombstoned_at_unix_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 1, ?10, ?11, ?12, ?13, ?14, ?14, NULL)",
            params![
                id.to_string(),
                input.kind.as_str(),
                input.workspace_id.map(|value| value.to_string()),
                workspace_key,
                input.parent_id.map(|value| value.to_string()),
                input.origin_platform,
                input.native_id,
                input.state,
                input.sensitivity.as_str(),
                serde_json::to_string(&input.payload)?,
                input.payload_hash,
                input.idempotency_key,
                input.expires_at_unix_ms,
                now,
            ],
        )?;
        append_audit_tx(
            &transaction,
            "canonical.entity.created",
            input.workspace_id,
            Some(id),
            json!({
                "kind":input.kind,
                "revision":1,
                "payloadHash":input.payload_hash,
                "state":input.state,
            }),
        )?;
        read_entity_tx(&transaction, id)?
    };
    transaction.commit()?;
    Ok(record)
}

pub(crate) fn upsert_native_entity(
    connection: &mut Connection,
    input: &SerializedEntityInput,
) -> Result<SerializedEntityRecord> {
    let origin_platform = input
        .origin_platform
        .as_deref()
        .context("native upsert requires an origin platform")?;
    let native_id = input
        .native_id
        .as_deref()
        .context("native upsert requires a native id")?;
    if input.expected_revision.is_some() {
        bail!("native upsert does not accept expected_revision");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let existing = transaction
        .query_row(
            &format!("{ENTITY_SELECT} WHERE kind = ?1 AND origin_platform = ?2 AND native_id = ?3"),
            params![input.kind.as_str(), origin_platform, native_id],
            entity_from_row,
        )
        .optional()?;
    let now = unix_ms();
    let record = if let Some(existing) = existing {
        if existing.workspace_id != input.workspace_id || existing.parent_id != input.parent_id {
            bail!("native upsert cannot change canonical workspace or parent identity");
        }
        if input.id.is_some_and(|id| id != existing.id) {
            bail!("native upsert id does not match the existing canonical entity");
        }
        if existing.tombstoned_at_unix_ms.is_some() {
            bail!("tombstoned canonical entity cannot be updated");
        }
        if entity_replay_matches(&existing, input) {
            transaction.commit()?;
            return Ok(existing);
        }
        let revision = existing
            .revision
            .checked_add(1)
            .context("canonical entity revision overflow")?;
        transaction.execute(
            "UPDATE canonical_entities
             SET state = ?2, sensitivity = ?3, revision = ?4, payload_json = ?5,
                 payload_hash = ?6, idempotency_key = ?7, expires_at_unix_ms = ?8,
                 updated_at_unix_ms = ?9
             WHERE id = ?1 AND tombstoned_at_unix_ms IS NULL",
            params![
                existing.id.to_string(),
                input.state,
                input.sensitivity.as_str(),
                i64_from_u64(revision, "entity revision")?,
                serde_json::to_string(&input.payload)?,
                input.payload_hash,
                input.idempotency_key,
                input.expires_at_unix_ms,
                now,
            ],
        )?;
        append_audit_tx(
            &transaction,
            "canonical.entity.native_upserted",
            input.workspace_id,
            Some(existing.id),
            json!({
                "kind":input.kind,
                "revision":revision,
                "payloadHash":input.payload_hash,
                "state":input.state,
                "originPlatform":origin_platform,
                "nativeId":native_id,
            }),
        )?;
        read_entity_tx(&transaction, existing.id)?
    } else {
        if let Some(parent_id) = input.parent_id {
            ensure_live_entity(&transaction, parent_id, "parent")?;
        }
        let id = input.id.unwrap_or_else(Uuid::now_v7);
        transaction.execute(
            "INSERT INTO canonical_entities(
                id, kind, workspace_id, workspace_key, parent_id, origin_platform, native_id,
                state, sensitivity, revision, payload_json, payload_hash, idempotency_key,
                expires_at_unix_ms, created_at_unix_ms, updated_at_unix_ms,
                tombstoned_at_unix_ms
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 1, ?10, ?11, ?12, ?13, ?14, ?14, NULL)",
            params![
                id.to_string(),
                input.kind.as_str(),
                input.workspace_id.map(|value| value.to_string()),
                workspace_key(input.workspace_id),
                input.parent_id.map(|value| value.to_string()),
                origin_platform,
                native_id,
                input.state,
                input.sensitivity.as_str(),
                serde_json::to_string(&input.payload)?,
                input.payload_hash,
                input.idempotency_key,
                input.expires_at_unix_ms,
                now,
            ],
        )?;
        append_audit_tx(
            &transaction,
            "canonical.entity.created",
            input.workspace_id,
            Some(id),
            json!({
                "kind":input.kind,
                "revision":1,
                "payloadHash":input.payload_hash,
                "state":input.state,
                "originPlatform":origin_platform,
                "nativeId":native_id,
            }),
        )?;
        read_entity_tx(&transaction, id)?
    };
    transaction.commit()?;
    Ok(record)
}

fn entity_replay_matches(existing: &SerializedEntityRecord, input: &SerializedEntityInput) -> bool {
    existing.kind == input.kind
        && existing.workspace_id == input.workspace_id
        && existing.parent_id == input.parent_id
        && existing.origin_platform == input.origin_platform
        && existing.native_id == input.native_id
        && existing.state == input.state
        && existing.sensitivity == input.sensitivity
        && existing.payload_hash == input.payload_hash
        && existing.expires_at_unix_ms == input.expires_at_unix_ms
        && existing.tombstoned_at_unix_ms.is_none()
}

fn read_entity_tx(transaction: &Transaction<'_>, id: Uuid) -> Result<SerializedEntityRecord> {
    transaction
        .query_row(
            &format!("{ENTITY_SELECT} WHERE id = ?1"),
            [id.to_string()],
            entity_from_row,
        )
        .context("reading canonical entity after mutation")
}

pub(crate) fn get_entity(
    connection: &Connection,
    id: Uuid,
) -> Result<Option<SerializedEntityRecord>> {
    connection
        .query_row(
            &format!("{ENTITY_SELECT} WHERE id = ?1"),
            [id.to_string()],
            entity_from_row,
        )
        .optional()
        .context("reading canonical entity")
}

pub(crate) fn get_entity_by_native(
    connection: &Connection,
    kind: EntityKind,
    origin_platform: &str,
    native_id: &str,
) -> Result<Option<SerializedEntityRecord>> {
    connection
        .query_row(
            &format!("{ENTITY_SELECT} WHERE kind = ?1 AND origin_platform = ?2 AND native_id = ?3"),
            params![kind.as_str(), origin_platform, native_id],
            entity_from_row,
        )
        .optional()
        .context("reading canonical entity by native identity")
}

pub(crate) fn list_entities(
    connection: &Connection,
    kind: Option<EntityKind>,
    workspace_id: Option<Uuid>,
    limit: usize,
    include_tombstoned: bool,
) -> Result<Vec<SerializedEntityRecord>> {
    let workspace = workspace_key(workspace_id);
    let tombstone = if include_tombstoned {
        ""
    } else {
        " AND tombstoned_at_unix_ms IS NULL"
    };
    let limit = i64::try_from(limit).unwrap_or(i64::MAX);
    let mut records = Vec::new();
    if let Some(kind) = kind {
        let sql = format!(
            "{ENTITY_SELECT} WHERE kind = ?1 AND workspace_key = ?2{tombstone}
             ORDER BY updated_at_unix_ms, id LIMIT ?3"
        );
        let mut statement = connection.prepare(&sql)?;
        let rows =
            statement.query_map(params![kind.as_str(), workspace, limit], entity_from_row)?;
        records.extend(rows.collect::<rusqlite::Result<Vec<_>>>()?);
    } else {
        let sql = format!(
            "{ENTITY_SELECT} WHERE workspace_key = ?1{tombstone}
             ORDER BY kind, updated_at_unix_ms, id LIMIT ?2"
        );
        let mut statement = connection.prepare(&sql)?;
        let rows = statement.query_map(params![workspace, limit], entity_from_row)?;
        records.extend(rows.collect::<rusqlite::Result<Vec<_>>>()?);
    }
    Ok(records)
}

pub(crate) fn list_entities_all(
    connection: &Connection,
    kind: EntityKind,
    limit: usize,
    include_tombstoned: bool,
) -> Result<Vec<SerializedEntityRecord>> {
    let tombstone = if include_tombstoned {
        ""
    } else {
        " AND tombstoned_at_unix_ms IS NULL"
    };
    let sql = format!(
        "{ENTITY_SELECT} WHERE kind = ?1{tombstone}
         ORDER BY updated_at_unix_ms, id LIMIT ?2"
    );
    let mut statement = connection.prepare(&sql)?;
    let rows = statement.query_map(
        params![kind.as_str(), i64::try_from(limit).unwrap_or(i64::MAX)],
        entity_from_row,
    )?;
    Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
}

pub(crate) fn put_link(
    connection: &mut Connection,
    input: &EntityLinkInput,
) -> Result<EntityLinkRecord> {
    if input.source_id == input.target_id {
        bail!("canonical relationship endpoints must differ");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let source = ensure_live_entity(&transaction, input.source_id, "source")?;
    ensure_live_entity(&transaction, input.target_id, "target")?;
    let now = unix_ms();
    let metadata = serde_json::to_string(&input.metadata)?;
    transaction.execute(
        "INSERT INTO entity_links(source_id, relationship, target_id, metadata_json, created_at_unix_ms, updated_at_unix_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?5)
         ON CONFLICT(source_id, relationship, target_id) DO UPDATE SET
             metadata_json = excluded.metadata_json,
             updated_at_unix_ms = excluded.updated_at_unix_ms",
        params![
            input.source_id.to_string(),
            input.relationship.as_str(),
            input.target_id.to_string(),
            metadata,
            now,
        ],
    )?;
    append_audit_tx(
        &transaction,
        "canonical.link.upserted",
        source.workspace_id,
        Some(input.source_id),
        json!({
            "relationship":input.relationship,
            "targetId":input.target_id,
        }),
    )?;
    transaction.commit()?;
    Ok(EntityLinkRecord {
        source_id: input.source_id,
        relationship: input.relationship,
        target_id: input.target_id,
        metadata: input.metadata.clone(),
        created_at_unix_ms: now,
        updated_at_unix_ms: now,
    })
}

pub(crate) fn links_from(
    connection: &Connection,
    source_id: Uuid,
) -> Result<Vec<EntityLinkRecord>> {
    let mut statement = connection.prepare(
        "SELECT source_id, relationship, target_id, metadata_json, created_at_unix_ms, updated_at_unix_ms
         FROM entity_links WHERE source_id = ?1 ORDER BY relationship, target_id",
    )?;
    let rows = statement.query_map([source_id.to_string()], link_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading canonical links")
}

pub(crate) fn all_links(connection: &Connection) -> Result<Vec<EntityLinkRecord>> {
    let mut statement = connection.prepare(
        "SELECT source_id, relationship, target_id, metadata_json, created_at_unix_ms, updated_at_unix_ms
         FROM entity_links ORDER BY source_id, relationship, target_id",
    )?;
    let rows = statement.query_map([], link_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading all canonical links")
}

pub(crate) fn put_adapter_cursor(
    connection: &mut Connection,
    input: &AdapterCursorInput,
) -> Result<AdapterCursorRecord> {
    require_non_empty(&input.adapter, "adapter")?;
    require_non_empty(&input.scope, "cursor scope")?;
    require_non_empty(&input.cursor, "cursor")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let existing = transaction
        .query_row(
            "SELECT adapter, scope, cursor, etag, watermark, revision, metadata_json, updated_at_unix_ms
             FROM adapter_cursors WHERE adapter = ?1 AND scope = ?2",
            params![input.adapter, input.scope],
            cursor_from_row,
        )
        .optional()?;
    let now = unix_ms();
    let revision = if let Some(existing) = &existing {
        if existing.cursor == input.cursor
            && existing.etag == input.etag
            && existing.watermark == input.watermark
            && existing.metadata == input.metadata
        {
            transaction.commit()?;
            return Ok(existing.clone());
        }
        let expected = input
            .expected_revision
            .context("adapter cursor update requires expected_revision")?;
        if expected != existing.revision {
            bail!(
                "adapter cursor revision conflict: expected {expected}, current {}",
                existing.revision
            );
        }
        existing
            .revision
            .checked_add(1)
            .context("adapter cursor revision overflow")?
    } else {
        if input.expected_revision.is_some() {
            bail!("new adapter cursor cannot declare expected_revision");
        }
        1
    };
    transaction.execute(
        "INSERT INTO adapter_cursors(adapter, scope, cursor, etag, watermark, revision, metadata_json, updated_at_unix_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
         ON CONFLICT(adapter, scope) DO UPDATE SET
             cursor = excluded.cursor,
             etag = excluded.etag,
             watermark = excluded.watermark,
             revision = excluded.revision,
             metadata_json = excluded.metadata_json,
             updated_at_unix_ms = excluded.updated_at_unix_ms",
        params![
            input.adapter,
            input.scope,
            input.cursor,
            input.etag,
            input.watermark,
            i64_from_u64(revision, "cursor revision")?,
            serde_json::to_string(&input.metadata)?,
            now,
        ],
    )?;
    append_audit_tx(
        &transaction,
        "adapter.cursor.updated",
        None,
        None,
        json!({"adapter":input.adapter,"scope":input.scope,"revision":revision}),
    )?;
    transaction.commit()?;
    Ok(AdapterCursorRecord {
        adapter: input.adapter.clone(),
        scope: input.scope.clone(),
        cursor: input.cursor.clone(),
        etag: input.etag.clone(),
        watermark: input.watermark.clone(),
        revision,
        metadata: input.metadata.clone(),
        updated_at_unix_ms: now,
    })
}

pub(crate) fn adapter_cursor(
    connection: &Connection,
    adapter: &str,
    scope: &str,
) -> Result<Option<AdapterCursorRecord>> {
    connection
        .query_row(
            "SELECT adapter, scope, cursor, etag, watermark, revision, metadata_json, updated_at_unix_ms
             FROM adapter_cursors WHERE adapter = ?1 AND scope = ?2",
            params![adapter, scope],
            cursor_from_row,
        )
        .optional()
        .context("reading adapter cursor")
}

pub(crate) fn all_adapter_cursors(connection: &Connection) -> Result<Vec<AdapterCursorRecord>> {
    let mut statement = connection.prepare(
        "SELECT adapter, scope, cursor, etag, watermark, revision, metadata_json, updated_at_unix_ms
         FROM adapter_cursors ORDER BY adapter, scope",
    )?;
    let rows = statement.query_map([], cursor_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading all adapter cursors")
}

pub(crate) fn put_retention_policy(
    connection: &mut Connection,
    input: &RetentionPolicyInput,
) -> Result<RetentionPolicyRecord> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let workspace = workspace_key(input.workspace_id);
    let kind_key = input.entity_kind.map(EntityKind::as_str).unwrap_or("");
    let existing = transaction
        .query_row(
            "SELECT id, workspace_id, entity_kind, retain_for_ms, tombstone_grace_ms,
                    enabled, revision, metadata_json, created_at_unix_ms, updated_at_unix_ms
             FROM retention_policies WHERE workspace_key = ?1 AND kind_key = ?2",
            params![workspace, kind_key],
            retention_from_row,
        )
        .optional()?;
    let now = unix_ms();
    let (id, revision, created_at) = if let Some(existing) = &existing {
        if existing.retain_for_ms == input.retain_for_ms
            && existing.tombstone_grace_ms == input.tombstone_grace_ms
            && existing.enabled == input.enabled
            && existing.metadata == input.metadata
        {
            transaction.commit()?;
            return Ok(existing.clone());
        }
        let expected = input
            .expected_revision
            .context("retention policy update requires expected_revision")?;
        if expected != existing.revision {
            bail!(
                "retention policy revision conflict: expected {expected}, current {}",
                existing.revision
            );
        }
        if input.id.is_some_and(|id| id != existing.id) {
            bail!("retention policy scope is already owned by another id");
        }
        (
            existing.id,
            existing
                .revision
                .checked_add(1)
                .context("retention policy revision overflow")?,
            existing.created_at_unix_ms,
        )
    } else {
        if input.expected_revision.is_some() {
            bail!("new retention policy cannot declare expected_revision");
        }
        (input.id.unwrap_or_else(Uuid::now_v7), 1, now)
    };
    transaction.execute(
        "INSERT INTO retention_policies(
            id, workspace_id, workspace_key, entity_kind, kind_key, retain_for_ms,
            tombstone_grace_ms, enabled, revision, metadata_json,
            created_at_unix_ms, updated_at_unix_ms
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
         ON CONFLICT(workspace_key, kind_key) DO UPDATE SET
            retain_for_ms = excluded.retain_for_ms,
            tombstone_grace_ms = excluded.tombstone_grace_ms,
            enabled = excluded.enabled,
            revision = excluded.revision,
            metadata_json = excluded.metadata_json,
            updated_at_unix_ms = excluded.updated_at_unix_ms",
        params![
            id.to_string(),
            input.workspace_id.map(|value| value.to_string()),
            workspace,
            input.entity_kind.map(EntityKind::as_str),
            kind_key,
            i64_from_u64(input.retain_for_ms, "retention duration")?,
            i64_from_u64(input.tombstone_grace_ms, "tombstone grace")?,
            input.enabled,
            i64_from_u64(revision, "retention policy revision")?,
            serde_json::to_string(&input.metadata)?,
            created_at,
            now,
        ],
    )?;
    append_audit_tx(
        &transaction,
        "retention.policy.updated",
        input.workspace_id,
        None,
        json!({"policyId":id,"entityKind":input.entity_kind,"revision":revision}),
    )?;
    transaction.commit()?;
    Ok(RetentionPolicyRecord {
        id,
        workspace_id: input.workspace_id,
        entity_kind: input.entity_kind,
        retain_for_ms: input.retain_for_ms,
        tombstone_grace_ms: input.tombstone_grace_ms,
        enabled: input.enabled,
        revision,
        metadata: input.metadata.clone(),
        created_at_unix_ms: created_at,
        updated_at_unix_ms: now,
    })
}

pub(crate) fn retention_policies(connection: &Connection) -> Result<Vec<RetentionPolicyRecord>> {
    let mut statement = connection.prepare(
        "SELECT id, workspace_id, entity_kind, retain_for_ms, tombstone_grace_ms,
                enabled, revision, metadata_json, created_at_unix_ms, updated_at_unix_ms
         FROM retention_policies ORDER BY workspace_key, kind_key",
    )?;
    let rows = statement.query_map([], retention_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading retention policies")
}

pub(crate) fn tombstone_entity(
    connection: &mut Connection,
    entity_id: Uuid,
    reason: &str,
    actor: &str,
) -> Result<TombstoneRecord> {
    require_non_empty(reason, "tombstone reason")?;
    require_non_empty(actor, "tombstone actor")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let record = tombstone_entity_tx(&transaction, entity_id, reason, actor, unix_ms())?;
    transaction.commit()?;
    Ok(record)
}

fn tombstone_entity_tx(
    transaction: &Transaction<'_>,
    entity_id: Uuid,
    reason: &str,
    actor: &str,
    now: i64,
) -> Result<TombstoneRecord> {
    let entity = read_entity_tx(transaction, entity_id)?;
    if entity.tombstoned_at_unix_ms.is_some() {
        return transaction
            .query_row(
                "SELECT entity_id, entity_kind, workspace_id, payload_hash, reason, actor,
                        tombstoned_at_unix_ms, purged_at_unix_ms
                 FROM tombstones WHERE entity_id = ?1",
                [entity_id.to_string()],
                tombstone_from_row,
            )
            .context("reading existing tombstone");
    }
    let revision = entity
        .revision
        .checked_add(1)
        .context("canonical entity revision overflow")?;
    transaction.execute(
        "UPDATE canonical_entities
         SET state = 'tombstoned', revision = ?2, tombstoned_at_unix_ms = ?3,
             updated_at_unix_ms = ?3
         WHERE id = ?1 AND tombstoned_at_unix_ms IS NULL",
        params![
            entity_id.to_string(),
            i64_from_u64(revision, "entity revision")?,
            now,
        ],
    )?;
    transaction.execute(
        "INSERT INTO tombstones(
            entity_id, entity_kind, workspace_id, payload_hash, reason, actor,
            tombstoned_at_unix_ms, purged_at_unix_ms
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL)",
        params![
            entity_id.to_string(),
            entity.kind.as_str(),
            entity.workspace_id.map(|value| value.to_string()),
            entity.payload_hash,
            reason,
            actor,
            now,
        ],
    )?;
    append_audit_tx(
        transaction,
        "canonical.entity.tombstoned",
        entity.workspace_id,
        Some(entity_id),
        json!({"kind":entity.kind,"reason":reason,"actor":actor,"revision":revision}),
    )?;
    Ok(TombstoneRecord {
        entity_id,
        entity_kind: entity.kind,
        workspace_id: entity.workspace_id,
        payload_hash: entity.payload_hash,
        reason: reason.to_string(),
        actor: actor.to_string(),
        tombstoned_at_unix_ms: now,
        purged_at_unix_ms: None,
    })
}

pub(crate) fn apply_retention(
    connection: &mut Connection,
    now: i64,
    limit: usize,
) -> Result<Vec<TombstoneRecord>> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let mut candidates = BTreeSet::new();
    {
        let mut statement = transaction.prepare(
            "SELECT id FROM canonical_entities
             WHERE tombstoned_at_unix_ms IS NULL
               AND expires_at_unix_ms IS NOT NULL
               AND expires_at_unix_ms <= ?1
             ORDER BY expires_at_unix_ms, id LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![now, i64::try_from(limit).unwrap_or(i64::MAX)],
            |row| row.get::<_, String>(0),
        )?;
        for id in rows {
            candidates.insert(parse_uuid(0, &id?)?);
        }
    }
    let policies = retention_policies_tx(&transaction)?;
    for policy in policies.into_iter().filter(|policy| policy.enabled) {
        if candidates.len() >= limit {
            break;
        }
        let cutoff = now.saturating_sub(i64_from_u64(policy.retain_for_ms, "retention duration")?);
        let remaining = limit.saturating_sub(candidates.len());
        let workspace = workspace_key(policy.workspace_id);
        let mut sql = String::from(
            "SELECT id FROM canonical_entities
             WHERE workspace_key = ?1
               AND tombstoned_at_unix_ms IS NULL
               AND updated_at_unix_ms <= ?2",
        );
        if policy.entity_kind.is_some() {
            sql.push_str(" AND kind = ?3 ORDER BY updated_at_unix_ms, id LIMIT ?4");
        } else {
            sql.push_str(" ORDER BY updated_at_unix_ms, id LIMIT ?3");
        }
        let mut statement = transaction.prepare(&sql)?;
        if let Some(kind) = policy.entity_kind {
            let rows = statement.query_map(
                params![
                    workspace,
                    cutoff,
                    kind.as_str(),
                    i64::try_from(remaining).unwrap_or(i64::MAX)
                ],
                |row| row.get::<_, String>(0),
            )?;
            for id in rows {
                candidates.insert(parse_uuid(0, &id?)?);
            }
        } else {
            let rows = statement.query_map(
                params![
                    workspace,
                    cutoff,
                    i64::try_from(remaining).unwrap_or(i64::MAX)
                ],
                |row| row.get::<_, String>(0),
            )?;
            for id in rows {
                candidates.insert(parse_uuid(0, &id?)?);
            }
        }
    }
    let mut tombstones = Vec::new();
    for id in candidates.into_iter().take(limit) {
        tombstones.push(tombstone_entity_tx(
            &transaction,
            id,
            "retention_policy",
            "soleaux-retention",
            now,
        )?);
    }
    transaction.commit()?;
    Ok(tombstones)
}

pub(crate) fn purge_tombstones(
    connection: &mut Connection,
    before_unix_ms: i64,
    limit: usize,
) -> Result<usize> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let ids = {
        let mut statement = transaction.prepare(
            "SELECT entity_id FROM tombstones
             WHERE purged_at_unix_ms IS NULL AND tombstoned_at_unix_ms <= ?1
             ORDER BY tombstoned_at_unix_ms, entity_id LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![before_unix_ms, i64::try_from(limit).unwrap_or(i64::MAX)],
            |row| row.get::<_, String>(0),
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let now = unix_ms();
    let mut purged = 0;
    for id in ids {
        let changed = transaction.execute(
            "DELETE FROM canonical_entities
             WHERE id = ?1 AND tombstoned_at_unix_ms IS NOT NULL",
            [id.as_str()],
        )?;
        if changed == 1 {
            transaction.execute(
                "UPDATE tombstones SET purged_at_unix_ms = ?2 WHERE entity_id = ?1",
                params![id, now],
            )?;
            purged += 1;
        }
    }
    if purged > 0 {
        append_audit_tx(
            &transaction,
            "retention.tombstones.purged",
            None,
            None,
            json!({"count":purged,"beforeUnixMs":before_unix_ms}),
        )?;
    }
    transaction.commit()?;
    Ok(purged)
}

pub(crate) fn all_tombstones(connection: &Connection) -> Result<Vec<TombstoneRecord>> {
    let mut statement = connection.prepare(
        "SELECT entity_id, entity_kind, workspace_id, payload_hash, reason, actor,
                tombstoned_at_unix_ms, purged_at_unix_ms
         FROM tombstones ORDER BY tombstoned_at_unix_ms, entity_id",
    )?;
    let rows = statement.query_map([], tombstone_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading tombstones")
}

pub(crate) fn acquire_operation(
    connection: &mut Connection,
    operation_key: &str,
    request_hash: &str,
    operation_kind: &str,
    workspace_id: Option<Uuid>,
    owner_id: &str,
    ttl_ms: u64,
) -> Result<OperationLeaseOutcome> {
    require_non_empty(operation_key, "operation key")?;
    require_non_empty(request_hash, "request hash")?;
    require_non_empty(operation_kind, "operation kind")?;
    require_non_empty(owner_id, "operation owner")?;
    if ttl_ms == 0 || ttl_ms > 86_400_000 {
        bail!("operation lease ttl must be between 1 ms and 24 hours");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let existing = operation_tx(&transaction, operation_key)?;
    let now = unix_ms();
    let expires = now
        .checked_add(i64_from_u64(ttl_ms, "operation lease ttl")?)
        .context("operation lease expiration overflow")?;
    let outcome = if let Some(existing) = existing {
        if existing.request_hash != request_hash {
            bail!("operation key collision: request hash differs");
        }
        if existing.operation_kind != operation_kind || existing.workspace_id != workspace_id {
            bail!("operation key collision: immutable operation identity differs");
        }
        match existing.state.as_str() {
            "completed" => OperationLeaseOutcome::Replayed(
                existing
                    .result
                    .context("completed operation omitted its result")?,
            ),
            "leased"
                if existing
                    .lease_expires_at_unix_ms
                    .is_some_and(|value| value > now) =>
            {
                OperationLeaseOutcome::InFlight(existing)
            }
            "leased" | "abandoned" | "failed" | "cancelled" => {
                let lease_id = Uuid::now_v7();
                let attempt = existing
                    .attempt
                    .checked_add(1)
                    .context("operation attempt overflow")?;
                transaction.execute(
                    "UPDATE operation_leases
                     SET state = 'leased', lease_id = ?2, owner_id = ?3, attempt = ?4,
                         lease_expires_at_unix_ms = ?5, result_json = NULL, error_json = NULL,
                         updated_at_unix_ms = ?6
                     WHERE operation_key = ?1",
                    params![
                        operation_key,
                        lease_id.to_string(),
                        owner_id,
                        i64_from_u64(attempt, "operation attempt")?,
                        expires,
                        now,
                    ],
                )?;
                let acquired = operation_tx(&transaction, operation_key)?
                    .context("reacquired operation disappeared")?;
                append_audit_tx(
                    &transaction,
                    "operation.lease.acquired",
                    workspace_id,
                    None,
                    json!({
                        "operationKey":operation_key,
                        "operationKind":operation_kind,
                        "leaseId":lease_id,
                        "ownerId":owner_id,
                        "attempt":attempt,
                    }),
                )?;
                OperationLeaseOutcome::Acquired(acquired)
            }
            other => bail!("unsupported operation state: {other}"),
        }
    } else {
        let lease_id = Uuid::now_v7();
        transaction.execute(
            "INSERT INTO operation_leases(
                operation_key, request_hash, operation_kind, workspace_id, state,
                lease_id, owner_id, attempt, lease_expires_at_unix_ms,
                result_json, error_json, created_at_unix_ms, updated_at_unix_ms
             ) VALUES (?1, ?2, ?3, ?4, 'leased', ?5, ?6, 1, ?7, NULL, NULL, ?8, ?8)",
            params![
                operation_key,
                request_hash,
                operation_kind,
                workspace_id.map(|value| value.to_string()),
                lease_id.to_string(),
                owner_id,
                expires,
                now,
            ],
        )?;
        let acquired =
            operation_tx(&transaction, operation_key)?.context("new operation disappeared")?;
        append_audit_tx(
            &transaction,
            "operation.lease.acquired",
            workspace_id,
            None,
            json!({
                "operationKey":operation_key,
                "operationKind":operation_kind,
                "leaseId":lease_id,
                "ownerId":owner_id,
                "attempt":1,
            }),
        )?;
        OperationLeaseOutcome::Acquired(acquired)
    };
    transaction.commit()?;
    Ok(outcome)
}

pub(crate) fn renew_operation(
    connection: &mut Connection,
    operation_key: &str,
    lease_id: Uuid,
    owner_id: &str,
    ttl_ms: u64,
) -> Result<OperationLease> {
    if ttl_ms == 0 || ttl_ms > 86_400_000 {
        bail!("operation lease ttl must be between 1 ms and 24 hours");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let now = unix_ms();
    let expires = now
        .checked_add(i64_from_u64(ttl_ms, "operation lease ttl")?)
        .context("operation lease expiration overflow")?;
    let changed = transaction.execute(
        "UPDATE operation_leases
         SET lease_expires_at_unix_ms = ?4, updated_at_unix_ms = ?5
         WHERE operation_key = ?1 AND lease_id = ?2 AND owner_id = ?3
           AND state = 'leased' AND lease_expires_at_unix_ms > ?5",
        params![operation_key, lease_id.to_string(), owner_id, expires, now],
    )?;
    if changed != 1 {
        bail!("operation lease cannot be renewed");
    }
    let record =
        operation_tx(&transaction, operation_key)?.context("renewed operation disappeared")?;
    transaction.commit()?;
    Ok(record)
}

pub(crate) fn complete_operation(
    connection: &mut Connection,
    operation_key: &str,
    lease_id: Uuid,
    owner_id: &str,
    result: &Value,
) -> Result<OperationLease> {
    transition_operation(
        connection,
        operation_key,
        lease_id,
        owner_id,
        "completed",
        Some(result),
        None,
    )
}

pub(crate) fn fail_operation(
    connection: &mut Connection,
    operation_key: &str,
    lease_id: Uuid,
    owner_id: &str,
    error: &Value,
) -> Result<OperationLease> {
    transition_operation(
        connection,
        operation_key,
        lease_id,
        owner_id,
        "failed",
        None,
        Some(error),
    )
}

pub(crate) fn cancel_operation(
    connection: &mut Connection,
    operation_key: &str,
    lease_id: Uuid,
    owner_id: &str,
    reason: &Value,
) -> Result<OperationLease> {
    transition_operation(
        connection,
        operation_key,
        lease_id,
        owner_id,
        "cancelled",
        None,
        Some(reason),
    )
}

fn transition_operation(
    connection: &mut Connection,
    operation_key: &str,
    lease_id: Uuid,
    owner_id: &str,
    state: &str,
    result: Option<&Value>,
    error: Option<&Value>,
) -> Result<OperationLease> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let existing =
        operation_tx(&transaction, operation_key)?.context("operation does not exist")?;
    let now = unix_ms();
    if existing.state != "leased"
        || existing.lease_id != Some(lease_id)
        || existing.owner_id.as_deref() != Some(owner_id)
        || existing
            .lease_expires_at_unix_ms
            .is_none_or(|value| value <= now)
    {
        bail!("operation lease ownership or expiration check failed");
    }
    transaction.execute(
        "UPDATE operation_leases
         SET state = ?4, lease_expires_at_unix_ms = NULL,
             result_json = ?5, error_json = ?6, updated_at_unix_ms = ?7
         WHERE operation_key = ?1 AND lease_id = ?2 AND owner_id = ?3 AND state = 'leased'",
        params![
            operation_key,
            lease_id.to_string(),
            owner_id,
            state,
            result.map(serde_json::to_string).transpose()?,
            error.map(serde_json::to_string).transpose()?,
            now,
        ],
    )?;
    append_audit_tx(
        &transaction,
        &format!("operation.{state}"),
        existing.workspace_id,
        None,
        json!({
            "operationKey":operation_key,
            "operationKind":existing.operation_kind,
            "leaseId":lease_id,
            "ownerId":owner_id,
            "attempt":existing.attempt,
        }),
    )?;
    let record =
        operation_tx(&transaction, operation_key)?.context("transitioned operation disappeared")?;
    transaction.commit()?;
    Ok(record)
}

pub(crate) fn recover_expired_operations(
    connection: &mut Connection,
    now: i64,
    limit: usize,
) -> Result<Vec<OperationLease>> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let keys = {
        let mut statement = transaction.prepare(
            "SELECT operation_key FROM operation_leases
             WHERE state = 'leased' AND lease_expires_at_unix_ms <= ?1
             ORDER BY lease_expires_at_unix_ms, operation_key LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![now, i64::try_from(limit).unwrap_or(i64::MAX)],
            |row| row.get::<_, String>(0),
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()?
    };
    let mut recovered = Vec::new();
    for key in keys {
        transaction.execute(
            "UPDATE operation_leases
             SET state = 'abandoned', lease_expires_at_unix_ms = NULL,
                 error_json = ?2, updated_at_unix_ms = ?3
             WHERE operation_key = ?1 AND state = 'leased'",
            params![
                key,
                serde_json::to_string(&json!({"code":"lease_expired"}))?,
                now,
            ],
        )?;
        let record =
            operation_tx(&transaction, &key)?.context("recovered operation disappeared")?;
        append_audit_tx(
            &transaction,
            "operation.abandoned",
            record.workspace_id,
            None,
            json!({
                "operationKey":record.operation_key,
                "operationKind":record.operation_kind,
                "attempt":record.attempt,
            }),
        )?;
        recovered.push(record);
    }
    transaction.commit()?;
    Ok(recovered)
}

pub(crate) fn operation(
    connection: &Connection,
    operation_key: &str,
) -> Result<Option<OperationLease>> {
    connection
        .query_row(
            &format!("{OPERATION_SELECT} WHERE operation_key = ?1"),
            [operation_key],
            operation_from_row,
        )
        .optional()
        .context("reading operation lease")
}

fn operation_tx(
    transaction: &Transaction<'_>,
    operation_key: &str,
) -> Result<Option<OperationLease>> {
    transaction
        .query_row(
            &format!("{OPERATION_SELECT} WHERE operation_key = ?1"),
            [operation_key],
            operation_from_row,
        )
        .optional()
        .context("reading operation lease")
}

pub(crate) fn all_operations(connection: &Connection) -> Result<Vec<OperationLease>> {
    let mut statement = connection.prepare(&format!(
        "{OPERATION_SELECT} ORDER BY created_at_unix_ms, operation_key"
    ))?;
    let rows = statement.query_map([], operation_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading all operation leases")
}

pub(crate) fn append_audit(
    connection: &mut Connection,
    event_type: &str,
    workspace_id: Option<Uuid>,
    entity_id: Option<Uuid>,
    payload: &Value,
) -> Result<AuditEntry> {
    require_non_empty(event_type, "audit event type")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let entry = append_audit_tx(
        &transaction,
        event_type,
        workspace_id,
        entity_id,
        payload.clone(),
    )?;
    transaction.commit()?;
    Ok(entry)
}

fn append_audit_tx(
    transaction: &Transaction<'_>,
    event_type: &str,
    workspace_id: Option<Uuid>,
    entity_id: Option<Uuid>,
    payload: Value,
) -> Result<AuditEntry> {
    let event_id = Uuid::now_v7();
    let payload_json = serde_json::to_string(&payload)?;
    let payload_hash = blake3::hash(payload_json.as_bytes()).to_hex().to_string();
    let previous_event_hash = transaction
        .query_row(
            "SELECT event_hash FROM state_audit ORDER BY sequence DESC LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    let created_at_unix_ms = unix_ms();
    let event_hash = audit_hash(
        previous_event_hash.as_deref(),
        event_id,
        event_type,
        workspace_id,
        entity_id,
        &payload_hash,
        created_at_unix_ms,
    );
    transaction.execute(
        "INSERT INTO state_audit(
            event_id, event_type, workspace_id, entity_id, payload_json, payload_hash,
            previous_event_hash, event_hash, created_at_unix_ms
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            event_id.to_string(),
            event_type,
            workspace_id.map(|value| value.to_string()),
            entity_id.map(|value| value.to_string()),
            payload_json,
            payload_hash,
            previous_event_hash,
            event_hash,
            created_at_unix_ms,
        ],
    )?;
    let sequence = transaction.last_insert_rowid();
    Ok(AuditEntry {
        sequence,
        event_id,
        event_type: event_type.to_string(),
        workspace_id,
        entity_id,
        payload,
        payload_hash,
        previous_event_hash,
        event_hash,
        created_at_unix_ms,
    })
}

pub(crate) fn audit_after(
    connection: &Connection,
    sequence: i64,
    limit: usize,
) -> Result<Vec<AuditEntry>> {
    let mut statement = connection.prepare(
        "SELECT sequence, event_id, event_type, workspace_id, entity_id, payload_json,
                payload_hash, previous_event_hash, event_hash, created_at_unix_ms
         FROM state_audit WHERE sequence > ?1 ORDER BY sequence LIMIT ?2",
    )?;
    let rows = statement.query_map(
        params![sequence, i64::try_from(limit).unwrap_or(i64::MAX)],
        audit_from_row,
    )?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading canonical audit")
}

pub(crate) fn all_audit(connection: &Connection) -> Result<Vec<AuditEntry>> {
    audit_after(connection, 0, usize::MAX)
}

pub(crate) fn verify_audit_chain(connection: &Connection) -> Result<bool> {
    let entries = all_audit(connection)?;
    let mut previous: Option<String> = None;
    for entry in entries {
        if entry.previous_event_hash != previous {
            return Ok(false);
        }
        let payload_json = serde_json::to_string(&entry.payload)?;
        if blake3::hash(payload_json.as_bytes()).to_hex().as_str() != entry.payload_hash {
            return Ok(false);
        }
        let expected = audit_hash(
            previous.as_deref(),
            entry.event_id,
            &entry.event_type,
            entry.workspace_id,
            entry.entity_id,
            &entry.payload_hash,
            entry.created_at_unix_ms,
        );
        if expected != entry.event_hash {
            return Ok(false);
        }
        previous = Some(entry.event_hash);
    }
    Ok(true)
}

pub(crate) fn integrity_report(connection: &Connection, path: &Path) -> Result<IntegrityReport> {
    let schema_version = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    let integrity: String = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    let foreign_key_violations = {
        let mut statement = connection.prepare("PRAGMA foreign_key_check")?;
        let mut rows = statement.query([])?;
        let mut count = 0_u64;
        while rows.next()?.is_some() {
            count = count.saturating_add(1);
        }
        count
    };
    Ok(IntegrityReport {
        schema_version,
        integrity,
        foreign_key_violations,
        audit_chain_valid: verify_audit_chain(connection)?,
        entity_count: table_count(connection, "canonical_entities")?,
        link_count: table_count(connection, "entity_links")?,
        operation_count: table_count(connection, "operation_leases")?,
        tombstone_count: table_count(connection, "tombstones")?,
        database_bytes: database_bytes(path),
    })
}

pub(crate) fn repair(connection: &mut Connection, path: &Path) -> Result<IntegrityReport> {
    connection.execute_batch(
        "REINDEX;
         PRAGMA optimize;
         PRAGMA wal_checkpoint(TRUNCATE);",
    )?;
    let report = integrity_report(connection, path)?;
    if report.integrity != "ok" || report.foreign_key_violations != 0 || !report.audit_chain_valid {
        bail!("canonical database repair could not establish integrity");
    }
    Ok(report)
}

pub(crate) fn backup(connection: &mut Connection, destination: &Path) -> Result<BackupManifest> {
    if destination.exists() {
        fs::remove_file(destination)
            .with_context(|| format!("removing existing backup {}", destination.display()))?;
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("creating backup directory {}", parent.display()))?;
    }
    connection.execute_batch("PRAGMA wal_checkpoint(FULL);")?;
    connection.execute(
        "VACUUM INTO ?1",
        [destination.to_string_lossy().to_string()],
    )?;
    let backup = open_reader(destination)?;
    let report = integrity_report(&backup, destination)?;
    if report.integrity != "ok" || report.foreign_key_violations != 0 || !report.audit_chain_valid {
        bail!("canonical backup failed integrity verification");
    }
    manifest(destination, report.schema_version)
}

pub(crate) fn restore_backup(source: &Path, destination: &Path) -> Result<BackupManifest> {
    let source_connection = open_reader(source)?;
    let source_report = integrity_report(&source_connection, source)?;
    if source_report.schema_version > SCHEMA_VERSION {
        bail!(
            "backup schema {} is newer than supported schema {SCHEMA_VERSION}",
            source_report.schema_version
        );
    }
    if source_report.integrity != "ok"
        || source_report.foreign_key_violations != 0
        || !source_report.audit_chain_valid
    {
        bail!("backup failed integrity verification before restore");
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("creating restore directory {}", parent.display()))?;
    }
    let nonce = Uuid::now_v7();
    let temporary = sibling(destination, &format!("restore-{nonce}.tmp"));
    let rollback = sibling(destination, &format!("pre-restore-{nonce}.bak"));
    fs::copy(source, &temporary).with_context(|| {
        format!(
            "copying canonical backup {} to {}",
            source.display(),
            temporary.display()
        )
    })?;
    remove_sqlite_sidecars(destination)?;
    let had_destination = destination.exists();
    if had_destination {
        fs::rename(destination, &rollback).with_context(|| {
            format!(
                "staging current canonical database {}",
                destination.display()
            )
        })?;
    }
    if let Err(error) = fs::rename(&temporary, destination) {
        if had_destination {
            let _ = fs::rename(&rollback, destination);
        }
        let _ = fs::remove_file(&temporary);
        return Err(error).context("installing restored canonical database");
    }
    let restored = open_reader(destination)?;
    let report = integrity_report(&restored, destination)?;
    if report.integrity != "ok" || report.foreign_key_violations != 0 || !report.audit_chain_valid {
        let _ = fs::remove_file(destination);
        if had_destination {
            let _ = fs::rename(&rollback, destination);
        }
        bail!("restored canonical database failed integrity verification");
    }
    if had_destination {
        fs::remove_file(&rollback).with_context(|| {
            format!("removing canonical restore rollback {}", rollback.display())
        })?;
    }
    manifest(destination, report.schema_version)
}

pub(crate) fn export_snapshot(connection: &Connection) -> Result<StateSnapshot> {
    Ok(StateSnapshot {
        schema_version: connection.query_row("PRAGMA user_version", [], |row| row.get(0))?,
        entities: list_all_entities(connection)?,
        links: all_links(connection)?,
        adapter_cursors: all_adapter_cursors(connection)?,
        retention_policies: retention_policies(connection)?,
        tombstones: all_tombstones(connection)?,
        operations: all_operations(connection)?,
        audit: all_audit(connection)?,
    })
}

fn list_all_entities(connection: &Connection) -> Result<Vec<SerializedEntityRecord>> {
    let mut statement = connection.prepare(&format!(
        "{ENTITY_SELECT} ORDER BY kind, created_at_unix_ms, id"
    ))?;
    let rows = statement.query_map([], entity_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading all canonical entities")
}

fn retention_policies_tx(transaction: &Transaction<'_>) -> Result<Vec<RetentionPolicyRecord>> {
    let mut statement = transaction.prepare(
        "SELECT id, workspace_id, entity_kind, retain_for_ms, tombstone_grace_ms,
                enabled, revision, metadata_json, created_at_unix_ms, updated_at_unix_ms
         FROM retention_policies ORDER BY workspace_key, kind_key",
    )?;
    let rows = statement.query_map([], retention_from_row)?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .context("reading retention policies")
}

fn ensure_live_entity(
    transaction: &Transaction<'_>,
    id: Uuid,
    label: &str,
) -> Result<SerializedEntityRecord> {
    let entity = transaction
        .query_row(
            &format!("{ENTITY_SELECT} WHERE id = ?1"),
            [id.to_string()],
            entity_from_row,
        )
        .optional()?
        .with_context(|| format!("canonical {label} entity does not exist"))?;
    if entity.tombstoned_at_unix_ms.is_some() {
        bail!("canonical {label} entity is tombstoned");
    }
    Ok(entity)
}

fn entity_from_row(row: &Row<'_>) -> rusqlite::Result<SerializedEntityRecord> {
    let id: String = row.get(0)?;
    let kind: String = row.get(1)?;
    let workspace_id: Option<String> = row.get(2)?;
    let parent_id: Option<String> = row.get(3)?;
    let sensitivity: String = row.get(7)?;
    let revision: i64 = row.get(8)?;
    let payload_json: String = row.get(9)?;
    Ok(SerializedEntityRecord {
        id: parse_uuid(0, &id)?,
        kind: parse_kind(1, &kind)?,
        workspace_id: parse_optional_uuid(2, workspace_id)?,
        parent_id: parse_optional_uuid(3, parent_id)?,
        origin_platform: row.get(4)?,
        native_id: row.get(5)?,
        state: row.get(6)?,
        sensitivity: parse_sensitivity(7, &sensitivity)?,
        revision: parse_u64(8, revision)?,
        payload: parse_json(9, &payload_json)?,
        payload_hash: row.get(10)?,
        idempotency_key: row.get(11)?,
        expires_at_unix_ms: row.get(12)?,
        created_at_unix_ms: row.get(13)?,
        updated_at_unix_ms: row.get(14)?,
        tombstoned_at_unix_ms: row.get(15)?,
    })
}

fn link_from_row(row: &Row<'_>) -> rusqlite::Result<EntityLinkRecord> {
    let source: String = row.get(0)?;
    let relationship: String = row.get(1)?;
    let target: String = row.get(2)?;
    let metadata: String = row.get(3)?;
    Ok(EntityLinkRecord {
        source_id: parse_uuid(0, &source)?,
        relationship: parse_relationship(1, &relationship)?,
        target_id: parse_uuid(2, &target)?,
        metadata: parse_json(3, &metadata)?,
        created_at_unix_ms: row.get(4)?,
        updated_at_unix_ms: row.get(5)?,
    })
}

fn cursor_from_row(row: &Row<'_>) -> rusqlite::Result<AdapterCursorRecord> {
    let revision: i64 = row.get(5)?;
    let metadata: String = row.get(6)?;
    Ok(AdapterCursorRecord {
        adapter: row.get(0)?,
        scope: row.get(1)?,
        cursor: row.get(2)?,
        etag: row.get(3)?,
        watermark: row.get(4)?,
        revision: parse_u64(5, revision)?,
        metadata: parse_json(6, &metadata)?,
        updated_at_unix_ms: row.get(7)?,
    })
}

fn retention_from_row(row: &Row<'_>) -> rusqlite::Result<RetentionPolicyRecord> {
    let id: String = row.get(0)?;
    let workspace_id: Option<String> = row.get(1)?;
    let entity_kind: Option<String> = row.get(2)?;
    let retain_for_ms: i64 = row.get(3)?;
    let tombstone_grace_ms: i64 = row.get(4)?;
    let revision: i64 = row.get(6)?;
    let metadata: String = row.get(7)?;
    Ok(RetentionPolicyRecord {
        id: parse_uuid(0, &id)?,
        workspace_id: parse_optional_uuid(1, workspace_id)?,
        entity_kind: entity_kind
            .as_deref()
            .map(|value| parse_kind(2, value))
            .transpose()?,
        retain_for_ms: parse_u64(3, retain_for_ms)?,
        tombstone_grace_ms: parse_u64(4, tombstone_grace_ms)?,
        enabled: row.get(5)?,
        revision: parse_u64(6, revision)?,
        metadata: parse_json(7, &metadata)?,
        created_at_unix_ms: row.get(8)?,
        updated_at_unix_ms: row.get(9)?,
    })
}

fn tombstone_from_row(row: &Row<'_>) -> rusqlite::Result<TombstoneRecord> {
    let entity_id: String = row.get(0)?;
    let kind: String = row.get(1)?;
    let workspace_id: Option<String> = row.get(2)?;
    Ok(TombstoneRecord {
        entity_id: parse_uuid(0, &entity_id)?,
        entity_kind: parse_kind(1, &kind)?,
        workspace_id: parse_optional_uuid(2, workspace_id)?,
        payload_hash: row.get(3)?,
        reason: row.get(4)?,
        actor: row.get(5)?,
        tombstoned_at_unix_ms: row.get(6)?,
        purged_at_unix_ms: row.get(7)?,
    })
}

fn operation_from_row(row: &Row<'_>) -> rusqlite::Result<OperationLease> {
    let workspace_id: Option<String> = row.get(3)?;
    let lease_id: Option<String> = row.get(5)?;
    let attempt: i64 = row.get(7)?;
    let result: Option<String> = row.get(9)?;
    let error: Option<String> = row.get(10)?;
    Ok(OperationLease {
        operation_key: row.get(0)?,
        request_hash: row.get(1)?,
        operation_kind: row.get(2)?,
        workspace_id: parse_optional_uuid(3, workspace_id)?,
        state: row.get(4)?,
        lease_id: parse_optional_uuid(5, lease_id)?,
        owner_id: row.get(6)?,
        attempt: parse_u64(7, attempt)?,
        lease_expires_at_unix_ms: row.get(8)?,
        result: result
            .as_deref()
            .map(|value| parse_json(9, value))
            .transpose()?,
        error: error
            .as_deref()
            .map(|value| parse_json(10, value))
            .transpose()?,
        created_at_unix_ms: row.get(11)?,
        updated_at_unix_ms: row.get(12)?,
    })
}

fn audit_from_row(row: &Row<'_>) -> rusqlite::Result<AuditEntry> {
    let event_id: String = row.get(1)?;
    let workspace_id: Option<String> = row.get(3)?;
    let entity_id: Option<String> = row.get(4)?;
    let payload: String = row.get(5)?;
    Ok(AuditEntry {
        sequence: row.get(0)?,
        event_id: parse_uuid(1, &event_id)?,
        event_type: row.get(2)?,
        workspace_id: parse_optional_uuid(3, workspace_id)?,
        entity_id: parse_optional_uuid(4, entity_id)?,
        payload: parse_json(5, &payload)?,
        payload_hash: row.get(6)?,
        previous_event_hash: row.get(7)?,
        event_hash: row.get(8)?,
        created_at_unix_ms: row.get(9)?,
    })
}

fn audit_hash(
    previous_event_hash: Option<&str>,
    event_id: Uuid,
    event_type: &str,
    workspace_id: Option<Uuid>,
    entity_id: Option<Uuid>,
    payload_hash: &str,
    created_at_unix_ms: i64,
) -> String {
    let mut hasher = blake3::Hasher::new();
    hash_field(&mut hasher, previous_event_hash.unwrap_or("").as_bytes());
    hash_field(&mut hasher, event_id.as_bytes());
    hash_field(&mut hasher, event_type.as_bytes());
    hash_field(
        &mut hasher,
        workspace_id
            .as_ref()
            .map(|value| value.as_bytes().as_slice())
            .unwrap_or(&[]),
    );
    hash_field(
        &mut hasher,
        entity_id
            .as_ref()
            .map(|value| value.as_bytes().as_slice())
            .unwrap_or(&[]),
    );
    hash_field(&mut hasher, payload_hash.as_bytes());
    hash_field(&mut hasher, &created_at_unix_ms.to_le_bytes());
    hasher.finalize().to_hex().to_string()
}

fn hash_field(hasher: &mut blake3::Hasher, value: &[u8]) {
    hasher.update(&(value.len() as u64).to_le_bytes());
    hasher.update(value);
}

fn table_count(connection: &Connection, table: &str) -> Result<u64> {
    let sql = format!("SELECT COUNT(*) FROM {table}");
    let value: i64 = connection.query_row(&sql, [], |row| row.get(0))?;
    u64::try_from(value).with_context(|| format!("negative row count returned for {table}"))
}

fn manifest(path: &Path, schema_version: i64) -> Result<BackupManifest> {
    let bytes = fs::read(path).with_context(|| format!("reading {}", path.display()))?;
    Ok(BackupManifest {
        schema_version,
        path: path.to_string_lossy().to_string(),
        byte_length: u64::try_from(bytes.len()).unwrap_or(u64::MAX),
        blake3: blake3::hash(&bytes).to_hex().to_string(),
        created_at_unix_ms: unix_ms(),
    })
}

fn database_bytes(path: &Path) -> u64 {
    [
        path.to_path_buf(),
        sidecar(path, "wal"),
        sidecar(path, "shm"),
    ]
    .iter()
    .filter_map(|candidate| fs::metadata(candidate).ok())
    .map(|metadata| metadata.len())
    .sum()
}

fn remove_sqlite_sidecars(path: &Path) -> Result<()> {
    for candidate in [sidecar(path, "wal"), sidecar(path, "shm")] {
        if candidate.exists() {
            fs::remove_file(&candidate)
                .with_context(|| format!("removing SQLite sidecar {}", candidate.display()))?;
        }
    }
    Ok(())
}

fn sidecar(path: &Path, suffix: &str) -> PathBuf {
    PathBuf::from(format!("{}-{suffix}", path.to_string_lossy()))
}

fn sibling(path: &Path, suffix: &str) -> PathBuf {
    let name = path
        .file_name()
        .map(|value| value.to_string_lossy())
        .unwrap_or_default();
    path.with_file_name(format!("{name}.{suffix}"))
}

fn workspace_key(workspace_id: Option<Uuid>) -> String {
    workspace_id
        .map(|value| value.to_string())
        .unwrap_or_default()
}

fn require_non_empty(value: &str, label: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("{label} must be non-empty");
    }
    Ok(())
}

fn unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

fn i64_from_u64(value: u64, label: &str) -> Result<i64> {
    i64::try_from(value).with_context(|| format!("{label} exceeds SQLite INTEGER"))
}

fn parse_u64(index: usize, value: i64) -> rusqlite::Result<u64> {
    u64::try_from(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Integer, Box::new(error))
    })
}

fn parse_uuid(index: usize, value: &str) -> rusqlite::Result<Uuid> {
    Uuid::parse_str(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(error))
    })
}

fn parse_optional_uuid(index: usize, value: Option<String>) -> rusqlite::Result<Option<Uuid>> {
    value
        .as_deref()
        .map(|value| parse_uuid(index, value))
        .transpose()
}

fn invalid_text_value(index: usize, error: anyhow::Error) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        index,
        Type::Text,
        Box::new(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            error.to_string(),
        )),
    )
}

fn parse_kind(index: usize, value: &str) -> rusqlite::Result<EntityKind> {
    EntityKind::parse(value).map_err(|error| invalid_text_value(index, error))
}

fn parse_sensitivity(index: usize, value: &str) -> rusqlite::Result<Sensitivity> {
    Sensitivity::parse(value).map_err(|error| invalid_text_value(index, error))
}

fn parse_relationship(index: usize, value: &str) -> rusqlite::Result<RelationshipKind> {
    RelationshipKind::parse(value).map_err(|error| invalid_text_value(index, error))
}

fn parse_json(index: usize, value: &str) -> rusqlite::Result<Value> {
    serde_json::from_str(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(index, Type::Text, Box::new(error))
    })
}
