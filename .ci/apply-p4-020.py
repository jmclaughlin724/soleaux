#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / "native/daemon/storage/src/lib.rs"
EDITOR = ROOT / "native/daemon/mcp/src/editor.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


storage = STORAGE.read_text(encoding="utf-8")
storage = replace_once(
    storage,
    "use rusqlite::{Connection, OpenFlags, OptionalExtension, params};\n",
    "use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};\n",
    "transaction behavior import",
)
storage = replace_once(
    storage,
    "const SCHEMA_VERSION: i64 = 1;\n",
    "const SCHEMA_VERSION: i64 = 2;\n",
    "schema version",
)
storage = replace_once(
    storage,
    '''#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StoreStats {
''',
    '''#[derive(Debug, Clone, PartialEq)]
pub enum OperationReservationOutcome {
    Acquired,
    InFlight,
    Replayed(Value),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StoreStats {
''',
    "operation reservation outcome",
)
storage = replace_once(
    storage,
    '''    AppendEvent {
        event_id: Uuid,
        event_type: String,
        workspace_id: Option<Uuid>,
        payload: Value,
        reply: mpsc::SyncSender<std::result::Result<EventRecord, String>>,
    },
    Shutdown,
''',
    '''    AppendEvent {
        event_id: Uuid,
        event_type: String,
        workspace_id: Option<Uuid>,
        payload: Value,
        reply: mpsc::SyncSender<std::result::Result<EventRecord, String>>,
    },
    ReserveOperation {
        operation_key: String,
        request_hash: String,
        operation_kind: String,
        workspace_id: Option<Uuid>,
        reply: mpsc::SyncSender<
            std::result::Result<OperationReservationOutcome, String>,
        >,
    },
    CommitOperation {
        operation_key: String,
        request_hash: String,
        result: Value,
        reply: mpsc::SyncSender<std::result::Result<(), String>>,
    },
    ReleaseOperation {
        operation_key: String,
        request_hash: String,
        reply: mpsc::SyncSender<std::result::Result<(), String>>,
    },
    Shutdown,
''',
    "operation write commands",
)
storage = replace_once(
    storage,
    '''    pub fn workspace(&self, id: Uuid) -> Result<Option<WorkspaceRecord>> {
''',
    '''    pub fn reserve_operation(
        &self,
        operation_key: impl Into<String>,
        request_hash: impl Into<String>,
        operation_kind: impl Into<String>,
        workspace_id: Option<Uuid>,
    ) -> Result<OperationReservationOutcome> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::ReserveOperation {
                operation_key: operation_key.into(),
                request_hash: request_hash.into(),
                operation_kind: operation_kind.into(),
                workspace_id,
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped operation reservation reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn commit_operation(
        &self,
        operation_key: impl Into<String>,
        request_hash: impl Into<String>,
        result: Value,
    ) -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::CommitOperation {
                operation_key: operation_key.into(),
                request_hash: request_hash.into(),
                result,
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped operation commit reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn release_operation(
        &self,
        operation_key: impl Into<String>,
        request_hash: impl Into<String>,
    ) -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::ReleaseOperation {
                operation_key: operation_key.into(),
                request_hash: request_hash.into(),
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped operation release reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn workspace(&self, id: Uuid) -> Result<Option<WorkspaceRecord>> {
''',
    "store operation methods",
)
storage = replace_once(
    storage,
    '''            WriteCommand::AppendEvent {
                event_id,
                event_type,
                workspace_id,
                payload,
                reply,
            } => {
                let result = write_event(
                    &mut connection,
                    event_id,
                    &event_type,
                    workspace_id,
                    &payload,
                )
                .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::Shutdown => break,
''',
    '''            WriteCommand::AppendEvent {
                event_id,
                event_type,
                workspace_id,
                payload,
                reply,
            } => {
                let result = write_event(
                    &mut connection,
                    event_id,
                    &event_type,
                    workspace_id,
                    &payload,
                )
                .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::ReserveOperation {
                operation_key,
                request_hash,
                operation_kind,
                workspace_id,
                reply,
            } => {
                let result = write_reserve_operation(
                    &mut connection,
                    &operation_key,
                    &request_hash,
                    &operation_kind,
                    workspace_id,
                )
                .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::CommitOperation {
                operation_key,
                request_hash,
                result,
                reply,
            } => {
                let result = write_commit_operation(
                    &mut connection,
                    &operation_key,
                    &request_hash,
                    &result,
                )
                .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::ReleaseOperation {
                operation_key,
                request_hash,
                reply,
            } => {
                let result = write_release_operation(
                    &mut connection,
                    &operation_key,
                    &request_hash,
                )
                .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::Shutdown => break,
''',
    "operation writer dispatch",
)

migrate_start = storage.index("fn migrate(connection: &mut Connection) -> Result<()> {")
migrate_end = storage.index("\nfn write_workspace", migrate_start)
new_migrate = r'''fn migrate(connection: &mut Connection) -> Result<()> {
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if version > SCHEMA_VERSION {
        bail!("Soleaux database schema {version} is newer than supported schema {SCHEMA_VERSION}");
    }
    if version == 0 {
        connection.execute_batch(
            r#"
            BEGIN IMMEDIATE;
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                root TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL
            );
            CREATE TABLE indexed_files (
                workspace_id TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                language TEXT NOT NULL,
                byte_length INTEGER NOT NULL,
                engine TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                indexed_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (workspace_id, path),
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
            );
            CREATE TABLE symbols (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                start_byte INTEGER NOT NULL,
                end_byte INTEGER NOT NULL,
                start_row INTEGER NOT NULL,
                end_row INTEGER NOT NULL,
                FOREIGN KEY (workspace_id, path) REFERENCES indexed_files(workspace_id, path) ON DELETE CASCADE
            );
            CREATE INDEX symbols_workspace_path ON symbols(workspace_id, path, start_byte);
            CREATE VIRTUAL TABLE symbols_fts USING fts5(
                name,
                path,
                kind,
                content='symbols',
                content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
                INSERT INTO symbols_fts(rowid, name, path, kind) VALUES (new.rowid, new.name, new.path, new.kind);
            END;
            CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
                INSERT INTO symbols_fts(symbols_fts, rowid, name, path, kind) VALUES ('delete', old.rowid, old.name, old.path, old.kind);
            END;
            CREATE TRIGGER symbols_au AFTER UPDATE ON symbols BEGIN
                INSERT INTO symbols_fts(symbols_fts, rowid, name, path, kind) VALUES ('delete', old.rowid, old.name, old.path, old.kind);
                INSERT INTO symbols_fts(rowid, name, path, kind) VALUES (new.rowid, new.name, new.path, new.kind);
            END;
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                workspace_id TEXT,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                previous_event_hash TEXT,
                event_hash TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );
            CREATE TABLE operation_reservations (
                operation_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                operation_kind TEXT NOT NULL,
                workspace_id TEXT,
                state TEXT NOT NULL CHECK(state IN ('reserved', 'committed')),
                result_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL
            );
            CREATE INDEX operation_reservations_state
                ON operation_reservations(state, updated_at_unix_ms);
            PRAGMA user_version = 2;
            COMMIT;
            "#,
        )?;
        return Ok(());
    }
    if version == 1 {
        connection.execute_batch(
            r#"
            BEGIN IMMEDIATE;
            CREATE TABLE operation_reservations (
                operation_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                operation_kind TEXT NOT NULL,
                workspace_id TEXT,
                state TEXT NOT NULL CHECK(state IN ('reserved', 'committed')),
                result_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL
            );
            CREATE INDEX operation_reservations_state
                ON operation_reservations(state, updated_at_unix_ms);
            PRAGMA user_version = 2;
            COMMIT;
            "#,
        )?;
    }
    Ok(())
}

fn write_reserve_operation(
    connection: &mut Connection,
    operation_key: &str,
    request_hash: &str,
    operation_kind: &str,
    workspace_id: Option<Uuid>,
) -> Result<OperationReservationOutcome> {
    if operation_key.trim().is_empty() || request_hash.trim().is_empty() {
        bail!("operation key and request hash must be non-empty");
    }
    let transaction =
        connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let existing = transaction
        .query_row(
            "SELECT request_hash, state, result_json
             FROM operation_reservations WHERE operation_key = ?1",
            [operation_key],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                ))
            },
        )
        .optional()?;
    let outcome = match existing {
        None => {
            let now = unix_ms();
            transaction.execute(
                "INSERT INTO operation_reservations(
                    operation_key, request_hash, operation_kind, workspace_id,
                    state, result_json, created_at_unix_ms, updated_at_unix_ms
                 ) VALUES (?1, ?2, ?3, ?4, 'reserved', NULL, ?5, ?5)",
                params![
                    operation_key,
                    request_hash,
                    operation_kind,
                    workspace_id.map(|value| value.to_string()),
                    now,
                ],
            )?;
            OperationReservationOutcome::Acquired
        }
        Some((existing_hash, state, result_json)) => {
            if existing_hash != request_hash {
                bail!("operation key collision: request hash differs from the reserved operation");
            }
            match state.as_str() {
                "reserved" => OperationReservationOutcome::InFlight,
                "committed" => {
                    let encoded = result_json
                        .context("committed operation omitted its result payload")?;
                    OperationReservationOutcome::Replayed(
                        serde_json::from_str(&encoded)
                            .context("decoding committed operation result")?,
                    )
                }
                other => bail!("unsupported operation reservation state: {other}"),
            }
        }
    };
    transaction.commit()?;
    Ok(outcome)
}

fn write_commit_operation(
    connection: &mut Connection,
    operation_key: &str,
    request_hash: &str,
    result: &Value,
) -> Result<()> {
    let transaction =
        connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let result_json = serde_json::to_string(result)?;
    let changed = transaction.execute(
        "UPDATE operation_reservations
         SET state = 'committed', result_json = ?3, updated_at_unix_ms = ?4
         WHERE operation_key = ?1 AND request_hash = ?2 AND state = 'reserved'",
        params![operation_key, request_hash, result_json, unix_ms()],
    )?;
    if changed != 1 {
        bail!("operation reservation could not be committed from the reserved state");
    }
    transaction.commit()?;
    Ok(())
}

fn write_release_operation(
    connection: &mut Connection,
    operation_key: &str,
    request_hash: &str,
) -> Result<()> {
    let transaction =
        connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute(
        "DELETE FROM operation_reservations
         WHERE operation_key = ?1 AND request_hash = ?2 AND state = 'reserved'",
        params![operation_key, request_hash],
    )?;
    transaction.commit()?;
    Ok(())
}
'''
storage = storage[:migrate_start] + new_migrate + storage[migrate_end:]
storage = replace_once(
    storage,
    '''    #[test]
    fn wal_store_indexes_symbols_and_hash_chains_events() {
''',
    '''    #[test]
    fn operation_reservations_are_atomic_replayable_and_releasable() {
        use std::sync::Barrier;

        let directory = tempdir().expect("tempdir");
        let store = Store::open(directory.path().join("soleaux.db")).expect("open store");
        let workspace_id = Uuid::now_v7();
        let key = "edit:workspace:preview";
        let request_hash = "request-hash";

        assert_eq!(
            store
                .reserve_operation(key, request_hash, "editor.apply", Some(workspace_id))
                .expect("acquire"),
            OperationReservationOutcome::Acquired
        );
        assert_eq!(
            store
                .reserve_operation(key, request_hash, "editor.apply", Some(workspace_id))
                .expect("in flight"),
            OperationReservationOutcome::InFlight
        );
        assert!(
            store
                .reserve_operation(key, "different", "editor.apply", Some(workspace_id))
                .is_err()
        );

        let result = serde_json::json!({"receipt_id":"receipt-1","applied":true});
        store
            .commit_operation(key, request_hash, result.clone())
            .expect("commit");
        assert_eq!(
            store
                .reserve_operation(key, request_hash, "editor.apply", Some(workspace_id))
                .expect("replay"),
            OperationReservationOutcome::Replayed(result)
        );

        let retry_key = "edit:workspace:retry";
        assert_eq!(
            store
                .reserve_operation(
                    retry_key,
                    request_hash,
                    "editor.apply",
                    Some(workspace_id),
                )
                .expect("retry acquire"),
            OperationReservationOutcome::Acquired
        );
        store
            .release_operation(retry_key, request_hash)
            .expect("release");
        assert_eq!(
            store
                .reserve_operation(
                    retry_key,
                    request_hash,
                    "editor.apply",
                    Some(workspace_id),
                )
                .expect("reacquire"),
            OperationReservationOutcome::Acquired
        );

        let concurrent_key = "edit:workspace:concurrent";
        let barrier = Arc::new(Barrier::new(8));
        let handles = (0..8)
            .map(|_| {
                let store = store.clone();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    store
                        .reserve_operation(
                            concurrent_key,
                            request_hash,
                            "editor.apply",
                            Some(workspace_id),
                        )
                        .expect("concurrent reserve")
                })
            })
            .collect::<Vec<_>>();
        let outcomes = handles
            .into_iter()
            .map(|handle| handle.join().expect("reservation thread"))
            .collect::<Vec<_>>();
        assert_eq!(
            outcomes
                .iter()
                .filter(|outcome| matches!(outcome, OperationReservationOutcome::Acquired))
                .count(),
            1
        );
        assert_eq!(
            outcomes
                .iter()
                .filter(|outcome| matches!(outcome, OperationReservationOutcome::InFlight))
                .count(),
            7
        );
    }

    #[test]
    fn wal_store_indexes_symbols_and_hash_chains_events() {
''',
    "operation reservation regression",
)
STORAGE.write_text(storage, encoding="utf-8")

editor = EDITOR.read_text(encoding="utf-8")
editor = replace_once(
    editor,
    "use soleaux_intelligence::index::RepositoryIndex;\n",
    "use soleaux_intelligence::index::RepositoryIndex;\nuse soleaux_storage::{OperationReservationOutcome, Store};\n",
    "editor storage imports",
)
editor = replace_once(
    editor,
    '''#[derive(Clone)]
pub struct EditorService {
    index: RepositoryIndex,
    preview_dir: PathBuf,
    process_epoch: String,
    fail_after_write: Arc<AtomicBool>,
}

impl EditorService {
''',
    '''#[derive(Clone)]
pub struct EditorService {
    index: RepositoryIndex,
    preview_dir: PathBuf,
    process_epoch: String,
    fail_after_write: Arc<AtomicBool>,
}

struct OperationReservationGuard {
    store: Store,
    operation_key: String,
    request_hash: String,
    committed: bool,
}

impl OperationReservationGuard {
    fn new(store: Store, operation_key: String, request_hash: String) -> Self {
        Self {
            store,
            operation_key,
            request_hash,
            committed: false,
        }
    }

    fn mark_committed(&mut self) {
        self.committed = true;
    }
}

impl Drop for OperationReservationGuard {
    fn drop(&mut self) {
        if !self.committed {
            let _ = self.store.release_operation(
                self.operation_key.clone(),
                self.request_hash.clone(),
            );
        }
    }
}

impl EditorService {
''',
    "operation reservation guard",
)
editor = replace_once(
    editor,
    '''        if !confirm {
            bail!("edit requires confirm=true");
        }
        let mut preview = self.load(preview_id)?;
''',
    '''        if !confirm {
            bail!("edit requires confirm=true");
        }
        let workspace_id = self.index.workspace_id();
        let operation_key = format!("edit:{workspace_id}:{preview_id}");
        let request_hash = sha256_hex(
            format!("editor.apply\n{workspace_id}\n{preview_id}\n{digest}\n{confirm}")
                .as_bytes(),
        );
        let mut reservation = match self.index.store().reserve_operation(
            operation_key.clone(),
            request_hash.clone(),
            "editor.apply",
            Some(workspace_id),
        )? {
            OperationReservationOutcome::Acquired => OperationReservationGuard::new(
                self.index.store().clone(),
                operation_key.clone(),
                request_hash.clone(),
            ),
            OperationReservationOutcome::InFlight => {
                bail!("edit operation is already in progress")
            }
            OperationReservationOutcome::Replayed(result) => return Ok(result),
        };
        let mut preview = self.load(preview_id)?;
''',
    "reserve before edit validation",
)
post_start = editor.index("        let post_write = async {")
post_end = editor.index("\n    async fn rollback_after_failure", post_start)
new_post = r'''        let post_write = async {
            if self.fail_after_write.swap(false, Ordering::SeqCst) {
                bail!("injected editor post-write failure");
            }
            let report = self.index.refresh().await?;
            preview.consumed = true;
            preview.writes_performed = true;
            self.persist(&preview)?;
            let event = self.index.store().append_event(
                "editor.preview_applied",
                Some(self.index.workspace_id()),
                json!({
                    "receipt_id":receipt_id,
                    "preview_id":preview.preview_id,
                    "digest":preview.digest,
                    "files":&files,
                    "reindexed":true,
                    "index_report":&report,
                }),
            )?;
            let result = json!({
                "receipt_id":receipt_id,
                "preview_id":preview.preview_id,
                "applied":true,
                "files":files,
                "formatter":null,
                "diagnostics":[],
                "reindexed":true,
                "audit_event_hash":event.event_hash,
                "operation_key":operation_key,
                "replayed":false,
            });
            self.index.store().commit_operation(
                operation_key.clone(),
                request_hash.clone(),
                result.clone(),
            )?;
            Ok::<_, anyhow::Error>(result)
        }
        .await;

        let result = match post_write {
            Ok(value) => value,
            Err(failure) => {
                return Err(self
                    .rollback_after_failure(&mut preview, &receipt_id, &originals, &files, failure)
                    .await);
            }
        };
        reservation.mark_committed();
        Ok(result)
    }
'''
editor = editor[:post_start] + new_post + editor[post_end:]
editor = replace_once(
    editor,
    '''        assert!(
            editor
                .apply(&preview.preview_id, &preview.digest, true)
                .await
                .is_err()
        );
''',
    '''        let replay = editor
            .apply(&preview.preview_id, &preview.digest, true)
            .await
            .expect("idempotent replay");
        assert_eq!(replay, result);
        let applied_events = editor
            .index
            .store()
            .events_after(0, 100)
            .expect("events")
            .into_iter()
            .filter(|event| event.event_type == "editor.preview_applied")
            .count();
        assert_eq!(applied_events, 1);
''',
    "edit replay regression",
)
editor = replace_once(
    editor,
    '''        assert!(receipt["rollback_errors"].as_array().is_some_and(Vec::is_empty));
    }

    #[test]
''',
    '''        assert!(receipt["rollback_errors"].as_array().is_some_and(Vec::is_empty));

        let retry = editor
            .apply(&preview.preview_id, &preview.digest, true)
            .await
            .expect("retry after released reservation");
        assert_eq!(retry["applied"], true);
        assert_eq!(
            fs::read_to_string(&source_path).expect("read after retry"),
            "export const value = 2;\n"
        );
    }

    #[test]
''',
    "rollback releases reservation regression",
)
EDITOR.write_text(editor, encoding="utf-8")
