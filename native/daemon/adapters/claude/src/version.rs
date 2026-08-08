//! Pinned-version policy for the Claude Code SDK host.
//!
//! `AGENTS.md` hard stop 9 forbids using an unknown adapter version in a
//! mutating mode without a passing capability probe. The harness reports the
//! SDK version it actually loaded in its `hello` frame; anything other than
//! the exact pinned capability-matrix version keeps the host in read-only
//! safe mode for that connection, and a missing report fails closed the same
//! way.

/// The exact Claude Code version pinned by
/// `native/contracts/client-capability-matrix-v1.json`; a test enforces the
/// pairing. The harness must load an SDK reporting this version for the host
/// to leave read-only safe mode.
pub const PINNED_CLAUDE_CODE_VERSION: &str = "2.1.223";

/// The safe-mode refusal reason for a reported SDK version, or `None` when
/// the exact pinned version was reported.
pub fn sdk_version_refusal(reported: Option<&str>) -> Option<String> {
    match reported {
        None => Some("the harness did not report an SDK version".to_string()),
        Some(version) if version == PINNED_CLAUDE_CODE_VERSION => None,
        Some(version) => Some(format!(
            "SDK version {version} is not the pinned capability-matrix version {PINNED_CLAUDE_CODE_VERSION}"
        )),
    }
}
