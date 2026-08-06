use serde::{Deserialize, Serialize};
use serde_json::Value;
use soleaux_state::{
    ClientAccessMode, ClientKind, REGISTRY_PAGE_LIMIT_DEFAULT, WorkspaceTrustState,
};
use uuid::Uuid;

pub const IPC_SCHEMA_VERSION: &str = "soleaux.ipc/v1";
pub const IPC_MAX_FRAME_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct IpcRequest {
    pub schema_version: String,
    pub request_id: Uuid,
    pub method: IpcMethod,
}

impl IpcRequest {
    pub fn new(method: IpcMethod) -> Self {
        Self {
            schema_version: IPC_SCHEMA_VERSION.to_string(),
            request_id: Uuid::now_v7(),
            method,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "name", content = "arguments", rename_all = "snake_case")]
pub enum IpcMethod {
    Ping,
    Status,
    StateIntegrity,
    StateBackup {
        destination: String,
    },
    StateRestore {
        source: String,
    },
    StateExport {
        destination: String,
    },
    StateRepair,
    StateSnapshot,
    RegistryStatus {
        #[serde(default)]
        include_stale: bool,
        #[serde(default = "default_registry_limit")]
        limit: usize,
        #[serde(default)]
        workspace_cursor: Option<Uuid>,
        #[serde(default)]
        client_cursor: Option<Uuid>,
        #[serde(default)]
        binding_cursor: Option<Uuid>,
    },
    WorkspaceRegister {
        path: String,
        #[serde(default)]
        display_name: Option<String>,
        trust_state: WorkspaceTrustState,
        #[serde(default)]
        metadata: Value,
    },
    WorkspaceList {
        #[serde(default)]
        cursor: Option<Uuid>,
        #[serde(default = "default_registry_limit")]
        limit: usize,
    },
    WorkspaceForget {
        workspace_id: Uuid,
    },
    ClientRegister {
        client_kind: ClientKind,
        instance_id: String,
        display_name: String,
        client_version: String,
        protocol_version: String,
        ttl_ms: u64,
        #[serde(default)]
        capabilities: Value,
        #[serde(default)]
        metadata: Value,
    },
    ClientHeartbeat {
        client_id: Uuid,
        ttl_ms: u64,
        #[serde(default)]
        capabilities: Option<Value>,
    },
    ClientList {
        #[serde(default)]
        include_stale: bool,
        #[serde(default)]
        cursor: Option<Uuid>,
        #[serde(default = "default_registry_limit")]
        limit: usize,
    },
    ClientBindingList {
        #[serde(default)]
        include_stale: bool,
        #[serde(default)]
        cursor: Option<Uuid>,
        #[serde(default = "default_registry_limit")]
        limit: usize,
    },
    ClientDisconnect {
        client_id: Uuid,
    },
    ClientBindWorkspace {
        client_id: Uuid,
        workspace_id: Uuid,
        access_mode: ClientAccessMode,
        #[serde(default)]
        capabilities: Value,
        #[serde(default)]
        metadata: Value,
    },
    ClientUnbindWorkspace {
        binding_id: Uuid,
    },
    Shutdown,
}

fn default_registry_limit() -> usize {
    REGISTRY_PAGE_LIMIT_DEFAULT
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct IpcResponse {
    pub schema_version: String,
    pub request_id: Uuid,
    pub status: IpcStatus,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<IpcError>,
}

impl IpcResponse {
    pub fn success(request_id: Uuid, result: Value) -> Self {
        Self {
            schema_version: IPC_SCHEMA_VERSION.to_string(),
            request_id,
            status: IpcStatus::Ok,
            result: Some(result),
            error: None,
        }
    }

    pub fn error(request_id: Uuid, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            schema_version: IPC_SCHEMA_VERSION.to_string(),
            request_id,
            status: IpcStatus::Error,
            result: None,
            error: Some(IpcError {
                code: code.into(),
                message: message.into(),
            }),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum IpcStatus {
    Ok,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct IpcError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DaemonStatus {
    pub product: String,
    pub version: String,
    pub pid: u32,
    pub started_at_unix_ms: i64,
    pub state_database: String,
    pub endpoint: String,
    pub peer_credential_check: bool,
    pub concurrent_clients: bool,
    pub workspace_registry: bool,
    pub client_registry: bool,
    pub supported_client_kinds: Vec<ClientKind>,
    pub production_claim_allowed: bool,
}
