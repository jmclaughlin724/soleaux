//! Persistent SSE cursor reconciliation over [`soleaux_state::StateStore`].
//!
//! The OpenCode bus carries an `evt_…` identity per event but offers no
//! replay: a dropped `/event` connection loses whatever was published while
//! disconnected. The reconciler therefore persists two facts in the daemon's
//! `AdapterCursor` table — the last observed event identity and a session
//! `time.updated` watermark — and closes reconnect gaps by comparing a fresh
//! session listing against the persisted watermark instead of trusting the
//! stream to be complete. Cursor writes use the store's optimistic revisions,
//! so two writers on one scope surface as a conflict, never a silent
//! overwrite.

use crate::types::{Event, Session};
use anyhow::{Context, Result, bail};
use serde_json::json;
use soleaux_state::{AdapterCursorInput, StateStore};
use url::Url;

/// `AdapterCursor.adapter` value for every scope this crate persists.
pub const OPENCODE_CURSOR_ADAPTER: &str = "opencode";
const CURSOR_METADATA_SCHEMA_VERSION: &str = "soleaux.opencode-cursor/v1";

/// Canonical cursor scope for one server authority and optional project
/// directory.
pub fn cursor_scope(base: &Url, directory: Option<&str>) -> String {
    let host = base.host_str().unwrap_or("localhost");
    let port = base.port_or_known_default().unwrap_or(80);
    match directory {
        Some(directory) => format!("{host}:{port}#{directory}"),
        None => format!("{host}:{port}"),
    }
}

/// Reconnect outcome: which sessions changed while the stream was down.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReconciliationReport {
    pub scope: String,
    /// The `server.connected` event identity the stream resumed at.
    pub resumed_at_event: String,
    pub previous_watermark_unix_ms: i64,
    pub watermark_unix_ms: i64,
    /// Sessions whose `time.updated` passed the persisted watermark while
    /// disconnected, sorted by identity.
    pub drifted_session_ids: Vec<String>,
}

/// Durable event cursor for one `(server, directory)` scope.
pub struct EventReconciler {
    state: StateStore,
    scope: String,
    last_event_id: Option<String>,
    watermark_unix_ms: i64,
    generation: u64,
    revision: Option<u64>,
}

impl EventReconciler {
    /// Load the persisted cursor for `scope`, or start empty.
    pub fn load(state: StateStore, scope: impl Into<String>) -> Result<Self> {
        let scope = scope.into();
        if scope.trim().is_empty() {
            bail!("cursor scope is empty");
        }
        let persisted = state.adapter_cursor(OPENCODE_CURSOR_ADAPTER, &scope)?;
        let (last_event_id, watermark_unix_ms, generation, revision) = match persisted {
            Some(record) => {
                let watermark = record
                    .watermark
                    .as_deref()
                    .map(|value| {
                        value.parse::<i64>().with_context(|| {
                            format!("persisted opencode watermark {value:?} is not an integer")
                        })
                    })
                    .transpose()?
                    .unwrap_or(0);
                let generation = record
                    .metadata
                    .get("generation")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0);
                (
                    Some(record.cursor),
                    watermark,
                    generation,
                    Some(record.revision),
                )
            }
            None => (None, 0, 0, None),
        };
        Ok(Self {
            state,
            scope,
            last_event_id,
            watermark_unix_ms,
            generation,
            revision,
        })
    }

    pub fn scope(&self) -> &str {
        &self.scope
    }

    /// Last observed `evt_…` identity, in memory or restored from the store.
    pub fn last_event_id(&self) -> Option<&str> {
        self.last_event_id.as_deref()
    }

    pub fn watermark_unix_ms(&self) -> i64 {
        self.watermark_unix_ms
    }

    /// Stream (re)connections observed across the cursor's lifetime.
    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// Record one stream event. Session lifecycle, permission, and connection
    /// events persist immediately; high-frequency delta kinds only advance
    /// the in-memory cursor until the next durable event or [`Self::flush`].
    /// Returns whether this observation was persisted.
    pub fn observe(&mut self, event: &Event) -> Result<bool> {
        if let Some(event_id) = event.event_id() {
            self.last_event_id = Some(event_id.to_string());
        }
        match event {
            Event::ServerConnected { .. } => {
                self.generation = self.generation.saturating_add(1);
            }
            Event::SessionCreated { session, .. }
            | Event::SessionUpdated { session, .. }
            | Event::SessionDeleted { session, .. } => {
                self.watermark_unix_ms = self.watermark_unix_ms.max(session.time.updated);
            }
            _ => {}
        }
        if durable(event) {
            self.persist(event.event_type())?;
            return Ok(true);
        }
        Ok(false)
    }

    /// Close a reconnect gap: compare a fresh session listing against the
    /// persisted watermark, advance it, and persist. Must follow at least one
    /// observed event — the server sends `server.connected` first, so a
    /// connected stream always satisfies this.
    pub fn reconcile(&mut self, sessions: &[Session]) -> Result<ReconciliationReport> {
        let Some(resumed_at_event) = self.last_event_id.clone() else {
            bail!("reconciliation requires an observed event; subscribe before reconciling");
        };
        let previous_watermark_unix_ms = self.watermark_unix_ms;
        let mut drifted: Vec<String> = sessions
            .iter()
            .filter(|session| session.time.updated > previous_watermark_unix_ms)
            .map(|session| session.id.clone())
            .collect();
        drifted.sort();
        for session in sessions {
            self.watermark_unix_ms = self.watermark_unix_ms.max(session.time.updated);
        }
        self.persist("reconcile")?;
        Ok(ReconciliationReport {
            scope: self.scope.clone(),
            resumed_at_event,
            previous_watermark_unix_ms,
            watermark_unix_ms: self.watermark_unix_ms,
            drifted_session_ids: drifted,
        })
    }

    /// Persist the in-memory cursor unconditionally.
    pub fn flush(&mut self) -> Result<()> {
        self.persist("flush")
    }

    fn persist(&mut self, reason: &str) -> Result<()> {
        let Some(cursor) = self.last_event_id.clone() else {
            bail!("cursor persistence requires an observed event");
        };
        let input = AdapterCursorInput {
            adapter: OPENCODE_CURSOR_ADAPTER.to_string(),
            scope: self.scope.clone(),
            cursor,
            etag: None,
            watermark: Some(self.watermark_unix_ms.to_string()),
            expected_revision: self.revision,
            metadata: json!({
                "schemaVersion": CURSOR_METADATA_SCHEMA_VERSION,
                "generation": self.generation,
                "reason": reason,
            }),
        };
        match self.state.put_adapter_cursor(input.clone()) {
            Ok(record) => {
                self.revision = Some(record.revision);
                Ok(())
            }
            Err(error) => {
                // Retry exactly once, and only when the stored revision
                // demonstrably moved past ours — a concurrent writer on this
                // scope. Every other failure propagates untouched.
                let current = match self
                    .state
                    .adapter_cursor(OPENCODE_CURSOR_ADAPTER, &self.scope)
                {
                    Ok(Some(current)) if Some(current.revision) != self.revision => current,
                    _ => return Err(error),
                };
                let retry = AdapterCursorInput {
                    expected_revision: Some(current.revision),
                    ..input
                };
                let record = self
                    .state
                    .put_adapter_cursor(retry)
                    .context("persisting the opencode cursor after a revision conflict")?;
                self.revision = Some(record.revision);
                Ok(())
            }
        }
    }
}

fn durable(event: &Event) -> bool {
    matches!(
        event,
        Event::ServerConnected { .. }
            | Event::SessionCreated { .. }
            | Event::SessionUpdated { .. }
            | Event::SessionDeleted { .. }
            | Event::SessionIdle { .. }
            | Event::SessionCompacted { .. }
            | Event::SessionError { .. }
            | Event::PermissionAsked { .. }
            | Event::PermissionReplied { .. }
            | Event::PluginAdded { .. }
    )
}
