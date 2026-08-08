//! Claude Desktop adapter: user-authorized export/import and supported
//! local-connector materialization only.
//!
//! The capability matrix pins Claude Desktop as a documentation-contract
//! surface (`writePolicy: read_only_documented_surface`), so this adapter is
//! permanently read-only toward the vendor: hosted session or memory CRUD is
//! a non-goal forever, and no admission receipt path applies because the
//! adapter never writes vendor state. It never opens, locks, or writes
//! Desktop's own databases or configuration stores — a test enforces that no
//! code path here takes a Desktop store location.
//!
//! Three supported workflows, all user-driven:
//!
//! - Import: parse a user-provided account-data export
//!   (`conversations.json`) and create canonical sessions, turns, and
//!   messages through native-identity upserts. Each import creates a NEW
//!   canonical session with its Desktop origin recorded — never a false
//!   native resume (GAP-016) — and re-imports replay instead of duplicating.
//!   Import is all-or-nothing per conversation: a malformed conversation is
//!   refused with a typed error before any write.
//! - Export: read one canonical session and render a Desktop-shaped
//!   conversations document to a user-authorized file the user takes to
//!   Desktop through its supported flows.
//! - Local connector: materialize the `mcpServers` configuration snippet the
//!   user applies through Desktop's own supported configuration flow.

mod adapter;
mod connector;
mod export;
mod files;
mod import;
mod types;

pub use adapter::ClaudeDesktopAdapter;
pub use connector::{local_connector_materialization, soleaux_local_connector};
pub use export::format_unix_ms_utc;
pub use files::{read_export_file, write_export_file};
pub use import::{
    IMPORT_METADATA_SCHEMA_VERSION, IMPORT_ORIGIN, ImportReport, ImportedConversation,
    RefusedConversation,
};
pub use types::{
    DesktopAdapterError, DesktopChatMessage, DesktopConversation, DesktopSender, ParsedEntry,
    ParsedExport, parse_export, render_conversations,
};

/// Capability-matrix platform identifier for Claude Desktop.
pub const CLAUDE_DESKTOP_PLATFORM_ID: &str = "claude_desktop";

/// The single matrix version for Claude Desktop. The surface is
/// documentation-pinned rather than binary-pinned, so the matrix tracks the
/// supported current release instead of an exact number; a test enforces the
/// pairing and that the entry stays `mutationEligible=false`.
pub const MATRIX_VERSION: &str = "supported-current";

/// The matrix write policy this adapter is built against. Permanent: the
/// documented Desktop surface offers no supported hosted CRUD to write
/// through, so there is no receipt-admitted write mode to grow into.
pub const WRITE_POLICY: &str = "read_only_documented_surface";

#[cfg(test)]
mod tests;
