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
        let patterns = content
            .lines()
            .filter_map(|line| line.trim().strip_prefix('-'))
            .map(str::trim)
            .map(|value| value.trim_matches(|character| character == '\'' || character == '"'))
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .collect::<Vec<_>>();
        if !patterns.is_empty() {
            return Ok(patterns);
        }
    }
    Ok(vec!["apps/*".to_string(), "packages/*".to_string()])
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
