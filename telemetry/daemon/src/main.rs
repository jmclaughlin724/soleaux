use std::{collections::{HashMap, HashSet}, convert::Infallible, net::SocketAddr, sync::Arc, time::{Duration, SystemTime, UNIX_EPOCH}};

use axum::{extract::{Path, State}, http::StatusCode, response::{sse::Event, IntoResponse, Sse}, routing::{get, post}, Json, Router};
use futures_util::stream::{self, Stream};
use serde::{Deserialize, Serialize};
use sysinfo::{Pid, System};
use tokio::{sync::RwLock, time};
use tower_http::trace::TraceLayer;

#[derive(Clone, Default)]
struct AppState {
    sessions: Arc<RwLock<HashMap<String, Session>>>,
    usage_events: Arc<RwLock<Vec<UsageEvent>>>,
    quotas: Arc<RwLock<HashMap<String, QuotaWindow>>>,
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
    let state = AppState::default();
    let app = Router::new()
        .route("/api/v1/health", get(health))
        .route("/api/v1/system", get(system_snapshot))
        .route("/api/v1/sessions", get(list_sessions).post(register_session))
        .route("/api/v1/sessions/{id}/end", post(end_session))
        .route("/api/v1/processes", get(list_processes))
        .route("/api/v1/usage/events", get(list_usage_events).post(record_usage_event))
        .route("/api/v1/usage/summary", get(usage_summary))
        .route("/api/v1/quotas", get(list_quotas).post(record_quota))
        .route("/api/v1/stream", get(stream_snapshots))
        .with_state(state)
        .layer(TraceLayer::new_for_http());

    let address = SocketAddr::from(([127, 0, 0, 1], 43_120));
    tracing::info!(%address, "starting Soleaux daemon");
    let listener = tokio::net::TcpListener::bind(address).await.expect("failed to bind Soleaux daemon");
    axum::serve(listener, app).with_graceful_shutdown(shutdown_signal()).await.expect("Soleaux daemon failed");
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
            executable: process.name().to_string_lossy().into_owned(), command: process.cmd().iter().map(|value| value.to_string_lossy().into_owned()).collect(),
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

fn average(values: &[f64]) -> Option<f64> { if values.is_empty() { None } else { Some(values.iter().sum::<f64>() / values.len() as f64) } }
fn now_ms() -> u128 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() }
async fn shutdown_signal() { let _ = tokio::signal::ctrl_c().await; tracing::info!("shutting down Soleaux daemon"); }
