//! Static and documented-CLI-first Turborepo intelligence.

use anyhow::{Context, Result};
use glob::glob;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::Path,
    process::Command,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct TurboPackage {
    pub name: String,
    pub path: String,
    pub dependencies: Vec<String>,
    pub dev_dependencies: Vec<String>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TurboGraph {
    pub packages: Vec<TurboPackage>,
    pub tasks: Vec<String>,
    pub boundaries: Value,
    pub provider: String,
    pub turbo_version: Option<String>,
}

pub fn load_graph(root: &Path) -> Result<TurboGraph> {
    let root = fs::canonicalize(root).with_context(|| format!("resolving {}", root.display()))?;
    let workspace_patterns = workspace_patterns(&root)?;
    let mut packages = Vec::new();
    for pattern in workspace_patterns {
        let absolute = root.join(&pattern).to_string_lossy().to_string();
        for entry in
            glob(&absolute).with_context(|| format!("invalid workspace pattern {pattern}"))?
        {
            let path = match entry {
                Ok(value) => value,
                Err(_) => continue,
            };
            let package_path = if path.join("package.json").is_file() {
                path
            } else {
                continue;
            };
            let value: Value =
                serde_json::from_slice(&fs::read(package_path.join("package.json"))?)?;
            let name = value
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_else(|| {
                    package_path
                        .file_name()
                        .and_then(|item| item.to_str())
                        .unwrap_or("package")
                });
            packages.push(TurboPackage {
                name: name.to_string(),
                path: relative(&root, &package_path),
                dependencies: dependency_names(value.get("dependencies")),
                dev_dependencies: dependency_names(value.get("devDependencies")),
                tags: Vec::new(),
            });
        }
    }
    packages.sort_by(|left, right| left.name.cmp(&right.name));
    packages.dedup_by(|left, right| left.path == right.path);

    let turbo_json = root.join("turbo.json");
    let configuration: Value = if turbo_json.is_file() {
        serde_json::from_slice(&fs::read(&turbo_json)?)?
    } else {
        Value::Null
    };
    let tasks = configuration
        .get("tasks")
        .or_else(|| configuration.get("pipeline"))
        .and_then(Value::as_object)
        .map(|value| value.keys().cloned().collect::<Vec<_>>())
        .unwrap_or_default();
    let boundaries = configuration
        .get("boundaries")
        .cloned()
        .unwrap_or(Value::Null);
    apply_boundary_tags(&mut packages, &boundaries);
    Ok(TurboGraph {
        packages,
        tasks,
        boundaries,
        provider: "static-workspace+turbo-json".to_string(),
        turbo_version: turbo_version(&root),
    })
}

pub fn packages_for_path(graph: &TurboGraph, path: &str) -> Vec<String> {
    let normalized = path.replace('\\', "/");
    graph
        .packages
        .iter()
        .filter(|package| {
            normalized == package.path || normalized.starts_with(&format!("{}/", package.path))
        })
        .map(|package| package.name.clone())
        .collect()
}

pub fn search_scope(
    graph: &TurboGraph,
    package_name: &str,
    include_dependents: bool,
) -> Vec<String> {
    let by_name = graph
        .packages
        .iter()
        .map(|package| (package.name.as_str(), package))
        .collect::<BTreeMap<_, _>>();
    let mut selected = BTreeSet::new();
    let mut queue = VecDeque::from([package_name.to_string()]);
    while let Some(name) = queue.pop_front() {
        if !selected.insert(name.clone()) {
            continue;
        }
        if let Some(package) = by_name.get(name.as_str()) {
            for dependency in package
                .dependencies
                .iter()
                .chain(package.dev_dependencies.iter())
            {
                if by_name.contains_key(dependency.as_str()) {
                    queue.push_back(dependency.clone());
                }
            }
        }
    }
    if include_dependents {
        loop {
            let mut changed = false;
            for package in &graph.packages {
                let depends_on_selected = package
                    .dependencies
                    .iter()
                    .chain(package.dev_dependencies.iter())
                    .any(|dependency| selected.contains(dependency));
                if depends_on_selected && selected.insert(package.name.clone()) {
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
    }
    selected.into_iter().collect()
}

pub fn affected_packages(
    root: &Path,
    graph: &TurboGraph,
    base: &str,
    head: &str,
) -> Result<Vec<String>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["diff", "--name-only", &format!("{base}...{head}")])
        .output()
        .context("running git diff for affected packages")?;
    if !output.status.success() {
        return Ok(Vec::new());
    }
    let mut affected = BTreeSet::new();
    for path in String::from_utf8_lossy(&output.stdout).lines() {
        for package in packages_for_path(graph, path) {
            affected.insert(package);
        }
    }
    let direct = affected.clone();
    for package in &graph.packages {
        if package
            .dependencies
            .iter()
            .chain(package.dev_dependencies.iter())
            .any(|dependency| direct.contains(dependency))
        {
            affected.insert(package.name.clone());
        }
    }
    Ok(affected.into_iter().collect())
}

fn workspace_patterns(root: &Path) -> Result<Vec<String>> {
    let package_json = root.join("package.json");
    if package_json.is_file() {
        let value: Value = serde_json::from_slice(&fs::read(package_json)?)?;
        if let Some(workspaces) = value.get("workspaces") {
            let values = workspaces
                .as_array()
                .or_else(|| workspaces.get("packages").and_then(Value::as_array));
            if let Some(values) = values {
                let patterns = values
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect::<Vec<_>>();
                if !patterns.is_empty() {
                    return Ok(patterns);
                }
            }
        }
    }
    let pnpm = root.join("pnpm-workspace.yaml");
    if pnpm.is_file() {
        let content = fs::read_to_string(pnpm)?;
        let patterns = pnpm_workspace_packages(&content);
        if !patterns.is_empty() {
            return Ok(patterns);
        }
    }
    Ok(vec!["apps/*".to_string(), "packages/*".to_string()])
}

/// Workspace globs from the `packages:` section of a `pnpm-workspace.yaml`
/// document. Parsing is section-aware: list items are collected only between
/// the top-level `packages:` key and the next top-level key, so entries under
/// coexisting sections such as `catalog:`, `allowBuilds:`, or `auditConfig:`
/// never leak into the globs.
pub fn pnpm_workspace_packages(content: &str) -> Vec<String> {
    pnpm_top_level_section(content, "packages")
        .unwrap_or_default()
        .into_iter()
        .filter_map(|line| line.trim().strip_prefix('-'))
        .map(yaml_scalar_value)
        .filter(|value| !value.is_empty())
        .collect()
}

/// Version pin for one dependency in the default `catalog:` section of a
/// `pnpm-workspace.yaml` document. This resolves `"catalog:"` dependency
/// references from workspace manifests without a package manager, a
/// `node_modules` tree, or network access.
pub fn pnpm_catalog_pin(content: &str, dependency: &str) -> Option<String> {
    pnpm_top_level_section(content, "catalog")?
        .into_iter()
        .filter_map(pnpm_map_entry)
        .find_map(|(key, value)| (key == dependency).then_some(value))
}

/// Lines belonging to one top-level section of a `pnpm-workspace.yaml`
/// document, or `None` when the section header is absent. A section ends at
/// the next top-level key; comments and list items never terminate it.
fn pnpm_top_level_section<'content>(
    content: &'content str,
    section: &str,
) -> Option<Vec<&'content str>> {
    let header = format!("{section}:");
    let mut inside = false;
    let mut lines = Vec::new();
    for line in content.lines() {
        let trimmed = line.trim();
        let top_level_key = !line.starts_with([' ', '\t'])
            && !trimmed.is_empty()
            && !trimmed.starts_with('#')
            && !trimmed.starts_with('-');
        if top_level_key {
            if inside {
                break;
            }
            inside = line.trim_end() == header;
            continue;
        }
        if inside {
            lines.push(line);
        }
    }
    inside.then_some(lines)
}

fn pnpm_map_entry(line: &str) -> Option<(String, String)> {
    let entry = line.trim();
    if entry.is_empty() || entry.starts_with('#') || entry.starts_with('-') {
        return None;
    }
    for quote in ['"', '\''] {
        if let Some(rest) = entry.strip_prefix(quote) {
            let end = rest.find(quote)?;
            let value = rest[end + 1..].trim_start().strip_prefix(':')?;
            return Some((rest[..end].to_string(), yaml_scalar_value(value)));
        }
    }
    let (key, value) = entry.split_once(':')?;
    Some((key.trim().to_string(), yaml_scalar_value(value)))
}

fn yaml_scalar_value(raw: &str) -> String {
    let raw = raw.trim();
    for quote in ['"', '\''] {
        if let Some(rest) = raw.strip_prefix(quote)
            && let Some(end) = rest.find(quote)
        {
            return rest[..end].to_string();
        }
    }
    raw.split_once(" #")
        .map_or(raw, |(value, _comment)| value)
        .trim()
        .to_string()
}

fn dependency_names(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_object)
        .map(|object| object.keys().cloned().collect::<Vec<_>>())
        .unwrap_or_default()
}

fn apply_boundary_tags(packages: &mut [TurboPackage], boundaries: &Value) {
    let Some(tags) = boundaries.get("tags").and_then(Value::as_object) else {
        return;
    };
    for package in packages {
        for (pattern, values) in tags {
            let prefix = pattern.trim_end_matches('*').trim_end_matches('/');
            if (package.path == prefix || package.path.starts_with(&format!("{prefix}/")))
                && let Some(values) = values.as_array()
            {
                package
                    .tags
                    .extend(values.iter().filter_map(Value::as_str).map(str::to_string));
            }
        }
        package.tags.sort();
        package.tags.dedup();
    }
}

fn turbo_version(root: &Path) -> Option<String> {
    let output = Command::new("turbo")
        .arg("--version")
        .current_dir(root)
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    /// Verbatim copy of this repository's own `pnpm-workspace.yaml`, whose
    /// `allowBuilds:`, `catalog:`, `overrides:`, and `auditConfig:` sections
    /// broke the previous line-prefix parser.
    const SOLEAUX_PNPM_WORKSPACE_SNAPSHOT: &str =
        include_str!("../testdata/pnpm-workspace-soleaux-snapshot.yaml");

    #[test]
    fn pnpm_packages_parse_is_section_aware_on_this_repositorys_workspace_file() {
        assert_eq!(
            pnpm_workspace_packages(SOLEAUX_PNPM_WORKSPACE_SNAPSHOT),
            vec![".", "docs", "telemetry/*"],
        );
        // The catalog map resolves pins, including quoted keys and unquoted values.
        for (dependency, pin) in [
            ("turbo", "2.10.5"),
            ("next", "16.3.0-preview.6"),
            ("@ast-grep/cli", "0.45.0"),
            ("@tailwindcss/postcss", "^4"),
            ("@libpg-query/parser", "17.6.10"),
        ] {
            assert_eq!(
                pnpm_catalog_pin(SOLEAUX_PNPM_WORKSPACE_SNAPSHOT, dependency).as_deref(),
                Some(pin),
            );
        }
        // Entries of coexisting sections stay out of the catalog: these names
        // exist only under `allowBuilds:` and `overrides:`.
        assert_eq!(
            pnpm_catalog_pin(SOLEAUX_PNPM_WORKSPACE_SNAPSHOT, "esbuild"),
            None
        );
        assert_eq!(
            pnpm_catalog_pin(SOLEAUX_PNPM_WORKSPACE_SNAPSHOT, "sharp"),
            None
        );
    }

    #[test]
    fn pnpm_audit_config_list_items_no_longer_become_workspace_packages() {
        let directory = tempdir().expect("tempdir");
        fs::write(
            directory.path().join("pnpm-workspace.yaml"),
            SOLEAUX_PNPM_WORKSPACE_SNAPSHOT,
        )
        .expect("workspace file");
        fs::write(
            directory.path().join("package.json"),
            r#"{"name":"snapshot-root"}"#,
        )
        .expect("root package");
        fs::create_dir_all(directory.path().join("docs")).expect("docs");
        fs::write(
            directory.path().join("docs/package.json"),
            r#"{"name":"snapshot-docs"}"#,
        )
        .expect("docs package");
        fs::create_dir_all(directory.path().join("telemetry/dashboard")).expect("dashboard");
        fs::write(
            directory.path().join("telemetry/dashboard/package.json"),
            r#"{"name":"snapshot-dashboard"}"#,
        )
        .expect("dashboard package");
        // The previous parser leaked `auditConfig.ignoreGhsas` list items into
        // the workspace globs; a directory matching one proves the regression.
        fs::create_dir_all(directory.path().join("GHSA-mh99-v99m-4gvg")).expect("trap directory");
        fs::write(
            directory.path().join("GHSA-mh99-v99m-4gvg/package.json"),
            r#"{"name":"ghsa-trap"}"#,
        )
        .expect("trap package");
        let graph = load_graph(directory.path()).expect("graph");
        let names = graph
            .packages
            .iter()
            .map(|package| package.name.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            names,
            vec!["snapshot-dashboard", "snapshot-docs", "snapshot-root"],
        );
    }

    #[test]
    fn workspace_object_form_and_default_globs_resolve() {
        let object_form = tempdir().expect("tempdir");
        fs::write(
            object_form.path().join("package.json"),
            r#"{"workspaces":{"packages":["modules/*"]}}"#,
        )
        .expect("root package");
        fs::create_dir_all(object_form.path().join("modules/alpha")).expect("module");
        fs::write(
            object_form.path().join("modules/alpha/package.json"),
            r#"{"name":"alpha"}"#,
        )
        .expect("module package");
        let graph = load_graph(object_form.path()).expect("object-form graph");
        assert_eq!(graph.packages.len(), 1);
        assert_eq!(graph.packages[0].name, "alpha");

        let defaults = tempdir().expect("tempdir");
        fs::create_dir_all(defaults.path().join("apps/web")).expect("app");
        fs::write(
            defaults.path().join("apps/web/package.json"),
            r#"{"name":"web"}"#,
        )
        .expect("app package");
        let graph = load_graph(defaults.path()).expect("default-glob graph");
        assert_eq!(graph.packages.len(), 1);
        assert_eq!(graph.packages[0].name, "web");
    }

    #[test]
    fn static_graph_scopes_search_to_package_dependencies() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("apps/web")).expect("web");
        fs::create_dir_all(directory.path().join("packages/ui")).expect("ui");
        fs::write(
            directory.path().join("package.json"),
            r#"{"workspaces":["apps/*","packages/*"]}"#,
        )
        .expect("root package");
        fs::write(
            directory.path().join("apps/web/package.json"),
            r#"{"name":"web","dependencies":{"ui":"workspace:*"}}"#,
        )
        .expect("web package");
        fs::write(
            directory.path().join("packages/ui/package.json"),
            r#"{"name":"ui"}"#,
        )
        .expect("ui package");
        fs::write(directory.path().join("turbo.json"), r#"{"tasks":{"build":{}},"boundaries":{"tags":{"apps/*":["app"],"packages/*":["shared"]}}}"#).expect("turbo");
        let graph = load_graph(directory.path()).expect("graph");
        assert_eq!(graph.packages.len(), 2);
        assert_eq!(search_scope(&graph, "web", false), vec!["ui", "web"]);
        assert!(
            graph
                .packages
                .iter()
                .find(|package| package.name == "web")
                .expect("web")
                .tags
                .contains(&"app".to_string())
        );
    }
}
