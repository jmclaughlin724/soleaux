//! Long-lived LSP supervision with an 800 ms interactive soft deadline.
//!
//! A server must complete initialization and advertise the applicable
//! capability before Soleaux exposes a semantic operation. Requests that miss
//! the soft deadline return cached data or a stable pending ID; the hard-bounded
//! request continues and publishes a completion event redeemable through
//! [`LspSupervisor::completion`].
//!
//! Depth guarantees (P5-023):
//! - Documents are supervisor-owned and versioned; out-of-order edits are
//!   rejected and reconnects replay the tracked document state.
//! - Diagnostics arrive by pull (`textDocument/diagnostic`, capability-gated)
//!   and by push (`textDocument/publishDiagnostics`, retained per document).
//! - In-flight requests are cancellable; `$/cancelRequest` is sent and the
//!   awaiting caller resolves immediately.
//! - Workspace edits apply to the tracked overlay all-or-nothing; a failed
//!   plan mutates nothing and a failed commit restores the prior text.
//! - Cache keys are request-parameter-aware; distinct parameters never share
//!   a cached value.
//! - Crash loops quarantine the server after repeated failures inside a
//!   window; `restart` is the explicit operator path out of quarantine.
//! - Declared RSS/CPU/concurrency/idle limits are enforced: a sweeper samples
//!   the process and terminates violators truthfully, concurrency is a hard
//!   admission bound, and idle servers stop while their documents survive for
//!   recovery.

use anyhow::{Context, Result, bail};
use dashmap::DashMap;
use moka::future::Cache;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, VecDeque},
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        Arc, Mutex as StdMutex, Weak,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::{Mutex, Notify, RwLock, Semaphore, broadcast, mpsc, oneshot},
    task::JoinHandle,
    time::timeout,
};
use url::Url;
use uuid::Uuid;

pub const DEFAULT_SOFT_DEADLINE: Duration = Duration::from_millis(800);
pub const DEFAULT_HARD_TIMEOUT: Duration = Duration::from_secs(15);
pub const DEFAULT_SWEEP_INTERVAL: Duration = Duration::from_secs(2);

/// Consecutive over-limit CPU samples tolerated before termination; one
/// sample can be a startup burst, two is sustained.
const CPU_STRIKE_LIMIT: u32 = 2;
/// Completed late-request events retained for [`LspSupervisor::completion`].
const COMPLETION_RETENTION: usize = 512;
/// Recent limit-violation reasons retained per server for health reporting.
const VIOLATION_RETENTION: usize = 8;

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
/// pre-conformance value (P5-009b re-runs the matrix at depth).
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
    #[serde(default = "default_cpu_limit_percent")]
    pub cpu_limit_percent: f64,
    #[serde(default = "default_maximum_concurrent_requests")]
    pub maximum_concurrent_requests: usize,
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
fn default_cpu_limit_percent() -> f64 {
    400.0
}
fn default_maximum_concurrent_requests() -> usize {
    32
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
    /// Caller-provided cache scope (typically the document content hash). The
    /// supervisor derives the final key from this scope plus the method, the
    /// canonicalized request parameters, and the document identity, so two
    /// requests with different parameters never share a cached value.
    pub cache_scope: String,
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

/// Push diagnostics retained from `textDocument/publishDiagnostics`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspPushDiagnostics {
    pub uri: String,
    pub version: Option<i64>,
    pub items: Vec<Value>,
    pub age_ms: u64,
}

/// One document mutated by [`LspSupervisor::apply_workspace_edit`].
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspAppliedDocument {
    pub uri: String,
    pub version: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspWorkspaceEditOutcome {
    pub applied: Vec<LspAppliedDocument>,
}

/// Truthful per-server runtime state, including servers that are stopped or
/// quarantined and therefore absent from [`LspSupervisor::probes`].
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspServerHealth {
    pub server_id: String,
    pub running: bool,
    pub open_documents: usize,
    pub in_flight_requests: usize,
    pub restarts: u64,
    pub recent_failures: usize,
    pub last_failure_reason: Option<String>,
    pub quarantined_remaining_ms: Option<u64>,
    pub stopped_reason: Option<String>,
    pub idle_ms: Option<u64>,
    pub rss_bytes: Option<u64>,
    pub cpu_percent: Option<f64>,
    pub limit_violations: Vec<String>,
}

/// Crash-loop policy: `threshold` failures inside `window` quarantine the
/// server for `cooldown`; [`LspSupervisor::restart`] is the explicit way out.
#[derive(Debug, Clone, Copy)]
pub struct QuarantinePolicy {
    pub threshold: usize,
    pub window: Duration,
    pub cooldown: Duration,
}

impl Default for QuarantinePolicy {
    fn default() -> Self {
        Self {
            threshold: 3,
            window: Duration::from_secs(60),
            cooldown: Duration::from_secs(300),
        }
    }
}

#[derive(Debug, Default)]
struct QuarantineState {
    failures: Vec<Instant>,
    until: Option<Instant>,
    last_reason: Option<String>,
}

#[derive(Debug, Clone)]
struct CachedResult {
    value: Value,
}

#[derive(Debug, Clone)]
struct OpenDocument {
    language_id: String,
    version: i64,
    text: String,
    last_used: Instant,
}

#[derive(Debug, Clone)]
struct PushDiagnosticsRecord {
    version: Option<i64>,
    items: Vec<Value>,
    received: Instant,
}

#[derive(Debug, Clone)]
struct InFlightRequest {
    server_id: String,
    lsp_id: u64,
}

#[derive(Debug, Clone, Copy)]
struct ResourceSample {
    rss_bytes: u64,
    cpu_seconds: f64,
    cpu_percent: Option<f64>,
    cpu_strikes: u32,
    at: Instant,
}

/// State the stdio reader task shares with the request path: the pending
/// response map, the outbound writer, the answered workspace-folder set, and
/// the push-diagnostics store.
struct ServerShared {
    outbound: mpsc::Sender<Value>,
    pending: DashMap<u64, oneshot::Sender<Result<Value, String>>>,
    workspace_folders: StdMutex<Vec<Value>>,
    push_diagnostics: DashMap<String, PushDiagnosticsRecord>,
    push_notify: Notify,
}

struct ServerProcess {
    spec: LspServerSpec,
    pid: Option<u32>,
    child: Mutex<Child>,
    shared: Arc<ServerShared>,
    next_id: AtomicU64,
    capabilities: RwLock<Value>,
    request_permits: Arc<Semaphore>,
    last_activity: StdMutex<Instant>,
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
        let pid = child.id();
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

        let shared = Arc::new(ServerShared {
            outbound: outbound.clone(),
            pending: DashMap::new(),
            workspace_folders: StdMutex::new(spec.workspace_folders.clone()),
            push_diagnostics: DashMap::new(),
            push_notify: Notify::new(),
        });
        let reader_shared = Arc::clone(&shared);
        let reader_task = tokio::spawn(async move {
            let mut reader = BufReader::new(stdout);
            let closed_reason = loop {
                match read_lsp_message(&mut reader).await {
                    Ok(Some(message)) => handle_server_message(&reader_shared, message).await,
                    Ok(None) => break "LSP server closed its output stream".to_string(),
                    Err(error) => break error.to_string(),
                }
            };
            let keys = reader_shared
                .pending
                .iter()
                .map(|entry| *entry.key())
                .collect::<Vec<_>>();
            for key in keys {
                if let Some((_, sender)) = reader_shared.pending.remove(&key) {
                    let _ = sender.send(Err(closed_reason.clone()));
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
            pid,
            child: Mutex::new(child),
            shared,
            next_id: AtomicU64::new(1),
            capabilities: RwLock::new(Value::Null),
            request_permits: Arc::new(Semaphore::new(spec.maximum_concurrent_requests.max(1))),
            last_activity: StdMutex::new(Instant::now()),
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
                            "applyEdit": true,
                            "workspaceEdit": {"documentChanges": true},
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

    fn touch(&self) {
        *self
            .last_activity
            .lock()
            .expect("last-activity lock poisoned") = Instant::now();
    }

    fn idle_for(&self) -> Duration {
        self.last_activity
            .lock()
            .expect("last-activity lock poisoned")
            .elapsed()
    }

    /// Register and send one request, returning its wire id and the receiver
    /// that resolves with the response, a cancellation, or stream closure.
    async fn begin_request(
        &self,
        method: &str,
        params: Value,
    ) -> Result<(u64, oneshot::Receiver<Result<Value, String>>)> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        self.shared.pending.insert(id, sender);
        self.touch();
        let payload = json!({"jsonrpc":"2.0","id":id,"method":method,"params":params});
        if let Err(error) = self.shared.outbound.send(payload).await {
            self.shared.pending.remove(&id);
            return Err(error).context("LSP writer task stopped");
        }
        Ok((id, receiver))
    }

    async fn request_raw(
        &self,
        method: &str,
        params: Value,
        hard_timeout: Duration,
    ) -> Result<Value> {
        let (id, receiver) = self.begin_request(method, params).await?;
        match timeout(hard_timeout, receiver).await {
            Ok(Ok(Ok(value))) => Ok(value),
            Ok(Ok(Err(error))) => bail!(error),
            Ok(Err(_)) => bail!("LSP response channel closed"),
            Err(_) => {
                self.cancel_request(id).await;
                bail!("LSP request exceeded hard timeout")
            }
        }
    }

    /// Resolve a pending request locally and tell the server to abandon it.
    async fn cancel_request(&self, id: u64) {
        if let Some((_, sender)) = self.shared.pending.remove(&id) {
            let _ = sender.send(Err("request canceled by the Soleaux client".to_string()));
        }
        let _ = self.notify("$/cancelRequest", json!({"id": id})).await;
    }

    async fn notify(&self, method: &str, params: Value) -> Result<()> {
        self.touch();
        self.shared
            .outbound
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

    async fn terminate(&self) {
        let mut child = self.child.lock().await;
        let _ = child.start_kill();
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

    fn in_flight(&self) -> usize {
        self.spec
            .maximum_concurrent_requests
            .max(1)
            .saturating_sub(self.request_permits.available_permits())
    }
}

async fn handle_server_message(shared: &Arc<ServerShared>, message: Value) {
    let has_id = message.get("id").is_some();
    let method = message.get("method").and_then(Value::as_str);
    match (has_id, method) {
        (true, None) => {
            if let Some(id) = message.get("id").and_then(Value::as_u64)
                && let Some((_, sender)) = shared.pending.remove(&id)
            {
                let value = if let Some(error) = message.get("error") {
                    Err(error.to_string())
                } else {
                    Ok(message.get("result").cloned().unwrap_or(Value::Null))
                };
                let _ = sender.send(value);
            }
        }
        (true, Some(_)) => {
            let response = server_request_response(shared, &message);
            let _ = shared.outbound.send(response).await;
        }
        (false, Some("textDocument/publishDiagnostics")) => {
            let Some(uri) = message
                .pointer("/params/uri")
                .and_then(Value::as_str)
                .map(str::to_string)
            else {
                return;
            };
            let record = PushDiagnosticsRecord {
                version: message.pointer("/params/version").and_then(Value::as_i64),
                items: message
                    .pointer("/params/diagnostics")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
                received: Instant::now(),
            };
            shared.push_diagnostics.insert(uri, record);
            shared.push_notify.notify_waiters();
        }
        (false, Some(other)) => {
            tracing::debug!(target: "soleaux_lsp", method = other, "unrouted server notification");
        }
        (false, None) => {}
    }
}

fn server_request_response(shared: &Arc<ServerShared>, request: &Value) -> Value {
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
        "workspace/workspaceFolders" => Value::Array(
            shared
                .workspace_folders
                .lock()
                .expect("workspace-folder lock poisoned")
                .clone(),
        ),
        // Server-initiated edits bypass the preview/edit confirmation flow, so
        // the truthful answer is a spec-shaped refusal, not silent application.
        "workspace/applyEdit" => json!({
            "applied": false,
            "failureReason": "Soleaux applies workspace edits only through its confirmed preview/edit flow",
        }),
        "client/registerCapability"
        | "client/unregisterCapability"
        | "window/workDoneProgress/create" => Value::Null,
        _ => {
            return json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"Soleaux LSP client does not implement this server request"}});
        }
    };
    json!({"jsonrpc":"2.0","id":id,"result":result})
}

struct SupervisorInner {
    servers: DashMap<String, Arc<ServerProcess>>,
    specs: DashMap<String, LspServerSpec>,
    documents: DashMap<String, Arc<DashMap<String, OpenDocument>>>,
    quarantine: DashMap<String, QuarantineState>,
    inflight: DashMap<Uuid, InFlightRequest>,
    completions: DashMap<Uuid, LspCompletionEvent>,
    completion_order: StdMutex<VecDeque<Uuid>>,
    samples: DashMap<String, ResourceSample>,
    violations: DashMap<String, Vec<String>>,
    restarts: DashMap<String, u64>,
    stopped: DashMap<String, String>,
    cache: Cache<String, Arc<CachedResult>>,
    events: broadcast::Sender<LspCompletionEvent>,
    sweeper: StdMutex<Option<JoinHandle<()>>>,
}

impl Drop for SupervisorInner {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.sweeper.lock()
            && let Some(task) = guard.take()
        {
            task.abort();
        }
    }
}

impl SupervisorInner {
    fn record_failure(&self, policy: QuarantinePolicy, server_id: &str, reason: &str) -> bool {
        let mut state = self.quarantine.entry(server_id.to_string()).or_default();
        let now = Instant::now();
        state.failures.push(now);
        state
            .failures
            .retain(|at| now.duration_since(*at) <= policy.window);
        state.last_reason = Some(reason.to_string());
        if state.failures.len() >= policy.threshold {
            state.until = Some(now + policy.cooldown);
            true
        } else {
            false
        }
    }

    fn quarantined_for(&self, server_id: &str) -> Option<Duration> {
        let state = self.quarantine.get(server_id)?;
        let until = state.until?;
        until.checked_duration_since(Instant::now())
    }

    fn record_violation(&self, server_id: &str, reason: String) {
        let mut violations = self.violations.entry(server_id.to_string()).or_default();
        violations.push(reason);
        let excess = violations.len().saturating_sub(VIOLATION_RETENTION);
        if excess > 0 {
            violations.drain(..excess);
        }
    }

    fn store_completion(&self, event: LspCompletionEvent) {
        let request_id = event.request_id;
        self.completions.insert(request_id, event.clone());
        {
            let mut order = self
                .completion_order
                .lock()
                .expect("completion-order lock poisoned");
            order.push_back(request_id);
            while order.len() > COMPLETION_RETENTION {
                if let Some(evicted) = order.pop_front() {
                    self.completions.remove(&evicted);
                }
            }
        }
        let _ = self.events.send(event);
    }

    async fn sweep(inner: &Arc<Self>, policy: QuarantinePolicy) {
        let servers = inner
            .servers
            .iter()
            .map(|entry| (entry.key().clone(), Arc::clone(entry.value())))
            .collect::<Vec<_>>();
        for (server_id, process) in servers {
            if !process.is_alive() {
                inner.servers.remove(&server_id);
                inner.record_failure(policy, &server_id, "server process exited");
                inner
                    .stopped
                    .insert(server_id.clone(), "exited".to_string());
                continue;
            }
            let idle_limit = Duration::from_millis(process.spec.idle_timeout_ms.max(1));
            if process.idle_for() >= idle_limit {
                let _ = process.notify("exit", Value::Null).await;
                process.terminate().await;
                inner.servers.remove(&server_id);
                inner.stopped.insert(
                    server_id.clone(),
                    format!("idle beyond {} ms", process.spec.idle_timeout_ms),
                );
                continue;
            }
            let Some(pid) = process.pid else { continue };
            let Some((rss_bytes, cpu_seconds)) = sample_process(pid).await else {
                continue;
            };
            let now = Instant::now();
            let previous = inner.samples.get(&server_id).map(|entry| *entry.value());
            let cpu_percent = previous.map(|sample| {
                let wall = now.duration_since(sample.at).as_secs_f64().max(0.001);
                ((cpu_seconds - sample.cpu_seconds).max(0.0) / wall) * 100.0
            });
            let over_cpu = cpu_percent
                .map(|percent| percent > process.spec.cpu_limit_percent)
                .unwrap_or(false);
            let cpu_strikes = if over_cpu {
                previous.map_or(1, |sample| sample.cpu_strikes + 1)
            } else {
                0
            };
            inner.samples.insert(
                server_id.clone(),
                ResourceSample {
                    rss_bytes,
                    cpu_seconds,
                    cpu_percent,
                    cpu_strikes,
                    at: now,
                },
            );
            let violation = if rss_bytes > process.spec.rss_limit_bytes {
                Some(format!(
                    "rss {} bytes exceeded the {} byte limit",
                    rss_bytes, process.spec.rss_limit_bytes
                ))
            } else if cpu_strikes >= CPU_STRIKE_LIMIT {
                Some(format!(
                    "cpu {:.0}% exceeded the {:.0}% limit across {cpu_strikes} samples",
                    cpu_percent.unwrap_or_default(),
                    process.spec.cpu_limit_percent
                ))
            } else {
                None
            };
            if let Some(reason) = violation {
                process.terminate().await;
                inner.servers.remove(&server_id);
                inner.record_failure(policy, &server_id, &reason);
                inner.record_violation(&server_id, reason.clone());
                inner.stopped.insert(server_id.clone(), reason);
            }
        }
    }
}

#[derive(Clone)]
pub struct LspSupervisor {
    inner: Arc<SupervisorInner>,
    soft_deadline: Duration,
    sweep_interval: Duration,
    policy: QuarantinePolicy,
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
            inner: Arc::new(SupervisorInner {
                servers: DashMap::new(),
                specs: DashMap::new(),
                documents: DashMap::new(),
                quarantine: DashMap::new(),
                inflight: DashMap::new(),
                completions: DashMap::new(),
                completion_order: StdMutex::new(VecDeque::new()),
                samples: DashMap::new(),
                violations: DashMap::new(),
                restarts: DashMap::new(),
                stopped: DashMap::new(),
                cache,
                events,
                sweeper: StdMutex::new(None),
            }),
            soft_deadline: DEFAULT_SOFT_DEADLINE,
            sweep_interval: DEFAULT_SWEEP_INTERVAL,
            policy: QuarantinePolicy::default(),
        }
    }

    pub fn with_soft_deadline(mut self, deadline: Duration) -> Self {
        self.soft_deadline = deadline.clamp(Duration::from_millis(50), Duration::from_secs(5));
        self
    }

    pub fn with_sweep_interval(mut self, interval: Duration) -> Self {
        self.sweep_interval = interval.clamp(Duration::from_millis(20), Duration::from_secs(60));
        self
    }

    pub fn with_quarantine_policy(mut self, policy: QuarantinePolicy) -> Self {
        self.policy = QuarantinePolicy {
            threshold: policy.threshold.max(1),
            window: policy.window,
            cooldown: policy.cooldown,
        };
        self
    }

    pub fn subscribe(&self) -> broadcast::Receiver<LspCompletionEvent> {
        self.inner.events.subscribe()
    }

    fn ensure_sweeper(&self) {
        let mut guard = self
            .inner
            .sweeper
            .lock()
            .expect("sweeper handle lock poisoned");
        if guard.is_some() {
            return;
        }
        let weak: Weak<SupervisorInner> = Arc::downgrade(&self.inner);
        let interval = self.sweep_interval;
        let policy = self.policy;
        *guard = Some(tokio::spawn(async move {
            loop {
                tokio::time::sleep(interval).await;
                let Some(inner) = weak.upgrade() else { break };
                SupervisorInner::sweep(&inner, policy).await;
            }
        }));
    }

    fn bail_quarantined(&self, server_id: &str, remaining: Duration) -> anyhow::Error {
        let reason = self
            .inner
            .quarantine
            .get(server_id)
            .and_then(|state| state.last_reason.clone())
            .unwrap_or_else(|| "repeated failures".to_string());
        anyhow::anyhow!(
            "LSP server {server_id} is quarantined for {} more seconds after a crash loop ({reason}); restart_lsp clears the quarantine",
            remaining.as_secs().max(1)
        )
    }

    /// Replay the tracked documents onto a freshly started process so a
    /// reconnect recovers the pre-crash document state.
    async fn replay_documents(&self, server_id: &str, process: &Arc<ServerProcess>) -> Result<()> {
        let Some(documents) = self
            .inner
            .documents
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
        else {
            return Ok(());
        };
        let snapshot = documents
            .iter()
            .map(|entry| (entry.key().clone(), entry.value().clone()))
            .collect::<Vec<_>>();
        for (uri, document) in snapshot {
            process
                .notify(
                    "textDocument/didOpen",
                    json!({
                        "textDocument": {
                            "uri": uri,
                            "languageId": document.language_id,
                            "version": document.version,
                            "text": document.text,
                        }
                    }),
                )
                .await?;
        }
        Ok(())
    }

    async fn start_server(
        &self,
        spec: LspServerSpec,
        is_restart: bool,
    ) -> Result<Arc<ServerProcess>> {
        match ServerProcess::start(spec.clone()).await {
            Ok(process) => {
                self.replay_documents(&spec.server_id, &process).await?;
                self.inner
                    .servers
                    .insert(spec.server_id.clone(), Arc::clone(&process));
                self.inner.stopped.remove(&spec.server_id);
                if is_restart {
                    *self
                        .inner
                        .restarts
                        .entry(spec.server_id.clone())
                        .or_insert(0) += 1;
                }
                Ok(process)
            }
            Err(error) => {
                let reason = format!("start failed: {error:#}");
                let quarantined = self
                    .inner
                    .record_failure(self.policy, &spec.server_id, &reason);
                if quarantined {
                    bail!(
                        "starting LSP server {} failed and the server is now quarantined after a crash loop: {error:#}",
                        spec.server_id
                    );
                }
                Err(error)
            }
        }
    }

    /// Fetch the running process or recover it from the stored spec, replaying
    /// tracked documents. Quarantined servers fail truthfully.
    async fn acquire_server(&self, server_id: &str) -> Result<Arc<ServerProcess>> {
        if let Some(existing) = self
            .inner
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
        {
            if existing.is_alive() {
                return Ok(existing);
            }
            self.inner.servers.remove(server_id);
            self.inner
                .record_failure(self.policy, server_id, "server process exited");
            self.inner
                .stopped
                .insert(server_id.to_string(), "exited".to_string());
        }
        if let Some(remaining) = self.inner.quarantined_for(server_id) {
            return Err(self.bail_quarantined(server_id, remaining));
        }
        let spec = self
            .inner
            .specs
            .get(server_id)
            .map(|entry| entry.value().clone())
            .context("LSP server is not running or failed its capability probe")?;
        self.start_server(spec, true).await
    }

    pub async fn ensure_server(&self, spec: LspServerSpec) -> Result<LspProbe> {
        self.ensure_sweeper();
        if let Some(remaining) = self.inner.quarantined_for(&spec.server_id) {
            return Err(self.bail_quarantined(&spec.server_id, remaining));
        }
        self.inner
            .specs
            .insert(spec.server_id.clone(), spec.clone());
        if let Some(existing) = self
            .inner
            .servers
            .get(&spec.server_id)
            .map(|entry| Arc::clone(entry.value()))
        {
            if existing.is_alive() {
                return Ok(existing.probe().await);
            }
            self.inner.servers.remove(&spec.server_id);
            self.inner
                .record_failure(self.policy, &spec.server_id, "server process exited");
            if let Some(remaining) = self.inner.quarantined_for(&spec.server_id) {
                return Err(self.bail_quarantined(&spec.server_id, remaining));
            }
        }
        let was_known = self.inner.documents.contains_key(&spec.server_id);
        let process = self.start_server(spec, was_known).await?;
        Ok(process.probe().await)
    }

    fn documents_for(&self, server_id: &str) -> Arc<DashMap<String, OpenDocument>> {
        self.inner
            .documents
            .entry(server_id.to_string())
            .or_default()
            .clone()
    }

    /// Open or refresh a document with supervisor-assigned versions: a new
    /// document opens at version 1, changed text bumps the version and sends
    /// `didChange`, identical text is a no-op. Returns the tracked version.
    pub async fn sync_document(
        &self,
        server_id: &str,
        uri: &str,
        language_id: &str,
        text: &str,
    ) -> Result<i64> {
        if text.len() > 4 * 1024 * 1024 {
            bail!("LSP document exceeds the 4 MiB open-document cap");
        }
        let server = self.acquire_server(server_id).await?;
        let documents = self.documents_for(server_id);
        if let Some(existing) = documents.get(uri).map(|entry| entry.value().clone()) {
            if existing.text == text {
                if let Some(mut entry) = documents.get_mut(uri) {
                    entry.last_used = Instant::now();
                }
                return Ok(existing.version);
            }
            let version = existing.version + 1;
            self.send_change(&server, uri, version, text).await?;
            documents.insert(
                uri.to_string(),
                OpenDocument {
                    language_id: existing.language_id,
                    version,
                    text: text.to_string(),
                    last_used: Instant::now(),
                },
            );
            return Ok(version);
        }
        let cap = server.spec.maximum_open_documents.max(1);
        while documents.len() >= cap {
            let oldest = documents
                .iter()
                .min_by_key(|entry| entry.value().last_used)
                .map(|entry| entry.key().clone());
            let Some(evicted) = oldest else { break };
            documents.remove(&evicted);
            server
                .notify(
                    "textDocument/didClose",
                    json!({"textDocument":{"uri":evicted}}),
                )
                .await?;
        }
        server
            .notify(
                "textDocument/didOpen",
                json!({
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                }),
            )
            .await?;
        documents.insert(
            uri.to_string(),
            OpenDocument {
                language_id: language_id.to_string(),
                version: 1,
                text: text.to_string(),
                last_used: Instant::now(),
            },
        );
        Ok(1)
    }

    /// Apply one explicit versioned change. The version must be strictly
    /// greater than the tracked version; out-of-order edits are rejected.
    pub async fn change_document(
        &self,
        server_id: &str,
        uri: &str,
        version: i64,
        text: &str,
    ) -> Result<()> {
        if text.len() > 4 * 1024 * 1024 {
            bail!("LSP document exceeds the 4 MiB open-document cap");
        }
        let server = self.acquire_server(server_id).await?;
        let documents = self.documents_for(server_id);
        let current = documents
            .get(uri)
            .map(|entry| entry.value().clone())
            .with_context(|| format!("document is not open on LSP server {server_id}: {uri}"))?;
        if version <= current.version {
            bail!(
                "out-of-order document version {version} for {uri}; the tracked version is {}",
                current.version
            );
        }
        self.send_change(&server, uri, version, text).await?;
        documents.insert(
            uri.to_string(),
            OpenDocument {
                language_id: current.language_id,
                version,
                text: text.to_string(),
                last_used: Instant::now(),
            },
        );
        Ok(())
    }

    async fn send_change(
        &self,
        server: &Arc<ServerProcess>,
        uri: &str,
        version: i64,
        text: &str,
    ) -> Result<()> {
        server
            .notify(
                "textDocument/didChange",
                json!({
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                }),
            )
            .await
    }

    pub async fn close_document(&self, server_id: &str, uri: &str) -> Result<()> {
        let server = self
            .inner
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))
            .context("LSP server is not running")?;
        if let Some(documents) = self.inner.documents.get(server_id) {
            documents.remove(uri);
        }
        server
            .notify("textDocument/didClose", json!({"textDocument":{"uri":uri}}))
            .await
    }

    pub async fn supports(&self, server_id: &str, method: &str) -> Result<bool> {
        let server = self.acquire_server(server_id).await?;
        Ok(server.supports(method).await)
    }

    pub async fn query(&self, query: LspQuery) -> Result<LspQueryResult> {
        self.ensure_sweeper();
        let server = self.acquire_server(&query.server_id).await?;
        if !server.supports(&query.method).await {
            bail!(
                "LSP server {} did not advertise capability for {}",
                query.server_id,
                query.method
            );
        }
        let permit = match Arc::clone(&server.request_permits).try_acquire_owned() {
            Ok(permit) => permit,
            Err(_) => bail!(
                "LSP server {} is at its concurrency limit of {} in-flight requests",
                query.server_id,
                server.spec.maximum_concurrent_requests.max(1)
            ),
        };
        let request_id = Uuid::now_v7();
        let cache_key = parameter_cache_key(&query);
        let cached = self.inner.cache.get(&cache_key).await;
        let started = Instant::now();
        let hard_timeout = server.spec.hard_timeout();
        let (lsp_id, receiver) = server
            .begin_request(&query.method, query.params.clone())
            .await?;
        self.inner.inflight.insert(
            request_id,
            InFlightRequest {
                server_id: query.server_id.clone(),
                lsp_id,
            },
        );
        let request_server = Arc::clone(&server);
        let mut task = tokio::spawn(async move {
            let _permit = permit;
            match timeout(hard_timeout, receiver).await {
                Ok(Ok(outcome)) => outcome,
                Ok(Err(_)) => Err("LSP response channel closed".to_string()),
                Err(_) => {
                    request_server.cancel_request(lsp_id).await;
                    Err("LSP request exceeded hard timeout".to_string())
                }
            }
        });
        match timeout(self.soft_deadline, &mut task).await {
            Ok(joined) => {
                self.inner.inflight.remove(&request_id);
                let value = joined
                    .context("LSP request task failed")?
                    .map_err(|error| anyhow::anyhow!(error))?;
                self.inner
                    .cache
                    .insert(
                        cache_key,
                        Arc::new(CachedResult {
                            value: value.clone(),
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
                let inner = Arc::clone(&self.inner);
                let server_id = query.server_id.clone();
                let event_method = query.method.clone();
                tokio::spawn(async move {
                    let result = task.await;
                    inner.inflight.remove(&request_id);
                    let duration_ms = started.elapsed().as_millis().try_into().unwrap_or(u64::MAX);
                    let event = match result {
                        Ok(Ok(value)) => {
                            inner
                                .cache
                                .insert(
                                    cache_key,
                                    Arc::new(CachedResult {
                                        value: value.clone(),
                                    }),
                                )
                                .await;
                            LspCompletionEvent {
                                request_id,
                                server_id,
                                method: event_method,
                                value: Some(value),
                                error: None,
                                duration_ms,
                            }
                        }
                        Ok(Err(error)) => LspCompletionEvent {
                            request_id,
                            server_id,
                            method: event_method,
                            value: None,
                            error: Some(error),
                            duration_ms,
                        },
                        Err(error) => LspCompletionEvent {
                            request_id,
                            server_id,
                            method: event_method,
                            value: None,
                            error: Some(error.to_string()),
                            duration_ms,
                        },
                    };
                    inner.store_completion(event);
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

    /// Cancel an in-flight request by its public id: the awaiting caller
    /// resolves immediately and `$/cancelRequest` is sent to the server.
    /// Returns false when the request already completed or was never issued.
    pub async fn cancel(&self, request_id: Uuid) -> Result<bool> {
        let Some((_, inflight)) = self.inner.inflight.remove(&request_id) else {
            return Ok(false);
        };
        if let Some(server) = self
            .inner
            .servers
            .get(&inflight.server_id)
            .map(|entry| Arc::clone(entry.value()))
        {
            server.cancel_request(inflight.lsp_id).await;
        }
        Ok(true)
    }

    /// Redeem a pending request id: returns the stored completion event, or
    /// waits up to `wait` for it to arrive.
    pub async fn completion(&self, request_id: Uuid, wait: Duration) -> Option<LspCompletionEvent> {
        let mut receiver = self.inner.events.subscribe();
        if let Some(event) = self.inner.completions.get(&request_id) {
            return Some(event.value().clone());
        }
        let deadline = Instant::now() + wait;
        loop {
            let remaining = deadline.checked_duration_since(Instant::now())?;
            match timeout(remaining, receiver.recv()).await {
                Ok(Ok(event)) if event.request_id == request_id => return Some(event),
                Ok(Ok(_)) => continue,
                Ok(Err(broadcast::error::RecvError::Lagged(_))) => {
                    if let Some(event) = self.inner.completions.get(&request_id) {
                        return Some(event.value().clone());
                    }
                }
                Ok(Err(broadcast::error::RecvError::Closed)) | Err(_) => {
                    return self
                        .inner
                        .completions
                        .get(&request_id)
                        .map(|event| event.value().clone());
                }
            }
        }
    }

    /// The diagnostics most recently pushed by the server for `uri`, if any.
    pub fn push_diagnostics(&self, server_id: &str, uri: &str) -> Option<LspPushDiagnostics> {
        let server = self
            .inner
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))?;
        let record = server.shared.push_diagnostics.get(uri)?;
        Some(LspPushDiagnostics {
            uri: uri.to_string(),
            version: record.version,
            items: record.items.clone(),
            age_ms: record
                .received
                .elapsed()
                .as_millis()
                .try_into()
                .unwrap_or(u64::MAX),
        })
    }

    /// Wait up to `wait` for the server to push diagnostics for `uri`.
    pub async fn wait_for_push_diagnostics(
        &self,
        server_id: &str,
        uri: &str,
        wait: Duration,
    ) -> Option<LspPushDiagnostics> {
        let server = self
            .inner
            .servers
            .get(server_id)
            .map(|entry| Arc::clone(entry.value()))?;
        let deadline = Instant::now() + wait;
        loop {
            let notified = server.shared.push_notify.notified();
            if let Some(record) = self.push_diagnostics(server_id, uri) {
                return Some(record);
            }
            let remaining = deadline.checked_duration_since(Instant::now())?;
            if timeout(remaining, notified).await.is_err() {
                return self.push_diagnostics(server_id, uri);
            }
        }
    }

    /// Update the multi-root folder set: state changes first so a concurrent
    /// `workspace/workspaceFolders` server request observes the new set, then
    /// `workspace/didChangeWorkspaceFolders` announces the delta. Returns the
    /// current folder set.
    pub async fn update_workspace_folders(
        &self,
        server_id: &str,
        added: Vec<Value>,
        removed: Vec<Value>,
    ) -> Result<Vec<Value>> {
        let server = self.acquire_server(server_id).await?;
        let current = {
            let mut folders = server
                .shared
                .workspace_folders
                .lock()
                .expect("workspace-folder lock poisoned");
            folders.retain(|folder| {
                let uri = folder.get("uri").and_then(Value::as_str);
                !removed.iter().any(|candidate| {
                    candidate.get("uri").and_then(Value::as_str) == uri && uri.is_some()
                })
            });
            for folder in &added {
                let uri = folder.get("uri").and_then(Value::as_str);
                let present = folders.iter().any(|existing| {
                    existing.get("uri").and_then(Value::as_str) == uri && uri.is_some()
                });
                if !present {
                    folders.push(folder.clone());
                }
            }
            folders.clone()
        };
        server
            .notify(
                "workspace/didChangeWorkspaceFolders",
                json!({"event": {"added": added, "removed": removed}}),
            )
            .await?;
        Ok(current)
    }

    /// Apply a `WorkspaceEdit` to the tracked document overlay all-or-nothing:
    /// every edit is planned against the tracked text and version before any
    /// `didChange` is sent, so a failing document leaves every other document
    /// untouched; a failed commit restores the already-changed documents.
    pub async fn apply_workspace_edit(
        &self,
        server_id: &str,
        edit: &Value,
    ) -> Result<LspWorkspaceEditOutcome> {
        let server = self.acquire_server(server_id).await?;
        let documents = self.documents_for(server_id);
        let operations = workspace_edit_operations(edit)?;
        if operations.is_empty() {
            return Ok(LspWorkspaceEditOutcome {
                applied: Vec::new(),
            });
        }
        let mut staged = Vec::new();
        for (uri, expected_version, edits) in operations {
            let document = documents
                .get(&uri)
                .map(|entry| entry.value().clone())
                .with_context(|| {
                    format!("workspace edit targets a document that is not open: {uri}")
                })?;
            if let Some(expected) = expected_version
                && expected != document.version
            {
                bail!(
                    "workspace edit targets {uri} at stale version {expected}; the tracked version is {}",
                    document.version
                );
            }
            let new_text = apply_text_edits(&document.text, &edits)
                .with_context(|| format!("applying workspace edit to {uri}"))?;
            staged.push((uri, document, new_text));
        }
        let mut committed: Vec<(String, OpenDocument)> = Vec::new();
        let mut applied = Vec::new();
        for (uri, document, new_text) in staged {
            let version = document.version + 1;
            if let Err(error) = self.send_change(&server, &uri, version, &new_text).await {
                for (rolled_uri, original) in committed {
                    let rollback_version = self
                        .documents_for(server_id)
                        .get(&rolled_uri)
                        .map(|entry| entry.value().version + 1)
                        .unwrap_or(original.version + 2);
                    let _ = self
                        .send_change(&server, &rolled_uri, rollback_version, &original.text)
                        .await;
                    documents.insert(
                        rolled_uri,
                        OpenDocument {
                            version: rollback_version,
                            last_used: Instant::now(),
                            ..original
                        },
                    );
                }
                return Err(error.context(format!(
                    "workspace edit commit failed at {uri}; prior documents were rolled back"
                )));
            }
            documents.insert(
                uri.clone(),
                OpenDocument {
                    language_id: document.language_id.clone(),
                    version,
                    text: new_text,
                    last_used: Instant::now(),
                },
            );
            committed.push((uri.clone(), document));
            applied.push(LspAppliedDocument { uri, version });
        }
        Ok(LspWorkspaceEditOutcome { applied })
    }

    /// Explicit operator restart: clears any quarantine, replaces the process
    /// (or starts it from the stored spec when stopped), and replays the
    /// tracked documents.
    pub async fn restart(&self, server_id: &str) -> Result<LspProbe> {
        self.inner.quarantine.remove(server_id);
        let spec = if let Some((_, server)) = self.inner.servers.remove(server_id) {
            let spec = server.spec.clone();
            server.terminate().await;
            drop(server);
            spec
        } else {
            self.inner
                .specs
                .get(server_id)
                .map(|entry| entry.value().clone())
                .with_context(|| format!("LSP server is not running: {server_id}"))?
        };
        let process = self.start_server(spec, true).await?;
        Ok(process.probe().await)
    }

    pub async fn probes(&self) -> Vec<LspProbe> {
        let servers = self
            .inner
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

    /// Truthful state for every known server, including stopped and
    /// quarantined servers that no longer appear in [`Self::probes`].
    pub fn health(&self) -> Vec<LspServerHealth> {
        let mut ids = BTreeSet::new();
        for entry in self.inner.specs.iter() {
            ids.insert(entry.key().clone());
        }
        for entry in self.inner.servers.iter() {
            ids.insert(entry.key().clone());
        }
        for entry in self.inner.quarantine.iter() {
            ids.insert(entry.key().clone());
        }
        ids.into_iter()
            .map(|server_id| {
                let process = self
                    .inner
                    .servers
                    .get(&server_id)
                    .map(|entry| Arc::clone(entry.value()));
                let running = process.as_ref().is_some_and(|server| server.is_alive());
                let sample = self
                    .inner
                    .samples
                    .get(&server_id)
                    .map(|entry| *entry.value());
                LspServerHealth {
                    open_documents: self
                        .inner
                        .documents
                        .get(&server_id)
                        .map_or(0, |documents| documents.len()),
                    in_flight_requests: process.as_ref().map_or(0, |server| server.in_flight()),
                    restarts: self
                        .inner
                        .restarts
                        .get(&server_id)
                        .map_or(0, |entry| *entry.value()),
                    recent_failures: self
                        .inner
                        .quarantine
                        .get(&server_id)
                        .map_or(0, |state| state.failures.len()),
                    last_failure_reason: self
                        .inner
                        .quarantine
                        .get(&server_id)
                        .and_then(|state| state.last_reason.clone()),
                    quarantined_remaining_ms: self
                        .inner
                        .quarantined_for(&server_id)
                        .map(|remaining| remaining.as_millis().try_into().unwrap_or(u64::MAX)),
                    stopped_reason: if running {
                        None
                    } else {
                        self.inner
                            .stopped
                            .get(&server_id)
                            .map(|entry| entry.value().clone())
                    },
                    idle_ms: process
                        .as_ref()
                        .map(|server| server.idle_for().as_millis().try_into().unwrap_or(u64::MAX)),
                    rss_bytes: sample.map(|sample| sample.rss_bytes),
                    cpu_percent: sample.and_then(|sample| sample.cpu_percent),
                    limit_violations: self
                        .inner
                        .violations
                        .get(&server_id)
                        .map(|entry| entry.value().clone())
                        .unwrap_or_default(),
                    running,
                    server_id,
                }
            })
            .collect()
    }

    #[cfg(test)]
    async fn cache_entries(&self) -> u64 {
        self.inner.cache.run_pending_tasks().await;
        self.inner.cache.entry_count()
    }

    #[cfg(test)]
    async fn raw_request(&self, server_id: &str, method: &str, params: Value) -> Result<Value> {
        let server = self.acquire_server(server_id).await?;
        let hard_timeout = server.spec.hard_timeout();
        server.request_raw(method, params, hard_timeout).await
    }
}

/// Derive the cache key from the request identity: the server, method,
/// canonicalized parameters, document identity and version, and the caller's
/// scope (content hash). Two requests differing in any component never share
/// a cached value.
fn parameter_cache_key(query: &LspQuery) -> String {
    let mut canonical_params = String::new();
    canonical_json(&query.params, &mut canonical_params);
    let mut hasher = Sha256::new();
    for component in [
        query.server_id.as_str(),
        query.method.as_str(),
        query.document_uri.as_deref().unwrap_or("-"),
        &query
            .document_version
            .map_or_else(|| "-".to_string(), |version| version.to_string()),
        query.cache_scope.as_str(),
        canonical_params.as_str(),
    ] {
        hasher.update(component.as_bytes());
        hasher.update([0]);
    }
    format!("{:x}", hasher.finalize())
}

/// Serialize with recursively sorted object keys so semantically equal
/// parameter objects hash identically regardless of construction order.
fn canonical_json(value: &Value, out: &mut String) {
    match value {
        Value::Object(map) => {
            out.push('{');
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&Value::String((*key).clone()).to_string());
                out.push(':');
                canonical_json(&map[*key], out);
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                canonical_json(item, out);
            }
            out.push(']');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// One workspace-edit document batch: target URI, optional expected document
/// version, and the raw text-edit operations for that document.
type DocumentEditBatch = (String, Option<i64>, Vec<Value>);

/// Parse a `WorkspaceEdit` into per-document operations. Resource operations
/// (create/rename/delete) are refused rather than half-applied.
fn workspace_edit_operations(edit: &Value) -> Result<Vec<DocumentEditBatch>> {
    let mut operations = Vec::new();
    if let Some(document_changes) = edit.get("documentChanges").and_then(Value::as_array) {
        for change in document_changes {
            if change.get("kind").is_some() {
                bail!(
                    "workspace edit resource operations (create/rename/delete file) are not supported by the document overlay"
                );
            }
            let uri = change
                .pointer("/textDocument/uri")
                .and_then(Value::as_str)
                .context("workspace edit document change omitted textDocument.uri")?
                .to_string();
            let version = change
                .pointer("/textDocument/version")
                .and_then(Value::as_i64);
            let edits = change
                .get("edits")
                .and_then(Value::as_array)
                .context("workspace edit document change omitted edits")?
                .clone();
            operations.push((uri, version, edits));
        }
        return Ok(operations);
    }
    if let Some(changes) = edit.get("changes").and_then(Value::as_object) {
        for (uri, edits) in changes {
            let edits = edits
                .as_array()
                .context("workspace edit changes entry was not an edit array")?
                .clone();
            operations.push((uri.clone(), None, edits));
        }
        return Ok(operations);
    }
    bail!("workspace edit carried neither documentChanges nor changes")
}

/// Apply LSP text edits to `text`, rejecting malformed ranges and overlapping
/// edits so a bad edit batch fails as a unit instead of corrupting the text.
fn apply_text_edits(text: &str, edits: &[Value]) -> Result<String> {
    let mut spans = Vec::with_capacity(edits.len());
    for edit in edits {
        let range = edit.get("range").context("text edit omitted range")?;
        let new_text = edit
            .get("newText")
            .and_then(Value::as_str)
            .context("text edit omitted newText")?;
        let start = position_to_byte(
            text,
            range.pointer("/start/line").and_then(Value::as_u64),
            range.pointer("/start/character").and_then(Value::as_u64),
        )?;
        let end = position_to_byte(
            text,
            range.pointer("/end/line").and_then(Value::as_u64),
            range.pointer("/end/character").and_then(Value::as_u64),
        )?;
        if start > end {
            bail!("text edit range start is after its end");
        }
        spans.push((start, end, new_text.to_string()));
    }
    spans.sort_by_key(|(start, end, _)| (*start, *end));
    for pair in spans.windows(2) {
        if pair[0].1 > pair[1].0 {
            bail!("text edits overlap");
        }
    }
    let mut updated = text.to_string();
    for (start, end, new_text) in spans.into_iter().rev() {
        updated.replace_range(start..end, &new_text);
    }
    Ok(updated)
}

/// Convert an LSP position (zero-based line, UTF-16 character offset) to a
/// byte offset. Characters past the line end clamp to the line end per the
/// specification; a line past the document end is an error.
fn position_to_byte(text: &str, line: Option<u64>, character: Option<u64>) -> Result<usize> {
    let line = line.context("text edit position omitted line")?;
    let character = character.context("text edit position omitted character")?;
    let mut offset = 0usize;
    let mut current_line = 0u64;
    let mut segments = text.split_inclusive('\n').peekable();
    while let Some(segment) = segments.next() {
        if current_line == line {
            let mut units = 0u64;
            for symbol in segment.chars() {
                if symbol == '\n' || symbol == '\r' {
                    break;
                }
                if units >= character {
                    return Ok(offset);
                }
                units += symbol.len_utf16() as u64;
                offset += symbol.len_utf8();
            }
            return Ok(offset);
        }
        offset += segment.len();
        current_line += 1;
        if segments.peek().is_none() && current_line == line {
            return Ok(offset);
        }
    }
    if current_line == line {
        return Ok(offset);
    }
    bail!("text edit position line {line} is outside the document")
}

/// One conformance check outcome. `state` is `pass`, `fail`, `unsupported`
/// (the server did not advertise the capability), `unobserved` (optional
/// behavior the server never exhibited), or `skipped`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspConformanceCheck {
    pub name: String,
    pub state: String,
    pub detail: String,
}

/// The fixture document a conformance run opens against the server.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspConformanceFixture {
    pub relative_path: String,
    pub language_id: String,
    pub text: String,
    pub line: u64,
    pub character: u64,
}

/// Per-server conformance report. The boolean columns mirror the capability
/// matrix so a full-depth re-run (P5-009b) can publish matrix v2 from these
/// reports; every value is observed, never assumed.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LspConformanceReport {
    pub server_id: String,
    pub command: String,
    pub arguments: Vec<String>,
    pub probe_state: String,
    pub pull_diagnostics: bool,
    pub push_diagnostics: bool,
    pub workspace_edits: bool,
    pub multi_root: bool,
    pub checks: Vec<LspConformanceCheck>,
}

fn conformance_check(name: &str, state: &str, detail: impl Into<String>) -> LspConformanceCheck {
    LspConformanceCheck {
        name: name.to_string(),
        state: state.to_string(),
        detail: detail.into(),
    }
}

/// Run the per-feature conformance sequence against one server: initialize,
/// document sync, pull and push diagnostics, hover, cancellation, rename
/// workspace edit, and multi-root folders. Works against real servers and the
/// scripted fake-server harness alike; unavailable capabilities are reported
/// as `unsupported`, never as failures.
pub async fn run_server_conformance(
    supervisor: &LspSupervisor,
    spec: LspServerSpec,
    root: &Path,
    fixture: &LspConformanceFixture,
) -> LspConformanceReport {
    let mut report = LspConformanceReport {
        server_id: spec.server_id.clone(),
        command: spec.command.clone(),
        arguments: spec.arguments.clone(),
        probe_state: "unprobed".to_string(),
        pull_diagnostics: false,
        push_diagnostics: false,
        workspace_edits: false,
        multi_root: false,
        checks: Vec::new(),
    };
    let workspace_id = Uuid::now_v7();
    let server_id = spec.server_id.clone();
    let probe = match supervisor.ensure_server(spec).await {
        Ok(probe) => {
            report.probe_state = "initialized".to_string();
            report
                .checks
                .push(conformance_check("initialize", "pass", &report.command));
            probe
        }
        Err(error) => {
            report.probe_state = "start_failed".to_string();
            report.checks.push(conformance_check(
                "initialize",
                "fail",
                format!("{error:#}"),
            ));
            return report;
        }
    };
    let uri = Url::from_file_path(root.join(&fixture.relative_path))
        .map(|url| url.to_string())
        .unwrap_or_else(|_| format!("file:///{}", fixture.relative_path));
    let version = match supervisor
        .sync_document(&server_id, &uri, &fixture.language_id, &fixture.text)
        .await
    {
        Ok(version) => {
            report.checks.push(conformance_check(
                "document_sync",
                "pass",
                format!("opened at version {version}"),
            ));
            version
        }
        Err(error) => {
            report.checks.push(conformance_check(
                "document_sync",
                "fail",
                format!("{error:#}"),
            ));
            return report;
        }
    };
    let content_scope = format!("{:x}", Sha256::digest(fixture.text.as_bytes()));
    let run_query = |method: &str, params: Value| LspQuery {
        workspace_id,
        server_id: server_id.clone(),
        method: method.to_string(),
        params,
        cache_scope: content_scope.clone(),
        document_uri: Some(uri.clone()),
        document_version: Some(version),
    };
    let settle = |result: LspQueryResult| async {
        match result {
            LspQueryResult::Ready { value, .. } => Ok(Some(value)),
            LspQueryResult::Pending { request_id, .. } => {
                match supervisor
                    .completion(request_id, Duration::from_secs(5))
                    .await
                {
                    Some(event) => match event.error {
                        Some(error) => Err(anyhow::anyhow!(error)),
                        None => Ok(event.value),
                    },
                    None => Ok(None),
                }
            }
        }
    };
    if supervisor
        .supports(&server_id, "textDocument/diagnostic")
        .await
        .unwrap_or(false)
    {
        let result = supervisor
            .query(run_query(
                "textDocument/diagnostic",
                json!({"textDocument":{"uri":uri},"identifier":"soleaux"}),
            ))
            .await;
        match result {
            Ok(result) => match settle(result).await {
                Ok(Some(_)) => {
                    report.pull_diagnostics = true;
                    report.checks.push(conformance_check(
                        "pull_diagnostics",
                        "pass",
                        "report received",
                    ));
                }
                Ok(None) => report.checks.push(conformance_check(
                    "pull_diagnostics",
                    "unobserved",
                    "no report before the wait elapsed",
                )),
                Err(error) => report.checks.push(conformance_check(
                    "pull_diagnostics",
                    "fail",
                    format!("{error:#}"),
                )),
            },
            Err(error) => report.checks.push(conformance_check(
                "pull_diagnostics",
                "fail",
                format!("{error:#}"),
            )),
        }
    } else {
        report.checks.push(conformance_check(
            "pull_diagnostics",
            "unsupported",
            "diagnosticProvider not advertised",
        ));
    }
    match supervisor
        .wait_for_push_diagnostics(&server_id, &uri, Duration::from_millis(1500))
        .await
    {
        Some(push) => {
            report.push_diagnostics = true;
            report.checks.push(conformance_check(
                "push_diagnostics",
                "pass",
                format!("{} items pushed", push.items.len()),
            ));
        }
        None => report.checks.push(conformance_check(
            "push_diagnostics",
            "unobserved",
            "no publishDiagnostics within the wait window",
        )),
    }
    if supervisor
        .supports(&server_id, "textDocument/hover")
        .await
        .unwrap_or(false)
    {
        let result = supervisor
            .query(run_query(
                "textDocument/hover",
                json!({"textDocument":{"uri":uri},"position":{"line":fixture.line,"character":fixture.character}}),
            ))
            .await;
        match result {
            Ok(result) => match settle(result).await {
                Ok(_) => report
                    .checks
                    .push(conformance_check("hover", "pass", "responded")),
                Err(error) => {
                    report
                        .checks
                        .push(conformance_check("hover", "fail", format!("{error:#}")))
                }
            },
            Err(error) => {
                report
                    .checks
                    .push(conformance_check("hover", "fail", format!("{error:#}")))
            }
        }
    } else {
        report.checks.push(conformance_check(
            "hover",
            "unsupported",
            "hoverProvider not advertised",
        ));
    }
    if supervisor
        .supports(&server_id, "textDocument/hover")
        .await
        .unwrap_or(false)
    {
        match supervisor
            .query(run_query(
                "textDocument/hover",
                json!({"textDocument":{"uri":uri},"position":{"line":fixture.line,"character":fixture.character},"workDoneToken":"soleaux-cancel-probe"}),
            ))
            .await
        {
            Ok(LspQueryResult::Pending { request_id, .. }) => {
                match supervisor.cancel(request_id).await {
                    Ok(true) => report.checks.push(conformance_check(
                        "cancellation",
                        "pass",
                        "in-flight request canceled",
                    )),
                    Ok(false) => report.checks.push(conformance_check(
                        "cancellation",
                        "skipped",
                        "request completed before the cancel",
                    )),
                    Err(error) => report.checks.push(conformance_check(
                        "cancellation",
                        "fail",
                        format!("{error:#}"),
                    )),
                }
            }
            Ok(LspQueryResult::Ready { .. }) => report.checks.push(conformance_check(
                "cancellation",
                "skipped",
                "request completed inside the soft deadline",
            )),
            Err(error) => report.checks.push(conformance_check(
                "cancellation",
                "fail",
                format!("{error:#}"),
            )),
        }
    } else {
        report.checks.push(conformance_check(
            "cancellation",
            "skipped",
            "no cancellable capability advertised",
        ));
    }
    if supervisor
        .supports(&server_id, "textDocument/rename")
        .await
        .unwrap_or(false)
    {
        let result = supervisor
            .query(run_query(
                "textDocument/rename",
                json!({"textDocument":{"uri":uri},"position":{"line":fixture.line,"character":fixture.character},"newName":"soleauxConformanceRename"}),
            ))
            .await;
        let settled = match result {
            Ok(result) => settle(result).await,
            Err(error) => Err(error),
        };
        match settled {
            Ok(Some(edit)) if !edit.is_null() => {
                match supervisor.apply_workspace_edit(&server_id, &edit).await {
                    Ok(outcome) => {
                        report.workspace_edits = true;
                        report.checks.push(conformance_check(
                            "workspace_edit",
                            "pass",
                            format!("applied to {} documents", outcome.applied.len()),
                        ));
                    }
                    Err(error) => report.checks.push(conformance_check(
                        "workspace_edit",
                        "fail",
                        format!("{error:#}"),
                    )),
                }
            }
            Ok(_) => report.checks.push(conformance_check(
                "workspace_edit",
                "unobserved",
                "rename returned no edit",
            )),
            Err(error) => report.checks.push(conformance_check(
                "workspace_edit",
                "fail",
                format!("{error:#}"),
            )),
        }
    } else {
        report.checks.push(conformance_check(
            "workspace_edit",
            "unsupported",
            "renameProvider not advertised",
        ));
    }
    let folders_supported = probe
        .capabilities
        .pointer("/workspace/workspaceFolders/supported")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if folders_supported {
        let extra = json!({
            "uri": format!("{}soleaux-conformance-extra/", ensure_trailing_slash(&probe_root_uri(supervisor, &server_id).unwrap_or_default())),
            "name": "soleaux-conformance-extra",
        });
        match supervisor
            .update_workspace_folders(&server_id, vec![extra.clone()], Vec::new())
            .await
        {
            Ok(folders) => {
                report.multi_root = true;
                report.checks.push(conformance_check(
                    "multi_root",
                    "pass",
                    format!("folder set now has {} entries", folders.len()),
                ));
                let _ = supervisor
                    .update_workspace_folders(&server_id, Vec::new(), vec![extra])
                    .await;
            }
            Err(error) => report.checks.push(conformance_check(
                "multi_root",
                "fail",
                format!("{error:#}"),
            )),
        }
    } else {
        report.checks.push(conformance_check(
            "multi_root",
            "unsupported",
            "workspace.workspaceFolders.supported not advertised",
        ));
    }
    let _ = supervisor.close_document(&server_id, &uri).await;
    report
}

fn probe_root_uri(supervisor: &LspSupervisor, server_id: &str) -> Option<String> {
    supervisor
        .inner
        .specs
        .get(server_id)
        .map(|entry| entry.value().root_uri.clone())
}

fn ensure_trailing_slash(uri: &str) -> String {
    if uri.ends_with('/') {
        uri.to_string()
    } else {
        format!("{uri}/")
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

/// Sample resident-set bytes and accumulated CPU seconds for `pid` through
/// `ps`. Returns `None` where sampling is unsupported (no `ps`, or Windows),
/// which truthfully disables RSS/CPU enforcement rather than guessing.
async fn sample_process(pid: u32) -> Option<(u64, f64)> {
    if cfg!(windows) {
        return None;
    }
    let output = Command::new("ps")
        .args(["-o", "rss=,time=", "-p", &pid.to_string()])
        .output()
        .await
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut parts = text.split_whitespace();
    let rss_kb: u64 = parts.next()?.parse().ok()?;
    let cpu_seconds = parse_cpu_time(parts.next()?)?;
    Some((rss_kb * 1024, cpu_seconds))
}

/// Parse `ps` cumulative CPU time: `[dd-]hh:mm:ss` on Linux, `mm:ss.ff` on
/// macOS.
fn parse_cpu_time(text: &str) -> Option<f64> {
    let (days, clock) = match text.split_once('-') {
        Some((days, clock)) => (days.parse::<f64>().ok()?, clock),
        None => (0.0, text),
    };
    let mut seconds = 0.0;
    for part in clock.split(':') {
        seconds = seconds * 60.0 + part.parse::<f64>().ok()?;
    }
    Some(days * 86_400.0 + seconds)
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
        cpu_limit_percent: default_cpu_limit_percent(),
        maximum_concurrent_requests: default_maximum_concurrent_requests(),
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
    use tempfile::TempDir;

    /// Scripted fake language server driven by a JSON behavior configuration
    /// (argv[1]). It records every received message and serves the recording
    /// through the `soleaux/testLog` request, so tests assert what actually
    /// crossed the wire. CI never needs a live language server.
    const FAKE_LANGUAGE_SERVER: &str = r#"import json
import sys
import threading
import time

configuration = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
log = []
folder_responses = []
apply_edit_response = None
hung = {}
next_server_id = 1000
pending_server = {}


def read_message(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    if length is None:
        return None
    return json.loads(stream.read(length))


def write_message(stream, payload):
    body = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


if configuration.get("exitBeforeInitialize"):
    sys.exit(1)
if configuration.get("allocateBytes"):
    retained = bytearray(int(configuration["allocateBytes"]))
if configuration.get("spinCpu"):
    def spin():
        while True:
            pass
    threading.Thread(target=spin, daemon=True).start()

while True:
    message = read_message(sys.stdin.buffer)
    if message is None:
        break
    method = message.get("method")
    identifier = message.get("id")
    log.append({"method": method, "id": identifier, "params": message.get("params")})
    if identifier is not None and method is None:
        purpose = pending_server.pop(identifier, None)
        if purpose == "folders":
            folder_responses.append(message.get("result"))
        elif purpose == "applyEdit":
            apply_edit_response = message.get("result")
        continue
    if method == "initialize":
        write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": {"capabilities": configuration.get("capabilities", {})}})
        continue
    if method == "initialized":
        if configuration.get("applyEditAfterInitialized") is not None:
            pending_server[next_server_id] = "applyEdit"
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": next_server_id, "method": "workspace/applyEdit", "params": {"edit": configuration["applyEditAfterInitialized"]}})
            next_server_id += 1
        continue
    if method == "textDocument/didOpen":
        if configuration.get("pushDiagnosticsOnOpen") is not None:
            text_document = message["params"]["textDocument"]
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": text_document["uri"], "version": text_document["version"], "diagnostics": configuration["pushDiagnosticsOnOpen"]}})
        continue
    if method == "workspace/didChangeWorkspaceFolders":
        if configuration.get("requestFoldersOnFolderChange"):
            pending_server[next_server_id] = "folders"
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": next_server_id, "method": "workspace/workspaceFolders", "params": None})
            next_server_id += 1
        continue
    if method == "$/cancelRequest":
        target = message["params"]["id"]
        if target in hung:
            hung.pop(target)
            write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": target, "error": {"code": -32800, "message": "request canceled"}})
        continue
    if identifier is None:
        continue
    if method == "soleaux/testLog":
        write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": {"messages": log, "folderResponses": folder_responses, "applyEditResponse": apply_edit_response}})
        continue
    if method in configuration.get("hangMethods", []):
        hung[identifier] = method
        continue
    delay = configuration.get("delaySeconds", {}).get(method)
    if delay:
        time.sleep(delay)
    if method == "textDocument/rename" and configuration.get("renameEditNewText") is not None:
        uri = message["params"]["textDocument"]["uri"]
        edit = {"changes": {uri: [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}, "newText": configuration["renameEditNewText"]}]}}
        write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": edit})
        continue
    if method in configuration.get("echoMethods", []):
        write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": {"echo": message.get("params")}})
        continue
    write_message(sys.stdout.buffer, {"jsonrpc": "2.0", "id": identifier, "result": configuration.get("results", {}).get(method)})
"#;

    const DOC_URI: &str = "file:///workspace/sample.ts";

    fn stub_spec(temp: &TempDir, configuration: &Value) -> LspServerSpec {
        let script = temp.path().join("fake_language_server.py");
        std::fs::write(&script, FAKE_LANGUAGE_SERVER).expect("write fake server script");
        let root_uri = Url::from_directory_path(temp.path())
            .expect("root uri")
            .to_string();
        LspServerSpec {
            server_id: "stub".to_string(),
            command: "python3".to_string(),
            arguments: vec![
                script.to_string_lossy().to_string(),
                configuration.to_string(),
            ],
            root_uri: root_uri.clone(),
            initialization_options: Value::Null,
            workspace_folders: vec![json!({"uri": root_uri, "name": "workspace"})],
            hard_timeout_ms: 10_000,
            idle_timeout_ms: 600_000,
            rss_limit_bytes: 2 * 1024 * 1024 * 1024,
            maximum_open_documents: 16,
            cpu_limit_percent: 400.0,
            maximum_concurrent_requests: 8,
        }
    }

    async fn start_stub(
        configuration: Value,
        supervisor: LspSupervisor,
        adjust: impl FnOnce(&mut LspServerSpec),
    ) -> (LspSupervisor, TempDir) {
        let temp = tempfile::tempdir().expect("tempdir");
        let mut spec = stub_spec(&temp, &configuration);
        adjust(&mut spec);
        supervisor
            .ensure_server(spec)
            .await
            .expect("fake language server starts");
        (supervisor, temp)
    }

    fn stub_query(method: &str, params: Value) -> LspQuery {
        LspQuery {
            workspace_id: Uuid::now_v7(),
            server_id: "stub".to_string(),
            method: method.to_string(),
            params,
            cache_scope: "scope".to_string(),
            document_uri: Some(DOC_URI.to_string()),
            document_version: Some(1),
        }
    }

    async fn test_log(supervisor: &LspSupervisor) -> Value {
        supervisor
            .raw_request("stub", "soleaux/testLog", json!({}))
            .await
            .expect("test log request")
    }

    fn logged_entries<'a>(log: &'a Value, method: &str) -> Vec<&'a Value> {
        log["messages"]
            .as_array()
            .expect("messages array")
            .iter()
            .filter(|message| message.get("method").and_then(Value::as_str) == Some(method))
            .collect()
    }

    fn stub_health(supervisor: &LspSupervisor) -> LspServerHealth {
        supervisor
            .health()
            .into_iter()
            .find(|entry| entry.server_id == "stub")
            .expect("stub health entry")
    }

    async fn wait_for(mut condition: impl FnMut() -> bool, deadline: Duration) -> bool {
        let end = Instant::now() + deadline;
        loop {
            if condition() {
                return true;
            }
            if Instant::now() >= end {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    }

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
    fn server_requests_are_answered_from_tracked_state() {
        let (outbound, _receiver) = mpsc::channel(8);
        let shared = Arc::new(ServerShared {
            outbound,
            pending: DashMap::new(),
            workspace_folders: StdMutex::new(vec![json!({"uri":"file:///w/","name":"w"})]),
            push_diagnostics: DashMap::new(),
            push_notify: Notify::new(),
        });
        let configuration = json!({"jsonrpc":"2.0","id":7,"method":"workspace/configuration","params":{"items":[{},{}]}});
        let response = server_request_response(&shared, &configuration);
        assert_eq!(response["result"].as_array().map(Vec::len), Some(2));

        let folders = json!({"jsonrpc":"2.0","id":8,"method":"workspace/workspaceFolders"});
        let response = server_request_response(&shared, &folders);
        assert_eq!(
            response.pointer("/result/0/uri").and_then(Value::as_str),
            Some("file:///w/")
        );

        let apply_edit =
            json!({"jsonrpc":"2.0","id":9,"method":"workspace/applyEdit","params":{"edit":{}}});
        let response = server_request_response(&shared, &apply_edit);
        assert_eq!(response.pointer("/result/applied"), Some(&json!(false)));
        assert!(
            response
                .pointer("/result/failureReason")
                .and_then(Value::as_str)
                .is_some_and(|reason| reason.contains("preview/edit"))
        );

        let unknown = json!({"jsonrpc":"2.0","id":10,"method":"workspace/unknownRequest"});
        let response = server_request_response(&shared, &unknown);
        assert_eq!(response.pointer("/error/code"), Some(&json!(-32601)));
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

    #[test]
    fn cache_keys_are_request_parameter_aware() {
        let position_one = stub_query(
            "textDocument/hover",
            json!({"textDocument":{"uri":DOC_URI},"position":{"line":1,"character":2}}),
        );
        let position_two = stub_query(
            "textDocument/hover",
            json!({"textDocument":{"uri":DOC_URI},"position":{"line":50,"character":2}}),
        );
        assert_ne!(
            parameter_cache_key(&position_one),
            parameter_cache_key(&position_two),
            "different parameters must never share a cache entry"
        );

        let reordered = stub_query(
            "textDocument/hover",
            json!({"position":{"character":2,"line":1},"textDocument":{"uri":DOC_URI}}),
        );
        assert_eq!(
            parameter_cache_key(&position_one),
            parameter_cache_key(&reordered),
            "semantically equal parameters must share a cache entry"
        );

        let other_method = LspQuery {
            method: "textDocument/definition".to_string(),
            ..position_one.clone()
        };
        assert_ne!(
            parameter_cache_key(&position_one),
            parameter_cache_key(&other_method)
        );
        let other_version = LspQuery {
            document_version: Some(2),
            ..position_one.clone()
        };
        assert_ne!(
            parameter_cache_key(&position_one),
            parameter_cache_key(&other_version)
        );
    }

    #[test]
    fn text_edits_apply_utf16_positions_and_reject_overlaps() {
        let text = "a😀b\nsecond\n";
        let replaced = apply_text_edits(
            text,
            &[json!({
                "range": {"start":{"line":0,"character":1},"end":{"line":0,"character":3}},
                "newText": "-",
            })],
        )
        .expect("surrogate-pair range applies");
        assert_eq!(replaced, "a-b\nsecond\n");

        let multi = apply_text_edits(
            text,
            &[
                json!({
                    "range": {"start":{"line":1,"character":0},"end":{"line":1,"character":6}},
                    "newText": "SECOND",
                }),
                json!({
                    "range": {"start":{"line":0,"character":0},"end":{"line":0,"character":1}},
                    "newText": "A",
                }),
            ],
        )
        .expect("descending application keeps earlier offsets valid");
        assert_eq!(multi, "A😀b\nSECOND\n");

        let overlap = apply_text_edits(
            "abcdefgh",
            &[
                json!({"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":5}},"newText":"x"}),
                json!({"range":{"start":{"line":0,"character":3},"end":{"line":0,"character":8}},"newText":"y"}),
            ],
        );
        assert!(
            overlap
                .expect_err("overlap")
                .to_string()
                .contains("overlap")
        );

        let outside = position_to_byte("one\n", Some(4), Some(0));
        assert!(
            outside
                .expect_err("line outside document")
                .to_string()
                .contains("outside the document")
        );
    }

    #[test]
    fn workspace_edit_resource_operations_are_refused() {
        let edit = json!({"documentChanges":[{"kind":"rename","oldUri":"file:///a","newUri":"file:///b"}]});
        let error = workspace_edit_operations(&edit).expect_err("resource operation");
        assert!(error.to_string().contains("not supported"));
        assert!(
            workspace_edit_operations(&json!({}))
                .expect_err("empty edit")
                .to_string()
                .contains("neither documentChanges nor changes")
        );
    }

    #[test]
    fn cpu_time_parses_linux_and_macos_formats() {
        assert_eq!(parse_cpu_time("1:02.50"), Some(62.5));
        assert_eq!(parse_cpu_time("01:02:03"), Some(3723.0));
        assert_eq!(parse_cpu_time("2-01:00:00"), Some(2.0 * 86_400.0 + 3600.0));
        assert_eq!(parse_cpu_time("garbage"), None);
    }

    #[tokio::test]
    async fn versioned_document_edits_enforce_ordering() {
        let (supervisor, _temp) = start_stub(
            json!({"capabilities": {"hoverProvider": true}}),
            LspSupervisor::new(8 * 1024 * 1024),
            |_| {},
        )
        .await;

        let opened = supervisor
            .sync_document("stub", DOC_URI, "typescript", "one\n")
            .await
            .expect("open");
        assert_eq!(opened, 1);
        supervisor
            .change_document("stub", DOC_URI, 3, "three\n")
            .await
            .expect("monotonic version advances");
        let stale = supervisor
            .change_document("stub", DOC_URI, 2, "two\n")
            .await
            .expect_err("stale version is rejected");
        assert!(stale.to_string().contains("out-of-order document version"));
        let resynced = supervisor
            .sync_document("stub", DOC_URI, "typescript", "four\n")
            .await
            .expect("sync assigns the next version");
        assert_eq!(resynced, 4);
        let unchanged = supervisor
            .sync_document("stub", DOC_URI, "typescript", "four\n")
            .await
            .expect("identical text is a no-op");
        assert_eq!(unchanged, 4);

        let log = test_log(&supervisor).await;
        let versions: Vec<i64> = logged_entries(&log, "textDocument/didChange")
            .iter()
            .filter_map(|entry| entry.pointer("/params/textDocument/version"))
            .filter_map(Value::as_i64)
            .collect();
        assert_eq!(
            versions,
            vec![3, 4],
            "the rejected edit never reached the wire"
        );
        assert_eq!(logged_entries(&log, "textDocument/didOpen").len(), 1);
    }

    #[tokio::test]
    async fn push_diagnostics_are_received_and_retained() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "pushDiagnosticsOnOpen": [{"message": "pushed", "severity": 1}],
            }),
            LspSupervisor::new(8 * 1024 * 1024),
            |_| {},
        )
        .await;

        supervisor
            .sync_document("stub", DOC_URI, "typescript", "one\n")
            .await
            .expect("open");
        let pushed = supervisor
            .wait_for_push_diagnostics("stub", DOC_URI, Duration::from_secs(5))
            .await
            .expect("publishDiagnostics arrives");
        assert_eq!(pushed.items.len(), 1);
        assert_eq!(
            pushed.items[0].get("message").and_then(Value::as_str),
            Some("pushed")
        );
        assert_eq!(pushed.version, Some(1));
        assert!(
            supervisor.push_diagnostics("stub", DOC_URI).is_some(),
            "push diagnostics stay retained for later reads"
        );
    }

    #[tokio::test]
    async fn cancellation_cancels_in_flight_requests() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true, "definitionProvider": true},
                "hangMethods": ["textDocument/hover"],
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_soft_deadline(Duration::from_millis(60)),
            |_| {},
        )
        .await;

        let result = supervisor
            .query(stub_query(
                "textDocument/hover",
                json!({"textDocument":{"uri":DOC_URI},"position":{"line":0,"character":0}}),
            ))
            .await
            .expect("query issues");
        let LspQueryResult::Pending { request_id, .. } = result else {
            panic!("hung request must go pending");
        };
        assert!(
            supervisor.cancel(request_id).await.expect("cancel"),
            "the request was in flight"
        );
        let event = supervisor
            .completion(request_id, Duration::from_secs(5))
            .await
            .expect("completion event for the canceled request");
        assert!(
            event
                .error
                .as_deref()
                .is_some_and(|error| error.contains("canceled")),
            "cancellation resolves the pending request: {event:?}"
        );
        assert!(
            !supervisor.cancel(request_id).await.expect("second cancel"),
            "a completed request is no longer in flight"
        );

        let log = test_log(&supervisor).await;
        assert!(
            !logged_entries(&log, "$/cancelRequest").is_empty(),
            "$/cancelRequest reached the server"
        );
    }

    #[tokio::test]
    async fn workspace_edit_application_is_atomic_with_rollback() {
        let (supervisor, _temp) = start_stub(
            json!({"capabilities": {"hoverProvider": true}}),
            LspSupervisor::new(8 * 1024 * 1024),
            |_| {},
        )
        .await;
        let first = "file:///workspace/first.ts";
        let second = "file:///workspace/second.ts";
        supervisor
            .sync_document("stub", first, "typescript", "alpha beta\n")
            .await
            .expect("open first");
        supervisor
            .sync_document("stub", second, "typescript", "gamma delta\n")
            .await
            .expect("open second");

        let stale = supervisor
            .apply_workspace_edit(
                "stub",
                &json!({"documentChanges": [
                    {"textDocument": {"uri": first, "version": 1},
                     "edits": [{"range": {"start": {"line":0,"character":0}, "end": {"line":0,"character":5}}, "newText": "ALPHA"}]},
                    {"textDocument": {"uri": second, "version": 99},
                     "edits": [{"range": {"start": {"line":0,"character":0}, "end": {"line":0,"character":5}}, "newText": "GAMMA"}]},
                ]}),
            )
            .await
            .expect_err("a stale version fails the whole edit");
        assert!(stale.to_string().contains("stale version 99"));
        assert_eq!(
            supervisor
                .sync_document("stub", first, "typescript", "alpha beta\n")
                .await
                .expect("first document unchanged"),
            1,
            "the failing plan mutated nothing"
        );

        let outcome = supervisor
            .apply_workspace_edit(
                "stub",
                &json!({"documentChanges": [
                    {"textDocument": {"uri": first, "version": 1},
                     "edits": [{"range": {"start": {"line":0,"character":0}, "end": {"line":0,"character":5}}, "newText": "ALPHA"}]},
                    {"textDocument": {"uri": second, "version": 1},
                     "edits": [{"range": {"start": {"line":0,"character":0}, "end": {"line":0,"character":5}}, "newText": "GAMMA"}]},
                ]}),
            )
            .await
            .expect("valid edit applies");
        assert_eq!(outcome.applied.len(), 2);
        assert!(outcome.applied.iter().all(|entry| entry.version == 2));
        assert_eq!(
            supervisor
                .sync_document("stub", first, "typescript", "ALPHA beta\n")
                .await
                .expect("first text tracked"),
            2,
            "the applied text is the tracked overlay text"
        );

        let log = test_log(&supervisor).await;
        assert_eq!(
            logged_entries(&log, "textDocument/didChange").len(),
            2,
            "only the atomic commit reached the wire"
        );
    }

    #[tokio::test]
    async fn pending_completions_are_redeemable_and_cached_per_parameters() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "echoMethods": ["textDocument/hover"],
                "delaySeconds": {"textDocument/hover": 0.3},
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_soft_deadline(Duration::from_millis(60)),
            |_| {},
        )
        .await;
        let position_one =
            json!({"textDocument":{"uri":DOC_URI},"position":{"line":1,"character":0}});
        let position_two =
            json!({"textDocument":{"uri":DOC_URI},"position":{"line":2,"character":0}});

        let first = supervisor
            .query(stub_query("textDocument/hover", position_one.clone()))
            .await
            .expect("first query");
        let LspQueryResult::Pending {
            request_id, cached, ..
        } = first
        else {
            panic!("delayed request must go pending");
        };
        assert!(
            cached.is_none(),
            "nothing cached before the first completion"
        );
        let event = supervisor
            .completion(request_id, Duration::from_secs(5))
            .await
            .expect("completion event");
        assert_eq!(event.value, Some(json!({"echo": position_one})));

        let second = supervisor
            .query(stub_query("textDocument/hover", position_two.clone()))
            .await
            .expect("second query");
        let LspQueryResult::Pending {
            request_id, cached, ..
        } = second
        else {
            panic!("delayed request must go pending");
        };
        assert!(
            cached.is_none(),
            "a different position must not reuse the first cached value"
        );
        supervisor
            .completion(request_id, Duration::from_secs(5))
            .await
            .expect("second completion");
        assert_eq!(supervisor.cache_entries().await, 2);

        let repeat = supervisor
            .query(stub_query("textDocument/hover", position_one.clone()))
            .await
            .expect("repeat query");
        let LspQueryResult::Pending { cached, .. } = repeat else {
            panic!("delayed request must go pending");
        };
        assert_eq!(
            cached,
            Some(json!({"echo": position_one})),
            "the repeat serves its own parameter-scoped cache entry"
        );
    }

    #[tokio::test]
    async fn concurrency_limit_rejects_excess_requests_truthfully() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true, "definitionProvider": true},
                "hangMethods": ["textDocument/hover"],
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_soft_deadline(Duration::from_millis(60)),
            |spec| spec.maximum_concurrent_requests = 1,
        )
        .await;

        let first = supervisor
            .query(stub_query("textDocument/hover", json!({"probe": 1})))
            .await
            .expect("first query issues");
        let LspQueryResult::Pending { request_id, .. } = first else {
            panic!("hung request must go pending");
        };
        let rejected = supervisor
            .query(stub_query("textDocument/definition", json!({"probe": 2})))
            .await
            .expect_err("the second request exceeds the concurrency limit");
        assert!(rejected.to_string().contains("concurrency limit"));

        supervisor.cancel(request_id).await.expect("cancel");
        supervisor
            .completion(request_id, Duration::from_secs(5))
            .await
            .expect("canceled completion releases the permit");
        let after = supervisor
            .query(stub_query("textDocument/definition", json!({"probe": 3})))
            .await
            .expect("capacity is available after cancellation");
        assert!(matches!(after, LspQueryResult::Ready { .. }));
    }

    #[tokio::test]
    async fn crash_loop_quarantines_after_repeated_failures_and_restart_clears() {
        let temp = tempfile::tempdir().expect("tempdir");
        let spec = stub_spec(&temp, &json!({"exitBeforeInitialize": true}));
        let supervisor =
            LspSupervisor::new(8 * 1024 * 1024).with_quarantine_policy(QuarantinePolicy {
                threshold: 3,
                window: Duration::from_secs(60),
                cooldown: Duration::from_secs(60),
            });

        for attempt in 0..2 {
            let error = supervisor
                .ensure_server(spec.clone())
                .await
                .expect_err("start fails");
            assert!(
                !error.to_string().contains("quarantined"),
                "attempt {attempt} stays below the quarantine threshold: {error:#}"
            );
        }
        let third = supervisor
            .ensure_server(spec.clone())
            .await
            .expect_err("third failure quarantines");
        assert!(third.to_string().contains("quarantined"));
        let fourth = supervisor
            .ensure_server(spec.clone())
            .await
            .expect_err("quarantine refuses fast");
        assert!(fourth.to_string().contains("is quarantined for"));

        let health = stub_health(&supervisor);
        assert!(!health.running);
        assert!(health.quarantined_remaining_ms.is_some());
        assert!(health.last_failure_reason.is_some());

        let after_restart = supervisor
            .restart("stub")
            .await
            .expect_err("restart clears quarantine and retries the broken server");
        assert!(
            !after_restart.to_string().contains("is quarantined for"),
            "restart cleared the quarantine before retrying: {after_restart:#}"
        );
    }

    #[tokio::test]
    async fn idle_servers_stop_and_reconnect_replays_documents() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "results": {"textDocument/hover": {"contents": "ok"}},
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_sweep_interval(Duration::from_millis(40)),
            |spec| spec.idle_timeout_ms = 150,
        )
        .await;
        supervisor
            .sync_document("stub", DOC_URI, "typescript", "one\n")
            .await
            .expect("open");

        assert!(
            wait_for(
                || {
                    let health = stub_health(&supervisor);
                    !health.running
                        && health
                            .stopped_reason
                            .as_deref()
                            .is_some_and(|reason| reason.contains("idle"))
                },
                Duration::from_secs(5),
            )
            .await,
            "the idle limit stops the server: {:?}",
            stub_health(&supervisor)
        );

        let recovered = supervisor
            .query(stub_query(
                "textDocument/hover",
                json!({"textDocument":{"uri":DOC_URI},"position":{"line":0,"character":0}}),
            ))
            .await
            .expect("query recovers the stopped server");
        assert!(matches!(recovered, LspQueryResult::Ready { .. }));

        let log = test_log(&supervisor).await;
        let opened = logged_entries(&log, "textDocument/didOpen");
        assert_eq!(
            opened.len(),
            1,
            "the fresh process replayed the tracked document"
        );
        assert_eq!(
            opened[0]
                .pointer("/params/textDocument/uri")
                .and_then(Value::as_str),
            Some(DOC_URI)
        );
        assert!(stub_health(&supervisor).restarts >= 1);
    }

    #[tokio::test]
    async fn rss_limit_terminates_the_server_truthfully() {
        if cfg!(windows) {
            return;
        }
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "allocateBytes": 160 * 1024 * 1024,
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_sweep_interval(Duration::from_millis(80)),
            |spec| spec.rss_limit_bytes = 32 * 1024 * 1024,
        )
        .await;

        assert!(
            wait_for(
                || {
                    let health = stub_health(&supervisor);
                    !health.running
                        && health
                            .limit_violations
                            .iter()
                            .any(|violation| violation.contains("rss"))
                },
                Duration::from_secs(10),
            )
            .await,
            "the RSS limit terminates the server: {:?}",
            stub_health(&supervisor)
        );
        let health = stub_health(&supervisor);
        assert!(
            health
                .stopped_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("rss")),
        );
        assert!(
            health
                .last_failure_reason
                .as_deref()
                .is_some_and(|reason| reason.contains("rss")),
            "resource kills count toward the crash-loop record"
        );
    }

    #[tokio::test]
    async fn cpu_limit_terminates_sustained_busy_servers() {
        if cfg!(windows) {
            return;
        }
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "spinCpu": true,
            }),
            LspSupervisor::new(8 * 1024 * 1024).with_sweep_interval(Duration::from_millis(200)),
            |spec| spec.cpu_limit_percent = 10.0,
        )
        .await;

        assert!(
            wait_for(
                || {
                    let health = stub_health(&supervisor);
                    !health.running
                        && health
                            .limit_violations
                            .iter()
                            .any(|violation| violation.contains("cpu"))
                },
                Duration::from_secs(15),
            )
            .await,
            "the CPU limit terminates the server: {:?}",
            stub_health(&supervisor)
        );
    }

    #[tokio::test]
    async fn open_document_cap_evicts_the_least_recently_used_document() {
        let (supervisor, _temp) = start_stub(
            json!({"capabilities": {"hoverProvider": true}}),
            LspSupervisor::new(8 * 1024 * 1024),
            |spec| spec.maximum_open_documents = 2,
        )
        .await;
        supervisor
            .sync_document("stub", "file:///workspace/a.ts", "typescript", "a\n")
            .await
            .expect("open a");
        tokio::time::sleep(Duration::from_millis(5)).await;
        supervisor
            .sync_document("stub", "file:///workspace/b.ts", "typescript", "b\n")
            .await
            .expect("open b");
        tokio::time::sleep(Duration::from_millis(5)).await;
        supervisor
            .sync_document("stub", "file:///workspace/c.ts", "typescript", "c\n")
            .await
            .expect("open c evicts a");

        assert_eq!(stub_health(&supervisor).open_documents, 2);
        let log = test_log(&supervisor).await;
        let closed = logged_entries(&log, "textDocument/didClose");
        assert_eq!(closed.len(), 1);
        assert_eq!(
            closed[0]
                .pointer("/params/textDocument/uri")
                .and_then(Value::as_str),
            Some("file:///workspace/a.ts"),
            "the least recently used document was closed"
        );
    }

    #[tokio::test]
    async fn workspace_folder_updates_answer_server_folder_requests() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "requestFoldersOnFolderChange": true,
            }),
            LspSupervisor::new(8 * 1024 * 1024),
            |_| {},
        )
        .await;

        let folders = supervisor
            .update_workspace_folders(
                "stub",
                vec![json!({"uri": "file:///workspace-b/", "name": "b"})],
                Vec::new(),
            )
            .await
            .expect("folder update");
        assert_eq!(folders.len(), 2);

        let mut observed = None;
        for _ in 0..40 {
            let log = test_log(&supervisor).await;
            let responses = log["folderResponses"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            if let Some(first) = responses.first() {
                observed = Some(first.clone());
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        let observed = observed.expect("the server asked for workspace folders and got an answer");
        let uris: Vec<&str> = observed
            .as_array()
            .expect("folder array")
            .iter()
            .filter_map(|folder| folder.get("uri").and_then(Value::as_str))
            .collect();
        assert!(uris.contains(&"file:///workspace-b/"));

        let removed = supervisor
            .update_workspace_folders(
                "stub",
                Vec::new(),
                vec![json!({"uri": "file:///workspace-b/", "name": "b"})],
            )
            .await
            .expect("folder removal");
        assert_eq!(removed.len(), 1);
    }

    #[tokio::test]
    async fn server_initiated_apply_edit_is_refused_truthfully() {
        let (supervisor, _temp) = start_stub(
            json!({
                "capabilities": {"hoverProvider": true},
                "applyEditAfterInitialized": {"changes": {}},
            }),
            LspSupervisor::new(8 * 1024 * 1024),
            |_| {},
        )
        .await;

        let mut response = None;
        for _ in 0..40 {
            let log = test_log(&supervisor).await;
            if !log["applyEditResponse"].is_null() {
                response = Some(log["applyEditResponse"].clone());
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        let response = response.expect("the applyEdit request was answered");
        assert_eq!(response.get("applied"), Some(&json!(false)));
        assert!(
            response
                .get("failureReason")
                .and_then(Value::as_str)
                .is_some_and(|reason| reason.contains("preview/edit")),
        );
    }

    #[tokio::test]
    async fn conformance_harness_reports_fake_server_depth() {
        let temp = tempfile::tempdir().expect("tempdir");
        let configuration = json!({
            "capabilities": {
                "hoverProvider": true,
                "renameProvider": true,
                "diagnosticProvider": {"interFileDependencies": false, "workspaceDiagnostics": false},
                "workspace": {"workspaceFolders": {"supported": true}},
            },
            "results": {
                "textDocument/diagnostic": {"kind": "full", "items": [{"message": "stub"}]},
                "textDocument/hover": {"contents": "stub"},
            },
            "renameEditNewText": "renamed ",
            "pushDiagnosticsOnOpen": [{"message": "pushed"}],
        });
        let spec = stub_spec(&temp, &configuration);
        let supervisor = LspSupervisor::new(8 * 1024 * 1024);
        let fixture = LspConformanceFixture {
            relative_path: "sample.ts".to_string(),
            language_id: "typescript".to_string(),
            text: "export const sample = 1;\n".to_string(),
            line: 0,
            character: 14,
        };
        let report = run_server_conformance(&supervisor, spec, temp.path(), &fixture).await;
        assert_eq!(report.probe_state, "initialized");
        assert!(report.pull_diagnostics);
        assert!(report.push_diagnostics);
        assert!(report.workspace_edits);
        assert!(report.multi_root);
        for check in &report.checks {
            assert!(
                matches!(check.state.as_str(), "pass" | "skipped"),
                "unexpected check state: {check:?}"
            );
        }
    }

    /// Local-optional live-server conformance: run with
    /// `cargo test -p soleaux-intelligence -- --ignored live_server` to probe
    /// the language servers installed on this machine and print a truthful
    /// per-family report. CI never requires live servers.
    #[tokio::test]
    #[ignore = "requires locally installed language servers"]
    async fn live_server_conformance_reports_installed_families() {
        fn live_fixture(family_id: &str) -> Option<LspConformanceFixture> {
            let (relative_path, language_id, text) = match family_id {
                "typescript" => (
                    "probe.ts",
                    "typescript",
                    "export function soleauxProbe(input: string) {\n  return input;\n}\n",
                ),
                "python" => (
                    "probe.py",
                    "python",
                    "def soleaux_probe(value):\n    return value\n",
                ),
                "bash" => ("probe.sh", "bash", "soleaux_probe() {\n  echo probe\n}\n"),
                "rust" => (
                    "probe.rs",
                    "rust",
                    "pub fn soleaux_probe(value: u32) -> u32 {\n    value\n}\n",
                ),
                "go" => (
                    "probe.go",
                    "go",
                    "package main\n\nfunc soleauxProbe(value int) int {\n\treturn value\n}\n",
                ),
                "json" => ("probe.json", "json", "{\n  \"soleaux\": true\n}\n"),
                "yaml" => ("probe.yaml", "yaml", "soleaux: true\n"),
                "css" => ("probe.css", "css", ".soleaux {\n  color: red;\n}\n"),
                "html" => (
                    "probe.html",
                    "html",
                    "<html>\n<body>probe</body>\n</html>\n",
                ),
                _ => return None,
            };
            Some(LspConformanceFixture {
                relative_path: relative_path.to_string(),
                language_id: language_id.to_string(),
                text: text.to_string(),
                line: 0,
                character: 4,
            })
        }

        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();
        let root_uri = Url::from_directory_path(root)
            .expect("root uri")
            .to_string();
        let workspace_folders = vec![json!({"uri": root_uri, "name": "conformance"})];
        let supervisor = LspSupervisor::new(32 * 1024 * 1024);
        let mut reports = Vec::new();
        for family in LANGUAGE_SERVER_FAMILIES {
            let Some(fixture) = live_fixture(family.id) else {
                println!("family {} skipped: no live fixture defined", family.id);
                continue;
            };
            let Some((command, arguments)) = family.commands.iter().find_map(|candidate| {
                find_executable(candidate.command).map(|path| (path, candidate.arguments))
            }) else {
                println!("family {} skipped: no server on PATH", family.id);
                continue;
            };
            std::fs::write(root.join(&fixture.relative_path), &fixture.text)
                .expect("write fixture");
            let spec = server_spec(
                family.id,
                command,
                arguments.iter().map(ToString::to_string).collect(),
                &root_uri,
                &workspace_folders,
            );
            let report = run_server_conformance(&supervisor, spec, root, &fixture).await;
            reports.push(report);
        }
        println!(
            "{}",
            serde_json::to_string_pretty(&reports).expect("serialize reports")
        );
    }
}
