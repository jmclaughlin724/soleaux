//! Long-lived LSP supervision with an 800 ms interactive soft deadline.
//!
//! A server must complete initialization and advertise the applicable
//! capability before Soleaux exposes a semantic operation. Requests that miss
//! the soft deadline return cached data or a stable pending ID; the hard-bounded
//! request continues and publishes a completion event.

use anyhow::{Context, Result, bail};
use dashmap::DashMap;
use moka::future::Cache;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::{Mutex, RwLock, broadcast, mpsc, oneshot},
    task::JoinHandle,
    time::timeout,
};
use url::Url;
use uuid::Uuid;

pub const DEFAULT_SOFT_DEADLINE: Duration = Duration::from_millis(800);
pub const DEFAULT_HARD_TIMEOUT: Duration = Duration::from_secs(15);

pub const LSP_CAPABILITY_MATRIX_SCHEMA_VERSION: &str = "soleaux.lsp-capability-matrix/v1";
pub const LSP_CAPABILITY_MATRIX_JSON: &str =
    include_str!("../../../contracts/lsp-capability-matrix-v1.json");

/// One wired language-server family. `id` is simultaneously the supervisor
/// `server_id`, the routing key [`language_key`] collapses onto, and the matrix
/// row identity; `languages` are indexer names, `extensions` the file suffixes
/// behind them, and `commands` the ordered launch candidates (primary first).
#[derive(Debug, Clone, Copy)]
pub struct LanguageServerFamily {
    pub id: &'static str,
    pub languages: &'static [&'static str],
    pub extensions: &'static [&'static str],
    pub commands: &'static [FamilyCommand],
}

#[derive(Debug, Clone, Copy)]
pub struct FamilyCommand {
    pub command: &'static str,
    pub arguments: &'static [&'static str],
}

const STDIO: &[&str] = &["--stdio"];
const NO_ARGUMENTS: &[&str] = &[];

pub const LANGUAGE_SERVER_FAMILIES: &[LanguageServerFamily] = &[
    LanguageServerFamily {
        id: "typescript",
        languages: &["typescript", "tsx", "javascript", "jsx"],
        extensions: &["ts", "mts", "cts", "tsx", "js", "mjs", "cjs", "jsx"],
        commands: &[
            FamilyCommand {
                command: "vtsls",
                arguments: STDIO,
            },
            FamilyCommand {
                command: "typescript-language-server",
                arguments: STDIO,
            },
        ],
    },
    LanguageServerFamily {
        id: "python",
        languages: &["python"],
        extensions: &["py", "pyi"],
        commands: &[
            FamilyCommand {
                command: "basedpyright-langserver",
                arguments: STDIO,
            },
            FamilyCommand {
                command: "pyright-langserver",
                arguments: STDIO,
            },
        ],
    },
    LanguageServerFamily {
        id: "bash",
        languages: &["bash"],
        extensions: &["sh", "bash", "zsh"],
        commands: &[FamilyCommand {
            command: "bash-language-server",
            arguments: &["start"],
        }],
    },
    LanguageServerFamily {
        id: "rust",
        languages: &["rust"],
        extensions: &["rs"],
        commands: &[FamilyCommand {
            command: "rust-analyzer",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "go",
        languages: &["go"],
        extensions: &["go"],
        commands: &[FamilyCommand {
            command: "gopls",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "swift",
        languages: &["swift"],
        extensions: &["swift"],
        commands: &[FamilyCommand {
            command: "sourcekit-lsp",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "cpp",
        languages: &["c", "cpp"],
        extensions: &["c", "h", "cpp", "cc", "cxx", "hpp", "hh", "hxx"],
        commands: &[FamilyCommand {
            command: "clangd",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "kotlin",
        languages: &["kotlin"],
        extensions: &["kt", "kts"],
        commands: &[FamilyCommand {
            command: "kotlin-language-server",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "java",
        languages: &["java"],
        extensions: &["java"],
        commands: &[FamilyCommand {
            command: "jdtls",
            arguments: NO_ARGUMENTS,
        }],
    },
    LanguageServerFamily {
        id: "vue",
        languages: &["vue"],
        extensions: &["vue"],
        commands: &[FamilyCommand {
            command: "vue-language-server",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "svelte",
        languages: &["svelte"],
        extensions: &["svelte"],
        commands: &[FamilyCommand {
            command: "svelteserver",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "astro",
        languages: &["astro"],
        extensions: &["astro"],
        commands: &[FamilyCommand {
            command: "astro-ls",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "mdx",
        languages: &["mdx"],
        extensions: &["mdx"],
        commands: &[FamilyCommand {
            command: "mdx-language-server",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "yaml",
        languages: &["yaml"],
        extensions: &["yaml", "yml"],
        commands: &[FamilyCommand {
            command: "yaml-language-server",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "json",
        languages: &["json"],
        extensions: &["json", "jsonc"],
        commands: &[FamilyCommand {
            command: "vscode-json-language-server",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "html",
        languages: &["html"],
        extensions: &["html", "htm"],
        commands: &[FamilyCommand {
            command: "vscode-html-language-server",
            arguments: STDIO,
        }],
    },
    LanguageServerFamily {
        id: "css",
        languages: &["css", "scss", "less"],
        extensions: &["css", "scss", "less"],
        commands: &[FamilyCommand {
            command: "vscode-css-language-server",
            arguments: STDIO,
        }],
    },
];

/// Collapse an indexer language name onto its family routing key; names outside
/// every family pass through unchanged.
pub fn language_key(language: &str) -> &str {
    LANGUAGE_SERVER_FAMILIES
        .iter()
        .find(|family| family.languages.contains(&language))
        .map_or(language, |family| family.id)
}

pub fn lsp_capability_matrix_sha256() -> String {
    let digest = Sha256::digest(LSP_CAPABILITY_MATRIX_JSON.as_bytes());
    format!("{digest:x}")
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LspCapabilityMatrix {
    schema_version: String,
    as_of_date: String,
    task: String,
    families: Vec<LspCapabilityFamilyRow>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LspCapabilityFamilyRow {
    id: String,
    language_key: String,
    extensions: Vec<String>,
    primary_command: LspCapabilityCommandRow,
    fallback_commands: Vec<LspCapabilityCommandRow>,
    probe_state: String,
    push_diagnostics: bool,
    multi_root: bool,
    workspace_edits: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LspCapabilityCommandRow {
    command: String,
    arguments: Vec<String>,
}

/// Fail unless the published matrix v1 mirrors [`LANGUAGE_SERVER_FAMILIES`]
/// row for row and keeps every not-yet capability column at its truthful
/// pre-conformance value (P5-023 owns subsystem depth).
pub fn validate_lsp_capability_matrix() -> Result<()> {
    let matrix: LspCapabilityMatrix = serde_json::from_str(LSP_CAPABILITY_MATRIX_JSON)
        .context("parsing lsp-capability-matrix-v1.json")?;
    if matrix.schema_version != LSP_CAPABILITY_MATRIX_SCHEMA_VERSION {
        bail!(
            "LSP capability matrix declares schema {}; expected {}",
            matrix.schema_version,
            LSP_CAPABILITY_MATRIX_SCHEMA_VERSION
        );
    }
    if matrix.as_of_date.is_empty() || matrix.task.is_empty() {
        bail!("LSP capability matrix omitted asOfDate or task");
    }
    if matrix.families.len() != LANGUAGE_SERVER_FAMILIES.len() {
        bail!(
            "LSP capability matrix lists {} families; the wired table has {}",
            matrix.families.len(),
            LANGUAGE_SERVER_FAMILIES.len()
        );
    }
    for (row, family) in matrix.families.iter().zip(LANGUAGE_SERVER_FAMILIES) {
        if row.id != family.id {
            bail!(
                "LSP capability matrix family {} does not match wired family {}",
                row.id,
                family.id
            );
        }
        if row.language_key != family.id {
            bail!(
                "LSP capability matrix family {} declares language key {}; routing requires the family id",
                family.id,
                row.language_key
            );
        }
        if row.extensions != family.extensions {
            bail!(
                "LSP capability matrix extensions diverge from the wired table for family {}",
                family.id
            );
        }
        let candidates: Vec<&LspCapabilityCommandRow> = std::iter::once(&row.primary_command)
            .chain(row.fallback_commands.iter())
            .collect();
        if candidates.len() != family.commands.len()
            || candidates
                .iter()
                .zip(family.commands)
                .any(|(candidate, wired)| {
                    candidate.command != wired.command || candidate.arguments != wired.arguments
                })
        {
            bail!(
                "LSP capability matrix commands diverge from the wired table for family {}",
                family.id
            );
        }
        if row.probe_state != "unprobed" {
            bail!(
                "LSP capability matrix v1 must default probeState to unprobed for family {}",
                family.id
            );
        }
        if row.push_diagnostics || row.multi_root || row.workspace_edits {
            bail!(
                "LSP capability matrix v1 must keep pushDiagnostics, multiRoot, and workspaceEdits false for family {}",
                family.id
            );
        }
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspServerSpec {
    pub server_id: String,
    pub command: String,
    pub arguments: Vec<String>,
    pub root_uri: String,
    #[serde(default)]
    pub initialization_options: Value,
    #[serde(default)]
    pub workspace_folders: Vec<Value>,
    #[serde(default = "default_hard_timeout_ms")]
    pub hard_timeout_ms: u64,
    #[serde(default = "default_idle_timeout_ms")]
    pub idle_timeout_ms: u64,
    #[serde(default = "default_rss_limit_bytes")]
    pub rss_limit_bytes: u64,
    #[serde(default = "default_maximum_open_documents")]
    pub maximum_open_documents: usize,
}

impl LspServerSpec {
    pub fn hard_timeout(&self) -> Duration {
        Duration::from_millis(self.hard_timeout_ms.clamp(10_000, 20_000))
    }
}

fn default_hard_timeout_ms() -> u64 {
    DEFAULT_HARD_TIMEOUT.as_millis() as u64
}
fn default_idle_timeout_ms() -> u64 {
    15 * 60 * 1000
}
fn default_rss_limit_bytes() -> u64 {
    2 * 1024 * 1024 * 1024
}
fn default_maximum_open_documents() -> usize {
    512
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspProbe {
    pub server_id: String,
    pub capabilities: Value,
    pub command: String,
    pub arguments: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspQuery {
    pub workspace_id: Uuid,
    pub server_id: String,
    pub method: String,
    pub params: Value,
    pub cache_key: String,
    pub document_uri: Option<String>,
    pub document_version: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", tag = "state")]
pub enum LspQueryResult {
    Ready {
        request_id: Uuid,
        value: Value,
        cache_status: String,
        duration_ms: u64,
        server_id: String,
        method: String,
    },
    Pending {
        request_id: Uuid,
        cached: Option<Value>,
        pending: bool,
        server_id: String,
        method: String,
        soft_deadline_ms: u64,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspCompletionEvent {
    pub request_id: Uuid,
    pub server_id: String,
    pub method: String,
    pub value: Option<Value>,
    pub error: Option<String>,
    pub duration_ms: u64,
}

#[derive(Debug, Clone)]
struct CachedResult {
    value: Value,
    document_version: Option<i64>,
}

struct ServerProcess {
    spec: LspServerSpec,
    child: Mutex<Child>,
    outbound: mpsc::Sender<Value>,
    pending: Arc<DashMap<u64, oneshot::Sender<Result<Value, String>>>>,
    next_id: AtomicU64,
    capabilities: RwLock<Value>,
    reader_task: JoinHandle<()>,
    writer_task: JoinHandle<()>,
    stderr_task: JoinHandle<()>,
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        self.reader_task.abort();
        self.writer_task.abort();
        self.stderr_task.abort();
        if let Ok(mut child) = self.child.try_lock() {
            let _ = child.start_kill();
        }
    }
}

impl ServerProcess {
    async fn start(spec: LspServerSpec) -> Result<Arc<Self>> {
        let mut command = Command::new(&spec.command);
        command
            .args(&spec.arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .env_clear();
        for key in [
            "PATH",
            "HOME",
            "USERPROFILE",
            "TMPDIR",
            "TEMP",
            "LANG",
            "LC_ALL",
            "SYSTEMROOT",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        let mut child = command
            .spawn()
            .with_context(|| format!("starting LSP server {}", spec.server_id))?;
        let stdin = child
            .stdin
            .take()
            .context("LSP server did not expose stdin")?;
        let stdout = child
            .stdout
            .take()
            .context("LSP server did not expose stdout")?;
        let stderr = child
            .stderr
            .take()
            .context("LSP server did not expose stderr")?;

        let (outbound, mut outbound_rx) = mpsc::channel::<Value>(256);
        let writer_task = tokio::spawn(async move {
            let mut writer = stdin;
            while let Some(message) = outbound_rx.recv().await {
                if write_lsp_message(&mut writer, &message).await.is_err() {
                    break;
                }
            }
        });

        let pending: Arc<DashMap<u64, oneshot::Sender<Result<Value, String>>>> =
            Arc::new(DashMap::new());
        let reader_pending = pending.clone();
        let reader_outbound = outbound.clone();
        let reader_task = tokio::spawn(async move {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_lsp_message(&mut reader).await {
                    Ok(Some(message)) => {
                        if let Some(id) = message.get("id").and_then(Value::as_u64) {
                            if message.get("method").is_none() {
                                if let Some((_, sender)) = reader_pending.remove(&id) {
                                    let value = if let Some(error) = message.get("error") {
                                        Err(error.to_string())
                                    } else {
                                        Ok(message.get("result").cloned().unwrap_or(Value::Null))
                                    };
                                    let _ = sender.send(value);
                                }
                            } else {
                                let response = default_server_request_response(&message);
                                let _ = reader_outbound.send(response).await;
                            }
                        }
                    }
                    Ok(None) => break,
                    Err(error) => {
                        let detail = error.to_string();
                        let keys = reader_pending
                            .iter()
                            .map(|entry| *entry.key())
                            .collect::<Vec<_>>();
                        for key in keys {
                            if let Some((_, sender)) = reader_pending.remove(&key) {
                                let _ = sender.send(Err(detail.clone()));
                            }
                        }
                        break;
                    }
                }
            }
        });

        let stderr_task = tokio::spawn(async move {
            let mut reader = BufReader::new(stderr);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        tracing::debug!(target: "soleaux_lsp_stderr", message = %line.trim_end())
                    }
                }
            }
        });

        let process = Arc::new(Self {
            spec: spec.clone(),
            child: Mutex::new(child),
            outbound,
            pending,
            next_id: AtomicU64::new(1),
            capabilities: RwLock::new(Value::Null),
            reader_task,
            writer_task,
            stderr_task,
        });
        let initialize = process
            .request_raw(
                "initialize",
                json!({
                    "processId": std::process::id(),
                    "rootUri": spec.root_uri,
                    "workspaceFolders": spec.workspace_folders,
                    "capabilities": {
                        "workspace": {
                            "workspaceFolders": true,
                            "configuration": true,
                            "didChangeWatchedFiles": {"dynamicRegistration": true}
                        },
                        "textDocument": {
                            "definition": {"linkSupport": true},
                            "references": {},
                            "implementation": {"linkSupport": true},
                            "hover": {"contentFormat": ["markdown", "plaintext"]},
                            "documentSymbol": {"hierarchicalDocumentSymbolSupport": true},
                            "callHierarchy": {"dynamicRegistration": false},
                            "completion": {"completionItem": {"snippetSupport": false}},
                            "signatureHelp": {"signatureInformation": {"documentationFormat": ["markdown", "plaintext"]}},
                            "codeAction": {"codeActionLiteralSupport": {"codeActionKind": {"valueSet": []}}},
                            "diagnostic": {"dynamicRegistration": false},
                            "publishDiagnostics": {"versionSupport": true},
                            "rename": {"prepareSupport": true},
                            "formatting": {"dynamicRegistration": false},
                            "rangeFormatting": {"dynamicRegistration": false}
                        }
                    },
                    "initializationOptions": spec.initialization_options,
                    "clientInfo": {"name":"Soleaux","version":env!("CARGO_PKG_VERSION")}
                }),
                spec.hard_timeout(),
            )
            .await
            .context("LSP initialize failed")?;
        let capabilities = initialize
            .get("capabilities")
            .cloned()
            .unwrap_or(Value::Null);
        if !capabilities.is_object() {
            bail!("LSP initialize result omitted capabilities");
        }
        *process.capabilities.write().await = capabilities;
        process.notify("initialized", json!({})).await?;
        Ok(process)
    }

    async fn request_raw(
        &self,
        method: &str,
        params: Value,
        hard_timeout: Duration,
    ) -> Result<Value> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        self.pending.insert(id, sender);
        let payload = json!({"jsonrpc":"2.0","id":id,"method":method,"params":params});
        if let Err(error) = self.outbound.send(payload).await {
            self.pending.remove(&id);
            return Err(error).context("LSP writer task stopped");
        }
        match timeout(hard_timeout, receiver).await {
            Ok(Ok(Ok(value))) => Ok(value),
            Ok(Ok(Err(error))) => bail!(error),
            Ok(Err(_)) => bail!("LSP response channel closed"),
            Err(_) => {
                self.pending.remove(&id);
                let _ = self.notify("$/cancelRequest", json!({"id":id})).await;
                bail!("LSP request exceeded hard timeout")
            }
        }
    }

    async fn notify(&self, method: &str, params: Value) -> Result<()> {
        self.outbound
            .send(json!({"jsonrpc":"2.0","method":method,"params":params}))
            .await
            .context("LSP writer task stopped")
    }

    fn is_alive(&self) -> bool {
        self.child
            .try_lock()
            .map(|mut child| child.try_wait().ok().flatten().is_none())
            .unwrap_or(true)
    }

    async fn probe(&self) -> LspProbe {
        LspProbe {
            server_id: self.spec.server_id.clone(),
            capabilities: self.capabilities.read().await.clone(),
            command: self.spec.command.clone(),
            arguments: self.spec.arguments.clone(),
        }
    }

    async fn supports(&self, method: &str) -> bool {
        let Some(property) = capability_property(method) else {
            return false;
        };
        let capabilities = self.capabilities.read().await;
        capability_enabled(capabilities.get(property))
    }
}

#[derive(Clone)]
pub struct LspSupervisor {
    servers: Arc<DashMap<String, Arc<ServerProcess>>>,
    cache: Cache<String, Arc<CachedResult>>,
    events: broadcast::Sender<LspCompletionEvent>,
    soft_deadline: Duration,
}

impl LspSupervisor {
    pub fn new(maximum_cache_weight_bytes: u64) -> Self {
        let cache = Cache::builder()
            .max_capacity(maximum_cache_weight_bytes)
            .weigher(|key: &String, value: &Arc<CachedResult>| {
                u32::try_from(key.len() + value.value.to_string().len()).unwrap_or(u32::MAX)
            })
            .time_to_idle(Duration::from_secs(15 * 60))
            .build();
        let (events, _) = broadcast::channel(2048);
        Self {
            servers: Arc::new(DashMap::new()),
            cache,
            events,
            soft_deadline: DEFAULT_SOFT_DEADLINE,
        }
    }

    pub fn with_soft_deadline(mut self, deadline: Duration) -> Self {
        self.soft_deadline = deadline.clamp(Duration::from_millis(50), Duration::from_secs(5));
        self
    }

    pub fn subscribe(&self) -> broadcast::Receiver<LspCompletionEvent> {
        self.events.subscribe()
    }

    pub async fn ensure_server(&self, spec: LspServerSpec) -> Result<LspProbe> {
        if let Some(existing) = self
            .servers
            .get(&spec.server_id)
            .map(|entry| Arc::clone(entry.value()))
        {
            if existing.is_alive() {
                return Ok(existing.probe().await);
            }
            self.servers.remove(&spec.server_id);
        }
        let server = ServerProcess::start(spec.clone()).await?;
        let probe = server.probe().await;
        self.servers.insert(spec.server_id, server);
        Ok(probe)
    }

    pub async fn open_document(
        &self,
        server_id: &str,
        uri: &str,
        language_id: &str,
        version: i64,
        text: &str,
    ) -> Result<()> {
        let server = self
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
            .context("LSP server is not running or failed its capability probe")?;
        if text.len() > 4 * 1024 * 1024 {
            bail!("LSP document exceeds the 4 MiB open-document cap");
        }
        server
            .notify(
                "textDocument/didOpen",
                json!({
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": version,
                        "text": text,
                    }
                }),
            )
            .await
    }

    pub async fn close_document(&self, server_id: &str, uri: &str) -> Result<()> {
        let server = self
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
            .context("LSP server is not running")?;
        server
            .notify("textDocument/didClose", json!({"textDocument":{"uri":uri}}))
            .await
    }

    pub async fn supports(&self, server_id: &str, method: &str) -> Result<bool> {
        let server = self
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
            .context("LSP server is not running or failed its capability probe")?;
        Ok(server.supports(method).await)
    }

    pub async fn query(&self, query: LspQuery) -> Result<LspQueryResult> {
        let server = self
            .servers
            .get(&query.server_id)
            .map(|entry| Arc::clone(entry.value()))
            .context("LSP server is not running or failed its capability probe")?;
        if !server.supports(&query.method).await {
            bail!(
                "LSP server {} did not advertise capability for {}",
                query.server_id,
                query.method
            );
        }
        let request_id = Uuid::now_v7();
        let cached = self
            .cache
            .get(&query.cache_key)
            .await
            .filter(|value| value.document_version == query.document_version);
        let started = Instant::now();
        let method = query.method.clone();
        let params = query.params.clone();
        let cache_key = query.cache_key.clone();
        let document_version = query.document_version;
        let hard_timeout = server.spec.hard_timeout();
        let mut task =
            tokio::spawn(async move { server.request_raw(&method, params, hard_timeout).await });
        match timeout(self.soft_deadline, &mut task).await {
            Ok(joined) => {
                let value = joined.context("LSP request task failed")??;
                self.cache
                    .insert(
                        cache_key,
                        Arc::new(CachedResult {
                            value: value.clone(),
                            document_version,
                        }),
                    )
                    .await;
                Ok(LspQueryResult::Ready {
                    request_id,
                    value,
                    cache_status: "live".into(),
                    duration_ms: started.elapsed().as_millis().try_into().unwrap_or(u64::MAX),
                    server_id: query.server_id,
                    method: query.method,
                })
            }
            Err(_) => {
                let cache = self.cache.clone();
                let events = self.events.clone();
                let server_id = query.server_id.clone();
                let event_method = query.method.clone();
                tokio::spawn(async move {
                    let result = task.await;
                    let duration_ms = started.elapsed().as_millis().try_into().unwrap_or(u64::MAX);
                    match result {
                        Ok(Ok(value)) => {
                            cache
                                .insert(
                                    cache_key,
                                    Arc::new(CachedResult {
                                        value: value.clone(),
                                        document_version,
                                    }),
                                )
                                .await;
                            let _ = events.send(LspCompletionEvent {
                                request_id,
                                server_id,
                                method: event_method,
                                value: Some(value),
                                error: None,
                                duration_ms,
                            });
                        }
                        Ok(Err(error)) => {
                            let _ = events.send(LspCompletionEvent {
                                request_id,
                                server_id,
                                method: event_method,
                                value: None,
                                error: Some(error.to_string()),
                                duration_ms,
                            });
                        }
                        Err(error) => {
                            let _ = events.send(LspCompletionEvent {
                                request_id,
                                server_id,
                                method: event_method,
                                value: None,
                                error: Some(error.to_string()),
                                duration_ms,
                            });
                        }
                    }
                });
                Ok(LspQueryResult::Pending {
                    request_id,
                    cached: cached.map(|value| value.value.clone()),
                    pending: true,
                    server_id: query.server_id,
                    method: query.method,
                    soft_deadline_ms: self.soft_deadline.as_millis().try_into().unwrap_or(800),
                })
            }
        }
    }

    pub async fn restart(&self, server_id: &str) -> Result<LspProbe> {
        let (_, server) = self
            .servers
            .remove(server_id)
            .with_context(|| format!("LSP server is not running: {server_id}"))?;
        let spec = server.spec.clone();
        drop(server);
        let replacement = ServerProcess::start(spec.clone()).await?;
        let probe = replacement.probe().await;
        self.servers.insert(spec.server_id, replacement);
        Ok(probe)
    }

    pub async fn probes(&self) -> Vec<LspProbe> {
        let servers = self
            .servers
            .iter()
            .map(|entry| Arc::clone(entry.value()))
            .collect::<Vec<_>>();
        let mut probes = Vec::with_capacity(servers.len());
        for server in servers {
            probes.push(server.probe().await);
        }
        probes.sort_by(|left, right| left.server_id.cmp(&right.server_id));
        probes
    }

    pub async fn invalidate(&self, cache_key: &str) {
        self.cache.invalidate(cache_key).await;
    }
}

pub fn capability_property(method: &str) -> Option<&'static str> {
    match method {
        "textDocument/definition" => Some("definitionProvider"),
        "textDocument/references" => Some("referencesProvider"),
        "textDocument/implementation" => Some("implementationProvider"),
        "textDocument/hover" => Some("hoverProvider"),
        "textDocument/documentSymbol" => Some("documentSymbolProvider"),
        "textDocument/prepareCallHierarchy"
        | "callHierarchy/incomingCalls"
        | "callHierarchy/outgoingCalls" => Some("callHierarchyProvider"),
        "textDocument/completion" => Some("completionProvider"),
        "textDocument/signatureHelp" => Some("signatureHelpProvider"),
        "textDocument/codeAction" => Some("codeActionProvider"),
        "textDocument/diagnostic" => Some("diagnosticProvider"),
        "textDocument/rename" | "textDocument/prepareRename" => Some("renameProvider"),
        "textDocument/formatting" => Some("documentFormattingProvider"),
        "textDocument/rangeFormatting" => Some("documentRangeFormattingProvider"),
        "workspace/symbol" => Some("workspaceSymbolProvider"),
        _ => None,
    }
}

fn capability_enabled(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(value)) => *value,
        Some(Value::Object(_)) => true,
        _ => false,
    }
}

fn default_server_request_response(request: &Value) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let result = match method {
        "workspace/configuration" => {
            let count = request
                .pointer("/params/items")
                .and_then(Value::as_array)
                .map_or(0, Vec::len);
            Value::Array(vec![Value::Null; count])
        }
        "workspace/workspaceFolders" => Value::Array(Vec::new()),
        "client/registerCapability"
        | "client/unregisterCapability"
        | "window/workDoneProgress/create" => Value::Null,
        _ => {
            return json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"Soleaux LSP client does not implement this server request"}});
        }
    };
    json!({"jsonrpc":"2.0","id":id,"result":result})
}

async fn write_lsp_message<W: AsyncWrite + Unpin>(writer: &mut W, value: &Value) -> Result<()> {
    let body = serde_json::to_vec(value)?;
    writer
        .write_all(format!("Content-Length: {}\r\n\r\n", body.len()).as_bytes())
        .await?;
    writer.write_all(&body).await?;
    writer.flush().await?;
    Ok(())
}

async fn read_lsp_message<R: AsyncBufRead + Unpin>(reader: &mut R) -> Result<Option<Value>> {
    let mut content_length = None;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line).await? == 0 {
            return Ok(None);
        }
        if line == "\r\n" || line == "\n" {
            break;
        }
        if let Some(value) = line.strip_prefix("Content-Length:") {
            content_length = Some(
                value
                    .trim()
                    .parse::<usize>()
                    .context("invalid LSP Content-Length")?,
            );
        }
    }
    let length = content_length.context("LSP response omitted Content-Length")?;
    if length > 64 * 1024 * 1024 {
        bail!("LSP message exceeded 64 MiB safety limit");
    }
    let mut body = vec![0; length];
    reader.read_exact(&mut body).await?;
    Ok(Some(serde_json::from_slice(&body)?))
}

pub fn discover_workspace_servers(root: &Path, languages: &[String]) -> Result<Vec<LspServerSpec>> {
    let root_uri = Url::from_directory_path(root)
        .map_err(|_| anyhow::anyhow!("unable to convert workspace root to file URI"))?
        .to_string();
    let workspace_folders = vec![json!({
        "uri": root_uri.clone(),
        "name": root.file_name().and_then(|value| value.to_str()).unwrap_or("workspace"),
    })];
    let mut specs = Vec::new();
    for family in LANGUAGE_SERVER_FAMILIES {
        let present = family
            .languages
            .iter()
            .any(|language| languages.iter().any(|candidate| candidate == language));
        if !present {
            continue;
        }
        let Some((command, arguments)) = family.commands.iter().find_map(|candidate| {
            find_executable(candidate.command).map(|path| (path, candidate.arguments))
        }) else {
            continue;
        };
        specs.push(server_spec(
            family.id,
            command,
            arguments.iter().map(ToString::to_string).collect(),
            &root_uri,
            &workspace_folders,
        ));
    }
    Ok(specs)
}

fn server_spec(
    server_id: &str,
    command: PathBuf,
    arguments: Vec<String>,
    root_uri: &str,
    workspace_folders: &[Value],
) -> LspServerSpec {
    LspServerSpec {
        server_id: server_id.to_string(),
        command: command.to_string_lossy().to_string(),
        arguments,
        root_uri: root_uri.to_string(),
        initialization_options: Value::Null,
        workspace_folders: workspace_folders.to_vec(),
        hard_timeout_ms: default_hard_timeout_ms(),
        idle_timeout_ms: default_idle_timeout_ms(),
        rss_limit_bytes: default_rss_limit_bytes(),
        maximum_open_documents: default_maximum_open_documents(),
    }
}

fn find_executable(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for directory in std::env::split_paths(&path) {
        let candidate = directory.join(if cfg!(windows) {
            format!("{name}.cmd")
        } else {
            name.to_string()
        });
        if candidate.is_file() {
            return Some(candidate);
        }
        if cfg!(windows) {
            let executable = directory.join(format!("{name}.exe"));
            if executable.is_file() {
                return Some(executable);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capabilities_are_required_before_tools_are_advertised() {
        assert!(capability_enabled(Some(&json!(true))));
        assert!(capability_enabled(Some(&json!({"workDoneProgress":true}))));
        assert!(!capability_enabled(Some(&Value::Null)));
        assert!(!capability_enabled(None));
    }

    #[test]
    fn brokered_methods_resolve_to_advertised_capability_properties() {
        assert_eq!(
            capability_property("textDocument/diagnostic"),
            Some("diagnosticProvider")
        );
        assert_eq!(
            capability_property("callHierarchy/incomingCalls"),
            Some("callHierarchyProvider")
        );
        assert_eq!(capability_property("textDocument/unknown"), None);
    }

    #[test]
    fn server_request_defaults_are_bounded() {
        let request = json!({"jsonrpc":"2.0","id":7,"method":"workspace/configuration","params":{"items":[{},{}]}});
        let response = default_server_request_response(&request);
        assert_eq!(response["result"].as_array().map(Vec::len), Some(2));
    }

    #[test]
    fn every_extension_maps_to_exactly_one_family() {
        let mut owners: std::collections::HashMap<&str, &str> = std::collections::HashMap::new();
        for family in LANGUAGE_SERVER_FAMILIES {
            for extension in family.extensions {
                if let Some(previous) = owners.insert(extension, family.id) {
                    panic!(
                        "extension {extension} is claimed by both {previous} and {}",
                        family.id
                    );
                }
                let sample = PathBuf::from(format!("sample.{extension}"));
                let language = crate::index::language_for_path(&sample).unwrap_or_else(|| {
                    panic!("indexer does not detect family extension {extension}")
                });
                assert!(
                    family.languages.contains(&language),
                    "extension {extension} indexes as {language}, outside family {}",
                    family.id
                );
            }
        }
    }

    #[test]
    fn family_languages_route_to_their_family_key_alone() {
        let mut owners: std::collections::HashMap<&str, &str> = std::collections::HashMap::new();
        for family in LANGUAGE_SERVER_FAMILIES {
            for language in family.languages {
                if let Some(previous) = owners.insert(language, family.id) {
                    panic!(
                        "language {language} is claimed by both {previous} and {}",
                        family.id
                    );
                }
                assert_eq!(language_key(language), family.id);
            }
        }
        assert_eq!(language_key("toml"), "toml");
        assert_eq!(language_key("markdown"), "markdown");
        assert_eq!(language_key("sql"), "sql");
    }

    #[test]
    fn capability_matrix_v1_matches_the_wired_family_table() {
        assert_eq!(LANGUAGE_SERVER_FAMILIES.len(), 17);
        validate_lsp_capability_matrix().expect("matrix v1 mirrors the wired family table");
        let digest = lsp_capability_matrix_sha256();
        assert_eq!(digest.len(), 64);
        assert!(
            digest
                .chars()
                .all(|character| character.is_ascii_hexdigit() && !character.is_ascii_uppercase())
        );
    }

    #[test]
    fn discovery_wires_nothing_without_matching_workspace_languages() {
        let temp = tempfile::tempdir().expect("tempdir");
        let empty = discover_workspace_servers(temp.path(), &[]).expect("empty workspace");
        assert!(empty.is_empty());
        let unmatched =
            discover_workspace_servers(temp.path(), &["toml".to_string(), "sql".to_string()])
                .expect("languages outside every family");
        assert!(unmatched.is_empty());
    }
}
