//! Durable adapter cursors for Codex threads, backed by `AdapterCursor`
//! persistence in canonical state.
//!
//! Cursors record the last durably observed position per scope so a
//! reconnected client resumes reconciliation instead of replaying from the
//! beginning. Writes use the store's optimistic revisions; a concurrent writer
//! surfaces as a bounded retry, never a silent overwrite.

use crate::version::CODEX_ADAPTER_ID;
use anyhow::{Context, Result, bail};
use serde_json::Value;
use soleaux_state::{AdapterCursorInput, AdapterCursorRecord, StateStore};

const REVISION_CONFLICT_RETRIES: usize = 3;

/// Scope key for one Codex thread.
pub fn thread_scope(thread_id: &str) -> String {
    format!("thread:{thread_id}")
}

/// Scope key for thread-list enumeration reconciliation.
pub const THREAD_LIST_SCOPE: &str = "thread-list";

#[derive(Debug, Clone)]
pub struct CodexCursorStore {
    store: StateStore,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CursorUpdate {
    pub scope: String,
    pub cursor: String,
    pub watermark: Option<String>,
    pub metadata: Value,
}

impl CodexCursorStore {
    pub fn new(store: StateStore) -> Self {
        Self { store }
    }

    pub fn get(&self, scope: &str) -> Result<Option<AdapterCursorRecord>> {
        self.store.adapter_cursor(CODEX_ADAPTER_ID, scope)
    }

    /// Write one cursor position, merging object metadata over any existing
    /// object metadata so markers such as `archived` accumulate.
    pub fn advance(&self, update: &CursorUpdate) -> Result<AdapterCursorRecord> {
        for _ in 0..REVISION_CONFLICT_RETRIES {
            let existing = self.get(&update.scope)?;
            let metadata = match (&existing, &update.metadata) {
                (Some(existing), Value::Object(incoming)) => {
                    if let Value::Object(current) = &existing.metadata {
                        let mut merged = current.clone();
                        merged.extend(incoming.clone());
                        Value::Object(merged)
                    } else {
                        update.metadata.clone()
                    }
                }
                _ => update.metadata.clone(),
            };
            let input = AdapterCursorInput {
                adapter: CODEX_ADAPTER_ID.to_string(),
                scope: update.scope.clone(),
                cursor: update.cursor.clone(),
                etag: None,
                watermark: update.watermark.clone(),
                expected_revision: existing.as_ref().map(|record| record.revision),
                metadata,
            };
            match self.store.put_adapter_cursor(input) {
                Ok(record) => return Ok(record),
                Err(error) => {
                    let detail = format!("{error:#}");
                    if !detail.contains("revision conflict") {
                        return Err(error).context("writing the Codex adapter cursor");
                    }
                }
            }
        }
        bail!(
            "adapter cursor scope {} kept conflicting after {REVISION_CONFLICT_RETRIES} retries",
            update.scope
        );
    }

    /// Record archive state on a thread scope. Without an existing cursor
    /// there is no observed position to preserve, so nothing is written.
    pub fn mark_archived(&self, thread_id: &str) -> Result<Option<AdapterCursorRecord>> {
        let scope = thread_scope(thread_id);
        let Some(existing) = self.get(&scope)? else {
            return Ok(None);
        };
        self.advance(&CursorUpdate {
            scope,
            cursor: existing.cursor,
            watermark: existing.watermark,
            metadata: serde_json::json!({"archived": true}),
        })
        .map(Some)
    }
}
