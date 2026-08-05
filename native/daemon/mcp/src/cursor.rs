//! Opaque, request-bound continuation cursors for bounded native reads.

use anyhow::{Context, Result, bail};
use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

const CURSOR_PREFIX: &str = "sxc1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContinuationState {
    pub phase: String,
    pub offset: usize,
}

pub fn request_fingerprint<T: Serialize>(
    kind: &str,
    workspace_id: Uuid,
    request: &T,
) -> Result<String> {
    let encoded = serde_json::to_vec(&(kind, workspace_id.to_string(), request))
        .context("serializing continuation request fingerprint")?;
    Ok(sha256_hex(&encoded))
}

pub fn encode_cursor(
    kind: &str,
    fingerprint: &str,
    snapshot_id: &str,
    phase: &str,
    offset: usize,
) -> String {
    let payload = format!("{CURSOR_PREFIX}:{kind}:{phase}:{offset}:{fingerprint}:{snapshot_id}");
    let checksum = sha256_hex(payload.as_bytes());
    format!("{payload}:{checksum}")
}

pub fn decode_cursor(
    cursor: Option<&str>,
    expected_kind: &str,
    expected_fingerprint: &str,
    expected_snapshot_id: &str,
    initial_phase: &str,
) -> Result<ContinuationState> {
    let Some(cursor) = cursor else {
        return Ok(ContinuationState {
            phase: initial_phase.to_string(),
            offset: 0,
        });
    };
    let parts = cursor.split(':').collect::<Vec<_>>();
    if parts.len() != 7 || parts[0] != CURSOR_PREFIX {
        bail!("continuation cursor has invalid framing");
    }
    let payload = parts[..6].join(":");
    let expected_checksum = sha256_hex(payload.as_bytes());
    if parts[6] != expected_checksum {
        bail!("continuation cursor checksum is invalid");
    }
    if parts[1] != expected_kind {
        bail!("continuation cursor belongs to a different tool");
    }
    if parts[4] != expected_fingerprint {
        bail!("continuation cursor does not match the current request");
    }
    if parts[5] != expected_snapshot_id {
        bail!("continuation cursor snapshot is stale; restart the bounded read");
    }
    let offset = parts[3]
        .parse::<usize>()
        .context("continuation cursor offset is invalid")?;
    Ok(ContinuationState {
        phase: parts[2].to_string(),
        offset,
    })
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_round_trip_is_bound_to_request_and_snapshot() {
        let workspace_id = Uuid::nil();
        let fingerprint = request_fingerprint(
            "code-search",
            workspace_id,
            &serde_json::json!({"query":"needle","paths":["src"]}),
        )
        .expect("fingerprint");
        let cursor = encode_cursor("code-search", &fingerprint, "snapshot-a", "text", 42);
        assert_eq!(
            decode_cursor(
                Some(&cursor),
                "code-search",
                &fingerprint,
                "snapshot-a",
                "structural",
            )
            .expect("decode"),
            ContinuationState {
                phase: "text".to_string(),
                offset: 42,
            }
        );
        assert!(
            decode_cursor(
                Some(&cursor),
                "code-search",
                &fingerprint,
                "snapshot-b",
                "structural",
            )
            .is_err()
        );
    }
}
