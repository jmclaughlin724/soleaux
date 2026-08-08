//! Supervised NDJSON stdio host for the Node SDK harness.
//!
//! One supervisor task owns the connection lifecycle: connect, `hello`
//! handshake, dispatch, and bounded-backoff reconnect. After every reconnect
//! the host reconciles canonical state before reporting itself converged, so
//! a restart never trusts an in-memory picture of the transcripts. Store
//! calls are served inline in frame order — appends must not reorder — and a
//! failed append is answered as a store failure, logged, and survived: the
//! SDK logs it, emits a `mirror_error` system message, and continues, per the
//! documented mirror contract. Permission requests arrive as
//! [`PendingPermission`] events; a dropped or timed-out permission, and any
//! permission arriving without admitted write authority, is answered with the
//! fail-closed deny.

use crate::{
    CLAUDE_PLATFORM_ID,
    admission::AdmissionVerifier,
    protocol::{
        EventFrame, HOST_PROTOCOL_VERSION, HarnessFrame, HelloFrame, PermissionRequestFrame,
        StoreOp, StoreRequestFrame, encode_hello_ack, encode_permission_decision, encode_request,
        encode_store_result, parse_harness_frame,
    },
    store::{ClaudeSessionStore, ReconcileEntry, SessionKey, transcript_scope},
    version::{PINNED_CLAUDE_CODE_VERSION, sdk_version_refusal},
};
use serde_json::{Value, json};
use soleaux_ipc::AdmissionReceipt;
use std::{
    collections::{BTreeSet, HashMap},
    future::Future,
    path::PathBuf,
    pin::Pin,
    process::Stdio,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicI64, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::{mpsc, oneshot},
    task::{AbortHandle, JoinHandle},
    time::{sleep, timeout},
};

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HostError {
    /// A mutating operation was refused locally by the safe-mode gate.
    SafeMode {
        operation: String,
        reason: String,
    },
    NotConnected,
    ConnectionLost,
    Closed,
    Timeout {
        operation: String,
    },
    /// The harness answered a host request with a failure.
    Harness {
        operation: String,
        message: String,
    },
    Protocol(String),
    Spawn(String),
    /// The presented receipt does not name the Claude Code platform at the
    /// pinned version.
    ReceiptMismatch(String),
    /// The admission receipt expired; the host demoted itself to read-only.
    AdmissionExpired,
    /// The daemon-trusted verifier rejected the receipt.
    VerifierRejected(String),
    /// A blocking store task failed to join.
    StoreTask(String),
}

impl std::fmt::Display for HostError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SafeMode { operation, reason } => {
                write!(formatter, "safe mode refused {operation}: {reason}")
            }
            Self::NotConnected => write!(formatter, "the SDK harness is not connected"),
            Self::ConnectionLost => write!(formatter, "the SDK harness connection was lost"),
            Self::Closed => write!(formatter, "the Claude host is closed"),
            Self::Timeout { operation } => write!(formatter, "{operation} timed out"),
            Self::Harness { operation, message } => {
                write!(formatter, "harness failed {operation}: {message}")
            }
            Self::Protocol(detail) => write!(formatter, "protocol violation: {detail}"),
            Self::Spawn(detail) => write!(formatter, "spawn failure: {detail}"),
            Self::ReceiptMismatch(detail) => {
                write!(formatter, "admission receipt mismatch: {detail}")
            }
            Self::AdmissionExpired => write!(
                formatter,
                "the admission receipt expired; the host returned to read-only safe mode"
            ),
            Self::VerifierRejected(detail) => {
                write!(formatter, "admission receipt verification failed: {detail}")
            }
            Self::StoreTask(detail) => write!(formatter, "store task failure: {detail}"),
        }
    }
}

impl std::error::Error for HostError {}

/// One live transport to a harness: the process form for production and any
/// duplex byte stream for tests.
pub struct HarnessConnection {
    pub reader: Box<dyn AsyncRead + Send + Unpin>,
    pub writer: Box<dyn AsyncWrite + Send + Unpin>,
    pub child: Option<Child>,
}

pub trait HarnessConnector: Send + Sync + 'static {
    fn connect(&self) -> BoxFuture<'_, Result<HarnessConnection, HostError>>;
}

/// Spawns `node <harness script>` over piped stdio with a cleared,
/// allow-listed environment. Credentials are never forwarded implicitly; an
/// operator running against the real SDK passes them through `extra_env`.
#[derive(Debug, Clone)]
pub struct ProcessConnector {
    pub node_binary: PathBuf,
    pub harness_script: PathBuf,
    pub current_dir: Option<PathBuf>,
    pub extra_env: Vec<(String, String)>,
}

impl ProcessConnector {
    pub fn new(harness_script: impl Into<PathBuf>) -> Self {
        Self {
            node_binary: PathBuf::from("node"),
            harness_script: harness_script.into(),
            current_dir: None,
            extra_env: Vec::new(),
        }
    }
}

const PROCESS_ENV_ALLOWLIST: &[&str] = &[
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_AGENT_SDK_PATH",
];

impl HarnessConnector for ProcessConnector {
    fn connect(&self) -> BoxFuture<'_, Result<HarnessConnection, HostError>> {
        Box::pin(async move {
            let mut command = Command::new(&self.node_binary);
            command
                .arg(&self.harness_script)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .kill_on_drop(true)
                .env_clear();
            for key in PROCESS_ENV_ALLOWLIST {
                if let Some(value) = std::env::var_os(key) {
                    command.env(key, value);
                }
            }
            for (key, value) in &self.extra_env {
                command.env(key, value);
            }
            if let Some(current_dir) = &self.current_dir {
                command.current_dir(current_dir);
            }
            let mut child = command.spawn().map_err(|error| {
                HostError::Spawn(format!(
                    "starting {}: {error}",
                    self.harness_script.display()
                ))
            })?;
            let stdin = child
                .stdin
                .take()
                .ok_or_else(|| HostError::Spawn("harness did not expose stdin".to_string()))?;
            let stdout = child
                .stdout
                .take()
                .ok_or_else(|| HostError::Spawn("harness did not expose stdout".to_string()))?;
            if let Some(stderr) = child.stderr.take() {
                tokio::spawn(async move {
                    let mut lines = BufReader::new(stderr).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        tracing::debug!(target: "soleaux_adapter_claude", stderr = %line);
                    }
                });
            }
            Ok(HarnessConnection {
                reader: Box::new(stdout),
                writer: Box::new(stdin),
                child: Some(child),
            })
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReconnectPolicy {
    /// Reconnect attempts per outage; zero disables reconnecting.
    pub max_attempts: u32,
    pub initial_delay: Duration,
    pub max_delay: Duration,
}

impl Default for ReconnectPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            initial_delay: Duration::from_millis(250),
            max_delay: Duration::from_secs(5),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ClaudeHostConfig {
    /// The SDK package the harness must report having loaded.
    pub expected_sdk_package: String,
    pub hello_timeout: Duration,
    pub request_timeout: Duration,
    pub permission_timeout: Duration,
    pub max_frame_bytes: usize,
    pub event_buffer: usize,
    pub reconnect: ReconnectPolicy,
}

impl Default for ClaudeHostConfig {
    fn default() -> Self {
        Self {
            expected_sdk_package: "@anthropic-ai/claude-agent-sdk".to_string(),
            hello_timeout: Duration::from_secs(15),
            request_timeout: Duration::from_secs(120),
            permission_timeout: Duration::from_secs(120),
            max_frame_bytes: 4 * 1024 * 1024,
            event_buffer: 256,
            reconnect: ReconnectPolicy::default(),
        }
    }
}

/// The host's write posture. `Admitted` is only reachable through
/// [`ClaudeHost::enable_write`], and expiry is re-checked before every
/// mutating operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteAuthority {
    ReadOnly,
    Admitted { expires_at_unix_ms: i64 },
}

/// A permission decision for one `canUseTool` round-trip.
#[derive(Debug, Clone, PartialEq)]
pub enum PermissionDecision {
    Allow { updated_input: Option<Value> },
    Deny { message: String },
}

impl PermissionDecision {
    /// The fail-closed default for dropped, timed-out, or unadmitted
    /// permission requests.
    pub fn fail_closed(reason: &str) -> Self {
        Self::Deny {
            message: format!("denied by the Soleaux Claude host: {reason}"),
        }
    }

    fn to_wire(&self) -> Value {
        match self {
            Self::Allow { updated_input } => json!({
                "behavior": "allow",
                "updatedInput": updated_input,
            }),
            Self::Deny { message } => json!({
                "behavior": "deny",
                "message": message,
            }),
        }
    }
}

/// One permission request awaiting a decision. Dropping the handle without
/// responding, or letting the permission timeout elapse, answers deny.
#[derive(Debug)]
pub struct PendingPermission {
    request: Value,
    responder: oneshot::Sender<PermissionDecision>,
}

impl PendingPermission {
    pub fn request(&self) -> &Value {
        &self.request
    }

    pub fn respond(self, decision: PermissionDecision) -> Result<(), HostError> {
        self.responder
            .send(decision)
            .map_err(|_| HostError::ConnectionLost)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionStartOutcome {
    /// The session id the SDK actually created or resumed.
    pub native_session_id: String,
}

#[derive(Debug)]
pub enum ClaudeHostEvent {
    /// The handshake completed; `epoch` starts at 1 and increments per
    /// reconnect.
    Connected {
        epoch: u64,
        sdk_version: Option<String>,
        /// `Some` when this connection is version-locked into safe mode.
        safe_mode_reason: Option<String>,
    },
    /// A lifecycle hook the harness observed (`PreToolUse`, `PostToolUse`,
    /// session events, compaction hooks).
    Hook {
        name: String,
        payload: Value,
    },
    /// A system message from the SDK iterator, including `compact_boundary`
    /// and `mirror_error` subtypes.
    System {
        payload: Value,
    },
    /// A non-system SDK iterator message.
    Message {
        payload: Value,
    },
    /// The first append for a subagent transcript subpath.
    SubagentTranscript {
        native_session_id: String,
        subpath: String,
    },
    /// A store append failed; the failure was answered to the harness so the
    /// SDK can emit `mirror_error`, and the connection continues.
    StoreAppendFailed {
        scope: String,
        error: String,
    },
    PermissionRequested(PendingPermission),
    /// The fail-closed gate answered a permission with deny before it
    /// reached the consumer.
    PermissionDenied {
        request: Value,
        reason: String,
    },
    Disconnected {
        epoch: u64,
        reason: String,
    },
    /// Post-reconnect reconciliation converged canonical state.
    Reconciled {
        epoch: u64,
        report: Vec<ReconcileEntry>,
    },
    Closed {
        reason: String,
    },
}

struct Shared {
    config: ClaudeHostConfig,
    store: ClaudeSessionStore,
    /// `None` when the connected harness reported the pinned SDK; otherwise
    /// the safe-mode reason for this connection.
    sdk_refusal: Mutex<Option<String>>,
    authority: Mutex<WriteAuthority>,
    next_id: AtomicI64,
    pending: Mutex<HashMap<i64, oneshot::Sender<Result<Value, HostError>>>>,
    outbound: Mutex<Option<mpsc::Sender<String>>>,
    hello: Mutex<Option<oneshot::Sender<HelloFrame>>>,
    child: Mutex<Option<Child>>,
    io_abort: Mutex<Option<AbortHandle>>,
    closed: AtomicBool,
    seen_subpaths: Mutex<BTreeSet<(String, String)>>,
}

pub struct ClaudeHost {
    shared: Arc<Shared>,
    supervisor: JoinHandle<()>,
}

impl ClaudeHost {
    /// Establish the first connection and complete the `hello` handshake
    /// before returning; connection failures surface here rather than as
    /// events.
    pub async fn connect(
        connector: Arc<dyn HarnessConnector>,
        config: ClaudeHostConfig,
        store: ClaudeSessionStore,
    ) -> Result<(Self, mpsc::Receiver<ClaudeHostEvent>), HostError> {
        let (events_tx, events_rx) = mpsc::channel(config.event_buffer.max(1));
        let shared = Arc::new(Shared {
            config,
            store,
            sdk_refusal: Mutex::new(Some("the harness has not completed hello".to_string())),
            authority: Mutex::new(WriteAuthority::ReadOnly),
            next_id: AtomicI64::new(1),
            pending: Mutex::new(HashMap::new()),
            outbound: Mutex::new(None),
            hello: Mutex::new(None),
            child: Mutex::new(None),
            io_abort: Mutex::new(None),
            closed: AtomicBool::new(false),
            seen_subpaths: Mutex::new(BTreeSet::new()),
        });
        let connection = connector.connect().await?;
        // Arm the hello receiver before the io task starts so a fast harness
        // cannot race its hello past the handshake.
        let hello_rx = arm_hello(&shared);
        let io_task = start_connection(connection, &shared, events_tx.clone());
        let hello = match complete_handshake(&shared, hello_rx).await {
            Ok(hello) => hello,
            Err(error) => {
                cleanup_connection(&shared, &error);
                io_task.abort();
                return Err(error);
            }
        };
        let safe_mode_reason = { shared.sdk_refusal.lock().expect("refusal lock").clone() };
        let _ = events_tx
            .send(ClaudeHostEvent::Connected {
                epoch: 1,
                sdk_version: hello.sdk_version.clone(),
                safe_mode_reason,
            })
            .await;
        let supervisor = tokio::spawn(supervise(connector, shared.clone(), events_tx, io_task, 1));
        Ok((Self { shared, supervisor }, events_rx))
    }

    /// `None` when the connected harness reported the pinned SDK version.
    pub fn safe_mode_reason(&self) -> Option<String> {
        self.shared
            .sdk_refusal
            .lock()
            .expect("refusal lock")
            .clone()
    }

    pub fn authority(&self) -> WriteAuthority {
        *self.shared.authority.lock().expect("authority lock")
    }

    pub fn is_connected(&self) -> bool {
        self.shared
            .outbound
            .lock()
            .expect("outbound lock")
            .is_some()
    }

    pub fn store(&self) -> &ClaudeSessionStore {
        &self.shared.store
    }

    /// Enter write mode. Fails closed on: an unpinned SDK version, a receipt
    /// naming another platform or version, an expired receipt, or verifier
    /// rejection — the host stays read-only in every failure case.
    pub async fn enable_write<V: AdmissionVerifier>(
        &self,
        receipt: &AdmissionReceipt,
        verifier: &V,
    ) -> Result<(), HostError> {
        if let Some(reason) = self.safe_mode_reason() {
            return Err(HostError::SafeMode {
                operation: "enable_write".to_string(),
                reason,
            });
        }
        if receipt.platform != CLAUDE_PLATFORM_ID {
            return Err(HostError::ReceiptMismatch(format!(
                "receipt platform {} is not {CLAUDE_PLATFORM_ID}",
                receipt.platform
            )));
        }
        if receipt.client_version != PINNED_CLAUDE_CODE_VERSION {
            return Err(HostError::ReceiptMismatch(format!(
                "receipt version {} is not the pinned {PINNED_CLAUDE_CODE_VERSION}",
                receipt.client_version
            )));
        }
        if receipt.expires_at_unix_ms <= now_unix_ms() {
            return Err(HostError::AdmissionExpired);
        }
        verifier.verify(receipt).await?;
        *self.shared.authority.lock().expect("authority lock") = WriteAuthority::Admitted {
            expires_at_unix_ms: receipt.expires_at_unix_ms,
        };
        Ok(())
    }

    /// Drop back to read-only safe mode.
    pub fn disable_write(&self) {
        *self.shared.authority.lock().expect("authority lock") = WriteAuthority::ReadOnly;
    }

    /// Start a new SDK session; the harness answers with the session id the
    /// SDK created.
    pub async fn session_start(
        &self,
        project_key: &str,
        prompt: &str,
        options: Value,
    ) -> Result<SessionStartOutcome, HostError> {
        require_write(&self.shared, "session.start")?;
        let params = json!({
            "projectKey": project_key,
            "prompt": prompt,
            "options": options,
        });
        let result = request(&self.shared, "session.start", params).await?;
        session_outcome("session.start", &result)
    }

    /// Resume an existing session from the store.
    pub async fn session_resume(
        &self,
        key: &SessionKey,
        prompt: &str,
        options: Value,
    ) -> Result<SessionStartOutcome, HostError> {
        require_write(&self.shared, "session.resume")?;
        let params = json!({
            "projectKey": key.project_key,
            "sessionId": key.session_id,
            "prompt": prompt,
            "options": options,
        });
        let result = request(&self.shared, "session.resume", params).await?;
        session_outcome("session.resume", &result)
    }

    /// Interrupt the in-flight session. Halting activity is permitted in
    /// every mode.
    pub async fn session_interrupt(&self) -> Result<(), HostError> {
        let _ = request(&self.shared, "session.interrupt", Value::Null).await?;
        Ok(())
    }

    /// Fork one stored transcript daemon-side, mirroring `forkSession`
    /// semantics against canonical state.
    pub async fn fork_session(
        &self,
        source: &SessionKey,
        fork_session_id: &str,
    ) -> Result<crate::store::ForkOutcome, HostError> {
        require_write(&self.shared, "session.fork")?;
        let store = self.shared.store.clone();
        let source = source.clone();
        let fork_session_id = fork_session_id.to_string();
        run_store_task(move || store.fork(&source, &fork_session_id)).await
    }

    /// Reconcile canonical state on demand; the supervisor also runs this
    /// after every reconnect.
    pub async fn reconcile(&self) -> Result<Vec<ReconcileEntry>, HostError> {
        let store = self.shared.store.clone();
        run_store_task(move || store.reconcile()).await
    }

    /// Close the connection, fail pending requests, and end the event stream.
    pub async fn shutdown(self) {
        self.shared.closed.store(true, Ordering::SeqCst);
        cleanup_connection(&self.shared, &HostError::Closed);
        if let Some(io_abort) = self.shared.io_abort.lock().expect("io abort lock").take() {
            io_abort.abort();
        }
        self.supervisor.abort();
        let _ = self.supervisor.await;
    }
}

async fn run_store_task<T, F>(task: F) -> Result<T, HostError>
where
    T: Send + 'static,
    F: FnOnce() -> anyhow::Result<T> + Send + 'static,
{
    tokio::task::spawn_blocking(task)
        .await
        .map_err(|error| HostError::StoreTask(format!("store task join: {error}")))?
        .map_err(|error| HostError::StoreTask(format!("{error:#}")))
}

fn session_outcome(operation: &str, result: &Value) -> Result<SessionStartOutcome, HostError> {
    let native_session_id = result
        .get("sessionId")
        .and_then(Value::as_str)
        .ok_or_else(|| HostError::Protocol(format!("{operation} response is missing sessionId")))?;
    Ok(SessionStartOutcome {
        native_session_id: native_session_id.to_string(),
    })
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or_default()
}

/// The mutating gate: pinned SDK version and an unexpired admission, checked
/// on every call so an expired admission demotes the host instead of letting
/// one stale grant keep writing.
fn require_write(shared: &Shared, operation: &str) -> Result<(), HostError> {
    if let Some(reason) = shared.sdk_refusal.lock().expect("refusal lock").clone() {
        return Err(HostError::SafeMode {
            operation: operation.to_string(),
            reason,
        });
    }
    let mut authority = shared.authority.lock().expect("authority lock");
    match *authority {
        WriteAuthority::ReadOnly => Err(HostError::SafeMode {
            operation: operation.to_string(),
            reason: "mutations require a verified admission receipt".to_string(),
        }),
        WriteAuthority::Admitted { expires_at_unix_ms } => {
            if expires_at_unix_ms <= now_unix_ms() {
                *authority = WriteAuthority::ReadOnly;
                return Err(HostError::AdmissionExpired);
            }
            Ok(())
        }
    }
}

fn outbound_sender(shared: &Shared) -> Result<mpsc::Sender<String>, HostError> {
    shared
        .outbound
        .lock()
        .expect("outbound lock")
        .clone()
        .ok_or(HostError::NotConnected)
}

async fn send_line(shared: &Shared, line: String) -> Result<(), HostError> {
    outbound_sender(shared)?
        .send(line)
        .await
        .map_err(|_| HostError::NotConnected)
}

async fn request(shared: &Arc<Shared>, op: &str, params: Value) -> Result<Value, HostError> {
    if shared.closed.load(Ordering::SeqCst) {
        return Err(HostError::Closed);
    }
    let outbound = outbound_sender(shared)?;
    let id = shared.next_id.fetch_add(1, Ordering::SeqCst);
    let (sender, receiver) = oneshot::channel();
    shared
        .pending
        .lock()
        .expect("pending lock")
        .insert(id, sender);
    let line = encode_request(id, op, &params);
    if outbound.send(line).await.is_err() {
        shared.pending.lock().expect("pending lock").remove(&id);
        return Err(HostError::NotConnected);
    }
    match timeout(shared.config.request_timeout, receiver).await {
        Err(_) => {
            shared.pending.lock().expect("pending lock").remove(&id);
            Err(HostError::Timeout {
                operation: op.to_string(),
            })
        }
        Ok(Err(_)) => Err(HostError::ConnectionLost),
        Ok(Ok(result)) => result,
    }
}

/// Register the oneshot the io task will route the next `hello` frame to.
fn arm_hello(shared: &Shared) -> oneshot::Receiver<HelloFrame> {
    let (sender, receiver) = oneshot::channel();
    *shared.hello.lock().expect("hello lock") = Some(sender);
    receiver
}

/// Wait for the harness `hello`, validate it, record the safe-mode outcome,
/// and acknowledge.
async fn complete_handshake(
    shared: &Arc<Shared>,
    receiver: oneshot::Receiver<HelloFrame>,
) -> Result<HelloFrame, HostError> {
    let hello = match timeout(shared.config.hello_timeout, receiver).await {
        Err(_) => {
            return Err(HostError::Timeout {
                operation: "hello".to_string(),
            });
        }
        Ok(Err(_)) => return Err(HostError::ConnectionLost),
        Ok(Ok(hello)) => hello,
    };
    if hello.protocol != HOST_PROTOCOL_VERSION {
        return Err(HostError::Protocol(format!(
            "harness protocol {} is not {HOST_PROTOCOL_VERSION}",
            hello.protocol
        )));
    }
    let refusal = if hello.sdk_package.as_deref() != Some(&shared.config.expected_sdk_package) {
        Some(format!(
            "the harness loaded {:?} instead of {}",
            hello.sdk_package, shared.config.expected_sdk_package
        ))
    } else {
        sdk_version_refusal(hello.sdk_version.as_deref())
    };
    *shared.sdk_refusal.lock().expect("refusal lock") = refusal.clone();
    let mode = if refusal.is_some() {
        "read_only"
    } else {
        "pinned_read_only"
    };
    send_line(shared, encode_hello_ack(mode, refusal.as_deref())).await?;
    Ok(hello)
}

fn start_connection(
    connection: HarnessConnection,
    shared: &Arc<Shared>,
    events: mpsc::Sender<ClaudeHostEvent>,
) -> JoinHandle<()> {
    let (outbound_tx, outbound_rx) = mpsc::channel(64);
    *shared.outbound.lock().expect("outbound lock") = Some(outbound_tx);
    *shared.child.lock().expect("child lock") = connection.child;
    let task = tokio::spawn(run_connection(
        connection.reader,
        connection.writer,
        outbound_rx,
        shared.clone(),
        events,
    ));
    *shared.io_abort.lock().expect("io abort lock") = Some(task.abort_handle());
    task
}

fn cleanup_connection(shared: &Shared, error: &HostError) {
    *shared.outbound.lock().expect("outbound lock") = None;
    *shared.hello.lock().expect("hello lock") = None;
    if let Some(mut child) = shared.child.lock().expect("child lock").take() {
        let _ = child.start_kill();
    }
    let drained: Vec<_> = {
        let mut pending = shared.pending.lock().expect("pending lock");
        pending.drain().map(|(_, sender)| sender).collect()
    };
    for sender in drained {
        let _ = sender.send(Err(error.clone()));
    }
}

async fn supervise(
    connector: Arc<dyn HarnessConnector>,
    shared: Arc<Shared>,
    events: mpsc::Sender<ClaudeHostEvent>,
    mut io_task: JoinHandle<()>,
    mut epoch: u64,
) {
    'lifecycle: loop {
        let _ = (&mut io_task).await;
        cleanup_connection(&shared, &HostError::ConnectionLost);
        if shared.closed.load(Ordering::SeqCst) {
            let _ = events
                .send(ClaudeHostEvent::Closed {
                    reason: "shutdown".to_string(),
                })
                .await;
            return;
        }
        let _ = events
            .send(ClaudeHostEvent::Disconnected {
                epoch,
                reason: "the harness connection ended".to_string(),
            })
            .await;
        let policy = shared.config.reconnect.clone();
        let mut attempt = 0u32;
        let mut delay = policy.initial_delay;
        loop {
            if attempt >= policy.max_attempts {
                shared.closed.store(true, Ordering::SeqCst);
                cleanup_connection(&shared, &HostError::Closed);
                let _ = events
                    .send(ClaudeHostEvent::Closed {
                        reason: format!("reconnect attempts exhausted after {attempt} tries"),
                    })
                    .await;
                return;
            }
            attempt += 1;
            sleep(delay).await;
            delay = (delay * 2).min(policy.max_delay);
            if shared.closed.load(Ordering::SeqCst) {
                let _ = events
                    .send(ClaudeHostEvent::Closed {
                        reason: "shutdown".to_string(),
                    })
                    .await;
                return;
            }
            let connection = match connector.connect().await {
                Ok(connection) => connection,
                Err(error) => {
                    tracing::debug!(
                        target: "soleaux_adapter_claude",
                        attempt,
                        error = %error,
                        "reconnect attempt failed"
                    );
                    continue;
                }
            };
            epoch += 1;
            let hello_rx = arm_hello(&shared);
            io_task = start_connection(connection, &shared, events.clone());
            match complete_handshake(&shared, hello_rx).await {
                Ok(hello) => {
                    let safe_mode_reason =
                        { shared.sdk_refusal.lock().expect("refusal lock").clone() };
                    let _ = events
                        .send(ClaudeHostEvent::Connected {
                            epoch,
                            sdk_version: hello.sdk_version.clone(),
                            safe_mode_reason,
                        })
                        .await;
                    let store = shared.store.clone();
                    match run_store_task(move || store.reconcile()).await {
                        Ok(report) => {
                            let _ = events
                                .send(ClaudeHostEvent::Reconciled { epoch, report })
                                .await;
                        }
                        Err(error) => {
                            tracing::warn!(
                                target: "soleaux_adapter_claude",
                                error = %error,
                                "post-reconnect reconciliation failed"
                            );
                        }
                    }
                    continue 'lifecycle;
                }
                Err(error) => {
                    tracing::debug!(
                        target: "soleaux_adapter_claude",
                        attempt,
                        error = %error,
                        "reconnect handshake failed"
                    );
                    cleanup_connection(&shared, &error);
                    io_task.abort();
                    continue;
                }
            }
        }
    }
}

async fn run_connection(
    reader: Box<dyn AsyncRead + Send + Unpin>,
    mut writer: Box<dyn AsyncWrite + Send + Unpin>,
    mut outbound_rx: mpsc::Receiver<String>,
    shared: Arc<Shared>,
    events: mpsc::Sender<ClaudeHostEvent>,
) {
    let writer_task = tokio::spawn(async move {
        while let Some(line) = outbound_rx.recv().await {
            if writer.write_all(line.as_bytes()).await.is_err()
                || writer.write_all(b"\n").await.is_err()
                || writer.flush().await.is_err()
            {
                break;
            }
        }
    });
    let mut reader = BufReader::new(reader);
    let mut buffer = Vec::new();
    let max = shared.config.max_frame_bytes;
    loop {
        match read_frame(&mut reader, max, &mut buffer).await {
            Ok(true) => {
                if buffer.is_empty() {
                    continue;
                }
                let Ok(text) = std::str::from_utf8(&buffer) else {
                    tracing::warn!(
                        target: "soleaux_adapter_claude",
                        "closing connection: frame is not UTF-8"
                    );
                    break;
                };
                match parse_harness_frame(text) {
                    Ok(frame) => dispatch(frame, &shared, &events).await,
                    Err(detail) => {
                        tracing::warn!(
                            target: "soleaux_adapter_claude",
                            %detail,
                            "closing connection on malformed frame"
                        );
                        break;
                    }
                }
            }
            Ok(false) => break,
            Err(detail) => {
                tracing::warn!(
                    target: "soleaux_adapter_claude",
                    %detail,
                    "closing connection"
                );
                break;
            }
        }
    }
    writer_task.abort();
}

/// Read one newline-delimited frame into `buffer`, enforcing the frame bound.
/// Returns `Ok(false)` on clean EOF at a frame boundary.
async fn read_frame<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    max_bytes: usize,
    buffer: &mut Vec<u8>,
) -> Result<bool, String> {
    buffer.clear();
    loop {
        let chunk = reader
            .fill_buf()
            .await
            .map_err(|error| format!("reading frame: {error}"))?;
        if chunk.is_empty() {
            return if buffer.is_empty() {
                Ok(false)
            } else {
                Err("connection ended mid-frame".to_string())
            };
        }
        if let Some(position) = chunk.iter().position(|byte| *byte == b'\n') {
            buffer.extend_from_slice(&chunk[..position]);
            reader.consume(position + 1);
            if buffer.last() == Some(&b'\r') {
                buffer.pop();
            }
            if buffer.len() > max_bytes {
                return Err(format!("frame exceeds the {max_bytes}-byte bound"));
            }
            return Ok(true);
        }
        let length = chunk.len();
        buffer.extend_from_slice(chunk);
        reader.consume(length);
        if buffer.len() > max_bytes {
            return Err(format!("frame exceeds the {max_bytes}-byte bound"));
        }
    }
}

async fn dispatch(
    frame: HarnessFrame,
    shared: &Arc<Shared>,
    events: &mpsc::Sender<ClaudeHostEvent>,
) {
    match frame {
        HarnessFrame::Hello(hello) => {
            let sender = shared.hello.lock().expect("hello lock").take();
            match sender {
                Some(sender) => {
                    let _ = sender.send(hello);
                }
                None => {
                    tracing::warn!(
                        target: "soleaux_adapter_claude",
                        "ignoring hello outside a handshake"
                    );
                }
            }
        }
        HarnessFrame::Store(store_request) => {
            // Served inline in frame order: appends must not reorder.
            handle_store(store_request, shared, events).await;
        }
        HarnessFrame::Event(event) => {
            handle_event(event, events).await;
        }
        HarnessFrame::PermissionRequest(permission) => {
            handle_permission(permission, shared, events).await;
        }
        HarnessFrame::Response(response) => {
            let sender = shared
                .pending
                .lock()
                .expect("pending lock")
                .remove(&response.id);
            if let Some(sender) = sender {
                let outcome = if response.ok {
                    Ok(response.result)
                } else {
                    Err(HostError::Harness {
                        operation: format!("request {}", response.id),
                        message: response
                            .error
                            .unwrap_or_else(|| "unspecified harness error".to_string()),
                    })
                };
                let _ = sender.send(outcome);
            }
        }
    }
}

async fn handle_store(
    store_request: StoreRequestFrame,
    shared: &Arc<Shared>,
    events: &mpsc::Sender<ClaudeHostEvent>,
) {
    let id = store_request.id;
    let outcome: Result<Value, String> = match store_request.op {
        StoreOp::Append { key, entries } => {
            let store = shared.store.clone();
            let task_key = key.clone();
            let result = run_store_task(move || {
                store
                    .append(&task_key, &entries)
                    .and_then(|outcome| serde_json::to_value(&outcome).map_err(Into::into))
            })
            .await;
            match result {
                Ok(outcome) => {
                    if let Some(subpath) = &key.subpath {
                        let marker = (key.native_session_id(), subpath.clone());
                        let newly_seen = shared
                            .seen_subpaths
                            .lock()
                            .expect("subpath lock")
                            .insert(marker);
                        if newly_seen {
                            let _ = events
                                .send(ClaudeHostEvent::SubagentTranscript {
                                    native_session_id: key.native_session_id(),
                                    subpath: subpath.clone(),
                                })
                                .await;
                        }
                    }
                    Ok(outcome)
                }
                Err(error) => {
                    // The mirror contract: answer the failure, log, and
                    // continue — the SDK retries, then emits `mirror_error`
                    // and keeps the query running.
                    let scope = transcript_scope(&key);
                    let detail = error.to_string();
                    tracing::warn!(
                        target: "soleaux_adapter_claude",
                        %scope,
                        error = %detail,
                        "store append failed; answering mirror failure and continuing"
                    );
                    let _ = events
                        .send(ClaudeHostEvent::StoreAppendFailed {
                            scope,
                            error: detail.clone(),
                        })
                        .await;
                    Err(detail)
                }
            }
        }
        StoreOp::Load { key } => {
            let store = shared.store.clone();
            run_store_task(move || {
                store.load(&key).map(|entries| match entries {
                    Some(entries) => Value::Array(entries),
                    None => Value::Null,
                })
            })
            .await
            .map_err(|error| error.to_string())
        }
        StoreOp::ListSessions { project_key } => {
            let store = shared.store.clone();
            run_store_task(move || {
                store.list_sessions(&project_key).map(|summaries| {
                    Value::Array(
                        summaries
                            .into_iter()
                            .map(|summary| {
                                json!({
                                    "sessionId": summary.session_id,
                                    "mtime": summary.mtime_unix_ms,
                                })
                            })
                            .collect(),
                    )
                })
            })
            .await
            .map_err(|error| error.to_string())
        }
        StoreOp::ListSubkeys {
            project_key,
            session_id,
        } => {
            let store = shared.store.clone();
            run_store_task(move || {
                store
                    .list_subkeys(&project_key, &session_id)
                    .and_then(|subkeys| serde_json::to_value(subkeys).map_err(Into::into))
            })
            .await
            .map_err(|error| error.to_string())
        }
        StoreOp::Delete { key } => {
            // The SDK never deletes from the store on its own; the daemon
            // owns retention, so a delete request is refused rather than
            // honored.
            Err(format!(
                "the daemon owns retention for {}; delete is refused",
                transcript_scope(&key)
            ))
        }
    };
    let line = match &outcome {
        Ok(result) => encode_store_result(id, Ok(result.clone())),
        Err(error) => encode_store_result(id, Err(error.as_str())),
    };
    if send_line(shared, line).await.is_err() {
        tracing::debug!(
            target: "soleaux_adapter_claude",
            "store result could not be delivered; connection is gone"
        );
    }
}

async fn handle_event(event: EventFrame, events: &mpsc::Sender<ClaudeHostEvent>) {
    let mapped = match event.event.as_str() {
        "hook" => ClaudeHostEvent::Hook {
            name: event.hook.unwrap_or_else(|| "unknown".to_string()),
            payload: event.payload,
        },
        "system" => ClaudeHostEvent::System {
            payload: event.payload,
        },
        "message" => ClaudeHostEvent::Message {
            payload: event.payload,
        },
        other => {
            tracing::debug!(
                target: "soleaux_adapter_claude",
                event = %other,
                "forwarding unrecognized harness event as a message"
            );
            ClaudeHostEvent::Message {
                payload: event.payload,
            }
        }
    };
    let _ = events.send(mapped).await;
}

async fn handle_permission(
    permission: PermissionRequestFrame,
    shared: &Arc<Shared>,
    events: &mpsc::Sender<ClaudeHostEvent>,
) {
    let id = permission.id;
    if let Err(error) = require_write(shared, "permission") {
        let reason = error.to_string();
        let decision = PermissionDecision::fail_closed(&reason);
        let _ = send_line(shared, encode_permission_decision(id, &decision.to_wire())).await;
        let _ = events
            .send(ClaudeHostEvent::PermissionDenied {
                request: permission.request,
                reason,
            })
            .await;
        return;
    }
    let (sender, receiver) = oneshot::channel();
    let pending = PendingPermission {
        request: permission.request,
        responder: sender,
    };
    let waiter_shared = shared.clone();
    tokio::spawn(async move {
        let decision = match timeout(waiter_shared.config.permission_timeout, receiver).await {
            Ok(Ok(decision)) => decision,
            _ => PermissionDecision::fail_closed("no decision arrived before the timeout"),
        };
        let _ = send_line(
            &waiter_shared,
            encode_permission_decision(id, &decision.to_wire()),
        )
        .await;
    });
    let _ = events
        .send(ClaudeHostEvent::PermissionRequested(pending))
        .await;
}
