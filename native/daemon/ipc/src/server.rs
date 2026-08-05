use crate::{DaemonStatus, IPC_SCHEMA_VERSION, IpcMethod, IpcRequest, IpcResponse, SoleauxPaths};
use anyhow::{Context, Result, anyhow};
use serde_json::json;
use soleaux_state::StateStore;
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Clone)]
pub struct IpcServer {
    paths: SoleauxPaths,
    state: StateStore,
    started_at_unix_ms: i64,
}

impl IpcServer {
    pub fn open(paths: SoleauxPaths) -> Result<Self> {
        paths.create_directories()?;
        let state = StateStore::open(&paths.state_database)?;
        Ok(Self {
            paths,
            state,
            started_at_unix_ms: unix_ms(),
        })
    }

    pub fn paths(&self) -> &SoleauxPaths {
        &self.paths
    }

    #[cfg(unix)]
    pub async fn run(self) -> Result<()> {
        crate::unix::run_server(self).await
    }

    #[cfg(not(unix))]
    pub async fn run(self) -> Result<()> {
        let _ = self;
        anyhow::bail!("Soleaux local IPC is not yet available on this operating system")
    }

    pub(crate) async fn dispatch(&self, request: &IpcRequest) -> IpcResponse {
        if request.schema_version != IPC_SCHEMA_VERSION {
            return IpcResponse::error(
                request.request_id,
                "unsupported_schema",
                "unsupported Soleaux IPC schema",
            );
        }
        let result = match &request.method {
            IpcMethod::Ping => Ok(json!({"pong":true,"pid":std::process::id()})),
            IpcMethod::Status => serde_json::to_value(self.status()).map_err(Into::into),
            IpcMethod::StateIntegrity => self
                .state
                .integrity_report()
                .and_then(|value| serde_json::to_value(value).map_err(Into::into)),
            IpcMethod::StateBackup { destination } => {
                let store = self.state.clone();
                let destination = PathBuf::from(destination);
                run_blocking(move || store.backup_to(destination))
                    .await
                    .and_then(|value| serde_json::to_value(value).map_err(Into::into))
            }
            IpcMethod::StateRestore { .. } => Err(anyhow!(
                "state restore is an offline operation; stop the service before restoring"
            )),
            IpcMethod::StateExport { destination } => {
                let store = self.state.clone();
                let destination = PathBuf::from(destination);
                run_blocking(move || export_snapshot(&store, &destination)).await
            }
            IpcMethod::StateRepair => {
                let store = self.state.clone();
                run_blocking(move || store.repair())
                    .await
                    .and_then(|value| serde_json::to_value(value).map_err(Into::into))
            }
            IpcMethod::StateSnapshot => self
                .state
                .export_snapshot()
                .and_then(|value| serde_json::to_value(value).map_err(Into::into)),
            IpcMethod::RegistryStatus { include_stale } => {
                crate::registry::status(&self.state, *include_stale)
            }
            IpcMethod::WorkspaceRegister {
                path,
                display_name,
                trust_state,
                metadata,
            } => crate::registry::register_workspace(
                &self.state,
                path,
                display_name.clone(),
                *trust_state,
                metadata.clone(),
            ),
            IpcMethod::WorkspaceList => crate::registry::list_workspaces(&self.state),
            IpcMethod::WorkspaceForget { workspace_id } => {
                crate::registry::forget_workspace(&self.state, *workspace_id)
            }
            IpcMethod::ClientRegister {
                client_kind,
                instance_id,
                display_name,
                client_version,
                protocol_version,
                ttl_ms,
                capabilities,
                metadata,
            } => crate::registry::register_client(
                &self.state,
                *client_kind,
                instance_id.clone(),
                display_name.clone(),
                client_version.clone(),
                protocol_version.clone(),
                *ttl_ms,
                capabilities.clone(),
                metadata.clone(),
            ),
            IpcMethod::ClientHeartbeat {
                client_id,
                ttl_ms,
                capabilities,
            } => crate::registry::heartbeat_client(
                &self.state,
                *client_id,
                *ttl_ms,
                capabilities.clone(),
            ),
            IpcMethod::ClientList { include_stale } => {
                crate::registry::list_clients(&self.state, *include_stale)
            }
            IpcMethod::ClientDisconnect { client_id } => {
                crate::registry::disconnect_client(&self.state, *client_id)
            }
            IpcMethod::ClientBindWorkspace {
                client_id,
                workspace_id,
                access_mode,
                capabilities,
                metadata,
            } => crate::registry::bind_client_workspace(
                &self.state,
                *client_id,
                *workspace_id,
                *access_mode,
                capabilities.clone(),
                metadata.clone(),
            ),
            IpcMethod::ClientUnbindWorkspace { binding_id } => {
                crate::registry::unbind_client_workspace(&self.state, *binding_id)
            }
            IpcMethod::Shutdown => Ok(json!({"shutdown":true})),
        };
        match result {
            Ok(value) => IpcResponse::success(request.request_id, value),
            Err(error) => IpcResponse::error(
                request.request_id,
                error_code(&request.method),
                error.to_string(),
            ),
        }
    }

    fn status(&self) -> DaemonStatus {
        DaemonStatus {
            product: "Soleaux".to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            pid: std::process::id(),
            started_at_unix_ms: self.started_at_unix_ms,
            state_database: self.paths.state_database.to_string_lossy().to_string(),
            endpoint: self.paths.endpoint.to_string_lossy().to_string(),
            peer_credential_check: cfg!(unix),
            concurrent_clients: true,
            workspace_registry: true,
            client_registry: true,
            supported_client_kinds: soleaux_state::ClientKind::ALL.to_vec(),
            production_claim_allowed: false,
        }
    }
}

fn export_snapshot(store: &StateStore, destination: &Path) -> Result<serde_json::Value> {
    let snapshot = store.export_snapshot()?;
    let bytes = serde_json::to_vec_pretty(&snapshot)?;
    atomic_write(destination, &bytes)?;
    Ok(json!({
        "schemaVersion":"soleaux.state-export/v1",
        "destination":destination,
        "bytes":bytes.len(),
        "blake3":blake3::hash(&bytes).to_hex().to_string(),
        "canonicalSchemaVersion":snapshot.schema_version,
        "entityCount":snapshot.entities.len(),
        "auditCount":snapshot.audit.len(),
    }))
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("output path has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".{}.tmp", uuid::Uuid::now_v7()));
    fs::write(&temporary, bytes)
        .with_context(|| format!("writing temporary output {}", temporary.display()))?;
    fs::rename(&temporary, path)
        .with_context(|| format!("installing output {}", path.display()))?;
    Ok(())
}

async fn run_blocking<T: Send + 'static>(
    operation: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    tokio::task::spawn_blocking(operation)
        .await
        .context("joining local IPC blocking operation")?
}

fn error_code(method: &IpcMethod) -> &'static str {
    match method {
        IpcMethod::StateRestore { .. } => "offline_operation_required",
        IpcMethod::StateBackup { .. } => "state_backup_failed",
        IpcMethod::StateExport { .. } => "state_export_failed",
        IpcMethod::StateRepair => "state_repair_failed",
        IpcMethod::StateIntegrity | IpcMethod::StateSnapshot => "state_read_failed",
        IpcMethod::RegistryStatus { .. } => "registry_status_failed",
        IpcMethod::WorkspaceRegister { .. }
        | IpcMethod::WorkspaceList
        | IpcMethod::WorkspaceForget { .. } => "workspace_registry_failed",
        IpcMethod::ClientRegister { .. }
        | IpcMethod::ClientHeartbeat { .. }
        | IpcMethod::ClientList { .. }
        | IpcMethod::ClientDisconnect { .. } => "client_registry_failed",
        IpcMethod::ClientBindWorkspace { .. } | IpcMethod::ClientUnbindWorkspace { .. } => {
            "client_workspace_binding_failed"
        }
        IpcMethod::Ping | IpcMethod::Status | IpcMethod::Shutdown => "daemon_operation_failed",
    }
}

pub(crate) fn write_pid(path: &Path) -> Result<()> {
    let parent = path.parent().context("pid file has no parent")?;
    fs::create_dir_all(parent)?;
    fs::write(path, format!("{}\n", std::process::id()))
        .with_context(|| format!("writing Soleaux pid file {}", path.display()))
}

pub(crate) struct EndpointCleanup {
    endpoint: PathBuf,
    pid_file: PathBuf,
}

impl EndpointCleanup {
    pub(crate) fn new(endpoint: PathBuf, pid_file: PathBuf) -> Self {
        Self { endpoint, pid_file }
    }
}

impl Drop for EndpointCleanup {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.endpoint);
        let _ = fs::remove_file(&self.pid_file);
    }
}

fn unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}
