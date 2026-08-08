//! Static and documented-CLI-first Turborepo intelligence.
//!
//! The static graph ([`load_graph`]) is the always-available evidence source.
//! [`probe_documented_cli`] extends it with the documented Turborepo CLI
//! (`turbo ls`, `turbo run --dry=json`, `turbo boundaries`, and the
//! `--affected` package listing), version-gated on the embedded
//! `turbo-next-matrix-v1` contract: only matrix-pinned versions are probed as
//! documented, an unknown version degrades to safe mode, and an absent binary
//! reports `probedAvailable: false` with its reason. Probing is read-only and
//! offline (`--skip-infer`, `--no-update-notifier`, telemetry disabled) and
//! carries no deadline — a hung pinned binary blocks the caller exactly as
//! the pre-existing `turbo --version` probe does.
//!
//! The optional Turbo LSP is deliberately omitted: the matrix-pinned turbo
//! CLI exposes no LSP entry point to probe (see [`TURBO_LSP_OMISSION_REASON`]).

use crate::turbo_next_matrix::{
    TURBO_NEXT_MATRIX_SCHEMA_VERSION, turbo_documented_cli_pins, turbo_next_matrix_sha256,
};
use anyhow::{Context, Result};
use glob::glob;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
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

pub const TURBO_CLI_PROBE_SCHEMA_VERSION: &str = "soleaux.turbo-cli-probe/v1";

/// Why every probe report carries `lspProbed: false`: the matrix-pinned turbo
/// CLI lists no LSP subcommand in `turbo --help` (the Turborepo LSP ships only
/// inside the VS Code extension), so no truthful compatibility probe can run
/// against the pinned binary and the optional Turbo LSP integration is
/// omitted.
pub const TURBO_LSP_OMISSION_REASON: &str = "the matrix-pinned turbo CLI exposes no LSP entry \
    point (`turbo --help` lists no lsp subcommand; the Turborepo LSP ships inside the VS Code \
    extension), so no compatibility probe can run and the optional Turbo LSP is omitted";

/// Global flags on every probe invocation: probe exactly the resolved binary
/// (no local-version re-execution) and skip the update-notifier network check.
const TURBO_PROBE_GLOBAL_FLAGS: [&str; 2] = ["--skip-infer", "--no-update-notifier"];

/// Options for one documented-CLI probe run. The default probes `turbo` from
/// `PATH` and dry-runs the first static-graph task.
#[derive(Debug, Clone, Default)]
pub struct TurboCliProbeOptions {
    /// Explicit turbo executable; `None` resolves `turbo` on `PATH`.
    pub turbo_executable: Option<PathBuf>,
    /// Task for the dry-run probe; `None` selects the first static-graph task.
    pub dry_run_task: Option<String>,
    /// `TURBO_SCM_BASE` for the affected probe; `None` keeps turbo's default.
    pub scm_base: Option<String>,
    /// `TURBO_SCM_HEAD` for the affected probe; `None` keeps turbo's default.
    pub scm_head: Option<String>,
}

/// One documented command probe: the exact argv, whether it spawned, its exit
/// code, and the typed summary when the output matched the documented shape.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurboCliCommandProbe {
    pub id: String,
    pub arguments: Vec<String>,
    pub executed: bool,
    pub exit_code: Option<i32>,
    pub parsed: bool,
    pub summary: Value,
    pub error: Option<String>,
}

/// Runtime report of one version-gated documented-CLI probe.
///
/// `documented_cli_probed` is the truthful per-run counterpart of the v1
/// contract's static `documentedCliProbed: false`: it is `true` only when the
/// binary reported a matrix-pinned version and all four documented probes
/// (`ls`, `dry_run`, `boundaries`, `affected`) executed and parsed. Every
/// other outcome keeps it `false` and names the reason in
/// `degradation_reason`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurboCliProbeReport {
    pub schema_version: String,
    pub matrix_schema_version: String,
    pub matrix_sha256: String,
    pub matrix_pinned_versions: Vec<String>,
    pub executable: String,
    pub probed_available: bool,
    pub reported_version: Option<String>,
    pub version_supported: bool,
    pub documented_cli_probed: bool,
    pub degradation_reason: Option<String>,
    pub lsp_probed: bool,
    pub lsp_omission_reason: String,
    pub commands: Vec<TurboCliCommandProbe>,
}

/// Probe the documented Turborepo CLI for `root`, version-gated on the
/// embedded matrix contract. Degradation is data, never an error: the report
/// always states what ran, what was refused, and why.
pub fn probe_documented_cli(
    root: &Path,
    graph: &TurboGraph,
    options: &TurboCliProbeOptions,
) -> TurboCliProbeReport {
    let executable = options
        .turbo_executable
        .clone()
        .unwrap_or_else(|| PathBuf::from("turbo"));
    let mut report = TurboCliProbeReport {
        schema_version: TURBO_CLI_PROBE_SCHEMA_VERSION.into(),
        matrix_schema_version: TURBO_NEXT_MATRIX_SCHEMA_VERSION.into(),
        matrix_sha256: turbo_next_matrix_sha256(),
        matrix_pinned_versions: Vec::new(),
        executable: executable.display().to_string(),
        probed_available: false,
        reported_version: None,
        version_supported: false,
        documented_cli_probed: false,
        degradation_reason: None,
        lsp_probed: false,
        lsp_omission_reason: TURBO_LSP_OMISSION_REASON.into(),
        commands: Vec::new(),
    };
    let pins = match turbo_documented_cli_pins() {
        Ok(pins) => pins,
        Err(error) => {
            report.degradation_reason = Some(format!(
                "version matrix rejected, so no version can gate the probe: {error:#}"
            ));
            return report;
        }
    };
    report.matrix_pinned_versions = pins.clone();

    let mut version = run_probe(&executable, root, "version", &["--version"], &[]);
    if !version.probe.executed {
        report.degradation_reason = Some(format!(
            "turbo executable unavailable ({}); the static graph remains the only Turborepo evidence",
            version
                .probe
                .error
                .as_deref()
                .unwrap_or("unknown spawn failure")
        ));
        report.commands.push(version.probe);
        return report;
    }
    let reported = version.stdout.trim().to_string();
    if version.probe.exit_code != Some(0) || reported.is_empty() {
        version.probe.error = Some(format!(
            "turbo --version reported no version: {}",
            output_snippet(&version.stderr)
        ));
        report.degradation_reason = version.probe.error.clone();
        report.commands.push(version.probe);
        return report;
    }
    version.probe.parsed = true;
    version.probe.summary = json!({ "version": reported });
    report.probed_available = true;
    report.reported_version = Some(reported.clone());
    report.commands.push(version.probe);

    if !pins.iter().any(|pin| pin == &reported) {
        report.degradation_reason = Some(format!(
            "safe mode: turbo {reported} is not pinned by {TURBO_NEXT_MATRIX_SCHEMA_VERSION} \
             (pinned: {pins:?}); documented CLI probing refused"
        ));
        return report;
    }
    report.version_supported = true;

    let mut listing = run_probe(&executable, root, "ls", &["ls", "--output=json"], &[]);
    finish_package_listing_probe(&mut listing, Some(graph));
    report.commands.push(listing.probe);

    report.commands.push(dry_run_probe(
        &executable,
        root,
        options
            .dry_run_task
            .clone()
            .or_else(|| graph.tasks.first().cloned()),
    ));

    let mut boundaries = run_probe(&executable, root, "boundaries", &["boundaries"], &[]);
    finish_boundaries_probe(&mut boundaries);
    report.commands.push(boundaries.probe);

    let mut environment = Vec::new();
    if let Some(base) = &options.scm_base {
        environment.push(("TURBO_SCM_BASE", base.as_str()));
    }
    if let Some(head) = &options.scm_head {
        environment.push(("TURBO_SCM_HEAD", head.as_str()));
    }
    let mut affected = run_probe(
        &executable,
        root,
        "affected",
        &["ls", "--affected", "--output=json"],
        &environment,
    );
    finish_package_listing_probe(&mut affected, None);
    report.commands.push(affected.probe);

    let degraded = report
        .commands
        .iter()
        .filter(|probe| probe.id != "version" && !(probe.executed && probe.parsed))
        .map(|probe| probe.id.clone())
        .collect::<Vec<_>>();
    if degraded.is_empty() {
        report.documented_cli_probed = true;
    } else {
        report.degradation_reason = Some(format!(
            "documented CLI probes degraded: {}",
            degraded.join(", ")
        ));
    }
    report
}

struct ProbeExecution {
    probe: TurboCliCommandProbe,
    stdout: String,
    stderr: String,
}

fn run_probe(
    executable: &Path,
    root: &Path,
    id: &str,
    arguments: &[&str],
    environment: &[(&str, &str)],
) -> ProbeExecution {
    let arguments = TURBO_PROBE_GLOBAL_FLAGS
        .iter()
        .chain(arguments)
        .map(|argument| (*argument).to_string())
        .collect::<Vec<_>>();
    let mut command = Command::new(executable);
    command
        .args(&arguments)
        .current_dir(root)
        .env("TURBO_TELEMETRY_DISABLED", "1");
    for (key, value) in environment {
        command.env(key, value);
    }
    match command.output() {
        Ok(output) => ProbeExecution {
            probe: TurboCliCommandProbe {
                id: id.into(),
                arguments,
                executed: true,
                exit_code: output.status.code(),
                parsed: false,
                summary: Value::Null,
                error: None,
            },
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        },
        Err(error) => ProbeExecution {
            probe: TurboCliCommandProbe {
                id: id.into(),
                arguments,
                executed: false,
                exit_code: None,
                parsed: false,
                summary: Value::Null,
                error: Some(format!("spawning turbo failed: {error}")),
            },
            stdout: String::new(),
            stderr: String::new(),
        },
    }
}

/// Documented `turbo ls --output=json` shape (turbo 2, probed on 2.10.5).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TurboLsOutput {
    package_manager: String,
    packages: TurboLsPackages,
}

#[derive(Debug, Deserialize)]
struct TurboLsPackages {
    count: u64,
    items: Vec<TurboLsItem>,
}

#[derive(Debug, Deserialize)]
struct TurboLsItem {
    name: String,
}

/// Documented `turbo run --dry=json` shape (turbo 2, probed on 2.10.5).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TurboDryRunOutput {
    turbo_version: String,
    packages: Vec<String>,
    tasks: Vec<Value>,
}

fn finish_package_listing_probe(execution: &mut ProbeExecution, static_graph: Option<&TurboGraph>) {
    if !execution.probe.executed {
        return;
    }
    if execution.probe.exit_code != Some(0) {
        execution.probe.error = Some(output_snippet(&execution.stderr));
        return;
    }
    match serde_json::from_str::<TurboLsOutput>(&execution.stdout) {
        Ok(listing) => {
            let names = listing
                .packages
                .items
                .iter()
                .map(|item| item.name.as_str())
                .collect::<Vec<_>>();
            let mut summary = json!({
                "packageManager": listing.package_manager,
                "packageCount": listing.packages.count,
                "packages": names,
            });
            if let Some(graph) = static_graph {
                let cli = names.iter().copied().collect::<BTreeSet<_>>();
                let statically_known = graph
                    .packages
                    .iter()
                    .map(|package| package.name.as_str())
                    .collect::<BTreeSet<_>>();
                summary["staticGraphAgreement"] = json!({
                    "matches": cli == statically_known,
                    "onlyCli": cli.difference(&statically_known).collect::<Vec<_>>(),
                    "onlyStatic": statically_known.difference(&cli).collect::<Vec<_>>(),
                });
            }
            execution.probe.parsed = true;
            execution.probe.summary = summary;
        }
        Err(error) => {
            execution.probe.error = Some(format!(
                "turbo ls output diverged from the documented JSON shape: {error}"
            ));
        }
    }
}

fn dry_run_probe(executable: &Path, root: &Path, task: Option<String>) -> TurboCliCommandProbe {
    let Some(task) = task else {
        return TurboCliCommandProbe {
            id: "dry_run".into(),
            arguments: Vec::new(),
            executed: false,
            exit_code: None,
            parsed: false,
            summary: Value::Null,
            error: Some("the static graph reports no turbo.json task to dry-run".into()),
        };
    };
    let mut execution = run_probe(
        executable,
        root,
        "dry_run",
        &["run", &task, "--dry=json"],
        &[],
    );
    if !execution.probe.executed {
        return execution.probe;
    }
    if execution.probe.exit_code != Some(0) {
        execution.probe.error = Some(output_snippet(&execution.stderr));
        return execution.probe;
    }
    match serde_json::from_str::<TurboDryRunOutput>(&execution.stdout) {
        Ok(dry_run) => {
            execution.probe.parsed = true;
            execution.probe.summary = json!({
                "task": task,
                "turboVersion": dry_run.turbo_version,
                "packages": dry_run.packages,
                "taskCount": dry_run.tasks.len(),
            });
        }
        Err(error) => {
            execution.probe.error = Some(format!(
                "turbo dry-run output diverged from the documented JSON shape: {error}"
            ));
        }
    }
    execution.probe
}

fn finish_boundaries_probe(execution: &mut ProbeExecution) {
    if !execution.probe.executed {
        return;
    }
    // `turbo boundaries` documents no JSON output; exit 0 is clean, exit 1 is
    // issues found, and the trailing summary line carries the counts.
    let Some(code @ (0 | 1)) = execution.probe.exit_code else {
        execution.probe.error = Some(format!(
            "turbo boundaries exited with {:?}: {}",
            execution.probe.exit_code,
            output_snippet(&execution.stderr)
        ));
        return;
    };
    let Some((files, packages, issues)) = boundaries_summary(&execution.stdout) else {
        execution.probe.error =
            Some("turbo boundaries printed no recognizable summary line".into());
        return;
    };
    if (code == 0) != (issues == 0) {
        execution.probe.error = Some(format!(
            "turbo boundaries exit status {code} disagrees with its reported {issues} issues"
        ));
        return;
    }
    execution.probe.parsed = true;
    execution.probe.summary = json!({
        "checkedFiles": files,
        "checkedPackages": packages,
        "issuesFound": issues,
        "clean": code == 0,
    });
}

/// Counts from the trailing `Checked N files in M packages, K issues found`
/// line of `turbo boundaries` output (`no issues` reports zero), scanned from
/// the last line backwards.
fn boundaries_summary(stdout: &str) -> Option<(u64, u64, u64)> {
    stdout.lines().rev().find_map(boundaries_summary_line)
}

fn boundaries_summary_line(line: &str) -> Option<(u64, u64, u64)> {
    let tokens = line.split_whitespace().collect::<Vec<_>>();
    if tokens.len() != 9 || tokens[0] != "Checked" || tokens[3] != "in" || tokens[8] != "found" {
        return None;
    }
    if !noun_token(tokens[2], "file")
        || !noun_token(tokens[5], "package")
        || !noun_token(tokens[7], "issue")
    {
        return None;
    }
    let files = tokens[1].parse().ok()?;
    let packages = tokens[4].parse().ok()?;
    let issues = if tokens[6] == "no" {
        0
    } else {
        tokens[6].parse().ok()?
    };
    Some((files, packages, issues))
}

/// Whether a summary token is the expected noun in singular or plural form,
/// ignoring an attached comma.
fn noun_token(token: &str, stem: &str) -> bool {
    let bare = token.trim_end_matches(',');
    bare == stem || bare.strip_suffix('s') == Some(stem)
}

fn output_snippet(output: &str) -> String {
    let trimmed = output.trim();
    let snippet = trimmed.chars().take(400).collect::<String>();
    if snippet.len() < trimmed.len() {
        format!("{snippet}…")
    } else {
        snippet
    }
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

    /// A `ui` + `web` workspace with one `build` task, matching the fake
    /// binary's scripted `ls`, dry-run, and affected fixtures.
    fn fixture_workspace() -> (tempfile::TempDir, TurboGraph) {
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
        fs::write(
            directory.path().join("turbo.json"),
            r#"{"tasks":{"build":{}}}"#,
        )
        .expect("turbo configuration");
        let graph = load_graph(directory.path()).expect("graph");
        (directory, graph)
    }

    /// Scripted fake `turbo` executable: it reports the version written next
    /// to it, answers the four documented probes with canned 2.10.5-shaped
    /// output, and logs every argv it receives. Never a network install.
    #[cfg(unix)]
    const FAKE_TURBO_SCRIPT: &str = r#"#!/bin/sh
directory="$(cd "$(dirname "$0")" && pwd)"
printf '%s\n' "$*" >> "$directory/invocations.log"
version="$(cat "$directory/version")"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-infer|--no-update-notifier) shift ;;
    *) break ;;
  esac
done
case "$1" in
  --version)
    printf '%s\n' "$version"
    ;;
  ls)
    affected=no
    for argument in "$@"; do
      if [ "$argument" = "--affected" ]; then affected=yes; fi
    done
    if [ "$affected" = yes ]; then
      printf '%s\n' '{"packageManager":"pnpm9","packages":{"count":1,"items":[{"name":"web","path":"apps/web"}]}}'
    else
      printf '%s\n' '{"packageManager":"pnpm9","packages":{"count":2,"items":[{"name":"ui","path":"packages/ui"},{"name":"web","path":"apps/web"}]}}'
    fi
    ;;
  run)
    printf '%s\n' "{\"turboVersion\":\"$version\",\"packages\":[\"ui\",\"web\"],\"tasks\":[{\"taskId\":\"ui#build\"},{\"taskId\":\"web#build\"}]}"
    ;;
  boundaries)
    printf 'Checking packages...\n'
    printf 'Checked 4 files in 2 packages, no issues found\n'
    ;;
  *)
    printf 'unknown subcommand\n' >&2
    exit 2
    ;;
esac
"#;

    #[cfg(unix)]
    fn fake_turbo(directory: &Path, version: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let executable = directory.join("turbo");
        fs::write(&executable, FAKE_TURBO_SCRIPT).expect("fake turbo script");
        fs::set_permissions(&executable, fs::Permissions::from_mode(0o755))
            .expect("fake turbo permissions");
        fs::write(directory.join("version"), format!("{version}\n")).expect("fake turbo version");
        executable
    }

    #[cfg(unix)]
    fn logged_invocations(directory: &Path) -> Vec<String> {
        fs::read_to_string(directory.join("invocations.log"))
            .expect("invocation log")
            .lines()
            .map(str::to_string)
            .collect()
    }

    #[cfg(unix)]
    #[test]
    fn documented_cli_probe_runs_all_documented_commands_on_a_pinned_version() {
        let (workspace, graph) = fixture_workspace();
        let binary_directory = tempdir().expect("binary tempdir");
        let executable = fake_turbo(binary_directory.path(), "2.10.5");
        let report = probe_documented_cli(
            workspace.path(),
            &graph,
            &TurboCliProbeOptions {
                turbo_executable: Some(executable),
                dry_run_task: None,
                scm_base: Some("main".into()),
                scm_head: Some("HEAD".into()),
            },
        );
        assert_eq!(report.schema_version, TURBO_CLI_PROBE_SCHEMA_VERSION);
        assert_eq!(
            report.matrix_schema_version,
            TURBO_NEXT_MATRIX_SCHEMA_VERSION
        );
        assert_eq!(report.matrix_sha256, turbo_next_matrix_sha256());
        assert_eq!(report.matrix_pinned_versions, vec!["2.10.5"]);
        assert!(report.probed_available);
        assert_eq!(report.reported_version.as_deref(), Some("2.10.5"));
        assert!(report.version_supported);
        assert!(report.documented_cli_probed);
        assert_eq!(report.degradation_reason, None);
        assert!(!report.lsp_probed);
        assert!(report.lsp_omission_reason.contains("omitted"));

        let ids = report
            .commands
            .iter()
            .map(|probe| probe.id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            ids,
            vec!["version", "ls", "dry_run", "boundaries", "affected"]
        );
        for probe in &report.commands {
            assert!(probe.executed, "{} did not execute", probe.id);
            assert!(probe.parsed, "{} did not parse", probe.id);
            assert_eq!(probe.error, None, "{} reported an error", probe.id);
        }

        let summary = |id: &str| {
            report
                .commands
                .iter()
                .find(|probe| probe.id == id)
                .expect("command probe")
                .summary
                .clone()
        };
        assert_eq!(
            summary("ls"),
            json!({
                "packageManager": "pnpm9",
                "packageCount": 2,
                "packages": ["ui", "web"],
                "staticGraphAgreement": {"matches": true, "onlyCli": [], "onlyStatic": []},
            })
        );
        assert_eq!(
            summary("dry_run"),
            json!({
                "task": "build",
                "turboVersion": "2.10.5",
                "packages": ["ui", "web"],
                "taskCount": 2,
            })
        );
        assert_eq!(
            summary("boundaries"),
            json!({
                "checkedFiles": 4,
                "checkedPackages": 2,
                "issuesFound": 0,
                "clean": true,
            })
        );
        assert_eq!(
            summary("affected"),
            json!({
                "packageManager": "pnpm9",
                "packageCount": 1,
                "packages": ["web"],
            })
        );

        // The probe ran exactly the documented argv, in the documented order.
        assert_eq!(
            logged_invocations(binary_directory.path()),
            vec![
                "--skip-infer --no-update-notifier --version",
                "--skip-infer --no-update-notifier ls --output=json",
                "--skip-infer --no-update-notifier run build --dry=json",
                "--skip-infer --no-update-notifier boundaries",
                "--skip-infer --no-update-notifier ls --affected --output=json",
            ],
        );
    }

    #[cfg(unix)]
    #[test]
    fn documented_cli_probe_refuses_versions_outside_the_matrix() {
        let (workspace, graph) = fixture_workspace();
        let binary_directory = tempdir().expect("binary tempdir");
        let executable = fake_turbo(binary_directory.path(), "1.13.4");
        let report = probe_documented_cli(
            workspace.path(),
            &graph,
            &TurboCliProbeOptions {
                turbo_executable: Some(executable),
                ..TurboCliProbeOptions::default()
            },
        );
        assert!(report.probed_available);
        assert_eq!(report.reported_version.as_deref(), Some("1.13.4"));
        assert!(!report.version_supported);
        assert!(!report.documented_cli_probed);
        let reason = report.degradation_reason.expect("safe-mode reason");
        assert!(reason.contains("safe mode"), "{reason}");
        assert!(reason.contains("1.13.4"), "{reason}");
        assert!(reason.contains("refused"), "{reason}");
        // The gate refused before any documented command ran.
        assert_eq!(
            report
                .commands
                .iter()
                .map(|probe| probe.id.as_str())
                .collect::<Vec<_>>(),
            vec!["version"],
        );
        assert_eq!(
            logged_invocations(binary_directory.path()),
            vec!["--skip-infer --no-update-notifier --version"],
        );
    }

    #[test]
    fn documented_cli_probe_degrades_truthfully_without_a_binary() {
        let (workspace, graph) = fixture_workspace();
        let missing = workspace.path().join("missing-toolchain/turbo");
        let report = probe_documented_cli(
            workspace.path(),
            &graph,
            &TurboCliProbeOptions {
                turbo_executable: Some(missing),
                ..TurboCliProbeOptions::default()
            },
        );
        assert!(!report.probed_available);
        assert_eq!(report.reported_version, None);
        assert!(!report.version_supported);
        assert!(!report.documented_cli_probed);
        assert_eq!(report.matrix_pinned_versions, vec!["2.10.5"]);
        let reason = report.degradation_reason.expect("degradation reason");
        assert!(reason.contains("turbo executable unavailable"), "{reason}");
        assert!(reason.contains("static graph"), "{reason}");
        assert_eq!(report.commands.len(), 1);
        assert!(!report.commands[0].executed);
        assert_eq!(report.commands[0].exit_code, None);
    }

    #[test]
    fn boundaries_summary_parses_the_documented_output_forms() {
        // Captured verbatim from turbo 2.10.5 against this repository and a
        // clean fixture.
        assert_eq!(
            boundaries_summary(
                "Checking packages...\nChecked 295 files in 9 packages, 9 issues found\n"
            ),
            Some((295, 9, 9)),
        );
        assert_eq!(
            boundaries_summary(
                "Checking packages...\nChecked 0 files in 0 packages, no issues found\n"
            ),
            Some((0, 0, 0)),
        );
        assert_eq!(
            boundaries_summary("Checked 1 file in 1 package, 1 issue found"),
            Some((1, 1, 1)),
        );
        assert_eq!(boundaries_summary("Checking packages..."), None);
        assert_eq!(boundaries_summary("9 issues found"), None);
        assert_eq!(
            boundaries_summary("Checked lots of files in packages, issues found"),
            None,
        );
    }

    /// Version-gated probe against this repository: with the pinned turbo on
    /// `PATH` the gate admits it and the core probes parse; with any other
    /// version it refuses in safe mode; with no binary it degrades truthfully.
    /// CI has no turbo binary, so the degraded branch is the one CI proves.
    #[test]
    fn documented_cli_probe_is_version_gated_for_this_repository() {
        let root = fs::canonicalize(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."))
            .expect("repository root");
        let graph = load_graph(&root).expect("static graph");
        let report = probe_documented_cli(&root, &graph, &TurboCliProbeOptions::default());
        assert_eq!(report.matrix_pinned_versions, vec!["2.10.5"]);
        assert!(!report.lsp_probed);
        if !report.probed_available {
            eprintln!("probe branch: binary unavailable (expected in CI)");
            let reason = report.degradation_reason.expect("degradation reason");
            assert!(reason.contains("static graph"), "{reason}");
            assert!(report.commands.len() <= 1);
            return;
        }
        let reported = report.reported_version.as_deref().expect("version");
        if !report.version_supported {
            eprintln!("probe branch: safe mode refused turbo {reported}");
            assert!(!report.documented_cli_probed);
            let reason = report.degradation_reason.expect("safe-mode reason");
            assert!(reason.contains("safe mode"), "{reason}");
            assert_eq!(report.commands.len(), 1);
            return;
        }
        eprintln!(
            "probe branch: documented probe against turbo {reported} (documentedCliProbed={})",
            report.documented_cli_probed
        );
        assert_eq!(reported, "2.10.5");
        let probe = |id: &str| {
            report
                .commands
                .iter()
                .find(|probe| probe.id == id)
                .expect("command probe")
        };
        let listing = probe("ls");
        assert!(listing.executed && listing.parsed, "{listing:?}");
        assert_eq!(
            listing.summary["staticGraphAgreement"]["matches"],
            json!(true),
            "turbo ls and the static graph disagree: {:?}",
            listing.summary["staticGraphAgreement"],
        );
        let dry_run = probe("dry_run");
        assert!(dry_run.executed && dry_run.parsed, "{dry_run:?}");
        assert_eq!(dry_run.summary["turboVersion"], json!(reported));
        let boundaries = probe("boundaries");
        assert!(boundaries.executed, "{boundaries:?}");
        assert!(
            boundaries.parsed || boundaries.error.is_some(),
            "{boundaries:?}"
        );
        // The affected probe depends on the local git context (a `main` ref
        // may be absent), so executed-with-outcome is the honest assertion.
        let affected = probe("affected");
        assert!(affected.executed, "{affected:?}");
        assert!(affected.parsed || affected.error.is_some(), "{affected:?}");
    }
}
