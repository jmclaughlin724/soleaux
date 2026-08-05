use crate::{database, model::*};
use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, mpsc},
    thread,
};
use uuid::Uuid;

pub const SCHEMA_VERSION: i64 = database::SCHEMA_VERSION;

#[derive(Clone, Debug)]
pub struct StateStore {
    path: Arc<PathBuf>,
    writer: mpsc::Sender<WriteCommand>,
}

enum WriteCommand {
    PutEntity {
        input: database::SerializedEntityInput,
        reply: mpsc::SyncSender<std::result::Result<SerializedEntityRecord, String>>,
    },
    UpsertNativeEntity {
        input: database::SerializedEntityInput,
        reply: mpsc::SyncSender<std::result::Result<SerializedEntityRecord, String>>,
    },
    PutLink {
        input: EntityLinkInput,
        reply: mpsc::SyncSender<std::result::Result<EntityLinkRecord, String>>,
    },
    PutAdapterCursor {
        input: AdapterCursorInput,
        reply: mpsc::SyncSender<std::result::Result<AdapterCursorRecord, String>>,
    },
    PutRetentionPolicy {
        input: RetentionPolicyInput,
        reply: mpsc::SyncSender<std::result::Result<RetentionPolicyRecord, String>>,
    },
    TombstoneEntity {
        entity_id: Uuid,
        reason: String,
        actor: String,
        reply: mpsc::SyncSender<std::result::Result<TombstoneRecord, String>>,
    },
    ApplyRetention {
        now_unix_ms: i64,
        limit: usize,
        reply: mpsc::SyncSender<std::result::Result<Vec<TombstoneRecord>, String>>,
    },
    PurgeTombstones {
        before_unix_ms: i64,
        limit: usize,
        reply: mpsc::SyncSender<std::result::Result<usize, String>>,
    },
    AcquireOperation {
        operation_key: String,
        request_hash: String,
        operation_kind: String,
        workspace_id: Option<Uuid>,
        owner_id: String,
        ttl_ms: u64,
        reply: mpsc::SyncSender<std::result::Result<OperationLeaseOutcome, String>>,
    },
    RenewOperation {
        operation_key: String,
        lease_id: Uuid,
        owner_id: String,
        ttl_ms: u64,
        reply: mpsc::SyncSender<std::result::Result<OperationLease, String>>,
    },
    CompleteOperation {
        operation_key: String,
        lease_id: Uuid,
        owner_id: String,
        result: Value,
        reply: mpsc::SyncSender<std::result::Result<OperationLease, String>>,
    },
    FailOperation {
        operation_key: String,
        lease_id: Uuid,
        owner_id: String,
        error: Value,
        reply: mpsc::SyncSender<std::result::Result<OperationLease, String>>,
    },
    CancelOperation {
        operation_key: String,
        lease_id: Uuid,
        owner_id: String,
        reason: Value,
        reply: mpsc::SyncSender<std::result::Result<OperationLease, String>>,
    },
    RecoverExpiredOperations {
        now_unix_ms: i64,
        limit: usize,
        reply: mpsc::SyncSender<std::result::Result<Vec<OperationLease>, String>>,
    },
    AppendAudit {
        event_type: String,
        workspace_id: Option<Uuid>,
        entity_id: Option<Uuid>,
        payload: Value,
        reply: mpsc::SyncSender<std::result::Result<AuditEntry, String>>,
    },
    Backup {
        destination: PathBuf,
        reply: mpsc::SyncSender<std::result::Result<BackupManifest, String>>,
    },
    Repair {
        reply: mpsc::SyncSender<std::result::Result<IntegrityReport, String>>,
    },
    Shutdown,
}

impl StateStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).with_context(|| {
                format!("creating canonical state directory {}", parent.display())
            })?;
        }
        let mut connection = database::open_writer(&path)?;
        database::migrate(&mut connection)?;
        let (sender, receiver) = mpsc::channel();
        let writer_path = path.clone();
        thread::Builder::new()
            .name("soleaux-canonical-state-writer".to_string())
            .spawn(move || writer_loop(connection, receiver, writer_path))
            .context("starting canonical state writer")?;
        Ok(Self {
            path: Arc::new(path),
            writer: sender,
        })
    }

    pub fn path(&self) -> &Path {
        self.path.as_ref()
    }

    pub fn put<T: CanonicalPayload>(
        &self,
        input: CanonicalEntityInput<T>,
    ) -> Result<CanonicalRecord<T>> {
        let serialized = serialize_entity_input(input)?;
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::PutEntity {
                input: serialized,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        let record = receive(receiver, "canonical entity")?;
        typed_record(record)
    }

    pub fn upsert_native<T: CanonicalPayload>(
        &self,
        input: CanonicalEntityInput<T>,
    ) -> Result<CanonicalRecord<T>> {
        let serialized = serialize_entity_input(input)?;
        if serialized.origin_platform.is_none() || serialized.native_id.is_none() {
            bail!("native upsert requires origin platform and native id");
        }
        if serialized.expected_revision.is_some() {
            bail!("native upsert does not accept expected_revision");
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::UpsertNativeEntity {
                input: serialized,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        let record = receive(receiver, "native canonical entity")?;
        typed_record(record)
    }

    pub fn get<T: CanonicalPayload>(&self, id: Uuid) -> Result<Option<CanonicalRecord<T>>> {
        self.get_serialized(id)?.map(typed_record).transpose()
    }

    pub fn get_serialized(&self, id: Uuid) -> Result<Option<SerializedEntityRecord>> {
        let connection = self.reader()?;
        database::get_entity(&connection, id)
    }

    pub fn get_by_native<T: CanonicalPayload>(
        &self,
        origin_platform: &str,
        native_id: &str,
    ) -> Result<Option<CanonicalRecord<T>>> {
        if origin_platform.trim().is_empty() || native_id.trim().is_empty() {
            bail!("native identity values must be non-empty");
        }
        let connection = self.reader()?;
        database::get_entity_by_native(&connection, T::KIND, origin_platform, native_id)?
            .map(typed_record)
            .transpose()
    }

    pub fn list<T: CanonicalPayload>(
        &self,
        workspace_id: Option<Uuid>,
        limit: usize,
        include_tombstoned: bool,
    ) -> Result<Vec<CanonicalRecord<T>>> {
        self.list_serialized(Some(T::KIND), workspace_id, limit, include_tombstoned)?
            .into_iter()
            .map(typed_record)
            .collect()
    }

    pub fn list_serialized(
        &self,
        kind: Option<EntityKind>,
        workspace_id: Option<Uuid>,
        limit: usize,
        include_tombstoned: bool,
    ) -> Result<Vec<SerializedEntityRecord>> {
        let connection = self.reader()?;
        database::list_entities(&connection, kind, workspace_id, limit, include_tombstoned)
    }

    pub fn list_all<T: CanonicalPayload>(
        &self,
        limit: usize,
        include_tombstoned: bool,
    ) -> Result<Vec<CanonicalRecord<T>>> {
        let connection = self.reader()?;
        database::list_entities_all(&connection, T::KIND, limit, include_tombstoned)?
            .into_iter()
            .map(typed_record)
            .collect()
    }

    pub fn link(&self, input: EntityLinkInput) -> Result<EntityLinkRecord> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::PutLink {
                input,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "canonical relationship")
    }

    pub fn links_from(&self, source_id: Uuid) -> Result<Vec<EntityLinkRecord>> {
        let connection = self.reader()?;
        database::links_from(&connection, source_id)
    }

    pub fn put_adapter_cursor(&self, input: AdapterCursorInput) -> Result<AdapterCursorRecord> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::PutAdapterCursor {
                input,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "adapter cursor")
    }

    pub fn adapter_cursor(
        &self,
        adapter: &str,
        scope: &str,
    ) -> Result<Option<AdapterCursorRecord>> {
        let connection = self.reader()?;
        database::adapter_cursor(&connection, adapter, scope)
    }

    pub fn put_retention_policy(
        &self,
        input: RetentionPolicyInput,
    ) -> Result<RetentionPolicyRecord> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::PutRetentionPolicy {
                input,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "retention policy")
    }

    pub fn retention_policies(&self) -> Result<Vec<RetentionPolicyRecord>> {
        let connection = self.reader()?;
        database::retention_policies(&connection)
    }

    pub fn tombstone(
        &self,
        entity_id: Uuid,
        reason: impl Into<String>,
        actor: impl Into<String>,
    ) -> Result<TombstoneRecord> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::TombstoneEntity {
                entity_id,
                reason: reason.into(),
                actor: actor.into(),
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "canonical tombstone")
    }

    pub fn apply_retention(&self, now_unix_ms: i64, limit: usize) -> Result<Vec<TombstoneRecord>> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::ApplyRetention {
                now_unix_ms,
                limit,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "retention application")
    }

    pub fn purge_tombstones(&self, before_unix_ms: i64, limit: usize) -> Result<usize> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::PurgeTombstones {
                before_unix_ms,
                limit,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "tombstone purge")
    }

    #[allow(clippy::too_many_arguments)]
    pub fn acquire_operation(
        &self,
        operation_key: impl Into<String>,
        request_hash: impl Into<String>,
        operation_kind: impl Into<String>,
        workspace_id: Option<Uuid>,
        owner_id: impl Into<String>,
        ttl_ms: u64,
    ) -> Result<OperationLeaseOutcome> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::AcquireOperation {
                operation_key: operation_key.into(),
                request_hash: request_hash.into(),
                operation_kind: operation_kind.into(),
                workspace_id,
                owner_id: owner_id.into(),
                ttl_ms,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "operation lease acquisition")
    }

    pub fn renew_operation(
        &self,
        operation_key: impl Into<String>,
        lease_id: Uuid,
        owner_id: impl Into<String>,
        ttl_ms: u64,
    ) -> Result<OperationLease> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::RenewOperation {
                operation_key: operation_key.into(),
                lease_id,
                owner_id: owner_id.into(),
                ttl_ms,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "operation lease renewal")
    }

    pub fn complete_operation(
        &self,
        operation_key: impl Into<String>,
        lease_id: Uuid,
        owner_id: impl Into<String>,
        result: Value,
    ) -> Result<OperationLease> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::CompleteOperation {
                operation_key: operation_key.into(),
                lease_id,
                owner_id: owner_id.into(),
                result,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "operation completion")
    }

    pub fn fail_operation(
        &self,
        operation_key: impl Into<String>,
        lease_id: Uuid,
        owner_id: impl Into<String>,
        error: Value,
    ) -> Result<OperationLease> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::FailOperation {
                operation_key: operation_key.into(),
                lease_id,
                owner_id: owner_id.into(),
                error,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "operation failure")
    }

    pub fn cancel_operation(
        &self,
        operation_key: impl Into<String>,
        lease_id: Uuid,
        owner_id: impl Into<String>,
        reason: Value,
    ) -> Result<OperationLease> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::CancelOperation {
                operation_key: operation_key.into(),
                lease_id,
                owner_id: owner_id.into(),
                reason,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "operation cancellation")
    }

    pub fn recover_expired_operations(
        &self,
        now_unix_ms: i64,
        limit: usize,
    ) -> Result<Vec<OperationLease>> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::RecoverExpiredOperations {
                now_unix_ms,
                limit,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "expired operation recovery")
    }

    pub fn operation(&self, operation_key: &str) -> Result<Option<OperationLease>> {
        let connection = self.reader()?;
        database::operation(&connection, operation_key)
    }

    pub fn append_audit(
        &self,
        event_type: impl Into<String>,
        workspace_id: Option<Uuid>,
        entity_id: Option<Uuid>,
        payload: Value,
    ) -> Result<AuditEntry> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::AppendAudit {
                event_type: event_type.into(),
                workspace_id,
                entity_id,
                payload,
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "canonical audit event")
    }

    pub fn audit_after(&self, sequence: i64, limit: usize) -> Result<Vec<AuditEntry>> {
        let connection = self.reader()?;
        database::audit_after(&connection, sequence, limit)
    }

    pub fn verify_audit_chain(&self) -> Result<bool> {
        let connection = self.reader()?;
        database::verify_audit_chain(&connection)
    }

    pub fn integrity_report(&self) -> Result<IntegrityReport> {
        let connection = self.reader()?;
        database::integrity_report(&connection, self.path())
    }

    pub fn repair(&self) -> Result<IntegrityReport> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::Repair { reply: sender })
            .context("canonical state writer stopped")?;
        receive(receiver, "canonical repair")
    }

    pub fn backup_to(&self, destination: impl AsRef<Path>) -> Result<BackupManifest> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::Backup {
                destination: destination.as_ref().to_path_buf(),
                reply: sender,
            })
            .context("canonical state writer stopped")?;
        receive(receiver, "canonical backup")
    }

    pub fn restore_backup(
        source: impl AsRef<Path>,
        destination: impl AsRef<Path>,
    ) -> Result<BackupManifest> {
        database::restore_backup(source.as_ref(), destination.as_ref())
    }

    pub fn export_snapshot(&self) -> Result<StateSnapshot> {
        let connection = self.reader()?;
        database::export_snapshot(&connection)
    }

    fn reader(&self) -> Result<rusqlite::Connection> {
        database::open_reader(self.path())
    }
}

impl Drop for StateStore {
    fn drop(&mut self) {
        if Arc::strong_count(&self.path) == 1 {
            let _ = self.writer.send(WriteCommand::Shutdown);
        }
    }
}

fn writer_loop(
    mut connection: rusqlite::Connection,
    receiver: mpsc::Receiver<WriteCommand>,
    path: PathBuf,
) {
    while let Ok(command) = receiver.recv() {
        match command {
            WriteCommand::PutEntity { input, reply } => {
                respond(reply, database::put_entity(&mut connection, &input));
            }
            WriteCommand::UpsertNativeEntity { input, reply } => {
                respond(
                    reply,
                    database::upsert_native_entity(&mut connection, &input),
                );
            }
            WriteCommand::PutLink { input, reply } => {
                respond(reply, database::put_link(&mut connection, &input));
            }
            WriteCommand::PutAdapterCursor { input, reply } => {
                respond(reply, database::put_adapter_cursor(&mut connection, &input));
            }
            WriteCommand::PutRetentionPolicy { input, reply } => {
                respond(
                    reply,
                    database::put_retention_policy(&mut connection, &input),
                );
            }
            WriteCommand::TombstoneEntity {
                entity_id,
                reason,
                actor,
                reply,
            } => {
                respond(
                    reply,
                    database::tombstone_entity(&mut connection, entity_id, &reason, &actor),
                );
            }
            WriteCommand::ApplyRetention {
                now_unix_ms,
                limit,
                reply,
            } => {
                respond(
                    reply,
                    database::apply_retention(&mut connection, now_unix_ms, limit),
                );
            }
            WriteCommand::PurgeTombstones {
                before_unix_ms,
                limit,
                reply,
            } => {
                respond(
                    reply,
                    database::purge_tombstones(&mut connection, before_unix_ms, limit),
                );
            }
            WriteCommand::AcquireOperation {
                operation_key,
                request_hash,
                operation_kind,
                workspace_id,
                owner_id,
                ttl_ms,
                reply,
            } => {
                respond(
                    reply,
                    database::acquire_operation(
                        &mut connection,
                        &operation_key,
                        &request_hash,
                        &operation_kind,
                        workspace_id,
                        &owner_id,
                        ttl_ms,
                    ),
                );
            }
            WriteCommand::RenewOperation {
                operation_key,
                lease_id,
                owner_id,
                ttl_ms,
                reply,
            } => {
                respond(
                    reply,
                    database::renew_operation(
                        &mut connection,
                        &operation_key,
                        lease_id,
                        &owner_id,
                        ttl_ms,
                    ),
                );
            }
            WriteCommand::CompleteOperation {
                operation_key,
                lease_id,
                owner_id,
                result,
                reply,
            } => {
                respond(
                    reply,
                    database::complete_operation(
                        &mut connection,
                        &operation_key,
                        lease_id,
                        &owner_id,
                        &result,
                    ),
                );
            }
            WriteCommand::FailOperation {
                operation_key,
                lease_id,
                owner_id,
                error,
                reply,
            } => {
                respond(
                    reply,
                    database::fail_operation(
                        &mut connection,
                        &operation_key,
                        lease_id,
                        &owner_id,
                        &error,
                    ),
                );
            }
            WriteCommand::CancelOperation {
                operation_key,
                lease_id,
                owner_id,
                reason,
                reply,
            } => {
                respond(
                    reply,
                    database::cancel_operation(
                        &mut connection,
                        &operation_key,
                        lease_id,
                        &owner_id,
                        &reason,
                    ),
                );
            }
            WriteCommand::RecoverExpiredOperations {
                now_unix_ms,
                limit,
                reply,
            } => {
                respond(
                    reply,
                    database::recover_expired_operations(&mut connection, now_unix_ms, limit),
                );
            }
            WriteCommand::AppendAudit {
                event_type,
                workspace_id,
                entity_id,
                payload,
                reply,
            } => {
                respond(
                    reply,
                    database::append_audit(
                        &mut connection,
                        &event_type,
                        workspace_id,
                        entity_id,
                        &payload,
                    ),
                );
            }
            WriteCommand::Backup { destination, reply } => {
                respond(reply, database::backup(&mut connection, &destination));
            }
            WriteCommand::Repair { reply } => {
                respond(reply, database::repair(&mut connection, &path));
            }
            WriteCommand::Shutdown => break,
        }
    }
}

fn serialize_entity_input<T: CanonicalPayload>(
    input: CanonicalEntityInput<T>,
) -> Result<database::SerializedEntityInput> {
    input.payload.validate()?;
    if input.state.trim().is_empty() {
        bail!("canonical entity state must be non-empty");
    }
    if input
        .idempotency_key
        .as_deref()
        .is_some_and(|value| value.trim().is_empty())
    {
        bail!("canonical idempotency key must be non-empty when supplied");
    }
    if input.origin_platform.is_some() != input.native_id.is_some() {
        bail!("origin platform and native id must be supplied together");
    }
    let payload = serde_json::to_value(&input.payload)?;
    let encoded = serde_json::to_vec(&payload)?;
    Ok(database::SerializedEntityInput {
        id: input.id,
        kind: T::KIND,
        workspace_id: input.workspace_id,
        parent_id: input.parent_id,
        origin_platform: input.origin_platform,
        native_id: input.native_id,
        state: input.state,
        sensitivity: input.sensitivity,
        idempotency_key: input.idempotency_key,
        expected_revision: input.expected_revision,
        expires_at_unix_ms: input.expires_at_unix_ms,
        payload,
        payload_hash: blake3::hash(&encoded).to_hex().to_string(),
    })
}

fn respond<T>(reply: mpsc::SyncSender<std::result::Result<T, String>>, result: Result<T>) {
    let _ = reply.send(result.map_err(|error| format!("{error:#}")));
}

fn receive<T>(receiver: mpsc::Receiver<std::result::Result<T, String>>, label: &str) -> Result<T> {
    receiver
        .recv()
        .with_context(|| format!("canonical state writer dropped {label} reply"))?
        .map_err(anyhow::Error::msg)
}

fn typed_record<T: CanonicalPayload>(record: SerializedEntityRecord) -> Result<CanonicalRecord<T>> {
    if record.kind != T::KIND {
        bail!(
            "canonical entity kind mismatch: requested {}, stored {}",
            T::KIND.as_str(),
            record.kind.as_str()
        );
    }
    Ok(CanonicalRecord {
        id: record.id,
        kind: record.kind,
        workspace_id: record.workspace_id,
        parent_id: record.parent_id,
        origin_platform: record.origin_platform,
        native_id: record.native_id,
        state: record.state,
        sensitivity: record.sensitivity,
        revision: record.revision,
        payload_hash: record.payload_hash,
        idempotency_key: record.idempotency_key,
        expires_at_unix_ms: record.expires_at_unix_ms,
        created_at_unix_ms: record.created_at_unix_ms,
        updated_at_unix_ms: record.updated_at_unix_ms,
        tombstoned_at_unix_ms: record.tombstoned_at_unix_ms,
        payload: serde_json::from_value(record.payload)?,
    })
}
