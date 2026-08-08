use crate::{
    AdapterMode, ApprovalDecision, ClientInfo, CodexClient, CodexClientConfig, CodexClientError,
    CodexConnection, CodexConnector, CodexCursorStore, CodexEvent, CodexNotification,
    CodexServerRequest, CursorUpdate, InitializeCapabilities, InitializeParams,
    PINNED_CODEX_VERSION, ReconnectPolicy, THREAD_LIST_SCOPE, ThreadForkParams, ThreadListParams,
    ThreadReadParams, ThreadResumeParams, ThreadStartParams, TurnInterruptParams, TurnStartParams,
    TurnSteerParams, UserInput,
    client::BoxFuture,
    evaluate_adapter_mode, method_is_read_only, parse_version_output,
    protocol::{
        METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL, METHOD_FILE_CHANGE_REQUEST_APPROVAL,
        METHOD_INITIALIZE, METHOD_THREAD_ARCHIVE, METHOD_THREAD_COMPACT_START, METHOD_THREAD_FORK,
        METHOD_THREAD_LIST, METHOD_THREAD_READ, METHOD_THREAD_RESUME, METHOD_THREAD_START,
        METHOD_TURN_INTERRUPT, METHOD_TURN_START, METHOD_TURN_STEER, NOTIFICATION_ERROR,
        NOTIFICATION_ITEM_COMPLETED, NOTIFICATION_THREAD_ARCHIVED, NOTIFICATION_THREAD_COMPACTED,
        NOTIFICATION_THREAD_STARTED, NOTIFICATION_TURN_COMPLETED, NOTIFICATION_TURN_STARTED,
        ThreadIdParams, encode_error_response, encode_notification, encode_request, parse_incoming,
    },
    thread_scope, vendored_schema_manifest,
};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use soleaux_state::StateStore;
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

static VENDORED: &[(&str, &str)] = &[
    (
        "json/ApplyPatchApprovalParams.json",
        include_str!("../schema/json/ApplyPatchApprovalParams.json"),
    ),
    (
        "json/ApplyPatchApprovalResponse.json",
        include_str!("../schema/json/ApplyPatchApprovalResponse.json"),
    ),
    (
        "json/AttestationGenerateParams.json",
        include_str!("../schema/json/AttestationGenerateParams.json"),
    ),
    (
        "json/AttestationGenerateResponse.json",
        include_str!("../schema/json/AttestationGenerateResponse.json"),
    ),
    (
        "json/ChatgptAuthTokensRefreshParams.json",
        include_str!("../schema/json/ChatgptAuthTokensRefreshParams.json"),
    ),
    (
        "json/ChatgptAuthTokensRefreshResponse.json",
        include_str!("../schema/json/ChatgptAuthTokensRefreshResponse.json"),
    ),
    (
        "json/ClientNotification.json",
        include_str!("../schema/json/ClientNotification.json"),
    ),
    (
        "json/ClientRequest.json",
        include_str!("../schema/json/ClientRequest.json"),
    ),
    (
        "json/CommandExecutionRequestApprovalParams.json",
        include_str!("../schema/json/CommandExecutionRequestApprovalParams.json"),
    ),
    (
        "json/CommandExecutionRequestApprovalResponse.json",
        include_str!("../schema/json/CommandExecutionRequestApprovalResponse.json"),
    ),
    (
        "json/DynamicToolCallParams.json",
        include_str!("../schema/json/DynamicToolCallParams.json"),
    ),
    (
        "json/DynamicToolCallResponse.json",
        include_str!("../schema/json/DynamicToolCallResponse.json"),
    ),
    (
        "json/ExecCommandApprovalParams.json",
        include_str!("../schema/json/ExecCommandApprovalParams.json"),
    ),
    (
        "json/ExecCommandApprovalResponse.json",
        include_str!("../schema/json/ExecCommandApprovalResponse.json"),
    ),
    (
        "json/FileChangeRequestApprovalParams.json",
        include_str!("../schema/json/FileChangeRequestApprovalParams.json"),
    ),
    (
        "json/FileChangeRequestApprovalResponse.json",
        include_str!("../schema/json/FileChangeRequestApprovalResponse.json"),
    ),
    (
        "json/FuzzyFileSearchParams.json",
        include_str!("../schema/json/FuzzyFileSearchParams.json"),
    ),
    (
        "json/FuzzyFileSearchResponse.json",
        include_str!("../schema/json/FuzzyFileSearchResponse.json"),
    ),
    (
        "json/FuzzyFileSearchSessionCompletedNotification.json",
        include_str!("../schema/json/FuzzyFileSearchSessionCompletedNotification.json"),
    ),
    (
        "json/FuzzyFileSearchSessionUpdatedNotification.json",
        include_str!("../schema/json/FuzzyFileSearchSessionUpdatedNotification.json"),
    ),
    (
        "json/JSONRPCError.json",
        include_str!("../schema/json/JSONRPCError.json"),
    ),
    (
        "json/JSONRPCErrorError.json",
        include_str!("../schema/json/JSONRPCErrorError.json"),
    ),
    (
        "json/JSONRPCMessage.json",
        include_str!("../schema/json/JSONRPCMessage.json"),
    ),
    (
        "json/JSONRPCNotification.json",
        include_str!("../schema/json/JSONRPCNotification.json"),
    ),
    (
        "json/JSONRPCRequest.json",
        include_str!("../schema/json/JSONRPCRequest.json"),
    ),
    (
        "json/JSONRPCResponse.json",
        include_str!("../schema/json/JSONRPCResponse.json"),
    ),
    (
        "json/McpServerElicitationRequestParams.json",
        include_str!("../schema/json/McpServerElicitationRequestParams.json"),
    ),
    (
        "json/McpServerElicitationRequestResponse.json",
        include_str!("../schema/json/McpServerElicitationRequestResponse.json"),
    ),
    (
        "json/PermissionsRequestApprovalParams.json",
        include_str!("../schema/json/PermissionsRequestApprovalParams.json"),
    ),
    (
        "json/PermissionsRequestApprovalResponse.json",
        include_str!("../schema/json/PermissionsRequestApprovalResponse.json"),
    ),
    (
        "json/RequestId.json",
        include_str!("../schema/json/RequestId.json"),
    ),
    (
        "json/ServerNotification.json",
        include_str!("../schema/json/ServerNotification.json"),
    ),
    (
        "json/ServerRequest.json",
        include_str!("../schema/json/ServerRequest.json"),
    ),
    (
        "json/ToolRequestUserInputParams.json",
        include_str!("../schema/json/ToolRequestUserInputParams.json"),
    ),
    (
        "json/ToolRequestUserInputResponse.json",
        include_str!("../schema/json/ToolRequestUserInputResponse.json"),
    ),
    (
        "json/codex_app_server_protocol.schemas.json",
        include_str!("../schema/json/codex_app_server_protocol.schemas.json"),
    ),
    (
        "json/codex_app_server_protocol.v2.schemas.json",
        include_str!("../schema/json/codex_app_server_protocol.v2.schemas.json"),
    ),
    (
        "json/v1/InitializeParams.json",
        include_str!("../schema/json/v1/InitializeParams.json"),
    ),
    (
        "json/v1/InitializeResponse.json",
        include_str!("../schema/json/v1/InitializeResponse.json"),
    ),
];

fn vendored(path: &str) -> Value {
    let content = VENDORED
        .iter()
        .find(|(candidate, _)| *candidate == path)
        .map(|(_, content)| *content)
        .unwrap_or_else(|| panic!("vendored schema {path} is missing"));
    serde_json::from_str(content).unwrap_or_else(|error| panic!("parsing {path}: {error}"))
}

fn method_string(variant: &Value) -> Option<String> {
    let method = variant.get("properties")?.get("method")?;
    method
        .get("const")
        .and_then(Value::as_str)
        .or_else(|| {
            method
                .get("enum")
                .and_then(Value::as_array)
                .and_then(|values| values.first())
                .and_then(Value::as_str)
        })
        .map(ToOwned::to_owned)
}

/// Resolve one union variant's params definition: `(properties, required)`.
fn union_params_contract(document: &Value, method: &str) -> (Map<String, Value>, Vec<String>) {
    let variant = document["oneOf"]
        .as_array()
        .expect("union oneOf")
        .iter()
        .find(|variant| method_string(variant).as_deref() == Some(method))
        .unwrap_or_else(|| panic!("method {method} is not in the vendored union"));
    let params = &variant["properties"]["params"];
    let definition = resolve_ref(document, params);
    contract_of(&definition)
}

fn resolve_ref(document: &Value, node: &Value) -> Value {
    match node.get("$ref").and_then(Value::as_str) {
        Some(reference) => {
            let name = reference
                .rsplit('/')
                .next()
                .expect("reference has a terminal segment");
            document["definitions"][name].clone()
        }
        None => node.clone(),
    }
}

fn contract_of(definition: &Value) -> (Map<String, Value>, Vec<String>) {
    let properties = definition
        .get("properties")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let required = definition
        .get("required")
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default();
    (properties, required)
}

fn assert_params_conform(document: &Value, method: &str, sample: &Value) {
    let (properties, required) = union_params_contract(document, method);
    let object = sample.as_object().expect("sample params are an object");
    for key in object.keys() {
        assert!(
            properties.contains_key(key),
            "{method} serializes {key}, which the vendored schema does not declare"
        );
    }
    for key in &required {
        assert!(
            object.contains_key(key),
            "{method} omits {key}, which the vendored schema requires"
        );
    }
}

fn full_thread_sample() -> Value {
    json!({
        "id": "thread-1",
        "cliVersion": PINNED_CODEX_VERSION,
        "createdAt": 1_700_000_000,
        "updatedAt": 1_700_000_100,
        "cwd": "/workspace",
        "ephemeral": false,
        "modelProvider": "openai",
        "preview": "first user message",
        "sessionId": "session-1",
        "source": {"type": "local"},
        "status": {"type": "idle"},
        "turns": [],
    })
}

fn full_turn_sample(status: &str, completed_at: Option<i64>) -> Value {
    json!({
        "id": "turn-1",
        "items": [],
        "status": status,
        "startedAt": 1_700_000_000,
        "completedAt": completed_at,
    })
}

#[test]
fn vendored_manifest_digests_match_the_vendored_files() {
    let manifest = vendored_schema_manifest().expect("embedded manifest");
    assert_eq!(manifest.file_count, 39);
    assert_eq!(manifest.tag, "rust-v0.146.1");
    assert_eq!(manifest.pinned_codex_version, PINNED_CODEX_VERSION);
    let vendored_paths: BTreeSet<&str> = VENDORED.iter().map(|(path, _)| *path).collect();
    let manifest_paths: BTreeSet<&str> = manifest
        .files
        .iter()
        .map(|file| file.path.as_str())
        .collect();
    assert_eq!(vendored_paths, manifest_paths);
    for file in &manifest.files {
        let (_, content) = VENDORED
            .iter()
            .find(|(path, _)| *path == file.path)
            .expect("manifest path is vendored");
        assert_eq!(content.len(), file.bytes, "{} byte count", file.path);
        let digest = format!("{:x}", Sha256::digest(content.as_bytes()));
        assert_eq!(digest, file.sha256, "{} digest", file.path);
    }
}

#[test]
fn outgoing_request_params_conform_to_the_vendored_client_request_schema() {
    let document = vendored("json/ClientRequest.json");
    let initialize = InitializeParams {
        client_info: ClientInfo {
            name: "soleaux".to_string(),
            title: Some("Soleaux Codex adapter".to_string()),
            version: "0.4.0-dev.5".to_string(),
        },
        capabilities: Some(InitializeCapabilities {
            experimental_api: Some(false),
            opt_out_notification_methods: Some(vec!["thread/started".to_string()]),
        }),
    };
    let samples: Vec<(&str, Value)> = vec![
        (METHOD_INITIALIZE, to_value(&initialize)),
        (
            METHOD_THREAD_START,
            to_value(&ThreadStartParams {
                cwd: Some("/workspace".to_string()),
                model: Some("gpt-5.1-codex".to_string()),
                approval_policy: Some("on-request".to_string()),
                sandbox: Some("workspace-write".to_string()),
                ephemeral: Some(false),
            }),
        ),
        (
            METHOD_THREAD_RESUME,
            to_value(&ThreadResumeParams {
                thread_id: "thread-1".to_string(),
                cwd: Some("/workspace".to_string()),
                model: Some("gpt-5.1-codex".to_string()),
                approval_policy: Some("on-request".to_string()),
                sandbox: Some("workspace-write".to_string()),
            }),
        ),
        (
            METHOD_THREAD_FORK,
            to_value(&ThreadForkParams {
                thread_id: "thread-1".to_string(),
                last_turn_id: Some("turn-1".to_string()),
                ephemeral: Some(false),
            }),
        ),
        (
            METHOD_THREAD_LIST,
            to_value(&ThreadListParams {
                cursor: Some("cursor-1".to_string()),
                limit: Some(10),
                archived: Some(false),
            }),
        ),
        (
            METHOD_THREAD_READ,
            to_value(&ThreadReadParams {
                thread_id: "thread-1".to_string(),
                include_turns: Some(true),
            }),
        ),
        (
            METHOD_THREAD_ARCHIVE,
            to_value(&ThreadIdParams {
                thread_id: "thread-1".to_string(),
            }),
        ),
        (
            METHOD_THREAD_COMPACT_START,
            to_value(&ThreadIdParams {
                thread_id: "thread-1".to_string(),
            }),
        ),
        (
            METHOD_TURN_START,
            to_value(&TurnStartParams {
                thread_id: "thread-1".to_string(),
                input: vec![UserInput::text("hello")],
                cwd: Some("/workspace".to_string()),
                model: Some("gpt-5.1-codex".to_string()),
                approval_policy: Some("on-request".to_string()),
            }),
        ),
        (
            METHOD_TURN_STEER,
            to_value(&TurnSteerParams {
                thread_id: "thread-1".to_string(),
                expected_turn_id: "turn-1".to_string(),
                input: vec![UserInput::text("also fix the tests")],
            }),
        ),
        (
            METHOD_TURN_INTERRUPT,
            to_value(&TurnInterruptParams {
                thread_id: "thread-1".to_string(),
                turn_id: "turn-1".to_string(),
            }),
        ),
    ];
    for (method, sample) in &samples {
        assert_params_conform(&document, method, sample);
    }

    for (name, sample) in [
        ("ClientInfo", to_value(&initialize.client_info)),
        (
            "InitializeCapabilities",
            to_value(initialize.capabilities.as_ref().expect("capabilities")),
        ),
    ] {
        let (properties, required) = contract_of(&document["definitions"][name]);
        let object = sample.as_object().expect("nested sample object");
        for key in object.keys() {
            assert!(properties.contains_key(key), "{name} declares {key}");
        }
        for key in &required {
            assert!(object.contains_key(key), "{name} requires {key}");
        }
    }

    let text_input = to_value(&UserInput::text("hello"));
    let text_variant = document["definitions"]["UserInput"]["oneOf"]
        .as_array()
        .expect("UserInput oneOf")
        .iter()
        .find(|variant| variant["title"] == "TextUserInput")
        .expect("TextUserInput variant");
    let (properties, required) = contract_of(text_variant);
    let object = text_input.as_object().expect("text input object");
    for key in object.keys() {
        assert!(properties.contains_key(key), "TextUserInput declares {key}");
    }
    for key in &required {
        assert!(object.contains_key(key), "TextUserInput requires {key}");
    }
}

#[test]
fn approval_methods_params_and_decisions_conform_to_the_vendored_schemas() {
    let server_request = vendored("json/ServerRequest.json");
    for method in [
        METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL,
        METHOD_FILE_CHANGE_REQUEST_APPROVAL,
    ] {
        union_params_contract(&server_request, method);
    }

    let required_params = json!({
        "itemId": "item-1",
        "threadId": "thread-1",
        "turnId": "turn-1",
        "startedAtMs": 1_700_000_000_000_i64,
    });
    match parse_server_request(METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL, &required_params) {
        CodexServerRequest::CommandExecutionApproval(params) => {
            assert_eq!(params.thread_id, "thread-1");
            assert_eq!(params.started_at_ms, 1_700_000_000_000);
        }
        other => panic!("expected a command approval, got {other:?}"),
    }
    match parse_server_request(METHOD_FILE_CHANGE_REQUEST_APPROVAL, &required_params) {
        CodexServerRequest::FileChangeApproval(params) => {
            assert_eq!(params.item_id, "item-1");
        }
        other => panic!("expected a file-change approval, got {other:?}"),
    }

    for schema_path in [
        "json/CommandExecutionRequestApprovalResponse.json",
        "json/FileChangeRequestApprovalResponse.json",
    ] {
        let document = vendored(schema_path);
        let decision_definition = document["definitions"]
            .as_object()
            .expect("decision definitions")
            .values()
            .find(|definition| definition.get("oneOf").is_some())
            .expect("decision union");
        let allowed: BTreeSet<String> = decision_definition["oneOf"]
            .as_array()
            .expect("decision variants")
            .iter()
            .filter_map(|variant| variant.get("enum"))
            .filter_map(Value::as_array)
            .flatten()
            .filter_map(Value::as_str)
            .map(ToOwned::to_owned)
            .collect();
        for decision in [
            ApprovalDecision::Accept,
            ApprovalDecision::AcceptForSession,
            ApprovalDecision::Decline,
            ApprovalDecision::Cancel,
        ] {
            let encoded = serde_json::to_value(decision).expect("decision encodes");
            let text = encoded.as_str().expect("decision is a string");
            assert!(
                allowed.contains(text),
                "{schema_path} does not allow decision {text}"
            );
        }
        let (properties, required) = contract_of(&document);
        assert!(properties.contains_key("decision"));
        assert_eq!(required, vec!["decision".to_string()]);
    }
}

#[test]
fn notification_payloads_conform_to_the_vendored_server_notification_schema() {
    let document = vendored("json/ServerNotification.json");

    for (definition_name, sample) in [
        ("Thread", full_thread_sample()),
        ("Turn", full_turn_sample("completed", Some(1_700_000_050))),
    ] {
        let (_, required) = contract_of(&document["definitions"][definition_name]);
        assert!(!required.is_empty(), "{definition_name} requires fields");
        let object = sample.as_object().expect("sample object");
        for key in &required {
            assert!(
                object.contains_key(key),
                "the {definition_name} sample omits required {key}"
            );
        }
    }

    let cases: Vec<(&str, Value)> = vec![
        (
            NOTIFICATION_THREAD_STARTED,
            json!({"thread": full_thread_sample()}),
        ),
        (
            NOTIFICATION_TURN_STARTED,
            json!({"threadId": "thread-1", "turn": full_turn_sample("inProgress", None)}),
        ),
        (
            NOTIFICATION_TURN_COMPLETED,
            json!({
                "threadId": "thread-1",
                "turn": full_turn_sample("completed", Some(1_700_000_050)),
            }),
        ),
        (
            NOTIFICATION_ITEM_COMPLETED,
            json!({
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "id": "item-1", "text": "done"},
                "completedAtMs": 1_700_000_000_000_i64,
            }),
        ),
        (
            NOTIFICATION_THREAD_COMPACTED,
            json!({"threadId": "thread-1", "turnId": "turn-2"}),
        ),
        (
            NOTIFICATION_THREAD_ARCHIVED,
            json!({"threadId": "thread-1"}),
        ),
        (
            NOTIFICATION_ERROR,
            json!({
                "threadId": "thread-1",
                "turnId": "turn-1",
                "error": {"message": "boom"},
                "willRetry": false,
            }),
        ),
    ];
    for (method, sample) in &cases {
        let (_, required) = union_params_contract(&document, method);
        let object = sample.as_object().expect("notification sample object");
        for key in &required {
            assert!(
                object.contains_key(key),
                "{method} sample omits required {key}"
            );
        }
        let notification = parse_notification(method, sample);
        assert!(
            !matches!(notification, CodexNotification::Other { .. }),
            "{method} parsed as Other: {notification:?}"
        );
    }

    let passthrough = parse_notification("model/rerouted", &json!({"anything": true}));
    match passthrough {
        CodexNotification::Other { method, .. } => assert_eq!(method, "model/rerouted"),
        other => panic!("expected passthrough, got {other:?}"),
    }
}

#[test]
fn json_rpc_envelope_matches_the_vendored_schemas_and_omits_the_jsonrpc_header() {
    let encoded = encode_request(7, METHOD_THREAD_LIST, &json!({})).expect("encode");
    let frame: Value = serde_json::from_str(&encoded).expect("frame parses");
    let object = frame.as_object().expect("frame object");
    let keys: BTreeSet<&str> = object.keys().map(String::as_str).collect();
    assert_eq!(keys, BTreeSet::from(["id", "method", "params"]));
    assert!(!object.contains_key("jsonrpc"));
    let (_, required) = contract_of(&vendored("json/JSONRPCRequest.json"));
    for key in &required {
        assert!(object.contains_key(key), "request envelope requires {key}");
    }

    let notification: Value =
        serde_json::from_str(&encode_notification("initialized")).expect("notification parses");
    assert_eq!(notification, json!({"method": "initialized"}));

    let error = encode_error_response(&crate::protocol::RequestId::Number(9), -32601, "no")
        .expect("encode error");
    let error_frame: Value = serde_json::from_str(&error).expect("error frame parses");
    let (_, required) = contract_of(&vendored("json/JSONRPCError.json"));
    for key in &required {
        assert!(
            error_frame
                .as_object()
                .expect("error object")
                .contains_key(key),
            "error envelope requires {key}"
        );
    }
    assert_eq!(error_frame["error"]["code"], -32601);
}

#[test]
fn version_policy_fails_closed() {
    assert_eq!(
        parse_version_output("codex-cli 0.146.1\n"),
        Some("0.146.1".to_string())
    );
    assert_eq!(parse_version_output("0.146.1"), Some("0.146.1".to_string()));
    assert_eq!(parse_version_output("codex-cli nightly"), None);
    assert_eq!(parse_version_output(""), None);
    assert_eq!(parse_version_output("codex-cli 1.2"), None);

    let evidence = "a".repeat(64);
    assert!(matches!(
        evaluate_adapter_mode(Some(PINNED_CODEX_VERSION), Some(&evidence)),
        AdapterMode::Mutating { .. }
    ));
    assert!(matches!(
        evaluate_adapter_mode(None, Some(&evidence)),
        AdapterMode::ReadOnly { .. }
    ));
    assert!(matches!(
        evaluate_adapter_mode(Some("0.147.0"), Some(&evidence)),
        AdapterMode::ReadOnly { .. }
    ));
    assert!(matches!(
        evaluate_adapter_mode(Some(PINNED_CODEX_VERSION), None),
        AdapterMode::ReadOnly { .. }
    ));
    assert!(matches!(
        evaluate_adapter_mode(Some(PINNED_CODEX_VERSION), Some("not-a-digest")),
        AdapterMode::ReadOnly { .. }
    ));

    assert!(method_is_read_only(METHOD_THREAD_LIST));
    assert!(method_is_read_only(METHOD_INITIALIZE));
    assert!(!method_is_read_only(METHOD_THREAD_START));
    assert!(!method_is_read_only(METHOD_TURN_START));
    assert!(!method_is_read_only("config/value/write"));
}

#[test]
fn cursor_store_advances_merges_and_marks_archived() {
    let directory = TempDir::new().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let cursors = CodexCursorStore::new(store);

    assert!(cursors.mark_archived("thread-1").expect("no-op").is_none());

    let first = cursors
        .advance(&CursorUpdate {
            scope: thread_scope("thread-1"),
            cursor: "turn-1".to_string(),
            watermark: Some("100".to_string()),
            metadata: json!({"status": "completed"}),
        })
        .expect("first write");
    assert_eq!(first.revision, 1);

    let second = cursors
        .advance(&CursorUpdate {
            scope: thread_scope("thread-1"),
            cursor: "turn-2".to_string(),
            watermark: None,
            metadata: json!({"compacted": true}),
        })
        .expect("second write");
    assert_eq!(second.revision, 2);
    assert_eq!(second.metadata["status"], "completed");
    assert_eq!(second.metadata["compacted"], true);

    let archived = cursors
        .mark_archived("thread-1")
        .expect("archive")
        .expect("existing cursor");
    assert_eq!(archived.cursor, "turn-2");
    assert_eq!(archived.metadata["archived"], true);
    assert_eq!(archived.metadata["compacted"], true);

    let list = cursors
        .advance(&CursorUpdate {
            scope: THREAD_LIST_SCOPE.to_string(),
            cursor: "page-2".to_string(),
            watermark: None,
            metadata: Value::Null,
        })
        .expect("list cursor");
    assert_eq!(list.revision, 1);
}

// --- scripted app-server fixtures -------------------------------------------

struct FakeServer {
    reader: BufReader<ReadHalf<DuplexStream>>,
    writer: WriteHalf<DuplexStream>,
}

impl FakeServer {
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
            .expect("server recv timed out")
            .expect("server read");
        assert!(bytes > 0, "client closed the connection unexpectedly");
        serde_json::from_str(line.trim()).expect("client frame parses")
    }

    async fn send(&mut self, value: &Value) {
        let mut line = serde_json::to_string(value).expect("server frame encodes");
        line.push('\n');
        self.writer
            .write_all(line.as_bytes())
            .await
            .expect("server write");
        self.writer.flush().await.expect("server flush");
    }

    async fn handshake(&mut self) {
        let frame = self.recv().await;
        assert_eq!(frame["method"], "initialize");
        assert_eq!(frame["params"]["clientInfo"]["name"], "soleaux");
        let id = frame["id"].clone();
        self.send(&json!({
            "id": id,
            "result": {
                "userAgent": "codex/0.146.1",
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }))
        .await;
        let initialized = self.recv().await;
        assert_eq!(initialized, json!({"method": "initialized"}));
    }

    /// Expect one request for `method` and answer it with `result`.
    async fn expect_request(&mut self, method: &str, result: Value) -> Value {
        let frame = self.recv().await;
        assert_eq!(frame["method"], method, "unexpected request: {frame}");
        let id = frame["id"].clone();
        self.send(&json!({"id": id, "result": result})).await;
        frame["params"].clone()
    }
}

struct QueueConnector {
    connections: Mutex<VecDeque<CodexConnection>>,
}

impl QueueConnector {
    fn scripted(count: usize) -> (Arc<Self>, Vec<DuplexStream>) {
        let mut connections = VecDeque::new();
        let mut server_sides = Vec::new();
        for _ in 0..count {
            let (client_side, server_side) = tokio::io::duplex(64 * 1024);
            let (reader, writer) = tokio::io::split(client_side);
            connections.push_back(CodexConnection {
                reader: Box::new(reader),
                writer: Box::new(writer),
                child: None,
            });
            server_sides.push(server_side);
        }
        (
            Arc::new(Self {
                connections: Mutex::new(connections),
            }),
            server_sides,
        )
    }
}

impl CodexConnector for QueueConnector {
    fn connect(&self) -> BoxFuture<'_, Result<CodexConnection, CodexClientError>> {
        let next = self.connections.lock().expect("connector lock").pop_front();
        Box::pin(async move {
            next.ok_or_else(|| CodexClientError::Spawn("no scripted connection left".to_string()))
        })
    }
}

fn mutating_config() -> CodexClientConfig {
    CodexClientConfig {
        probed_version: Some(PINNED_CODEX_VERSION.to_string()),
        probe_evidence_sha256: Some("a".repeat(64)),
        reconnect: ReconnectPolicy {
            max_attempts: 0,
            initial_delay: Duration::from_millis(10),
            max_delay: Duration::from_millis(50),
        },
        ..CodexClientConfig::default()
    }
}

async fn next_event(events: &mut mpsc::Receiver<CodexEvent>) -> CodexEvent {
    timeout(Duration::from_secs(5), events.recv())
        .await
        .expect("event timed out")
        .expect("event stream ended early")
}

fn parse_server_request(method: &str, params: &Value) -> CodexServerRequest {
    let frame = json!({"id": 1, "method": method, "params": params});
    match parse_incoming(&frame.to_string()).expect("frame parses") {
        crate::protocol::IncomingMessage::ServerRequest { request, .. } => request,
        other => panic!("expected a server request, got {other:?}"),
    }
}

fn parse_notification(method: &str, params: &Value) -> CodexNotification {
    let frame = json!({"method": method, "params": params});
    match parse_incoming(&frame.to_string()).expect("frame parses") {
        crate::protocol::IncomingMessage::Notification(notification) => notification,
        other => panic!("expected a notification, got {other:?}"),
    }
}

fn to_value<T: serde::Serialize>(value: &T) -> Value {
    serde_json::to_value(value).expect("serializes")
}

#[tokio::test]
async fn thread_turn_compact_archive_flow_records_cursors() {
    let directory = TempDir::new().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let cursors = CodexCursorStore::new(store);
    let (connector, mut servers) = QueueConnector::scripted(1);
    let server_stream = servers.remove(0);

    let server = tokio::spawn(async move {
        let mut server = FakeServer::new(server_stream);
        server.handshake().await;
        let params = server
            .expect_request(METHOD_THREAD_START, json!({"thread": full_thread_sample()}))
            .await;
        assert_eq!(params["cwd"], "/workspace");
        let params = server
            .expect_request(
                METHOD_TURN_START,
                json!({"turn": full_turn_sample("inProgress", None)}),
            )
            .await;
        assert_eq!(params["input"][0], json!({"type": "text", "text": "hello"}));
        server
            .send(&json!({
                "method": NOTIFICATION_TURN_COMPLETED,
                "params": {
                    "threadId": "thread-1",
                    "turn": full_turn_sample("completed", Some(111)),
                },
            }))
            .await;
        server
            .expect_request(METHOD_THREAD_COMPACT_START, json!({}))
            .await;
        server
            .send(&json!({
                "method": NOTIFICATION_THREAD_COMPACTED,
                "params": {"threadId": "thread-1", "turnId": "turn-2"},
            }))
            .await;
        server
            .expect_request(METHOD_THREAD_ARCHIVE, json!({}))
            .await;
        server
            .send(&json!({
                "method": NOTIFICATION_THREAD_ARCHIVED,
                "params": {"threadId": "thread-1"},
            }))
            .await;
    });

    let (client, mut events) =
        CodexClient::connect(connector, mutating_config(), Some(cursors.clone()))
            .await
            .expect("connect");
    assert!(matches!(client.mode(), AdapterMode::Mutating { .. }));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { epoch: 1, .. }
    ));

    let thread = client
        .thread_start(ThreadStartParams {
            cwd: Some("/workspace".to_string()),
            ..ThreadStartParams::default()
        })
        .await
        .expect("thread start");
    assert_eq!(thread.thread.id, "thread-1");
    assert_eq!(client.subscribed_threads(), vec!["thread-1".to_string()]);

    let turn = client
        .turn_start(TurnStartParams {
            thread_id: "thread-1".to_string(),
            input: vec![UserInput::text("hello")],
            cwd: None,
            model: None,
            approval_policy: None,
        })
        .await
        .expect("turn start");
    assert_eq!(turn.turn.id, "turn-1");

    match next_event(&mut events).await {
        CodexEvent::Notification(CodexNotification::TurnCompleted(turn)) => {
            assert_eq!(turn.thread_id, "thread-1");
        }
        other => panic!("expected turn completion, got {other:?}"),
    }
    let cursor = cursors
        .get(&thread_scope("thread-1"))
        .expect("cursor read")
        .expect("cursor recorded before the event was delivered");
    assert_eq!(cursor.cursor, "turn-1");
    assert_eq!(cursor.watermark.as_deref(), Some("111"));
    assert_eq!(cursor.metadata["status"], "completed");

    client
        .thread_compact_start("thread-1")
        .await
        .expect("compaction");
    match next_event(&mut events).await {
        CodexEvent::Notification(CodexNotification::ThreadCompacted(reference)) => {
            assert_eq!(reference.turn_id, "turn-2");
        }
        other => panic!("expected compaction, got {other:?}"),
    }
    let cursor = cursors
        .get(&thread_scope("thread-1"))
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(cursor.cursor, "turn-2");
    assert_eq!(cursor.metadata["compacted"], true);

    client.thread_archive("thread-1").await.expect("archive");
    assert!(client.subscribed_threads().is_empty());
    match next_event(&mut events).await {
        CodexEvent::Notification(CodexNotification::ThreadArchived { thread_id }) => {
            assert_eq!(thread_id, "thread-1");
        }
        other => panic!("expected archive, got {other:?}"),
    }
    let cursor = client
        .thread_cursor("thread-1")
        .await
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(cursor.metadata["archived"], true);

    server.await.expect("server script completed");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Disconnected { epoch: 1, .. }
    ));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Closed { .. }
    ));
}

#[tokio::test]
async fn approvals_round_trip_fail_closed_on_drop_and_reject_unsupported_requests() {
    let (connector, mut servers) = QueueConnector::scripted(1);
    let server_stream = servers.remove(0);

    let server = tokio::spawn(async move {
        let mut server = FakeServer::new(server_stream);
        server.handshake().await;
        server
            .send(&json!({
                "id": "approval-1",
                "method": METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL,
                "params": {
                    "itemId": "item-1",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "startedAtMs": 1,
                    "command": ["git", "status"],
                },
            }))
            .await;
        let response = server.recv().await;
        assert_eq!(
            response,
            json!({"id": "approval-1", "result": {"decision": "accept"}})
        );
        server
            .send(&json!({
                "id": 7,
                "method": METHOD_FILE_CHANGE_REQUEST_APPROVAL,
                "params": {
                    "itemId": "item-2",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "startedAtMs": 2,
                },
            }))
            .await;
        let response = server.recv().await;
        assert_eq!(response, json!({"id": 7, "result": {"decision": "cancel"}}));
        server
            .send(&json!({"id": 9, "method": "attestation/generate", "params": {}}))
            .await;
        let response = server.recv().await;
        assert_eq!(response["id"], 9);
        assert_eq!(response["error"]["code"], -32601);
    });

    let (client, mut events) = CodexClient::connect(connector, mutating_config(), None)
        .await
        .expect("connect");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { .. }
    ));

    match next_event(&mut events).await {
        CodexEvent::ApprovalRequested(pending) => {
            match pending.request() {
                CodexServerRequest::CommandExecutionApproval(params) => {
                    assert_eq!(params.command, Some(json!(["git", "status"])));
                }
                other => panic!("expected a command approval, got {other:?}"),
            }
            pending.respond(ApprovalDecision::Accept).expect("respond");
        }
        other => panic!("expected an approval, got {other:?}"),
    }

    match next_event(&mut events).await {
        CodexEvent::ApprovalRequested(pending) => drop(pending),
        other => panic!("expected an approval, got {other:?}"),
    }

    match next_event(&mut events).await {
        CodexEvent::UnsupportedServerRequest { method } => {
            assert_eq!(method, "attestation/generate");
        }
        other => panic!("expected an unsupported-request event, got {other:?}"),
    }

    server.await.expect("server script completed");
    client.shutdown().await;
}

#[tokio::test]
async fn safe_mode_refuses_mutations_and_answers_approvals_with_cancel() {
    let (connector, mut servers) = QueueConnector::scripted(1);
    let server_stream = servers.remove(0);

    let server = tokio::spawn(async move {
        let mut server = FakeServer::new(server_stream);
        server.handshake().await;
        server
            .expect_request(METHOD_THREAD_LIST, json!({"data": []}))
            .await;
        server
            .send(&json!({
                "id": 5,
                "method": METHOD_COMMAND_EXECUTION_REQUEST_APPROVAL,
                "params": {
                    "itemId": "item-1",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "startedAtMs": 3,
                },
            }))
            .await;
        let response = server.recv().await;
        assert_eq!(response, json!({"id": 5, "result": {"decision": "cancel"}}));
    });

    let config = CodexClientConfig {
        probed_version: None,
        reconnect: ReconnectPolicy {
            max_attempts: 0,
            initial_delay: Duration::from_millis(10),
            max_delay: Duration::from_millis(50),
        },
        ..CodexClientConfig::default()
    };
    let (client, mut events) = CodexClient::connect(connector, config, None)
        .await
        .expect("connect");
    assert!(matches!(client.mode(), AdapterMode::ReadOnly { .. }));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { .. }
    ));

    let listed = client
        .thread_list(ThreadListParams::default())
        .await
        .expect("read-only listing is allowed");
    assert!(listed.data.is_empty());

    let refused = client
        .thread_start(ThreadStartParams::default())
        .await
        .expect_err("safe mode refuses thread/start");
    assert!(
        matches!(refused, CodexClientError::SafeMode { .. }),
        "got: {refused:?}"
    );
    let refused = client
        .turn_steer(TurnSteerParams {
            thread_id: "thread-1".to_string(),
            expected_turn_id: "turn-1".to_string(),
            input: vec![UserInput::text("steer")],
        })
        .await
        .expect_err("safe mode refuses turn/steer");
    assert!(
        matches!(refused, CodexClientError::SafeMode { .. }),
        "got: {refused:?}"
    );

    match next_event(&mut events).await {
        CodexEvent::ApprovalDenied { request, reason } => {
            assert!(request.is_approval());
            assert!(reason.contains("has not been probed"));
        }
        other => panic!("expected a safe-mode denial, got {other:?}"),
    }

    server.await.expect("server script completed");
    client.shutdown().await;
}

#[tokio::test]
async fn reconnect_resumes_subscribed_threads_and_cursors_survive() {
    let directory = TempDir::new().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let cursors = CodexCursorStore::new(store);
    let (connector, mut servers) = QueueConnector::scripted(2);
    let second_stream = servers.remove(1);
    let first_stream = servers.remove(0);

    let first = tokio::spawn(async move {
        let mut server = FakeServer::new(first_stream);
        server.handshake().await;
        server
            .expect_request(METHOD_THREAD_START, json!({"thread": full_thread_sample()}))
            .await;
        server
            .send(&json!({
                "method": NOTIFICATION_TURN_COMPLETED,
                "params": {
                    "threadId": "thread-1",
                    "turn": full_turn_sample("completed", Some(222)),
                },
            }))
            .await;
    });

    let second = tokio::spawn(async move {
        let mut server = FakeServer::new(second_stream);
        server.handshake().await;
        let params = server
            .expect_request(
                METHOD_THREAD_RESUME,
                json!({"thread": full_thread_sample()}),
            )
            .await;
        assert_eq!(params["threadId"], "thread-1");
    });

    let config = CodexClientConfig {
        reconnect: ReconnectPolicy {
            max_attempts: 3,
            initial_delay: Duration::from_millis(10),
            max_delay: Duration::from_millis(50),
        },
        ..mutating_config()
    };
    let (client, mut events) = CodexClient::connect(connector, config, Some(cursors))
        .await
        .expect("connect");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { epoch: 1, .. }
    ));

    client
        .thread_start(ThreadStartParams::default())
        .await
        .expect("thread start");

    match next_event(&mut events).await {
        CodexEvent::Notification(CodexNotification::TurnCompleted(_)) => {}
        other => panic!("expected turn completion, got {other:?}"),
    }

    first.await.expect("first server script completed");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Disconnected { epoch: 1, .. }
    ));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { epoch: 2, .. }
    ));
    match next_event(&mut events).await {
        CodexEvent::ThreadResumed { epoch, thread_id } => {
            assert_eq!(epoch, 2);
            assert_eq!(thread_id, "thread-1");
        }
        other => panic!("expected an automatic resume, got {other:?}"),
    }
    second.await.expect("second server script completed");

    let cursor = client
        .thread_cursor("thread-1")
        .await
        .expect("cursor read")
        .expect("cursor survived the reconnect");
    assert_eq!(cursor.cursor, "turn-1");
    assert_eq!(cursor.watermark.as_deref(), Some("222"));

    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Disconnected { epoch: 2, .. }
    ));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Closed { .. }
    ));
}

#[tokio::test]
async fn a_new_thread_reporting_a_foreign_cli_version_downgrades_the_client() {
    let (connector, mut servers) = QueueConnector::scripted(1);
    let server_stream = servers.remove(0);

    let server = tokio::spawn(async move {
        let mut server = FakeServer::new(server_stream);
        server.handshake().await;
        let mut thread = full_thread_sample();
        thread["cliVersion"] = json!("0.999.0");
        server
            .expect_request(METHOD_THREAD_START, json!({"thread": thread}))
            .await;
    });

    let (client, mut events) = CodexClient::connect(connector, mutating_config(), None)
        .await
        .expect("connect");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { .. }
    ));

    let drift = client
        .thread_start(ThreadStartParams::default())
        .await
        .expect_err("version drift must fail the call");
    assert!(matches!(drift, CodexClientError::VersionDrift { .. }));
    assert!(matches!(client.mode(), AdapterMode::ReadOnly { .. }));
    assert!(client.subscribed_threads().is_empty());

    let refused = client
        .turn_start(TurnStartParams {
            thread_id: "thread-1".to_string(),
            input: vec![UserInput::text("hello")],
            cwd: None,
            model: None,
            approval_policy: None,
        })
        .await
        .expect_err("the downgraded client refuses mutations");
    assert!(
        matches!(refused, CodexClientError::SafeMode { .. }),
        "got: {refused:?}"
    );

    server.await.expect("server script completed");
    client.shutdown().await;
}

#[tokio::test]
async fn an_oversized_frame_closes_the_connection() {
    let (connector, mut servers) = QueueConnector::scripted(1);
    let server_stream = servers.remove(0);

    let server = tokio::spawn(async move {
        let mut server = FakeServer::new(server_stream);
        server.handshake().await;
        server
            .send(&json!({
                "method": "noise",
                "params": {"padding": "x".repeat(2_000)},
            }))
            .await;
    });

    let config = CodexClientConfig {
        max_frame_bytes: 512,
        ..mutating_config()
    };
    let (_client, mut events) = CodexClient::connect(connector, config, None)
        .await
        .expect("connect");
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Connected { .. }
    ));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Disconnected { .. }
    ));
    assert!(matches!(
        next_event(&mut events).await,
        CodexEvent::Closed { .. }
    ));
    server.await.expect("server script completed");
}
