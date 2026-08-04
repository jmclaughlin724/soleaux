//! Hash-bound native editor previews and single-preview application.
//!
//! A preview never writes repository files. Apply revalidates the digest,
//! expiry, workspace, and every whole-file preimage hash before any write.

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use soleaux_intelligence::index::RepositoryIndex;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub const PREVIEW_SCHEMA_VERSION: &str = "soleaux.preview/v1";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub struct EditPatch {
    pub path: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub replacement: String,
    pub preimage_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct StoredPreview {
    pub schema_version: String,
    pub preview_id: String,
    pub digest: String,
    pub workspace_id: String,
    pub process_epoch: String,
    pub created_at_unix_ms: u64,
    pub expires_at_unix_ms: u64,
    pub operation: String,
    pub patches: Vec<EditPatch>,
    pub non_overlapping: bool,
    pub writes_performed: bool,
    pub validation_plan: Vec<String>,
    pub warnings: Vec<String>,
    pub consumed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct AppliedFile {
    pub path: String,
    pub preimage_sha256: String,
    pub postimage_sha256: String,
    pub backup_path: Option<String>,
}

#[derive(Clone)]
pub struct EditorService {
    index: RepositoryIndex,
    preview_dir: PathBuf,
    process_epoch: String,
}

impl EditorService {
    pub fn new(index: RepositoryIndex) -> Result<Self> {
        let parent = index
            .store()
            .path()
            .parent()
            .context("Soleaux index database has no parent directory")?;
        let preview_dir = parent.join("previews");
        fs::create_dir_all(preview_dir.join("backups"))?;
        Ok(Self {
            index,
            preview_dir,
            process_epoch: Uuid::now_v7().to_string(),
        })
    }

    pub fn structural_preview(&self, arguments: &Value) -> Result<StoredPreview> {
        let paths = arguments
            .get("paths")
            .and_then(Value::as_array)
            .context("structural_rewrite requires paths")?;
        if paths.len() != 1 {
            bail!("Phase 1 structural_rewrite requires exactly one path");
        }
        let path = paths[0]
            .as_str()
            .context("structural_rewrite paths must be strings")?;
        let structural = arguments
            .get("structural")
            .and_then(Value::as_object)
            .context("structural_rewrite requires a structural object")?;
        let source_path = self.index.resolve_existing_path(path)?;
        let source = fs::read(&source_path)
            .with_context(|| format!("reading editor preimage {}", source_path.display()))?;
        let preimage_sha256 = sha256_hex(&source);
        let (start_byte, end_byte, replacement) = if let (
            Some(start),
            Some(end),
            Some(replacement),
        ) = (
            structural.get("start_byte").and_then(Value::as_u64),
            structural.get("end_byte").and_then(Value::as_u64),
            structural.get("replacement").and_then(Value::as_str),
        ) {
            (
                usize::try_from(start).context("start_byte exceeds platform usize")?,
                usize::try_from(end).context("end_byte exceeds platform usize")?,
                replacement.to_string(),
            )
        } else if let (Some(search), Some(replacement)) = (
            structural.get("search").and_then(Value::as_str),
            structural.get("replacement").and_then(Value::as_str),
        ) {
            let haystack = std::str::from_utf8(&source)
                .context("structural exact-text edit requires UTF-8")?;
            let matches = haystack.match_indices(search).collect::<Vec<_>>();
            if matches.len() != 1 {
                bail!(
                    "structural exact-text edit requires one unique match; observed {}",
                    matches.len()
                );
            }
            let start = matches[0].0;
            (start, start + search.len(), replacement.to_string())
        } else {
            bail!(
                "structural must provide start_byte/end_byte/replacement or unique search/replacement"
            );
        };
        if end_byte < start_byte || end_byte > source.len() {
            bail!("structural patch range is outside the source preimage");
        }
        self.create_preview(
            arguments,
            "structural_rewrite",
            vec![EditPatch {
                path: path.to_string(),
                start_byte,
                end_byte,
                replacement,
                preimage_sha256,
            }],
            vec![
                "Revalidate whole-file SHA-256 preimage".to_string(),
                "Apply one atomic same-directory replacement".to_string(),
                "Refresh the native structural index".to_string(),
                "Append a hash-chained audit event".to_string(),
            ],
            Vec::new(),
        )
    }

    pub fn preview_from_workspace_edit(
        &self,
        arguments: &Value,
        operation: &str,
        workspace_edit: &Value,
        validation_plan: Vec<String>,
    ) -> Result<StoredPreview> {
        let patches = self.normalize_workspace_edit(workspace_edit)?;
        self.create_preview(arguments, operation, patches, validation_plan, Vec::new())
    }

    pub async fn apply(&self, preview_id: &str, digest: &str, confirm: bool) -> Result<Value> {
        if !confirm {
            bail!("edit requires confirm=true");
        }
        let mut preview = self.load(preview_id)?;
        if preview.digest != digest {
            bail!("preview digest does not match");
        }
        if preview.workspace_id != self.index.workspace_id().to_string() {
            bail!("preview belongs to another workspace");
        }
        if preview.process_epoch != self.process_epoch {
            bail!("preview belongs to another Soleaux process epoch");
        }
        if preview.consumed {
            bail!("preview has already been consumed");
        }
        if unix_ms() > preview.expires_at_unix_ms {
            bail!("preview has expired");
        }

        let grouped = group_patches(&preview.patches)?;
        let mut prepared = BTreeMap::new();
        for (path, patches) in &grouped {
            let absolute = self.index.resolve_existing_path(path)?;
            let source = fs::read(&absolute)
                .with_context(|| format!("reading live preimage {}", absolute.display()))?;
            let live_hash = sha256_hex(&source);
            let expected = patches
                .first()
                .map(|patch| patch.preimage_sha256.as_str())
                .context("preview patch group was empty")?;
            if live_hash != expected {
                bail!("preimage conflict for {path}: live SHA-256 changed after preview");
            }
            let postimage = apply_patches(&source, patches)?;
            prepared.insert(path.clone(), (absolute, source, postimage));
        }

        let receipt_id = Uuid::now_v7().to_string();
        let mut files = Vec::new();
        for (path, (absolute, preimage, postimage)) in prepared {
            let backup = self.backup_path(&receipt_id, &path);
            if let Some(parent) = backup.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&backup, &preimage)
                .with_context(|| format!("writing editor backup {}", backup.display()))?;
            atomic_replace(&absolute, &postimage)?;
            files.push(AppliedFile {
                path,
                preimage_sha256: sha256_hex(&preimage),
                postimage_sha256: sha256_hex(&postimage),
                backup_path: Some(backup.to_string_lossy().to_string()),
            });
        }

        let report = self.index.refresh().await?;
        let event = self.index.store().append_event(
            "editor.preview_applied",
            Some(self.index.workspace_id()),
            json!({
                "receipt_id":receipt_id,
                "preview_id":preview.preview_id,
                "digest":preview.digest,
                "files":files,
                "reindexed":true,
                "index_report":report,
            }),
        )?;
        preview.consumed = true;
        preview.writes_performed = true;
        self.persist(&preview)?;
        Ok(json!({
            "receipt_id":receipt_id,
            "preview_id":preview.preview_id,
            "applied":true,
            "files":files,
            "formatter":null,
            "diagnostics":[],
            "reindexed":true,
            "audit_event_hash":event.event_hash,
        }))
    }

    fn create_preview(
        &self,
        arguments: &Value,
        operation: &str,
        patches: Vec<EditPatch>,
        validation_plan: Vec<String>,
        warnings: Vec<String>,
    ) -> Result<StoredPreview> {
        validate_non_overlapping(&patches)?;
        if patches.is_empty() {
            bail!("preview produced no patches");
        }
        let now = unix_ms();
        let ttl = arguments
            .get("ttl_seconds")
            .and_then(Value::as_u64)
            .unwrap_or(300)
            .clamp(30, 3_600);
        let preview_id = Uuid::now_v7().to_string();
        let expires_at_unix_ms = now.saturating_add(ttl.saturating_mul(1_000));
        let digest_payload = json!({
            "schema_version":PREVIEW_SCHEMA_VERSION,
            "preview_id":preview_id,
            "workspace_id":self.index.workspace_id(),
            "process_epoch":self.process_epoch,
            "created_at_unix_ms":now,
            "expires_at_unix_ms":expires_at_unix_ms,
            "operation":operation,
            "patches":patches,
        });
        let digest = sha256_hex(&serde_json::to_vec(&digest_payload)?);
        let preview = StoredPreview {
            schema_version: PREVIEW_SCHEMA_VERSION.to_string(),
            preview_id,
            digest,
            workspace_id: self.index.workspace_id().to_string(),
            process_epoch: self.process_epoch.clone(),
            created_at_unix_ms: now,
            expires_at_unix_ms,
            operation: operation.to_string(),
            patches,
            non_overlapping: true,
            writes_performed: false,
            validation_plan,
            warnings,
            consumed: false,
        };
        self.persist(&preview)?;
        Ok(preview)
    }

    fn normalize_workspace_edit(&self, edit: &Value) -> Result<Vec<EditPatch>> {
        let mut edits_by_path: BTreeMap<String, Vec<Value>> = BTreeMap::new();
        if let Some(changes) = edit.get("changes").and_then(Value::as_object) {
            for (uri, edits) in changes {
                let path = self.relative_from_uri(uri)?;
                let values = edits
                    .as_array()
                    .context("workspace edit changes must be arrays")?;
                edits_by_path
                    .entry(path)
                    .or_default()
                    .extend(values.iter().cloned());
            }
        }
        if let Some(document_changes) = edit.get("documentChanges").and_then(Value::as_array) {
            for change in document_changes {
                let uri = change
                    .pointer("/textDocument/uri")
                    .and_then(Value::as_str)
                    .context("only TextDocumentEdit documentChanges are supported")?;
                let path = self.relative_from_uri(uri)?;
                let edits = change
                    .get("edits")
                    .and_then(Value::as_array)
                    .context("TextDocumentEdit omitted edits")?;
                edits_by_path
                    .entry(path)
                    .or_default()
                    .extend(edits.iter().cloned());
            }
        }
        if edits_by_path.is_empty() {
            bail!("LSP operation returned no workspace edits");
        }
        if edits_by_path.len() > 1 {
            bail!("Phase 1 editor accepts one affected file per preview");
        }
        let mut patches = Vec::new();
        for (path, edits) in edits_by_path {
            let absolute = self.index.resolve_existing_path(&path)?;
            let source = fs::read(&absolute)?;
            let text =
                std::str::from_utf8(&source).context("LSP text edits require UTF-8 source")?;
            let preimage_sha256 = sha256_hex(&source);
            for edit in edits {
                let range = edit.get("range").context("LSP text edit omitted range")?;
                let start = lsp_position_to_byte(text, range.get("start").context("range start")?)?;
                let end = lsp_position_to_byte(text, range.get("end").context("range end")?)?;
                let replacement = edit
                    .get("newText")
                    .and_then(Value::as_str)
                    .context("LSP text edit omitted newText")?;
                patches.push(EditPatch {
                    path: path.clone(),
                    start_byte: start,
                    end_byte: end,
                    replacement: replacement.to_string(),
                    preimage_sha256: preimage_sha256.clone(),
                });
            }
        }
        validate_non_overlapping(&patches)?;
        Ok(patches)
    }

    fn relative_from_uri(&self, uri: &str) -> Result<String> {
        let url = url::Url::parse(uri).context("invalid file URI in workspace edit")?;
        let absolute = url
            .to_file_path()
            .map_err(|_| anyhow::anyhow!("workspace edit URI is not a local file"))?;
        let canonical = fs::canonicalize(&absolute)
            .with_context(|| format!("resolving workspace edit path {}", absolute.display()))?;
        let relative = canonical
            .strip_prefix(self.index.root())
            .context("workspace edit escaped the repository root")?;
        Ok(relative.to_string_lossy().replace('\\', "/"))
    }

    fn preview_path(&self, preview_id: &str) -> PathBuf {
        self.preview_dir.join(format!("{preview_id}.json"))
    }

    fn backup_path(&self, receipt_id: &str, path: &str) -> PathBuf {
        let name = blake3::hash(path.as_bytes()).to_hex().to_string();
        self.preview_dir
            .join("backups")
            .join(receipt_id)
            .join(format!("{name}.bak"))
    }

    fn persist(&self, preview: &StoredPreview) -> Result<()> {
        let path = self.preview_path(&preview.preview_id);
        let temporary = path.with_extension(format!("json.{}.tmp", Uuid::now_v7()));
        fs::write(&temporary, serde_json::to_vec_pretty(preview)?)?;
        fs::rename(&temporary, &path)?;
        Ok(())
    }

    fn load(&self, preview_id: &str) -> Result<StoredPreview> {
        if preview_id.contains('/') || preview_id.contains('\\') || preview_id.contains("..") {
            bail!("invalid preview identifier");
        }
        let path = self.preview_path(preview_id);
        let bytes =
            fs::read(&path).with_context(|| format!("reading preview {}", path.display()))?;
        serde_json::from_slice(&bytes).context("decoding stored preview")
    }
}

fn validate_non_overlapping(patches: &[EditPatch]) -> Result<()> {
    let mut by_path: BTreeMap<&str, Vec<&EditPatch>> = BTreeMap::new();
    for patch in patches {
        if patch.end_byte < patch.start_byte {
            bail!("patch end precedes its start");
        }
        by_path.entry(&patch.path).or_default().push(patch);
    }
    for values in by_path.values_mut() {
        values.sort_by_key(|patch| (patch.start_byte, patch.end_byte));
        for pair in values.windows(2) {
            if pair[1].start_byte < pair[0].end_byte {
                bail!("preview patches overlap");
            }
        }
    }
    Ok(())
}

fn group_patches(patches: &[EditPatch]) -> Result<BTreeMap<String, Vec<EditPatch>>> {
    validate_non_overlapping(patches)?;
    let mut grouped: BTreeMap<String, Vec<EditPatch>> = BTreeMap::new();
    for patch in patches {
        grouped
            .entry(patch.path.clone())
            .or_default()
            .push(patch.clone());
    }
    for values in grouped.values_mut() {
        values.sort_by_key(|patch| patch.start_byte);
    }
    Ok(grouped)
}

fn apply_patches(source: &[u8], patches: &[EditPatch]) -> Result<Vec<u8>> {
    let mut output = source.to_vec();
    for patch in patches.iter().rev() {
        if patch.end_byte > output.len() || patch.start_byte > patch.end_byte {
            bail!("patch range is outside the live source");
        }
        output.splice(
            patch.start_byte..patch.end_byte,
            patch.replacement.as_bytes().iter().copied(),
        );
    }
    Ok(output)
}

fn lsp_position_to_byte(source: &str, position: &Value) -> Result<usize> {
    let line = position
        .get("line")
        .and_then(Value::as_u64)
        .context("LSP position omitted line")?;
    let character = position
        .get("character")
        .and_then(Value::as_u64)
        .context("LSP position omitted character")?;
    let line = usize::try_from(line).context("LSP line exceeds platform usize")?;
    let character = usize::try_from(character).context("LSP character exceeds platform usize")?;
    let mut offset = 0usize;
    let text = source
        .split_inclusive('\n')
        .nth(line)
        .context("LSP line is outside the source")?;
    for preceding in source.split_inclusive('\n').take(line) {
        offset = offset.saturating_add(preceding.len());
    }
    let line_without_newline = text.strip_suffix('\n').unwrap_or(text);
    let mut utf16_units = 0usize;
    let mut byte_in_line = 0usize;
    for character_value in line_without_newline.chars() {
        if utf16_units >= character {
            break;
        }
        utf16_units = utf16_units.saturating_add(character_value.len_utf16());
        byte_in_line = byte_in_line.saturating_add(character_value.len_utf8());
    }
    if utf16_units < character {
        bail!("LSP character is outside the source line");
    }
    Ok(offset.saturating_add(byte_in_line))
}

fn atomic_replace(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .context("edited file has no parent directory")?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("soleaux-edit");
    let temporary = parent.join(format!(".{name}.{}.soleaux.tmp", Uuid::now_v7()));
    fs::write(&temporary, bytes)
        .with_context(|| format!("writing temporary edit {}", temporary.display()))?;
    fs::rename(&temporary, path)
        .with_context(|| format!("atomically replacing {}", path.display()))?;
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use soleaux_intelligence::index::{IndexConfig, RepositoryIndex};
    use soleaux_storage::Store;
    use tempfile::tempdir;

    #[tokio::test]
    async fn preview_is_no_write_and_apply_revalidates_hash() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        let source_path = directory.path().join("src/value.ts");
        fs::write(&source_path, "export const value = 1;\n").expect("source");
        let store = Store::open(directory.path().join("soleaux.db")).expect("store");
        let index =
            RepositoryIndex::open(directory.path(), store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("refresh");
        let editor = EditorService::new(index).expect("editor");
        let preview = editor
            .structural_preview(&json!({
                "operation":"structural_rewrite",
                "paths":["src/value.ts"],
                "structural":{"search":"value = 1","replacement":"value = 2"},
                "ttl_seconds":300,
            }))
            .expect("preview");
        assert_eq!(
            fs::read_to_string(&source_path).expect("read"),
            "export const value = 1;\n"
        );
        let result = editor
            .apply(&preview.preview_id, &preview.digest, true)
            .await
            .expect("apply");
        assert_eq!(result["applied"], true);
        assert_eq!(
            fs::read_to_string(&source_path).expect("read"),
            "export const value = 2;\n"
        );
        assert!(
            editor
                .apply(&preview.preview_id, &preview.digest, true)
                .await
                .is_err()
        );
    }

    #[test]
    fn overlapping_patches_are_rejected() {
        let patches = vec![
            EditPatch {
                path: "src/a.ts".to_string(),
                start_byte: 1,
                end_byte: 5,
                replacement: "x".to_string(),
                preimage_sha256: "0".repeat(64),
            },
            EditPatch {
                path: "src/a.ts".to_string(),
                start_byte: 4,
                end_byte: 6,
                replacement: "y".to_string(),
                preimage_sha256: "0".repeat(64),
            },
        ];
        assert!(validate_non_overlapping(&patches).is_err());
    }
}
