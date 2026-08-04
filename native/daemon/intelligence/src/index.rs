//! Incremental on-disk repository index.

use crate::{
    Grammar, OXC_ENGINE_VERSION, ParseCache, ParseEnvelope, ParseKey, StructuralRange,
    TREE_SITTER_ENGINE_VERSION, parse_oxc, parse_tree_sitter, parser_fingerprint,
};
use anyhow::{Context, Result, bail};
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use soleaux_storage::{
    IndexedFileRecord, Store, StoreStats, SymbolHit, SymbolRecord, WorkspaceRecord, unix_ms,
};
use std::{
    collections::BTreeSet,
    fs,
    path::{Component, Path, PathBuf},
    sync::Arc,
    time::Instant,
};
use uuid::Uuid;

const DEFAULT_MAX_FILES: usize = 250_000;
const DEFAULT_MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;
const DEFAULT_MINIFIED_LINE_BYTES: usize = 32 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct IndexConfig {
    pub maximum_files: usize,
    pub maximum_file_bytes: u64,
    pub maximum_minified_line_bytes: usize,
}

impl Default for IndexConfig {
    fn default() -> Self {
        Self {
            maximum_files: DEFAULT_MAX_FILES,
            maximum_file_bytes: DEFAULT_MAX_FILE_BYTES,
            maximum_minified_line_bytes: DEFAULT_MINIFIED_LINE_BYTES,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct IndexReport {
    pub workspace_id: Uuid,
    pub root: String,
    pub scanned_files: usize,
    pub indexed_files: usize,
    pub skipped_files: usize,
    pub removed_files: usize,
    pub parse_errors: usize,
    pub duration_ms: u64,
    pub cancelled: bool,
}

#[derive(Clone)]
pub struct RepositoryIndex {
    root: Arc<PathBuf>,
    workspace_id: Uuid,
    store: Store,
    parse_cache: ParseCache,
    config: IndexConfig,
}

impl RepositoryIndex {
    pub fn open(root: impl AsRef<Path>, store: Store, config: IndexConfig) -> Result<Self> {
        let root = fs::canonicalize(root.as_ref())
            .with_context(|| format!("resolving repository {}", root.as_ref().display()))?;
        if !root.is_dir() {
            bail!("repository root is not a directory");
        }
        let workspace_id = workspace_id_for_root(&root);
        let now = unix_ms();
        store.upsert_workspace(WorkspaceRecord {
            id: workspace_id,
            root: root.to_string_lossy().to_string(),
            display_name: root
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("repository")
                .to_string(),
            identity_hash: blake3::hash(root.to_string_lossy().as_bytes())
                .to_hex()
                .to_string(),
            created_at_unix_ms: now,
            updated_at_unix_ms: now,
        })?;
        Ok(Self {
            root: Arc::new(root),
            workspace_id,
            store,
            parse_cache: ParseCache::new(256 * 1024 * 1024),
            config,
        })
    }

    pub fn root(&self) -> &Path {
        self.root.as_ref()
    }

    pub fn workspace_id(&self) -> Uuid {
        self.workspace_id
    }

    pub fn store(&self) -> &Store {
        &self.store
    }

    pub fn resolve_existing_path(&self, relative: &str) -> Result<PathBuf> {
        let relative_path = Path::new(relative);
        if relative_path.is_absolute()
            || relative_path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::RootDir | Component::Prefix(_)
                )
            })
        {
            bail!("path must remain relative to the repository root");
        }
        let candidate = self.root.join(relative_path);
        let canonical = fs::canonicalize(&candidate)
            .with_context(|| format!("resolving {}", candidate.display()))?;
        if !canonical.starts_with(self.root()) {
            bail!("resolved path escaped the repository root");
        }
        Ok(canonical)
    }

    pub async fn refresh(&self) -> Result<IndexReport> {
        let started = Instant::now();
        let mut scanned_files = 0usize;
        let mut indexed_files = 0usize;
        let mut skipped_files = 0usize;
        let mut parse_errors = 0usize;
        let mut present = BTreeSet::new();
        let walker = WalkBuilder::new(self.root())
            .standard_filters(true)
            .hidden(false)
            .add_custom_ignore_filename(".soleauxignore")
            .build();

        for entry in walker {
            let entry = match entry {
                Ok(value) => value,
                Err(_) => {
                    skipped_files += 1;
                    continue;
                }
            };
            let Some(file_type) = entry.file_type() else {
                continue;
            };
            if file_type.is_dir() {
                continue;
            }
            if file_type.is_symlink() {
                skipped_files += 1;
                continue;
            }
            if scanned_files >= self.config.maximum_files {
                break;
            }
            scanned_files += 1;
            let path = entry.path();
            let canonical_path = match fs::canonicalize(path) {
                Ok(value) if value.starts_with(self.root()) => value,
                _ => {
                    skipped_files += 1;
                    continue;
                }
            };
            let relative = canonical_path
                .strip_prefix(self.root())
                .unwrap_or(path)
                .to_string_lossy()
                .replace('\\', "/");
            if should_skip_path(&relative) {
                skipped_files += 1;
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(value) => value,
                Err(_) => {
                    skipped_files += 1;
                    continue;
                }
            };
            if metadata.len() == 0 || metadata.len() > self.config.maximum_file_bytes {
                skipped_files += 1;
                continue;
            }
            let language = match language_for_path(&canonical_path) {
                Some(value) => value,
                None => {
                    skipped_files += 1;
                    continue;
                }
            };
            let source = match fs::read(&canonical_path) {
                Ok(value) => value,
                Err(_) => {
                    skipped_files += 1;
                    continue;
                }
            };
            if source.contains(&0)
                || source
                    .split(|byte| *byte == b'\n')
                    .any(|line| line.len() > self.config.maximum_minified_line_bytes)
            {
                skipped_files += 1;
                continue;
            }
            let text = match std::str::from_utf8(&source) {
                Ok(value) => value,
                Err(_) => {
                    skipped_files += 1;
                    continue;
                }
            };
            match self
                .index_file(&relative, language, text, metadata.len())
                .await
            {
                Ok(()) => {
                    present.insert(relative.clone());
                    indexed_files += 1;
                }
                Err(error) => {
                    tracing::warn!(path = %relative, error = %error, "repository parse failed; stale index entry will be removed");
                    parse_errors += 1;
                    skipped_files += 1;
                }
            }
        }

        let existing = self
            .store
            .files(self.workspace_id, self.config.maximum_files)?;
        let mut removed_files = 0usize;
        for file in existing {
            if !present.contains(&file.path) {
                self.store.remove_file(self.workspace_id, file.path)?;
                removed_files += 1;
            }
        }
        self.store.append_event(
            "workspace.indexed",
            Some(self.workspace_id),
            serde_json::json!({
                "scannedFiles": scanned_files,
                "indexedFiles": indexed_files,
                "skippedFiles": skipped_files,
                "removedFiles": removed_files,
                "parseErrors": parse_errors,
            }),
        )?;
        Ok(IndexReport {
            workspace_id: self.workspace_id,
            root: self.root.to_string_lossy().to_string(),
            scanned_files,
            indexed_files,
            skipped_files,
            removed_files,
            parse_errors,
            duration_ms: u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
            cancelled: false,
        })
    }

    pub async fn index_file(
        &self,
        relative: &str,
        language: &str,
        source: &str,
        byte_length: u64,
    ) -> Result<()> {
        let content_hash = blake3::hash(source.as_bytes()).to_hex().to_string();
        let parsed: Option<Arc<ParseEnvelope>> = match language {
            "typescript" | "tsx" | "javascript" | "jsx" => {
                let grammar = if matches!(language, "tsx" | "jsx") {
                    Grammar::Tsx
                } else {
                    Grammar::TypeScript
                };
                let grammar_hash =
                    parser_fingerprint(&[grammar.version(), TREE_SITTER_ENGINE_VERSION]);
                let key = ParseKey {
                    workspace_id: self.workspace_id,
                    relative_path: relative.to_string(),
                    file_size: byte_length,
                    content_hash: content_hash.clone(),
                    engine_version: OXC_ENGINE_VERSION.to_string(),
                    grammar_hash,
                    config_fingerprint: "default".to_string(),
                };
                Some(
                    self.cached_parse(key, || {
                        parse_oxc(self.workspace_id, relative, source, 1, "default")
                    })
                    .await?,
                )
            }
            "python" => {
                let grammar = Grammar::Python;
                let key = ParseKey {
                    workspace_id: self.workspace_id,
                    relative_path: relative.to_string(),
                    file_size: byte_length,
                    content_hash: content_hash.clone(),
                    engine_version: TREE_SITTER_ENGINE_VERSION.to_string(),
                    grammar_hash: parser_fingerprint(&[
                        grammar.version(),
                        TREE_SITTER_ENGINE_VERSION,
                    ]),
                    config_fingerprint: "default".to_string(),
                };
                Some(
                    self.cached_parse(key, || {
                        parse_tree_sitter(
                            self.workspace_id,
                            relative,
                            source,
                            grammar,
                            1,
                            "default",
                        )
                    })
                    .await?,
                )
            }
            "bash" => {
                let grammar = Grammar::Bash;
                let key = ParseKey {
                    workspace_id: self.workspace_id,
                    relative_path: relative.to_string(),
                    file_size: byte_length,
                    content_hash: content_hash.clone(),
                    engine_version: TREE_SITTER_ENGINE_VERSION.to_string(),
                    grammar_hash: parser_fingerprint(&[
                        grammar.version(),
                        TREE_SITTER_ENGINE_VERSION,
                    ]),
                    config_fingerprint: "default".to_string(),
                };
                Some(
                    self.cached_parse(key, || {
                        parse_tree_sitter(
                            self.workspace_id,
                            relative,
                            source,
                            grammar,
                            1,
                            "default",
                        )
                    })
                    .await?,
                )
            }
            _ => None,
        };
        let (engine, engine_version, ranges) = parsed.map_or_else(
            || ("text".to_string(), "1".to_string(), Vec::new()),
            |parsed| {
                (
                    parsed.engine.clone(),
                    parsed.engine_version.clone(),
                    parsed.structural_ranges.clone(),
                )
            },
        );
        let symbols = ranges
            .into_iter()
            .filter_map(symbol_from_range)
            .collect::<Vec<_>>();
        self.store.replace_file(
            IndexedFileRecord {
                workspace_id: self.workspace_id,
                path: relative.to_string(),
                content_hash,
                language: language.to_string(),
                byte_length,
                engine,
                engine_version,
                indexed_at_unix_ms: unix_ms(),
            },
            symbols,
        )?;
        Ok(())
    }

    async fn cached_parse<F>(&self, key: ParseKey, build: F) -> Result<Arc<ParseEnvelope>>
    where
        F: FnOnce() -> Result<ParseEnvelope>,
    {
        if let Some(value) = self.parse_cache.get(&key).await {
            return Ok(value);
        }
        let value = build()?;
        self.parse_cache.insert(key.clone(), value).await;
        self.parse_cache
            .get(&key)
            .await
            .context("parse cache did not retain inserted result")
    }

    pub async fn refresh_file(&self, relative: &str, source: &str) -> Result<()> {
        let canonical = self.resolve_existing_path(relative)?;
        let metadata = fs::metadata(&canonical)
            .with_context(|| format!("reading metadata for {}", canonical.display()))?;
        let language = language_for_path(&canonical)
            .with_context(|| format!("unsupported language for indexed edit: {relative}"))?;
        self.index_file(relative, language, source, metadata.len())
            .await
    }

    pub fn files(&self, limit: usize) -> Result<Vec<IndexedFileRecord>> {
        self.store.files(self.workspace_id, limit)
    }

    pub fn search_symbols(&self, query: &str, limit: usize) -> Result<Vec<SymbolHit>> {
        self.store.search_symbols(self.workspace_id, query, limit)
    }

    pub fn languages(&self) -> Result<Vec<String>> {
        self.store.languages(self.workspace_id)
    }

    pub fn symbols_for_file(&self, relative: &str) -> Result<Vec<SymbolRecord>> {
        self.store.symbols_for_file(self.workspace_id, relative)
    }

    pub fn store_stats(&self) -> Result<StoreStats> {
        self.store.stats()
    }

    pub fn parse_cache_stats(&self) -> (u64, u64) {
        (
            self.parse_cache.entry_count(),
            self.parse_cache.weighted_size(),
        )
    }

    pub fn indexed_file(&self, relative: &str) -> Result<Option<IndexedFileRecord>> {
        self.store.file(self.workspace_id, relative)
    }

    pub fn read_source_range(
        &self,
        relative: &str,
        start_byte: usize,
        end_byte: usize,
        maximum_bytes: usize,
    ) -> Result<String> {
        if end_byte < start_byte || end_byte.saturating_sub(start_byte) > maximum_bytes {
            bail!("requested source range exceeds the configured cap");
        }
        let source = fs::read(self.resolve_existing_path(relative)?)?;
        let slice = source
            .get(start_byte..end_byte)
            .context("source range is outside the file")?;
        Ok(String::from_utf8_lossy(slice).to_string())
    }
}

fn symbol_from_range(range: StructuralRange) -> Option<SymbolRecord> {
    let name = range.name?;
    Some(SymbolRecord {
        name,
        kind: range.kind,
        start_byte: u64::try_from(range.start_byte).ok()?,
        end_byte: u64::try_from(range.end_byte).ok()?,
        start_row: u64::try_from(range.start_row).ok()?,
        end_row: u64::try_from(range.end_row).ok()?,
    })
}

pub fn workspace_id_for_root(root: &Path) -> Uuid {
    let digest = blake3::hash(root.to_string_lossy().as_bytes());
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&digest.as_bytes()[..16]);
    Uuid::from_bytes(bytes)
}

pub fn language_for_path(path: &Path) -> Option<&'static str> {
    let extension = path.extension()?.to_str()?.to_ascii_lowercase();
    match extension.as_str() {
        "ts" | "mts" | "cts" => Some("typescript"),
        "tsx" => Some("tsx"),
        "js" | "mjs" | "cjs" => Some("javascript"),
        "jsx" => Some("jsx"),
        "py" | "pyi" => Some("python"),
        "sh" | "bash" | "zsh" => Some("bash"),
        "rs" => Some("rust"),
        "go" => Some("go"),
        "java" => Some("java"),
        "kt" | "kts" => Some("kotlin"),
        "swift" => Some("swift"),
        "json" | "jsonc" => Some("json"),
        "toml" => Some("toml"),
        "yaml" | "yml" => Some("yaml"),
        "md" | "mdx" => Some("markdown"),
        "sql" => Some("sql"),
        _ => None,
    }
}

fn should_skip_path(relative: &str) -> bool {
    relative.split('/').any(|component| {
        matches!(
            component,
            ".git"
                | ".soleaux"
                | "node_modules"
                | ".next"
                | "target"
                | "dist"
                | "build"
                | "coverage"
                | "vendor"
                | "vendored"
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[tokio::test]
    async fn index_persists_symbols_and_respects_repository_boundaries() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(
            directory.path().join("src/context.ts"),
            "export function compileContext(task: string) { return { task }; }",
        )
        .expect("fixture");
        fs::create_dir_all(directory.path().join("node_modules/pkg")).expect("node_modules");
        fs::write(
            directory.path().join("node_modules/pkg/index.js"),
            "export const ignored = true;",
        )
        .expect("ignored fixture");
        let store = Store::open(directory.path().join("soleaux.db")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        let report = index.refresh().await.expect("refresh");
        assert_eq!(report.indexed_files, 1);
        let hits = index.search_symbols("compile", 10).expect("search");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].name, "compileContext");
        assert!(index.resolve_existing_path("../outside").is_err());
    }
}
