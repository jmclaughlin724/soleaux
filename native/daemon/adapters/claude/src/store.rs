//! Daemon-side `SessionStore` bridge over canonical state.
//!
//! The SDK's store contract (`code.claude.com/docs/en/agent-sdk/session-storage`)
//! is append/load over opaque JSON transcript entries keyed by
//! `{projectKey, sessionId, subpath?}`: entries round-trip deep-equal in
//! order, a retried batch may re-deliver entries so `append` deduplicates by
//! `entry.uuid`, `load` returns `null` for an unknown transcript, and the SDK
//! never deletes — retention stays host-owned. This bridge satisfies that
//! contract by writing through the daemon's canonical entities: one session
//! per `{projectKey, sessionId}` (native-identity upsert, adapter-idempotent),
//! one mirror turn per appended batch (race-free ordinals via the
//! `turn:{session}:{ordinal}` idempotency key, mirroring
//! `native/daemon/ipc/src/session.rs`), and one message per entry carrying
//! the verbatim entry JSON. `AdapterCursor` rows record the durably observed
//! position per transcript so a restarted host reconciles instead of
//! guessing.

use crate::CLAUDE_PLATFORM_ID;
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use soleaux_state::{
    AdapterCursorInput, AdapterCursorRecord, CanonicalEntityInput, CanonicalRecord, MessagePayload,
    REGISTRY_PAGE_LIMIT_MAX, SESSION_STATE_ACTIVE, SessionPayload, StateStore, TurnPayload,
    WorkspacePayload,
};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

/// `AdapterCursor` adapter key for this bridge.
pub const CLAUDE_CURSOR_ADAPTER: &str = CLAUDE_PLATFORM_ID;

/// Serialized size bound per transcript entry, mirroring the daemon's 1 MiB
/// IPC frame bound.
pub const MAX_ENTRY_BYTES: usize = 1024 * 1024;

const MAX_BATCH_ENTRIES: usize = 512;
const MAX_KEY_COMPONENT_CHARS: usize = 512;
const TURN_APPEND_MAX_ATTEMPTS: usize = 8;
const CURSOR_CONFLICT_RETRIES: usize = 3;
const RESUME_CHAIN_MAX_DEPTH: usize = 10_000;
const ENUMERATION_PAGE_LIMIT: usize = REGISTRY_PAGE_LIMIT_MAX;

/// Addresses one transcript, mirroring the SDK `SessionKey`: `subpath` is set
/// for subagent transcripts and sidecar files and is treated as an opaque key
/// suffix.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionKey {
    pub project_key: String,
    pub session_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subpath: Option<String>,
}

impl SessionKey {
    pub fn main(project_key: impl Into<String>, session_id: impl Into<String>) -> Self {
        Self {
            project_key: project_key.into(),
            session_id: session_id.into(),
            subpath: None,
        }
    }

    /// The canonical native identity for the session this key addresses.
    /// Key components exclude `/`, so the join cannot be ambiguous.
    pub fn native_session_id(&self) -> String {
        format!("{}/{}", self.project_key, self.session_id)
    }

    pub fn validate(&self) -> Result<()> {
        validate_key_component(&self.project_key, "project key")?;
        validate_key_component(&self.session_id, "session id")?;
        if let Some(subpath) = &self.subpath {
            if subpath.trim().is_empty() {
                bail!("session key subpath must be non-empty when present");
            }
            if subpath.chars().count() > MAX_KEY_COMPONENT_CHARS {
                bail!("session key subpath exceeds {MAX_KEY_COMPONENT_CHARS} characters");
            }
            if subpath.chars().any(char::is_control) {
                bail!("session key subpath must not contain control characters");
            }
        }
        Ok(())
    }
}

fn validate_key_component(value: &str, label: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("session key {label} must be non-empty");
    }
    if value.chars().count() > MAX_KEY_COMPONENT_CHARS {
        bail!("session key {label} exceeds {MAX_KEY_COMPONENT_CHARS} characters");
    }
    if value.contains('/') || value.chars().any(|character| character.is_control()) {
        bail!("session key {label} must not contain '/' or control characters");
    }
    Ok(())
}

/// `AdapterCursor` scope for one transcript key.
pub fn transcript_scope(key: &SessionKey) -> String {
    match &key.subpath {
        Some(subpath) => format!("transcript:{}#{subpath}", key.native_session_id()),
        None => format!("transcript:{}", key.native_session_id()),
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppendOutcome {
    pub appended: usize,
    pub deduplicated: usize,
    /// Ordinal of the mirror turn created for this batch; `None` when every
    /// entry was a re-delivered duplicate.
    pub turn_ordinal: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ForkOutcome {
    pub source_session_id: Uuid,
    pub fork_session_id: Uuid,
    pub fork_native_session_id: String,
    pub entry_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StoredSessionSummary {
    pub session_id: String,
    pub mtime_unix_ms: i64,
    pub canonical_id: Uuid,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReconcileEntry {
    pub scope: String,
    pub entry_count: u64,
    pub cursor_watermark_before: Option<u64>,
    pub repaired: bool,
}

/// The daemon-side store bridge for one workspace.
#[derive(Debug, Clone)]
pub struct ClaudeSessionStore {
    state: StateStore,
    workspace_id: Uuid,
}

impl ClaudeSessionStore {
    /// Bind the bridge to a registered, live workspace; a missing or
    /// tombstoned workspace refuses construction.
    pub fn new(state: StateStore, workspace_id: Uuid) -> Result<Self> {
        let workspace = state
            .get::<WorkspacePayload>(workspace_id)?
            .context("workspace is not registered")?;
        if workspace.tombstoned_at_unix_ms.is_some() {
            bail!("workspace is tombstoned");
        }
        Ok(Self {
            state,
            workspace_id,
        })
    }

    pub fn workspace_id(&self) -> Uuid {
        self.workspace_id
    }

    /// Append one batch of transcript entries, deduplicating re-delivered
    /// entries by `entry.uuid`.
    pub fn append(&self, key: &SessionKey, entries: &[Value]) -> Result<AppendOutcome> {
        key.validate()?;
        if entries.is_empty() {
            bail!("append requires at least one entry");
        }
        if entries.len() > MAX_BATCH_ENTRIES {
            bail!("append batch exceeds {MAX_BATCH_ENTRIES} entries");
        }
        for entry in entries {
            if !entry.is_object() {
                bail!("transcript entries must be JSON objects");
            }
            let bytes = serde_json::to_vec(entry)?.len();
            if bytes > MAX_ENTRY_BYTES {
                bail!("transcript entry of {bytes} bytes exceeds the {MAX_ENTRY_BYTES}-byte bound");
            }
        }
        let session = self.ensure_session(key)?;
        if session.payload.session_state != SESSION_STATE_ACTIVE {
            bail!("session is not active");
        }

        let mut turn: Option<CanonicalRecord<TurnPayload>> = None;
        let mut appended = 0usize;
        let mut deduplicated = 0usize;
        let mut last_cursor_value: Option<String> = None;
        for entry in entries {
            let native_uuid = entry.get("uuid").and_then(Value::as_str);
            if let Some(uuid) = native_uuid
                && self
                    .state
                    .get_by_native::<MessagePayload>(CLAUDE_PLATFORM_ID, uuid)?
                    .is_some()
            {
                deduplicated += 1;
                continue;
            }
            if turn.is_none() {
                turn = Some(self.claim_batch_turn(&session, key, entries.len())?);
            }
            let turn = turn.as_ref().expect("batch turn was just claimed");
            let batch_index = u64::try_from(appended).context("batch index overflow")?;
            let role = entry
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or("entry")
                .to_string();
            let model = entry
                .pointer("/message/model")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned);
            let payload = MessagePayload {
                session_id: session.id,
                turn_id: turn.id,
                role,
                native_message_id: native_uuid.map(ToOwned::to_owned),
                model,
                message_state: "mirrored".to_string(),
                metadata: json!({
                    "subpath": key.subpath,
                    "batchIndex": batch_index,
                    "entry": entry,
                }),
            };
            let mut input = CanonicalEntityInput::active(payload);
            input.workspace_id = Some(self.workspace_id);
            input.parent_id = Some(turn.id);
            if let Some(uuid) = native_uuid {
                input.origin_platform = Some(CLAUDE_PLATFORM_ID.to_string());
                input.native_id = Some(uuid.to_string());
                self.state.upsert_native(input)?;
            } else {
                self.state.put(input)?;
            }
            last_cursor_value = Some(match native_uuid {
                Some(uuid) => uuid.to_string(),
                None => format!("ordinal:{}:{batch_index}", turn.payload.ordinal),
            });
            appended += 1;
        }
        if let Some(cursor_value) = last_cursor_value {
            self.advance_cursor(key, &cursor_value, appended as u64)?;
        }
        Ok(AppendOutcome {
            appended,
            deduplicated,
            turn_ordinal: turn.map(|turn| turn.payload.ordinal),
        })
    }

    /// Load the transcript for one key: the appended entries, in order, or
    /// `None` when the transcript is unknown.
    pub fn load(&self, key: &SessionKey) -> Result<Option<Vec<Value>>> {
        key.validate()?;
        let Some(session) = self
            .state
            .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, &key.native_session_id())?
        else {
            return Ok(None);
        };
        let entries = self.transcript_entries(session.id, key.subpath.as_deref())?;
        if entries.is_empty() {
            return Ok(None);
        }
        Ok(Some(entries.into_iter().map(|(_, entry)| entry).collect()))
    }

    /// Sessions this bridge has mirrored for one project key, newest last.
    pub fn list_sessions(&self, project_key: &str) -> Result<Vec<StoredSessionSummary>> {
        validate_key_component(project_key, "project key")?;
        let mut summaries = Vec::new();
        for session in self.claude_sessions()? {
            let session_project = session
                .payload
                .metadata
                .get("projectKey")
                .and_then(Value::as_str);
            if session_project != Some(project_key) {
                continue;
            }
            let Some(native_session_id) = &session.payload.native_session_id else {
                continue;
            };
            summaries.push(StoredSessionSummary {
                session_id: native_session_id.clone(),
                mtime_unix_ms: session.updated_at_unix_ms,
                canonical_id: session.id,
            });
        }
        summaries.sort_by_key(|summary| (summary.mtime_unix_ms, summary.canonical_id));
        Ok(summaries)
    }

    /// Distinct subpaths mirrored for one session, for subagent-transcript
    /// discovery on resume.
    pub fn list_subkeys(&self, project_key: &str, session_id: &str) -> Result<Vec<String>> {
        let key = SessionKey::main(project_key, session_id);
        key.validate()?;
        let Some(session) = self
            .state
            .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, &key.native_session_id())?
        else {
            return Ok(Vec::new());
        };
        let mut subpaths = BTreeSet::new();
        self.visit_messages(session.id, |_, message| {
            if let Some(subpath) = message
                .payload
                .metadata
                .get("subpath")
                .and_then(Value::as_str)
            {
                subpaths.insert(subpath.to_string());
            }
            Ok(())
        })?;
        Ok(subpaths.into_iter().collect())
    }

    /// The linked message chain the agent would see on resume: walk parent
    /// links back from the newest entry. After auto-compaction the SDK roots
    /// post-compaction entries at a summary entry, so earlier turns fall off
    /// this chain while `load` still returns the full raw history.
    pub fn resume_view(&self, key: &SessionKey) -> Result<Vec<Value>> {
        let Some(entries) = self.load(key)? else {
            return Ok(Vec::new());
        };
        let mut by_uuid: BTreeMap<String, Value> = BTreeMap::new();
        let mut tail: Option<String> = None;
        for entry in &entries {
            if let Some(uuid) = entry.get("uuid").and_then(Value::as_str) {
                by_uuid.insert(uuid.to_string(), entry.clone());
                tail = Some(uuid.to_string());
            }
        }
        let mut chain = Vec::new();
        let mut visited = BTreeSet::new();
        let mut cursor = tail;
        while let Some(uuid) = cursor {
            if !visited.insert(uuid.clone()) || chain.len() >= RESUME_CHAIN_MAX_DEPTH {
                bail!(
                    "resume chain does not terminate for {}",
                    key.native_session_id()
                );
            }
            let Some(entry) = by_uuid.get(&uuid) else {
                break;
            };
            cursor = entry
                .get("parentUuid")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned);
            chain.push(entry.clone());
        }
        chain.reverse();
        Ok(chain)
    }

    /// Fork one main transcript under a new session id, mirroring the SDK's
    /// `forkSession`: every `sessionId` field is rewritten and every entry
    /// UUID is remapped, so the fork never references the source session. The
    /// source transcript is not touched; canonical lineage records the fork's
    /// parent and preserved lineage root.
    pub fn fork(&self, source: &SessionKey, fork_session_id: &str) -> Result<ForkOutcome> {
        source.validate()?;
        if source.subpath.is_some() {
            bail!("fork addresses the main transcript, not a subpath");
        }
        let fork_key = SessionKey::main(source.project_key.clone(), fork_session_id);
        fork_key.validate()?;
        if self
            .state
            .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, &fork_key.native_session_id())?
            .is_some()
        {
            bail!("fork target session already exists");
        }
        let source_session = self
            .state
            .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, &source.native_session_id())?
            .context("fork source session does not exist")?;
        let entries = self
            .load(source)?
            .context("fork source transcript is empty")?;

        let mut uuid_map: BTreeMap<String, String> = BTreeMap::new();
        for entry in &entries {
            if let Some(uuid) = entry.get("uuid").and_then(Value::as_str) {
                uuid_map.insert(uuid.to_string(), Uuid::new_v4().to_string());
            }
        }
        let rewritten: Vec<Value> = entries
            .iter()
            .map(|entry| {
                let mut entry = entry.clone();
                rewrite_forked_entry(&mut entry, &uuid_map, fork_session_id);
                entry
            })
            .collect();

        let fork_id = Uuid::now_v7();
        let payload = SessionPayload {
            platform: CLAUDE_PLATFORM_ID.to_string(),
            native_session_id: Some(fork_key.session_id.clone()),
            title: source_session.payload.title.clone(),
            parent_session_id: Some(source_session.id),
            lineage_root_id: source_session.payload.lineage_root_id,
            session_state: SESSION_STATE_ACTIVE.to_string(),
            repository_ref: source_session.payload.repository_ref.clone(),
            model: source_session.payload.model.clone(),
            metadata: json!({
                "projectKey": fork_key.project_key,
                "forkedFrom": source.native_session_id(),
            }),
        };
        let mut input = CanonicalEntityInput::active(payload);
        input.id = Some(fork_id);
        input.workspace_id = Some(self.workspace_id);
        input.origin_platform = Some(CLAUDE_PLATFORM_ID.to_string());
        input.native_id = Some(fork_key.native_session_id());
        let fork_session = self.state.upsert_native(input)?;
        let outcome = self.append(&fork_key, &rewritten)?;
        if outcome.appended != rewritten.len() {
            bail!(
                "fork appended {} of {} rewritten entries",
                outcome.appended,
                rewritten.len()
            );
        }
        Ok(ForkOutcome {
            source_session_id: source_session.id,
            fork_session_id: fork_session.id,
            fork_native_session_id: fork_key.native_session_id(),
            entry_count: rewritten.len(),
        })
    }

    /// Converge durable cursors with what actually landed in canonical state.
    /// A crash between an entity write and its cursor write leaves the cursor
    /// behind the transcript; this recount repairs every drifted scope and
    /// reports each transcript it examined.
    pub fn reconcile(&self) -> Result<Vec<ReconcileEntry>> {
        let mut report = Vec::new();
        for session in self.claude_sessions()? {
            let Some(native_session_id) = session.payload.native_session_id.clone() else {
                continue;
            };
            let Some(project_key) = session
                .payload
                .metadata
                .get("projectKey")
                .and_then(Value::as_str)
                .map(ToOwned::to_owned)
            else {
                continue;
            };
            let mut per_subpath: BTreeMap<Option<String>, (u64, Option<String>)> = BTreeMap::new();
            self.visit_messages(session.id, |turn, message| {
                let subpath = message
                    .payload
                    .metadata
                    .get("subpath")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                let batch_index = message
                    .payload
                    .metadata
                    .get("batchIndex")
                    .and_then(Value::as_u64)
                    .unwrap_or_default();
                let cursor_value = match &message.payload.native_message_id {
                    Some(uuid) => uuid.clone(),
                    None => format!("ordinal:{}:{batch_index}", turn.payload.ordinal),
                };
                let slot = per_subpath.entry(subpath).or_insert((0, None));
                slot.0 += 1;
                slot.1 = Some(cursor_value);
                Ok(())
            })?;
            for (subpath, (entry_count, last_cursor_value)) in per_subpath {
                let key = SessionKey {
                    project_key: project_key.clone(),
                    session_id: native_session_id.clone(),
                    subpath,
                };
                let scope = transcript_scope(&key);
                let cursor = self.state.adapter_cursor(CLAUDE_CURSOR_ADAPTER, &scope)?;
                let watermark_before = cursor
                    .as_ref()
                    .and_then(|record| record.watermark.as_deref())
                    .and_then(|watermark| watermark.parse::<u64>().ok());
                let cursor_matches = cursor.as_ref().is_some_and(|record| {
                    watermark_before == Some(entry_count)
                        && Some(record.cursor.as_str()) == last_cursor_value.as_deref()
                });
                let repaired = if cursor_matches {
                    false
                } else if let Some(cursor_value) = &last_cursor_value {
                    self.put_cursor(&scope, cursor_value, entry_count, cursor)?;
                    true
                } else {
                    false
                };
                report.push(ReconcileEntry {
                    scope,
                    entry_count,
                    cursor_watermark_before: watermark_before,
                    repaired,
                });
            }
        }
        Ok(report)
    }

    /// The durable cursor for one transcript, if recorded.
    pub fn transcript_cursor(&self, key: &SessionKey) -> Result<Option<AdapterCursorRecord>> {
        self.state
            .adapter_cursor(CLAUDE_CURSOR_ADAPTER, &transcript_scope(key))
    }

    fn ensure_session(&self, key: &SessionKey) -> Result<CanonicalRecord<SessionPayload>> {
        let native_session_id = key.native_session_id();
        if let Some(existing) = self
            .state
            .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, &native_session_id)?
        {
            if existing.tombstoned_at_unix_ms.is_some() {
                bail!("session is tombstoned");
            }
            return Ok(existing);
        }
        let session_id = Uuid::now_v7();
        let payload = SessionPayload {
            platform: CLAUDE_PLATFORM_ID.to_string(),
            native_session_id: Some(key.session_id.clone()),
            title: format!("Claude Code session {}", key.session_id),
            parent_session_id: None,
            lineage_root_id: session_id,
            session_state: SESSION_STATE_ACTIVE.to_string(),
            repository_ref: Value::Null,
            model: None,
            metadata: json!({"projectKey": key.project_key}),
        };
        let mut input = CanonicalEntityInput::active(payload);
        input.id = Some(session_id);
        input.workspace_id = Some(self.workspace_id);
        input.origin_platform = Some(CLAUDE_PLATFORM_ID.to_string());
        input.native_id = Some(native_session_id);
        self.state.upsert_native(input)
    }

    /// Claim the next turn ordinal for one appended batch. The
    /// (kind, workspace, idempotency-key) unique index makes the ordinal
    /// race-free: a concurrent append claims the same key, the put collides,
    /// and this loop re-reads the next free ordinal.
    fn claim_batch_turn(
        &self,
        session: &CanonicalRecord<SessionPayload>,
        key: &SessionKey,
        entry_count: usize,
    ) -> Result<CanonicalRecord<TurnPayload>> {
        let mut last_error = None;
        for _ in 0..TURN_APPEND_MAX_ATTEMPTS {
            let ordinal = self.state.next_turn_ordinal(session.id)?;
            let payload = TurnPayload {
                session_id: session.id,
                ordinal,
                actor: "mirror".to_string(),
                native_turn_id: None,
                turn_state: "recorded".to_string(),
                usage: Value::Null,
                metadata: json!({
                    "source": "claude_sdk_mirror",
                    "subpath": key.subpath,
                    "batchEntryCount": entry_count,
                }),
            };
            let mut input = CanonicalEntityInput::active(payload);
            input.workspace_id = Some(self.workspace_id);
            input.parent_id = Some(session.id);
            input.idempotency_key = Some(format!("turn:{}:{ordinal}", session.id));
            match self.state.put(input) {
                Ok(turn) => return Ok(turn),
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

    fn advance_cursor(&self, key: &SessionKey, cursor_value: &str, appended: u64) -> Result<()> {
        let scope = transcript_scope(key);
        let existing = self.state.adapter_cursor(CLAUDE_CURSOR_ADAPTER, &scope)?;
        let watermark = existing
            .as_ref()
            .and_then(|record| record.watermark.as_deref())
            .and_then(|watermark| watermark.parse::<u64>().ok())
            .unwrap_or_default()
            .saturating_add(appended);
        self.put_cursor(&scope, cursor_value, watermark, existing)
    }

    fn put_cursor(
        &self,
        scope: &str,
        cursor_value: &str,
        watermark: u64,
        existing: Option<AdapterCursorRecord>,
    ) -> Result<()> {
        let mut existing = existing;
        for _ in 0..CURSOR_CONFLICT_RETRIES {
            let input = AdapterCursorInput {
                adapter: CLAUDE_CURSOR_ADAPTER.to_string(),
                scope: scope.to_string(),
                cursor: cursor_value.to_string(),
                etag: None,
                watermark: Some(watermark.to_string()),
                expected_revision: existing.as_ref().map(|record| record.revision),
                metadata: json!({"source": "claude_sdk_mirror"}),
            };
            match self.state.put_adapter_cursor(input) {
                Ok(_) => return Ok(()),
                Err(error) => {
                    let detail = format!("{error:#}");
                    if !detail.contains("revision conflict") {
                        return Err(error).context("writing the Claude adapter cursor");
                    }
                    existing = self.state.adapter_cursor(CLAUDE_CURSOR_ADAPTER, scope)?;
                }
            }
        }
        bail!(
            "adapter cursor scope {scope} kept conflicting after {CURSOR_CONFLICT_RETRIES} retries"
        )
    }

    /// Ordered `(sort_key, entry)` pairs for one transcript.
    fn transcript_entries(
        &self,
        session_id: Uuid,
        subpath: Option<&str>,
    ) -> Result<Vec<((u64, u64), Value)>> {
        let mut entries = Vec::new();
        self.visit_messages(session_id, |turn, message| {
            let message_subpath = message
                .payload
                .metadata
                .get("subpath")
                .and_then(Value::as_str);
            if message_subpath != subpath {
                return Ok(());
            }
            let batch_index = message
                .payload
                .metadata
                .get("batchIndex")
                .and_then(Value::as_u64)
                .unwrap_or_default();
            let entry = message
                .payload
                .metadata
                .get("entry")
                .cloned()
                .context("mirrored message is missing its transcript entry")?;
            entries.push(((turn.payload.ordinal, batch_index), entry));
            Ok(())
        })?;
        entries.sort_by_key(|entry| entry.0);
        Ok(entries)
    }

    /// Visit every live message of one session in (turn ordinal, message id)
    /// order, paging within the registry bounds.
    fn visit_messages<F>(&self, session_id: Uuid, mut visit: F) -> Result<()>
    where
        F: FnMut(&CanonicalRecord<TurnPayload>, &CanonicalRecord<MessagePayload>) -> Result<()>,
    {
        let mut after_ordinal = None;
        loop {
            let page = self
                .state
                .turn_page(session_id, after_ordinal, ENUMERATION_PAGE_LIMIT)?;
            for turn in &page.items {
                let mut cursor = None;
                loop {
                    let (messages, next_cursor, truncated) = self
                        .state
                        .child_page::<MessagePayload>(turn.id, cursor, ENUMERATION_PAGE_LIMIT)?;
                    for message in &messages {
                        visit(turn, message)?;
                    }
                    if !truncated {
                        break;
                    }
                    cursor = next_cursor;
                }
            }
            if !page.truncated {
                return Ok(());
            }
            after_ordinal = page.next_ordinal;
        }
    }

    /// Every live Claude Code session in this workspace, paged within the
    /// registry bounds.
    fn claude_sessions(&self) -> Result<Vec<CanonicalRecord<SessionPayload>>> {
        let mut sessions = Vec::new();
        let mut cursor = None;
        loop {
            let page = self.state.session_page(
                Some(self.workspace_id),
                true,
                cursor,
                ENUMERATION_PAGE_LIMIT,
            )?;
            for session in page.items {
                if session.payload.platform == CLAUDE_PLATFORM_ID {
                    sessions.push(session);
                }
            }
            if !page.truncated {
                return Ok(sessions);
            }
            cursor = page.next_cursor;
        }
    }
}

/// Rewrite one forked entry in place: remap linkage UUID fields through the
/// fork map and point every `sessionId` field at the fork.
fn rewrite_forked_entry(entry: &mut Value, uuid_map: &BTreeMap<String, String>, fork_id: &str) {
    match entry {
        Value::Object(object) => rewrite_forked_object(object, uuid_map, fork_id),
        Value::Array(items) => {
            for item in items {
                rewrite_forked_entry(item, uuid_map, fork_id);
            }
        }
        _ => {}
    }
}

fn rewrite_forked_object(
    object: &mut Map<String, Value>,
    uuid_map: &BTreeMap<String, String>,
    fork_id: &str,
) {
    for (field, value) in object.iter_mut() {
        match field.as_str() {
            "sessionId" => {
                if value.is_string() {
                    *value = Value::String(fork_id.to_string());
                }
            }
            "uuid" | "parentUuid" | "leafUuid" => {
                if let Some(mapped) = value.as_str().and_then(|uuid| uuid_map.get(uuid)) {
                    *value = Value::String(mapped.clone());
                }
            }
            _ => rewrite_forked_entry(value, uuid_map, fork_id),
        }
    }
}
