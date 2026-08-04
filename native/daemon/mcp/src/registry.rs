//! Native registry projection for rules, skills, agents, ownership, MCP backends,
//! and the fixed SQLite-backed repository tables.

use anyhow::{Context, Result};
use glob::Pattern;
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use soleaux_intelligence::{
    governance::build_governance_graph, index::RepositoryIndex, nextjs::index_nextjs,
    turborepo::load_graph,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::{Path, PathBuf},
};

pub const REGISTRY_DOMAINS: [&str; 6] = [
    "tables",
    "ownership",
    "skills",
    "agents",
    "rules",
    "mcp_backends",
];

const MAX_REGISTRY_FILE_BYTES: u64 = 256 * 1024;
const MAX_ENTRY_CONTENT_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct RegistryEntry {
    pub id: String,
    pub domain: String,
    pub name: String,
    pub summary: String,
    pub version: String,
    pub digest: String,
    pub available: bool,
    pub path: Option<String>,
    pub content: Option<String>,
    pub metadata: Value,
}

impl RegistryEntry {
    pub fn compact(&self) -> Value {
        json!({
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "summary": self.summary,
            "version": self.version,
            "digest": self.digest,
            "available": self.available,
        })
    }

    pub fn expanded(&self) -> Value {
        json!({
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "summary": self.summary,
            "version": self.version,
            "digest": self.digest,
            "available": self.available,
            "path": self.path,
            "content": self.content,
            "metadata": self.metadata,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct OwnershipRecord {
    pub id: String,
    pub pattern: String,
    pub owners: Vec<String>,
    pub source_path: String,
    pub line: usize,
    pub digest: String,
}

impl OwnershipRecord {
    pub fn as_value(&self) -> Value {
        json!({
            "id": self.id,
            "pattern": self.pattern,
            "owners": self.owners,
            "source_path": self.source_path,
            "line": self.line,
            "digest": self.digest,
        })
    }

    pub fn matches(&self, relative: &str) -> bool {
        let normalized = self.pattern.trim_start_matches('/');
        Pattern::new(normalized)
            .map(|pattern| pattern.matches(relative))
            .unwrap_or_else(|_| relative == normalized || relative.starts_with(normalized))
    }
}

#[derive(Debug, Clone)]
pub struct RegistrySnapshot {
    pub entries: Vec<RegistryEntry>,
    pub ownership: Vec<OwnershipRecord>,
    pub catalog_digest: String,
}

impl RegistrySnapshot {
    pub fn list(&self, domain: Option<&str>, limit: usize, offset: usize) -> Value {
        let mut counts = BTreeMap::<&str, usize>::new();
        for name in REGISTRY_DOMAINS {
            counts.insert(name, 0);
        }
        for entry in &self.entries {
            *counts.entry(entry.domain.as_str()).or_default() += 1;
        }
        counts.insert("ownership", self.ownership.len());
        counts.insert("tables", builtin_table_names().len());

        let domains = REGISTRY_DOMAINS
            .iter()
            .map(|name| {
                json!({
                    "name": name,
                    "count": counts.get(name).copied().unwrap_or(0),
                    "available": true,
                    "coverage_complete": true,
                })
            })
            .collect::<Vec<_>>();

        let mut entries = Vec::new();
        if domain.is_none() || domain == Some("tables") {
            entries.extend(builtin_table_entries());
        }
        if domain.is_none() || domain == Some("ownership") {
            entries.extend(self.ownership.iter().map(|record| {
                json!({
                    "id": record.id,
                    "domain": "ownership",
                    "name": record.pattern,
                    "summary": format!("Canonical owners: {}", record.owners.join(", ")),
                    "version": "1",
                    "digest": record.digest,
                    "available": true,
                })
            }));
        }
        entries.extend(
            self.entries
                .iter()
                .filter(|entry| domain.is_none_or(|value| value == entry.domain))
                .map(RegistryEntry::compact),
        );
        entries.sort_by(|left, right| {
            left.get("id")
                .and_then(Value::as_str)
                .cmp(&right.get("id").and_then(Value::as_str))
        });
        let page = entries
            .into_iter()
            .skip(offset)
            .take(limit)
            .collect::<Vec<_>>();
        json!({
            "domains": domains,
            "entries": page,
            "catalog_digest": self.catalog_digest,
        })
    }

    pub fn read(
        &self,
        index: &RepositoryIndex,
        arguments: &Value,
    ) -> Result<(Value, Vec<Value>, Vec<String>)> {
        let limit = arguments
            .get("limit")
            .and_then(Value::as_u64)
            .unwrap_or(50)
            .clamp(1, 200) as usize;
        let include_ownership = arguments
            .get("include_ownership")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let mut gaps = Vec::new();
        let mut warnings = Vec::new();

        if let Some(tables) = arguments.get("tables").and_then(Value::as_array) {
            let excluded = arguments
                .get("exclude_tables")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect::<BTreeSet<_>>();
            let requested = tables
                .iter()
                .filter_map(Value::as_str)
                .filter(|name| !excluded.contains(name))
                .collect::<Vec<_>>();
            let (table_values, table_gaps) = read_tables(index, self, &requested, limit)?;
            gaps.extend(table_gaps);
            return Ok((
                json!({
                    "domain": Value::Null,
                    "entries": [],
                    "tables": table_values,
                    "ownership": if include_ownership { self.ownership.iter().map(OwnershipRecord::as_value).take(limit).collect::<Vec<_>>() } else { Vec::new() },
                    "coverage_complete": gaps.is_empty(),
                    "gaps": gaps,
                }),
                Vec::new(),
                warnings,
            ));
        }

        let domain = arguments
            .get("domain")
            .and_then(Value::as_str)
            .context("registry.read requires domain+ids or tables")?;
        let ids = arguments
            .get("ids")
            .and_then(Value::as_array)
            .context("registry.read domain mode requires ids")?
            .iter()
            .filter_map(Value::as_str)
            .collect::<BTreeSet<_>>();
        let mut entries = Vec::new();
        let mut ownership = Vec::new();
        if domain == "ownership" {
            for record in &self.ownership {
                if ids.contains(record.id.as_str()) || ids.contains(record.pattern.as_str()) {
                    ownership.push(record.as_value());
                }
            }
        } else if domain == "tables" {
            let requested = ids.iter().copied().collect::<Vec<_>>();
            let (tables, table_gaps) = read_tables(index, self, &requested, limit)?;
            gaps.extend(table_gaps);
            return Ok((
                json!({
                    "domain": domain,
                    "entries": [],
                    "tables": tables,
                    "ownership": if include_ownership { self.ownership.iter().map(OwnershipRecord::as_value).take(limit).collect::<Vec<_>>() } else { Vec::new() },
                    "coverage_complete": gaps.is_empty(),
                    "gaps": gaps,
                }),
                Vec::new(),
                warnings,
            ));
        } else {
            for entry in &self.entries {
                if entry.domain == domain && ids.contains(entry.id.as_str()) {
                    entries.push(entry.expanded());
                }
            }
        }
        if entries.len() + ownership.len() < ids.len() {
            gaps.push(gap(
                "registry_ids_unresolved",
                "One or more requested registry identifiers were not present in the native registry projection.",
                "warning",
                false,
                None,
                Some(domain),
            ));
        }
        if domain == "mcp_backends" && entries.is_empty() {
            warnings.push("No [mcp.*] backends were declared in soleaux.toml.".to_string());
        }
        Ok((
            json!({
                "domain": domain,
                "entries": entries.into_iter().take(limit).collect::<Vec<_>>(),
                "tables": {},
                "ownership": if include_ownership || domain == "ownership" { ownership.into_iter().take(limit).collect::<Vec<_>>() } else { Vec::new() },
                "coverage_complete": gaps.is_empty(),
                "gaps": gaps,
            }),
            Vec::new(),
            warnings,
        ))
    }
}

pub fn scan_registry(root: &Path, index: &RepositoryIndex) -> Result<RegistrySnapshot> {
    let root = &fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let mut entries = Vec::new();
    let mut ownership = Vec::new();
    let walker = WalkBuilder::new(root)
        .standard_filters(true)
        .hidden(false)
        .add_custom_ignore_filename(".soleauxignore")
        .build();
    for entry in walker.flatten() {
        let Some(file_type) = entry.file_type() else {
            continue;
        };
        if !file_type.is_file() || file_type.is_symlink() {
            continue;
        }
        let path = entry.path();
        let metadata = match entry.metadata() {
            Ok(value) if value.len() <= MAX_REGISTRY_FILE_BYTES => value,
            _ => continue,
        };
        let canonical = match fs::canonicalize(path) {
            Ok(value) if value.starts_with(root) => value,
            _ => continue,
        };
        let relative = canonical
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        if is_codeowners_path(&relative) {
            ownership.extend(parse_codeowners(&canonical, &relative)?);
            continue;
        }
        if let Some(domain) = classify_registry_path(&relative) {
            let content = fs::read_to_string(&canonical).unwrap_or_default();
            let bounded = utf8_prefix(&content, MAX_ENTRY_CONTENT_BYTES);
            let digest = sha256_hex(content.as_bytes());
            entries.push(RegistryEntry {
                id: format!("{domain}:{relative}"),
                domain: domain.to_string(),
                name: registry_name(&relative),
                summary: first_meaningful_line(&content)
                    .unwrap_or_else(|| format!("Soleaux {domain} registry object")),
                version: digest[..12].to_string(),
                digest,
                available: true,
                path: Some(relative.clone()),
                content: Some(bounded),
                metadata: json!({"byte_length":metadata.len(),"source":"repository"}),
            });
        }
    }
    entries.extend(scan_external_catalogs(root)?);
    entries.extend(parse_mcp_backends(root)?);
    entries.sort_by(|left, right| left.id.cmp(&right.id));
    entries.dedup_by(|left, right| left.id == right.id);
    ownership.sort_by(|left, right| {
        (left.source_path.as_str(), left.line).cmp(&(right.source_path.as_str(), right.line))
    });
    let digest_input = serde_json::to_vec(&json!({
        "workspace_id": index.workspace_id(),
        "entries": entries.iter().map(RegistryEntry::compact).collect::<Vec<_>>(),
        "ownership": ownership.iter().map(OwnershipRecord::as_value).collect::<Vec<_>>(),
        "tables": builtin_table_names(),
    }))?;
    Ok(RegistrySnapshot {
        entries,
        ownership,
        catalog_digest: sha256_hex(&digest_input),
    })
}

pub fn ownership_for_path<'a>(
    snapshot: &'a RegistrySnapshot,
    path: &str,
) -> Vec<&'a OwnershipRecord> {
    snapshot
        .ownership
        .iter()
        .filter(|record| record.matches(path))
        .collect()
}

fn read_tables(
    index: &RepositoryIndex,
    snapshot: &RegistrySnapshot,
    requested: &[&str],
    limit: usize,
) -> Result<(Value, Vec<Value>)> {
    let mut tables = serde_json::Map::new();
    let mut gaps = Vec::new();
    let files = index
        .store()
        .files(index.workspace_id(), limit.max(1_000))?;
    for name in requested {
        let value = match *name {
            "workspaces" => json!({
                "rows": [{"workspace_id":index.workspace_id(),"root":index.root().to_string_lossy()}],
                "coverage_complete": true,
            }),
            "indexed_files" => json!({
                "rows": files.iter().take(limit).collect::<Vec<_>>(),
                "coverage_complete": files.len() <= limit,
            }),
            "symbols" => {
                let mut rows = Vec::new();
                for file in &files {
                    for symbol in index
                        .store()
                        .symbols_for_file(index.workspace_id(), &file.path)?
                    {
                        rows.push(json!({"path":file.path,"symbol":symbol}));
                        if rows.len() >= limit {
                            break;
                        }
                    }
                    if rows.len() >= limit {
                        break;
                    }
                }
                json!({"rows":rows,"coverage_complete":rows.len() < limit})
            }
            "ownership" => json!({
                "rows":snapshot.ownership.iter().map(OwnershipRecord::as_value).take(limit).collect::<Vec<_>>(),
                "coverage_complete":snapshot.ownership.len() <= limit,
            }),
            "catalog" => json!({
                "rows":snapshot.entries.iter().map(RegistryEntry::expanded).take(limit).collect::<Vec<_>>(),
                "coverage_complete":snapshot.entries.len() <= limit,
                "catalog_digest":snapshot.catalog_digest,
            }),
            "frameworks" => json!({
                "rows":detect_frameworks(index.root()),
                "coverage_complete":true,
            }),
            "packages" => match load_graph(index.root()) {
                Ok(graph) => {
                    json!({"rows":graph.packages.into_iter().take(limit).collect::<Vec<_>>(),"coverage_complete":true,"provider":graph.provider})
                }
                Err(error) => {
                    gaps.push(gap(
                        "turborepo_graph_unavailable",
                        &error.to_string(),
                        "info",
                        true,
                        None,
                        Some("packages"),
                    ));
                    json!({"rows":[],"coverage_complete":false})
                }
            },
            "governance" => {
                let graph = build_governance_graph(index.root(), &[])?;
                if !graph.coverage_complete {
                    gaps.extend(graph.gaps.clone());
                }
                json!({
                    "rows":graph.edges.into_iter().take(limit).collect::<Vec<_>>(),
                    "coverage_complete":graph.coverage_complete,
                    "digest":graph.digest,
                    "provider":"soleaux-native-governance-graph",
                })
            }
            "routes" => match index_nextjs(index.root()) {
                Ok(route_index) => {
                    json!({"rows":route_index.routes.into_iter().take(limit).collect::<Vec<_>>(),"coverage_complete":true,"provider":route_index.provider})
                }
                Err(error) => {
                    gaps.push(gap(
                        "nextjs_route_index_unavailable",
                        &error.to_string(),
                        "info",
                        true,
                        None,
                        Some("routes"),
                    ));
                    json!({"rows":[],"coverage_complete":false})
                }
            },
            unknown => {
                gaps.push(gap(
                    "unknown_registry_table",
                    &format!("Unknown registry table: {unknown}"),
                    "warning",
                    false,
                    None,
                    Some(unknown),
                ));
                json!({"rows":[],"coverage_complete":false})
            }
        };
        tables.insert((*name).to_string(), value);
    }
    Ok((Value::Object(tables), gaps))
}

pub fn detect_frameworks(root: &Path) -> Vec<Value> {
    let mut frameworks = Vec::new();
    if root.join("turbo.json").is_file() {
        frameworks
            .push(json!({"name":"turborepo","provider":"static+documented-cli","native":true}));
    }
    let mut next_roots = BTreeSet::new();
    for entry in WalkBuilder::new(root)
        .standard_filters(true)
        .hidden(false)
        .max_depth(Some(5))
        .build()
        .flatten()
    {
        if !entry.file_type().is_some_and(|kind| kind.is_file()) {
            continue;
        }
        let name = entry.file_name().to_string_lossy();
        if matches!(
            name.as_ref(),
            "next.config.js" | "next.config.mjs" | "next.config.cjs" | "next.config.ts"
        ) && let Some(parent) = entry.path().parent()
        {
            next_roots.insert(
                parent
                    .strip_prefix(root)
                    .unwrap_or(parent)
                    .to_string_lossy()
                    .replace('\\', "/"),
            );
        }
    }
    for app_root in next_roots {
        frameworks.push(json!({"name":"nextjs","app_root":app_root,"provider":"oxc-static+capability-driven-runtime","native":true}));
    }
    if root.join("Cargo.toml").is_file() {
        frameworks.push(json!({"name":"rust","provider":"cargo","native":true}));
    }
    if root.join("pyproject.toml").is_file() {
        frameworks.push(json!({"name":"python","provider":"tree-sitter+native-lsp","native":true}));
    }
    frameworks
}

fn parse_codeowners(path: &Path, relative: &str) -> Result<Vec<OwnershipRecord>> {
    let content = fs::read_to_string(path).with_context(|| format!("reading {relative}"))?;
    let mut output = Vec::new();
    for (index, raw) in content.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 2 {
            continue;
        }
        let pattern = fields[0].to_string();
        let owners = fields[1..]
            .iter()
            .map(|value| (*value).to_string())
            .collect::<Vec<_>>();
        let digest = sha256_hex(line.as_bytes());
        output.push(OwnershipRecord {
            id: format!("ownership:{relative}:{}", index + 1),
            pattern,
            owners,
            source_path: relative.to_string(),
            line: index + 1,
            digest,
        });
    }
    Ok(output)
}

fn parse_mcp_backends(root: &Path) -> Result<Vec<RegistryEntry>> {
    crate::gateway::discover_backends(root)?
        .into_iter()
        .map(|backend| {
            let authenticated = backend.auth == "none"
                || crate::gateway::credential_present(&backend.name).unwrap_or(false);
            let digest = backend.config_digest.clone();
            Ok(RegistryEntry {
                id: format!("mcp_backends:{}", backend.name),
                domain: "mcp_backends".to_string(),
                name: backend.name.clone(),
                summary:
                    "Configured namespaced Soleaux gateway backend; authentication is CLI-mediated."
                        .to_string(),
                version: digest[..12].to_string(),
                digest,
                available: backend.enabled,
                path: Some(backend.config_path.clone()),
                content: None,
                metadata: json!({
                    "namespace":backend.namespace,
                    "transport":backend.transport,
                    "auth":backend.auth,
                    "authenticated":authenticated,
                    "scopes":backend.scopes,
                    "root_tool_inflation":false,
                    "production_runtime":"rust",
                }),
            })
        })
        .collect()
}

fn scan_external_catalogs(workspace_root: &Path) -> Result<Vec<RegistryEntry>> {
    let mut roots = Vec::<(&str, PathBuf)>::new();
    if let Ok(home) = crate::gateway::soleaux_home() {
        roots.push(("user", home.join("catalog")));
    }
    if let Some(team) = env::var_os("SOLEAUX_TEAM_CATALOG") {
        roots.push(("team", PathBuf::from(team)));
    }
    let workspace_catalog = workspace_root.join(".soleaux/catalog");
    roots.push(("workspace", workspace_catalog));
    let mut output = Vec::new();
    for (scope, root) in roots {
        if !root.is_dir() {
            continue;
        }
        let canonical_root = match fs::canonicalize(&root) {
            Ok(value) => value,
            Err(_) => continue,
        };
        for entry in WalkBuilder::new(&canonical_root)
            .standard_filters(true)
            .hidden(false)
            .build()
            .flatten()
        {
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                continue;
            }
            let path = entry.path();
            let metadata = match entry.metadata() {
                Ok(value) if value.len() <= MAX_REGISTRY_FILE_BYTES => value,
                _ => continue,
            };
            let canonical = match fs::canonicalize(path) {
                Ok(value) if value.starts_with(&canonical_root) => value,
                _ => continue,
            };
            let relative = canonical
                .strip_prefix(&canonical_root)
                .unwrap_or(path)
                .to_string_lossy()
                .replace('\\', "/");
            let first = Path::new(&relative)
                .components()
                .next()
                .and_then(|component| component.as_os_str().to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            let domain = match first.as_str() {
                "skills" => "skills",
                "agents" => "agents",
                "rules" => "rules",
                _ => continue,
            };
            let content = fs::read_to_string(&canonical).unwrap_or_default();
            let digest = sha256_hex(content.as_bytes());
            output.push(RegistryEntry {
                id: format!("{domain}:{scope}:{relative}"),
                domain: domain.to_string(),
                name: registry_name(&relative),
                summary: first_meaningful_line(&content)
                    .unwrap_or_else(|| format!("Soleaux {scope} {domain} registry object")),
                version: digest[..12].to_string(),
                digest,
                available: true,
                path: Some(canonical.to_string_lossy().to_string()),
                content: Some(utf8_prefix(&content, MAX_ENTRY_CONTENT_BYTES)),
                metadata: json!({
                    "byte_length":metadata.len(),
                    "scope":scope,
                    "source":"soleaux_catalog",
                    "root_tool_inflation":false,
                }),
            });
        }
    }
    Ok(output)
}

fn classify_registry_path(relative: &str) -> Option<&'static str> {
    let normalized = relative.to_ascii_lowercase();
    let file_name = Path::new(relative)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if file_name == "skill.md" && normalized.split('/').any(|component| component == "skills") {
        return Some("skills");
    }
    if normalized.contains("/agents/") || normalized.starts_with(".agents/agents/") {
        return Some("agents");
    }
    if matches!(file_name.as_str(), "agents.md" | "claude.md")
        || normalized.contains("/.cursor/rules/")
        || normalized.starts_with(".cursor/rules/")
        || normalized.contains("/.claude/rules/")
        || normalized.starts_with(".claude/rules/")
        || normalized.contains("/.codex/rules/")
        || normalized.starts_with(".codex/rules/")
    {
        return Some("rules");
    }
    None
}

fn registry_name(relative: &str) -> String {
    let path = Path::new(relative);
    if path.file_name().and_then(|value| value.to_str()) == Some("SKILL.md") {
        return path
            .parent()
            .and_then(Path::file_name)
            .and_then(|value| value.to_str())
            .unwrap_or("skill")
            .to_string();
    }
    path.file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or(relative)
        .to_string()
}

fn first_meaningful_line(content: &str) -> Option<String> {
    content
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty() && !line.starts_with("---") && !line.starts_with('#'))
        .map(|line| utf8_prefix(line, 512))
}

fn is_codeowners_path(relative: &str) -> bool {
    matches!(
        relative,
        "CODEOWNERS" | ".github/CODEOWNERS" | "docs/CODEOWNERS"
    )
}

fn builtin_table_names() -> Vec<&'static str> {
    vec![
        "workspaces",
        "indexed_files",
        "symbols",
        "ownership",
        "catalog",
        "frameworks",
        "packages",
        "routes",
        "governance",
    ]
}

fn builtin_table_entries() -> Vec<Value> {
    builtin_table_names()
        .into_iter()
        .map(|name| {
            json!({
                "id":format!("tables:{name}"),
                "domain":"tables",
                "name":name,
                "summary":"Native Soleaux registry table",
                "version":"1",
                "digest":sha256_hex(name.as_bytes()),
                "available":true,
            })
        })
        .collect()
}

pub fn gap(
    code: &str,
    message: &str,
    severity: &str,
    retryable: bool,
    path: Option<&str>,
    table: Option<&str>,
) -> Value {
    json!({
        "code":utf8_prefix(code,128),
        "message":utf8_prefix(message,1024),
        "severity":severity,
        "retryable":retryable,
        "path":path,
        "table":table,
    })
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn utf8_prefix(value: &str, maximum_bytes: usize) -> String {
    if value.len() <= maximum_bytes {
        return value.to_string();
    }
    let mut end = maximum_bytes.min(value.len());
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use soleaux_intelligence::index::IndexConfig;
    use soleaux_storage::Store;
    use tempfile::tempdir;

    #[tokio::test]
    async fn registry_discovers_rules_skills_and_ownership() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".agents/skills/example")).expect("skills");
        fs::create_dir_all(directory.path().join(".github")).expect("github");
        fs::write(
            directory.path().join(".agents/skills/example/SKILL.md"),
            "# Example\nNative skill",
        )
        .expect("skill");
        fs::write(
            directory.path().join("AGENTS.md"),
            "# Rules\nKeep tools lean",
        )
        .expect("rules");
        fs::write(
            directory.path().join(".github/CODEOWNERS"),
            "src/** @soleaux/core",
        )
        .expect("owners");
        let store = Store::open(directory.path().join("index.sqlite3")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("refresh");
        let snapshot = scan_registry(directory.path(), &index).expect("registry");
        assert!(
            snapshot
                .entries
                .iter()
                .any(|entry| entry.domain == "skills")
        );
        assert!(snapshot.entries.iter().any(|entry| entry.domain == "rules"));
        assert_eq!(snapshot.ownership.len(), 1);
        assert_eq!(ownership_for_path(&snapshot, "src/lib.rs").len(), 1);
    }
}
