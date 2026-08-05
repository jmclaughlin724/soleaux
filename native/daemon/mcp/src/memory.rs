//! Native bounded memory discovery for compiled, session, and team surfaces.

use crate::{
    cursor::{decode_cursor, encode_cursor, request_fingerprint},
    envelope::{gap, provenance},
};
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
const MAX_MEMORY_SCAN_CANDIDATES: usize = 10_000;
const MAX_GAPS: usize = 64;

#[derive(Debug, Clone)]
pub struct MemorySearchPage {
    pub data: Value,
    pub attached: bool,
    pub complete: bool,
    pub gaps: Vec<Value>,
    pub truncated: bool,
    pub next_cursor: Option<String>,
    pub snapshot_id: String,
}

#[derive(Debug, Clone)]
struct MemoryRoot {
    scope: String,
    origin: &'static str,
    path: PathBuf,
}

#[derive(Debug, Clone)]
struct MemoryCandidate {
    scope: String,
    origin: &'static str,
    root: PathBuf,
    path: PathBuf,
    relative: String,
    byte_length: u64,
    modified_unix_ms: Option<u64>,
}

pub fn search_memory(
    root: &Path,
    workspace_id: Uuid,
    query: &str,
    scopes: &[String],
    limit: usize,
    cursor: Option<&str>,
) -> Result<MemorySearchPage> {
    let selected_scopes = selected_scopes(scopes);
    let roots = memory_roots(root, &selected_scopes)?;
    let attached = roots.iter().any(|entry| entry.path.is_dir());
    let mut gaps = Vec::new();
    let mut seen_gaps = BTreeSet::new();

    for scope in &selected_scopes {
        if !roots
            .iter()
            .any(|entry| &entry.scope == scope && entry.path.is_dir())
        {
            push_gap(
                &mut gaps,
                &mut seen_gaps,
                gap(
                    "memory_scope_not_attached",
                    &format!("No native {scope} memory directory is attached."),
                    "info",
                    true,
                    Some("memory"),
                    None,
                ),
            );
        }
    }

    let mut candidates = collect_candidates(&roots, &mut gaps, &mut seen_gaps);
    candidates.sort_by(|left, right| {
        (&left.scope, left.origin, &left.relative).cmp(&(
            &right.scope,
            right.origin,
            &right.relative,
        ))
    });
    let snapshot_id = memory_snapshot_id(workspace_id, &selected_scopes, &roots, &candidates);
    let request = json!({
        "query": query.trim(),
        "scopes": selected_scopes.iter().collect::<Vec<_>>(),
    });
    let fingerprint = request_fingerprint("memory-search", workspace_id, &request)?;
    let state = decode_cursor(
        cursor,
        "memory-search",
        &fingerprint,
        &snapshot_id,
        "records",
    )?;
    if state.phase != "records" {
        anyhow::bail!("memory continuation cursor has an unknown phase");
    }
    if state.offset > candidates.len() {
        anyhow::bail!("memory continuation cursor offset is outside the current snapshot");
    }

    let needle = query.trim().to_ascii_lowercase();
    let mut items = Vec::new();
    let mut next_offset = None;
    let mut scanned = 0usize;
    for (candidate_index, candidate) in candidates.iter().enumerate().skip(state.offset) {
        if scanned >= MAX_MEMORY_SCAN_CANDIDATES {
            next_offset = Some(candidate_index);
            push_gap(
                &mut gaps,
                &mut seen_gaps,
                gap(
                    "memory_scan_budget_reached",
                    "The bounded memory scan reached its per-request candidate budget; continue with the returned cursor.",
                    "info",
                    true,
                    Some("memory"),
                    None,
                ),
            );
            break;
        }
        scanned = scanned.saturating_add(1);
        let display_path = format!(
            "{}:{}/{}",
            candidate.scope, candidate.origin, candidate.relative
        );
        if candidate.byte_length > MAX_MEMORY_FILE_BYTES {
            push_gap(
                &mut gaps,
                &mut seen_gaps,
                gap(
                    "memory_file_too_large",
                    "A memory file exceeded the bounded native memory-read limit and was omitted.",
                    "warning",
                    false,
                    Some("memory"),
                    Some(&display_path),
                ),
            );
            continue;
        }
        let canonical = match fs::canonicalize(&candidate.path) {
            Ok(value) if value.starts_with(&candidate.root) => value,
            Ok(_) => {
                push_gap(
                    &mut gaps,
                    &mut seen_gaps,
                    gap(
                        "memory_path_outside_root",
                        "A memory path resolved outside its admitted memory root and was omitted.",
                        "warning",
                        false,
                        Some("memory"),
                        Some(&display_path),
                    ),
                );
                continue;
            }
            Err(_) => {
                push_gap(
                    &mut gaps,
                    &mut seen_gaps,
                    gap(
                        "memory_path_unavailable",
                        "A discovered memory path could not be resolved and was omitted.",
                        "warning",
                        true,
                        Some("memory"),
                        Some(&display_path),
                    ),
                );
                continue;
            }
        };
        let content = match fs::read_to_string(&canonical) {
            Ok(value) => value,
            Err(_) => {
                push_gap(
                    &mut gaps,
                    &mut seen_gaps,
                    gap(
                        "memory_file_unreadable",
                        "A memory file could not be decoded as UTF-8 text and was omitted.",
                        "warning",
                        true,
                        Some("memory"),
                        Some(&display_path),
                    ),
                );
                continue;
            }
        };
        if !needle.is_empty() && !content.to_ascii_lowercase().contains(&needle) {
            continue;
        }
        if items.len() >= limit {
            next_offset = Some(candidate_index);
            break;
        }
        let content_truncated = content.len() > MAX_MEMORY_CONTENT_BYTES;
        let bounded = utf8_prefix(&content, MAX_MEMORY_CONTENT_BYTES);
        if content_truncated {
            push_gap(
                &mut gaps,
                &mut seen_gaps,
                gap(
                    "memory_content_truncated",
                    "A matching memory record exceeded the public content cap; its returned content was truncated.",
                    "info",
                    true,
                    Some("memory"),
                    Some(&display_path),
                ),
            );
        }
        let summary = content
            .lines()
            .map(str::trim)
            .find(|line| !line.is_empty())
            .map(|line| utf8_prefix(line, 1024))
            .unwrap_or_else(|| "Soleaux memory record".to_string());
        let digest = sha256_hex(content.as_bytes());
        let identity = format!(
            "{}:{}:{}",
            candidate.scope, candidate.origin, candidate.relative
        );
        items.push(json!({
            "memory_id": identity,
            "scope": candidate.scope,
            "summary": summary,
            "content": bounded,
            "created_at_unix_ms": candidate.modified_unix_ms,
            "trust": "retrieved_code_data",
            "provenance": provenance(
                "soleaux-native-memory",
                "soleaux-native-memory",
                Some(workspace_id),
                Some(&snapshot_id),
                Some(&display_path),
                Some(&digest),
                "none",
            ),
        }));
    }

    if !attached {
        push_gap(
            &mut gaps,
            &mut seen_gaps,
            gap(
                "memory_surfaces_not_attached",
                "No native compiled-context, session, or team memory directory is attached.",
                "info",
                true,
                Some("memory"),
                None,
            ),
        );
    }
    if next_offset.is_some()
        && !gaps.iter().any(|value| {
            value.get("code").and_then(Value::as_str) == Some("memory_scan_budget_reached")
        })
    {
        push_gap(
            &mut gaps,
            &mut seen_gaps,
            gap(
                "memory_limit_reached",
                "Additional matching memory records remain; continue with the returned cursor.",
                "info",
                true,
                Some("memory"),
                None,
            ),
        );
    }
    let next_cursor = next_offset.map(|offset| {
        encode_cursor(
            "memory-search",
            &fingerprint,
            &snapshot_id,
            "records",
            offset,
        )
    });
    let truncated = next_cursor.is_some();
    let complete = gaps.is_empty() && !truncated;
    let data = json!({
        "query": query,
        "attached": attached,
        "items": items,
        "coverage_complete": complete,
        "gaps": gaps,
    });
    Ok(MemorySearchPage {
        data,
        attached,
        complete,
        gaps,
        truncated,
        next_cursor,
        snapshot_id,
    })
}

fn selected_scopes(scopes: &[String]) -> BTreeSet<String> {
    if scopes.is_empty() {
        ["compiled_context", "session", "team"]
            .into_iter()
            .map(str::to_string)
            .collect()
    } else {
        scopes.iter().cloned().collect()
    }
}

fn collect_candidates(
    roots: &[MemoryRoot],
    gaps: &mut Vec<Value>,
    seen_gaps: &mut BTreeSet<String>,
) -> Vec<MemoryCandidate> {
    let mut candidates = Vec::new();
    for memory_root in roots.iter().filter(|entry| entry.path.is_dir()) {
        let canonical_root = match fs::canonicalize(&memory_root.path) {
            Ok(value) => value,
            Err(_) => {
                push_gap(
                    gaps,
                    seen_gaps,
                    gap(
                        "memory_root_unavailable",
                        "An attached memory root could not be canonicalized and was omitted.",
                        "warning",
                        true,
                        Some("memory"),
                        Some(&memory_root.path.to_string_lossy()),
                    ),
                );
                continue;
            }
        };
        for entry in WalkBuilder::new(&canonical_root)
            .standard_filters(true)
            .hidden(false)
            .max_depth(Some(8))
            .build()
        {
            let entry = match entry {
                Ok(value) => value,
                Err(_) => {
                    push_gap(
                        gaps,
                        seen_gaps,
                        gap(
                            "memory_walk_error",
                            "A memory directory entry could not be traversed and was omitted.",
                            "warning",
                            true,
                            Some("memory"),
                            None,
                        ),
                    );
                    continue;
                }
            };
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
            let relative = path
                .strip_prefix(&canonical_root)
                .unwrap_or(path)
                .to_string_lossy()
                .replace('\\', "/");
            let display_path = format!("{}:{}/{}", memory_root.scope, memory_root.origin, relative);
            let metadata = match entry.metadata() {
                Ok(value) => value,
                Err(_) => {
                    push_gap(
                        gaps,
                        seen_gaps,
                        gap(
                            "memory_metadata_unavailable",
                            "A memory file's metadata could not be read and the file was omitted.",
                            "warning",
                            true,
                            Some("memory"),
                            Some(&display_path),
                        ),
                    );
                    continue;
                }
            };
            candidates.push(MemoryCandidate {
                scope: memory_root.scope.clone(),
                origin: memory_root.origin,
                root: canonical_root.clone(),
                path: path.to_path_buf(),
                relative,
                byte_length: metadata.len(),
                modified_unix_ms: metadata.modified().ok().and_then(unix_ms),
            });
        }
    }
    candidates
}

fn memory_snapshot_id(
    workspace_id: Uuid,
    scopes: &BTreeSet<String>,
    roots: &[MemoryRoot],
    candidates: &[MemoryCandidate],
) -> String {
    let mut hasher = blake3::Hasher::new();
    hasher.update(workspace_id.as_bytes());
    for scope in scopes {
        hash_component(&mut hasher, scope);
    }
    for root in roots {
        hash_component(&mut hasher, &root.scope);
        hash_component(&mut hasher, root.origin);
        hash_component(&mut hasher, &root.path.to_string_lossy());
        hasher.update(&[u8::from(root.path.is_dir())]);
    }
    for candidate in candidates {
        hash_component(&mut hasher, &candidate.scope);
        hash_component(&mut hasher, candidate.origin);
        hash_component(&mut hasher, &candidate.relative);
        hasher.update(&candidate.byte_length.to_le_bytes());
        hasher.update(&candidate.modified_unix_ms.unwrap_or_default().to_le_bytes());
    }
    hasher.finalize().to_hex().to_string()
}

fn hash_component(hasher: &mut blake3::Hasher, value: &str) {
    hasher.update(&(value.len() as u64).to_le_bytes());
    hasher.update(value.as_bytes());
}

fn push_gap(gaps: &mut Vec<Value>, seen: &mut BTreeSet<String>, value: Value) {
    if gaps.len() >= MAX_GAPS {
        return;
    }
    let key = format!(
        "{}:{}",
        value
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        value
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    if seen.insert(key) {
        gaps.push(value);
    }
}

fn memory_roots(root: &Path, scopes: &BTreeSet<String>) -> Result<Vec<MemoryRoot>> {
    let soleaux_home = std::env::var_os("SOLEAUX_HOME")
        .map(PathBuf::from)
        .or_else(|| dirs::home_dir().map(|path| path.join(".soleaux")))
        .context("unable to determine SOLEAUX_HOME")?;
    let mut roots = Vec::new();
    if scopes.contains("compiled_context") {
        roots.push(MemoryRoot {
            scope: "compiled_context".to_string(),
            origin: "user",
            path: soleaux_home.join("compiled-context"),
        });
        roots.push(MemoryRoot {
            scope: "compiled_context".to_string(),
            origin: "workspace",
            path: root.join(".soleaux/compiled-context"),
        });
    }
    if scopes.contains("session") {
        roots.push(MemoryRoot {
            scope: "session".to_string(),
            origin: "user",
            path: soleaux_home.join("sessions"),
        });
        roots.push(MemoryRoot {
            scope: "session".to_string(),
            origin: "workspace",
            path: root.join(".soleaux/sessions"),
        });
    }
    if scopes.contains("team") {
        roots.push(MemoryRoot {
            scope: "team".to_string(),
            origin: "user",
            path: soleaux_home.join("team-memory"),
        });
        roots.push(MemoryRoot {
            scope: "team".to_string(),
            origin: "workspace",
            path: root.join(".soleaux/team-memory"),
        });
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

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn memory_pages_are_disjoint_and_cursor_is_request_bound() {
        let root = tempdir().expect("root");
        let home = tempdir().expect("home");
        let _home_guard = crate::test_environment::SoleauxHomeGuard::set(home.path());
        let memory = root.path().join(".soleaux/sessions");
        fs::create_dir_all(&memory).expect("memory root");
        for index in 0..3 {
            fs::write(
                memory.join(format!("{index}.md")),
                format!("needle {index}"),
            )
            .expect("memory file");
        }
        let workspace_id = Uuid::nil();
        let first = search_memory(
            root.path(),
            workspace_id,
            "needle",
            &["session".to_string()],
            2,
            None,
        )
        .expect("first page");
        assert!(first.truncated);
        assert_eq!(first.data["items"].as_array().expect("items").len(), 2);
        let second = search_memory(
            root.path(),
            workspace_id,
            "needle",
            &["session".to_string()],
            2,
            first.next_cursor.as_deref(),
        )
        .expect("second page");
        assert!(!second.truncated);
        assert_eq!(second.data["items"].as_array().expect("items").len(), 1);
        assert!(
            search_memory(
                root.path(),
                workspace_id,
                "different",
                &["session".to_string()],
                2,
                first.next_cursor.as_deref(),
            )
            .is_err()
        );
    }

    #[test]
    fn memory_reports_missing_scope_and_content_truncation() {
        let root = tempdir().expect("root");
        let home = tempdir().expect("home");
        let _home_guard = crate::test_environment::SoleauxHomeGuard::set(home.path());
        let memory = root.path().join(".soleaux/sessions");
        fs::create_dir_all(&memory).expect("memory root");
        fs::write(
            memory.join("large.md"),
            "x".repeat(MAX_MEMORY_CONTENT_BYTES + 10),
        )
        .expect("memory file");
        let page = search_memory(
            root.path(),
            Uuid::nil(),
            "x",
            &["session".to_string(), "team".to_string()],
            20,
            None,
        )
        .expect("page");
        let codes = page
            .gaps
            .iter()
            .filter_map(|value| value.get("code").and_then(Value::as_str))
            .collect::<BTreeSet<_>>();
        assert!(codes.contains("memory_scope_not_attached"));
        assert!(codes.contains("memory_content_truncated"));
        assert!(!page.complete);
    }
}
