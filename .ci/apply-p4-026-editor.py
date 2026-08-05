from pathlib import Path


path = Path("native/daemon/mcp/src/editor.rs")
text = path.read_text(encoding="utf-8")

old_import = "use soleaux_storage::{OperationReservationOutcome, Store};"
new_import = "use soleaux_storage::{OperationReservationOutcome, PreviewClaimOutcome, Store};"
if text.count(old_import) != 1:
    raise SystemExit("P4-026 editor storage import drifted")
text = text.replace(old_import, new_import, 1)

operation_guard_end = '''impl Drop for OperationReservationGuard {
    fn drop(&mut self) {
        if !self.committed {
            let _ = self
                .store
                .release_operation(self.operation_key.clone(), self.request_hash.clone());
        }
    }
}
'''
preview_guard = operation_guard_end + '''
struct PreviewClaimGuard {
    store: Store,
    preview_id: String,
    binding_hash: String,
    claim_id: String,
    committed: bool,
}

impl PreviewClaimGuard {
    fn new(
        store: Store,
        preview_id: String,
        binding_hash: String,
        claim_id: String,
    ) -> Self {
        Self {
            store,
            preview_id,
            binding_hash,
            claim_id,
            committed: false,
        }
    }

    fn mark_committed(&mut self) {
        self.committed = true;
    }
}

impl Drop for PreviewClaimGuard {
    fn drop(&mut self) {
        if !self.committed {
            let _ = self.store.release_preview_claim(
                self.preview_id.clone(),
                self.binding_hash.clone(),
                self.claim_id.clone(),
            );
        }
    }
}
'''
if text.count(operation_guard_end) != 1:
    raise SystemExit("P4-026 operation guard insertion point drifted")
text = text.replace(operation_guard_end, preview_guard, 1)

start_marker = "    pub async fn apply(&self, preview_id: &str, digest: &str, confirm: bool) -> Result<Value> {\n"
end_marker = "    async fn rollback_after_failure(\n"
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("P4-026 apply method boundaries drifted")

new_apply = '''    pub async fn apply(&self, preview_id: &str, digest: &str, confirm: bool) -> Result<Value> {
        let workspace_id = self.index.workspace_id();
        let operation_key = format!("edit:{workspace_id}:{preview_id}");
        let request_hash = sha256_hex(
            format!(
                "editor.apply\n{workspace_id}\n{preview_id}\n{digest}\n{confirm}"
            )
            .as_bytes(),
        );
        self.apply_with_identity(
            preview_id,
            digest,
            confirm,
            operation_key,
            request_hash,
        )
        .await
    }

    async fn apply_with_identity(
        &self,
        preview_id: &str,
        digest: &str,
        confirm: bool,
        operation_key: String,
        request_hash: String,
    ) -> Result<Value> {
        if !confirm {
            bail!("edit requires confirm=true");
        }
        let workspace_id = self.index.workspace_id();
        let mut preview = self.load(preview_id)?;
        if preview.digest != digest {
            bail!("preview digest does not match");
        }
        if preview.workspace_id != workspace_id.to_string() {
            bail!("preview belongs to another workspace");
        }
        if preview.process_epoch != self.process_epoch {
            bail!("preview belongs to another Soleaux process epoch");
        }
        if unix_ms() > preview.expires_at_unix_ms {
            bail!("preview has expired");
        }
        let binding_hash = preview_binding_hash(&preview)?;
        let expires_at_unix_ms = i64::try_from(preview.expires_at_unix_ms)
            .context("preview expiration exceeds SQLite INTEGER")?;

        let mut reservation = match self.index.store().reserve_operation(
            operation_key.clone(),
            request_hash.clone(),
            "editor.apply",
            Some(workspace_id),
        )? {
            OperationReservationOutcome::Acquired => OperationReservationGuard::new(
                self.index.store().clone(),
                operation_key.clone(),
                request_hash.clone(),
            ),
            OperationReservationOutcome::InFlight => {
                bail!("edit operation is already in progress")
            }
            OperationReservationOutcome::Replayed(result) => return Ok(result),
        };

        let mut preview_claim = match self.index.store().claim_preview(
            preview.preview_id.clone(),
            binding_hash.clone(),
            workspace_id,
            expires_at_unix_ms,
        )? {
            PreviewClaimOutcome::Acquired { claim_id } => {
                if preview.consumed {
                    bail!("preview was consumed before durable claim tracking was available");
                }
                PreviewClaimGuard::new(
                    self.index.store().clone(),
                    preview.preview_id.clone(),
                    binding_hash.clone(),
                    claim_id,
                )
            }
            PreviewClaimOutcome::InFlight => {
                bail!("preview is already claimed by another edit operation")
            }
            PreviewClaimOutcome::Replayed(result) => {
                self.index.store().commit_operation(
                    operation_key.clone(),
                    request_hash.clone(),
                    result.clone(),
                )?;
                reservation.mark_committed();
                return Ok(result);
            }
        };

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
                    .rollback_after_failure(&mut preview, &receipt_id, &originals, &files, failure)
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
                Some(workspace_id),
                json!({
                    "receipt_id":receipt_id,
                    "preview_id":preview.preview_id,
                    "digest":preview.digest,
                    "preview_binding_hash":binding_hash,
                    "claim_id":preview_claim.claim_id,
                    "files":&files,
                    "reindexed":true,
                    "index_report":&report,
                }),
            )?;
            let result = json!({
                "schema_version":"soleaux.preview-consumption/v1",
                "receipt_id":receipt_id,
                "preview_id":preview.preview_id,
                "preview_binding_hash":binding_hash,
                "claim_id":preview_claim.claim_id,
                "applied":true,
                "files":files,
                "formatter":null,
                "diagnostics":[],
                "reindexed":true,
                "audit_event_hash":event.event_hash,
                "operation_key":operation_key,
                "replayed":false,
            });
            self.index.store().complete_preview_application(
                preview.preview_id.clone(),
                binding_hash.clone(),
                preview_claim.claim_id.clone(),
                operation_key.clone(),
                request_hash.clone(),
                result.clone(),
            )?;
            Ok::<_, anyhow::Error>(result)
        }
        .await;

        let result = match post_write {
            Ok(value) => value,
            Err(failure) => {
                return Err(self
                    .rollback_after_failure(&mut preview, &receipt_id, &originals, &files, failure)
                    .await);
            }
        };
        reservation.mark_committed();
        preview_claim.mark_committed();
        Ok(result)
    }

'''
text = text[:start] + new_apply + text[end:]

sha_marker = '''fn sha256_hex(bytes: &[u8]) -> String {
'''
binding_helper = '''fn preview_binding_hash(preview: &StoredPreview) -> Result<String> {
    let mut source_revisions = preview
        .patches
        .iter()
        .map(|patch| {
            json!({
                "path":patch.path,
                "preimage_sha256":patch.preimage_sha256,
                "start_byte":patch.start_byte,
                "end_byte":patch.end_byte,
                "replacement_sha256":sha256_hex(patch.replacement.as_bytes()),
            })
        })
        .collect::<Vec<_>>();
    source_revisions.sort_by(|left, right| left.to_string().cmp(&right.to_string()));
    let binding = json!({
        "schema_version":"soleaux.preview-claim-binding/v1",
        "preview_schema_version":preview.schema_version,
        "preview_id":preview.preview_id,
        "digest":preview.digest,
        "workspace_id":preview.workspace_id,
        "process_epoch":preview.process_epoch,
        "created_at_unix_ms":preview.created_at_unix_ms,
        "expires_at_unix_ms":preview.expires_at_unix_ms,
        "operation":preview.operation,
        "source_revisions":source_revisions,
        "non_overlapping":preview.non_overlapping,
        "formatter_plan":null,
        "diagnostic_plan":preview.validation_plan,
        "warnings":preview.warnings,
    });
    Ok(sha256_hex(&serde_json::to_vec(&binding)?))
}

'''
if text.count(sha_marker) != 1:
    raise SystemExit("P4-026 binding helper insertion point drifted")
text = text.replace(sha_marker, binding_helper + sha_marker, 1)

first_assert = '''        assert_eq!(result["applied"], true);
'''
first_replacement = '''        assert_eq!(result["schema_version"], "soleaux.preview-consumption/v1");
        assert_eq!(result["applied"], true);
        assert_eq!(result["preview_id"], preview.preview_id);
        assert_eq!(result["preview_binding_hash"], preview_binding_hash(&preview).expect("binding"));
'''
if text.count(first_assert) < 1:
    raise SystemExit("P4-026 first apply assertion drifted")
text = text.replace(first_assert, first_replacement, 1)

insert_marker = '''    #[tokio::test]
    async fn post_write_failure_restores_source_preview_and_receipt() {
'''
new_tests = '''    #[tokio::test]
    async fn different_operation_keys_share_one_atomic_preview_claim_and_receipt() {
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

        let first = editor.clone();
        let second = editor.clone();
        let first_preview = preview.clone();
        let second_preview = preview.clone();
        let (left, right) = tokio::join!(
            first.apply_with_identity(
                &first_preview.preview_id,
                &first_preview.digest,
                true,
                "edit:concurrent:left".to_string(),
                sha256_hex(b"concurrent-left"),
            ),
            second.apply_with_identity(
                &second_preview.preview_id,
                &second_preview.digest,
                true,
                "edit:concurrent:right".to_string(),
                sha256_hex(b"concurrent-right"),
            ),
        );
        let successful = [left.as_ref().ok(), right.as_ref().ok()]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();
        assert!(!successful.is_empty());
        if successful.len() == 2 {
            assert_eq!(successful[0], successful[1]);
        }
        assert_eq!(
            fs::read_to_string(&source_path).expect("read"),
            "export const value = 2;\n"
        );
        let applied_events = editor
            .index
            .store()
            .events_after(0, 100)
            .expect("events")
            .into_iter()
            .filter(|event| event.event_type == "editor.preview_applied")
            .count();
        assert_eq!(applied_events, 1);

        let replay = editor
            .apply_with_identity(
                &preview.preview_id,
                &preview.digest,
                true,
                "edit:concurrent:replay".to_string(),
                sha256_hex(b"concurrent-replay"),
            )
            .await
            .expect("durable preview replay");
        assert_eq!(replay, *successful[0]);
    }

    #[tokio::test]
    async fn preview_claim_binding_rejects_validation_plan_tampering() {
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
        let original_binding = preview_binding_hash(&preview).expect("original binding");
        let expiry = i64::try_from(preview.expires_at_unix_ms).expect("expiry");
        let acquired = editor
            .index
            .store()
            .claim_preview(
                preview.preview_id.clone(),
                original_binding.clone(),
                editor.index.workspace_id(),
                expiry,
            )
            .expect("manual claim");
        let PreviewClaimOutcome::Acquired { claim_id } = acquired else {
            panic!("expected manual preview claim");
        };

        let mut tampered = preview.clone();
        tampered
            .validation_plan
            .push("Run an unapproved external formatter".to_string());
        editor.persist(&tampered).expect("tampered preview fixture");
        let error = editor
            .apply_with_identity(
                &tampered.preview_id,
                &tampered.digest,
                true,
                "edit:tampered".to_string(),
                sha256_hex(b"tampered-request"),
            )
            .await
            .expect_err("binding mismatch must fail closed");
        assert!(format!("{error:#}").contains("immutable binding differs"));
        assert_eq!(
            fs::read_to_string(&source_path).expect("read"),
            "export const value = 1;\n"
        );
        editor
            .index
            .store()
            .release_preview_claim(preview.preview_id, original_binding, claim_id)
            .expect("release manual claim");
    }

'''
if text.count(insert_marker) != 1:
    raise SystemExit("P4-026 editor test insertion point drifted")
text = text.replace(insert_marker, new_tests + insert_marker, 1)

path.write_text(text, encoding="utf-8")
