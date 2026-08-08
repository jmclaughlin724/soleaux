//! The vendored-schema manifest, embedded for evidence reporting.
//!
//! The schema files themselves are embedded only in test builds; the release
//! binary carries just this manifest. `scripts/refresh_vendored_schemas.py`
//! re-fetches the files at the pinned tag and verifies them against these
//! digests, so the build never depends on the network.

use anyhow::{Context, Result, bail};
use serde::Deserialize;

pub const VENDORED_SCHEMA_MANIFEST_JSON: &str = include_str!("../schema/MANIFEST.json");
pub const VENDORED_SCHEMA_TAG: &str = "rust-v0.146.1";

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VendoredSchemaManifest {
    pub schema_version: String,
    pub source: String,
    pub source_path: String,
    pub tag: String,
    pub pinned_codex_version: String,
    pub file_count: usize,
    pub files: Vec<VendoredSchemaFile>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VendoredSchemaFile {
    pub path: String,
    pub bytes: usize,
    pub sha256: String,
    pub upstream_git_blob_sha: String,
}

pub fn vendored_schema_manifest() -> Result<VendoredSchemaManifest> {
    let manifest: VendoredSchemaManifest = serde_json::from_str(VENDORED_SCHEMA_MANIFEST_JSON)
        .context("parsing the embedded vendored-schema manifest")?;
    if manifest.tag != VENDORED_SCHEMA_TAG {
        bail!(
            "vendored-schema manifest tag {} is not the pinned tag {VENDORED_SCHEMA_TAG}",
            manifest.tag
        );
    }
    if manifest.pinned_codex_version != crate::version::PINNED_CODEX_VERSION {
        bail!(
            "vendored-schema manifest pins Codex {} instead of {}",
            manifest.pinned_codex_version,
            crate::version::PINNED_CODEX_VERSION
        );
    }
    if manifest.file_count != manifest.files.len() {
        bail!(
            "vendored-schema manifest declares {} files but lists {}",
            manifest.file_count,
            manifest.files.len()
        );
    }
    Ok(manifest)
}
