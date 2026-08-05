use super::*;
use serde_json::Value;
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
    assert_eq!(backup_result["schemaVersion"], 1);
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
        assert!(manifest.contains("systemd").not());
    }
}

trait BoolNot {
    fn not(self) -> bool;
}

impl BoolNot for bool {
    fn not(self) -> bool {
        !self
    }
}
