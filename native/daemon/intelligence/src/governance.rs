//! Native authority and governance graph projection.
//!
//! The graph is deliberately repository-local, bounded, and provenance tagged.
//! It augments Context Packet V2 with ownership, constraint, validation, and
//! conflict edges without creating another public tool catalog.

use anyhow::{Context, Result};
use glob::Pattern;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

const MAX_GOVERNANCE_FILE_BYTES: u64 = 512 * 1024;
const MAX_EDGES: usize = 256;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GovernanceEdge {
    pub id: String,
    pub kind: String,
    pub source: String,
    pub target: String,
    pub summary: String,
    pub path: String,
    pub line: usize,
    pub digest: String,
    pub trust: String,
    pub metadata: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GovernanceGraph {
    pub edges: Vec<GovernanceEdge>,
    pub coverage_complete: bool,
    pub gaps: Vec<Value>,
    pub digest: String,
}

pub fn build_governance_graph(root: &Path, selected_paths: &[String]) -> Result<GovernanceGraph> {
    let mut edges = Vec::new();
    let mut gaps = Vec::new();
    for relative in ["CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"] {
        let path = root.join(relative);
        if path.is_file() {
            edges.extend(codeowners_edges(&path, relative, selected_paths)?);
        }
    }
    edges.extend(constraint_edges(root)?);
    edges.extend(validation_edges(root)?);
    edges.extend(explicit_governance_edges(root)?);
    edges.sort_by(|left, right| left.id.cmp(&right.id));
    edges.dedup_by(|left, right| left.id == right.id);
    let complete = edges.len() <= MAX_EDGES;
    if !complete {
        let omitted = edges.len().saturating_sub(MAX_EDGES);
        edges.truncate(MAX_EDGES);
        gaps.push(json!({
            "code":"governance_edge_limit",
            "message":format!("{omitted} governance edges were omitted by the native bound."),
            "severity":"warning",
            "retryable":true,
            "table":"authority.governance",
            "path":Value::Null,
        }));
    }
    if edges.is_empty() {
        gaps.push(json!({
            "code":"governance_unavailable",
            "message":"No native ownership, constraint, or validation governance source was discovered.",
            "severity":"info",
            "retryable":false,
            "table":"authority.governance",
            "path":Value::Null,
        }));
    }
    let digest = sha256_hex(&serde_json::to_vec(&edges)?);
    Ok(GovernanceGraph {
        edges,
        coverage_complete: complete && gaps.is_empty(),
        gaps,
        digest,
    })
}

fn codeowners_edges(
    path: &Path,
    relative: &str,
    selected_paths: &[String],
) -> Result<Vec<GovernanceEdge>> {
    let content = bounded_text(path)?;
    let mut output = Vec::new();
    for (index, raw) in content.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split_whitespace();
        let Some(pattern) = fields.next() else {
            continue;
        };
        let owners = fields.map(str::to_string).collect::<Vec<_>>();
        if owners.is_empty() {
            continue;
        }
        let normalized = pattern.trim_start_matches('/');
        let matcher = Pattern::new(normalized).ok();
        let matched = if selected_paths.is_empty() {
            vec![normalized.to_string()]
        } else {
            selected_paths
                .iter()
                .filter(|candidate| {
                    matcher
                        .as_ref()
                        .map(|value| value.matches(candidate))
                        .unwrap_or_else(|| candidate.starts_with(normalized))
                })
                .cloned()
                .collect::<Vec<_>>()
        };
        for target in matched {
            for owner in &owners {
                output.push(edge(
                    "owns",
                    owner,
                    &target,
                    &format!("{owner} is a canonical owner for {target}"),
                    relative,
                    index + 1,
                    json!({"pattern":pattern,"owners":owners,"resolution":"last_matching_rule_wins"}),
                ));
            }
        }
    }
    Ok(output)
}

fn constraint_edges(root: &Path) -> Result<Vec<GovernanceEdge>> {
    let mut output = Vec::new();
    for relative in [
        "AGENTS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        ".soleaux/rules.md",
        ".soleaux/governance.md",
    ] {
        let path = root.join(relative);
        if !path.is_file() {
            continue;
        }
        let content = bounded_text(&path)?;
        for (index, raw) in content.lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with("<!--") {
                continue;
            }
            if line.len() < 8 {
                continue;
            }
            output.push(edge(
                "constrains",
                relative,
                "workspace",
                line,
                relative,
                index + 1,
                json!({"source_kind":"managed_or_repository_guidance"}),
            ));
            if output.len() >= 96 {
                break;
            }
        }
    }
    Ok(output)
}

fn validation_edges(root: &Path) -> Result<Vec<GovernanceEdge>> {
    let mut output = Vec::new();
    let package = root.join("package.json");
    if package.is_file() {
        let value: Value = serde_json::from_slice(&fs::read(&package)?)?;
        if let Some(scripts) = value.get("scripts").and_then(Value::as_object) {
            for (name, command) in scripts {
                if matches!(
                    name.as_str(),
                    "test" | "lint" | "typecheck" | "check" | "build"
                ) {
                    output.push(edge(
                        "validates",
                        &format!("package.json#{name}"),
                        "workspace",
                        command.as_str().unwrap_or_default(),
                        "package.json",
                        1,
                        json!({"runner":"package_script","script":name}),
                    ));
                }
            }
        }
    }
    if root.join("Cargo.toml").is_file() {
        for (name, command) in [
            (
                "cargo-check",
                "cargo check --workspace --all-targets --all-features",
            ),
            ("cargo-test", "cargo test --workspace --all-features"),
            (
                "cargo-clippy",
                "cargo clippy --workspace --all-targets --all-features -- -D warnings",
            ),
        ] {
            output.push(edge(
                "validates",
                name,
                "workspace",
                command,
                "Cargo.toml",
                1,
                json!({"runner":"cargo"}),
            ));
        }
    }
    if root.join("pyproject.toml").is_file() {
        for (name, command) in [("pytest", "pytest"), ("ruff", "ruff check .")] {
            output.push(edge(
                "validates",
                name,
                "workspace",
                command,
                "pyproject.toml",
                1,
                json!({"runner":"python"}),
            ));
        }
    }
    Ok(output)
}

fn explicit_governance_edges(root: &Path) -> Result<Vec<GovernanceEdge>> {
    let path = root.join(".soleaux/governance.json");
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let value: Value = serde_json::from_slice(&fs::read(&path)?)?;
    let mut output = Vec::new();
    for (index, value) in value
        .get("edges")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
    {
        let kind = value
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("relates");
        let source = value
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let target = value
            .get("target")
            .and_then(Value::as_str)
            .unwrap_or("workspace");
        let summary = value
            .get("summary")
            .and_then(Value::as_str)
            .unwrap_or("Explicit Soleaux governance edge");
        output.push(edge(
            kind,
            source,
            target,
            summary,
            ".soleaux/governance.json",
            index + 1,
            value.clone(),
        ));
    }
    Ok(output)
}

fn edge(
    kind: &str,
    source: &str,
    target: &str,
    summary: &str,
    path: &str,
    line: usize,
    metadata: Value,
) -> GovernanceEdge {
    let input = format!("{kind}\0{source}\0{target}\0{path}\0{line}\0{summary}");
    GovernanceEdge {
        id: format!("governance:{}", &sha256_hex(input.as_bytes())[..24]),
        kind: kind.to_string(),
        source: source.to_string(),
        target: target.to_string(),
        summary: summary.chars().take(1024).collect(),
        path: path.to_string(),
        line,
        digest: sha256_hex(input.as_bytes()),
        trust: "verified_repository_metadata".to_string(),
        metadata,
    }
}

fn bounded_text(path: &Path) -> Result<String> {
    let metadata = fs::metadata(path)?;
    if metadata.len() > MAX_GOVERNANCE_FILE_BYTES {
        anyhow::bail!(
            "governance source exceeds the bounded file ceiling: {}",
            path.display()
        );
    }
    fs::read_to_string(path)
        .with_context(|| format!("reading governance source {}", path.display()))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn materializes_ownership_constraints_and_validation() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("CODEOWNERS"), "src/** @team/core\n").expect("owners");
        fs::write(
            directory.path().join("AGENTS.md"),
            "# Rules\nRun tests before merge.\n",
        )
        .expect("rules");
        fs::write(
            directory.path().join("Cargo.toml"),
            "[package]\nname='demo'\nversion='0.1.0'\n",
        )
        .expect("cargo");
        let graph =
            build_governance_graph(directory.path(), &["src/lib.rs".to_string()]).expect("graph");
        assert!(graph.edges.iter().any(|edge| edge.kind == "owns"));
        assert!(graph.edges.iter().any(|edge| edge.kind == "constrains"));
        assert!(graph.edges.iter().any(|edge| edge.kind == "validates"));
    }
}
