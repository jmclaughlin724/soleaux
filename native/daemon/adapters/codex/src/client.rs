//! Supervised JSONL stdio client for the Codex app-server.
//!
//! One supervisor task owns the connection lifecycle: connect, `initialize`
//! handshake, dispatch, and bounded-backoff reconnect with automatic
//! `thread/resume` of subscribed threads. Approvals arrive as
//! [`PendingApproval`] events; a dropped or timed-out approval is answered
//! with the fail-closed `cancel` decision, and every mutating method is
//! refused locally while the adapter is in safe mode.

use crate::{
    cursors::{CodexCursorStore, CursorUpdate, THREAD_LIST_SCOPE, thread_scope},
    protocol::{
        ApprovalDecision, ClientInfo, CodexNotification, CodexServerRequest, IncomingMessage,
        InitializeCapabilities, InitializeParams, InitializeResponse, METHOD_INITIALIZE,
        METHOD_INITIALIZED, METHOD_THREAD_ARCHIVE, METHOD_THREAD_COMPACT_START, METHOD_THREAD_FORK,
        METHOD_THREAD_LIST, METHOD_THREAD_READ, METHOD_THREAD_RESUME, METHOD_THREAD_START,
        METHOD_TURN_INTERRUPT, METHOD_TURN_START, METHOD_TURN_STEER, RequestId, ThreadForkParams,
        ThreadIdParams, ThreadListParams, ThreadListResponse, ThreadReadParams, ThreadResponse,
        ThreadResumeParams, ThreadStartParams, TurnInterruptParams, TurnStartParams,
        TurnStartResponse, TurnSteerParams, TurnSteerResponse, UNSUPPORTED_SERVER_REQUEST_CODE,
        approval_response_body, encode_error_response, encode_notification, encode_request,
        encode_response, method_is_read_only, parse_incoming,
    },
    version::{AdapterMode, PINNED_CODEX_VERSION},
};
use serde::de::DeserializeOwned;
use serde_json::Value;
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
    time::Duration,
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
pub enum CodexClientError {
    /// A mutating method was refused because the adapter is in safe mode.
    SafeMode {
        method: String,
        reason: String,
    },
    NotConnected,
    ConnectionLost,
    Closed,
    Timeout {
        method: String,
    },
    Rpc {
        code: i64,
        message: String,
    },
    Protocol(String),
    Spawn(String),
    /// The live server contradicted the probed version; the client has
    /// downgraded itself to safe mode.
    VersionDrift {
        expected: String,
        reported: String,
    },
}

impl std::fmt::Display for CodexClientError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::SafeMode { method, reason } => {
                write!(formatter, "safe mode refused {method}: {reason}")
            }
            Self::NotConnected => write!(formatter, "the Codex app-server is not connected"),
            Self::ConnectionLost => {
                write!(formatter, "the Codex app-server connection was lost")
            }
            Self::Closed => write!(formatter, "the Codex client is closed"),
            Self::Timeout { method } => write!(formatter, "{method} timed out"),
            Self::Rpc { code, message } => {
                write!(formatter, "app-server error {code}: {message}")
            }
            Self::Protocol(detail) => write!(formatter, "protocol violation: {detail}"),
            Self::Spawn(detail) => write!(formatter, "spawn failure: {detail}"),
            Self::VersionDrift { expected, reported } => write!(
                formatter,
                "app-server reported version {reported} instead of pinned {expected}; the client downgraded to safe mode"
            ),
        }
    }
}

impl std::error::Error for CodexClientError {}

/// One live transport to a Codex app-server: the process form for production
/// and any duplex byte stream for tests.
pub struct CodexConnection {
    pub reader: Box<dyn AsyncRead + Send + Unpin>,
    pub writer: Box<dyn AsyncWrite + Send + Unpin>,
    pub child: Option<Child>,
}

pub trait CodexConnector: Send + Sync + 'static {
    fn connect(&self) -> BoxFuture<'_, Result<CodexConnection, CodexClientError>>;
}

/// Spawns `codex app-server` over piped stdio with a cleared, allow-listed
/// environment.
#[derive(Debug, Clone)]
pub struct ProcessConnector {
    pub binary: PathBuf,
    pub arguments: Vec<String>,
    pub current_dir: Option<PathBuf>,
    pub extra_env: Vec<(String, String)>,
}

impl ProcessConnector {
    pub fn new(binary: impl Into<PathBuf>) -> Self {
        Self {
            binary: binary.into(),
            arguments: vec!["app-server".to_string()],
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
    "CODEX_HOME",
];

impl CodexConnector for ProcessConnector {
    fn connect(&self) -> BoxFuture<'_, Result<CodexConnection, CodexClientError>> {
        Box::pin(async move {
            let mut command = Command::new(&self.binary);
            command
                .args(&self.arguments)
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
                CodexClientError::Spawn(format!("starting {}: {error}", self.binary.display()))
            })?;
            let stdin = child.stdin.take().ok_or_else(|| {
                CodexClientError::Spawn("app-server did not expose stdin".to_string())
            })?;
            let stdout = child.stdout.take().ok_or_else(|| {
                CodexClientError::Spawn("app-server did not expose stdout".to_string())
            })?;
            if let Some(stderr) = child.stderr.take() {
                tokio::spawn(async move {
                    let mut lines = BufReader::new(stderr).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        tracing::debug!(target: "soleaux_adapter_codex", stderr = %line);
                    }
                });
            }
            Ok(CodexConnection {
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
pub struct CodexClientConfig {
    pub client_info: ClientInfo,
    pub initialize_capabilities: Option<InitializeCapabilities>,
    /// Version reported by the pinned-binary probe; `None` means unprobed.
    pub probed_version: Option<String>,
    /// SHA-256 digest of the capability probe evidence backing mutating mode.
    pub probe_evidence_sha256: Option<String>,
    pub request_timeout: Duration,
    pub approval_timeout: Duration,
    pub max_frame_bytes: usize,
    pub event_buffer: usize,
    pub reconnect: ReconnectPolicy,
}

impl Default for CodexClientConfig {
    fn default() -> Self {
        Self {
            client_info: ClientInfo {
                name: "soleaux".to_string(),
                title: Some("Soleaux Codex adapter".to_string()),
                version: env!("CARGO_PKG_VERSION").to_string(),
            },
            initialize_capabilities: None,
            probed_version: None,
            probe_evidence_sha256: None,
            request_timeout: Duration::from_secs(30),
            approval_timeout: Duration::from_secs(120),
            max_frame_bytes: 4 * 1024 * 1024,
            event_buffer: 256,
            reconnect: ReconnectPolicy::default(),
        }
    }
}

/// One approval awaiting a decision. Dropping the handle without responding,
/// or letting the approval timeout elapse, answers `cancel`.
#[derive(Debug)]
pub struct PendingApproval {
    request: CodexServerRequest,
    responder: oneshot::Sender<ApprovalDecision>,
}

impl PendingApproval {
    pub fn request(&self) -> &CodexServerRequest {
        &self.request
    }

    pub fn respond(self, decision: ApprovalDecision) -> Result<(), CodexClientError> {
        self.responder
            .send(decision)
            .map_err(|_| CodexClientError::ConnectionLost)
    }
}

#[derive(Debug)]
pub enum CodexEvent {
    /// The handshake completed; `epoch` starts at 1 and increments per
    /// reconnect.
    Connected {
        epoch: u64,
        initialize: InitializeResponse,
    },
    Notification(CodexNotification),
    ApprovalRequested(PendingApproval),
    /// Safe mode answered an approval with `cancel` before it reached the
    /// consumer.
    ApprovalDenied {
        request: CodexServerRequest,
        reason: String,
    },
    UnsupportedServerRequest {
        method: String,
    },
    Disconnected {
        epoch: u64,
        reason: String,
    },
    ThreadResumed {
        epoch: u64,
        thread_id: String,
    },
    ThreadResumeFailed {
        epoch: u64,
        thread_id: String,
        error: String,
    },
    Closed {
        reason: String,
    },
}

struct Shared {
    config: CodexClientConfig,
    mode: Mutex<AdapterMode>,
    next_id: AtomicI64,
    pending: Mutex<HashMap<i64, oneshot::Sender<Result<Value, CodexClientError>>>>,
    outbound: Mutex<Option<mpsc::Sender<String>>>,
    subscribed: Mutex<BTreeSet<String>>,
    child: Mutex<Option<Child>>,
    io_abort: Mutex<Option<AbortHandle>>,
    closed: AtomicBool,
    cursors: Option<CodexCursorStore>,
}

pub struct CodexClient {
    shared: Arc<Shared>,
    supervisor: JoinHandle<()>,
}

impl CodexClient {
    /// Establish the first connection and complete the handshake before
    /// returning; connection failures surface here rather than as events.
    pub async fn connect(
        connector: Arc<dyn CodexConnector>,
        config: CodexClientConfig,
        cursors: Option<CodexCursorStore>,
    ) -> Result<(Self, mpsc::Receiver<CodexEvent>), CodexClientError> {
        let mode = crate::version::evaluate_adapter_mode(
            config.probed_version.as_deref(),
            config.probe_evidence_sha256.as_deref(),
        );
        let (events_tx, events_rx) = mpsc::channel(config.event_buffer.max(1));
        let shared = Arc::new(Shared {
            config,
            mode: Mutex::new(mode),
            next_id: AtomicI64::new(1),
            pending: Mutex::new(HashMap::new()),
            outbound: Mutex::new(None),
            subscribed: Mutex::new(BTreeSet::new()),
            child: Mutex::new(None),
            io_abort: Mutex::new(None),
            closed: AtomicBool::new(false),
            cursors,
        });
        let connection = connector.connect().await?;
        let io_task = start_connection(connection, &shared, events_tx.clone());
        let initialize = match handshake(&shared).await {
            Ok(initialize) => initialize,
            Err(error) => {
                cleanup_connection(&shared, &error);
                io_task.abort();
                return Err(error);
            }
        };
        let _ = events_tx
            .send(CodexEvent::Connected {
                epoch: 1,
                initialize,
            })
            .await;
        let supervisor = tokio::spawn(supervise(connector, shared.clone(), events_tx, io_task, 1));
        Ok((Self { shared, supervisor }, events_rx))
    }

    pub fn mode(&self) -> AdapterMode {
        self.shared.mode.lock().expect("mode lock").clone()
    }

    pub fn is_connected(&self) -> bool {
        self.shared
            .outbound
            .lock()
            .expect("outbound lock")
            .is_some()
    }

    /// Threads the supervisor will `thread/resume` after a reconnect.
    pub fn subscribed_threads(&self) -> Vec<String> {
        self.shared
            .subscribed
            .lock()
            .expect("subscribed lock")
            .iter()
            .cloned()
            .collect()
    }

    /// The last durably recorded cursor for one thread, if cursors are wired.
    pub async fn thread_cursor(
        &self,
        thread_id: &str,
    ) -> Result<Option<soleaux_state::AdapterCursorRecord>, CodexClientError> {
        let Some(cursors) = self.shared.cursors.clone() else {
            return Ok(None);
        };
        let scope = thread_scope(thread_id);
        tokio::task::spawn_blocking(move || cursors.get(&scope))
            .await
            .map_err(|error| CodexClientError::Protocol(format!("cursor read task: {error}")))?
            .map_err(|error| CodexClientError::Protocol(format!("cursor read: {error:#}")))
    }

    pub async fn thread_start(
        &self,
        params: ThreadStartParams,
    ) -> Result<ThreadResponse, CodexClientError> {
        let response: ThreadResponse = self.call(METHOD_THREAD_START, ser(&params)?).await?;
        self.admit_created_thread(&response)?;
        Ok(response)
    }

    pub async fn thread_resume(
        &self,
        params: ThreadResumeParams,
    ) -> Result<ThreadResponse, CodexClientError> {
        let response: ThreadResponse = self.call(METHOD_THREAD_RESUME, ser(&params)?).await?;
        self.subscribe(&response.thread.id);
        Ok(response)
    }

    pub async fn thread_fork(
        &self,
        params: ThreadForkParams,
    ) -> Result<ThreadResponse, CodexClientError> {
        let response: ThreadResponse = self.call(METHOD_THREAD_FORK, ser(&params)?).await?;
        self.admit_created_thread(&response)?;
        Ok(response)
    }

    pub async fn thread_list(
        &self,
        params: ThreadListParams,
    ) -> Result<ThreadListResponse, CodexClientError> {
        let response: ThreadListResponse = self.call(METHOD_THREAD_LIST, ser(&params)?).await?;
        if let (Some(cursors), Some(next_cursor)) =
            (self.shared.cursors.clone(), response.next_cursor.clone())
        {
            let update = CursorUpdate {
                scope: THREAD_LIST_SCOPE.to_string(),
                cursor: next_cursor,
                watermark: None,
                metadata: Value::Null,
            };
            tokio::task::spawn_blocking(move || cursors.advance(&update))
                .await
                .map_err(|error| CodexClientError::Protocol(format!("cursor write task: {error}")))?
                .map_err(|error| CodexClientError::Protocol(format!("cursor write: {error:#}")))?;
        }
        Ok(response)
    }

    pub async fn thread_read(
        &self,
        params: ThreadReadParams,
    ) -> Result<ThreadResponse, CodexClientError> {
        self.call(METHOD_THREAD_READ, ser(&params)?).await
    }

    pub async fn thread_archive(&self, thread_id: &str) -> Result<(), CodexClientError> {
        let params = ThreadIdParams {
            thread_id: thread_id.to_string(),
        };
        let _: Value = self.call(METHOD_THREAD_ARCHIVE, ser(&params)?).await?;
        self.shared
            .subscribed
            .lock()
            .expect("subscribed lock")
            .remove(thread_id);
        Ok(())
    }

    pub async fn thread_compact_start(&self, thread_id: &str) -> Result<(), CodexClientError> {
        let params = ThreadIdParams {
            thread_id: thread_id.to_string(),
        };
        let _: Value = self
            .call(METHOD_THREAD_COMPACT_START, ser(&params)?)
            .await?;
        Ok(())
    }

    pub async fn turn_start(
        &self,
        params: TurnStartParams,
    ) -> Result<TurnStartResponse, CodexClientError> {
        self.call(METHOD_TURN_START, ser(&params)?).await
    }

    /// Steer: add user input to the in-flight turn without starting a new one.
    pub async fn turn_steer(
        &self,
        params: TurnSteerParams,
    ) -> Result<TurnSteerResponse, CodexClientError> {
        self.call(METHOD_TURN_STEER, ser(&params)?).await
    }

    pub async fn turn_interrupt(
        &self,
        thread_id: &str,
        turn_id: &str,
    ) -> Result<(), CodexClientError> {
        let params = TurnInterruptParams {
            thread_id: thread_id.to_string(),
            turn_id: turn_id.to_string(),
        };
        let _: Value = self.call(METHOD_TURN_INTERRUPT, ser(&params)?).await?;
        Ok(())
    }

    /// Close the connection, fail pending requests, and end the event stream.
    pub async fn shutdown(self) {
        self.shared.closed.store(true, Ordering::SeqCst);
        cleanup_connection(&self.shared, &CodexClientError::Closed);
        if let Some(io_abort) = self.shared.io_abort.lock().expect("io abort lock").take() {
            io_abort.abort();
        }
        self.supervisor.abort();
        let _ = self.supervisor.await;
    }

    async fn call<T: DeserializeOwned>(
        &self,
        method: &'static str,
        params: Value,
    ) -> Result<T, CodexClientError> {
        let value = request(&self.shared, method, params).await?;
        serde_json::from_value(value).map_err(|error| {
            CodexClientError::Protocol(format!("decoding {method} response: {error}"))
        })
    }

    fn subscribe(&self, thread_id: &str) {
        self.shared
            .subscribed
            .lock()
            .expect("subscribed lock")
            .insert(thread_id.to_string());
    }

    /// In mutating mode a newly created thread must report the pinned CLI
    /// version; a contradiction permanently downgrades the client.
    fn admit_created_thread(&self, response: &ThreadResponse) -> Result<(), CodexClientError> {
        if let Some(cli_version) = &response.thread.cli_version
            && self.mode().is_mutating()
            && cli_version != PINNED_CODEX_VERSION
        {
            let reason = format!(
                "a new thread reported CLI version {cli_version} instead of {PINNED_CODEX_VERSION}"
            );
            *self.shared.mode.lock().expect("mode lock") = AdapterMode::ReadOnly {
                reason: reason.clone(),
            };
            tracing::warn!(target: "soleaux_adapter_codex", %reason, "downgraded to safe mode");
            return Err(CodexClientError::VersionDrift {
                expected: PINNED_CODEX_VERSION.to_string(),
                reported: cli_version.clone(),
            });
        }
        self.subscribe(&response.thread.id);
        Ok(())
    }
}

fn ser<T: serde::Serialize>(params: &T) -> Result<Value, CodexClientError> {
    serde_json::to_value(params)
        .map_err(|error| CodexClientError::Protocol(format!("encoding params: {error}")))
}

fn outbound_sender(shared: &Shared) -> Result<mpsc::Sender<String>, CodexClientError> {
    shared
        .outbound
        .lock()
        .expect("outbound lock")
        .clone()
        .ok_or(CodexClientError::NotConnected)
}

async fn send_line(shared: &Shared, line: String) -> Result<(), CodexClientError> {
    outbound_sender(shared)?
        .send(line)
        .await
        .map_err(|_| CodexClientError::NotConnected)
}

async fn request(
    shared: &Arc<Shared>,
    method: &str,
    params: Value,
) -> Result<Value, CodexClientError> {
    // Policy first: a safe-mode refusal is local and deterministic, so it must
    // not depend on whether the transport has already closed.
    if !method_is_read_only(method) {
        let mode = shared.mode.lock().expect("mode lock").clone();
        if let AdapterMode::ReadOnly { reason } = mode {
            return Err(CodexClientError::SafeMode {
                method: method.to_string(),
                reason,
            });
        }
    }
    if shared.closed.load(Ordering::SeqCst) {
        return Err(CodexClientError::Closed);
    }
    let outbound = outbound_sender(shared)?;
    let id = shared.next_id.fetch_add(1, Ordering::SeqCst);
    let (sender, receiver) = oneshot::channel();
    shared
        .pending
        .lock()
        .expect("pending lock")
        .insert(id, sender);
    let line = encode_request(id, method, &params).map_err(CodexClientError::Protocol)?;
    if outbound.send(line).await.is_err() {
        shared.pending.lock().expect("pending lock").remove(&id);
        return Err(CodexClientError::NotConnected);
    }
    match timeout(shared.config.request_timeout, receiver).await {
        Err(_) => {
            shared.pending.lock().expect("pending lock").remove(&id);
            Err(CodexClientError::Timeout {
                method: method.to_string(),
            })
        }
        Ok(Err(_)) => Err(CodexClientError::ConnectionLost),
        Ok(Ok(result)) => result,
    }
}

async fn handshake(shared: &Arc<Shared>) -> Result<InitializeResponse, CodexClientError> {
    let params = InitializeParams {
        client_info: shared.config.client_info.clone(),
        capabilities: shared.config.initialize_capabilities.clone(),
    };
    let value = request(shared, METHOD_INITIALIZE, ser(&params)?).await?;
    let response: InitializeResponse = serde_json::from_value(value).map_err(|error| {
        CodexClientError::Protocol(format!("decoding initialize response: {error}"))
    })?;
    send_line(shared, encode_notification(METHOD_INITIALIZED)).await?;
    Ok(response)
}

fn start_connection(
    connection: CodexConnection,
    shared: &Arc<Shared>,
    events: mpsc::Sender<CodexEvent>,
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

fn cleanup_connection(shared: &Shared, error: &CodexClientError) {
    *shared.outbound.lock().expect("outbound lock") = None;
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
    connector: Arc<dyn CodexConnector>,
    shared: Arc<Shared>,
    events: mpsc::Sender<CodexEvent>,
    mut io_task: JoinHandle<()>,
    mut epoch: u64,
) {
    'lifecycle: loop {
        let _ = (&mut io_task).await;
        cleanup_connection(&shared, &CodexClientError::ConnectionLost);
        if shared.closed.load(Ordering::SeqCst) {
            let _ = events
                .send(CodexEvent::Closed {
                    reason: "shutdown".to_string(),
                })
                .await;
            return;
        }
        let _ = events
            .send(CodexEvent::Disconnected {
                epoch,
                reason: "the app-server connection ended".to_string(),
            })
            .await;
        let policy = shared.config.reconnect.clone();
        let mut attempt = 0u32;
        let mut delay = policy.initial_delay;
        loop {
            if attempt >= policy.max_attempts {
                shared.closed.store(true, Ordering::SeqCst);
                cleanup_connection(&shared, &CodexClientError::Closed);
                let _ = events
                    .send(CodexEvent::Closed {
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
                    .send(CodexEvent::Closed {
                        reason: "shutdown".to_string(),
                    })
                    .await;
                return;
            }
            let connection = match connector.connect().await {
                Ok(connection) => connection,
                Err(error) => {
                    tracing::debug!(
                        target: "soleaux_adapter_codex",
                        attempt,
                        error = %error,
                        "reconnect attempt failed"
                    );
                    continue;
                }
            };
            epoch += 1;
            io_task = start_connection(connection, &shared, events.clone());
            match handshake(&shared).await {
                Ok(initialize) => {
                    let _ = events
                        .send(CodexEvent::Connected { epoch, initialize })
                        .await;
                    resume_subscribed(&shared, &events, epoch).await;
                    continue 'lifecycle;
                }
                Err(error) => {
                    tracing::debug!(
                        target: "soleaux_adapter_codex",
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

async fn resume_subscribed(shared: &Arc<Shared>, events: &mpsc::Sender<CodexEvent>, epoch: u64) {
    let subscribed: Vec<String> = shared
        .subscribed
        .lock()
        .expect("subscribed lock")
        .iter()
        .cloned()
        .collect();
    for thread_id in subscribed {
        let params = ThreadResumeParams {
            thread_id: thread_id.clone(),
            cwd: None,
            model: None,
            approval_policy: None,
            sandbox: None,
        };
        let outcome = match ser(&params) {
            Ok(params) => request(shared, METHOD_THREAD_RESUME, params)
                .await
                .map(|_| ()),
            Err(error) => Err(error),
        };
        let event = match outcome {
            Ok(()) => CodexEvent::ThreadResumed { epoch, thread_id },
            Err(error) => CodexEvent::ThreadResumeFailed {
                epoch,
                thread_id,
                error: error.to_string(),
            },
        };
        let _ = events.send(event).await;
    }
}

async fn run_connection(
    reader: Box<dyn AsyncRead + Send + Unpin>,
    mut writer: Box<dyn AsyncWrite + Send + Unpin>,
    mut outbound_rx: mpsc::Receiver<String>,
    shared: Arc<Shared>,
    events: mpsc::Sender<CodexEvent>,
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
                        target: "soleaux_adapter_codex",
                        "closing connection: frame is not UTF-8"
                    );
                    break;
                };
                match parse_incoming(text) {
                    Ok(message) => dispatch(message, &shared, &events).await,
                    Err(detail) => {
                        tracing::warn!(
                            target: "soleaux_adapter_codex",
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
                    target: "soleaux_adapter_codex",
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
    message: IncomingMessage,
    shared: &Arc<Shared>,
    events: &mpsc::Sender<CodexEvent>,
) {
    match message {
        IncomingMessage::Response { id, result } => {
            let RequestId::Number(id) = id else {
                tracing::debug!(
                    target: "soleaux_adapter_codex",
                    "ignoring response with a non-numeric id"
                );
                return;
            };
            let sender = shared.pending.lock().expect("pending lock").remove(&id);
            if let Some(sender) = sender {
                let outcome = result.map_err(|error| CodexClientError::Rpc {
                    code: error.code,
                    message: error.message,
                });
                let _ = sender.send(outcome);
            }
        }
        IncomingMessage::ServerRequest { id, request } => {
            handle_server_request(id, request, shared, events).await;
        }
        IncomingMessage::Notification(notification) => {
            record_notification_cursor(shared, &notification).await;
            let _ = events.send(CodexEvent::Notification(notification)).await;
        }
    }
}

async fn handle_server_request(
    id: RequestId,
    request: CodexServerRequest,
    shared: &Arc<Shared>,
    events: &mpsc::Sender<CodexEvent>,
) {
    if !request.is_approval() {
        let method = request.method().to_string();
        if let Ok(line) = encode_error_response(
            &id,
            UNSUPPORTED_SERVER_REQUEST_CODE,
            &format!("the Soleaux Codex adapter does not support {method}"),
        ) {
            let _ = send_line(shared, line).await;
        }
        let _ = events
            .send(CodexEvent::UnsupportedServerRequest { method })
            .await;
        return;
    }
    let mode = shared.mode.lock().expect("mode lock").clone();
    if let AdapterMode::ReadOnly { reason } = mode {
        if let Ok(line) =
            encode_response(&id, &approval_response_body(ApprovalDecision::FAIL_CLOSED))
        {
            let _ = send_line(shared, line).await;
        }
        let _ = events
            .send(CodexEvent::ApprovalDenied { request, reason })
            .await;
        return;
    }
    let (sender, receiver) = oneshot::channel();
    let pending = PendingApproval {
        request,
        responder: sender,
    };
    let waiter_shared = shared.clone();
    tokio::spawn(async move {
        let decision = match timeout(waiter_shared.config.approval_timeout, receiver).await {
            Ok(Ok(decision)) => decision,
            _ => ApprovalDecision::FAIL_CLOSED,
        };
        if let Ok(line) = encode_response(&id, &approval_response_body(decision)) {
            let _ = send_line(&waiter_shared, line).await;
        }
    });
    let _ = events.send(CodexEvent::ApprovalRequested(pending)).await;
}

/// Record durable cursors before the notification is delivered, so a consumer
/// observing an event may rely on the cursor already being persisted.
async fn record_notification_cursor(shared: &Arc<Shared>, notification: &CodexNotification) {
    let Some(cursors) = shared.cursors.clone() else {
        return;
    };
    enum Write {
        Advance(CursorUpdate),
        Archive(String),
    }
    let write = match notification {
        CodexNotification::TurnCompleted(turn) => Write::Advance(CursorUpdate {
            scope: thread_scope(&turn.thread_id),
            cursor: turn.turn.id.clone(),
            watermark: turn.turn.completed_at.map(|at| at.to_string()),
            metadata: serde_json::json!({
                "status": turn.turn.status,
            }),
        }),
        CodexNotification::ThreadCompacted(reference) => Write::Advance(CursorUpdate {
            scope: thread_scope(&reference.thread_id),
            cursor: reference.turn_id.clone(),
            watermark: None,
            metadata: serde_json::json!({"compacted": true}),
        }),
        CodexNotification::ThreadArchived { thread_id } => Write::Archive(thread_id.clone()),
        _ => return,
    };
    let outcome = tokio::task::spawn_blocking(move || match write {
        Write::Advance(update) => cursors.advance(&update).map(|_| ()),
        Write::Archive(thread_id) => cursors.mark_archived(&thread_id).map(|_| ()),
    })
    .await;
    match outcome {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            tracing::warn!(
                target: "soleaux_adapter_codex",
                error = %format!("{error:#}"),
                "adapter cursor write failed"
            );
        }
        Err(error) => {
            tracing::warn!(
                target: "soleaux_adapter_codex",
                error = %error,
                "adapter cursor task failed"
            );
        }
    }
}
