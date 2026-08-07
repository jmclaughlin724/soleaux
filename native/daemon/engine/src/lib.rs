use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use soleaux_mcp::{PUBLIC_ROOT_TOOL_MAX, PublicMcpServer, ToolSubstitution};
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct IntegrationSelection {
    pub substitutions: Vec<ToolSubstitution>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DoctorReport {
    pub product: String,
    pub version: String,
    pub native_daemon: bool,
    pub workspace: String,
    pub workspace_exists: bool,
    pub public_root_tools: usize,
    pub public_root_tool_ceiling: usize,
    pub active_tools: Vec<String>,
    pub substitutions: Vec<ToolSubstitution>,
    pub transport: Vec<String>,
    pub production_claim_allowed: bool,
}

pub async fn prepared_server(
    root: impl AsRef<Path>,
    selection: &IntegrationSelection,
) -> Result<PublicMcpServer> {
    let mut server = PublicMcpServer::new(root)?;
    for substitution in &selection.substitutions {
        server = server.substitute_tool(&substitution.replace, &substitution.with)?;
    }
    // Attach the daemon-owned canonical database only when it already exists;
    // a standalone serve stays detached rather than materializing state.
    if let Ok(paths) = soleaux_ipc::SoleauxPaths::resolve() {
        server = server.with_canonical_state(&paths.state_database)?;
    }
    server.prepare().await?;
    Ok(server)
}

pub async fn serve_stdio(root: impl AsRef<Path>, selection: IntegrationSelection) -> Result<()> {
    prepared_server(root, &selection).await?.serve_stdio().await
}

pub async fn serve_streamable_http(
    root: impl AsRef<Path>,
    selection: IntegrationSelection,
    address: SocketAddr,
    token: String,
) -> Result<()> {
    prepared_server(root, &selection)
        .await?
        .serve_streamable_http(address, token)
        .await
}

pub async fn index(
    root: impl AsRef<Path>,
    selection: IntegrationSelection,
) -> Result<serde_json::Value> {
    let server = prepared_server(root, &selection).await?;
    let info = server
        .call_async("repo_info", &serde_json::json!({}))
        .await?;
    Ok(serde_json::to_value(info)?)
}

pub async fn doctor(
    root: impl AsRef<Path>,
    selection: IntegrationSelection,
) -> Result<DoctorReport> {
    let root = root.as_ref();
    let canonical = if root.exists() {
        std::fs::canonicalize(root).with_context(|| format!("resolving {}", root.display()))?
    } else {
        PathBuf::from(root)
    };
    let server = prepared_server(&canonical, &selection).await?;
    Ok(DoctorReport {
        product: "Soleaux".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        native_daemon: true,
        workspace: canonical.to_string_lossy().to_string(),
        workspace_exists: canonical.is_dir(),
        public_root_tools: server.tools().len(),
        public_root_tool_ceiling: PUBLIC_ROOT_TOOL_MAX,
        active_tools: server.active_tool_names().to_vec(),
        substitutions: server.substitutions().to_vec(),
        transport: vec!["stdio".to_string(), "streamable-http".to_string()],
        production_claim_allowed: false,
    })
}
