//! Incremental on-disk repository index.

use crate::{
    Grammar, OXC_ENGINE_VERSION, ParseCache, ParseEnvelope, ParseKey, StructuralRange,
    TREE_SITTER_ENGINE_VERSION, oxc_grammar_hash, parse_oxc, parse_tree_sitter,
    tree_sitter_grammar_hash,
};
use anyhow::{Context, Result, bail};
use ignore::WalkBuilder;
use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use serde::{Deserialize, Serialize};
use soleaux_storage::{
    IndexedFileRecord, Store, StoreStats, SymbolHit, SymbolRecord, WorkspaceRecord, unix_ms,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Component, Path, PathBuf},
    sync::{
        Arc, Mutex, RwLock,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant, UNIX_EPOCH},
};
use uuid::Uuid;

const DEFAULT_MAX_FILES: usize = 250_000;
const DEFAULT_MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;
const DEFAULT_MINIFIED_LINE_BYTES: usize = 32 * 1024;
const WATCH_RECONCILE_INTERVAL: Duration = Duration::from_secs(30);

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileFingerprint {
    modified_ns: u128,
    byte_length: u64,
}

impl FileFingerprint {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        let modified_ns = metadata
            .modified()
            .ok()
            .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
            .map_or(0, |duration| duration.as_nanos());
        Self {
            modified_ns,
            byte_length: metadata.len(),
        }
    }
}

#[derive(Clone)]
pub struct RepositoryIndex {
    root: Arc<PathBuf>,
    workspace_id: Uuid,
    store: Store,
    parse_cache: ParseCache,
    config: IndexConfig,
    fingerprints: Arc<RwLock<BTreeMap<String, FileFingerprint>>>,
    watcher: Arc<Mutex<Option<RecommendedWatcher>>>,
    watch_dirty: Arc<AtomicBool>,
    last_reconcile: Arc<Mutex<Instant>>,
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
        let watch_dirty = Arc::new(AtomicBool::new(true));
        let watcher_dirty = Arc::clone(&watch_dirty);
        let mut watcher = match notify::recommended_watcher(move |event: notify::Result<Event>| {
            match event {
                Ok(event) if event_affects_index(&event) => {
                    watcher_dirty.store(true, Ordering::Release);
                }
                Ok(_) => {}
                Err(_) => {
                    watcher_dirty.store(true, Ordering::Release);
                }
            }
        }) {
            Ok(watcher) => Some(watcher),
            Err(error) => {
                tracing::warn!(%error, "repository watcher unavailable; reads will reconcile by scan");
                None
            }
        };
        let watch_error = watcher
            .as_mut()
            .and_then(|active| active.watch(&root, RecursiveMode::Recursive).err());
        if let Some(error) = watch_error {
            tracing::warn!(%error, "repository watcher failed to start; reads will reconcile by scan");
            watcher = None;
        }
        Ok(Self {
            root: Arc::new(root),
            workspace_id,
            store,
            parse_cache: ParseCache::new(256 * 1024 * 1024),
            config,
            fingerprints: Arc::new(RwLock::new(BTreeMap::new())),
            watcher: Arc::new(Mutex::new(watcher)),
            watch_dirty,
            last_reconcile: Arc::new(Mutex::new(Instant::now())),
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
        self.watch_dirty.store(false, Ordering::Release);
        match self.refresh_internal(false).await {
            Ok(report) => {
                *self
                    .last_reconcile
                    .lock()
                    .expect("repository reconcile lock poisoned") = Instant::now();
                Ok(report)
            }
            Err(error) => {
                self.watch_dirty.store(true, Ordering::Release);
                Err(error)
            }
        }
    }

    pub async fn refresh_incremental(&self) -> Result<IndexReport> {
        let watcher_available = self
            .watcher
            .lock()
            .expect("repository watcher lock poisoned")
            .is_some();
        let reconcile_due = self
            .last_reconcile
            .lock()
            .expect("repository reconcile lock poisoned")
            .elapsed()
            >= WATCH_RECONCILE_INTERVAL;
        let dirty = self.watch_dirty.swap(false, Ordering::AcqRel);
        if watcher_available && !reconcile_due && !dirty {
            tokio::task::yield_now().await;
            if !self.watch_dirty.swap(false, Ordering::AcqRel) {
                return Ok(self.noop_report());
            }
        }
        match self.refresh_internal(true).await {
            Ok(report) => {
                *self
                    .last_reconcile
                    .lock()
                    .expect("repository reconcile lock poisoned") = Instant::now();
                Ok(report)
            }
            Err(error) => {
                self.watch_dirty.store(true, Ordering::Release);
                Err(error)
            }
        }
    }

    fn noop_report(&self) -> IndexReport {
        IndexReport {
            workspace_id: self.workspace_id,
            root: self.root.to_string_lossy().to_string(),
            scanned_files: 0,
            indexed_files: 0,
            skipped_files: 0,
            removed_files: 0,
            parse_errors: 0,
            duration_ms: 0,
            cancelled: false,
        }
    }

    async fn refresh_internal(&self, incremental: bool) -> Result<IndexReport> {
        let started = Instant::now();
        let mut scanned_files = 0usize;
        let mut indexed_files = 0usize;
        let mut skipped_files = 0usize;
        let mut parse_errors = 0usize;
        let mut present = BTreeSet::new();
        let previous_fingerprints = self
            .fingerprints
            .read()
            .expect("repository fingerprint lock poisoned")
            .clone();
        let mut next_fingerprints = BTreeMap::new();
        let mut changed_files = 0usize;
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
            let fingerprint = FileFingerprint::from_metadata(&metadata);
            if incremental && previous_fingerprints.get(&relative).copied() == Some(fingerprint) {
                present.insert(relative.clone());
                next_fingerprints.insert(relative, fingerprint);
                indexed_files += 1;
                continue;
            }
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
            let content_hash = blake3::hash(source.as_slice()).to_hex().to_string();
            if self
                .store
                .file(self.workspace_id, &relative)?
                .is_some_and(|existing| existing.content_hash == content_hash)
            {
                present.insert(relative.clone());
                next_fingerprints.insert(relative, fingerprint);
                indexed_files += 1;
                continue;
            }
            match self
                .index_file(&relative, language, text, metadata.len())
                .await
            {
                Ok(()) => {
                    present.insert(relative.clone());
                    next_fingerprints.insert(relative.clone(), fingerprint);
                    indexed_files += 1;
                    changed_files += 1;
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
        changed_files = changed_files.saturating_add(removed_files);
        *self
            .fingerprints
            .write()
            .expect("repository fingerprint lock poisoned") = next_fingerprints;
        if changed_files > 0 || parse_errors > 0 {
            self.store.append_event(
                "workspace.indexed",
                Some(self.workspace_id),
                serde_json::json!({
                    "scannedFiles": scanned_files,
                    "indexedFiles": indexed_files,
                    "changedFiles": changed_files,
                    "skippedFiles": skipped_files,
                    "removedFiles": removed_files,
                    "parseErrors": parse_errors,
                }),
            )?;
        }
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
                let key = ParseKey {
                    workspace_id: self.workspace_id,
                    relative_path: relative.to_string(),
                    file_size: byte_length,
                    content_hash: content_hash.clone(),
                    engine_version: OXC_ENGINE_VERSION.to_string(),
                    grammar_hash: oxc_grammar_hash(grammar),
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
                    grammar_hash: tree_sitter_grammar_hash(grammar),
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
                    grammar_hash: tree_sitter_grammar_hash(grammar),
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
            .await?;
        self.fingerprints
            .write()
            .expect("repository fingerprint lock poisoned")
            .insert(
                relative.to_string(),
                FileFingerprint::from_metadata(&metadata),
            );
        Ok(())
    }

    pub fn files(&self, limit: usize) -> Result<Vec<IndexedFileRecord>> {
        self.files_page(limit, 0)
    }

    pub fn files_page(&self, limit: usize, offset: usize) -> Result<Vec<IndexedFileRecord>> {
        self.store.files_page(self.workspace_id, limit, offset)
    }

    pub fn search_symbols(&self, query: &str, limit: usize) -> Result<Vec<SymbolHit>> {
        self.search_symbols_page(query, limit, 0)
    }

    pub fn search_symbols_page(
        &self,
        query: &str,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<SymbolHit>> {
        self.store
            .search_symbols_page(self.workspace_id, query, limit, offset)
    }

    pub fn snapshot_id(&self) -> Result<String> {
        self.store.workspace_snapshot_id(self.workspace_id)
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

    pub fn validate_indexed_file(&self, relative: &str) -> Result<bool> {
        let Some(file) = self.indexed_file(relative)? else {
            return Ok(false);
        };
        let absolute = self.root.join(relative);
        let source = match fs::read(&absolute) {
            Ok(source) => source,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                self.watch_dirty.store(true, Ordering::Release);
                self.fingerprints
                    .write()
                    .expect("repository fingerprint lock poisoned")
                    .remove(relative);
                return Ok(false);
            }
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("reading indexed file {}", absolute.display()));
            }
        };
        let current_hash = blake3::hash(source.as_slice()).to_hex().to_string();
        let current = current_hash == file.content_hash;
        if !current {
            self.watch_dirty.store(true, Ordering::Release);
            self.fingerprints
                .write()
                .expect("repository fingerprint lock poisoned")
                .remove(relative);
        }
        Ok(current)
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
        let file = self
            .indexed_file(relative)?
            .with_context(|| format!("file is not in the structural index: {relative}"))?;
        let source = fs::read(self.resolve_existing_path(relative)?)?;
        let current_hash = blake3::hash(source.as_slice()).to_hex().to_string();
        if current_hash != file.content_hash {
            self.watch_dirty.store(true, Ordering::Release);
            self.fingerprints
                .write()
                .expect("repository fingerprint lock poisoned")
                .remove(relative);
            bail!("indexed file changed before source-range hydration: {relative}");
        }
        let slice = source
            .get(start_byte..end_byte)
            .context("source range is outside the file")?;
        Ok(String::from_utf8_lossy(slice).to_string())
    }
}

fn symbol_from_range(range: StructuralRange) -> Option<SymbolRecord> {
    // The symbol table stores structural containers. Definition name spans,
    // reference sites, and injection ranges stay envelope-only detail.
    if range.kind == "injection"
        || range.kind.starts_with("reference.")
        || range.kind.starts_with("definition.")
    {
        return None;
    }
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

fn event_affects_index(event: &Event) -> bool {
    if matches!(event.kind, EventKind::Access(_)) {
        return false;
    }
    event.paths.iter().any(|path| {
        language_for_path(path).is_some()
            || path.extension().is_none()
            || path
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|name| matches!(name, ".gitignore" | ".soleauxignore"))
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

    #[tokio::test]
    async fn incremental_refresh_skips_unchanged_files_and_preserves_generation() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        fs::write(
            directory.path().join("src/context.ts"),
            "export function compileContext() { return true; }",
        )
        .expect("fixture");
        let store = Store::open(directory.path().join("soleaux.db")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("initial refresh");
        let before = index.store_stats().expect("stats before");
        let report = index
            .refresh_incremental()
            .await
            .expect("incremental refresh");
        let after = index.store_stats().expect("stats after");
        assert_eq!(report.scanned_files, 0);
        assert_eq!(report.indexed_files, 0);
        assert_eq!(report.removed_files, 0);
        assert_eq!(before.event_count, after.event_count);
    }

    #[tokio::test]
    async fn watcher_drives_incremental_reconciliation_without_per_read_walks() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        let path = directory.path().join("src/context.ts");
        fs::write(&path, "export function oldState() { return true; }").expect("fixture");
        let store = Store::open(directory.path().join("soleaux.db")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("initial refresh");

        let unchanged = index
            .refresh_incremental()
            .await
            .expect("unchanged refresh");
        assert_eq!(unchanged.scanned_files, 0);

        fs::write(&path, "export function newState() { return true; }").expect("mutation");
        let mut observed = false;
        for _ in 0..50 {
            let report = index
                .refresh_incremental()
                .await
                .expect("watch reconciliation");
            if report.scanned_files > 0
                && index
                    .search_symbols("newState", 10)
                    .expect("new symbol")
                    .iter()
                    .any(|hit| hit.name == "newState")
            {
                observed = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(
            observed,
            "watcher did not schedule repository reconciliation"
        );
    }

    #[tokio::test]
    async fn stale_source_ranges_fail_closed_and_force_revalidation() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        let path = directory.path().join("src/context.ts");
        fs::write(&path, "export function oldState() { return true; }").expect("fixture");
        let store = Store::open(directory.path().join("soleaux.db")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("initial refresh");
        fs::write(&path, "export function newState() { return true; }").expect("mutation");
        assert!(
            !index
                .validate_indexed_file("src/context.ts")
                .expect("validation")
        );
        assert!(
            index
                .read_source_range("src/context.ts", 0, 8, 1024)
                .is_err()
        );
        index
            .refresh_incremental()
            .await
            .expect("refresh changed file");
        assert!(
            index
                .validate_indexed_file("src/context.ts")
                .expect("validation after refresh")
        );
    }
}
