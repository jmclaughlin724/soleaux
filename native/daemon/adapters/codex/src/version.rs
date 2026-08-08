//! Pinned-version policy and safe-mode evaluation for the Codex adapter.
//!
//! `AGENTS.md` hard stop 9 forbids using an unknown adapter version in a
//! mutating mode without a passing capability probe. Mode evaluation therefore
//! fails closed: mutating mode requires the exact capability-matrix version
//! and a well-formed probe-evidence digest, and a client downgrades itself
//! permanently when the live server contradicts the probed version.

use crate::CodexClientError;
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{io::AsyncReadExt, process::Command, time::timeout};

/// The exact Codex version pinned by `native/contracts/client-capability-matrix-v1.json`.
pub const PINNED_CODEX_VERSION: &str = "0.146.1";

/// Capability-matrix platform id, and the `AdapterCursor` adapter key.
pub const CODEX_ADAPTER_ID: &str = "codex";

const VERSION_PROBE_TIMEOUT: Duration = Duration::from_secs(10);
const VERSION_PROBE_MAX_OUTPUT_BYTES: usize = 64 * 1024;

/// The write posture of one Codex adapter instance.
///
/// `Mutating` is only constructible through [`evaluate_adapter_mode`], so a
/// mode value always carries the evidence that justified it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AdapterMode {
    /// Safe mode: only the read-only method allow list may be issued, and
    /// approval requests are answered with `cancel`.
    ReadOnly { reason: String },
    /// Mutating mode: the exact pinned version was probed and the capability
    /// probe evidence digest is recorded.
    Mutating { probe_evidence_sha256: String },
}

impl AdapterMode {
    pub fn is_mutating(&self) -> bool {
        matches!(self, Self::Mutating { .. })
    }
}

/// Evaluate the adapter mode from probe results, failing closed.
pub fn evaluate_adapter_mode(
    probed_version: Option<&str>,
    probe_evidence_sha256: Option<&str>,
) -> AdapterMode {
    let Some(version) = probed_version else {
        return AdapterMode::ReadOnly {
            reason: "the Codex version has not been probed".to_string(),
        };
    };
    if version != PINNED_CODEX_VERSION {
        return AdapterMode::ReadOnly {
            reason: format!(
                "Codex version {version} is not the pinned capability-matrix version {PINNED_CODEX_VERSION}"
            ),
        };
    }
    let Some(evidence) = probe_evidence_sha256 else {
        return AdapterMode::ReadOnly {
            reason: "mutating mode requires capability probe evidence".to_string(),
        };
    };
    if !is_lower_hex_digest(evidence) {
        return AdapterMode::ReadOnly {
            reason: "capability probe evidence digest is malformed".to_string(),
        };
    }
    AdapterMode::Mutating {
        probe_evidence_sha256: evidence.to_string(),
    }
}

/// Extract the version from `codex --version` output, e.g. `codex-cli 0.146.1`.
///
/// Returns `None` for any shape that is not a trailing dotted-numeric version
/// token on the first line; the caller then stays in safe mode.
pub fn parse_version_output(stdout: &str) -> Option<String> {
    let first_line = stdout.lines().next()?.trim();
    let token = first_line.rsplit(char::is_whitespace).next()?;
    let mut parts = 0usize;
    for part in token.split('.') {
        if part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        parts += 1;
    }
    (parts == 3).then(|| token.to_string())
}

/// Run `<binary> --version` and parse the reported version.
pub async fn probe_binary_version(binary: &Path) -> Result<String, CodexClientError> {
    let mut child = Command::new(binary)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| CodexClientError::Spawn(format!("spawning version probe: {error}")))?;
    let mut stdout = child.stdout.take().ok_or_else(|| {
        CodexClientError::Spawn("version probe did not expose stdout".to_string())
    })?;
    let mut output = Vec::new();
    let read = async {
        let mut buffer = [0u8; 4096];
        loop {
            let bytes = stdout.read(&mut buffer).await.map_err(|error| {
                CodexClientError::Spawn(format!("reading version probe output: {error}"))
            })?;
            if bytes == 0 {
                break;
            }
            if output.len() + bytes > VERSION_PROBE_MAX_OUTPUT_BYTES {
                return Err(CodexClientError::Protocol(
                    "version probe output exceeded the output bound".to_string(),
                ));
            }
            output.extend_from_slice(&buffer[..bytes]);
        }
        Ok(())
    };
    timeout(VERSION_PROBE_TIMEOUT, read)
        .await
        .map_err(|_| CodexClientError::Timeout {
            method: "--version".to_string(),
        })??;
    let text = String::from_utf8_lossy(&output);
    parse_version_output(&text).ok_or_else(|| {
        CodexClientError::Protocol(format!(
            "version probe output {:?} does not end in a dotted version",
            text.trim()
        ))
    })
}

pub(crate) fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}
