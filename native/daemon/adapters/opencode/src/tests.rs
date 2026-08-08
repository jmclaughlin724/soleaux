//! Adapter tests: vendored-spec conformance, a wire-accurate fixture server,
//! safe-mode admission gating, and persistent cursor reconciliation.

use crate::adapter::{AdapterError, AdmissionVerifier, IpcAdmissionVerifier, WriteMode};
use crate::client::OpencodeClient;
use crate::events::{EventReconciler, OPENCODE_CURSOR_ADAPTER, cursor_scope};
use crate::spec::load_vendored_spec;
use crate::types::{
    CreateSessionRequest, Event, PermissionReply, RevertRequest, Session, SessionTime,
    SummarizeRequest,
};
use crate::{OPENCODE_PLATFORM_ID, OpencodeAdapter, PINNED_OPENCODE_VERSION};
use serde_json::{Value, json};
use soleaux_ipc::{ADMISSION_RECEIPT_SCHEMA_VERSION, AdmissionReceipt};
use soleaux_state::StateStore;
use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use url::Url;
use uuid::Uuid;

const EVENT_TIMEOUT: Duration = Duration::from_secs(5);

// --- vendored-spec conformance -------------------------------------------

fn spec() -> Value {
    load_vendored_spec().expect("vendored spec loads and matches the pinned digest")
}

fn component<'a>(spec: &'a Value, name: &str) -> &'a Value {
    let schema = &spec["components"]["schemas"][name];
    assert!(schema.is_object(), "spec has no component schema {name}");
    schema
}

fn resolve<'a>(spec: &'a Value, schema: &'a Value) -> &'a Value {
    match schema.get("$ref").and_then(Value::as_str) {
        Some(reference) => {
            let name = reference.rsplit('/').next().expect("ref tail");
            component(spec, name)
        }
        None => schema,
    }
}

fn names(schema: &Value, key: &str) -> BTreeSet<String> {
    match key {
        "properties" => schema["properties"]
            .as_object()
            .map(|properties| properties.keys().cloned().collect())
            .unwrap_or_default(),
        _ => schema[key]
            .as_array()
            .map(|required| {
                required
                    .iter()
                    .filter_map(Value::as_str)
                    .map(ToOwned::to_owned)
                    .collect()
            })
            .unwrap_or_default(),
    }
}

fn serialized_keys<T: serde::Serialize>(sample: &T) -> BTreeSet<String> {
    serde_json::to_value(sample)
        .expect("sample serializes")
        .as_object()
        .expect("sample is an object")
        .keys()
        .cloned()
        .collect()
}

fn assert_covers(sample_keys: &BTreeSet<String>, schema: &Value, context: &str) {
    let properties = names(schema, "properties");
    let required = names(schema, "required");
    for key in sample_keys {
        assert!(
            properties.contains(key),
            "{context}: field {key:?} is not a property in the vendored spec"
        );
    }
    for key in &required {
        assert!(
            sample_keys.contains(key),
            "{context}: spec-required property {key:?} is not represented"
        );
    }
}

fn full_session() -> Session {
    serde_json::from_value(json!({
        "id": "ses_1",
        "slug": "ses-one",
        "projectID": "prj_1",
        "workspaceID": "wrk_1",
        "directory": "/tmp/fixture",
        "path": "/tmp/fixture",
        "parentID": "ses_0",
        "title": "conformance",
        "version": PINNED_OPENCODE_VERSION,
        "time": {"created": 1, "updated": 2, "compacting": 3, "archived": 4.0},
        "agent": "build",
        "revert": {"messageID": "msg_1", "partID": "prt_1", "snapshot": "s", "diff": "d"},
        "metadata": {"origin": "test"},
    }))
    .expect("full session decodes")
}

#[test]
fn implemented_operations_exist_in_the_vendored_spec() {
    let spec = spec();
    let operations = [
        ("get", "/global/health", "global.health"),
        ("get", "/global/event", "global.event"),
        ("get", "/event", "event.subscribe"),
        ("get", "/config", "config.get"),
        ("get", "/session", "session.list"),
        ("get", "/session/{sessionID}", "session.get"),
        ("get", "/session/{sessionID}/children", "session.children"),
        ("get", "/session/{sessionID}/message", "session.messages"),
        ("get", "/permission", "permission.list"),
        ("post", "/session", "session.create"),
        ("post", "/session/{sessionID}/fork", "session.fork"),
        ("post", "/session/{sessionID}/abort", "session.abort"),
        (
            "post",
            "/session/{sessionID}/summarize",
            "session.summarize",
        ),
        ("post", "/session/{sessionID}/revert", "session.revert"),
        ("post", "/session/{sessionID}/unrevert", "session.unrevert"),
        ("post", "/permission/{requestID}/reply", "permission.reply"),
        (
            "post",
            "/session/{sessionID}/permissions/{permissionID}",
            "permission.respond",
        ),
    ];
    for (method, path, operation_id) in operations {
        let declared = spec["paths"][path][method]["operationId"]
            .as_str()
            .unwrap_or_else(|| panic!("spec has no {method} {path}"));
        assert_eq!(declared, operation_id, "{method} {path}");
    }
}

#[test]
fn hand_derived_types_match_the_vendored_schemas() {
    let spec = spec();

    let session_schema = component(&spec, "Session");
    let session = full_session();
    assert_covers(&serialized_keys(&session), session_schema, "Session");
    assert_covers(
        &serialized_keys(&session.time),
        &session_schema["properties"]["time"],
        "Session.time",
    );
    assert_covers(
        &serialized_keys(session.revert.as_ref().expect("revert populated")),
        &session_schema["properties"]["revert"],
        "Session.revert",
    );

    let permission_schema = component(&spec, "PermissionRequest");
    let permission: crate::types::PermissionRequest = serde_json::from_value(json!({
        "id": "per_1",
        "sessionID": "ses_1",
        "permission": "bash",
        "patterns": ["*"],
        "metadata": {},
        "always": [],
        "tool": {"messageID": "msg_1", "callID": "call_1"},
    }))
    .expect("permission decodes");
    assert_covers(
        &serialized_keys(&permission),
        permission_schema,
        "PermissionRequest",
    );
    assert_covers(
        &serialized_keys(permission.tool.as_ref().expect("tool populated")),
        &permission_schema["properties"]["tool"],
        "PermissionRequest.tool",
    );

    let health_schema = &spec["paths"]["/global/health"]["get"]["responses"]["200"]["content"]["application/json"]
        ["schema"];
    let health = crate::types::HealthInfo {
        healthy: true,
        version: PINNED_OPENCODE_VERSION.to_string(),
    };
    assert_covers(&serialized_keys(&health), health_schema, "HealthInfo");

    let envelope_schema = &spec["paths"]["/session/{sessionID}/message"]["get"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["items"];
    let envelope = crate::types::MessageEnvelope {
        info: crate::types::MessageInfo {
            id: "msg_1".into(),
            session_id: "ses_1".into(),
            role: "user".into(),
            agent: "build".into(),
            time: json!({"created": 1}),
        },
        parts: Vec::new(),
    };
    assert_covers(
        &serialized_keys(&envelope),
        envelope_schema,
        "MessageEnvelope",
    );

    // Message and Part are unions: our common structs must stay inside the
    // intersection of every arm and cover every arm-shared required field.
    for (name, keys) in [
        ("Message", serialized_keys(&envelope.info)),
        (
            "Part",
            serialized_keys(&crate::types::Part {
                id: "prt_1".into(),
                session_id: "ses_1".into(),
                message_id: "msg_1".into(),
                part_type: "text".into(),
            }),
        ),
    ] {
        let arms = component(&spec, name)["anyOf"]
            .as_array()
            .unwrap_or_else(|| panic!("{name} is not a union"));
        let mut common_properties: Option<BTreeSet<String>> = None;
        let mut common_required: Option<BTreeSet<String>> = None;
        for arm in arms {
            let arm = resolve(&spec, arm);
            let properties = names(arm, "properties");
            let required = names(arm, "required");
            common_properties = Some(match common_properties {
                Some(existing) => existing.intersection(&properties).cloned().collect(),
                None => properties,
            });
            common_required = Some(match common_required {
                Some(existing) => existing.intersection(&required).cloned().collect(),
                None => required,
            });
        }
        let common_properties = common_properties.expect("union arms");
        let common_required = common_required.expect("union arms");
        for key in &keys {
            assert!(
                common_properties.contains(key),
                "{name}: field {key:?} is not shared by every union arm"
            );
        }
        for key in &common_required {
            assert!(
                keys.contains(key),
                "{name}: arm-shared required property {key:?} is not represented"
            );
        }
    }

    assert!(
        names(component(&spec, "Config"), "properties").contains("plugin"),
        "Config.plugin left the spec"
    );
}

#[test]
fn request_bodies_match_the_vendored_schemas() {
    let spec = spec();
    let body_schema = |path: &str| {
        &spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
    };

    let create = CreateSessionRequest {
        parent_id: Some("ses_0".into()),
        title: Some("t".into()),
        metadata: Some(serde_json::Map::new()),
    };
    assert_covers(
        &serialized_keys(&create),
        body_schema("/session"),
        "session.create body",
    );

    let summarize = SummarizeRequest {
        provider_id: "anthropic".into(),
        model_id: "claude".into(),
        auto: Some(false),
    };
    assert_covers(
        &serialized_keys(&summarize),
        body_schema("/session/{sessionID}/summarize"),
        "session.summarize body",
    );

    let revert = RevertRequest {
        message_id: "msg_1".into(),
        part_id: Some("prt_1".into()),
    };
    assert_covers(
        &serialized_keys(&revert),
        body_schema("/session/{sessionID}/revert"),
        "session.revert body",
    );

    // Inline bodies assembled in the client.
    assert!(names(body_schema("/session/{sessionID}/fork"), "properties").contains("messageID"));
    let reply_properties = names(body_schema("/permission/{requestID}/reply"), "properties");
    assert!(reply_properties.contains("reply") && reply_properties.contains("message"));
    assert!(
        names(
            body_schema("/session/{sessionID}/permissions/{permissionID}"),
            "properties"
        )
        .contains("response")
    );
    assert_eq!(
        serde_json::to_value(PermissionReply::Once).expect("reply"),
        json!("once")
    );
}

#[test]
fn typed_event_kinds_exist_in_the_vendored_event_union() {
    let spec = spec();
    let mut spec_tags = BTreeSet::new();
    for arm in component(&spec, "Event")["anyOf"]
        .as_array()
        .expect("union")
    {
        let arm = resolve(&spec, arm);
        if let Some(tag) = arm["properties"]["type"]["enum"][0].as_str() {
            spec_tags.insert(tag.to_string());
        }
    }
    for tag in [
        "server.connected",
        "session.created",
        "session.updated",
        "session.deleted",
        "session.idle",
        "session.compacted",
        "session.error",
        "message.updated",
        "permission.asked",
        "permission.replied",
        "plugin.added",
    ] {
        assert!(spec_tags.contains(tag), "spec Event union lost {tag}");
    }
}

#[test]
fn pinned_version_matches_the_embedded_capability_matrix() {
    let matrix: Value =
        serde_json::from_str(soleaux_ipc::CLIENT_CAPABILITY_MATRIX_JSON).expect("matrix parses");
    let platform = matrix["platforms"]
        .as_array()
        .expect("platforms")
        .iter()
        .find(|platform| platform["id"] == OPENCODE_PLATFORM_ID)
        .expect("matrix has an opencode platform");
    let versions = platform["versions"].as_array().expect("versions");
    assert_eq!(versions.len(), 1, "opencode pins exactly one version");
    assert_eq!(versions[0]["version"], PINNED_OPENCODE_VERSION);
    assert_eq!(versions[0]["mutationEligible"], false);
    let asset_digest = versions[0]["linuxX64Asset"]["sha256"]
        .as_str()
        .expect("pinned release asset digest");
    assert_eq!(asset_digest.len(), 64);
}

// --- wire fixture ---------------------------------------------------------

struct Fixture {
    base: Url,
    mutations: Arc<Mutex<Vec<String>>>,
}

impl Fixture {
    fn recorded_mutations(&self) -> Vec<String> {
        self.mutations.lock().expect("mutation log").clone()
    }
}

async fn spawn_fixture(version: &str) -> Fixture {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("fixture bind");
    let port = listener.local_addr().expect("fixture address").port();
    let mutations = Arc::new(Mutex::new(Vec::new()));
    let recorded = mutations.clone();
    let version = version.to_string();
    tokio::spawn(async move {
        loop {
            let Ok((stream, _)) = listener.accept().await else {
                break;
            };
            let version = version.clone();
            let recorded = recorded.clone();
            tokio::spawn(async move {
                let outcome = handle_connection(stream, &version, &recorded).await;
                drop(outcome);
            });
        }
    });
    Fixture {
        base: Url::parse(&format!("http://127.0.0.1:{port}")).expect("fixture url"),
        mutations,
    }
}

async fn read_request(stream: &mut TcpStream) -> anyhow::Result<(String, String, String)> {
    let mut raw = Vec::new();
    let mut chunk = [0_u8; 4096];
    let head_end = loop {
        if let Some(position) = raw.windows(4).position(|window| window == b"\r\n\r\n") {
            break position;
        }
        let read = stream.read(&mut chunk).await?;
        if read == 0 {
            anyhow::bail!("client closed before a complete request head");
        }
        raw.extend_from_slice(&chunk[..read]);
    };
    let head = String::from_utf8(raw[..head_end].to_vec())?;
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or_default();
    let mut parts = request_line.split(' ');
    let method = parts.next().unwrap_or_default().to_string();
    let target = parts.next().unwrap_or_default().to_string();
    let mut content_length = 0_usize;
    for line in lines {
        if let Some((name, value)) = line.split_once(':')
            && name.trim().eq_ignore_ascii_case("content-length")
        {
            content_length = value.trim().parse().unwrap_or(0);
        }
    }
    let mut body = raw[head_end + 4..].to_vec();
    while body.len() < content_length {
        let read = stream.read(&mut chunk).await?;
        if read == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..read]);
    }
    Ok((method, target, String::from_utf8_lossy(&body).into_owned()))
}

async fn respond_json(stream: &mut TcpStream, status: u16, value: &Value) -> anyhow::Result<()> {
    let body = value.to_string();
    let head = format!(
        "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(head.as_bytes()).await?;
    stream.write_all(body.as_bytes()).await?;
    Ok(())
}

/// Content-length-free response: the body ends when the server closes the
/// connection, exercising the client's read-to-EOF framing.
async fn respond_json_eof(stream: &mut TcpStream, value: &Value) -> anyhow::Result<()> {
    let head = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\nconnection: close\r\n\r\n";
    stream.write_all(head.as_bytes()).await?;
    stream.write_all(value.to_string().as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}

fn chunk(data: &[u8]) -> Vec<u8> {
    let mut wire = format!("{:x}\r\n", data.len()).into_bytes();
    wire.extend_from_slice(data);
    wire.extend_from_slice(b"\r\n");
    wire
}

async fn respond_sse(stream: &mut TcpStream) -> anyhow::Result<()> {
    let head =
        "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n";
    stream.write_all(head.as_bytes()).await?;
    let connected = json!({"id": "evt_1", "type": "server.connected", "properties": {}});
    stream
        .write_all(&chunk(format!("data: {connected}\n\n").as_bytes()))
        .await?;
    // One frame split across two chunks: the decoder must reassemble it.
    let updated = json!({
        "id": "evt_2",
        "type": "session.updated",
        "properties": {"sessionID": "ses_1", "info": session_value("ses_1", None, 2000)},
    });
    let frame = format!(": keepalive\ndata: {updated}\n\n");
    let (first, second) = frame.as_bytes().split_at(frame.len() / 2);
    stream.write_all(&chunk(first)).await?;
    stream.write_all(&chunk(second)).await?;
    let unknown = json!({"id": "evt_3", "type": "myplugin.custom", "properties": {"marker": 7}});
    stream
        .write_all(&chunk(format!("data: {unknown}\n\n").as_bytes()))
        .await?;
    let asked = json!({
        "id": "evt_4",
        "type": "permission.asked",
        "properties": permission_value(),
    });
    stream
        .write_all(&chunk(format!("data: {asked}\n\n").as_bytes()))
        .await?;
    stream.write_all(b"0\r\n\r\n").await?;
    Ok(())
}

fn session_value(id: &str, parent: Option<&str>, updated: i64) -> Value {
    let mut session = json!({
        "id": id,
        "slug": id,
        "projectID": "prj_1",
        "directory": "/tmp/fixture",
        "title": format!("session {id}"),
        "version": PINNED_OPENCODE_VERSION,
        "time": {"created": 1000, "updated": updated},
    });
    if let Some(parent) = parent {
        session["parentID"] = json!(parent);
    }
    session
}

fn permission_value() -> Value {
    json!({
        "id": "per_1",
        "sessionID": "ses_1",
        "permission": "bash",
        "patterns": ["*"],
        "metadata": {},
        "always": [],
        "tool": {"messageID": "msg_1", "callID": "call_1"},
    })
}

async fn handle_connection(
    mut stream: TcpStream,
    version: &str,
    mutations: &Mutex<Vec<String>>,
) -> anyhow::Result<()> {
    let (method, target, body) = read_request(&mut stream).await?;
    let path = target.split('?').next().unwrap_or_default().to_string();
    if method == "POST" {
        mutations
            .lock()
            .expect("mutation log")
            .push(format!("{method} {path} {body}"));
    }
    match (method.as_str(), path.as_str()) {
        ("GET", "/global/health") => {
            respond_json(
                &mut stream,
                200,
                &json!({"healthy": true, "version": version}),
            )
            .await
        }
        ("GET", "/config") => {
            let config = json!({
                "plugin": ["file:./plug.js", ["opencode-plugin-x", {"level": 1}]],
                "theme": "tolerated-unknown-field",
            });
            respond_json(&mut stream, 200, &config).await
        }
        ("GET", "/session") => {
            let sessions = json!([
                session_value("ses_1", None, 2000),
                session_value("ses_2", Some("ses_1"), 2600),
            ]);
            respond_json_eof(&mut stream, &sessions).await
        }
        ("GET", "/session/ses_1") => {
            respond_json(&mut stream, 200, &session_value("ses_1", None, 2000)).await
        }
        ("GET", "/session/ses_1/children") => {
            respond_json(
                &mut stream,
                200,
                &json!([session_value("ses_2", Some("ses_1"), 2600)]),
            )
            .await
        }
        ("GET", "/session/ses_1/message") => {
            let envelope = json!([{
                "info": {
                    "id": "msg_1",
                    "sessionID": "ses_1",
                    "role": "user",
                    "agent": "build",
                    "time": {"created": 1500},
                },
                "parts": [{
                    "id": "prt_1",
                    "sessionID": "ses_1",
                    "messageID": "msg_1",
                    "type": "text",
                    "text": "hello",
                }],
            }]);
            respond_json(&mut stream, 200, &envelope).await
        }
        ("GET", "/permission") => {
            respond_json(&mut stream, 200, &json!([permission_value()])).await
        }
        ("GET", "/event") => respond_sse(&mut stream).await,
        ("POST", "/session") => {
            respond_json(&mut stream, 200, &session_value("ses_new", None, 3000)).await
        }
        ("POST", "/session/ses_1/fork") => {
            respond_json(
                &mut stream,
                200,
                &session_value("ses_fork", Some("ses_1"), 3100),
            )
            .await
        }
        ("POST", "/session/ses_1/abort") => respond_json(&mut stream, 200, &json!(true)).await,
        ("POST", "/session/ses_1/summarize") => respond_json(&mut stream, 200, &json!(true)).await,
        ("POST", "/session/ses_1/revert") => {
            let mut reverted = session_value("ses_1", None, 3200);
            reverted["revert"] = json!({"messageID": "msg_1"});
            respond_json(&mut stream, 200, &reverted).await
        }
        ("POST", "/session/ses_1/unrevert") => {
            respond_json(&mut stream, 200, &session_value("ses_1", None, 3300)).await
        }
        ("POST", "/permission/per_1/reply") => respond_json(&mut stream, 200, &json!(true)).await,
        ("POST", "/session/ses_1/permissions/per_1") => {
            respond_json(&mut stream, 200, &json!(true)).await
        }
        _ => {
            let error = json!({"name": "NotFoundError", "data": {"message": "unknown route"}});
            respond_json(&mut stream, 404, &error).await
        }
    }
}

// --- typed client against the fixture ------------------------------------

#[tokio::test]
async fn typed_client_round_trips_every_wrapped_operation() {
    let fixture = spawn_fixture(PINNED_OPENCODE_VERSION).await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");

    let health = client.health().await.expect("health");
    assert!(health.healthy);
    assert_eq!(health.version, PINNED_OPENCODE_VERSION);

    // `/session` answers without content-length: read-to-EOF framing.
    let sessions = client.list_sessions().await.expect("sessions");
    assert_eq!(sessions.len(), 2);
    assert_eq!(sessions[0].id, "ses_1");
    assert_eq!(sessions[1].parent_id.as_deref(), Some("ses_1"));

    let session = client.get_session("ses_1").await.expect("session");
    assert_eq!(session.time.updated, 2000);
    let children = client.session_children("ses_1").await.expect("children");
    assert_eq!(children.len(), 1);

    let messages = client.list_messages("ses_1").await.expect("messages");
    assert_eq!(messages[0].info.role, "user");
    assert_eq!(messages[0].parts[0].part_type, "text");

    let permissions = client.list_permissions().await.expect("permissions");
    assert_eq!(permissions[0].id, "per_1");
    assert_eq!(
        permissions[0].tool.as_ref().expect("tool").call_id,
        "call_1"
    );

    let config = client.config().await.expect("config");
    let specifiers: Vec<&str> = config
        .plugin
        .iter()
        .map(|plugin| plugin.specifier())
        .collect();
    assert_eq!(specifiers, ["file:./plug.js", "opencode-plugin-x"]);

    let missing = client
        .get_session("ses_missing")
        .await
        .expect_err("404 surfaces");
    assert!(format!("{missing:#}").contains("404"));
}

#[tokio::test]
async fn event_stream_yields_connected_bus_and_unknown_plugin_events() {
    let fixture = spawn_fixture(PINNED_OPENCODE_VERSION).await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");
    let mut stream = client.subscribe_events().await.expect("subscribe");

    let first = stream
        .next_event(EVENT_TIMEOUT)
        .await
        .expect("first event")
        .expect("stream open");
    assert_eq!(first, Event::ServerConnected { id: "evt_1".into() });

    let second = stream
        .next_event(EVENT_TIMEOUT)
        .await
        .expect("second event")
        .expect("stream open");
    match &second {
        Event::SessionUpdated { id, session } => {
            assert_eq!(id, "evt_2");
            assert_eq!(session.id, "ses_1");
            assert_eq!(session.time.updated, 2000);
        }
        other => panic!("expected session.updated, got {other:?}"),
    }

    let third = stream
        .next_event(EVENT_TIMEOUT)
        .await
        .expect("third event")
        .expect("stream open");
    match &third {
        Event::Unknown {
            id,
            event_type,
            raw,
        } => {
            assert_eq!(id.as_deref(), Some("evt_3"));
            assert_eq!(event_type, "myplugin.custom");
            assert_eq!(raw["properties"]["marker"], 7);
        }
        other => panic!("expected a preserved unknown event, got {other:?}"),
    }

    let fourth = stream
        .next_event(EVENT_TIMEOUT)
        .await
        .expect("fourth event")
        .expect("stream open");
    match &fourth {
        Event::PermissionAsked { id, request } => {
            assert_eq!(id, "evt_4");
            assert_eq!(request.session_id, "ses_1");
        }
        other => panic!("expected permission.asked, got {other:?}"),
    }

    let end = stream.next_event(EVENT_TIMEOUT).await.expect("clean end");
    assert_eq!(end, None);
}

// --- admission gating -----------------------------------------------------

struct AcceptingVerifier;

impl AdmissionVerifier for AcceptingVerifier {
    async fn verify(&self, _receipt: &AdmissionReceipt) -> Result<(), AdapterError> {
        Ok(())
    }
}

struct RejectingVerifier;

impl AdmissionVerifier for RejectingVerifier {
    async fn verify(&self, _receipt: &AdmissionReceipt) -> Result<(), AdapterError> {
        Err(AdapterError::VerifierRejected("fixture rejects".into()))
    }
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_millis() as i64
}

fn receipt(platform: &str, client_version: &str, expires_in_ms: i64) -> AdmissionReceipt {
    let now = now_unix_ms();
    AdmissionReceipt {
        schema_version: ADMISSION_RECEIPT_SCHEMA_VERSION.to_string(),
        client_id: Uuid::now_v7(),
        platform: platform.to_string(),
        client_version: client_version.to_string(),
        matrix_sha256: "0".repeat(64),
        workspace_id: Uuid::now_v7(),
        issued_at_unix_ms: now,
        expires_at_unix_ms: now + expires_in_ms,
        probe_evidence_sha256: "a".repeat(64),
        key_version: 1,
        mac: "0".repeat(64),
    }
}

fn adapter_error(error: anyhow::Error) -> AdapterError {
    error
        .downcast::<AdapterError>()
        .expect("typed adapter error")
}

#[tokio::test]
async fn adapter_starts_read_only_and_refuses_mutations_locally() {
    let fixture = spawn_fixture(PINNED_OPENCODE_VERSION).await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");
    let mut adapter = OpencodeAdapter::connect(client).await.expect("connect");
    assert_eq!(adapter.mode(), &WriteMode::ReadOnly);
    assert!(adapter.version_pinned());

    let refusal = adapter_error(
        adapter
            .fork_session("ses_1", None)
            .await
            .expect_err("read-only fork must refuse"),
    );
    assert_eq!(refusal, AdapterError::ReadOnly);
    assert!(format!("{refusal}").contains("read-only safe mode"));

    // Reads still flow, and the refusal never reached the wire.
    adapter.list_sessions().await.expect("reads stay available");
    assert!(fixture.recorded_mutations().is_empty());
}

#[tokio::test]
async fn unpinned_server_versions_stay_in_safe_mode() {
    let fixture = spawn_fixture("9.9.9").await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");
    let mut adapter = OpencodeAdapter::connect(client).await.expect("connect");
    assert!(!adapter.version_pinned());
    assert_eq!(adapter.probed_version(), "9.9.9");

    let refusal = adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION, 60_000),
            &AcceptingVerifier,
        )
        .await
        .expect_err("unpinned version must refuse write mode");
    assert_eq!(
        refusal,
        AdapterError::VersionUnpinned {
            probed: "9.9.9".into(),
            pinned: PINNED_OPENCODE_VERSION,
        }
    );
    assert_eq!(adapter.mode(), &WriteMode::ReadOnly);
    assert!(fixture.recorded_mutations().is_empty());
}

#[tokio::test]
async fn mismatched_expired_and_rejected_receipts_keep_the_adapter_read_only() {
    let fixture = spawn_fixture(PINNED_OPENCODE_VERSION).await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");
    let mut adapter = OpencodeAdapter::connect(client).await.expect("connect");

    let wrong_platform = adapter
        .enable_write(
            &receipt("claude_code", PINNED_OPENCODE_VERSION, 60_000),
            &AcceptingVerifier,
        )
        .await
        .expect_err("another platform's receipt must refuse");
    assert!(matches!(wrong_platform, AdapterError::ReceiptMismatch(_)));

    let wrong_version = adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, "1.0.0", 60_000),
            &AcceptingVerifier,
        )
        .await
        .expect_err("another version's receipt must refuse");
    assert!(matches!(wrong_version, AdapterError::ReceiptMismatch(_)));

    let expired = adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION, -1_000),
            &AcceptingVerifier,
        )
        .await
        .expect_err("an expired receipt must refuse");
    assert_eq!(expired, AdapterError::AdmissionExpired);

    let rejected = adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION, 60_000),
            &RejectingVerifier,
        )
        .await
        .expect_err("verifier rejection must refuse");
    assert!(matches!(rejected, AdapterError::VerifierRejected(_)));

    assert_eq!(adapter.mode(), &WriteMode::ReadOnly);
    assert!(fixture.recorded_mutations().is_empty());
}

#[tokio::test]
async fn admitted_writes_flow_and_expiry_demotes_the_adapter() {
    let fixture = spawn_fixture(PINNED_OPENCODE_VERSION).await;
    let client = OpencodeClient::new(fixture.base.clone(), None).expect("client");
    let mut adapter = OpencodeAdapter::connect(client).await.expect("connect");
    adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION, 60_000),
            &AcceptingVerifier,
        )
        .await
        .expect("verified receipt admits writes");
    assert!(matches!(adapter.mode(), WriteMode::ReadWrite { .. }));

    let created = adapter
        .create_session(&CreateSessionRequest {
            title: Some("orchestrated".into()),
            ..CreateSessionRequest::default()
        })
        .await
        .expect("create");
    assert_eq!(created.id, "ses_new");
    let fork = adapter
        .fork_session("ses_1", Some("msg_1"))
        .await
        .expect("fork");
    assert_eq!(fork.parent_id.as_deref(), Some("ses_1"));
    assert!(adapter.abort_session("ses_1").await.expect("abort"));
    assert!(
        adapter
            .summarize_session(
                "ses_1",
                &SummarizeRequest {
                    provider_id: "anthropic".into(),
                    model_id: "claude".into(),
                    auto: None,
                },
            )
            .await
            .expect("summarize")
    );
    let reverted = adapter
        .revert_session(
            "ses_1",
            &RevertRequest {
                message_id: "msg_1".into(),
                part_id: None,
            },
        )
        .await
        .expect("revert");
    assert_eq!(reverted.revert.expect("revert marker").message_id, "msg_1");
    adapter.unrevert_session("ses_1").await.expect("unrevert");
    assert!(
        adapter
            .reply_permission("per_1", PermissionReply::Once, Some("approved"))
            .await
            .expect("permission reply")
    );
    assert!(
        adapter
            .respond_session_permission("ses_1", "per_1", PermissionReply::Reject)
            .await
            .expect("permission respond")
    );

    let mutations = fixture.recorded_mutations();
    assert_eq!(mutations.len(), 8);
    assert!(mutations[0].contains("POST /session") && mutations[0].contains("orchestrated"));
    assert!(mutations[1].contains("/fork") && mutations[1].contains("msg_1"));
    assert!(mutations[3].contains("providerID"));
    assert!(mutations[6].contains("\"reply\":\"once\""));
    assert!(mutations[7].contains("\"response\":\"reject\""));

    // A receipt that expires while held demotes the adapter at the next call.
    adapter
        .enable_write(
            &receipt(OPENCODE_PLATFORM_ID, PINNED_OPENCODE_VERSION, 250),
            &AcceptingVerifier,
        )
        .await
        .expect("short receipt admits");
    tokio::time::sleep(Duration::from_millis(400)).await;
    let demoted = adapter_error(
        adapter
            .abort_session("ses_1")
            .await
            .expect_err("expired admission must refuse"),
    );
    assert_eq!(demoted, AdapterError::AdmissionExpired);
    assert_eq!(adapter.mode(), &WriteMode::ReadOnly);
}

#[tokio::test]
async fn ipc_verifier_fails_closed_without_a_daemon() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let verifier = IpcAdmissionVerifier::new(directory.path().join("missing.sock"));
    let error = verifier
        .verify(&receipt(
            OPENCODE_PLATFORM_ID,
            PINNED_OPENCODE_VERSION,
            60_000,
        ))
        .await
        .expect_err("no daemon socket means no verification");
    assert!(matches!(error, AdapterError::VerifierRejected(_)));
}

// --- persistent cursor reconciliation ------------------------------------

fn session_struct(id: &str, updated: i64) -> Session {
    Session {
        id: id.to_string(),
        slug: id.to_string(),
        project_id: "prj_1".to_string(),
        workspace_id: None,
        directory: "/tmp/fixture".to_string(),
        path: None,
        parent_id: None,
        title: format!("session {id}"),
        version: PINNED_OPENCODE_VERSION.to_string(),
        time: SessionTime {
            created: 1000,
            updated,
            compacting: None,
            archived: None,
        },
        agent: None,
        revert: None,
        metadata: None,
    }
}

fn updated_event(event_id: &str, session_id: &str, updated: i64) -> Event {
    Event::SessionUpdated {
        id: event_id.to_string(),
        session: session_struct(session_id, updated),
    }
}

#[test]
fn cursor_persists_across_reopen_and_reconciles_drift() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let path = directory.path().join("state.sqlite3");
    let scope = cursor_scope(&Url::parse("http://127.0.0.1:4096").expect("url"), None);
    assert_eq!(scope, "127.0.0.1:4096");

    {
        let state = StateStore::open(&path).expect("state");
        let mut reconciler = EventReconciler::load(state, scope.clone()).expect("load");
        assert_eq!(reconciler.last_event_id(), None);
        assert!(
            reconciler.reconcile(&[]).is_err(),
            "reconciliation before any observed event must refuse"
        );
        assert!(
            reconciler
                .observe(&Event::ServerConnected { id: "evt_1".into() })
                .expect("observe connected")
        );
        assert!(
            reconciler
                .observe(&updated_event("evt_2", "ses_1", 2000))
                .expect("observe update")
        );
        assert_eq!(reconciler.watermark_unix_ms(), 2000);
        assert_eq!(reconciler.generation(), 1);
    }

    let state = StateStore::open(&path).expect("state reopens");
    let mut reconciler = EventReconciler::load(state.clone(), scope.clone()).expect("reload");
    assert_eq!(reconciler.last_event_id(), Some("evt_2"));
    assert_eq!(reconciler.watermark_unix_ms(), 2000);
    assert_eq!(reconciler.generation(), 1);

    // Reconnect: the stream restarts with server.connected, then a fresh
    // session listing closes the gap.
    assert!(
        reconciler
            .observe(&Event::ServerConnected { id: "evt_9".into() })
            .expect("observe reconnect")
    );
    assert_eq!(reconciler.generation(), 2);
    let report = reconciler
        .reconcile(&[session_struct("ses_1", 1500), session_struct("ses_2", 2600)])
        .expect("reconcile");
    assert_eq!(report.scope, scope);
    assert_eq!(report.resumed_at_event, "evt_9");
    assert_eq!(report.previous_watermark_unix_ms, 2000);
    assert_eq!(report.watermark_unix_ms, 2600);
    assert_eq!(report.drifted_session_ids, vec!["ses_2".to_string()]);

    let record = state
        .adapter_cursor(OPENCODE_CURSOR_ADAPTER, &scope)
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(record.cursor, "evt_9");
    assert_eq!(record.watermark.as_deref(), Some("2600"));
}

#[test]
fn delta_events_stay_in_memory_until_a_durable_event_or_flush() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let mut reconciler = EventReconciler::load(state.clone(), "scope").expect("load");
    assert!(
        reconciler
            .observe(&Event::ServerConnected { id: "evt_1".into() })
            .expect("durable connected")
    );

    let delta = Event::Unknown {
        id: Some("evt_2".into()),
        event_type: "message.part.delta".into(),
        raw: json!({"id": "evt_2", "type": "message.part.delta", "properties": {}}),
    };
    assert!(!reconciler.observe(&delta).expect("delta observed"));
    let record = state
        .adapter_cursor(OPENCODE_CURSOR_ADAPTER, "scope")
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(
        record.cursor, "evt_1",
        "delta events must not write the store"
    );
    assert_eq!(reconciler.last_event_id(), Some("evt_2"));

    reconciler.flush().expect("flush");
    let record = state
        .adapter_cursor(OPENCODE_CURSOR_ADAPTER, "scope")
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(record.cursor, "evt_2");
}

#[test]
fn concurrent_cursor_writers_surface_as_one_absorbed_conflict() {
    let directory = tempfile::TempDir::new().expect("tempdir");
    let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
    let mut first = EventReconciler::load(state.clone(), "scope").expect("first");
    let mut second = EventReconciler::load(state.clone(), "scope").expect("second");

    assert!(
        first
            .observe(&Event::ServerConnected { id: "evt_1".into() })
            .expect("first persists")
    );
    // The second reconciler loaded before the first wrote; its next durable
    // observation collides and is absorbed by one reload-and-retry.
    assert!(
        second
            .observe(&updated_event("evt_2", "ses_1", 500))
            .expect("second absorbs the conflict")
    );
    // And the first, now stale in the other direction, absorbs it too.
    assert!(
        first
            .observe(&updated_event("evt_3", "ses_1", 900))
            .expect("first absorbs the conflict")
    );

    let record = state
        .adapter_cursor(OPENCODE_CURSOR_ADAPTER, "scope")
        .expect("cursor read")
        .expect("cursor exists");
    assert_eq!(record.cursor, "evt_3");
    assert_eq!(record.revision, 3);
    assert_eq!(record.watermark.as_deref(), Some("900"));
}
