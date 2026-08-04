#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "native/daemon/mcp/src/editor.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


text = EDITOR.read_text(encoding="utf-8")
text = replace_once(
    text,
    "use anyhow::{Context, Result, bail};\n",
    "use anyhow::{Context, Result, anyhow, bail};\n",
    "anyhow import",
)
text = replace_once(
    text,
    '''use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
''',
    '''use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::{SystemTime, UNIX_EPOCH},
};
''',
    "atomic rollback import",
)
text = replace_once(
    text,
    '''pub struct EditorService {
    index: RepositoryIndex,
    preview_dir: PathBuf,
    process_epoch: String,
}
''',
    '''pub struct EditorService {
    index: RepositoryIndex,
    preview_dir: PathBuf,
    process_epoch: String,
    fail_after_write: Arc<AtomicBool>,
}
''',
    "editor failpoint field",
)
text = replace_once(
    text,
    '''        fs::create_dir_all(preview_dir.join("backups"))?;
        Ok(Self {
            index,
            preview_dir,
            process_epoch: Uuid::now_v7().to_string(),
        })
''',
    '''        fs::create_dir_all(preview_dir.join("backups"))?;
        fs::create_dir_all(preview_dir.join("receipts"))?;
        Ok(Self {
            index,
            preview_dir,
            process_epoch: Uuid::now_v7().to_string(),
            fail_after_write: Arc::new(AtomicBool::new(false)),
        })
''',
    "editor initialization",
)

apply_start = text.index("    pub async fn apply(")
apply_end = text.index("    fn create_preview(", apply_start)
new_apply = r'''    #[cfg(test)]
    fn fail_after_write_once(&self) {
        self.fail_after_write.store(true, Ordering::SeqCst);
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
        let mut staged = Vec::new();
        for (path, (absolute, preimage, postimage)) in prepared {
            let backup = self.backup_path(&receipt_id, &path);
            if let Some(parent) = backup.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&backup, &preimage)
                .with_context(|| format!("writing editor backup {}", backup.display()))?;
            staged.push((path, absolute, preimage, postimage, backup));
        }

        let mut originals = Vec::new();
        let mut files = Vec::new();
        for (path, absolute, preimage, postimage, backup) in &staged {
            if let Err(failure) = atomic_replace(absolute, postimage) {
                return Err(self
                    .rollback_after_failure(
                        &mut preview,
                        &receipt_id,
                        &originals,
                        &files,
                        failure,
                    )
                    .await);
            }
            originals.push((absolute.clone(), preimage.clone()));
            files.push(AppliedFile {
                path: path.clone(),
                preimage_sha256: sha256_hex(preimage),
                postimage_sha256: sha256_hex(postimage),
                backup_path: Some(backup.to_string_lossy().to_string()),
            });
        }

        let post_write = async {
            if self.fail_after_write.swap(false, Ordering::SeqCst) {
                bail!("injected editor post-write failure");
            }
            let report = self.index.refresh().await?;
            preview.consumed = true;
            preview.writes_performed = true;
            self.persist(&preview)?;
            let event = self.index.store().append_event(
                "editor.preview_applied",
                Some(self.index.workspace_id()),
                json!({
                    "receipt_id":receipt_id,
                    "preview_id":preview.preview_id,
                    "digest":preview.digest,
                    "files":&files,
                    "reindexed":true,
                    "index_report":&report,
                }),
            )?;
            Ok::<_, anyhow::Error>((report, event))
        }
        .await;

        let (_report, event) = match post_write {
            Ok(value) => value,
            Err(failure) => {
                return Err(self
                    .rollback_after_failure(
                        &mut preview,
                        &receipt_id,
                        &originals,
                        &files,
                        failure,
                    )
                    .await);
            }
        };

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

    async fn rollback_after_failure(
        &self,
        preview: &mut StoredPreview,
        receipt_id: &str,
        originals: &[(PathBuf, Vec<u8>)],
        files: &[AppliedFile],
        failure: anyhow::Error,
    ) -> anyhow::Error {
        let failure_message = format!("{failure:#}");
        let mut rollback_errors = Vec::new();
        for (absolute, preimage) in originals.iter().rev() {
            if let Err(error) = atomic_replace(absolute, preimage) {
                rollback_errors.push(format!(
                    "restoring {} failed: {error:#}",
                    absolute.display()
                ));
            }
        }
        if let Err(error) = self.index.refresh().await {
            rollback_errors.push(format!("refreshing the restored index failed: {error:#}"));
        }
        preview.consumed = false;
        preview.writes_performed = false;
        if let Err(error) = self.persist(preview) {
            rollback_errors.push(format!("restoring preview state failed: {error:#}"));
        }

        let audit_event_hash = match self.index.store().append_event(
            "editor.preview_rolled_back",
            Some(self.index.workspace_id()),
            json!({
                "receipt_id":receipt_id,
                "preview_id":preview.preview_id,
                "failure":failure_message,
                "files":files,
                "rollback_errors":rollback_errors,
            }),
        ) {
            Ok(event) => Some(event.event_hash),
            Err(error) => {
                rollback_errors.push(format!("recording rollback audit failed: {error:#}"));
                None
            }
        };

        let rolled_back = rollback_errors.is_empty();
        let receipt = json!({
            "schema_version":"soleaux.editor-rollback/v1",
            "receipt_id":receipt_id,
            "preview_id":preview.preview_id,
            "failure":failure_message,
            "rolled_back":rolled_back,
            "files":files,
            "rollback_errors":rollback_errors,
            "audit_event_hash":audit_event_hash,
            "created_at_unix_ms":unix_ms(),
        });
        let receipt_path = self.rollback_receipt_path(receipt_id);
        if let Err(error) = persist_json_value(&receipt_path, &receipt) {
            rollback_errors.push(format!(
                "persisting rollback receipt {} failed: {error:#}",
                receipt_path.display()
            ));
        }

        if rolled_back && rollback_errors.is_empty() {
            anyhow!(
                "editor apply failed after repository mutation and was rolled back: {failure_message}"
            )
        } else {
            anyhow!(
                "editor apply failed after repository mutation: {failure_message}; rollback reconciliation errors: {}",
                rollback_errors.join("; ")
            )
        }
    }

'''
text = text[:apply_start] + new_apply + text[apply_end:]
text = replace_once(
    text,
    '''    fn backup_path(&self, receipt_id: &str, path: &str) -> PathBuf {
        let name = blake3::hash(path.as_bytes()).to_hex().to_string();
        self.preview_dir
            .join("backups")
            .join(receipt_id)
            .join(format!("{name}.bak"))
    }

''',
    '''    fn backup_path(&self, receipt_id: &str, path: &str) -> PathBuf {
        let name = blake3::hash(path.as_bytes()).to_hex().to_string();
        self.preview_dir
            .join("backups")
            .join(receipt_id)
            .join(format!("{name}.bak"))
    }

    fn rollback_receipt_path(&self, receipt_id: &str) -> PathBuf {
        self.preview_dir
            .join("receipts")
            .join(format!("{receipt_id}.rollback.json"))
    }

''',
    "rollback receipt path",
)
text = replace_once(
    text,
    '''fn validate_non_overlapping(patches: &[EditPatch]) -> Result<()> {
''',
    '''fn persist_json_value(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension(format!("json.{}.tmp", Uuid::now_v7()));
    fs::write(&temporary, serde_json::to_vec_pretty(value)?)?;
    fs::rename(&temporary, path)?;
    Ok(())
}

fn validate_non_overlapping(patches: &[EditPatch]) -> Result<()> {
''',
    "atomic rollback receipt persistence",
)
text = replace_once(
    text,
    '''    #[test]
    fn overlapping_patches_are_rejected() {
''',
    '''    #[tokio::test]
    async fn post_write_failure_restores_source_preview_and_receipt() {
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

        editor.fail_after_write_once();
        let error = editor
            .apply(&preview.preview_id, &preview.digest, true)
            .await
            .expect_err("injected post-write failure must fail");
        assert!(format!("{error:#}").contains("was rolled back"));
        assert_eq!(
            fs::read_to_string(&source_path).expect("read"),
            "export const value = 1;\n"
        );
        let stored = editor.load(&preview.preview_id).expect("stored preview");
        assert!(!stored.consumed);
        assert!(!stored.writes_performed);

        let receipts = fs::read_dir(editor.preview_dir.join("receipts"))
            .expect("receipt directory")
            .collect::<std::io::Result<Vec<_>>>()
            .expect("receipt entries");
        assert_eq!(receipts.len(), 1);
        let receipt: Value = serde_json::from_slice(
            &fs::read(receipts[0].path()).expect("rollback receipt"),
        )
        .expect("receipt json");
        assert_eq!(receipt["schema_version"], "soleaux.editor-rollback/v1");
        assert_eq!(receipt["rolled_back"], true);
        assert!(receipt["rollback_errors"].as_array().is_some_and(Vec::is_empty));
    }

    #[test]
    fn overlapping_patches_are_rejected() {
''',
    "post-write rollback regression",
)
EDITOR.write_text(text, encoding="utf-8")
