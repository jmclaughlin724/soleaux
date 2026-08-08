use crate::{
    AdmissionVerifier, BoxFuture, CLAUDE_PLATFORM_ID, ClaudeHost, ClaudeHostConfig,
    ClaudeHostEvent, ClaudeSessionStore, HOST_PROTOCOL_VERSION, HarnessConnection,
    HarnessConnector, HostError, MAX_ENTRY_BYTES, PINNED_CLAUDE_CODE_VERSION, PermissionDecision,
    ReconnectPolicy, SessionKey, WriteAuthority, sdk_version_refusal, transcript_scope,
};
use serde_json::{Value, json};
use soleaux_ipc::{ADMISSION_RECEIPT_SCHEMA_VERSION, AdmissionReceipt};
use soleaux_state::{
    AdapterCursorInput, CanonicalEntityInput, LOCKED_CONTEXT_PACKET_SHA256, LOCKED_PROFILE_SHA256,
    MessagePayload, PUBLIC_TOOL_CEILING, SessionPayload, StateStore, WorkspacePayload,
    WorkspaceTrustState,
};
use std::{
    collections::{BTreeSet, VecDeque},
    sync::{Arc, Mutex},
    time::Duration,
};
use tempfile::TempDir;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader, DuplexStream, ReadHalf, WriteHalf},
    sync::mpsc,
    time::timeout,
};
use uuid::Uuid;

// --- fixtures ---------------------------------------------------------------

struct StoreFixture {
    _directory: TempDir,
    state: StateStore,
    store: ClaudeSessionStore,
    workspace_id: Uuid,
}

fn open_store() -> StoreFixture {
    let directory = TempDir::new().expect("tempdir");
    let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let path_hash = "a".repeat(64);
    let payload = WorkspacePayload {
        canonical_path: "/workspace/fixture".to_string(),
        path_hash: path_hash.clone(),
        display_name: "Fixture workspace".to_string(),
        trust_state: WorkspaceTrustState::Trusted,
        profile_digest: LOCKED_PROFILE_SHA256.to_string(),
        context_digest: LOCKED_CONTEXT_PACKET_SHA256.to_string(),
        public_tool_ceiling: PUBLIC_TOOL_CEILING,
        production_claim_allowed: false,
        metadata: json!({"fixture": true}),
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.state = "registered".to_string();
    input.origin_platform = Some("soleaux.workspace".to_string());
    input.native_id = Some(path_hash.clone());
    input.idempotency_key = Some(format!("workspace:{path_hash}"));
    let workspace_id = state
        .registry_register_workspace(input)
        .expect("workspace registration")
        .workspace
        .id;
    let store = ClaudeSessionStore::new(state.clone(), workspace_id).expect("store bridge");
    StoreFixture {
        _directory: directory,
        state,
        store,
        workspace_id,
    }
}

fn entry(
    entry_type: &str,
    uuid: Option<&str>,
    parent_uuid: Option<&str>,
    session_id: &str,
    text: &str,
) -> Value {
    let mut value = json!({
        "type": entry_type,
        "parentUuid": parent_uuid,
        "sessionId": session_id,
        "message": {
            "role": entry_type,
            "model": "claude-fable-5",
            "content": [{"type": "text", "text": text}],
        },
    });
    if let Some(uuid) = uuid {
        value["uuid"] = json!(uuid);
    }
    value
}

fn receipt(platform: &str, client_version: &str, expires_at_unix_ms: i64) -> AdmissionReceipt {
    AdmissionReceipt {
        schema_version: ADMISSION_RECEIPT_SCHEMA_VERSION.to_string(),
        client_id: Uuid::new_v4(),
        platform: platform.to_string(),
        client_version: client_version.to_string(),
        matrix_sha256: "d".repeat(64),
        workspace_id: Uuid::new_v4(),
        issued_at_unix_ms: 0,
        expires_at_unix_ms,
        probe_evidence_sha256: "e".repeat(64),
        key_version: 1,
        mac: "f".repeat(64),
    }
}

fn far_future_unix_ms() -> i64 {
    4_102_444_800_000 // 2100-01-01
}

fn now_unix_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or_default()
}

struct FakeVerifier {
    accept: bool,
}

impl AdmissionVerifier for FakeVerifier {
    async fn verify(&self, _receipt: &AdmissionReceipt) -> Result<(), HostError> {
        if self.accept {
            Ok(())
        } else {
            Err(HostError::VerifierRejected(
                "scripted rejection".to_string(),
            ))
        }
    }
}

// --- store bridge -----------------------------------------------------------

#[test]
fn pinned_version_matches_the_embedded_capability_matrix() {
    let matrix: Value =
        serde_json::from_str(soleaux_ipc::CLIENT_CAPABILITY_MATRIX_JSON).expect("matrix parses");
    let platform = matrix["platforms"]
        .as_array()
        .expect("platforms")
        .iter()
        .find(|platform| platform["id"] == CLAUDE_PLATFORM_ID)
        .expect("matrix has a claude_code platform");
    let versions = platform["versions"].as_array().expect("versions");
    assert_eq!(versions.len(), 1, "claude_code pins exactly one version");
    assert_eq!(versions[0]["version"], PINNED_CLAUDE_CODE_VERSION);
    assert_eq!(versions[0]["mutationEligible"], false);
}

#[test]
fn sdk_version_gate_fails_closed() {
    assert!(sdk_version_refusal(None).is_some());
    assert!(sdk_version_refusal(Some("2.1.222")).is_some());
    assert!(sdk_version_refusal(Some("3.0.0")).is_some());
    assert!(sdk_version_refusal(Some(PINNED_CLAUDE_CODE_VERSION)).is_none());
}

#[test]
fn store_append_load_round_trips_through_canonical_entities() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-1");
    let entries = vec![
        entry("user", Some("u1"), None, "sess-1", "list the files"),
        entry(
            "assistant",
            Some("a1"),
            Some("u1"),
            "sess-1",
            "here they are",
        ),
        json!({"type": "file-history-snapshot", "snapshot": {"files": []}}),
    ];
    let outcome = fixture.store.append(&key, &entries).expect("append");
    assert_eq!(outcome.appended, 3);
    assert_eq!(outcome.deduplicated, 0);
    let first_ordinal = outcome.turn_ordinal.expect("batch turn");

    let session = fixture
        .state
        .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, "project-a/sess-1")
        .expect("session read")
        .expect("session exists");
    assert_eq!(session.payload.platform, CLAUDE_PLATFORM_ID);
    assert_eq!(session.payload.native_session_id.as_deref(), Some("sess-1"));
    assert_eq!(session.workspace_id, Some(fixture.workspace_id));
    assert_eq!(session.payload.lineage_root_id, session.id);

    let turns = fixture
        .state
        .turn_page(session.id, None, 32)
        .expect("turn page");
    assert_eq!(turns.items.len(), 1);
    assert_eq!(turns.items[0].payload.ordinal, first_ordinal);
    let (messages, _, _) = fixture
        .state
        .child_page::<MessagePayload>(turns.items[0].id, None, 32)
        .expect("message page");
    assert_eq!(messages.len(), 3);
    let roles: BTreeSet<&str> = messages
        .iter()
        .map(|message| message.payload.role.as_str())
        .collect();
    assert_eq!(
        roles,
        BTreeSet::from(["user", "assistant", "file-history-snapshot"])
    );
    let with_native: usize = messages
        .iter()
        .filter(|message| message.payload.native_message_id.is_some())
        .count();
    assert_eq!(with_native, 2, "uuid-less entries carry no native id");

    let second = vec![entry("user", Some("u2"), Some("a1"), "sess-1", "thanks")];
    let outcome = fixture.store.append(&key, &second).expect("second append");
    assert!(outcome.turn_ordinal.expect("second turn") > first_ordinal);

    let loaded = fixture.store.load(&key).expect("load").expect("entries");
    let mut expected = entries.clone();
    expected.extend(second.clone());
    assert_eq!(loaded, expected, "load returns appended entries in order");

    let cursor = fixture
        .store
        .transcript_cursor(&key)
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(cursor.cursor, "u2");
    assert_eq!(cursor.watermark.as_deref(), Some("4"));

    assert!(
        fixture
            .store
            .load(&SessionKey::main("project-a", "missing"))
            .expect("missing load")
            .is_none(),
        "an unknown transcript loads as None"
    );
}

#[test]
fn append_deduplicates_redelivered_entries_by_uuid() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-2");
    let entries = vec![
        entry("user", Some("u1"), None, "sess-2", "hello"),
        entry("assistant", Some("a1"), Some("u1"), "sess-2", "hi"),
    ];
    fixture.store.append(&key, &entries).expect("append");
    let redelivered = fixture.store.append(&key, &entries).expect("redelivery");
    assert_eq!(redelivered.appended, 0);
    assert_eq!(redelivered.deduplicated, 2);
    assert_eq!(redelivered.turn_ordinal, None, "no empty mirror turn");
    let loaded = fixture.store.load(&key).expect("load").expect("entries");
    assert_eq!(loaded.len(), 2);
}

#[test]
fn entry_bounds_fail_closed() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-3");
    let oversized = json!({
        "type": "user",
        "uuid": "u1",
        "message": {"content": "x".repeat(MAX_ENTRY_BYTES)},
    });
    let error = fixture
        .store
        .append(&key, &[oversized])
        .expect_err("oversized entry is refused");
    assert!(error.to_string().contains("exceeds"), "{error:#}");
    let error = fixture
        .store
        .append(&key, &[json!("not an object")])
        .expect_err("non-object entry is refused");
    assert!(error.to_string().contains("JSON objects"), "{error:#}");
    assert!(
        fixture.store.load(&key).expect("load").is_none(),
        "a refused batch writes nothing"
    );
}

#[test]
fn resume_view_returns_the_linked_chain() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-4");
    let entries = vec![
        entry("user", Some("u1"), None, "sess-4", "start"),
        json!({"type": "file-history-snapshot", "snapshot": {}}),
        entry("assistant", Some("a1"), Some("u1"), "sess-4", "working"),
        entry("assistant", Some("a2"), Some("a1"), "sess-4", "done"),
    ];
    fixture.store.append(&key, &entries).expect("append");
    let chain = fixture.store.resume_view(&key).expect("resume view");
    let uuids: Vec<&str> = chain
        .iter()
        .map(|entry| entry["uuid"].as_str().expect("chain uuid"))
        .collect();
    assert_eq!(uuids, vec!["u1", "a1", "a2"]);
}

#[test]
fn compaction_summary_replaces_earlier_turns_in_the_resume_view() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-5");
    let before = vec![
        entry("user", Some("u1"), None, "sess-5", "first ask"),
        entry(
            "assistant",
            Some("a1"),
            Some("u1"),
            "sess-5",
            "first answer",
        ),
        entry("user", Some("u2"), Some("a1"), "sess-5", "second ask"),
        entry(
            "assistant",
            Some("a2"),
            Some("u2"),
            "sess-5",
            "second answer",
        ),
    ];
    fixture.store.append(&key, &before).expect("append history");
    // Auto-compaction: the SDK roots the continuation at a summary entry, so
    // the pre-compaction turns fall off the linked chain.
    let compacted = vec![
        json!({
            "type": "summary",
            "uuid": "s1",
            "parentUuid": null,
            "sessionId": "sess-5",
            "summary": "earlier turns condensed",
            "leafUuid": "a2",
        }),
        entry("user", Some("u3"), Some("s1"), "sess-5", "third ask"),
        entry(
            "assistant",
            Some("a3"),
            Some("u3"),
            "sess-5",
            "third answer",
        ),
    ];
    fixture
        .store
        .append(&key, &compacted)
        .expect("append compacted");

    let raw = fixture.store.load(&key).expect("load").expect("entries");
    assert_eq!(raw.len(), 7, "load returns the full raw history");

    let chain = fixture.store.resume_view(&key).expect("resume view");
    let uuids: Vec<&str> = chain
        .iter()
        .map(|entry| entry["uuid"].as_str().expect("chain uuid"))
        .collect();
    assert_eq!(
        uuids,
        vec!["s1", "u3", "a3"],
        "the summary replaces the pre-compaction turns in the resume view"
    );
}

#[test]
fn fork_rewrites_ids_and_leaves_the_source_untouched() {
    let fixture = open_store();
    let source_key = SessionKey::main("project-a", "sess-6");
    let entries = vec![
        entry("user", Some("u1"), None, "sess-6", "start"),
        entry("assistant", Some("a1"), Some("u1"), "sess-6", "answer"),
    ];
    fixture.store.append(&source_key, &entries).expect("append");
    let before_fork = fixture
        .store
        .load(&source_key)
        .expect("load")
        .expect("entries");

    let outcome = fixture
        .store
        .fork(&source_key, "sess-6-fork")
        .expect("fork");
    assert_eq!(outcome.entry_count, 2);
    assert_eq!(outcome.fork_native_session_id, "project-a/sess-6-fork");

    let after_fork = fixture
        .store
        .load(&source_key)
        .expect("source load")
        .expect("source entries");
    assert_eq!(
        before_fork, after_fork,
        "the source transcript is untouched"
    );

    let fork_key = SessionKey::main("project-a", "sess-6-fork");
    let forked = fixture
        .store
        .load(&fork_key)
        .expect("fork load")
        .expect("fork entries");
    assert_eq!(forked.len(), 2);
    let source_uuids: BTreeSet<&str> = before_fork
        .iter()
        .filter_map(|entry| entry["uuid"].as_str())
        .collect();
    for entry in &forked {
        let uuid = entry["uuid"].as_str().expect("fork uuid");
        assert!(!source_uuids.contains(uuid), "fork remaps entry uuids");
        assert_eq!(entry["sessionId"], "sess-6-fork", "sessionId is rewritten");
    }
    assert_eq!(
        forked[1]["parentUuid"], forked[0]["uuid"],
        "fork preserves the link structure under remapped uuids"
    );

    let source_session = fixture
        .state
        .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, "project-a/sess-6")
        .expect("source session")
        .expect("source exists");
    let fork_session = fixture
        .state
        .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, "project-a/sess-6-fork")
        .expect("fork session")
        .expect("fork exists");
    assert_eq!(
        fork_session.payload.parent_session_id,
        Some(source_session.id)
    );
    assert_eq!(
        fork_session.payload.lineage_root_id,
        source_session.payload.lineage_root_id
    );

    let chain = fixture.store.resume_view(&fork_key).expect("fork resume");
    assert_eq!(chain.len(), 2, "the fork resumes on its own chain");
}

#[test]
fn subagent_transcripts_round_trip_under_subpaths() {
    let fixture = open_store();
    let main_key = SessionKey::main("project-a", "sess-7");
    let subagent_key = SessionKey {
        project_key: "project-a".to_string(),
        session_id: "sess-7".to_string(),
        subpath: Some("subagents/agent-1".to_string()),
    };
    fixture
        .store
        .append(
            &main_key,
            &[entry("user", Some("u1"), None, "sess-7", "delegate")],
        )
        .expect("main append");
    fixture
        .store
        .append(
            &subagent_key,
            &[
                entry("user", Some("su1"), None, "sess-7", "subtask"),
                entry("assistant", Some("sa1"), Some("su1"), "sess-7", "done"),
            ],
        )
        .expect("subagent append");

    let main_entries = fixture
        .store
        .load(&main_key)
        .expect("main load")
        .expect("main entries");
    assert_eq!(
        main_entries.len(),
        1,
        "subagent entries stay off the main transcript"
    );
    let subagent_entries = fixture
        .store
        .load(&subagent_key)
        .expect("subagent load")
        .expect("subagent entries");
    assert_eq!(subagent_entries.len(), 2);
    assert_eq!(
        fixture
            .store
            .list_subkeys("project-a", "sess-7")
            .expect("subkeys"),
        vec!["subagents/agent-1".to_string()]
    );
    assert_eq!(
        fixture
            .store
            .list_sessions("project-a")
            .expect("sessions")
            .iter()
            .filter(|summary| summary.session_id == "sess-7")
            .count(),
        1,
        "the subagent transcript does not mint a second session"
    );
}

#[test]
fn restart_reconciliation_converges_cursor_state() {
    let fixture = open_store();
    let key = SessionKey::main("project-a", "sess-8");
    fixture
        .store
        .append(
            &key,
            &[
                entry("user", Some("u1"), None, "sess-8", "one"),
                entry("assistant", Some("a1"), Some("u1"), "sess-8", "two"),
            ],
        )
        .expect("append");

    // Simulate a crash between the entity writes and the cursor write: the
    // durable cursor is stale when the next host generation starts.
    fixture
        .state
        .put_adapter_cursor(AdapterCursorInput {
            adapter: crate::CLAUDE_CURSOR_ADAPTER.to_string(),
            scope: transcript_scope(&key),
            cursor: "bogus".to_string(),
            etag: None,
            watermark: Some("1".to_string()),
            expected_revision: Some(1),
            metadata: json!({}),
        })
        .expect("clobber cursor");

    let restarted =
        ClaudeSessionStore::new(fixture.state.clone(), fixture.workspace_id).expect("restart");
    let report = restarted.reconcile().expect("reconcile");
    let entry = report
        .iter()
        .find(|entry| entry.scope == transcript_scope(&key))
        .expect("report covers the transcript");
    assert_eq!(entry.entry_count, 2);
    assert_eq!(entry.cursor_watermark_before, Some(1));
    assert!(entry.repaired, "the drifted cursor is repaired");

    let cursor = restarted
        .transcript_cursor(&key)
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(cursor.cursor, "a1");
    assert_eq!(cursor.watermark.as_deref(), Some("2"));

    let second = restarted.reconcile().expect("second reconcile");
    let entry = second
        .iter()
        .find(|entry| entry.scope == transcript_scope(&key))
        .expect("report covers the transcript");
    assert!(!entry.repaired, "a converged cursor is left alone");

    assert_eq!(
        restarted.load(&key).expect("load").expect("entries").len(),
        2,
        "the restarted bridge serves the same transcript"
    );
}

// --- scripted harness fixtures ----------------------------------------------

struct FakeHarness {
    reader: BufReader<ReadHalf<DuplexStream>>,
    writer: WriteHalf<DuplexStream>,
}

impl FakeHarness {
    fn new(stream: DuplexStream) -> Self {
        let (reader, writer) = tokio::io::split(stream);
        Self {
            reader: BufReader::new(reader),
            writer,
        }
    }

    async fn recv(&mut self) -> Value {
        let mut line = String::new();
        let bytes = timeout(Duration::from_secs(5), self.reader.read_line(&mut line))
            .await
            .expect("harness recv timed out")
            .expect("harness read");
        assert!(bytes > 0, "host closed the connection unexpectedly");
        serde_json::from_str(line.trim()).expect("host frame parses")
    }

    async fn send(&mut self, value: &Value) {
        let mut line = serde_json::to_string(value).expect("harness frame encodes");
        line.push('\n');
        self.writer
            .write_all(line.as_bytes())
            .await
            .expect("harness write");
        self.writer.flush().await.expect("harness flush");
    }

    /// Send `hello` with one SDK version and return the host's ack.
    async fn hello(&mut self, sdk_version: &str) -> Value {
        self.send(&json!({
            "type": "hello",
            "protocol": HOST_PROTOCOL_VERSION,
            "sdkPackage": "@anthropic-ai/claude-agent-sdk",
            "sdkVersion": sdk_version,
            "harnessVersion": "test",
        }))
        .await;
        let ack = self.recv().await;
        assert_eq!(ack["type"], "hello_ack");
        assert_eq!(ack["pinnedSdkVersion"], PINNED_CLAUDE_CODE_VERSION);
        ack
    }

    /// Send one store frame and return the matching `store_result`.
    async fn store_call(&mut self, frame: Value) -> Value {
        let id = frame["id"].clone();
        self.send(&frame).await;
        let result = self.recv().await;
        assert_eq!(result["type"], "store_result");
        assert_eq!(result["id"], id);
        result
    }
}

struct QueueConnector {
    connections: Mutex<VecDeque<HarnessConnection>>,
}

impl QueueConnector {
    fn scripted(count: usize) -> (Arc<Self>, Vec<DuplexStream>) {
        let mut connections = VecDeque::new();
        let mut harness_sides = Vec::new();
        for _ in 0..count {
            let (host_side, harness_side) = tokio::io::duplex(64 * 1024);
            let (reader, writer) = tokio::io::split(host_side);
            connections.push_back(HarnessConnection {
                reader: Box::new(reader),
                writer: Box::new(writer),
                child: None,
            });
            harness_sides.push(harness_side);
        }
        (
            Arc::new(Self {
                connections: Mutex::new(connections),
            }),
            harness_sides,
        )
    }
}

impl HarnessConnector for QueueConnector {
    fn connect(&self) -> BoxFuture<'_, Result<HarnessConnection, HostError>> {
        let next = self.connections.lock().expect("connector lock").pop_front();
        Box::pin(async move {
            next.ok_or_else(|| HostError::Spawn("no scripted connection left".to_string()))
        })
    }
}

fn test_config() -> ClaudeHostConfig {
    ClaudeHostConfig {
        hello_timeout: Duration::from_secs(5),
        request_timeout: Duration::from_secs(5),
        permission_timeout: Duration::from_secs(5),
        reconnect: ReconnectPolicy {
            max_attempts: 0,
            initial_delay: Duration::from_millis(10),
            max_delay: Duration::from_millis(50),
        },
        ..ClaudeHostConfig::default()
    }
}

async fn next_event(events: &mut mpsc::Receiver<ClaudeHostEvent>) -> ClaudeHostEvent {
    timeout(Duration::from_secs(5), events.recv())
        .await
        .expect("event timed out")
        .expect("event stream ended early")
}

async fn admit(host: &ClaudeHost) {
    let verifier = FakeVerifier { accept: true };
    host.enable_write(
        &receipt(
            CLAUDE_PLATFORM_ID,
            PINNED_CLAUDE_CODE_VERSION,
            far_future_unix_ms(),
        ),
        &verifier,
    )
    .await
    .expect("write admission");
}

// --- host -------------------------------------------------------------------

#[tokio::test]
async fn host_handshake_serves_store_calls_through_canonical_state() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(1);
    let harness_stream = streams.remove(0);
    let harness = tokio::spawn(async move {
        let mut harness = FakeHarness::new(harness_stream);
        let ack = harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        assert_eq!(ack["mode"], "pinned_read_only");
        assert_eq!(ack["refusal"], Value::Null);

        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 1,
                "op": "append",
                "key": {"projectKey": "project-a", "sessionId": "sess-h1"},
                "entries": [
                    entry("user", Some("u1"), None, "sess-h1", "hello"),
                    entry("assistant", Some("a1"), Some("u1"), "sess-h1", "hi"),
                ],
            }))
            .await;
        assert_eq!(result["ok"], true);
        assert_eq!(result["result"]["appended"], 2);

        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 2,
                "op": "load",
                "key": {"projectKey": "project-a", "sessionId": "sess-h1"},
            }))
            .await;
        assert_eq!(result["ok"], true);
        assert_eq!(result["result"].as_array().expect("entries").len(), 2);

        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 3,
                "op": "list_sessions",
                "projectKey": "project-a",
            }))
            .await;
        assert_eq!(result["result"][0]["sessionId"], "sess-h1");

        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 4,
                "op": "delete",
                "key": {"projectKey": "project-a", "sessionId": "sess-h1"},
            }))
            .await;
        assert_eq!(result["ok"], false);
        assert!(
            result["error"]
                .as_str()
                .expect("error")
                .contains("owns retention"),
            "delete is refused: {result}"
        );
        harness
    });

    let (host, mut events) = ClaudeHost::connect(connector, test_config(), fixture.store.clone())
        .await
        .expect("connect");
    match next_event(&mut events).await {
        ClaudeHostEvent::Connected {
            epoch,
            sdk_version,
            safe_mode_reason,
        } => {
            assert_eq!(epoch, 1);
            assert_eq!(sdk_version.as_deref(), Some(PINNED_CLAUDE_CODE_VERSION));
            assert_eq!(safe_mode_reason, None);
        }
        other => panic!("expected Connected, got {other:?}"),
    }
    let _harness = harness.await.expect("harness task");

    let session = fixture
        .state
        .get_by_native::<SessionPayload>(CLAUDE_PLATFORM_ID, "project-a/sess-h1")
        .expect("session read")
        .expect("session exists");
    assert_eq!(session.payload.platform, CLAUDE_PLATFORM_ID);
    host.shutdown().await;
}

#[tokio::test]
async fn safe_mode_refuses_unpinned_versions_and_denies_permissions() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(1);
    let harness_stream = streams.remove(0);
    let harness = tokio::spawn(async move {
        let mut harness = FakeHarness::new(harness_stream);
        let ack = harness.hello("2.1.222").await;
        assert_eq!(ack["mode"], "read_only");
        assert!(
            ack["refusal"]
                .as_str()
                .expect("refusal")
                .contains("2.1.222"),
            "refusal names the version: {ack}"
        );
        harness
            .send(&json!({
                "type": "permission_request",
                "id": 7,
                "request": {"toolName": "Bash", "input": {"command": "rm -rf /"}},
            }))
            .await;
        let decision = harness.recv().await;
        assert_eq!(decision["type"], "permission_decision");
        assert_eq!(decision["id"], 7);
        assert_eq!(decision["decision"]["behavior"], "deny");
        harness
    });

    let (host, mut events) = ClaudeHost::connect(connector, test_config(), fixture.store.clone())
        .await
        .expect("connect");
    match next_event(&mut events).await {
        ClaudeHostEvent::Connected {
            safe_mode_reason, ..
        } => {
            assert!(safe_mode_reason.is_some(), "safe mode is recorded");
        }
        other => panic!("expected Connected, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::PermissionDenied { reason, .. } => {
            assert!(reason.contains("safe mode"), "{reason}");
        }
        other => panic!("expected PermissionDenied, got {other:?}"),
    }

    let error = host
        .session_start("project-a", "hello", Value::Null)
        .await
        .expect_err("safe mode refuses execution");
    assert!(matches!(error, HostError::SafeMode { .. }), "{error}");

    let verifier = FakeVerifier { accept: true };
    let error = host
        .enable_write(
            &receipt(
                CLAUDE_PLATFORM_ID,
                PINNED_CLAUDE_CODE_VERSION,
                far_future_unix_ms(),
            ),
            &verifier,
        )
        .await
        .expect_err("safe mode refuses admission");
    assert!(matches!(error, HostError::SafeMode { .. }), "{error}");

    let _harness = harness.await.expect("harness task");
    host.shutdown().await;
}

#[tokio::test]
async fn write_admission_requires_a_verified_receipt() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(1);
    let harness_stream = streams.remove(0);
    let harness = tokio::spawn(async move {
        let mut harness = FakeHarness::new(harness_stream);
        harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        // Answer the admitted session.start command.
        let request = harness.recv().await;
        assert_eq!(request["type"], "request");
        assert_eq!(request["op"], "session.start");
        assert_eq!(request["params"]["projectKey"], "project-a");
        harness
            .send(&json!({
                "type": "response",
                "id": request["id"],
                "ok": true,
                "result": {"sessionId": "sess-w1"},
            }))
            .await;
        harness
    });

    let (host, mut events) = ClaudeHost::connect(connector, test_config(), fixture.store.clone())
        .await
        .expect("connect");
    let _ = next_event(&mut events).await; // Connected

    let accepting = FakeVerifier { accept: true };
    let rejecting = FakeVerifier { accept: false };
    let valid = receipt(
        CLAUDE_PLATFORM_ID,
        PINNED_CLAUDE_CODE_VERSION,
        far_future_unix_ms(),
    );

    let error = host
        .session_start("project-a", "hello", Value::Null)
        .await
        .expect_err("no admission yet");
    assert!(matches!(error, HostError::SafeMode { .. }), "{error}");

    let error = host
        .enable_write(
            &receipt("opencode", PINNED_CLAUDE_CODE_VERSION, far_future_unix_ms()),
            &accepting,
        )
        .await
        .expect_err("wrong platform");
    assert!(matches!(error, HostError::ReceiptMismatch(_)), "{error}");

    let error = host
        .enable_write(
            &receipt(CLAUDE_PLATFORM_ID, "2.1.222", far_future_unix_ms()),
            &accepting,
        )
        .await
        .expect_err("wrong version");
    assert!(matches!(error, HostError::ReceiptMismatch(_)), "{error}");

    let error = host
        .enable_write(
            &receipt(CLAUDE_PLATFORM_ID, PINNED_CLAUDE_CODE_VERSION, 1),
            &accepting,
        )
        .await
        .expect_err("expired receipt");
    assert!(matches!(error, HostError::AdmissionExpired), "{error}");

    let error = host
        .enable_write(&valid, &rejecting)
        .await
        .expect_err("verifier rejection");
    assert!(matches!(error, HostError::VerifierRejected(_)), "{error}");
    assert_eq!(host.authority(), WriteAuthority::ReadOnly);

    host.enable_write(&valid, &accepting)
        .await
        .expect("admission");
    assert!(matches!(host.authority(), WriteAuthority::Admitted { .. }));

    let outcome = host
        .session_start("project-a", "hello", Value::Null)
        .await
        .expect("admitted start");
    assert_eq!(outcome.native_session_id, "sess-w1");

    // An expired admission demotes the host on the next mutating call.
    let short = receipt(
        CLAUDE_PLATFORM_ID,
        PINNED_CLAUDE_CODE_VERSION,
        now_unix_ms() + 40,
    );
    host.enable_write(&short, &accepting)
        .await
        .expect("short admission");
    tokio::time::sleep(Duration::from_millis(60)).await;
    let error = host
        .session_start("project-a", "again", Value::Null)
        .await
        .expect_err("expired admission demotes");
    assert!(matches!(error, HostError::AdmissionExpired), "{error}");
    assert_eq!(host.authority(), WriteAuthority::ReadOnly);

    let _harness = harness.await.expect("harness task");
    host.shutdown().await;
}

#[tokio::test]
async fn hooks_permissions_and_subagent_events_surface_through_the_host() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(1);
    let harness_stream = streams.remove(0);
    let harness = tokio::spawn(async move {
        let mut harness = FakeHarness::new(harness_stream);
        harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        harness
            .send(&json!({
                "type": "event",
                "event": "hook",
                "hook": "PreToolUse",
                "payload": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            }))
            .await;
        harness
            .send(&json!({
                "type": "event",
                "event": "system",
                "payload": {"type": "system", "subtype": "compact_boundary"},
            }))
            .await;
        // Wait for the host's interrupt barrier so the permission request
        // arrives only after write admission is in place.
        let barrier = harness.recv().await;
        assert_eq!(barrier["op"], "session.interrupt");
        harness
            .send(&json!({
                "type": "response",
                "id": barrier["id"],
                "ok": true,
                "result": {},
            }))
            .await;
        harness
            .send(&json!({
                "type": "permission_request",
                "id": 11,
                "request": {"toolName": "Edit", "input": {"file_path": "/tmp/x"}},
            }))
            .await;
        let decision = harness.recv().await;
        assert_eq!(decision["id"], 11);
        assert_eq!(decision["decision"]["behavior"], "allow");
        assert_eq!(decision["decision"]["updatedInput"]["file_path"], "/tmp/x");
        // A subagent transcript append surfaces a typed event.
        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 12,
                "op": "append",
                "key": {
                    "projectKey": "project-a",
                    "sessionId": "sess-e1",
                    "subpath": "subagents/agent-9",
                },
                "entries": [entry("user", Some("su1"), None, "sess-e1", "subtask")],
            }))
            .await;
        assert_eq!(result["ok"], true);
        harness
    });

    let (host, mut events) = ClaudeHost::connect(connector, test_config(), fixture.store.clone())
        .await
        .expect("connect");
    admit(&host).await;
    host.session_interrupt().await.expect("interrupt barrier");
    let _ = next_event(&mut events).await; // Connected
    match next_event(&mut events).await {
        ClaudeHostEvent::Hook { name, payload } => {
            assert_eq!(name, "PreToolUse");
            assert_eq!(payload["tool_name"], "Bash");
        }
        other => panic!("expected Hook, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::System { payload } => {
            assert_eq!(payload["subtype"], "compact_boundary");
        }
        other => panic!("expected System, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::PermissionRequested(pending) => {
            assert_eq!(pending.request()["toolName"], "Edit");
            pending
                .respond(PermissionDecision::Allow {
                    updated_input: Some(json!({"file_path": "/tmp/x"})),
                })
                .expect("decision delivered");
        }
        other => panic!("expected PermissionRequested, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::SubagentTranscript {
            native_session_id,
            subpath,
        } => {
            assert_eq!(native_session_id, "project-a/sess-e1");
            assert_eq!(subpath, "subagents/agent-9");
        }
        other => panic!("expected SubagentTranscript, got {other:?}"),
    }
    let _harness = harness.await.expect("harness task");
    host.shutdown().await;
}

#[tokio::test]
async fn mirror_error_append_failure_is_answered_logged_and_survived() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(1);
    let harness_stream = streams.remove(0);
    let harness = tokio::spawn(async move {
        let mut harness = FakeHarness::new(harness_stream);
        harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        // A failing append is answered as a store failure, not a hangup.
        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 21,
                "op": "append",
                "key": {"projectKey": "project-a", "sessionId": "sess-m1"},
                "entries": ["not an object"],
            }))
            .await;
        assert_eq!(result["ok"], false);
        assert!(
            result["error"]
                .as_str()
                .expect("error")
                .contains("JSON objects"),
            "{result}"
        );
        // The SDK then logs, emits mirror_error into the iterator, drops the
        // batch, and continues; the harness forwards that system message.
        harness
            .send(&json!({
                "type": "event",
                "event": "system",
                "payload": {"type": "system", "subtype": "mirror_error", "error": "append failed"},
            }))
            .await;
        // The connection is still alive: the next batch lands.
        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 22,
                "op": "append",
                "key": {"projectKey": "project-a", "sessionId": "sess-m1"},
                "entries": [entry("user", Some("u1"), None, "sess-m1", "recovered")],
            }))
            .await;
        assert_eq!(result["ok"], true);
        harness
    });

    let (host, mut events) = ClaudeHost::connect(connector, test_config(), fixture.store.clone())
        .await
        .expect("connect");
    let _ = next_event(&mut events).await; // Connected
    match next_event(&mut events).await {
        ClaudeHostEvent::StoreAppendFailed { scope, error } => {
            assert_eq!(scope, "transcript:project-a/sess-m1");
            assert!(error.contains("JSON objects"), "{error}");
        }
        other => panic!("expected StoreAppendFailed, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::System { payload } => {
            assert_eq!(payload["subtype"], "mirror_error");
        }
        other => panic!("expected System mirror_error, got {other:?}"),
    }
    let _harness = harness.await.expect("harness task");

    assert_eq!(
        fixture
            .store
            .load(&SessionKey::main("project-a", "sess-m1"))
            .expect("load")
            .expect("entries")
            .len(),
        1,
        "the failed batch is dropped and the next batch lands"
    );
    host.shutdown().await;
}

#[tokio::test]
async fn host_restart_reconciles_session_state_after_reconnect() {
    let fixture = open_store();
    let (connector, mut streams) = QueueConnector::scripted(2);
    let second_stream = streams.pop().expect("second scripted stream");
    let first_stream = streams.pop().expect("first scripted stream");
    let key = SessionKey::main("project-a", "sess-r1");

    let first = tokio::spawn(async move {
        let mut harness = FakeHarness::new(first_stream);
        harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        let result = harness
            .store_call(json!({
                "type": "store",
                "id": 31,
                "op": "append",
                "key": {"projectKey": "project-a", "sessionId": "sess-r1"},
                "entries": [
                    entry("user", Some("u1"), None, "sess-r1", "one"),
                    entry("assistant", Some("a1"), Some("u1"), "sess-r1", "two"),
                ],
            }))
            .await;
        assert_eq!(result["ok"], true);
        // Dropping the stream ends the connection mid-session.
    });

    let config = ClaudeHostConfig {
        reconnect: ReconnectPolicy {
            max_attempts: 3,
            initial_delay: Duration::from_millis(10),
            max_delay: Duration::from_millis(50),
        },
        ..test_config()
    };
    let (host, mut events) = ClaudeHost::connect(connector, config, fixture.store.clone())
        .await
        .expect("connect");
    let _ = next_event(&mut events).await; // Connected epoch 1
    first.await.expect("first harness");

    // Clobber the cursor while the host is down, as a crash between entity
    // and cursor writes would.
    fixture
        .state
        .put_adapter_cursor(AdapterCursorInput {
            adapter: crate::CLAUDE_CURSOR_ADAPTER.to_string(),
            scope: transcript_scope(&key),
            cursor: "bogus".to_string(),
            etag: None,
            watermark: Some("1".to_string()),
            expected_revision: Some(1),
            metadata: json!({}),
        })
        .expect("clobber cursor");

    let second = tokio::spawn(async move {
        let mut harness = FakeHarness::new(second_stream);
        harness.hello(PINNED_CLAUDE_CODE_VERSION).await;
        harness
    });

    // Disconnected for epoch 1, Connected for epoch 2, then Reconciled.
    match next_event(&mut events).await {
        ClaudeHostEvent::Disconnected { epoch, .. } => assert_eq!(epoch, 1),
        other => panic!("expected Disconnected, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::Connected { epoch, .. } => assert_eq!(epoch, 2),
        other => panic!("expected Connected, got {other:?}"),
    }
    match next_event(&mut events).await {
        ClaudeHostEvent::Reconciled { epoch, report } => {
            assert_eq!(epoch, 2);
            let entry = report
                .iter()
                .find(|entry| entry.scope == transcript_scope(&key))
                .expect("report covers the transcript");
            assert_eq!(entry.entry_count, 2);
            assert!(entry.repaired, "the stale cursor converged");
        }
        other => panic!("expected Reconciled, got {other:?}"),
    }
    let cursor = fixture
        .store
        .transcript_cursor(&key)
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(cursor.cursor, "a1");
    assert_eq!(cursor.watermark.as_deref(), Some("2"));

    let _harness = second.await.expect("second harness");
    host.shutdown().await;
}

#[test]
fn harness_frames_parse_and_encode() {
    let hello = crate::parse_harness_frame(
        &json!({
            "type": "hello",
            "protocol": HOST_PROTOCOL_VERSION,
            "sdkPackage": "@anthropic-ai/claude-agent-sdk",
            "sdkVersion": "2.1.223",
        })
        .to_string(),
    )
    .expect("hello parses");
    match hello {
        crate::HarnessFrame::Hello(hello) => {
            assert_eq!(hello.sdk_version.as_deref(), Some("2.1.223"));
        }
        other => panic!("expected hello, got {other:?}"),
    }
    let store = crate::parse_harness_frame(
        &json!({
            "type": "store",
            "id": 5,
            "op": "list_subkeys",
            "projectKey": "p",
            "sessionId": "s",
        })
        .to_string(),
    )
    .expect("store parses");
    match store {
        crate::HarnessFrame::Store(frame) => {
            assert_eq!(frame.id, 5);
            assert!(matches!(frame.op, crate::StoreOp::ListSubkeys { .. }));
        }
        other => panic!("expected store, got {other:?}"),
    }
    assert!(crate::parse_harness_frame("{\"type\":\"unknown\"}").is_err());

    let ack: Value = serde_json::from_str(&crate::encode_hello_ack("read_only", Some("why")))
        .expect("ack encodes");
    assert_eq!(ack["mode"], "read_only");
    assert_eq!(ack["refusal"], "why");
    let result: Value =
        serde_json::from_str(&crate::encode_store_result(3, Err("boom"))).expect("result encodes");
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "boom");
}
