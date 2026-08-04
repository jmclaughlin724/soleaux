//! Durable local state for the native Soleaux wedge.
//!
//! SQLite is opened in WAL mode. Exactly one writer thread owns the write
//! connection; readers open short-lived read-only connections. This keeps the
//! first production slice simple while preserving the single-writer contract.

use anyhow::{Context, Result, bail};
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, mpsc},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

const SCHEMA_VERSION: i64 = 2;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceRecord {
    pub id: Uuid,
    pub root: String,
    pub display_name: String,
    pub identity_hash: String,
    pub created_at_unix_ms: i64,
    pub updated_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct IndexedFileRecord {
    pub workspace_id: Uuid,
    pub path: String,
    pub content_hash: String,
    pub language: String,
    pub byte_length: u64,
    pub engine: String,
    pub engine_version: String,
    pub indexed_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SymbolRecord {
    pub name: String,
    pub kind: String,
    pub start_byte: u64,
    pub end_byte: u64,
    pub start_row: u64,
    pub end_row: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SymbolHit {
    pub workspace_id: Uuid,
    pub path: String,
    pub name: String,
    pub kind: String,
    pub start_byte: u64,
    pub end_byte: u64,
    pub start_row: u64,
    pub end_row: u64,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct EventRecord {
    pub sequence: i64,
    pub event_id: Uuid,
    pub event_type: String,
    pub workspace_id: Option<Uuid>,
    pub payload: Value,
    pub payload_hash: String,
    pub previous_event_hash: Option<String>,
    pub event_hash: String,
    pub created_at_unix_ms: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OperationReservationOutcome {
    Acquired,
    InFlight,
    Replayed(Value),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct StoreStats {
    pub schema_version: i64,
    pub workspace_count: u64,
    pub file_count: u64,
    pub symbol_count: u64,
    pub event_count: u64,
    pub database_bytes: u64,
}

#[derive(Clone)]
pub struct Store {
    path: Arc<PathBuf>,
    writer: mpsc::Sender<WriteCommand>,
}

enum WriteCommand {
    UpsertWorkspace {
        record: WorkspaceRecord,
        reply: mpsc::SyncSender<std::result::Result<(), String>>,
    },
    ReplaceFile {
        file: IndexedFileRecord,
        symbols: Vec<SymbolRecord>,
        reply: mpsc::SyncSender<std::result::Result<(), String>>,
    },
    RemoveFile {
        workspace_id: Uuid,
        path: String,
        reply: mpsc::SyncSender<std::result::Result<(), String>>,
    },
    AppendEvent {
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
        reply: mpsc::SyncSender<std::result::Result<OperationReservationOutcome, String>>,
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
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("creating storage directory {}", parent.display()))?;
        }
        let mut connection = open_writer(&path)?;
        migrate(&mut connection)?;
        let (sender, receiver) = mpsc::channel::<WriteCommand>();
        let writer_path = path.clone();
        thread::Builder::new()
            .name("soleaux-sqlite-writer".to_string())
            .spawn(move || writer_loop(connection, receiver))
            .with_context(|| format!("starting SQLite writer for {}", writer_path.display()))?;
        Ok(Self {
            path: Arc::new(path),
            writer: sender,
        })
    }

    pub fn path(&self) -> &Path {
        self.path.as_ref()
    }

    pub fn upsert_workspace(&self, record: WorkspaceRecord) -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::UpsertWorkspace {
                record,
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped workspace reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn replace_file(&self, file: IndexedFileRecord, symbols: Vec<SymbolRecord>) -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::ReplaceFile {
                file,
                symbols,
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped file reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn remove_file(&self, workspace_id: Uuid, path: impl Into<String>) -> Result<()> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::RemoveFile {
                workspace_id,
                path: path.into(),
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped remove reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn append_event(
        &self,
        event_type: impl Into<String>,
        workspace_id: Option<Uuid>,
        payload: Value,
    ) -> Result<EventRecord> {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.writer
            .send(WriteCommand::AppendEvent {
                event_id: Uuid::now_v7(),
                event_type: event_type.into(),
                workspace_id,
                payload,
                reply: sender,
            })
            .context("SQLite writer stopped")?;
        receiver
            .recv()
            .context("SQLite writer dropped event reply")?
            .map_err(anyhow::Error::msg)
    }

    pub fn reserve_operation(
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
        let connection = self.reader()?;
        connection
            .query_row(
                "SELECT id, root, display_name, identity_hash, created_at_unix_ms, updated_at_unix_ms FROM workspaces WHERE id = ?1",
                [id.to_string()],
                workspace_from_row,
            )
            .optional()
            .context("reading workspace")
    }

    pub fn file(&self, workspace_id: Uuid, path: &str) -> Result<Option<IndexedFileRecord>> {
        let connection = self.reader()?;
        connection
            .query_row(
                "SELECT workspace_id, path, content_hash, language, byte_length, engine, engine_version, indexed_at_unix_ms FROM indexed_files WHERE workspace_id = ?1 AND path = ?2",
                params![workspace_id.to_string(), path],
                file_from_row,
            )
            .optional()
            .context("reading indexed file")
    }

    pub fn files(&self, workspace_id: Uuid, limit: usize) -> Result<Vec<IndexedFileRecord>> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT workspace_id, path, content_hash, language, byte_length, engine, engine_version, indexed_at_unix_ms
             FROM indexed_files WHERE workspace_id = ?1 ORDER BY path LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![
                workspace_id.to_string(),
                i64::try_from(limit).unwrap_or(i64::MAX)
            ],
            file_from_row,
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("listing indexed files")
    }

    pub fn languages(&self, workspace_id: Uuid) -> Result<Vec<String>> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT DISTINCT language FROM indexed_files WHERE workspace_id = ?1 ORDER BY language",
        )?;
        let rows = statement.query_map([workspace_id.to_string()], |row| row.get(0))?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("listing indexed languages")
    }

    pub fn symbols_for_file(&self, workspace_id: Uuid, path: &str) -> Result<Vec<SymbolRecord>> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT name, kind, start_byte, end_byte, start_row, end_row
             FROM symbols WHERE workspace_id = ?1 AND path = ?2 ORDER BY start_byte",
        )?;
        let rows = statement.query_map(params![workspace_id.to_string(), path], |row| {
            Ok(SymbolRecord {
                name: row.get(0)?,
                kind: row.get(1)?,
                start_byte: read_u64(row, 2)?,
                end_byte: read_u64(row, 3)?,
                start_row: read_u64(row, 4)?,
                end_row: read_u64(row, 5)?,
            })
        })?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("reading file symbols")
    }

    pub fn search_symbols(
        &self,
        workspace_id: Uuid,
        query: &str,
        limit: usize,
    ) -> Result<Vec<SymbolHit>> {
        let trimmed = query.trim();
        if trimmed.is_empty() {
            return Ok(Vec::new());
        }
        let connection = self.reader()?;
        let expression = fts_expression(trimmed);
        if expression.is_empty() {
            return Ok(Vec::new());
        }
        let mut statement = connection.prepare(
            "SELECT s.workspace_id, s.path, s.name, s.kind, s.start_byte, s.end_byte, s.start_row, s.end_row,
                    bm25(symbols_fts) AS rank
             FROM symbols_fts
             JOIN symbols s ON s.rowid = symbols_fts.rowid
             WHERE symbols_fts MATCH ?1 AND s.workspace_id = ?2
             ORDER BY rank LIMIT ?3",
        )?;
        let rows = statement.query_map(
            params![
                expression,
                workspace_id.to_string(),
                i64::try_from(limit).unwrap_or(i64::MAX)
            ],
            |row| {
                let workspace: String = row.get(0)?;
                Ok(SymbolHit {
                    workspace_id: Uuid::parse_str(&workspace).map_err(|error| {
                        rusqlite::Error::FromSqlConversionFailure(
                            0,
                            rusqlite::types::Type::Text,
                            Box::new(error),
                        )
                    })?,
                    path: row.get(1)?,
                    name: row.get(2)?,
                    kind: row.get(3)?,
                    start_byte: read_u64(row, 4)?,
                    end_byte: read_u64(row, 5)?,
                    start_row: read_u64(row, 6)?,
                    end_row: read_u64(row, 7)?,
                    score: -row.get::<_, f64>(8)?,
                })
            },
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("searching symbols")
    }

    pub fn events_after(&self, sequence: i64, limit: usize) -> Result<Vec<EventRecord>> {
        let connection = self.reader()?;
        let mut statement = connection.prepare(
            "SELECT sequence, event_id, event_type, workspace_id, payload_json, payload_hash,
                    previous_event_hash, event_hash, created_at_unix_ms
             FROM events WHERE sequence > ?1 ORDER BY sequence LIMIT ?2",
        )?;
        let rows = statement.query_map(
            params![sequence, i64::try_from(limit).unwrap_or(i64::MAX)],
            event_from_row,
        )?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("reading events")
    }

    pub fn stats(&self) -> Result<StoreStats> {
        let connection = self.reader()?;
        let count = |table: &str| -> Result<u64> {
            let sql = format!("SELECT COUNT(*) FROM {table}");
            let value: i64 = connection
                .query_row(&sql, [], |row| row.get(0))
                .with_context(|| format!("counting {table}"))?;
            u64::try_from(value).with_context(|| format!("negative count returned for {table}"))
        };
        let schema_version = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        let database_bytes = fs::metadata(self.path())
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        Ok(StoreStats {
            schema_version,
            workspace_count: count("workspaces")?,
            file_count: count("indexed_files")?,
            symbol_count: count("symbols")?,
            event_count: count("events")?,
            database_bytes,
        })
    }

    fn reader(&self) -> Result<Connection> {
        let connection = Connection::open_with_flags(
            self.path(),
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .with_context(|| format!("opening read connection {}", self.path().display()))?;
        connection.busy_timeout(Duration::from_secs(5))?;
        Ok(connection)
    }
}

impl Drop for Store {
    fn drop(&mut self) {
        if Arc::strong_count(&self.path) == 1 {
            let _ = self.writer.send(WriteCommand::Shutdown);
        }
    }
}

fn writer_loop(mut connection: Connection, receiver: mpsc::Receiver<WriteCommand>) {
    while let Ok(command) = receiver.recv() {
        match command {
            WriteCommand::UpsertWorkspace { record, reply } => {
                let result =
                    write_workspace(&mut connection, &record).map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::ReplaceFile {
                file,
                symbols,
                reply,
            } => {
                let result =
                    write_file(&mut connection, &file, &symbols).map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::RemoveFile {
                workspace_id,
                path,
                reply,
            } => {
                let result = remove_file(&mut connection, workspace_id, &path)
                    .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::AppendEvent {
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
                let result =
                    write_commit_operation(&mut connection, &operation_key, &request_hash, &result)
                        .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::ReleaseOperation {
                operation_key,
                request_hash,
                reply,
            } => {
                let result =
                    write_release_operation(&mut connection, &operation_key, &request_hash)
                        .map_err(|error| error.to_string());
                let _ = reply.send(result);
            }
            WriteCommand::Shutdown => break,
        }
    }
}

fn open_writer(path: &Path) -> Result<Connection> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_FULL_MUTEX,
    )
    .with_context(|| format!("opening SQLite writer {}", path.display()))?;
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "NORMAL")?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.pragma_update(None, "wal_autocheckpoint", 1_000_i64)?;
    Ok(connection)
}

fn migrate(connection: &mut Connection) -> Result<()> {
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
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
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
                    let encoded =
                        result_json.context("committed operation omitted its result payload")?;
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
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
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
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute(
        "DELETE FROM operation_reservations
         WHERE operation_key = ?1 AND request_hash = ?2 AND state = 'reserved'",
        params![operation_key, request_hash],
    )?;
    transaction.commit()?;
    Ok(())
}

fn write_workspace(connection: &mut Connection, record: &WorkspaceRecord) -> Result<()> {
    connection.execute(
        "INSERT INTO workspaces(id, root, display_name, identity_hash, created_at_unix_ms, updated_at_unix_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)
         ON CONFLICT(id) DO UPDATE SET root=excluded.root, display_name=excluded.display_name,
             identity_hash=excluded.identity_hash, updated_at_unix_ms=excluded.updated_at_unix_ms",
        params![
            record.id.to_string(),
            record.root,
            record.display_name,
            record.identity_hash,
            record.created_at_unix_ms,
            record.updated_at_unix_ms,
        ],
    )?;
    Ok(())
}

fn write_file(
    connection: &mut Connection,
    file: &IndexedFileRecord,
    symbols: &[SymbolRecord],
) -> Result<()> {
    let transaction = connection.transaction()?;
    let byte_length = sql_i64(file.byte_length, "indexed_files.byte_length")?;
    transaction.execute(
        "INSERT INTO indexed_files(workspace_id, path, content_hash, language, byte_length, engine, engine_version, indexed_at_unix_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
         ON CONFLICT(workspace_id, path) DO UPDATE SET content_hash=excluded.content_hash,
             language=excluded.language, byte_length=excluded.byte_length, engine=excluded.engine,
             engine_version=excluded.engine_version, indexed_at_unix_ms=excluded.indexed_at_unix_ms",
        params![
            file.workspace_id.to_string(),
            file.path,
            file.content_hash,
            file.language,
            byte_length,
            file.engine,
            file.engine_version,
            file.indexed_at_unix_ms,
        ],
    )?;
    transaction.execute(
        "DELETE FROM symbols WHERE workspace_id = ?1 AND path = ?2",
        params![file.workspace_id.to_string(), file.path],
    )?;
    {
        let mut statement = transaction.prepare(
            "INSERT INTO symbols(workspace_id, path, name, kind, start_byte, end_byte, start_row, end_row)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        )?;
        for symbol in symbols {
            let start_byte = sql_i64(symbol.start_byte, "symbols.start_byte")?;
            let end_byte = sql_i64(symbol.end_byte, "symbols.end_byte")?;
            let start_row = sql_i64(symbol.start_row, "symbols.start_row")?;
            let end_row = sql_i64(symbol.end_row, "symbols.end_row")?;
            statement.execute(params![
                file.workspace_id.to_string(),
                file.path,
                symbol.name,
                symbol.kind,
                start_byte,
                end_byte,
                start_row,
                end_row,
            ])?;
        }
    }
    transaction.commit()?;
    Ok(())
}

fn remove_file(connection: &mut Connection, workspace_id: Uuid, path: &str) -> Result<()> {
    connection.execute(
        "DELETE FROM indexed_files WHERE workspace_id = ?1 AND path = ?2",
        params![workspace_id.to_string(), path],
    )?;
    Ok(())
}

fn write_event(
    connection: &mut Connection,
    event_id: Uuid,
    event_type: &str,
    workspace_id: Option<Uuid>,
    payload: &Value,
) -> Result<EventRecord> {
    let transaction = connection.transaction()?;
    let previous: Option<String> = transaction
        .query_row(
            "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let payload_json = serde_json::to_string(payload)?;
    let payload_hash = blake3::hash(payload_json.as_bytes()).to_hex().to_string();
    let created_at = unix_ms();
    let event_hash = event_digest(
        event_id,
        event_type,
        workspace_id,
        &payload_hash,
        previous.as_deref(),
        created_at,
    );
    transaction.execute(
        "INSERT INTO events(event_id, event_type, workspace_id, payload_json, payload_hash, previous_event_hash, event_hash, created_at_unix_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            event_id.to_string(),
            event_type,
            workspace_id.map(|id| id.to_string()),
            payload_json,
            payload_hash,
            previous,
            event_hash,
            created_at,
        ],
    )?;
    let sequence = transaction.last_insert_rowid();
    transaction.commit()?;
    Ok(EventRecord {
        sequence,
        event_id,
        event_type: event_type.to_string(),
        workspace_id,
        payload: payload.clone(),
        payload_hash,
        previous_event_hash: previous,
        event_hash,
        created_at_unix_ms: created_at,
    })
}

fn event_digest(
    event_id: Uuid,
    event_type: &str,
    workspace_id: Option<Uuid>,
    payload_hash: &str,
    previous_event_hash: Option<&str>,
    created_at_unix_ms: i64,
) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}\n{}\n{}",
        event_id,
        event_type,
        workspace_id.map(|id| id.to_string()).unwrap_or_default(),
        payload_hash,
        previous_event_hash.unwrap_or_default(),
        created_at_unix_ms,
    );
    blake3::hash(canonical.as_bytes()).to_hex().to_string()
}

fn fts_expression(query: &str) -> String {
    query
        .split(|character: char| {
            !character.is_alphanumeric() && character != '_' && character != '-'
        })
        .filter(|term| !term.is_empty())
        .map(|term| format!("\"{}\"*", term.replace('"', "\"\"")))
        .collect::<Vec<_>>()
        .join(" OR ")
}

fn workspace_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<WorkspaceRecord> {
    let id: String = row.get(0)?;
    Ok(WorkspaceRecord {
        id: parse_uuid_column(0, &id)?,
        root: row.get(1)?,
        display_name: row.get(2)?,
        identity_hash: row.get(3)?,
        created_at_unix_ms: row.get(4)?,
        updated_at_unix_ms: row.get(5)?,
    })
}

fn file_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<IndexedFileRecord> {
    let workspace: String = row.get(0)?;
    Ok(IndexedFileRecord {
        workspace_id: parse_uuid_column(0, &workspace)?,
        path: row.get(1)?,
        content_hash: row.get(2)?,
        language: row.get(3)?,
        byte_length: read_u64(row, 4)?,
        engine: row.get(5)?,
        engine_version: row.get(6)?,
        indexed_at_unix_ms: row.get(7)?,
    })
}

fn event_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<EventRecord> {
    let event_id: String = row.get(1)?;
    let workspace: Option<String> = row.get(3)?;
    let payload_json: String = row.get(4)?;
    Ok(EventRecord {
        sequence: row.get(0)?,
        event_id: parse_uuid_column(1, &event_id)?,
        event_type: row.get(2)?,
        workspace_id: workspace
            .as_deref()
            .map(|value| parse_uuid_column(3, value))
            .transpose()?,
        payload: serde_json::from_str(&payload_json).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                4,
                rusqlite::types::Type::Text,
                Box::new(error),
            )
        })?,
        payload_hash: row.get(5)?,
        previous_event_hash: row.get(6)?,
        event_hash: row.get(7)?,
        created_at_unix_ms: row.get(8)?,
    })
}

fn read_u64(row: &rusqlite::Row<'_>, column: usize) -> rusqlite::Result<u64> {
    let value: i64 = row.get(column)?;
    u64::try_from(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            column,
            rusqlite::types::Type::Integer,
            Box::new(error),
        )
    })
}

fn sql_i64(value: u64, field: &str) -> Result<i64> {
    i64::try_from(value).with_context(|| format!("{field} exceeds SQLite INTEGER range"))
}

fn parse_uuid_column(column: usize, value: &str) -> rusqlite::Result<Uuid> {
    Uuid::parse_str(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            column,
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

pub fn unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
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
                .reserve_operation(retry_key, request_hash, "editor.apply", Some(workspace_id),)
                .expect("retry acquire"),
            OperationReservationOutcome::Acquired
        );
        store
            .release_operation(retry_key, request_hash)
            .expect("release");
        assert_eq!(
            store
                .reserve_operation(retry_key, request_hash, "editor.apply", Some(workspace_id),)
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
        let directory = tempdir().expect("tempdir");
        let store = Store::open(directory.path().join("soleaux.db")).expect("open store");
        let workspace_id = Uuid::now_v7();
        store
            .upsert_workspace(WorkspaceRecord {
                id: workspace_id,
                root: directory.path().to_string_lossy().to_string(),
                display_name: "fixture".to_string(),
                identity_hash: "abc".to_string(),
                created_at_unix_ms: unix_ms(),
                updated_at_unix_ms: unix_ms(),
            })
            .expect("workspace");
        store
            .replace_file(
                IndexedFileRecord {
                    workspace_id,
                    path: "src/index.ts".to_string(),
                    content_hash: "hash".to_string(),
                    language: "typescript".to_string(),
                    byte_length: 64,
                    engine: "oxc".to_string(),
                    engine_version: "0.142.0".to_string(),
                    indexed_at_unix_ms: unix_ms(),
                },
                vec![SymbolRecord {
                    name: "compileContext".to_string(),
                    kind: "function_declaration".to_string(),
                    start_byte: 0,
                    end_byte: 42,
                    start_row: 0,
                    end_row: 2,
                }],
            )
            .expect("file");
        let hits = store
            .search_symbols(workspace_id, "compile", 10)
            .expect("search");
        assert_eq!(hits.len(), 1);
        assert!(
            store
                .search_symbols(workspace_id, "!!!", 10)
                .expect("punctuation search")
                .is_empty()
        );
        let first = store
            .append_event(
                "workspace.indexed",
                Some(workspace_id),
                serde_json::json!({"files":1}),
            )
            .expect("first event");
        let second = store
            .append_event(
                "context.compiled",
                Some(workspace_id),
                serde_json::json!({"tokens":128}),
            )
            .expect("second event");
        assert_eq!(
            second.previous_event_hash.as_deref(),
            Some(first.event_hash.as_str())
        );
        assert_eq!(store.stats().expect("stats").schema_version, SCHEMA_VERSION);
    }
}
