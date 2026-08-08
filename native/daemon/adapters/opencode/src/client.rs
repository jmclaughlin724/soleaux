//! Typed client for the OpenCode HTTP surface this adapter uses.
//!
//! Each method maps one vendored-spec operation; the pairing (path, method,
//! operationId) is enforced by the conformance tests. The client carries no
//! write-mode policy — [`crate::OpencodeAdapter`] owns that gate.

use crate::http::{self, SseFrame, SseStream, validate_path_segment};
use crate::types::{
    CreateSessionRequest, Event, GlobalEvent, HealthInfo, MessageEnvelope, OpencodeConfig,
    PermissionReply, PermissionRequest, RevertRequest, Session, SummarizeRequest,
};
use anyhow::{Context, Result, bail};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::time::Duration;
use url::Url;
use url::form_urlencoded::Serializer;

const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// Typed loopback client for one `opencode serve` instance, optionally scoped
/// to one project directory (the server resolves relative state per request
/// through the documented `directory` query parameter).
#[derive(Debug, Clone)]
pub struct OpencodeClient {
    base: Url,
    directory: Option<String>,
    timeout: Duration,
}

impl OpencodeClient {
    /// Build a client for a loopback base URL such as `http://127.0.0.1:4096`.
    pub fn new(base: Url, directory: Option<String>) -> Result<Self> {
        http::loopback_authority(&base)?;
        Ok(Self {
            base,
            directory,
            timeout: DEFAULT_REQUEST_TIMEOUT,
        })
    }

    pub fn base(&self) -> &Url {
        &self.base
    }

    pub fn directory(&self) -> Option<&str> {
        self.directory.as_deref()
    }

    fn path(&self, segments: &[&str]) -> Result<String> {
        let mut path = String::new();
        for segment in segments {
            validate_path_segment(segment)?;
            path.push('/');
            path.push_str(segment);
        }
        let mut query = Serializer::new(String::new());
        if let Some(directory) = &self.directory {
            query.append_pair("directory", directory);
        }
        let query = query.finish();
        if !query.is_empty() {
            path.push('?');
            path.push_str(&query);
        }
        Ok(path)
    }

    async fn get_json<T: DeserializeOwned>(&self, segments: &[&str]) -> Result<T> {
        let path = self.path(segments)?;
        let (status, body) = http::request(&self.base, self.timeout, "GET", &path, None).await?;
        decode(status, &body, &path)
    }

    async fn post_json<T: DeserializeOwned>(
        &self,
        segments: &[&str],
        body: Option<&Value>,
    ) -> Result<T> {
        let path = self.path(segments)?;
        let (status, bytes) = http::request(&self.base, self.timeout, "POST", &path, body).await?;
        decode(status, &bytes, &path)
    }

    /// `GET /global/health` — the version probe every mode decision rests on.
    pub async fn health(&self) -> Result<HealthInfo> {
        let path = "/global/health";
        let (status, body) = http::request(&self.base, self.timeout, "GET", path, None).await?;
        decode(status, &body, path)
    }

    /// `GET /config` — configuration including the plugin roster.
    pub async fn config(&self) -> Result<OpencodeConfig> {
        self.get_json(&["config"]).await
    }

    /// `GET /session`.
    pub async fn list_sessions(&self) -> Result<Vec<Session>> {
        self.get_json(&["session"]).await
    }

    /// `GET /session/{sessionID}`.
    pub async fn get_session(&self, session_id: &str) -> Result<Session> {
        self.get_json(&["session", session_id]).await
    }

    /// `GET /session/{sessionID}/children`.
    pub async fn session_children(&self, session_id: &str) -> Result<Vec<Session>> {
        self.get_json(&["session", session_id, "children"]).await
    }

    /// `GET /session/{sessionID}/message`.
    pub async fn list_messages(&self, session_id: &str) -> Result<Vec<MessageEnvelope>> {
        self.get_json(&["session", session_id, "message"]).await
    }

    /// `GET /permission` — pending permission requests.
    pub async fn list_permissions(&self) -> Result<Vec<PermissionRequest>> {
        self.get_json(&["permission"]).await
    }

    /// `POST /session`.
    pub async fn create_session(&self, request: &CreateSessionRequest) -> Result<Session> {
        self.post_json(&["session"], Some(&serde_json::to_value(request)?))
            .await
    }

    /// `POST /session/{sessionID}/fork` — fork at an optional message.
    pub async fn fork_session(
        &self,
        session_id: &str,
        message_id: Option<&str>,
    ) -> Result<Session> {
        let body = match message_id {
            Some(message_id) => json!({"messageID": message_id}),
            None => json!({}),
        };
        self.post_json(&["session", session_id, "fork"], Some(&body))
            .await
    }

    /// `POST /session/{sessionID}/abort`.
    pub async fn abort_session(&self, session_id: &str) -> Result<bool> {
        self.post_json(&["session", session_id, "abort"], None)
            .await
    }

    /// `POST /session/{sessionID}/summarize`.
    pub async fn summarize_session(
        &self,
        session_id: &str,
        request: &SummarizeRequest,
    ) -> Result<bool> {
        self.post_json(
            &["session", session_id, "summarize"],
            Some(&serde_json::to_value(request)?),
        )
        .await
    }

    /// `POST /session/{sessionID}/revert`.
    pub async fn revert_session(
        &self,
        session_id: &str,
        request: &RevertRequest,
    ) -> Result<Session> {
        self.post_json(
            &["session", session_id, "revert"],
            Some(&serde_json::to_value(request)?),
        )
        .await
    }

    /// `POST /session/{sessionID}/unrevert`.
    pub async fn unrevert_session(&self, session_id: &str) -> Result<Session> {
        self.post_json(&["session", session_id, "unrevert"], None)
            .await
    }

    /// `POST /permission/{requestID}/reply`.
    pub async fn reply_permission(
        &self,
        request_id: &str,
        reply: PermissionReply,
        message: Option<&str>,
    ) -> Result<bool> {
        let mut body = json!({"reply": reply});
        if let Some(message) = message {
            body["message"] = Value::String(message.to_string());
        }
        self.post_json(&["permission", request_id, "reply"], Some(&body))
            .await
    }

    /// `POST /session/{sessionID}/permissions/{permissionID}`.
    pub async fn respond_session_permission(
        &self,
        session_id: &str,
        permission_id: &str,
        response: PermissionReply,
    ) -> Result<bool> {
        self.post_json(
            &["session", session_id, "permissions", permission_id],
            Some(&json!({"response": response})),
        )
        .await
    }

    /// `GET /event` — the per-instance bus. The server sends
    /// `server.connected` first, then bus events.
    pub async fn subscribe_events(&self) -> Result<EventStream> {
        let path = self.path(&["event"])?;
        let stream = SseStream::open(&self.base, &path).await?;
        Ok(EventStream { stream })
    }

    /// `GET /global/event` — the cross-project bus with origin scopes.
    pub async fn subscribe_global_events(&self) -> Result<GlobalEventStream> {
        let stream = SseStream::open(&self.base, "/global/event").await?;
        Ok(GlobalEventStream { stream })
    }
}

fn decode<T: DeserializeOwned>(status: u16, body: &[u8], path: &str) -> Result<T> {
    if !(200..300).contains(&status) {
        let detail = String::from_utf8_lossy(body);
        let detail = detail.trim();
        bail!(
            "opencode answered {status} for {path}: {}",
            &detail[..detail.len().min(512)]
        );
    }
    serde_json::from_slice(body)
        .with_context(|| format!("decoding the opencode response for {path}"))
}

/// Typed `/event` subscription.
pub struct EventStream {
    stream: SseStream,
}

impl EventStream {
    /// Next parsed event; `None` when the server closes the stream.
    pub async fn next_event(&mut self, timeout: Duration) -> Result<Option<Event>> {
        match next_data_frame(&mut self.stream, timeout).await? {
            Some(frame) => {
                let value: Value = serde_json::from_str(&frame.data)
                    .context("decoding a server-sent event payload")?;
                Ok(Some(Event::from_value(value)?))
            }
            None => Ok(None),
        }
    }
}

/// Typed `/global/event` subscription.
pub struct GlobalEventStream {
    stream: SseStream,
}

impl GlobalEventStream {
    pub async fn next_event(&mut self, timeout: Duration) -> Result<Option<GlobalEvent>> {
        match next_data_frame(&mut self.stream, timeout).await? {
            Some(frame) => {
                let value: Value = serde_json::from_str(&frame.data)
                    .context("decoding a global server-sent event payload")?;
                Ok(Some(GlobalEvent::from_value(value)?))
            }
            None => Ok(None),
        }
    }
}

async fn next_data_frame(stream: &mut SseStream, timeout: Duration) -> Result<Option<SseFrame>> {
    loop {
        match stream.next_frame_timeout(timeout).await? {
            Some(frame) if frame.data.is_empty() => continue,
            other => return Ok(other),
        }
    }
}
