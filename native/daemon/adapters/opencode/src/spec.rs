//! Vendored OpenCode OpenAPI 3.1 document, pinned by content digest.
//!
//! The build never fetches the spec: `contracts/opencode-openapi-1.18.14.json`
//! is byte-identical to `packages/sdk/openapi.json` at the upstream release
//! tag `v1.18.14` and is re-fetchable only through
//! `native/scripts/regenerate_opencode_openapi_spec.py`, which re-verifies the
//! digest below. Loading fails closed when the on-disk bytes do not hash to
//! the pinned digest.

use anyhow::{Context, Result, bail};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::PathBuf;

/// SHA-256 of the vendored spec bytes.
pub const OPENCODE_OPENAPI_SHA256: &str =
    "5bbd6493a1a488ef4294889341c896e420f814ecea95822100aaa9f3f95ab2d1";

/// Upstream commit the spec bytes were taken from (`anomalyco/opencode` tag
/// `v1.18.14`, path `packages/sdk/openapi.json`).
pub const OPENCODE_SPEC_SOURCE_COMMIT: &str = "65cf14df16c191f3e9684f0d9a8bae69103ced6d";

const VENDORED_SPEC_RELATIVE: &str = "contracts/opencode-openapi-1.18.14.json";

/// Absolute path of the vendored spec inside this crate's source tree. Valid
/// wherever the crate sources are present (tests, regeneration checks); the
/// runtime client never needs the file.
pub fn vendored_spec_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(VENDORED_SPEC_RELATIVE)
}

/// Read, digest-verify, and parse the vendored spec.
pub fn load_vendored_spec() -> Result<Value> {
    let path = vendored_spec_path();
    let bytes = std::fs::read(&path)
        .with_context(|| format!("reading the vendored OpenCode spec at {}", path.display()))?;
    let digest = Sha256::digest(&bytes);
    let digest = format!("{digest:x}");
    if digest != OPENCODE_OPENAPI_SHA256 {
        bail!(
            "vendored OpenCode spec digest mismatch: expected {OPENCODE_OPENAPI_SHA256}, found {digest}"
        );
    }
    serde_json::from_slice(&bytes).context("parsing the vendored OpenCode spec")
}
