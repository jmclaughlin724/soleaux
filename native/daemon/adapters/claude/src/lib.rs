//! Claude Code SDK execution host for the Soleaux daemon (P5-014).
//!
//! Hosts Claude Agent SDK sessions with the daemon as the external
//! `SessionStore`: a thin Node harness runs `query()` against the pinned SDK
//! and forwards every `SessionStore` call, hook event, permission request,
//! and system message (compaction boundaries, subagent transcripts,
//! `mirror_error`) to this host over newline-delimited JSON on stdio. The
//! store bridge writes each transcript entry through the daemon's canonical
//! session/turn/message entities, so resume, fork, and the post-compaction
//! resume view are served from canonical state, and restart reconciliation
//! converges `AdapterCursor` positions with what actually landed.
//!
//! Safety posture: an unprobed or non-pinned SDK version runs in read-only
//! safe mode, where session execution is refused locally and permission
//! requests are answered with the fail-closed deny (`AGENTS.md` hard stop 9).
//! Write admission additionally requires a daemon-issued admission receipt
//! that a daemon-trusted verifier accepts (P5-V1), re-checked before every
//! mutating operation. Vendor-native stores are never written: the SDK owns
//! its local JSONL mirror, and this crate touches only canonical state.

mod admission;
mod host;
mod protocol;
mod store;
mod version;

pub use admission::{AdmissionVerifier, IpcAdmissionVerifier};
pub use host::{
    BoxFuture, ClaudeHost, ClaudeHostConfig, ClaudeHostEvent, HarnessConnection, HarnessConnector,
    HostError, PendingPermission, PermissionDecision, ProcessConnector, ReconnectPolicy,
    SessionStartOutcome, WriteAuthority,
};
pub use protocol::{
    EventFrame, HOST_PROTOCOL_VERSION, HarnessFrame, HelloFrame, PermissionRequestFrame,
    ResponseFrame, StoreOp, StoreRequestFrame, encode_hello_ack, encode_permission_decision,
    encode_request, encode_store_result, parse_harness_frame,
};
pub use store::{
    AppendOutcome, CLAUDE_CURSOR_ADAPTER, ClaudeSessionStore, ForkOutcome, MAX_ENTRY_BYTES,
    ReconcileEntry, SessionKey, StoredSessionSummary, transcript_scope,
};
pub use version::{PINNED_CLAUDE_CODE_VERSION, sdk_version_refusal};

/// Capability-matrix platform identifier for Claude Code, and the
/// `AdapterCursor` adapter key.
pub const CLAUDE_PLATFORM_ID: &str = "claude_code";

#[cfg(test)]
mod tests;
