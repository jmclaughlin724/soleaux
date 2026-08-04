//! Native bounded memory discovery for compiled, session, and team surfaces.

use crate::envelope::{gap, provenance};
use anyhow::{Context, Result};
use ignore::WalkBuilder;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

const MAX_MEMORY_FILE_BYTES: u64 = 512 * 1024;
const MAX_MEMORY_CONTENT_BYTES: usize = 16 * 1024;

pub fn search_memory(
    root: &Path,
    workspace_id: Uuid,
    query: &str,
    scopes: &[String],
    limit: usize,
) -> Result<Value> {
    let selected_scopes = if scopes.is_empty() {
        ["compiled_context", "session", "team"]
            .into_iter()
            .map(str::to_string)
            .collect::<BTreeSet<_>>()
    } else {
        scopes.iter().cloned().collect::<BTreeSet<_>>()
    };
    let roots = memory_roots(root, &selected_scopes)?;
    let attached = roots.iter().any(|(_, path)| path.is_dir());
    let needle = query.trim().to_ascii_lowercase();
    let mut items = Vec::new();
    let mut seen = BTreeSet::new();
    for (scope, memory_root) in roots {
        if !memory_root.is_dir() {
            continue;
        }
        for entry in WalkBuilder::new(&memory_root)
            .standard_filters(true)
            .hidden(false)
            .max_depth(Some(8))
            .build()
            .flatten()
        {
            if items.len() >= limit {
                break;
            }
            let Some(kind) = entry.file_type() else {
                continue;
            };
            if !kind.is_file() || kind.is_symlink() {
                continue;
            }
            let path = entry.path();
            let extension = path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            if !matches!(extension, "md" | "txt" | "json" | "jsonl" | "yaml" | "yml") {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(value) if value.len() <= MAX_MEMORY_FILE_BYTES => value,
                _ => continue,
            };
            let canonical = match fs::canonicalize(path) {
                Ok(value) if value.starts_with(&memory_root) => value,
                _ => continue,
            };
            let content = match fs::read_to_string(&canonical) {
                Ok(value) => value,
                Err(_) => continue,
            };
            if !needle.is_empty() && !content.to_ascii_lowercase().contains(&needle) {
                continue;
            }
            let relative = canonical
                .strip_prefix(&memory_root)
                .unwrap_or(&canonical)
                .to_string_lossy()
                .replace('\\', "/");
            let identity = format!("{scope}:{relative}");
            if !seen.insert(identity.clone()) {
                continue;
            }
            let bounded = utf8_prefix(&content, MAX_MEMORY_CONTENT_BYTES);
            let summary = content
                .lines()
                .map(str::trim)
                .find(|line| !line.is_empty())
                .map(|line| utf8_prefix(line, 1024))
                .unwrap_or_else(|| "Soleaux memory record".to_string());
            let digest = sha256_hex(content.as_bytes());
            items.push(json!({
                "memory_id": identity,
                "scope": scope,
                "summary": summary,
                "content": bounded,
                "created_at_unix_ms": metadata.modified().ok().and_then(unix_ms),
                "trust": "retrieved_code_data",
                "provenance": provenance(
                    "soleaux-native-memory",
                    "soleaux-native-memory",
                    Some(workspace_id),
                    None,
                    Some(&relative),
                    Some(&digest),
                    "none",
                ),
            }));
        }
    }
    let gaps = if attached {
        Vec::new()
    } else {
        vec![gap(
            "memory_surfaces_not_attached",
            "No native compiled-context, session, or team memory directory is attached.",
            "info",
            true,
            Some("memory"),
            None,
        )]
    };
    Ok(json!({
        "query": query,
        "attached": attached,
        "items": items,
        "coverage_complete": attached,
        "gaps": gaps,
    }))
}

fn memory_roots(root: &Path, scopes: &BTreeSet<String>) -> Result<Vec<(String, PathBuf)>> {
    let soleaux_home = std::env::var_os("SOLEAUX_HOME")
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|path| path.join(".soleaux")))
        .context("unable to determine SOLEAUX_HOME")?;
    let mut roots = Vec::new();
    if scopes.contains("compiled_context") {
        roots.push((
            "compiled_context".to_string(),
            soleaux_home.join("compiled-context"),
        ));
        roots.push((
            "compiled_context".to_string(),
            root.join(".soleaux/compiled-context"),
        ));
    }
    if scopes.contains("session") {
        roots.push(("session".to_string(), soleaux_home.join("sessions")));
        roots.push(("session".to_string(), root.join(".soleaux/sessions")));
    }
    if scopes.contains("team") {
        roots.push(("team".to_string(), soleaux_home.join("team-memory")));
        roots.push(("team".to_string(), root.join(".soleaux/team-memory")));
    }
    Ok(roots)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn utf8_prefix(value: &str, maximum: usize) -> String {
    if value.len() <= maximum {
        return value.to_string();
    }
    let mut end = maximum;
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_string()
}

fn unix_ms(value: SystemTime) -> Option<u64> {
    value
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| duration.as_millis().try_into().ok())
}
