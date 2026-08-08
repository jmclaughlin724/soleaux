//! Schema-derived Codex app-server client for the Soleaux daemon (P5-016).
//!
//! Drives a pinned `codex app-server` process over newline-delimited JSON-RPC
//! on stdio: thread lifecycle, turns, steering, compaction, archive, approval
//! round-trips, durable `AdapterCursor` positions, and supervised reconnect.
//! The wire types are hand-derived from the JSON Schemas vendored under
//! `schema/json` at tag `rust-v0.146.1` and are validated against those
//! schemas in tests; `schema/MANIFEST.json` records the digest of every
//! vendored file.
//!
//! Safety posture: an unprobed or non-pinned Codex version runs in safe mode,
//! where every mutating method is refused locally and approvals are answered
//! `cancel` (`AGENTS.md` hard stop 9). Vendor-native stores are never written
//! directly — every operation goes through the documented app-server API, and
//! a Soleaux workspace stays read-only to external clients regardless of this
//! adapter's mode until a daemon-issued admission receipt verifies.

mod client;
mod cursors;
mod protocol;
mod vendored;
mod version;

pub use client::{
    BoxFuture, CodexClient, CodexClientConfig, CodexClientError, CodexConnection, CodexConnector,
    CodexEvent, PendingApproval, ProcessConnector, ReconnectPolicy,
};
pub use cursors::{CodexCursorStore, CursorUpdate, THREAD_LIST_SCOPE, thread_scope};
pub use protocol::{
    ApprovalDecision, ClientInfo, CodexNotification, CodexServerRequest,
    CommandExecutionApprovalParams, ErrorNotification, FileChangeApprovalParams,
    InitializeCapabilities, InitializeParams, InitializeResponse, ItemCompletedNotification,
    Thread, ThreadForkParams, ThreadListParams, ThreadListResponse, ThreadReadParams,
    ThreadResponse, ThreadResumeParams, ThreadStartParams, ThreadTurnRef, TurnInterruptParams,
    TurnNotification, TurnStartParams, TurnStartResponse, TurnStatus, TurnSteerParams,
    TurnSteerResponse, TurnSummary, UserInput, method_is_read_only,
};
pub use vendored::{
    VENDORED_SCHEMA_MANIFEST_JSON, VENDORED_SCHEMA_TAG, VendoredSchemaFile, VendoredSchemaManifest,
    vendored_schema_manifest,
};
pub use version::{
    AdapterMode, CODEX_ADAPTER_ID, PINNED_CODEX_VERSION, evaluate_adapter_mode,
    parse_version_output, probe_binary_version,
};

#[cfg(test)]
mod tests;
