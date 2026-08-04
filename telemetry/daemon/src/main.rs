use std::{collections::{BTreeSet, HashMap, HashSet}, convert::Infallible, net::SocketAddr, path::{Path as FilePath, PathBuf}, sync::Arc, time::{Duration, SystemTime, UNIX_EPOCH}};

use axum::{body::Body, extract::{Path, Query, Request, State}, http::{header, HeaderMap, HeaderValue, Method, StatusCode}, middleware::{self, Next}, response::{sse::Event, IntoResponse, Response, Sse}, routing::{get, post}, Json, Router};
use futures_util::stream::{self, Stream};
use serde::{Deserialize, Serialize};
use serde_json::json;
use subtle::ConstantTimeEq;
use sysinfo::{Pid, System};
use tokio::{sync::RwLock, time};
use tower_http::{cors::{AllowOrigin, CorsLayer}, trace::TraceLayer};

const TOKEN_ENV: &str = "SOLEAUX_DAEMON_TOKEN";
const TOKEN_FILE_ENV: &str = "SOLEAUX_DAEMON_TOKEN_FILE";
const ALLOWED_ORIGINS_ENV: &str = "SOLEAUX_DAEMON_ALLOWED_ORIGINS";
const MIN_TOKEN_CHARACTERS: usize = 32;
const GENERATED_TOKEN_BYTES: usize = 32;
const DEFAULT_DASHBOARD_ORIGINS: [&str; 3] = ["http://127.0.0.1:43121", "http://localhost:43121", "http://[::1]:43121"];

#[derive(Clone)]
struct SecurityConfig {
    bearer_token: Arc<String>,
    allowed_origins: Arc<BTreeSet<String>>,
}

#[derive(Debug, Default, PartialEq)]
struct DaemonOptions {
    token: Option<String>,
    token_file: Option<PathBuf>,
    allowed_origins: Vec<String>,
}

#[derive(Clone, Default)]
struct AppState {
    sessions: Arc<RwLock<HashMap<String, Session>>>,
    usage_events: Arc<RwLock<Vec<UsageEvent>>>,
    quotas: Arc<RwLock<HashMap<String, QuotaWindow>>>,
    mcp_events: Arc<RwLock<Vec<McpToolCallEvent>>>,
    mcp_backends: Arc<RwLock<HashSet<String>>>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProcessIdentity { pid: u32, started_at_unix_ms: u64 }

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Session {
    id: String,
    provider_id: String,
    display_name: String,
    root_process: ProcessIdentity,
    working_directory: Option<String>,
    repository_root: Option<String>,
    branch: Option<String>,
    model_id: Option<String>,
    context_window_tokens: Option<u64>,
    started_at: u128,
    ended_at: Option<u128>,
    state: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegisterSession {
    id: String,
    provider_id: String,
    display_name: Option<String>,
    root_pid: u32,
    root_started_at_unix_ms: u64,
    working_directory: Option<String>,
    repository_root: Option<String>,
    branch: Option<String>,
    model_id: Option<String>,
    context_window_tokens: Option<u64>,
}

#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct TokenUsage {
    input_tokens: u64,
    cached_input_tokens: u64,
    cache_write_tokens: u64,
    output_tokens: u64,
    reasoning_tokens: u64,
    total_tokens: u64,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LlmPerformance {
    request_started_at: u128,
    first_token_at: Option<u128>,
    completed_at: Option<u128>,
    latency_ms: Option<f64>,
    time_to_first_token_ms: Option<f64>,
    tokens_per_second: Option<f64>,
    retry_count: u32,
    status: String,
    error_code: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UsageEvent {
    id: String,
    provider_id: String,
    account_id: Option<String>,
    workspace_id: Option<String>,
    session_id: Option<String>,
    tool_execution_id: Option<String>,
    request_id: Option<String>,
    model_id: String,
    source: String,
    occurred_at: u128,
    usage: TokenUsage,
    performance: LlmPerformance,
    context_window_tokens: Option<u64>,
    context_tokens_used: Option<u64>,
    context_utilization_percent: Option<f64>,
    estimated_cost_usd: Option<f64>,
    credits_consumed: Option<f64>,
    metadata: Option<HashMap<String, serde_json::Value>>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QuotaWindow {
    id: String,
    provider_id: String,
    account_id: Option<String>,
    plan_id: Option<String>,
    label: String,
    kind: String,
    metric: String,
    limit: Option<f64>,
    used: f64,
    remaining: Option<f64>,
    utilization_percent: Option<f64>,
    window_started_at: Option<u128>,
    resets_at: Option<u128>,
    duration_seconds: Option<u64>,
    source: String,
    observed_at: u128,
    confidence: f64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProviderUsageSummary {
    provider_id: String,
    account_id: Option<String>,
    plan_id: Option<String>,
    observed_at: u128,
    tokens: TokenUsage,
    request_count: usize,
    failed_request_count: usize,
    estimated_cost_usd: f64,
    credits_consumed: f64,
    average_latency_ms: Option<f64>,
    average_time_to_first_token_ms: Option<f64>,
    average_tokens_per_second: Option<f64>,
    quotas: Vec<QuotaWindow>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthResponse { status: &'static str, service: &'static str, protocol_version: u8 }

// Wire shape of the soleaux metrics middleware's ToolCallEvent.payload(); the
// snake_case Python emitter contract is ingested unchanged, unlike the
// camelCase daemon-native types above.
#[derive(Clone, Serialize, Deserialize)]
struct McpToolCallEvent {
    operation: String,
    backend: String,
    tool_name: String,
    duration_ms: f64,
    ok: bool,
    error_type: Option<String>,
    at: String,
}

// Registration payload from the soleaux server: one configured MCP backend.
// Registration lets the summary surface backends that have produced no events.
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegisterMcpBackend { backend: String }

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct McpBackendSummary {
    backend: String,
    call_count: usize,
    error_count: usize,
    p50_duration_ms: Option<f64>,
    p95_duration_ms: Option<f64>,
    last_event_at: Option<String>,
    last_error_type: Option<String>,
    last_auth_state: Option<&'static str>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SystemSnapshot { cpu_percent: f32, memory_used_bytes: u64, memory_total_bytes: u64, process_count: usize }

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProcessSnapshot {
    identity: ProcessIdentity,
    parent_pid: Option<u32>,
    session_id: Option<String>,
    executable: String,
    command: Vec<String>,
    cpu_percent: f32,
    resident_memory_bytes: u64,
    runtime_seconds: u64,
    attribution_method: &'static str,
    attribution_confidence: f32,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AlertSnapshot {
    id: String,
    severity: &'static str,
    category: &'static str,
    title: String,
    description: String,
    session_id: Option<String>,
    process: Option<ProcessIdentity>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SnapshotEvent {
    r#type: &'static str,
    protocol_version: u8,
    sequence: u64,
    timestamp: u128,
    system: SystemSnapshot,
    sessions: Vec<Session>,
    process_changes: Vec<ProcessSnapshot>,
    removed_processes: Vec<ProcessIdentity>,
    alerts: Vec<AlertSnapshot>,
    provider_usage: Vec<ProviderUsageSummary>,
    recent_usage: Vec<UsageEvent>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "soleaux_daemon=info,tower_http=info".into())).init();
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let options = match parse_arguments(&arguments) {
        Ok(options) => options,
        Err(message) => { eprintln!("soleaux-daemon: {message}"); std::process::exit(2); }
    };
    let (security, token_source) = match resolve_security(&options) {
        Ok(resolved) => resolved,
        Err(message) => { eprintln!("soleaux-daemon: {message}"); std::process::exit(2); }
    };
    let allowed_origins = security.allowed_origins.iter().cloned().collect::<Vec<_>>().join(", ");
    tracing::info!(%token_source, %allowed_origins, "daemon API requires a bearer token; /api/v1/health stays open");
    let app = secure_api_router(build_router(AppState::default()), security);

    let address = SocketAddr::from(([127, 0, 0, 1], 43_120));
    tracing::info!(%address, "starting Soleaux daemon");
    let listener = tokio::net::TcpListener::bind(address).await.expect("failed to bind Soleaux daemon");
    axum::serve(listener, app).with_graceful_shutdown(shutdown_signal()).await.expect("Soleaux daemon failed");
}

fn parse_arguments(arguments: &[String]) -> Result<DaemonOptions, String> {
    let mut options = DaemonOptions::default();
    let mut index = 0;
    while index < arguments.len() {
        let argument = arguments[index].as_str();
        let (flag, mut inline) = match argument.split_once('=') {
            Some((flag, value)) => (flag, Some(value.to_string())),
            None => (argument, None),
        };
        if !matches!(flag, "--token" | "--token-file" | "--allowed-origin") {
            return Err(format!("unsupported argument {flag}; expected --token, --token-file, or --allowed-origin"));
        }
        let value = match inline.take() {
            Some(value) => value,
            None => {
                index += 1;
                arguments.get(index).cloned().ok_or_else(|| format!("{flag} requires a value"))?
            }
        };
        match flag {
            "--token" => options.token = Some(value),
            "--token-file" => options.token_file = Some(PathBuf::from(value)),
            _ => options.allowed_origins.push(value),
        }
        index += 1;
    }
    Ok(options)
}

fn resolve_security(options: &DaemonOptions) -> Result<(SecurityConfig, String), String> {
    let environment_token = std::env::var(TOKEN_ENV).ok().map(|value| value.trim().to_string()).filter(|value| !value.is_empty());
    let (token, token_source) = if let Some(token) = options.token.as_deref() {
        (validated_token(token)?, "--token".to_string())
    } else if let Some(token) = environment_token {
        (validated_token(&token)?, TOKEN_ENV.to_string())
    } else {
        let path = token_file_path(options)?;
        let token = load_or_generate_token(&path)?;
        (token, path.display().to_string())
    };
    let environment_origins = std::env::var(ALLOWED_ORIGINS_ENV).ok();
    let allowed_origins = resolve_allowed_origins(&options.allowed_origins, environment_origins.as_deref());
    let security = SecurityConfig { bearer_token: Arc::new(token), allowed_origins: Arc::new(allowed_origins) };
    Ok((security, token_source))
}

fn validated_token(token: &str) -> Result<String, String> {
    let token = token.trim();
    if token.chars().count() < MIN_TOKEN_CHARACTERS {
        return Err(format!("bearer token must contain at least {MIN_TOKEN_CHARACTERS} characters"));
    }
    Ok(token.to_string())
}

fn token_file_path(options: &DaemonOptions) -> Result<PathBuf, String> {
    if let Some(path) = &options.token_file {
        return Ok(path.clone());
    }
    if let Some(path) = std::env::var_os(TOKEN_FILE_ENV).filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(path));
    }
    let home = std::env::var_os("HOME").filter(|value| !value.is_empty()).ok_or_else(|| format!("no bearer token configured and HOME is unset; pass --token, set {TOKEN_ENV}, or set {TOKEN_FILE_ENV}"))?;
    Ok(PathBuf::from(home).join(".soleaux").join("telemetry").join("daemon.token"))
}

fn load_or_generate_token(path: &FilePath) -> Result<String, String> {
    if path.exists() {
        let stored = std::fs::read_to_string(path).map_err(|error| format!("reading bearer token file {}: {error}", path.display()))?;
        return validated_token(&stored).map_err(|message| format!("{message} in {}", path.display()));
    }
    let token = generate_token()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| format!("creating token directory {}: {error}", parent.display()))?;
        restrict_permissions(parent, 0o700)?;
    }
    std::fs::write(path, format!("{token}\n")).map_err(|error| format!("writing bearer token file {}: {error}", path.display()))?;
    restrict_permissions(path, 0o600)?;
    Ok(token)
}

fn generate_token() -> Result<String, String> {
    let mut bytes = [0_u8; GENERATED_TOKEN_BYTES];
    getrandom::fill(&mut bytes).map_err(|error| format!("generating bearer token: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

#[cfg(unix)]
fn restrict_permissions(path: &FilePath, mode: u32) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).map_err(|error| format!("restricting permissions on {}: {error}", path.display()))
}

#[cfg(not(unix))]
fn restrict_permissions(_path: &FilePath, _mode: u32) -> Result<(), String> {
    Ok(())
}

fn resolve_allowed_origins(flag_origins: &[String], environment_origins: Option<&str>) -> BTreeSet<String> {
    let mut origins: BTreeSet<String> = flag_origins.iter().map(|origin| origin.trim().to_string()).filter(|origin| !origin.is_empty()).collect();
    if origins.is_empty() {
        if let Some(environment_origins) = environment_origins {
            origins = environment_origins.split(',').map(|origin| origin.trim().to_string()).filter(|origin| !origin.is_empty()).collect();
        }
    }
    if origins.is_empty() {
        origins = DEFAULT_DASHBOARD_ORIGINS.iter().map(|origin| (*origin).to_string()).collect();
    }
    origins
}

fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/api/v1/health", get(health))
        .route("/api/v1/system", get(system_snapshot))
        .route("/api/v1/sessions", get(list_sessions).post(register_session))
        .route("/api/v1/sessions/{id}/end", post(end_session))
        .route("/api/v1/processes", get(list_processes))
        .route("/api/v1/usage/events", get(list_usage_events).post(record_usage_event))
        .route("/api/v1/usage/summary", get(usage_summary))
        .route("/api/v1/quotas", get(list_quotas).post(record_quota))
        .route("/api/v1/mcp/events", get(list_mcp_events).post(record_mcp_event))
        .route("/api/v1/mcp/backends", post(register_mcp_backend))
        .route("/api/v1/mcp/summary", get(mcp_summary))
        .route("/api/v1/stream", get(stream_snapshots))
        .with_state(state)
        .layer(TraceLayer::new_for_http().make_span_with(request_span))
}

fn secure_api_router(router: Router, security: SecurityConfig) -> Router {
    let cors = cors_layer(&security);
    router
        .layer(middleware::from_fn_with_state(security, guard_api_request))
        .layer(cors)
}

const HEALTH_PATH: &str = "/api/v1/health";
const STREAM_PATH: &str = "/api/v1/stream";

async fn guard_api_request(State(security): State<SecurityConfig>, request: Request, next: Next) -> Response {
    if let Some(origin) = request.headers().get(header::ORIGIN) {
        let allowed = origin.to_str().is_ok_and(|origin| security.allowed_origins.contains(origin));
        if !allowed {
            return (StatusCode::FORBIDDEN, Json(json!({ "error": "origin not permitted" }))).into_response();
        }
    }
    let path = request.uri().path();
    if path == HEALTH_PATH {
        return next.run(request).await;
    }
    let supplied = header_token(request.headers());
    if supplied.is_some_and(|token| bearer_matches(&security, token)) {
        return next.run(request).await;
    }
    if path == STREAM_PATH && query_access_token(request.uri().query()).is_some_and(|token| bearer_matches(&security, &token)) {
        return next.run(request).await;
    }
    match supplied {
        Some(_) => unauthorized("invalid bearer token"),
        None => unauthorized("missing bearer token"),
    }
}

#[derive(Deserialize)]
struct StreamQuery {
    access_token: Option<String>,
}

fn query_access_token(query: Option<&str>) -> Option<String> {
    serde_urlencoded::from_str::<StreamQuery>(query.unwrap_or_default()).ok().and_then(|parsed| parsed.access_token)
}

fn request_span(request: &axum::http::Request<Body>) -> tracing::Span {
    tracing::info_span!("request", method = %request.method(), path = %request.uri().path())
}

fn cors_layer(security: &SecurityConfig) -> CorsLayer {
    let origins: Vec<HeaderValue> = security.allowed_origins.iter().filter_map(|origin| HeaderValue::from_str(origin).ok()).collect();
    CorsLayer::new()
        .allow_origin(AllowOrigin::list(origins))
        .allow_methods([Method::GET, Method::POST])
        .allow_headers([header::AUTHORIZATION, header::CONTENT_TYPE])
}

fn bearer_matches(security: &SecurityConfig, supplied: &str) -> bool {
    let expected = security.bearer_token.as_bytes();
    let supplied = supplied.as_bytes();
    expected.len() == supplied.len() && bool::from(expected.ct_eq(supplied))
}

fn header_token(headers: &HeaderMap) -> Option<&str> {
    headers.get(header::AUTHORIZATION).and_then(|value| value.to_str().ok()).and_then(|value| value.strip_prefix("Bearer "))
}

fn unauthorized(message: &'static str) -> Response {
    (StatusCode::UNAUTHORIZED, [(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"))], Json(json!({ "error": message }))).into_response()
}

async fn health() -> impl IntoResponse { Json(HealthResponse { status: "ok", service: "soleaux-daemon", protocol_version: 2 }) }
async fn system_snapshot() -> impl IntoResponse { Json(collect_processes(&[]).0) }

async fn list_sessions(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.sessions.read().await.values().cloned().collect::<Vec<_>>())
}

async fn register_session(State(state): State<AppState>, Json(input): Json<RegisterSession>) -> impl IntoResponse {
    let session = Session {
        id: input.id.clone(), provider_id: input.provider_id, display_name: input.display_name.unwrap_or_else(|| input.id.clone()),
        root_process: ProcessIdentity { pid: input.root_pid, started_at_unix_ms: input.root_started_at_unix_ms },
        working_directory: input.working_directory, repository_root: input.repository_root, branch: input.branch,
        model_id: input.model_id, context_window_tokens: input.context_window_tokens,
        started_at: now_ms(), ended_at: None, state: "active".into(),
    };
    state.sessions.write().await.insert(input.id, session.clone());
    (StatusCode::CREATED, Json(session))
}

async fn end_session(Path(id): Path<String>, State(state): State<AppState>) -> impl IntoResponse {
    let mut sessions = state.sessions.write().await;
    match sessions.get_mut(&id) {
        Some(session) => { session.ended_at = Some(now_ms()); session.state = "ended".into(); StatusCode::NO_CONTENT }
        None => StatusCode::NOT_FOUND,
    }
}

async fn list_processes(State(state): State<AppState>) -> impl IntoResponse {
    let sessions = state.sessions.read().await.values().cloned().collect::<Vec<_>>();
    Json(collect_processes(&sessions).1)
}

async fn record_usage_event(State(state): State<AppState>, Json(mut event): Json<UsageEvent>) -> impl IntoResponse {
    if event.occurred_at == 0 { event.occurred_at = now_ms(); }
    if event.usage.total_tokens == 0 {
        event.usage.total_tokens = event.usage.input_tokens + event.usage.output_tokens + event.usage.reasoning_tokens;
    }
    if event.context_utilization_percent.is_none() {
        if let (Some(used), Some(window)) = (event.context_tokens_used, event.context_window_tokens) {
            if window > 0 { event.context_utilization_percent = Some((used as f64 / window as f64) * 100.0); }
        }
    }
    let mut events = state.usage_events.write().await;
    if events.iter().any(|item| item.id == event.id) { return (StatusCode::CONFLICT, Json(event)); }
    events.push(event.clone());
    if events.len() > 10_000 { let drain_to = events.len() - 10_000; events.drain(0..drain_to); }
    (StatusCode::CREATED, Json(event))
}

async fn list_usage_events(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.usage_events.read().await.clone())
}

async fn record_quota(State(state): State<AppState>, Json(mut quota): Json<QuotaWindow>) -> impl IntoResponse {
    if quota.observed_at == 0 { quota.observed_at = now_ms(); }
    if quota.utilization_percent.is_none() {
        if let Some(limit) = quota.limit { if limit > 0.0 { quota.utilization_percent = Some((quota.used / limit) * 100.0); } }
    }
    state.quotas.write().await.insert(quota.id.clone(), quota.clone());
    (StatusCode::CREATED, Json(quota))
}

async fn list_quotas(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.quotas.read().await.values().cloned().collect::<Vec<_>>())
}

async fn usage_summary(State(state): State<AppState>) -> impl IntoResponse {
    let events = state.usage_events.read().await.clone();
    let quotas = state.quotas.read().await.values().cloned().collect::<Vec<_>>();
    Json(summarize_usage(&events, &quotas))
}

async fn record_mcp_event(State(state): State<AppState>, Json(event): Json<McpToolCallEvent>) -> impl IntoResponse {
    let mut events = state.mcp_events.write().await;
    events.push(event.clone());
    if events.len() > 10_000 { let drain_to = events.len() - 10_000; events.drain(0..drain_to); }
    (StatusCode::CREATED, Json(event))
}

// Idempotent upsert: server restarts re-register the same configured backends.
async fn register_mcp_backend(State(state): State<AppState>, Json(input): Json<RegisterMcpBackend>) -> impl IntoResponse {
    state.mcp_backends.write().await.insert(input.backend.clone());
    (StatusCode::CREATED, Json(input))
}

#[derive(Deserialize)]
struct McpEventQuery {
    backend: Option<String>,
    limit: Option<usize>,
}

// Drill-down consumers poll one backend's recent calls every few seconds; the
// page cap keeps a full 10,000-event retention buffer off the wire.
const MAX_MCP_EVENT_PAGE: usize = 1_000;

async fn list_mcp_events(State(state): State<AppState>, Query(query): Query<McpEventQuery>) -> impl IntoResponse {
    let events = state.mcp_events.read().await;
    if query.backend.is_none() && query.limit.is_none() {
        return Json(events.clone());
    }
    let mut selected: Vec<McpToolCallEvent> = events
        .iter()
        .filter(|event| query.backend.as_deref().map_or(true, |backend| event.backend == backend))
        .cloned()
        .collect();
    // Concurrent completions reach the daemon out of order through detached
    // emitter tasks; `at` is UTC ISO-8601, so newest-first is lexicographic.
    selected.sort_by(|a, b| b.at.cmp(&a.at));
    if let Some(limit) = query.limit {
        selected.truncate(limit.min(MAX_MCP_EVENT_PAGE));
    }
    Json(selected)
}

async fn mcp_summary(State(state): State<AppState>) -> impl IntoResponse {
    let events = state.mcp_events.read().await;
    let backends = state.mcp_backends.read().await;
    Json(summarize_mcp(&events, &backends))
}

fn summarize_mcp(events: &[McpToolCallEvent], registered: &HashSet<String>) -> Vec<McpBackendSummary> {
    // The middleware also emits tools/list catalog discoveries (attributed to
    // the local backend); call counts and latency percentiles summarize only
    // actual tool executions.
    let mut grouped: HashMap<&str, Vec<&McpToolCallEvent>> = HashMap::new();
    for event in events.iter().filter(|event| event.operation == "tools/call") {
        grouped.entry(event.backend.as_str()).or_default().push(event);
    }
    let mut summaries = Vec::new();
    for (backend, backend_events) in &grouped {
        let mut durations: Vec<f64> = backend_events.iter().map(|event| event.duration_ms).collect();
        durations.sort_by(f64::total_cmp);
        // The middleware stamps `at` with UTC ISO-8601, so the latest event
        // is the lexicographic maximum.
        let last = backend_events.iter().max_by(|a, b| a.at.cmp(&b.at));
        summaries.push(McpBackendSummary {
            backend: backend.to_string(),
            call_count: backend_events.len(),
            error_count: backend_events.iter().filter(|event| !event.ok).count(),
            p50_duration_ms: percentile(&durations, 0.50),
            p95_duration_ms: percentile(&durations, 0.95),
            last_event_at: last.map(|event| event.at.clone()),
            last_error_type: last.and_then(|event| event.error_type.clone()),
            last_auth_state: last.map(|event| auth_state(event)),
        });
    }
    // Registered backends with no recorded events still surface with zeroed
    // aggregates so a configured but silent (down, unauthenticated, or newly
    // added) backend is visible instead of looking like no gateway traffic.
    for backend in registered.iter().filter(|backend| !grouped.contains_key(backend.as_str())) {
        summaries.push(McpBackendSummary {
            backend: backend.clone(),
            call_count: 0,
            error_count: 0,
            p50_duration_ms: None,
            p95_duration_ms: None,
            last_event_at: None,
            last_error_type: None,
            last_auth_state: None,
        });
    }
    summaries.sort_by(|a, b| a.backend.cmp(&b.backend));
    summaries
}

// The emitter reports exception type names only, so auth state is derived
// from the latest event: an auth-named exception means the backend's auth is
// failing, any other failure is a generic error, success means auth worked.
fn auth_state(event: &McpToolCallEvent) -> &'static str {
    if event.ok { return "ok"; }
    match &event.error_type {
        Some(error_type) if error_type.to_lowercase().contains("auth") => "auth_error",
        _ => "error",
    }
}

fn percentile(sorted: &[f64], quantile: f64) -> Option<f64> {
    if sorted.is_empty() { return None; }
    let rank = ((sorted.len() as f64) * quantile).ceil() as usize;
    sorted.get(rank.saturating_sub(1)).copied()
}

async fn stream_snapshots(State(state): State<AppState>) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let interval = time::interval(Duration::from_secs(1));
    let stream = stream::unfold((interval, 0_u64, state), |(mut interval, mut sequence, state)| async move {
        interval.tick().await;
        sequence += 1;
        let sessions = state.sessions.read().await.values().cloned().collect::<Vec<_>>();
        let events = state.usage_events.read().await.clone();
        let quotas = state.quotas.read().await.values().cloned().collect::<Vec<_>>();
        let (system, processes, mut alerts) = collect_processes(&sessions);
        alerts.extend(usage_alerts(&events, &quotas));
        let payload = SnapshotEvent {
            r#type: "snapshot", protocol_version: 2, sequence, timestamp: now_ms(), system, sessions,
            process_changes: processes, removed_processes: vec![], alerts,
            provider_usage: summarize_usage(&events, &quotas), recent_usage: events.into_iter().rev().take(100).collect(),
        };
        let event = Event::default().event("snapshot").json_data(payload).expect("snapshot should serialize");
        Some((Ok(event), (interval, sequence, state)))
    });
    Sse::new(stream).keep_alive(axum::response::sse::KeepAlive::default())
}

fn summarize_usage(events: &[UsageEvent], quotas: &[QuotaWindow]) -> Vec<ProviderUsageSummary> {
    let mut grouped: HashMap<String, Vec<&UsageEvent>> = HashMap::new();
    for event in events { grouped.entry(event.provider_id.clone()).or_default().push(event); }
    let mut summaries = Vec::new();
    for (provider_id, provider_events) in grouped {
        let mut tokens = TokenUsage::default();
        let mut failed = 0;
        let mut request_count: u64 = 0;
        let mut cost = 0.0;
        let mut credits = 0.0;
        let mut latencies = Vec::new();
        let mut ttfts = Vec::new();
        let mut rates = Vec::new();
        for event in &provider_events {
            tokens.input_tokens += event.usage.input_tokens;
            tokens.cached_input_tokens += event.usage.cached_input_tokens;
            tokens.cache_write_tokens += event.usage.cache_write_tokens;
            tokens.output_tokens += event.usage.output_tokens;
            tokens.reasoning_tokens += event.usage.reasoning_tokens;
            tokens.total_tokens += event.usage.total_tokens;
            if event.performance.status == "failed" { failed += 1; }
            // Aggregate imports carry the provider's real request count in
            // metadata; counting events would report bucket counts instead.
            request_count += event.metadata.as_ref()
                .and_then(|metadata| metadata.get("requestCount"))
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(1);
            cost += event.estimated_cost_usd.unwrap_or(0.0);
            credits += event.credits_consumed.unwrap_or(0.0);
            if let Some(value) = event.performance.latency_ms { latencies.push(value); }
            if let Some(value) = event.performance.time_to_first_token_ms { ttfts.push(value); }
            if let Some(value) = event.performance.tokens_per_second { rates.push(value); }
        }
        summaries.push(ProviderUsageSummary {
            provider_id: provider_id.clone(), account_id: provider_events.first().and_then(|e| e.account_id.clone()),
            plan_id: quotas.iter().find(|q| q.provider_id == provider_id).and_then(|q| q.plan_id.clone()), observed_at: now_ms(),
            tokens, request_count: request_count as usize, failed_request_count: failed, estimated_cost_usd: cost, credits_consumed: credits,
            average_latency_ms: average(&latencies), average_time_to_first_token_ms: average(&ttfts), average_tokens_per_second: average(&rates),
            quotas: quotas.iter().filter(|q| q.provider_id == provider_id).cloned().collect(),
        });
    }
    for quota in quotas {
        if summaries.iter().all(|summary| summary.provider_id != quota.provider_id) {
            summaries.push(ProviderUsageSummary {
                provider_id: quota.provider_id.clone(), account_id: quota.account_id.clone(), plan_id: quota.plan_id.clone(), observed_at: now_ms(),
                tokens: TokenUsage::default(), request_count: 0, failed_request_count: 0, estimated_cost_usd: 0.0, credits_consumed: 0.0,
                average_latency_ms: None, average_time_to_first_token_ms: None, average_tokens_per_second: None,
                quotas: quotas.iter().filter(|item| item.provider_id == quota.provider_id).cloned().collect(),
            });
        }
    }
    summaries
}

fn usage_alerts(events: &[UsageEvent], quotas: &[QuotaWindow]) -> Vec<AlertSnapshot> {
    let mut alerts = Vec::new();
    for quota in quotas {
        if quota.utilization_percent.unwrap_or(0.0) >= 80.0 {
            alerts.push(AlertSnapshot { id: format!("quota-{}", quota.id), severity: if quota.utilization_percent.unwrap_or(0.0) >= 95.0 { "critical" } else { "warning" }, category: "quota", title: format!("{} quota nearly exhausted", quota.provider_id), description: format!("{} is at {:.1}% and resets {}", quota.label, quota.utilization_percent.unwrap_or(0.0), quota.resets_at.map(|value| value.to_string()).unwrap_or_else(|| "at an unknown time".into())), session_id: None, process: None });
        }
    }
    for event in events.iter().rev().take(200) {
        if event.context_utilization_percent.unwrap_or(0.0) >= 80.0 {
            alerts.push(AlertSnapshot { id: format!("context-{}", event.id), severity: if event.context_utilization_percent.unwrap_or(0.0) >= 95.0 { "critical" } else { "warning" }, category: "context", title: "Context window pressure".into(), description: format!("{} on {} is using {:.1}% of its context window", event.model_id, event.provider_id, event.context_utilization_percent.unwrap_or(0.0)), session_id: event.session_id.clone(), process: None });
        }
        if event.performance.status == "failed" {
            alerts.push(AlertSnapshot { id: format!("llm-error-{}", event.id), severity: "warning", category: "performance", title: "LLM request failed".into(), description: format!("{} {} failed: {}", event.provider_id, event.model_id, event.performance.error_code.clone().unwrap_or_else(|| "unknown error".into())), session_id: event.session_id.clone(), process: None });
        }
    }
    alerts
}

fn collect_processes(sessions: &[Session]) -> (SystemSnapshot, Vec<ProcessSnapshot>, Vec<AlertSnapshot>) {
    let mut system = System::new_all();
    system.refresh_all();
    let mut parent_map: HashMap<u32, Option<u32>> = HashMap::new();
    for (pid, process) in system.processes() { parent_map.insert(pid.as_u32(), process.parent().map(Pid::as_u32)); }
    let roots = sessions.iter().map(|s| (s.id.clone(), s.root_process.pid)).collect::<Vec<_>>();
    let mut attributed: HashMap<u32, String> = roots.iter().map(|(id, pid)| (*pid, id.clone())).collect();
    let mut changed = true;
    while changed {
        changed = false;
        for (pid, parent) in &parent_map {
            if attributed.contains_key(pid) { continue; }
            if let Some(parent_pid) = parent.and_then(|p| attributed.get(&p).cloned()) { attributed.insert(*pid, parent_pid); changed = true; }
        }
    }
    let known_roots: HashSet<u32> = roots.iter().map(|(_, pid)| *pid).collect();
    let mut processes = Vec::new();
    let mut alerts = Vec::new();
    for (pid, process) in system.processes() {
        let pid_value = pid.as_u32();
        let identity = ProcessIdentity { pid: pid_value, started_at_unix_ms: process.start_time().saturating_mul(1000) };
        let session_id = attributed.get(&pid_value).cloned();
        let snapshot = ProcessSnapshot {
            identity: identity.clone(), parent_pid: process.parent().map(Pid::as_u32), session_id: session_id.clone(),
            executable: process.name().to_string_lossy().into_owned(), command: redacted_command(process.cmd().iter().map(|value| value.to_string_lossy().into_owned()).collect()),
            cpu_percent: process.cpu_usage(), resident_memory_bytes: process.memory(), runtime_seconds: process.run_time(),
            attribution_method: if known_roots.contains(&pid_value) { "registered-root" } else if session_id.is_some() { "ancestor" } else { "unattributed" },
            attribution_confidence: if known_roots.contains(&pid_value) { 1.0 } else if session_id.is_some() { 0.95 } else { 0.0 },
        };
        if snapshot.cpu_percent >= 80.0 && session_id.is_some() {
            alerts.push(AlertSnapshot { id: format!("cpu-{}-{}", pid_value, identity.started_at_unix_ms), severity: "warning", category: "system", title: "High CPU process".into(), description: format!("{} is using {:.1}% CPU", snapshot.executable, snapshot.cpu_percent), session_id: session_id.clone(), process: Some(identity) });
        }
        processes.push(snapshot);
    }
    (SystemSnapshot { cpu_percent: system.global_cpu_usage(), memory_used_bytes: system.used_memory(), memory_total_bytes: system.total_memory(), process_count: system.processes().len() }, processes, alerts)
}

fn redacted_command(command: Vec<String>) -> Vec<String> {
    let mut parts = command.into_iter();
    let Some(program) = parts.next() else { return Vec::new(); };
    let hidden = parts.count();
    match hidden {
        0 => vec![program],
        1 => vec![program, "[1 argument redacted]".to_string()],
        _ => vec![program, format!("[{hidden} arguments redacted]")],
    }
}

fn average(values: &[f64]) -> Option<f64> { if values.is_empty() { None } else { Some(values.iter().sum::<f64>() / values.len() as f64) } }
fn now_ms() -> u128 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() }
async fn shutdown_signal() { let _ = tokio::signal::ctrl_c().await; tracing::info!("shutting down Soleaux daemon"); }

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{to_bytes, Body};
    use axum::http::Request;
    use tower::ServiceExt;

    fn mcp_event(backend: &str, tool_name: &str, duration_ms: f64, ok: bool, error_type: Option<&str>, at: &str) -> McpToolCallEvent {
        McpToolCallEvent {
            operation: "tools/call".into(),
            backend: backend.into(),
            tool_name: tool_name.into(),
            duration_ms,
            ok,
            error_type: error_type.map(str::to_string),
            at: at.into(),
        }
    }

    // The middleware emits tools/list catalog discoveries alongside calls;
    // they must not inflate call summaries.
    fn mcp_catalog_event(backend: &str, at: &str) -> McpToolCallEvent {
        McpToolCallEvent {
            operation: "tools/list".into(),
            backend: backend.into(),
            tool_name: "tools/list".into(),
            duration_ms: 1.0,
            ok: true,
            error_type: None,
            at: at.into(),
        }
    }

    async fn post_event(app: &Router, event: &McpToolCallEvent) -> axum::response::Response {
        app.clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/mcp/events")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_vec(event).expect("event should serialize")))
                    .expect("request should build"),
            )
            .await
            .expect("router should respond")
    }

    #[tokio::test]
    async fn mcp_event_ingest_stores_and_lists_events() {
        let app = build_router(AppState::default());
        let event = mcp_event("context7", "context7_search", 12.5, true, None, "2026-07-31T10:00:00+00:00");

        let response = post_event(&app, &event).await;
        assert_eq!(response.status(), StatusCode::CREATED);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let echoed: McpToolCallEvent = serde_json::from_slice(&body).expect("echo should deserialize");
        assert_eq!(echoed.backend, "context7");
        assert_eq!(echoed.tool_name, "context7_search");

        let response = app
            .oneshot(Request::builder().uri("/api/v1/mcp/events").body(Body::empty()).expect("request should build"))
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let events: Vec<McpToolCallEvent> = serde_json::from_slice(&body).expect("list should deserialize");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].duration_ms, 12.5);
        assert!(events[0].ok);
    }

    #[tokio::test]
    async fn mcp_summary_includes_registered_backends_without_events() {
        let app = build_router(AppState::default());
        for backend in ["context7", "vercel"] {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri("/api/v1/mcp/backends")
                        .header("content-type", "application/json")
                        .body(Body::from(format!(r#"{{"backend":"{backend}"}}"#)))
                        .expect("request should build"),
                )
                .await
                .expect("router should respond");
            assert_eq!(response.status(), StatusCode::CREATED);
        }
        // Re-registration (server restart) is idempotent.
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/mcp/backends")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"backend":"context7"}"#))
                    .expect("request should build"),
            )
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::CREATED);
        assert_eq!(
            post_event(&app, &mcp_event("context7", "context7_search", 10.0, true, None, "2026-07-31T10:00:00+00:00")).await.status(),
            StatusCode::CREATED,
        );

        let response = app
            .oneshot(Request::builder().uri("/api/v1/mcp/summary").body(Body::empty()).expect("request should build"))
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let summaries: Vec<serde_json::Value> = serde_json::from_slice(&body).expect("summary should deserialize");
        assert_eq!(summaries.len(), 2);

        let context7 = &summaries[0];
        assert_eq!(context7["backend"], "context7");
        assert_eq!(context7["callCount"], 1);

        // The silent registered backend surfaces with zeroed aggregates and
        // no last-event fields instead of disappearing from the registry.
        let vercel = &summaries[1];
        assert_eq!(vercel["backend"], "vercel");
        assert_eq!(vercel["callCount"], 0);
        assert_eq!(vercel["errorCount"], 0);
        assert_eq!(vercel["p50DurationMs"], serde_json::Value::Null);
        assert_eq!(vercel["p95DurationMs"], serde_json::Value::Null);
        assert_eq!(vercel["lastEventAt"], serde_json::Value::Null);
        assert_eq!(vercel["lastErrorType"], serde_json::Value::Null);
        assert_eq!(vercel["lastAuthState"], serde_json::Value::Null);
    }

    #[tokio::test]
    async fn mcp_summary_aggregates_per_backend() {
        let app = build_router(AppState::default());
        let events = [
            mcp_event("context7", "context7_search", 10.0, true, None, "2026-07-31T10:00:00+00:00"),
            mcp_event("context7", "context7_search", 20.0, true, None, "2026-07-31T10:01:00+00:00"),
            mcp_event("context7", "context7_fetch", 30.0, false, Some("AuthenticationError"), "2026-07-31T10:02:00+00:00"),
            mcp_event("local", "search", 5.0, false, Some("ToolError"), "2026-07-31T10:03:00+00:00"),
            mcp_event("local", "search", 15.0, true, None, "2026-07-31T10:04:00+00:00"),
            // Catalog listings newer than every call must not move counts,
            // last-event fields, or auth state.
            mcp_catalog_event("context7", "2026-07-31T10:05:00+00:00"),
            mcp_catalog_event("local", "2026-07-31T10:06:00+00:00"),
        ];
        for event in &events {
            assert_eq!(post_event(&app, event).await.status(), StatusCode::CREATED);
        }

        let response = app
            .oneshot(Request::builder().uri("/api/v1/mcp/summary").body(Body::empty()).expect("request should build"))
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let summaries: Vec<serde_json::Value> = serde_json::from_slice(&body).expect("summary should deserialize");
        assert_eq!(summaries.len(), 2);

        let context7 = &summaries[0];
        assert_eq!(context7["backend"], "context7");
        assert_eq!(context7["callCount"], 3);
        assert_eq!(context7["errorCount"], 1);
        assert_eq!(context7["p50DurationMs"], 20.0);
        assert_eq!(context7["p95DurationMs"], 30.0);
        assert_eq!(context7["lastEventAt"], "2026-07-31T10:02:00+00:00");
        assert_eq!(context7["lastErrorType"], "AuthenticationError");
        assert_eq!(context7["lastAuthState"], "auth_error");

        let local = &summaries[1];
        assert_eq!(local["backend"], "local");
        assert_eq!(local["callCount"], 2);
        assert_eq!(local["errorCount"], 1);
        assert_eq!(local["lastEventAt"], "2026-07-31T10:04:00+00:00");
        assert_eq!(local["lastAuthState"], "ok");
        assert_eq!(local["lastErrorType"], serde_json::Value::Null);
    }

    #[tokio::test]
    async fn mcp_events_query_filters_sorts_and_bounds() {
        let app = build_router(AppState::default());
        let events = [
            mcp_event("context7", "older", 10.0, true, None, "2026-07-31T10:00:00+00:00"),
            mcp_event("local", "other_backend", 5.0, true, None, "2026-07-31T10:03:00+00:00"),
            // Posted before "middle" but completed later: arrival order must
            // not determine drill-down order.
            mcp_event("context7", "newest", 20.0, true, None, "2026-07-31T10:02:00+00:00"),
            mcp_event("context7", "middle", 15.0, true, None, "2026-07-31T10:01:00+00:00"),
        ];
        for event in &events {
            assert_eq!(post_event(&app, event).await.status(), StatusCode::CREATED);
        }

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/api/v1/mcp/events?backend=context7&limit=2")
                    .body(Body::empty())
                    .expect("request should build"),
            )
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let events: Vec<McpToolCallEvent> = serde_json::from_slice(&body).expect("page should deserialize");
        let tools: Vec<&str> = events.iter().map(|event| event.tool_name.as_str()).collect();
        assert_eq!(tools, ["newest", "middle"]);
    }

    #[tokio::test]
    async fn mcp_event_storage_is_bounded() {
        let state = AppState::default();
        {
            let mut events = state.mcp_events.write().await;
            for index in 0..10_000 {
                events.push(mcp_event("local", "search", index as f64, true, None, "2026-07-31T10:00:00+00:00"));
            }
        }
        let overflow = mcp_event("local", "search", 1.0, true, None, "2026-07-31T10:05:00+00:00");
        let response = record_mcp_event(State(state.clone()), Json(overflow)).await.into_response();
        assert_eq!(response.status(), StatusCode::CREATED);
        let events = state.mcp_events.read().await;
        assert_eq!(events.len(), 10_000);
        assert_eq!(events.last().expect("events should not be empty").duration_ms, 1.0);
    }

    #[test]
    fn percentile_uses_nearest_rank() {
        assert_eq!(percentile(&[], 0.95), None);
        assert_eq!(percentile(&[4.0], 0.50), Some(4.0));
        let sorted = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0];
        assert_eq!(percentile(&sorted, 0.50), Some(5.0));
        assert_eq!(percentile(&sorted, 0.95), Some(10.0));
    }

    const TEST_TOKEN: &str = "0123456789abcdef0123456789abcdef";
    const TEST_ORIGIN: &str = "http://127.0.0.1:43121";

    fn test_security() -> SecurityConfig {
        SecurityConfig {
            bearer_token: Arc::new(TEST_TOKEN.to_string()),
            allowed_origins: Arc::new(BTreeSet::from([TEST_ORIGIN.to_string()])),
        }
    }

    fn secured_app() -> Router {
        secure_api_router(build_router(AppState::default()), test_security())
    }

    async fn secured_get(app: &Router, uri: &str, authorization: Option<&str>, origin: Option<&str>) -> axum::response::Response {
        let mut builder = Request::builder().uri(uri);
        if let Some(value) = authorization {
            builder = builder.header("authorization", value);
        }
        if let Some(value) = origin {
            builder = builder.header("origin", value);
        }
        app.clone()
            .oneshot(builder.body(Body::empty()).expect("request should build"))
            .await
            .expect("router should respond")
    }

    #[tokio::test]
    async fn secured_requests_without_token_are_unauthorized() {
        let app = secured_app();
        let response = secured_get(&app, "/api/v1/sessions", None, None).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(response.headers().get("www-authenticate").and_then(|value| value.to_str().ok()), Some("Bearer"));
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/v1/mcp/events")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"operation":"tools/call","backend":"local","tool_name":"search","duration_ms":1.0,"ok":true,"error_type":null,"at":"2026-07-31T10:00:00+00:00"}"#))
                    .expect("request should build"),
            )
            .await
            .expect("router should respond");
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn secured_requests_with_wrong_token_are_unauthorized() {
        let app = secured_app();
        let response = secured_get(&app, "/api/v1/sessions", Some("Bearer ffffffffffffffffffffffffffffffff"), None).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn secured_requests_with_correct_token_succeed() {
        let app = secured_app();
        let authorization = format!("Bearer {TEST_TOKEN}");
        for uri in ["/api/v1/sessions", "/api/v1/quotas", "/api/v1/usage/summary"] {
            let response = secured_get(&app, uri, Some(&authorization), None).await;
            assert_eq!(response.status(), StatusCode::OK);
        }
    }

    #[tokio::test]
    async fn health_stays_unauthenticated() {
        let app = secured_app();
        let response = secured_get(&app, "/api/v1/health", None, None).await;
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn stream_requires_token_and_accepts_query_parameter() {
        let app = secured_app();
        let response = secured_get(&app, "/api/v1/stream", None, None).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        let response = secured_get(&app, "/api/v1/stream?access_token=ffffffffffffffffffffffffffffffff", None, None).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        let response = secured_get(&app, &format!("/api/v1/stream?access_token={TEST_TOKEN}"), None, None).await;
        assert_eq!(response.status(), StatusCode::OK);
        let content_type = response.headers().get("content-type").and_then(|value| value.to_str().ok()).unwrap_or_default();
        assert!(content_type.starts_with("text/event-stream"));
        let response = secured_get(&app, "/api/v1/stream", Some(&format!("Bearer {TEST_TOKEN}")), None).await;
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn disallowed_origins_are_rejected() {
        let app = secured_app();
        let authorization = format!("Bearer {TEST_TOKEN}");
        let response = secured_get(&app, "/api/v1/sessions", Some(&authorization), Some("http://evil.example")).await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let response = secured_get(&app, "/api/v1/health", None, Some("http://evil.example")).await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let response = secured_get(&app, "/api/v1/sessions", Some(&authorization), Some(TEST_ORIGIN)).await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("access-control-allow-origin").and_then(|value| value.to_str().ok()),
            Some(TEST_ORIGIN),
        );
    }

    #[tokio::test]
    async fn preflight_reflects_only_allowed_origins() {
        let app = secured_app();
        let preflight = |origin: &'static str| {
            Request::builder()
                .method("OPTIONS")
                .uri("/api/v1/sessions")
                .header("origin", origin)
                .header("access-control-request-method", "GET")
                .body(Body::empty())
                .expect("request should build")
        };
        let response = app.clone().oneshot(preflight(TEST_ORIGIN)).await.expect("router should respond");
        assert_eq!(
            response.headers().get("access-control-allow-origin").and_then(|value| value.to_str().ok()),
            Some(TEST_ORIGIN),
        );
        let response = app.clone().oneshot(preflight("http://evil.example")).await.expect("router should respond");
        assert!(response.headers().get("access-control-allow-origin").is_none());
    }

    #[tokio::test]
    async fn process_listing_redacts_command_arguments() {
        let app = secured_app();
        let response = secured_get(&app, "/api/v1/processes", Some(&format!("Bearer {TEST_TOKEN}")), None).await;
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.expect("body should read");
        let processes: Vec<serde_json::Value> = serde_json::from_slice(&body).expect("processes should deserialize");
        assert!(!processes.is_empty());
        for process in &processes {
            let command = process["command"].as_array().expect("command should be an array");
            assert!(command.len() <= 2);
            if command.len() == 2 {
                let marker = command[1].as_str().expect("marker should be a string");
                assert!(marker.starts_with('[') && marker.ends_with("redacted]"));
            }
        }
    }

    #[test]
    fn redacted_command_keeps_only_the_program() {
        assert_eq!(redacted_command(vec![]), Vec::<String>::new());
        assert_eq!(redacted_command(vec!["node".to_string()]), vec!["node".to_string()]);
        assert_eq!(
            redacted_command(vec!["node".to_string(), "--secret=value".to_string()]),
            vec!["node".to_string(), "[1 argument redacted]".to_string()],
        );
        assert_eq!(
            redacted_command(vec!["claude".to_string(), "-p".to_string(), "prompt".to_string(), "--key".to_string()]),
            vec!["claude".to_string(), "[3 arguments redacted]".to_string()],
        );
    }

    #[test]
    fn parse_arguments_supports_token_and_origin_flags() {
        let arguments = ["--token".to_string(), "a".repeat(32), "--allowed-origin".to_string(), "http://127.0.0.1:43121".to_string(), "--allowed-origin=http://localhost:43121".to_string(), "--token-file=/tmp/example.token".to_string()];
        let options = parse_arguments(&arguments).expect("arguments should parse");
        assert_eq!(options.token.as_deref(), Some("a".repeat(32).as_str()));
        assert_eq!(options.token_file.as_deref(), Some(FilePath::new("/tmp/example.token")));
        assert_eq!(options.allowed_origins, vec!["http://127.0.0.1:43121".to_string(), "http://localhost:43121".to_string()]);
        assert!(parse_arguments(&["--unknown".to_string()]).is_err());
        assert!(parse_arguments(&["--token".to_string()]).is_err());
    }

    #[test]
    fn validated_token_enforces_minimum_length() {
        assert!(validated_token(&"a".repeat(31)).is_err());
        assert_eq!(validated_token(&format!(" {} \n", "a".repeat(32))).as_deref(), Ok("a".repeat(32).as_str()));
    }

    #[test]
    fn resolve_allowed_origins_prefers_flags_then_environment_then_defaults() {
        let flagged = resolve_allowed_origins(&["http://one.test".to_string()], Some("http://two.test"));
        assert_eq!(flagged, BTreeSet::from(["http://one.test".to_string()]));
        let environment = resolve_allowed_origins(&[], Some("http://two.test, http://three.test"));
        assert_eq!(environment, BTreeSet::from(["http://two.test".to_string(), "http://three.test".to_string()]));
        let defaults = resolve_allowed_origins(&[], None);
        assert_eq!(defaults, DEFAULT_DASHBOARD_ORIGINS.iter().map(|origin| (*origin).to_string()).collect());
    }

    #[test]
    fn query_access_token_decodes_url_encoding() {
        assert_eq!(query_access_token(None), None);
        assert_eq!(query_access_token(Some("other=value")), None);
        assert_eq!(query_access_token(Some("access_token=abc123")), Some("abc123".to_string()));
        assert_eq!(query_access_token(Some("access_token=a%2Bb")), Some("a+b".to_string()));
    }

    #[test]
    fn token_file_is_generated_once_with_user_only_permissions() {
        let directory = std::env::temp_dir().join(format!("soleaux-daemon-token-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        let path = directory.join("nested").join("daemon.token");
        let first = load_or_generate_token(&path).expect("token should generate");
        assert_eq!(first.len(), GENERATED_TOKEN_BYTES * 2);
        assert!(first.chars().all(|character| character.is_ascii_hexdigit()));
        let second = load_or_generate_token(&path).expect("token should reload");
        assert_eq!(first, second);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let file_mode = std::fs::metadata(&path).expect("token file should exist").permissions().mode() & 0o777;
            assert_eq!(file_mode, 0o600);
            let directory_mode = std::fs::metadata(path.parent().expect("token file should have a parent")).expect("token directory should exist").permissions().mode() & 0o777;
            assert_eq!(directory_mode, 0o700);
        }
        let _ = std::fs::remove_dir_all(&directory);
    }
}
