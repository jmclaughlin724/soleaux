use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_ipc::{
    IpcClient, IpcMethod, IpcRequest, SoleauxPaths, install, install_service, restart_service,
    service_status, start_service, stop_service, uninstall,
};
use soleaux_state::{CanonicalEntityInput, HandoffPayload, StateStore, WorkspaceTrustState};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub async fn install_product(
    cli: Option<PathBuf>,
    daemon: Option<PathBuf>,
    start: bool,
) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let current = std::env::current_exe().context("resolving current Soleaux executable")?;
    let cli = cli.unwrap_or_else(|| current.clone());
    let daemon = daemon.unwrap_or_else(|| sibling_binary(&current, "soleauxd"));
    Ok(serde_json::to_value(install(
        &paths, &cli, &daemon, start,
    )?)?)
}

pub async fn service_install(daemon: Option<PathBuf>) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let current = std::env::current_exe().context("resolving current Soleaux executable")?;
    let daemon = daemon.unwrap_or_else(|| sibling_binary(&current, "soleauxd"));
    let manifest = install_service(&paths, &daemon)?;
    Ok(json!({
        "schemaVersion":"soleaux.service-install/v1",
        "manifest":manifest,
        "daemon":daemon,
        "started":false,
        "productionClaimAllowed":false,
    }))
}

pub async fn service_start() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    start_service(&paths)?;
    Ok(json!({"started":true,"endpoint":paths.endpoint}))
}

pub async fn service_stop() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let graceful = stop_service(&paths).await?;
    Ok(json!({"stopped":true,"gracefulIpcShutdown":graceful}))
}

pub async fn service_restart() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    restart_service(&paths).await?;
    Ok(json!({"restarted":true,"endpoint":paths.endpoint}))
}

pub async fn service_status_value() -> Result<Value> {
    Ok(serde_json::to_value(
        service_status(&SoleauxPaths::resolve()?).await?,
    )?)
}

pub fn cache_status() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let cache = paths.home.join("cache");
    let (files, bytes) = directory_usage(&cache)?;
    Ok(json!({
        "schemaVersion":"soleaux.cache-status/v1",
        "path":cache,
        "exists":cache.exists(),
        "files":files,
        "bytes":bytes,
    }))
}

pub fn cache_clear() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let cache = paths.home.join("cache");
    let (files, bytes) = directory_usage(&cache)?;
    if cache.exists() {
        fs::remove_dir_all(&cache)
            .with_context(|| format!("removing Soleaux cache {}", cache.display()))?;
    }
    Ok(json!({
        "schemaVersion":"soleaux.cache-clear/v1",
        "path":cache,
        "removedFiles":files,
        "removedBytes":bytes,
        "cleared":true,
    }))
}

pub async fn registry_call(method: IpcMethod) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let status = service_status(&paths).await?;
    if !status.running || !paths.endpoint.exists() {
        bail!(
            "the Soleaux per-user service must be running for workspace/client registry operations"
        );
    }
    ipc_result(&paths, method).await
}

/// Canonical-state operations owned by the daemon; the CLI never opens the
/// state database directly for them.
pub async fn daemon_call(method: IpcMethod) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let status = service_status(&paths).await?;
    if !status.running || !paths.endpoint.exists() {
        bail!("the Soleaux per-user service must be running for daemon canonical-state operations");
    }
    ipc_result(&paths, method).await
}

pub async fn apply_and_register_attach(root: &Path) -> Result<Value> {
    let (canonical, path, display_name) = canonical_workspace_registration(root)?;
    let manifest_path = canonical.join(".soleaux/backups/latest.json");
    let manifest_preimage = capture_file_preimage(&manifest_path)?;
    let receipt = soleaux_mcp::provisioning::apply_attach(&canonical)?;
    let registration = registry_call(IpcMethod::WorkspaceRegister {
        path,
        display_name,
        trust_state: WorkspaceTrustState::ReadOnly,
        metadata: json!({"attachedBy":"soleaux attach","canonicalRegistry":true}),
    })
    .await;
    finish_attachment(&canonical, receipt, registration, manifest_preimage).await
}

fn canonical_workspace_registration(root: &Path) -> Result<(PathBuf, String, Option<String>)> {
    let canonical = fs::canonicalize(root)
        .with_context(|| format!("resolving workspace path {}", root.display()))?;
    workspace_registration_from_canonical(canonical)
}

fn workspace_registration_from_canonical(
    canonical: PathBuf,
) -> Result<(PathBuf, String, Option<String>)> {
    let path = canonical
        .to_str()
        .context("workspace path is not valid UTF-8")?
        .to_owned();
    let display_name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .map(ToOwned::to_owned);
    Ok((canonical, path, display_name))
}

fn capture_file_preimage(path: &Path) -> Result<Option<Vec<u8>>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error)
            .with_context(|| format!("reading prior provisioning manifest {}", path.display())),
    }
}

fn restore_file_preimage(path: &Path, preimage: Option<&[u8]>) -> Result<()> {
    match preimage {
        Some(bytes) => atomic_write(path, bytes),
        None => match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error)
                .with_context(|| format!("removing replacement manifest {}", path.display())),
        },
    }
}

async fn finish_attachment(
    root: &Path,
    receipt: soleaux_mcp::provisioning::ProvisionReceipt,
    registration: Result<Value>,
    manifest_preimage: Option<Vec<u8>>,
) -> Result<Value> {
    match registration {
        Ok(registry) => {
            let workspace_id = registry
                .pointer("/workspace/id")
                .and_then(Value::as_str)
                .context("workspace registration omitted its canonical id")?
                .parse::<Uuid>()
                .context("workspace registration returned an invalid canonical id")?;
            if let Err(failure) =
                soleaux_mcp::provisioning::bind_attachment_workspace_id(root, workspace_id)
            {
                let registry_rollback =
                    registry_call(IpcMethod::WorkspaceForget { workspace_id }).await;
                let local_rollback = soleaux_mcp::provisioning::revert_last(root);
                let manifest_path = root.join(".soleaux/backups/latest.json");
                let manifest_restore =
                    restore_file_preimage(&manifest_path, manifest_preimage.as_deref());
                bail!(
                    "canonical workspace registration succeeded but binding its id to the attachment failed: {failure:#}; registry rollback: {registry_rollback:?}; local rollback: {local_rollback:?}; manifest restore: {manifest_restore:?}"
                );
            }
            Ok(json!({
                "schemaVersion":"soleaux.workspace-attach/v2",
                "provisioning":receipt,
                "registry":registry,
                "canonicalWorkspaceId":workspace_id,
                "productionClaimAllowed":false,
            }))
        }
        Err(failure) => {
            let rollback = soleaux_mcp::provisioning::revert_last(root);
            let manifest_path = root.join(".soleaux/backups/latest.json");
            let manifest_restore =
                restore_file_preimage(&manifest_path, manifest_preimage.as_deref());
            match (rollback, manifest_restore) {
                (Ok(restored), Ok(())) => bail!(
                    "canonical workspace registration failed and attachment files were restored ({restored:?}); the prior provisioning manifest was also restored: {failure:#}"
                ),
                (Ok(restored), Err(manifest_failure)) => bail!(
                    "canonical workspace registration failed and attachment files were restored ({restored:?}), but the prior provisioning manifest could not be restored: {manifest_failure:#}; original failure: {failure:#}"
                ),
                (Err(rollback), Ok(())) => bail!(
                    "canonical workspace registration failed: {failure:#}; attachment rollback also failed: {rollback:#}; the prior provisioning manifest was restored"
                ),
                (Err(rollback), Err(manifest_failure)) => bail!(
                    "canonical workspace registration failed: {failure:#}; attachment rollback also failed: {rollback:#}; prior provisioning manifest restoration also failed: {manifest_failure:#}"
                ),
            }
        }
    }
}

pub async fn revert_adoption(root: &Path) -> Result<Value> {
    let canonical = fs::canonicalize(root)
        .with_context(|| format!("resolving workspace path {}", root.display()))?;
    let workspace_id = soleaux_mcp::provisioning::attachment_workspace_id(&canonical)?;
    let marker_path = canonical.join(".soleaux/attachment.json");
    let marker_preimage = capture_file_preimage(&marker_path)?;
    let receipt_path = canonical.join(".soleaux/backups/last-revert.json");
    let receipt_preimage = capture_file_preimage(&receipt_path)?;
    let restored = soleaux_mcp::provisioning::revert_last(&canonical)?;
    let attachment_reverted = restored
        .iter()
        .any(|path| path == ".soleaux/attachment.json");

    if attachment_reverted
        && let Some(workspace_id) = workspace_id
        && let Err(failure) = registry_call(IpcMethod::WorkspaceForget { workspace_id }).await
    {
        let marker_restore = restore_file_preimage(&marker_path, marker_preimage.as_deref());
        let receipt_restore = restore_file_preimage(&receipt_path, receipt_preimage.as_deref());
        bail!(
            "local attachment revert completed but canonical workspace removal failed: {failure:#}; attachment marker restoration: {marker_restore:?}; revert receipt restoration: {receipt_restore:?}"
        );
    }

    Ok(json!({
        "schemaVersion":"soleaux.provisioning-revert/v2",
        "restored":restored,
        "canonicalWorkspaceId":workspace_id,
        "canonicalWorkspaceRemoved":attachment_reverted && workspace_id.is_some(),
        "productionClaimAllowed":false,
    }))
}

pub async fn backup(destination: PathBuf) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    if paths.endpoint.exists() {
        return ipc_result(
            &paths,
            IpcMethod::StateBackup {
                destination: destination.to_string_lossy().to_string(),
            },
        )
        .await;
    }
    let store = StateStore::open(&paths.state_database)?;
    Ok(serde_json::to_value(store.backup_to(destination)?)?)
}

pub async fn export_state(destination: PathBuf) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    if paths.endpoint.exists() {
        return ipc_result(
            &paths,
            IpcMethod::StateExport {
                destination: destination.to_string_lossy().to_string(),
            },
        )
        .await;
    }
    let store = StateStore::open(&paths.state_database)?;
    let snapshot = store.export_snapshot()?;
    let bytes = serde_json::to_vec_pretty(&snapshot)?;
    atomic_write(&destination, &bytes)?;
    Ok(json!({
        "schemaVersion":"soleaux.state-export/v1",
        "destination":destination,
        "bytes":bytes.len(),
        "blake3":blake3::hash(&bytes).to_hex().to_string(),
        "canonicalSchemaVersion":snapshot.schema_version,
        "entityCount":snapshot.entities.len(),
        "auditCount":snapshot.audit.len(),
    }))
}

pub async fn repair() -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    if paths.endpoint.exists() {
        return ipc_result(&paths, IpcMethod::StateRepair).await;
    }
    let store = StateStore::open(&paths.state_database)?;
    Ok(serde_json::to_value(store.repair()?)?)
}

pub async fn restore(source: PathBuf) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let status = service_status(&paths).await?;
    if status.running || paths.endpoint.exists() {
        bail!("stop the Soleaux service before restoring canonical state");
    }
    let manifest = StateStore::restore_backup(source, &paths.state_database)?;
    Ok(serde_json::to_value(manifest)?)
}

#[allow(clippy::too_many_arguments)]
pub fn create_handoff(
    source_session_id: Uuid,
    destination_platform: String,
    destination_session_id: Option<Uuid>,
    payload_hash: String,
    signature: String,
    workspace_id: Option<Uuid>,
    git_state: Value,
    code_state: Value,
) -> Result<Value> {
    validate_digest(&payload_hash)?;
    if signature.trim().is_empty() {
        bail!("handoff signature must be non-empty");
    }
    let paths = SoleauxPaths::resolve()?;
    let store = StateStore::open(&paths.state_database)?;
    let mut input = CanonicalEntityInput::active(HandoffPayload {
        source_session_id,
        destination_platform,
        destination_session_id,
        handoff_state: "created".to_string(),
        payload_hash: payload_hash.clone(),
        signature,
        git_state,
        code_state,
        artifact_ids: Vec::new(),
        permissions: json!({}),
        exclusions: json!({}),
        metadata: json!({"createdBy":"soleaux-cli"}),
    });
    input.workspace_id = workspace_id;
    input.parent_id = Some(source_session_id);
    input.idempotency_key = Some(format!("handoff:{source_session_id}:{payload_hash}"));
    Ok(serde_json::to_value(store.put(input)?)?)
}

pub async fn uninstall_product(preserve_state: bool, restore_native: bool) -> Result<Value> {
    let paths = SoleauxPaths::resolve()?;
    let report = uninstall(&paths, preserve_state).await?;
    Ok(json!({
        "schemaVersion":"soleaux.uninstall/v1",
        "uninstall":report,
        "restoreNativeRequested":restore_native,
        "nativeConfigurationRestored":false,
        "warnings": if restore_native {
            vec!["No vendor-native configuration receipt was supplied; native stores were not mutated or guessed."]
        } else {
            Vec::<&str>::new()
        },
    }))
}

async fn ipc_result(paths: &SoleauxPaths, method: IpcMethod) -> Result<Value> {
    IpcClient::new(&paths.endpoint)
        .call(IpcRequest::new(method))
        .await?
        .result
        .context("successful Soleaux IPC response omitted its result")
}

fn sibling_binary(current: &Path, name: &str) -> PathBuf {
    let file = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    current.with_file_name(file)
}

fn directory_usage(path: &Path) -> Result<(u64, u64)> {
    if !path.exists() {
        return Ok((0, 0));
    }
    let mut files = 0u64;
    let mut bytes = 0u64;
    let mut pending = vec![path.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory)
            .with_context(|| format!("reading cache directory {}", directory.display()))?
        {
            let entry = entry?;
            let metadata = entry.metadata()?;
            if metadata.is_dir() {
                pending.push(entry.path());
            } else if metadata.is_file() {
                files = files.saturating_add(1);
                bytes = bytes.saturating_add(metadata.len());
            }
        }
    }
    Ok((files, bytes))
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("output path has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::now_v7()));
    fs::write(&temporary, bytes)?;
    fs::rename(&temporary, path)
        .with_context(|| format!("installing output {}", path.display()))?;
    Ok(())
}

fn validate_digest(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        bail!("handoff payload hash must be 64 lowercase hexadecimal characters");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use anyhow::anyhow;
    use tempfile::tempdir;

    #[tokio::test]
    async fn failed_canonical_registration_rolls_back_workspace_attachment() {
        let directory = tempdir().expect("tempdir");
        let root = fs::canonicalize(directory.path()).expect("root");
        let manifest_path = root.join(".soleaux/backups/latest.json");
        let manifest_preimage = capture_file_preimage(&manifest_path).expect("manifest preimage");
        let receipt = soleaux_mcp::provisioning::apply_attach(&root).expect("apply attach");
        assert!(root.join(".soleaux/attachment.json").is_file());
        let error = finish_attachment(
            &root,
            receipt,
            Err(anyhow!("registry unavailable")),
            manifest_preimage,
        )
        .await
        .expect_err("registration failure must roll back attachment");
        assert!(error.to_string().contains("attachment files were restored"));
        assert!(!root.join(".soleaux/attachment.json").exists());
        assert!(!manifest_path.exists());
    }

    #[tokio::test]
    async fn failed_registration_restores_the_prior_provisioning_manifest() {
        let directory = tempdir().expect("tempdir");
        let root = fs::canonicalize(directory.path()).expect("root");
        soleaux_mcp::provisioning::apply_adopt(&root).expect("prior adopt");
        let manifest_path = root.join(".soleaux/backups/latest.json");
        let prior = fs::read(&manifest_path).expect("prior manifest");
        let manifest_preimage = capture_file_preimage(&manifest_path).expect("manifest preimage");
        let receipt = soleaux_mcp::provisioning::apply_attach(&root).expect("apply attach");
        finish_attachment(
            &root,
            receipt,
            Err(anyhow!("registry unavailable")),
            manifest_preimage,
        )
        .await
        .expect_err("registration failure must roll back attachment");
        assert_eq!(fs::read(&manifest_path).expect("restored manifest"), prior);
        assert!(!root.join(".soleaux/attachment.json").exists());
    }

    #[tokio::test]
    async fn reverting_a_later_adopt_preserves_an_existing_attachment() {
        let directory = tempdir().expect("tempdir");
        let root = fs::canonicalize(directory.path()).expect("root");
        soleaux_mcp::provisioning::apply_attach(&root).expect("attach");
        let workspace_id = Uuid::now_v7();
        soleaux_mcp::provisioning::bind_attachment_workspace_id(&root, workspace_id)
            .expect("bind canonical workspace id");
        let marker_path = root.join(".soleaux/attachment.json");
        let marker_before = fs::read(&marker_path).expect("attachment marker");

        soleaux_mcp::provisioning::apply_adopt(&root).expect("later adopt");
        let result = revert_adoption(&root).await.expect("revert later adopt");

        assert_eq!(result["canonicalWorkspaceId"], workspace_id.to_string());
        assert_eq!(result["canonicalWorkspaceRemoved"], false);
        assert_eq!(
            fs::read(&marker_path).expect("preserved marker"),
            marker_before
        );
    }

    #[cfg(unix)]
    #[test]
    fn non_utf8_workspace_registration_is_rejected_before_provisioning() {
        use std::{ffi::OsString, os::unix::ffi::OsStringExt};

        let invalid = PathBuf::from(OsString::from_vec(vec![b'w', 0xff]));
        let error = workspace_registration_from_canonical(invalid)
            .expect_err("non-UTF8 workspace must fail closed");
        assert!(error.to_string().contains("valid UTF-8"));
    }
}
