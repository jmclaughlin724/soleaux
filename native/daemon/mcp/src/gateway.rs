//! Native namespaced MCP gateway and CLI-mediated credential store.
//!
//! Gateway backends never become root tools. They are discovered through the
//! registry and invoked explicitly through the CLI using a namespace-qualified
//! backend name. OAuth credentials are written only by foreground CLI commands
//! to the per-user Soleaux home, never to the repository worktree.

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    env, fs,
    io::Write,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command,
    time::timeout,
};

const MAX_CONFIG_BYTES: u64 = 512 * 1024;
const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum GatewayTransport {
    Stdio,
    StreamableHttp,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GatewayBackend {
    pub name: String,
    pub namespace: String,
    pub enabled: bool,
    pub transport: GatewayTransport,
    pub command: Vec<String>,
    pub url: Option<String>,
    pub cwd: Option<String>,
    pub environment: BTreeMap<String, String>,
    pub auth: String,
    pub scopes: Vec<String>,
    pub config_path: String,
    pub config_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GatewayStatus {
    pub backend: GatewayBackend,
    pub authenticated: bool,
    pub credential_store: String,
    pub root_tool_inflation: bool,
    pub available: bool,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GatewayInvocation {
    pub backend: String,
    pub namespace: String,
    pub tool: String,
    pub response: Value,
    pub transport: GatewayTransport,
}

pub fn soleaux_home() -> Result<PathBuf> {
    env::var_os("SOLEAUX_HOME")
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|path| path.join(".soleaux")))
        .context("unable to determine SOLEAUX_HOME")
}

pub fn discover_backends(root: &Path) -> Result<Vec<GatewayBackend>> {
    let config_path = root.join("soleaux.toml");
    if !config_path.is_file() {
        return Ok(Vec::new());
    }
    let metadata = fs::metadata(&config_path)?;
    if metadata.len() > MAX_CONFIG_BYTES {
        bail!("soleaux.toml exceeds the native gateway configuration ceiling");
    }
    let content = fs::read_to_string(&config_path)?;
    parse_backends(&content, "soleaux.toml")
}

fn parse_backends(content: &str, config_path: &str) -> Result<Vec<GatewayBackend>> {
    let mut sections = BTreeMap::<String, BTreeMap<String, String>>::new();
    let mut current: Option<String> = None;
    for raw in content.lines() {
        let line = strip_comment(raw).trim();
        if line.is_empty() {
            continue;
        }
        if let Some(name) = line
            .strip_prefix("[mcp.")
            .and_then(|value| value.strip_suffix(']'))
        {
            validate_identifier(name, "backend")?;
            current = Some(name.to_string());
            sections.entry(name.to_string()).or_default();
            continue;
        }
        let Some(section) = current.as_ref() else {
            continue;
        };
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        sections
            .entry(section.clone())
            .or_default()
            .insert(key.trim().to_string(), value.trim().to_string());
    }

    let digest = sha256_hex(content.as_bytes());
    let mut backends = Vec::new();
    for (name, values) in sections {
        let command = values
            .get("command")
            .map(|value| parse_string_array(value))
            .transpose()?
            .unwrap_or_default();
        let url = values
            .get("url")
            .map(|value| parse_string(value))
            .transpose()?;
        if command.is_empty() == url.is_none() {
            bail!("gateway backend {name} must declare exactly one of command or url");
        }
        let namespace = values
            .get("namespace")
            .map(|value| parse_string(value))
            .transpose()?
            .unwrap_or_else(|| name.clone());
        validate_identifier(&namespace, "namespace")?;
        let enabled = values
            .get("enabled")
            .map(|value| parse_bool(value))
            .transpose()?
            .unwrap_or(true);
        let auth = values
            .get("auth")
            .map(|value| parse_string(value))
            .transpose()?
            .unwrap_or_else(|| "none".to_string());
        if !matches!(auth.as_str(), "none" | "bearer" | "oauth") {
            bail!("gateway backend {name} uses unsupported auth mode {auth}");
        }
        let cwd = values
            .get("cwd")
            .map(|value| parse_string(value))
            .transpose()?;
        let scopes = values
            .get("oauth_scopes")
            .map(|value| parse_string_array(value))
            .transpose()?
            .unwrap_or_default();
        let mut environment = BTreeMap::new();
        for (key, value) in &values {
            if let Some(name) = key.strip_prefix("env.") {
                validate_environment_name(name)?;
                environment.insert(name.to_string(), parse_string(value)?);
            }
        }
        backends.push(GatewayBackend {
            name: name.clone(),
            namespace,
            enabled,
            transport: if command.is_empty() {
                GatewayTransport::StreamableHttp
            } else {
                GatewayTransport::Stdio
            },
            command,
            url,
            cwd,
            environment,
            auth,
            scopes,
            config_path: config_path.to_string(),
            config_digest: digest.clone(),
        });
    }
    backends.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(backends)
}

pub fn backend_status(root: &Path) -> Result<Vec<GatewayStatus>> {
    let home = soleaux_home()?;
    discover_backends(root)?
        .into_iter()
        .map(|backend| {
            let credential = credential_path(&home, &backend.name)?;
            let authenticated = backend.auth == "none" || credential.is_file();
            let available = backend.enabled
                && match backend.transport {
                    GatewayTransport::Stdio => executable_available(&backend.command[0]),
                    GatewayTransport::StreamableHttp => backend.url.is_some(),
                };
            let message = if !backend.enabled {
                "disabled by workspace configuration"
            } else if !available {
                "configured backend executable or URL is unavailable"
            } else if !authenticated {
                "authentication required; run `soleaux mcp login <name>`"
            } else {
                "ready"
            };
            Ok(GatewayStatus {
                backend,
                authenticated,
                credential_store: credential.to_string_lossy().to_string(),
                root_tool_inflation: false,
                available,
                message: message.to_string(),
            })
        })
        .collect()
}

pub fn store_credential(backend: &str, token: &str) -> Result<PathBuf> {
    validate_identifier(backend, "backend")?;
    let token = token.trim();
    if token.is_empty() || token.len() > 64 * 1024 || token.chars().any(char::is_control) {
        bail!("gateway credential is empty, oversized, or contains control characters");
    }
    let path = credential_path(&soleaux_home()?, backend)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
        secure_directory(parent)?;
    }
    atomic_write(
        &path,
        serde_json::to_vec_pretty(&json!({
            "schema_version":"soleaux.gateway.credential/v1",
            "backend":backend,
            "token":token,
        }))?
        .as_slice(),
    )?;
    secure_file(&path)?;
    Ok(path)
}

pub fn clear_credential(backend: &str) -> Result<bool> {
    let path = credential_path(&soleaux_home()?, backend)?;
    if path.is_file() {
        fs::remove_file(path)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

pub fn credential_present(backend: &str) -> Result<bool> {
    Ok(credential_path(&soleaux_home()?, backend)?.is_file())
}

pub async fn invoke(
    root: &Path,
    backend_name: &str,
    tool: &str,
    arguments: Value,
) -> Result<GatewayInvocation> {
    validate_identifier(backend_name, "backend")?;
    if tool.trim().is_empty() {
        bail!("gateway tool name must not be empty");
    }
    let backend = discover_backends(root)?
        .into_iter()
        .find(|backend| backend.name == backend_name)
        .with_context(|| format!("unknown gateway backend {backend_name}"))?;
    if !backend.enabled {
        bail!("gateway backend {backend_name} is disabled");
    }
    if backend.auth != "none" && !credential_present(backend_name)? {
        bail!("gateway backend {backend_name} requires CLI-mediated login");
    }
    match backend.transport {
        GatewayTransport::Stdio => invoke_stdio(root, &backend, tool, arguments).await,
        GatewayTransport::StreamableHttp => bail!(
            "native HTTPS gateway invocation requires a configured transport adapter; backend remains registered and discoverable without inflating tools/list"
        ),
    }
}

async fn invoke_stdio(
    root: &Path,
    backend: &GatewayBackend,
    tool: &str,
    arguments: Value,
) -> Result<GatewayInvocation> {
    let program = backend.command.first().context("empty gateway command")?;
    let mut command = Command::new(program);
    command.args(&backend.command[1..]);
    let cwd = resolve_cwd(root, backend.cwd.as_deref())?;
    command.current_dir(cwd);
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.env_clear();
    for name in ["PATH", "HOME", "USER", "TMPDIR", "TEMP", "SystemRoot"] {
        if let Some(value) = env::var_os(name) {
            command.env(name, value);
        }
    }
    for (name, value) in &backend.environment {
        command.env(name, value);
    }
    if backend.auth != "none" {
        let credential = read_credential(&backend.name)?;
        command.env("SOLEAUX_GATEWAY_TOKEN", credential);
    }
    let mut child = command
        .kill_on_drop(true)
        .spawn()
        .with_context(|| format!("starting gateway backend {}", backend.name))?;
    let mut stdin = child
        .stdin
        .take()
        .context("gateway backend stdin unavailable")?;
    let stdout = child
        .stdout
        .take()
        .context("gateway backend stdout unavailable")?;
    let mut reader = BufReader::new(stdout).lines();

    write_json_line(
        &mut stdin,
        &json!({
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Soleaux Gateway","version":env!("CARGO_PKG_VERSION")}}
        }),
    )
    .await?;
    let initialized = read_response(&mut reader, 1).await?;
    if initialized.get("error").is_some() {
        bail!("gateway backend initialize failed: {initialized}");
    }
    write_json_line(
        &mut stdin,
        &json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    )
    .await?;
    write_json_line(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":tool,"arguments":arguments}}),
    )
    .await?;
    let response = read_response(&mut reader, 2).await?;
    drop(stdin);
    let _ = timeout(Duration::from_secs(2), child.wait()).await;
    if let Some(error) = response.get("error") {
        bail!("gateway backend tool failed: {error}");
    }
    Ok(GatewayInvocation {
        backend: backend.name.clone(),
        namespace: backend.namespace.clone(),
        tool: tool.to_string(),
        response: response.get("result").cloned().unwrap_or(Value::Null),
        transport: GatewayTransport::Stdio,
    })
}

async fn write_json_line(stdin: &mut tokio::process::ChildStdin, value: &Value) -> Result<()> {
    let bytes = serde_json::to_vec(value)?;
    stdin.write_all(&bytes).await?;
    stdin.write_all(b"\n").await?;
    stdin.flush().await?;
    Ok(())
}

async fn read_response(
    reader: &mut tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    id: u64,
) -> Result<Value> {
    timeout(DEFAULT_TIMEOUT, async {
        let mut consumed = 0usize;
        while let Some(line) = reader.next_line().await? {
            consumed = consumed.saturating_add(line.len());
            if consumed > MAX_RESPONSE_BYTES {
                bail!("gateway backend response exceeded the bounded response ceiling");
            }
            let value: Value = serde_json::from_str(&line)
                .with_context(|| "gateway backend emitted a non-JSON line")?;
            if value.get("id").and_then(Value::as_u64) == Some(id) {
                return Ok(value);
            }
        }
        bail!("gateway backend closed before returning response id {id}")
    })
    .await
    .context("gateway backend request timed out")?
}

fn credential_path(home: &Path, backend: &str) -> Result<PathBuf> {
    validate_identifier(backend, "backend")?;
    Ok(home
        .join("credentials")
        .join("mcp")
        .join(format!("{backend}.json")))
}

fn read_credential(backend: &str) -> Result<String> {
    let path = credential_path(&soleaux_home()?, backend)?;
    let value: Value = serde_json::from_slice(&fs::read(&path).with_context(|| {
        format!(
            "reading gateway credential {}; run `soleaux mcp login {backend}`",
            path.display()
        )
    })?)?;
    value
        .get("token")
        .and_then(Value::as_str)
        .map(str::to_string)
        .context("gateway credential record is malformed")
}

fn resolve_cwd(root: &Path, configured: Option<&str>) -> Result<PathBuf> {
    let root = fs::canonicalize(root)?;
    let candidate = configured
        .map(|value| root.join(value))
        .unwrap_or_else(|| root.clone());
    let candidate = fs::canonicalize(candidate)?;
    if !candidate.is_dir() || !candidate.starts_with(&root) {
        bail!("gateway cwd escapes the workspace or is not a directory");
    }
    Ok(candidate)
}

fn executable_available(program: &str) -> bool {
    let path = Path::new(program);
    if path.components().count() > 1 {
        return path.is_file();
    }
    env::var_os("PATH").is_some_and(|paths| {
        env::split_paths(&paths).any(|directory| {
            let candidate = directory.join(program);
            candidate.is_file()
                || (cfg!(windows) && directory.join(format!("{program}.exe")).is_file())
        })
    })
}

fn parse_string(value: &str) -> Result<String> {
    let value = value.trim();
    if value.len() < 2 || !value.starts_with('"') || !value.ends_with('"') {
        bail!("expected a quoted TOML string, got {value}");
    }
    serde_json::from_str(value).context("decoding gateway TOML string")
}

fn parse_string_array(value: &str) -> Result<Vec<String>> {
    let value = value.trim();
    if !value.starts_with('[') || !value.ends_with(']') {
        bail!("expected a TOML string array, got {value}");
    }
    serde_json::from_str(value).context("decoding gateway TOML string array")
}

fn parse_bool(value: &str) -> Result<bool> {
    match value.trim() {
        "true" => Ok(true),
        "false" => Ok(false),
        value => bail!("expected a TOML boolean, got {value}"),
    }
}

fn strip_comment(line: &str) -> &str {
    let mut quoted = false;
    let mut escaped = false;
    for (index, byte) in line.bytes().enumerate() {
        if escaped {
            escaped = false;
            continue;
        }
        match byte {
            b'\\' if quoted => escaped = true,
            b'"' => quoted = !quoted,
            b'#' if !quoted => return &line[..index],
            _ => {}
        }
    }
    line
}

fn validate_identifier(value: &str, kind: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || !value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.')
        })
    {
        bail!("invalid gateway {kind} identifier: {value}");
    }
    Ok(())
}

fn validate_environment_name(value: &str) -> Result<()> {
    if value.is_empty()
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_')
    {
        bail!("invalid gateway environment variable name: {value}");
    }
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("gateway write path has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("credential"),
        std::process::id()
    ));
    {
        let mut file = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
    }
    fs::rename(&temporary, path)?;
    Ok(())
}

#[cfg(unix)]
fn secure_directory(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn secure_directory(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn secure_file(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(not(unix))]
fn secure_file(_path: &Path) -> Result<()> {
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn parses_namespaced_backends_without_root_tools() {
        let parsed = parse_backends(
            r#"
[mcp.docs]
command = ["docs-mcp", "--stdio"]
namespace = "team.docs"
auth = "oauth"
oauth_scopes = ["read"]

[mcp.local]
url = "http://127.0.0.1:4555/mcp"
enabled = true
"#,
            "soleaux.toml",
        )
        .expect("parse");
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].namespace, "team.docs");
        assert_eq!(parsed[0].auth, "oauth");
        assert_eq!(parsed[1].transport, GatewayTransport::StreamableHttp);
    }

    #[test]
    fn credentials_are_outside_the_workspace() {
        let directory = tempdir().expect("tempdir");
        let home = directory.path().join("home");
        let _home_guard = crate::test_environment::SoleauxHomeGuard::set(&home);
        let path = store_credential("team.docs", "opaque-token").expect("store");
        assert!(path.starts_with(&home));
        assert!(!path.to_string_lossy().contains("workspace"));
        assert!(credential_present("team.docs").expect("present"));
        assert!(clear_credential("team.docs").expect("clear"));
    }
}
