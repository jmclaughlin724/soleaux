use super::*;
use serde_json::{Value, json};
use soleaux_state::{
    ClientAccessMode, ClientCompatibilityState, ClientKind, REGISTRY_PAGE_LIMIT_DEFAULT,
    WorkspaceTrustState,
};
use std::{fs, path::PathBuf};
use tempfile::tempdir;

fn fixture_paths(root: PathBuf) -> SoleauxPaths {
    SoleauxPaths {
        home: root.join("home"),
        runtime: root.join("runtime"),
        endpoint: root.join("runtime").join("soleaux.sock"),
        state_database: root.join("home").join("state").join("canonical.sqlite3"),
        pid_file: root.join("runtime").join("soleauxd.pid"),
        log_file: root.join("home").join("logs").join("soleauxd.log"),
        service_manifest: root.join("service").join(if cfg!(target_os = "macos") {
            "com.soleaux.daemon.plist"
        } else if cfg!(target_os = "windows") {
            "soleaux-task.xml"
        } else {
            "soleaux.service"
        }),
        install_bin: root.join("bin"),
    }
}

#[test]
fn protocol_is_closed_typed_and_versioned() {
    let request = IpcRequest::new(IpcMethod::StateBackup {
        destination: "/tmp/backup.sqlite3".to_string(),
    });
    let encoded = serde_json::to_value(&request).expect("serialize");
    assert_eq!(encoded["schemaVersion"], IPC_SCHEMA_VERSION);
    assert_eq!(encoded["method"]["name"], "state_backup");
    assert_eq!(
        encoded["method"]["arguments"]["destination"],
        "/tmp/backup.sqlite3"
    );
    let mut invalid = encoded;
    invalid["unexpected"] = Value::Bool(true);
    assert!(serde_json::from_value::<IpcRequest>(invalid).is_err());
}

#[test]
fn daemon_boot_constructs_capability_policy_and_vault_key_store() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let server = IpcServer::open(paths).expect("server");
    assert!(server.capability_policy().grants().is_empty());
    let key_store = format!("{:?}", server.vault_key_store());
    assert!(key_store.contains("soleaux"));
    assert!(key_store.contains("vault-master"));
}

#[cfg(unix)]
#[tokio::test]
async fn same_user_ipc_supports_concurrent_clients_state_operations_and_shutdown() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let server = IpcServer::open(paths.clone()).expect("server");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(paths.endpoint.exists());

    let mut clients = Vec::new();
    for _ in 0..12 {
        let endpoint = paths.endpoint.clone();
        clients.push(tokio::spawn(async move {
            let response = IpcClient::new(endpoint)
                .call(IpcRequest::new(IpcMethod::Ping))
                .await
                .expect("ping");
            assert_eq!(response.status, IpcStatus::Ok);
            assert_eq!(response.result.expect("result")["pong"], true);
        }));
    }
    for client in clients {
        client.await.expect("client");
    }

    let client = IpcClient::new(&paths.endpoint);
    let status = client
        .call(IpcRequest::new(IpcMethod::Status))
        .await
        .expect("status")
        .result
        .expect("status result");
    assert_eq!(status["product"], "Soleaux");
    assert_eq!(status["peerCredentialCheck"], true);
    assert_eq!(status["concurrentClients"], true);
    assert_eq!(status["workspaceRegistry"], true);
    assert_eq!(status["clientRegistry"], true);
    assert_eq!(
        status["supportedClientKinds"].as_array().map(Vec::len),
        Some(4)
    );
    assert_eq!(status["productionClaimAllowed"], false);

    let integrity = client
        .call(IpcRequest::new(IpcMethod::StateIntegrity))
        .await
        .expect("integrity")
        .result
        .expect("integrity result");
    assert_eq!(integrity["integrity"], "ok");
    assert_eq!(integrity["foreignKeyViolations"], 0);
    assert_eq!(integrity["auditChainValid"], true);

    let backup = directory.path().join("backup.sqlite3");
    let backup_result = client
        .call(IpcRequest::new(IpcMethod::StateBackup {
            destination: backup.to_string_lossy().to_string(),
        }))
        .await
        .expect("backup")
        .result
        .expect("backup result");
    assert_eq!(
        backup_result["schemaVersion"],
        soleaux_state::SCHEMA_VERSION
    );
    assert!(backup.is_file());

    let export = directory.path().join("state.json");
    let export_result = client
        .call(IpcRequest::new(IpcMethod::StateExport {
            destination: export.to_string_lossy().to_string(),
        }))
        .await
        .expect("export")
        .result
        .expect("export result");
    assert_eq!(export_result["schemaVersion"], "soleaux.state-export/v1");
    assert!(export.is_file());

    let error = client
        .call(IpcRequest::new(IpcMethod::StateRestore {
            source: backup.to_string_lossy().to_string(),
        }))
        .await
        .expect_err("online restore must fail closed");
    assert!(format!("{error:#}").contains("offline_operation_required"));

    let shutdown = client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    assert_eq!(shutdown.result.expect("shutdown result")["shutdown"], true);
    task.await.expect("server task").expect("server exit");
    assert!(!paths.endpoint.exists());
    assert!(!paths.pid_file.exists());
}

#[cfg(unix)]
#[tokio::test]
async fn session_history_service_supports_lifecycle_turns_and_lineage() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let workspace_path = directory.path().join("workspace");
    fs::create_dir_all(&workspace_path).expect("workspace");

    let server = IpcServer::open(paths.clone()).expect("server");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "session IPC endpoint was not created"
    );
    let client = IpcClient::new(&paths.endpoint);

    let workspace = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_string_lossy().to_string(),
            display_name: None,
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({}),
        }))
        .await
        .expect("register workspace")
        .result
        .expect("workspace result");
    let workspace_id: uuid::Uuid =
        serde_json::from_value(workspace["workspace"]["id"].clone()).expect("workspace id");

    let created = client
        .call(IpcRequest::new(IpcMethod::SessionCreate {
            workspace_id,
            platform: "claude_code".to_string(),
            native_session_id: Some("native-session-1".to_string()),
            title: "Fixture session".to_string(),
            repository_ref: json!({"path": workspace_path.to_string_lossy()}),
            model: Some("fixture-model".to_string()),
            metadata: json!({"fixture": true}),
        }))
        .await
        .expect("create session")
        .result
        .expect("session result");
    let session_id: uuid::Uuid =
        serde_json::from_value(created["session"]["id"].clone()).expect("session id");
    assert_eq!(created["session"]["payload"]["sessionState"], "active");
    assert_eq!(
        created["session"]["payload"]["lineageRootId"],
        json!(session_id)
    );

    for expected_ordinal in 0..2u64 {
        let turn = client
            .call(IpcRequest::new(IpcMethod::TurnAppend {
                session_id,
                actor: "user".to_string(),
                native_turn_id: None,
                usage: json!({}),
                metadata: json!({}),
            }))
            .await
            .expect("append turn")
            .result
            .expect("turn result");
        assert_eq!(turn["turn"]["payload"]["ordinal"], json!(expected_ordinal));
    }

    let turns = client
        .call(IpcRequest::new(IpcMethod::TurnList {
            session_id,
            after_ordinal: Some(0),
            limit: 8,
        }))
        .await
        .expect("list turns")
        .result
        .expect("turn list");
    assert_eq!(turns["turns"].as_array().expect("turns").len(), 1);
    assert_eq!(turns["turns"][0]["payload"]["ordinal"], json!(1));

    let first_turn = client
        .call(IpcRequest::new(IpcMethod::TurnList {
            session_id,
            after_ordinal: None,
            limit: 1,
        }))
        .await
        .expect("first turn page")
        .result
        .expect("first turn result");
    assert_eq!(first_turn["truncated"], true);
    assert_eq!(first_turn["nextOrdinal"], json!(0));
    let turn_id: uuid::Uuid =
        serde_json::from_value(first_turn["turns"][0]["id"].clone()).expect("turn id");

    let message = client
        .call(IpcRequest::new(IpcMethod::MessageAppend {
            turn_id,
            role: "user".to_string(),
            native_message_id: None,
            model: None,
            metadata: json!({}),
        }))
        .await
        .expect("append message")
        .result
        .expect("message result");
    assert_eq!(message["message"]["payload"]["turnId"], json!(turn_id));
    let messages = client
        .call(IpcRequest::new(IpcMethod::MessageList {
            turn_id,
            cursor: None,
            limit: 8,
        }))
        .await
        .expect("list messages")
        .result
        .expect("message list");
    assert_eq!(messages["messages"].as_array().expect("messages").len(), 1);

    let fork = client
        .call(IpcRequest::new(IpcMethod::SessionFork {
            session_id,
            title: Some("Fixture fork".to_string()),
        }))
        .await
        .expect("fork session")
        .result
        .expect("fork result");
    let fork_id: uuid::Uuid =
        serde_json::from_value(fork["session"]["id"].clone()).expect("fork id");
    assert_eq!(fork["forkedFrom"], json!(session_id));
    assert_eq!(
        fork["session"]["payload"]["lineageRootId"],
        json!(session_id)
    );

    let lineage = client
        .call(IpcRequest::new(IpcMethod::SessionLineage {
            session_id: fork_id,
        }))
        .await
        .expect("lineage")
        .result
        .expect("lineage result");
    assert_eq!(lineage["lineage"].as_array().expect("lineage").len(), 2);
    assert_eq!(lineage["lineage"][0]["id"], json!(fork_id));
    assert_eq!(lineage["lineage"][1]["id"], json!(session_id));

    let archived = client
        .call(IpcRequest::new(IpcMethod::SessionArchive { session_id }))
        .await
        .expect("archive")
        .result
        .expect("archive result");
    assert_eq!(archived["session"]["payload"]["sessionState"], "archived");

    let rejected = client
        .call(IpcRequest::new(IpcMethod::TurnAppend {
            session_id,
            actor: "user".to_string(),
            native_turn_id: None,
            usage: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect_err("archived session must reject turns");
    assert!(format!("{rejected:#}").contains("session_operation_failed"));

    let sessions = client
        .call(IpcRequest::new(IpcMethod::SessionList {
            workspace_id: Some(workspace_id),
            include_archived: false,
            cursor: None,
            limit: 8,
        }))
        .await
        .expect("list active sessions")
        .result
        .expect("session list");
    assert_eq!(sessions["sessions"].as_array().expect("sessions").len(), 1);
    assert_eq!(sessions["sessions"][0]["id"], json!(fork_id));
    let all_sessions = client
        .call(IpcRequest::new(IpcMethod::SessionList {
            workspace_id: Some(workspace_id),
            include_archived: true,
            cursor: None,
            limit: 8,
        }))
        .await
        .expect("list all sessions")
        .result
        .expect("all session list");
    assert_eq!(
        all_sessions["sessions"].as_array().expect("sessions").len(),
        2
    );

    let resumed = client
        .call(IpcRequest::new(IpcMethod::SessionResume { session_id }))
        .await
        .expect("resume")
        .result
        .expect("resume result");
    assert_eq!(resumed["session"]["payload"]["sessionState"], "active");
    let turn = client
        .call(IpcRequest::new(IpcMethod::TurnAppend {
            session_id,
            actor: "assistant".to_string(),
            native_turn_id: None,
            usage: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect("append after resume")
        .result
        .expect("resumed turn result");
    assert_eq!(turn["turn"]["payload"]["ordinal"], json!(2));

    let read = client
        .call(IpcRequest::new(IpcMethod::SessionRead {
            session_id,
            after_ordinal: None,
            turn_limit: 8,
        }))
        .await
        .expect("read session")
        .result
        .expect("read result");
    assert_eq!(read["turns"].as_array().expect("turns").len(), 3);

    let shutdown = client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    assert_eq!(shutdown.result.expect("shutdown result")["shutdown"], true);
    task.await.expect("server task").expect("server exit");
}

#[cfg(unix)]
#[tokio::test]
async fn memory_lifecycle_operations_flow_over_ipc_behind_the_capability_gate() {
    use soleaux_vault::{Capability, CapabilityGrant, RiskLevel, SensitivityLevel};
    use std::collections::BTreeSet;

    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let workspace_path = directory.path().join("workspace");
    fs::create_dir_all(&workspace_path).expect("workspace");

    let mut server = IpcServer::open(paths.clone()).expect("server");
    server
        .capability_policy_mut()
        .add_grant(CapabilityGrant {
            id: uuid::Uuid::now_v7(),
            subject: "granted-reviewer".to_string(),
            workspace_id: None,
            capabilities: BTreeSet::from([Capability::WriteMemory]),
            resource_prefixes: Vec::new(),
            max_risk: RiskLevel::LocalWrite,
            max_sensitivity: SensitivityLevel::Secret,
            expires_at_unix_ms: None,
            requires_approval: false,
            delegable: false,
            parent_grant_id: None,
            labels: BTreeSet::new(),
        })
        .expect("grant");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "memory IPC endpoint was not created"
    );
    let client = IpcClient::new(&paths.endpoint);

    let workspace = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_string_lossy().to_string(),
            display_name: None,
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({}),
        }))
        .await
        .expect("register workspace")
        .result
        .expect("workspace result");
    let workspace_id: uuid::Uuid =
        serde_json::from_value(workspace["workspace"]["id"].clone()).expect("workspace id");

    let propose = |actor: &str, content: &str| {
        IpcRequest::new(IpcMethod::MemoryPropose {
            workspace_id,
            actor: actor.to_string(),
            scope: "team".to_string(),
            claim_type: "decision".to_string(),
            subject: "database".to_string(),
            content: content.to_string(),
            confidence: 0.9,
            evidence_uris: vec!["soleaux://audit/fixture".to_string()],
            supersedes_id: None,
            source_session_id: None,
            sensitivity: soleaux_state::Sensitivity::Internal,
            expires_at_unix_ms: None,
            metadata: json!({}),
        })
    };

    let denied = client
        .call(propose("ungranted-actor", "Use one serialized writer"))
        .await
        .expect_err("ungranted memory propose must be denied");
    let denied = format!("{denied:#}");
    assert!(denied.contains("memory_operation_failed"));
    assert!(denied.contains("memory capability denied"));

    let proposed = client
        .call(propose("granted-reviewer", "Use one serialized writer"))
        .await
        .expect("granted memory propose")
        .result
        .expect("propose result");
    assert_eq!(proposed["claim"]["payload"]["memoryState"], "proposed");
    let claim_id: uuid::Uuid =
        serde_json::from_value(proposed["claim"]["id"].clone()).expect("claim id");

    let denied = client
        .call(IpcRequest::new(IpcMethod::MemoryValidate {
            claim_id,
            actor: "ungranted-actor".to_string(),
            disposition: "validated".to_string(),
        }))
        .await
        .expect_err("ungranted memory validate must be denied");
    assert!(format!("{denied:#}").contains("memory capability denied"));

    for disposition in ["validated", "active"] {
        let advanced = client
            .call(IpcRequest::new(IpcMethod::MemoryValidate {
                claim_id,
                actor: "granted-reviewer".to_string(),
                disposition: disposition.to_string(),
            }))
            .await
            .expect("granted memory validate")
            .result
            .expect("validate result");
        assert_eq!(advanced["claim"]["payload"]["memoryState"], disposition);
    }

    let listed = client
        .call(IpcRequest::new(IpcMethod::MemoryList {
            workspace_id: Some(workspace_id),
            scope: Some("team".to_string()),
            memory_state: Some("active".to_string()),
            cursor: None,
            limit: 8,
        }))
        .await
        .expect("memory list")
        .result
        .expect("list result");
    assert_eq!(listed["claims"].as_array().expect("claims").len(), 1);
    assert_eq!(listed["claims"][0]["id"], json!(claim_id));

    let exported = client
        .call(IpcRequest::new(IpcMethod::MemoryExport {
            workspace_id,
            scope: None,
            cursor: None,
            limit: 8,
        }))
        .await
        .expect("memory export")
        .result
        .expect("export result");
    assert_eq!(exported["schemaVersion"], "soleaux.memory-export/v1");
    assert_eq!(exported["count"], 1);

    let shutdown = client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    assert_eq!(shutdown.result.expect("shutdown result")["shutdown"], true);
    task.await.expect("server task").expect("server exit");
}

#[cfg(unix)]
#[tokio::test]
async fn workspace_registry_converges_concurrent_client_types_and_survives_restart() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let workspace_path = directory.path().join("workspace");
    fs::create_dir_all(&workspace_path).expect("workspace");

    let server = IpcServer::open(paths.clone()).expect("server");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "registry IPC endpoint was not created"
    );

    let client = IpcClient::new(&paths.endpoint);
    let workspace = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_string_lossy().to_string(),
            display_name: Some("Fixture workspace".to_string()),
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({"source":"test"}),
        }))
        .await
        .expect("register workspace")
        .result
        .expect("workspace result");
    let workspace_id: uuid::Uuid =
        serde_json::from_value(workspace["workspace"]["id"].clone()).expect("workspace id");

    let mut registrations = Vec::new();
    for kind in ClientKind::ALL {
        let endpoint = paths.endpoint.clone();
        registrations.push(tokio::spawn(async move {
            let client_version = if kind == ClientKind::Cli {
                env!("CARGO_PKG_VERSION")
            } else {
                "unprobed-fixture"
            };
            let value = IpcClient::new(endpoint)
                .call(IpcRequest::new(IpcMethod::ClientRegister {
                    client_kind: kind,
                    instance_id: format!("{}-fixture", kind.as_str()),
                    display_name: format!("Fixture {}", kind.as_str()),
                    client_version: client_version.to_string(),
                    protocol_version: CLIENT_PROTOCOL_VERSION.to_string(),
                    ttl_ms: 60_000,
                    capabilities: json!({"registry":true}),
                    metadata: json!({"concurrent":true}),
                }))
                .await
                .expect("register client")
                .result
                .expect("client result");
            let expected = if kind == ClientKind::Cli {
                ClientCompatibilityState::Verified
            } else {
                ClientCompatibilityState::Unprobed
            };
            assert_eq!(
                serde_json::from_value::<ClientCompatibilityState>(
                    value["compatibilityState"].clone()
                )
                .expect("compatibility state"),
                expected
            );
            assert_eq!(value["writeCapable"], kind == ClientKind::Cli);
            let id = serde_json::from_value::<uuid::Uuid>(value["client"]["id"].clone())
                .expect("client id");
            (kind, id)
        }));
    }
    let mut clients = Vec::new();
    for registration in registrations {
        clients.push(registration.await.expect("registration task"));
    }

    let mut bindings = Vec::new();
    for (kind, client_id) in &clients {
        let endpoint = paths.endpoint.clone();
        let kind = *kind;
        let client_id = *client_id;
        bindings.push(tokio::spawn(async move {
            IpcClient::new(endpoint)
                .call(IpcRequest::new(IpcMethod::ClientBindWorkspace {
                    client_id,
                    workspace_id,
                    access_mode: if kind == ClientKind::Cli {
                        ClientAccessMode::ReadWrite
                    } else {
                        ClientAccessMode::ReadOnly
                    },
                    admission_receipt: None,
                    capabilities: json!({"context":true}),
                    metadata: json!({}),
                }))
                .await
                .expect("bind client")
        }));
    }
    for binding in bindings {
        binding.await.expect("binding task");
    }

    let registry = client
        .call(IpcRequest::new(IpcMethod::RegistryStatus {
            include_stale: false,
            limit: REGISTRY_PAGE_LIMIT_DEFAULT,
            workspace_cursor: None,
            client_cursor: None,
            binding_cursor: None,
        }))
        .await
        .expect("registry status")
        .result
        .expect("registry result");
    assert_eq!(registry["schemaVersion"], REGISTRY_SCHEMA_VERSION);
    assert_eq!(registry["workspaces"].as_array().map(Vec::len), Some(1));
    assert_eq!(registry["clients"].as_array().map(Vec::len), Some(4));
    assert_eq!(registry["bindings"].as_array().map(Vec::len), Some(4));
    assert_eq!(registry["pagination"]["limit"], REGISTRY_PAGE_LIMIT_DEFAULT);
    assert_eq!(registry["pagination"]["clients"]["truncated"], false);
    assert_eq!(registry["publicToolCeiling"], 12);
    assert_eq!(registry["productionClaimAllowed"], false);

    client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    task.await.expect("server task").expect("server exit");

    let restarted = IpcServer::open(paths.clone()).expect("restart server");
    let restarted_task = tokio::spawn(restarted.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "restarted registry IPC endpoint was not created"
    );
    let client = IpcClient::new(&paths.endpoint);
    let persisted = client
        .call(IpcRequest::new(IpcMethod::RegistryStatus {
            include_stale: false,
            limit: REGISTRY_PAGE_LIMIT_DEFAULT,
            workspace_cursor: None,
            client_cursor: None,
            binding_cursor: None,
        }))
        .await
        .expect("persisted registry")
        .result
        .expect("persisted result");
    assert_eq!(persisted["workspaces"].as_array().map(Vec::len), Some(1));
    assert_eq!(persisted["clients"].as_array().map(Vec::len), Some(4));
    assert_eq!(persisted["bindings"].as_array().map(Vec::len), Some(4));

    let heartbeat = client
        .call(IpcRequest::new(IpcMethod::ClientHeartbeat {
            client_id: clients[0].1,
            ttl_ms: 60_000,
            capabilities: Some(json!({"registry":true,"heartbeat":true})),
        }))
        .await
        .expect("heartbeat")
        .result
        .expect("heartbeat result");
    assert!(
        heartbeat["client"]["revision"]
            .as_u64()
            .is_some_and(|value| value >= 2)
    );

    client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("final shutdown");
    restarted_task
        .await
        .expect("restarted task")
        .expect("restarted server exit");
}

#[cfg(unix)]
#[tokio::test]
async fn registry_rejects_oversized_inputs_and_unverified_write_elevation() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let workspace_path = directory.path().join("workspace");
    fs::create_dir_all(&workspace_path).expect("workspace");
    let server = IpcServer::open(paths.clone()).expect("server");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "registry IPC endpoint was not created"
    );
    let client = IpcClient::new(&paths.endpoint);

    let oversized = "x".repeat(soleaux_state::REGISTRY_JSON_FIELD_MAX_BYTES + 1);
    let error = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_str().expect("UTF-8").to_string(),
            display_name: None,
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({"oversized":oversized}),
        }))
        .await
        .expect_err("oversized registry metadata must fail closed");
    assert!(format!("{error:#}").contains("registry limit"));

    let workspace = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_str().expect("UTF-8").to_string(),
            display_name: None,
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({}),
        }))
        .await
        .expect("workspace")
        .result
        .expect("workspace result");
    let workspace_id = serde_json::from_value::<uuid::Uuid>(workspace["workspace"]["id"].clone())
        .expect("workspace id");
    let desktop = client
        .call(IpcRequest::new(IpcMethod::ClientRegister {
            client_kind: ClientKind::Desktop,
            instance_id: "desktop-unprobed".to_string(),
            display_name: "Desktop unprobed".to_string(),
            client_version: "unknown".to_string(),
            protocol_version: CLIENT_PROTOCOL_VERSION.to_string(),
            ttl_ms: 60_000,
            capabilities: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect("desktop registration")
        .result
        .expect("desktop result");
    assert_eq!(desktop["compatibilityState"], "unprobed");
    assert_eq!(desktop["writeCapable"], false);
    let desktop_id =
        serde_json::from_value::<uuid::Uuid>(desktop["client"]["id"].clone()).expect("desktop id");
    let error = client
        .call(IpcRequest::new(IpcMethod::ClientBindWorkspace {
            client_id: desktop_id,
            workspace_id,
            access_mode: ClientAccessMode::ReadWrite,
            admission_receipt: None,
            capabilities: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect_err("unverified desktop must remain read-only");
    assert!(format!("{error:#}").contains("verified daemon-trusted client compatibility decision"));

    client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    task.await.expect("server task").expect("server exit");
}

#[cfg(unix)]
#[tokio::test]
async fn admission_receipts_flow_over_ipc_and_gate_read_write_bindings() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let workspace_path = directory.path().join("workspace");
    fs::create_dir_all(&workspace_path).expect("workspace");
    let key_store = soleaux_vault::FileKeyStore::new(directory.path().join("keys").join("ring"));
    let server = IpcServer::open_with_key_store(paths.clone(), std::sync::Arc::new(key_store))
        .expect("server");
    let task = tokio::spawn(server.run());
    for _ in 0..100 {
        if paths.endpoint.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    assert!(
        paths.endpoint.exists(),
        "admission IPC endpoint was not created"
    );
    let client = IpcClient::new(&paths.endpoint);

    let workspace = client
        .call(IpcRequest::new(IpcMethod::WorkspaceRegister {
            path: workspace_path.to_string_lossy().to_string(),
            display_name: None,
            trust_state: WorkspaceTrustState::Trusted,
            metadata: json!({}),
        }))
        .await
        .expect("workspace")
        .result
        .expect("workspace result");
    let workspace_id: uuid::Uuid =
        serde_json::from_value(workspace["workspace"]["id"].clone()).expect("workspace id");

    let external = client
        .call(IpcRequest::new(IpcMethod::ClientRegister {
            client_kind: ClientKind::Adapter,
            instance_id: "admitted-fixture".to_string(),
            display_name: "Admitted fixture".to_string(),
            client_version: "mcp-2025-11-25".to_string(),
            protocol_version: CLIENT_PROTOCOL_VERSION.to_string(),
            ttl_ms: 60_000,
            capabilities: json!({}),
            metadata: json!({"platform":"generic_mcp_host"}),
        }))
        .await
        .expect("external registration")
        .result
        .expect("external result");
    assert_eq!(external["writeCapable"], false);
    let client_id: uuid::Uuid =
        serde_json::from_value(external["client"]["id"].clone()).expect("client id");

    let denied = client
        .call(IpcRequest::new(IpcMethod::ClientBindWorkspace {
            client_id,
            workspace_id,
            access_mode: ClientAccessMode::ReadWrite,
            admission_receipt: None,
            capabilities: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect_err("read-write without a receipt must stay denied");
    assert!(format!("{denied:#}").contains("client_workspace_binding_failed"));

    let refused = client
        .call(IpcRequest::new(IpcMethod::AdmissionIssue {
            client_id,
            workspace_id,
            probe_evidence_sha256: "not-a-digest".to_string(),
            ttl_ms: 60_000,
        }))
        .await
        .expect_err("malformed probe evidence must be refused");
    assert!(format!("{refused:#}").contains("admission_operation_failed"));

    let issued = client
        .call(IpcRequest::new(IpcMethod::AdmissionIssue {
            client_id,
            workspace_id,
            probe_evidence_sha256: "a".repeat(64),
            ttl_ms: 60_000,
        }))
        .await
        .expect("issue")
        .result
        .expect("issue result");
    assert_eq!(issued["schemaVersion"], "soleaux.admission-issue/v1");
    assert_eq!(issued["productionClaimAllowed"], false);
    let receipt: AdmissionReceipt =
        serde_json::from_value(issued["receipt"].clone()).expect("receipt");
    assert_eq!(receipt.schema_version, ADMISSION_RECEIPT_SCHEMA_VERSION);

    let verified = client
        .call(IpcRequest::new(IpcMethod::AdmissionVerify {
            receipt: receipt.clone(),
        }))
        .await
        .expect("verify")
        .result
        .expect("verify result");
    assert_eq!(verified["verified"], true);
    assert_eq!(verified["platform"], "generic_mcp_host");

    let mut forged = receipt.clone();
    forged.expires_at_unix_ms += 60_000;
    let rejected = client
        .call(IpcRequest::new(IpcMethod::AdmissionVerify {
            receipt: forged,
        }))
        .await
        .expect_err("a tampered receipt must be rejected");
    assert!(format!("{rejected:#}").contains("admission_operation_failed"));

    let bound = client
        .call(IpcRequest::new(IpcMethod::ClientBindWorkspace {
            client_id,
            workspace_id,
            access_mode: ClientAccessMode::ReadWrite,
            admission_receipt: Some(receipt),
            capabilities: json!({}),
            metadata: json!({}),
        }))
        .await
        .expect("receipt-admitted binding")
        .result
        .expect("binding result");
    assert_eq!(bound["binding"]["payload"]["accessMode"], "read_write");
    assert_eq!(bound["admission"]["receiptVerified"], true);

    let heartbeat = client
        .call(IpcRequest::new(IpcMethod::ClientHeartbeat {
            client_id,
            ttl_ms: 60_000,
            capabilities: None,
        }))
        .await
        .expect("heartbeat")
        .result
        .expect("heartbeat result");
    assert_eq!(heartbeat["writeCapable"], false);
    assert_eq!(
        heartbeat["bindings"][0]["payload"]["accessMode"], "read_write",
        "an unexpired admission survives revalidation"
    );

    client
        .call(IpcRequest::new(IpcMethod::Shutdown))
        .await
        .expect("shutdown");
    task.await.expect("server task").expect("server exit");
}

#[test]
fn service_installation_is_per_user_explicit_and_non_production() {
    let directory = tempdir().expect("tempdir");
    let paths = fixture_paths(directory.path().to_path_buf());
    let cli = directory.path().join(if cfg!(windows) {
        "source-soleaux.exe"
    } else {
        "source-soleaux"
    });
    let daemon = directory.path().join(if cfg!(windows) {
        "source-soleauxd.exe"
    } else {
        "source-soleauxd"
    });
    fs::write(&cli, b"fixture-cli").expect("cli");
    fs::write(&daemon, b"fixture-daemon").expect("daemon");
    let report = install(&paths, &cli, &daemon, false).expect("install");
    assert!(paths.installed_cli().is_file());
    assert!(paths.installed_daemon().is_file());
    assert!(paths.service_manifest.is_file());
    assert!(report.service_installed);
    assert!(!report.service_started);
    assert!(!report.production_claim_allowed);
    let manifest = fs::read_to_string(&paths.service_manifest).expect("manifest");
    assert!(manifest.contains("soleaux"));
    assert!(manifest.contains("ipc"));
    assert!(manifest.contains(&paths.state_database.to_string_lossy().to_string()));
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        assert!(manifest.contains("NoNewPrivileges=true"));
        assert!(manifest.contains("UMask=0077"));
        assert!(!manifest.contains("systemd"));
    }
}
