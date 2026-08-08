//! Typed OpenCode adapter pinned to the capability-matrix version `1.18.14`.
//!
//! The adapter speaks only the documented HTTP/OpenAPI/SSE surface of a local
//! `opencode serve` instance; it never reads or writes OpenCode's on-disk
//! stores. Every request/response type is hand-derived from the vendored
//! OpenAPI document in `contracts/` and conformance-tested against it, so type
//! drift fails the build's test gate rather than a live call. The adapter
//! starts read-only and enters write mode only when the probed server version
//! equals the pinned matrix version and a daemon-issued admission receipt
//! verifies (P5-V1); anything else stays in read-only safe mode.

mod adapter;
mod client;
mod events;
mod http;
mod spec;
mod types;

pub use adapter::{
    AdapterError, AdmissionVerifier, IpcAdmissionVerifier, OpencodeAdapter, WriteMode,
};
pub use client::OpencodeClient;
pub use events::{EventReconciler, OPENCODE_CURSOR_ADAPTER, ReconciliationReport, cursor_scope};
pub use http::{SseFrame, SseStream};
pub use spec::{
    OPENCODE_OPENAPI_SHA256, OPENCODE_SPEC_SOURCE_COMMIT, load_vendored_spec, vendored_spec_path,
};
pub use types::{
    CreateSessionRequest, Event, GlobalEvent, HealthInfo, MessageEnvelope, MessageInfo,
    OpencodeConfig, Part, PermissionReply, PermissionRequest, PluginSpec, RevertRequest, Session,
    SessionRevert, SessionTime, SummarizeRequest,
};

/// Capability-matrix platform identifier for OpenCode.
pub const OPENCODE_PLATFORM_ID: &str = "opencode";

/// The exact OpenCode server version this adapter is built and verified
/// against. Must equal the single `opencode` version in
/// `native/contracts/client-capability-matrix-v1.json`; a test enforces the
/// pairing. Any other probed server version keeps the adapter in read-only
/// safe mode.
pub const PINNED_OPENCODE_VERSION: &str = "1.18.14";

#[cfg(test)]
mod tests;
