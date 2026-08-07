//! P5-010 Turborepo and Next.js version-matrix contract.
//!
//! Mirrors the client-capability-matrix precedent: the contract is embedded,
//! schema-checked, and digest-addressable. Every version pin is backed by
//! evidence inside this repository (catalog pins, lockfile snapshots, and
//! workspace manifests) — never by a network probe. `documentedCliProbed`
//! stays `false` until P5-024 lands the documented CLI probe, and
//! `devtoolsIntegration` stays `false` until P5-025 lands the DevTools
//! integration.

use anyhow::{Context, Result, bail};
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::Path;

pub const TURBO_NEXT_MATRIX_SCHEMA_VERSION: &str = "soleaux.turbo-next-matrix/v1";
pub const TURBO_NEXT_MATRIX_JSON: &str =
    include_str!("../../../contracts/turbo-next-matrix-v1.json");

/// Route kinds the static Next.js provider emits (`nextjs::route_from_file`).
const NEXT_ROUTE_KINDS: [&str; 4] = ["page", "route_handler", "metadata_route", "api_route"];

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TurboNextMatrix {
    schema_version: String,
    as_of_date: String,
    task: String,
    version_evidence_policy: String,
    documented_cli_probed: bool,
    documented_cli_probe_task: String,
    devtools_integration: bool,
    devtools_integration_task: String,
    repository_layouts: Vec<RepositoryLayout>,
    tools: Vec<ToolMatrix>,
    validation_repositories: Vec<ValidationRepository>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RepositoryLayout {
    id: String,
    source: String,
    precedence: u8,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ToolMatrix {
    id: String,
    display_name: String,
    provider: String,
    version_policy: String,
    versions: Vec<ToolVersion>,
    #[serde(default)]
    route_kinds: Vec<RouteKindMatrix>,
    #[serde(default)]
    segment_normalization: Vec<Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ToolVersion {
    version: String,
    release_channel: String,
    major: u64,
    evidence: Vec<EvidenceReference>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct EvidenceReference {
    #[serde(rename = "type")]
    kind: String,
    path: String,
    key: String,
    #[serde(default)]
    integrity: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RouteKindMatrix {
    kind: String,
    detection: String,
    routers: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ValidationRepository {
    id: String,
    path: String,
    workspace_source: String,
    turbo_configuration: String,
    expected_workspace_globs: Vec<String>,
    expected_tasks: Vec<String>,
    next_applications: Vec<String>,
}

pub fn turbo_next_matrix_sha256() -> String {
    let digest = Sha256::digest(TURBO_NEXT_MATRIX_JSON.as_bytes());
    format!("{digest:x}")
}

pub fn validate_turbo_next_matrix() -> Result<()> {
    let matrix = load_matrix()?;
    validate_matrix(&matrix)
}

fn load_matrix() -> Result<TurboNextMatrix> {
    serde_json::from_str(TURBO_NEXT_MATRIX_JSON)
        .context("parsing the embedded Turborepo and Next.js version matrix")
}

fn validate_matrix(matrix: &TurboNextMatrix) -> Result<()> {
    if matrix.schema_version != TURBO_NEXT_MATRIX_SCHEMA_VERSION {
        bail!("unsupported Turborepo and Next.js matrix schema");
    }
    if matrix.documented_cli_probed {
        bail!(
            "the documented CLI probe is {}; the v1 matrix cannot claim it",
            matrix.documented_cli_probe_task
        );
    }
    if matrix.devtools_integration {
        bail!(
            "the DevTools integration is {}; the v1 matrix cannot claim it",
            matrix.devtools_integration_task
        );
    }
    if matrix.as_of_date.trim().is_empty()
        || matrix.task.trim().is_empty()
        || matrix.version_evidence_policy.trim().is_empty()
        || matrix.documented_cli_probe_task.trim().is_empty()
        || matrix.devtools_integration_task.trim().is_empty()
    {
        bail!("Turborepo and Next.js matrix metadata is incomplete");
    }

    if matrix.repository_layouts.is_empty() {
        bail!("Turborepo and Next.js matrix declares no repository layouts");
    }
    let mut layout_ids = BTreeSet::new();
    let mut layout_precedences = BTreeSet::new();
    for layout in &matrix.repository_layouts {
        if layout.id.trim().is_empty() || layout.source.trim().is_empty() {
            bail!("repository layout metadata is incomplete");
        }
        if !layout_ids.insert(layout.id.as_str()) {
            bail!("duplicate repository layout: {}", layout.id);
        }
        if !layout_precedences.insert(layout.precedence) {
            bail!(
                "duplicate repository layout precedence: {}",
                layout.precedence
            );
        }
    }

    let tool_ids = matrix
        .tools
        .iter()
        .map(|tool| tool.id.as_str())
        .collect::<BTreeSet<_>>();
    if tool_ids.len() != matrix.tools.len() || tool_ids != BTreeSet::from(["nextjs", "turborepo"]) {
        bail!("the v1 matrix must pin exactly the turborepo and nextjs tools");
    }
    for tool in &matrix.tools {
        if tool.display_name.trim().is_empty()
            || tool.provider.trim().is_empty()
            || tool.version_policy.trim().is_empty()
        {
            bail!("matrix tool metadata is incomplete for {}", tool.id);
        }
        if tool.versions.is_empty() {
            bail!("matrix tool {} pins no versions", tool.id);
        }
        let mut versions = BTreeSet::new();
        for version in &tool.versions {
            if version.version.trim().is_empty() || version.release_channel.trim().is_empty() {
                bail!("matrix version metadata is incomplete for {}", tool.id);
            }
            if !versions.insert(version.version.as_str()) {
                bail!("duplicate version {} for tool {}", version.version, tool.id);
            }
            if !version.version.starts_with(&format!("{}.", version.major)) {
                bail!(
                    "pinned version {} for tool {} does not match its declared major {}",
                    version.version,
                    tool.id,
                    version.major
                );
            }
            if version.evidence.is_empty() {
                bail!(
                    "matrix version {} for tool {} carries no repository evidence",
                    version.version,
                    tool.id
                );
            }
            for evidence in &version.evidence {
                if evidence.kind.trim().is_empty()
                    || evidence.path.trim().is_empty()
                    || evidence.key.trim().is_empty()
                {
                    bail!("matrix evidence metadata is incomplete for {}", tool.id);
                }
                if evidence.path.contains("://") || Path::new(&evidence.path).is_absolute() {
                    bail!(
                        "matrix evidence for {} must be repository-relative, never a network or absolute location: {}",
                        tool.id,
                        evidence.path
                    );
                }
                if let Some(integrity) = &evidence.integrity
                    && !integrity.starts_with("sha512-")
                {
                    bail!(
                        "matrix evidence integrity for {} must be a lockfile sha512 value",
                        tool.id
                    );
                }
            }
        }
    }

    let nextjs = matrix
        .tools
        .iter()
        .find(|tool| tool.id == "nextjs")
        .expect("tool id set was validated above");
    let route_kinds = nextjs
        .route_kinds
        .iter()
        .map(|route| route.kind.as_str())
        .collect::<BTreeSet<_>>();
    if route_kinds.len() != nextjs.route_kinds.len()
        || route_kinds != NEXT_ROUTE_KINDS.iter().copied().collect::<BTreeSet<_>>()
    {
        bail!("the Next.js matrix must cover exactly the static provider route kinds");
    }
    for route in &nextjs.route_kinds {
        if route.detection.trim().is_empty() || route.routers.is_empty() {
            bail!("Next.js route kind {} metadata is incomplete", route.kind);
        }
    }
    if nextjs.segment_normalization.is_empty() {
        bail!("the Next.js matrix must document segment normalization");
    }

    if matrix.validation_repositories.is_empty() {
        bail!("the matrix names no validation repositories");
    }
    let mut repository_ids = BTreeSet::new();
    for repository in &matrix.validation_repositories {
        if repository.path.trim().is_empty()
            || repository.workspace_source.trim().is_empty()
            || repository.turbo_configuration.trim().is_empty()
        {
            bail!("validation repository metadata is incomplete");
        }
        if !repository_ids.insert(repository.id.as_str()) {
            bail!("duplicate validation repository: {}", repository.id);
        }
        if repository.expected_workspace_globs.is_empty()
            || repository.expected_tasks.is_empty()
            || repository.next_applications.is_empty()
        {
            bail!(
                "validation repository {} declares no expectations",
                repository.id
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nextjs::index_nextjs;
    use crate::turborepo::{load_graph, pnpm_catalog_pin, pnpm_workspace_packages};
    use std::fs;
    use std::path::PathBuf;

    fn repository_root() -> PathBuf {
        fs::canonicalize(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."))
            .expect("repository root")
    }

    fn matrix() -> TurboNextMatrix {
        load_matrix().expect("embedded matrix parses")
    }

    fn tool<'matrix>(matrix: &'matrix TurboNextMatrix, id: &str) -> &'matrix ToolMatrix {
        matrix
            .tools
            .iter()
            .find(|tool| tool.id == id)
            .expect("matrix tool")
    }

    /// The version a workspace manifest actually reports for one dependency,
    /// with `"catalog:"` references resolved against the default catalog.
    fn manifest_reported_pin(
        root: &Path,
        workspace: &str,
        manifest_path: &str,
        dependency: &str,
    ) -> String {
        let manifest: Value =
            serde_json::from_slice(&fs::read(root.join(manifest_path)).expect("manifest"))
                .expect("manifest json");
        let declared = ["dependencies", "devDependencies"]
            .iter()
            .find_map(|section| {
                manifest
                    .get(section)?
                    .get(dependency)?
                    .as_str()
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| panic!("{manifest_path} does not declare {dependency}"));
        if declared == "catalog:" {
            pnpm_catalog_pin(workspace, dependency)
                .unwrap_or_else(|| panic!("no default catalog pin for {dependency}"))
        } else {
            declared
        }
    }

    #[test]
    fn embedded_matrix_is_valid_and_keeps_probe_honesty() {
        validate_turbo_next_matrix().expect("valid matrix");
        let digest = turbo_next_matrix_sha256();
        assert_eq!(digest.len(), 64);
        assert!(
            digest
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
        );
        let matrix = matrix();
        assert!(!matrix.documented_cli_probed);
        assert!(!matrix.devtools_integration);
        assert_eq!(matrix.documented_cli_probe_task, "P5-024");
        assert_eq!(matrix.devtools_integration_task, "P5-025");
        assert_eq!(matrix.task, "P5-010");
        assert_eq!(tool(&matrix, "turborepo").versions[0].major, 2);
        assert_eq!(tool(&matrix, "nextjs").versions[0].major, 16);
    }

    /// The self-validating real-repository check: every pin and layout claim
    /// in the matrix must match what this repository's own files, the static
    /// graph, and the static Next.js index actually report — no network.
    #[test]
    fn matrix_pins_and_layouts_match_this_repository() {
        let root = repository_root();
        let matrix = matrix();
        let workspace =
            fs::read_to_string(root.join("pnpm-workspace.yaml")).expect("pnpm workspace file");
        let turborepo = tool(&matrix, "turborepo");
        let nextjs = tool(&matrix, "nextjs");
        let turbo_pin = &turborepo.versions[0].version;

        for tool in &matrix.tools {
            for version in &tool.versions {
                for evidence in &version.evidence {
                    let evidence_file = root.join(&evidence.path);
                    assert!(
                        evidence_file.is_file(),
                        "matrix evidence file {} is missing",
                        evidence.path
                    );
                    match evidence.kind.as_str() {
                        "pnpm_catalog_pin" => assert_eq!(
                            pnpm_catalog_pin(&workspace, &evidence.key).as_deref(),
                            Some(version.version.as_str()),
                            "catalog pin for {} diverged from the matrix",
                            evidence.key
                        ),
                        "pnpm_lockfile_snapshot" => {
                            let lockfile =
                                fs::read_to_string(&evidence_file).expect("lockfile read");
                            assert!(
                                lockfile.contains(&format!("{}:", evidence.key)),
                                "lockfile snapshot {} diverged from the matrix",
                                evidence.key
                            );
                            if let Some(integrity) = &evidence.integrity {
                                assert!(
                                    lockfile.contains(integrity),
                                    "lockfile integrity for {} diverged from the matrix",
                                    evidence.key
                                );
                            }
                        }
                        "workspace_dependency_catalog_reference" => assert_eq!(
                            manifest_reported_pin(&root, &workspace, &evidence.path, &evidence.key),
                            version.version,
                            "{} reports a different {} version than the matrix",
                            evidence.path,
                            evidence.key
                        ),
                        other => panic!("unknown matrix evidence type {other}"),
                    }
                }
            }
        }

        let repository = &matrix.validation_repositories[0];
        assert_eq!(repository.id, "soleaux");
        assert_eq!(
            pnpm_workspace_packages(&workspace),
            repository.expected_workspace_globs,
            "the section-aware pnpm parse must report exactly the packages section"
        );
        assert!(root.join(&repository.turbo_configuration).is_file());
        assert!(root.join(&repository.workspace_source).is_file());

        let graph = load_graph(&root.join(&repository.path)).expect("static graph");
        assert_eq!(graph.provider, turborepo.provider);
        assert!(
            graph
                .packages
                .iter()
                .any(|package| package.name == "soleaux-dashboard"
                    && package.path == "telemetry/dashboard"),
            "the telemetry/* workspace glob must resolve the dashboard package"
        );
        let mut tasks = graph.tasks.clone();
        tasks.sort();
        let mut expected_tasks = repository.expected_tasks.clone();
        expected_tasks.sort();
        assert_eq!(tasks, expected_tasks);
        // The binary probe stays optional evidence: `documentedCliProbed` is
        // false until P5-024, but when a turbo binary reports a version for
        // this repository it must match the lockfile pin.
        if let Some(reported) = &graph.turbo_version {
            assert_eq!(
                reported, turbo_pin,
                "the turbo binary on PATH reports a version that diverges from the pin"
            );
        }

        for application in &repository.next_applications {
            let index = index_nextjs(&root.join(application)).expect("static next index");
            assert_eq!(index.provider, nextjs.provider);
            assert!(
                !index.runtime_evidence_attached,
                "runtime DevTools evidence is P5-025 and cannot be attached statically"
            );
            assert!(
                index
                    .routes
                    .iter()
                    .any(|route| route.kind == "page" && route.route == "/"),
                "{application} must report its root page route"
            );
            assert!(
                index
                    .routes
                    .iter()
                    .all(|route| NEXT_ROUTE_KINDS.contains(&route.kind.as_str())),
                "every reported route kind must be covered by the matrix"
            );
        }
    }
}
