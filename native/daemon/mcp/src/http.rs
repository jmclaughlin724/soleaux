//! Authenticated loopback Streamable HTTP transport.
//!
//! Standalone HTTP+SSE is intentionally not exposed. POST carries JSON-RPC;
//! DELETE terminates an MCP session. GET returns 405 because this development
//! slice does not yet offer a server-initiated SSE channel.

use crate::{MCP_STABLE_VERSION, PublicMcpServer};
use anyhow::{Context, Result, bail};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashMap},
    net::SocketAddr,
    sync::Arc,
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use tokio::{net::TcpListener, sync::RwLock};
use uuid::Uuid;

const SESSION_HEADER: &str = "mcp-session-id";
const PROTOCOL_HEADER: &str = "mcp-protocol-version";
const MAX_REQUEST_BYTES: usize = 1024 * 1024;
const SESSION_TTL: Duration = Duration::from_secs(24 * 60 * 60);
const MAX_ACTIVE_SESSIONS: usize = 1_024;

#[derive(Clone)]
struct HttpState {
    server: PublicMcpServer,
    bearer_token: Arc<String>,
    allowed_origins: Arc<BTreeSet<String>>,
    sessions: Arc<RwLock<HashMap<String, SessionRecord>>>,
}

#[derive(Debug, Clone)]
struct SessionRecord {
    protocol_version: String,
    created_at: Instant,
}

#[derive(Debug)]
struct HttpError {
    status: StatusCode,
    message: String,
}

impl IntoResponse for HttpError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({
                "jsonrpc":"2.0",
                "id":Value::Null,
                "error":{"code":-32000,"message":self.message}
            })),
        )
            .into_response()
    }
}

impl PublicMcpServer {
    pub async fn serve_streamable_http(
        self,
        address: SocketAddr,
        bearer_token: impl Into<String>,
    ) -> Result<()> {
        if !address.ip().is_loopback() {
            bail!("Streamable HTTP binds to loopback only by default");
        }
        let bearer_token = bearer_token.into();
        if bearer_token.len() < 32 {
            bail!("Streamable HTTP bearer token must contain at least 32 characters");
        }
        let listener = TcpListener::bind(address)
            .await
            .with_context(|| format!("binding Soleaux Streamable HTTP at {address}"))?;
        let bound_address = listener.local_addr().context("reading bound MCP address")?;
        let mut allowed_origins = BTreeSet::new();
        let port = bound_address.port();
        allowed_origins.insert(format!("http://127.0.0.1:{port}"));
        allowed_origins.insert(format!("http://localhost:{port}"));
        allowed_origins.insert(format!("http://[::1]:{port}"));
        let state = HttpState {
            server: self,
            bearer_token: Arc::new(bearer_token),
            allowed_origins: Arc::new(allowed_origins),
            sessions: Arc::new(RwLock::new(HashMap::new())),
        };
        let application = Router::new()
            .route("/mcp", post(post_mcp).get(get_mcp).delete(delete_mcp))
            .route("/health", get(health))
            .layer(DefaultBodyLimit::max(MAX_REQUEST_BYTES))
            .with_state(state);
        axum::serve(listener, application)
            .await
            .context("Soleaux Streamable HTTP server stopped")
    }
}

async fn health(State(state): State<HttpState>, headers: HeaderMap) -> Result<Response, HttpError> {
    authorize(&state, &headers)?;
    validate_origin(&state, &headers)?;
    Ok(Json(json!({
        "ok":true,
        "product":"Soleaux",
        "transport":"streamable-http",
        "standaloneSse":false,
        "publicRootTools":state.server.tools().len(),
    }))
    .into_response())
}

async fn post_mcp(
    State(state): State<HttpState>,
    headers: HeaderMap,
    Json(request): Json<Value>,
) -> Result<Response, HttpError> {
    authorize(&state, &headers)?;
    validate_origin(&state, &headers)?;
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let mut new_session = None;
    let response_protocol = if method == "initialize" {
        let protocol = match request
            .pointer("/params/protocolVersion")
            .and_then(Value::as_str)
        {
            Some(crate::MCP_EXPERIMENTAL_VERSION) => crate::MCP_EXPERIMENTAL_VERSION,
            _ => MCP_STABLE_VERSION,
        }
        .to_string();
        let session_id = Uuid::now_v7().to_string();
        let mut sessions = state.sessions.write().await;
        sessions.retain(|_, record| record.created_at.elapsed() <= SESSION_TTL);
        if sessions.len() >= MAX_ACTIVE_SESSIONS {
            return Err(HttpError {
                status: StatusCode::TOO_MANY_REQUESTS,
                message: "maximum active MCP sessions reached".to_string(),
            });
        }
        sessions.insert(
            session_id.clone(),
            SessionRecord {
                protocol_version: protocol.clone(),
                created_at: Instant::now(),
            },
        );
        drop(sessions);
        new_session = Some(session_id);
        protocol
    } else {
        require_session(&state, &headers).await?.protocol_version
    };

    let Some(response_body) = state.server.handle_json_rpc_async(&request).await else {
        let mut response = StatusCode::ACCEPTED.into_response();
        response.headers_mut().insert(
            HeaderName::from_static(PROTOCOL_HEADER),
            HeaderValue::from_str(&response_protocol).map_err(|error| HttpError {
                status: StatusCode::INTERNAL_SERVER_ERROR,
                message: error.to_string(),
            })?,
        );
        return Ok(response);
    };
    let mut response = Json(response_body).into_response();
    if let Some(session_id) = new_session {
        response.headers_mut().insert(
            HeaderName::from_static(SESSION_HEADER),
            HeaderValue::from_str(&session_id).map_err(|error| HttpError {
                status: StatusCode::INTERNAL_SERVER_ERROR,
                message: error.to_string(),
            })?,
        );
    }
    response.headers_mut().insert(
        HeaderName::from_static(PROTOCOL_HEADER),
        HeaderValue::from_str(&response_protocol).map_err(|error| HttpError {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: error.to_string(),
        })?,
    );
    Ok(response)
}

async fn get_mcp(
    State(state): State<HttpState>,
    headers: HeaderMap,
) -> Result<Response, HttpError> {
    authorize(&state, &headers)?;
    validate_origin(&state, &headers)?;
    require_session(&state, &headers).await?;
    Ok((
        StatusCode::METHOD_NOT_ALLOWED,
        Json(json!({
            "error":"This Soleaux development build supports Streamable HTTP POST and DELETE. A server-initiated SSE channel is not enabled."
        })),
    )
        .into_response())
}

async fn delete_mcp(
    State(state): State<HttpState>,
    headers: HeaderMap,
) -> Result<Response, HttpError> {
    authorize(&state, &headers)?;
    validate_origin(&state, &headers)?;
    let session_id = session_id(&headers)?;
    let removed = state.sessions.write().await.remove(&session_id).is_some();
    if !removed {
        return Err(HttpError {
            status: StatusCode::NOT_FOUND,
            message: "unknown MCP session".to_string(),
        });
    }
    Ok(StatusCode::NO_CONTENT.into_response())
}

fn authorize(state: &HttpState, headers: &HeaderMap) -> Result<(), HttpError> {
    let supplied = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .ok_or_else(|| HttpError {
            status: StatusCode::UNAUTHORIZED,
            message: "missing bearer token".to_string(),
        })?;
    let expected = state.bearer_token.as_bytes();
    let supplied = supplied.as_bytes();
    let valid = expected.len() == supplied.len() && bool::from(expected.ct_eq(supplied));
    if !valid {
        return Err(HttpError {
            status: StatusCode::UNAUTHORIZED,
            message: "invalid bearer token".to_string(),
        });
    }
    Ok(())
}

fn validate_origin(state: &HttpState, headers: &HeaderMap) -> Result<(), HttpError> {
    let Some(origin) = headers.get(header::ORIGIN) else {
        return Ok(());
    };
    let origin = origin.to_str().map_err(|_| HttpError {
        status: StatusCode::FORBIDDEN,
        message: "invalid Origin header".to_string(),
    })?;
    if !state.allowed_origins.contains(origin) {
        return Err(HttpError {
            status: StatusCode::FORBIDDEN,
            message: "Origin is not permitted".to_string(),
        });
    }
    Ok(())
}

async fn require_session(
    state: &HttpState,
    headers: &HeaderMap,
) -> Result<SessionRecord, HttpError> {
    let session_id = session_id(headers)?;
    let mut sessions = state.sessions.write().await;
    let record = sessions
        .get(&session_id)
        .cloned()
        .ok_or_else(|| HttpError {
            status: StatusCode::NOT_FOUND,
            message: "unknown MCP session".to_string(),
        })?;
    if record.created_at.elapsed() > SESSION_TTL {
        sessions.remove(&session_id);
        return Err(HttpError {
            status: StatusCode::GONE,
            message: "MCP session expired".to_string(),
        });
    }
    drop(sessions);
    let protocol = headers
        .get(HeaderName::from_static(PROTOCOL_HEADER))
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| HttpError {
            status: StatusCode::BAD_REQUEST,
            message: "missing MCP-Protocol-Version header".to_string(),
        })?;
    if protocol != record.protocol_version {
        return Err(HttpError {
            status: StatusCode::BAD_REQUEST,
            message: "MCP protocol version does not match the initialized session".to_string(),
        });
    }
    Ok(record)
}

fn session_id(headers: &HeaderMap) -> Result<String, HttpError> {
    headers
        .get(HeaderName::from_static(SESSION_HEADER))
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| HttpError {
            status: StatusCode::BAD_REQUEST,
            message: "missing Mcp-Session-Id header".to_string(),
        })
}
