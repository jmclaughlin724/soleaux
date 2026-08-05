#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATH = Path("native/daemon/mcp/src/provisioning.rs")
text = PATH.read_text(encoding="utf-8")


def replace_between(source: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        raise SystemExit(f"{label}: start marker not found")
    end_index = source.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{label}: end marker not found")
    return source[:start_index] + replacement + "\n\n" + source[end_index:]


text = text.replace(
    "use std::{\n    fs,",
    "use std::{\n    ffi::OsString,\n    fs,",
    1,
)

text = replace_between(
    text,
    "#[derive(Debug, Clone, Serialize, Deserialize)]\n#[serde(rename_all = \"snake_case\")]\nstruct BackupRecord {",
    "pub fn adopt_plan",
    r'''#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
enum BackupScope {
    #[default]
    Workspace,
    SoleauxHome,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct BackupRecord {
    #[serde(default)]
    scope: BackupScope,
    path: String,
    backup_path: Option<String>,
    created: bool,
    preimage_sha256: Option<String>,
    applied_sha256: String,
}

#[derive(Debug, Clone)]
struct AdditionalWrite {
    scope: BackupScope,
    path: String,
    rendered: Vec<u8>,
}

#[derive(Debug, Clone)]
struct PreparedWrite {
    scope: BackupScope,
    path: String,
    target: PathBuf,
    existing: Option<Vec<u8>>,
    rendered: Vec<u8>,
    backup_path: Option<String>,
    receipt_path: String,
}

#[derive(Debug, Clone)]
struct PreparedRevert {
    path: String,
    target: PathBuf,
    current: Vec<u8>,
    restore: Option<Vec<u8>>,
}''',
    "backup transaction types",
)

text = replace_between(
    text,
    "pub fn apply_attach(root: &Path) -> Result<ProvisionReceipt> {",
    "pub fn revert_last",
    r'''pub fn apply_attach(root: &Path) -> Result<ProvisionReceipt> {
    let root = canonical_root(root)?;
    let plan = attach_plan(&root)?;
    let id = workspace_id(&root);
    let registry_relative = format!("workspaces/{id}.json");
    let value = json!({
        "schema_version":"soleaux.workspace-attachment/v1",
        "workspace_id":id,
        "workspace":root,
        "profile_digest":"89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc",
        "context_digest":"3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f",
        "public_tool_ceiling":12,
        "production_claim_allowed":false,
    });
    apply_plan_with_additional(
        &root,
        &plan,
        vec![AdditionalWrite {
            scope: BackupScope::SoleauxHome,
            path: registry_relative,
            rendered: serde_json::to_vec_pretty(&value)?,
        }],
    )
}''',
    "transactional attach",
)

text = replace_between(
    text,
    "pub fn revert_last(root: &Path) -> Result<Vec<String>> {",
    "fn render_action(",
    r'''pub fn revert_last(root: &Path) -> Result<Vec<String>> {
    let root = canonical_root(root)?;
    let mut writer = |path: &Path, bytes: &[u8]| atomic_write(path, bytes);
    revert_last_with_writer(&root, &mut writer)
}

fn revert_last_with_writer<F>(root: &Path, writer: &mut F) -> Result<Vec<String>>
where
    F: FnMut(&Path, &[u8]) -> Result<()>,
{
    let manifest_path = root.join(".soleaux/backups/latest.json");
    let manifest: BackupManifest = serde_json::from_slice(
        &fs::read(&manifest_path)
            .with_context(|| format!("reading backup manifest {}", manifest_path.display()))?,
    )?;
    if Path::new(&manifest.workspace) != root {
        bail!("backup manifest belongs to a different workspace");
    }

    // Validate every target and load every backup before changing any path.
    let mut prepared = Vec::new();
    for record in manifest.records.iter().rev() {
        let target = admit_scoped(root, record.scope, &record.path)?;
        let current = read_optional(&target)?
            .with_context(|| format!("provisioned target {} is missing", target.display()))?;
        let current_hash = sha256_hex(&current);
        if current_hash != record.applied_sha256 {
            bail!(
                "refusing to overwrite locally modified provisioned file {}",
                record.path
            );
        }
        let restore = if record.created {
            None
        } else {
            let backup = record
                .backup_path
                .as_deref()
                .context("existing provisioned file omitted its backup path")?;
            Some(
                fs::read(admit(root, backup)?)
                    .with_context(|| format!("reading provisioning backup {backup}"))?,
            )
        };
        prepared.push(PreparedRevert {
            path: record.path.clone(),
            target,
            current,
            restore,
        });
    }

    let mut changed: Vec<&PreparedRevert> = Vec::new();
    for entry in &prepared {
        let result = match &entry.restore {
            Some(bytes) => writer(&entry.target, bytes),
            None => fs::remove_file(&entry.target)
                .with_context(|| format!("removing provisioned file {}", entry.target.display())),
        };
        if let Err(failure) = result {
            let rollback = rollback_reverts(&changed);
            return Err(transaction_error("provisioning revert", failure, rollback));
        }
        changed.push(entry);
    }

    let receipt_path = root.join(".soleaux/backups/last-revert.json");
    let receipt = json!({
        "schema_version":"soleaux.provisioning-revert/v1",
        "workspace":root,
        "manifest_path":manifest_path,
        "restored":prepared.iter().map(|entry| entry.path.clone()).collect::<Vec<_>>(),
        "created_unix_ms":unix_ms(),
    });
    if let Err(failure) = writer(&receipt_path, &serde_json::to_vec_pretty(&receipt)?) {
        let rollback = rollback_reverts(&changed);
        return Err(transaction_error(
            "persisting provisioning revert receipt",
            failure,
            rollback,
        ));
    }
    Ok(prepared.into_iter().map(|entry| entry.path).collect())
}

fn apply_plan(root: &Path, plan: &ProvisionPlan) -> Result<ProvisionReceipt> {
    apply_plan_with_additional(root, plan, Vec::new())
}

fn apply_plan_with_additional(
    root: &Path,
    plan: &ProvisionPlan,
    additional: Vec<AdditionalWrite>,
) -> Result<ProvisionReceipt> {
    let mut writer = |path: &Path, bytes: &[u8]| atomic_write(path, bytes);
    apply_plan_with_writer(root, plan, additional, &mut writer)
}

fn apply_plan_with_writer<F>(
    root: &Path,
    plan: &ProvisionPlan,
    additional: Vec<AdditionalWrite>,
    writer: &mut F,
) -> Result<ProvisionReceipt>
where
    F: FnMut(&Path, &[u8]) -> Result<()>,
{
    if Path::new(&plan.workspace) != root {
        bail!("provisioning plan belongs to a different workspace");
    }
    if plan.root_tool_inflation || plan.public_tool_ceiling != 12 {
        bail!("provisioning plan violates the locked twelve-tool profile");
    }

    let timestamp = unix_ms();
    let backup_root = root.join(".soleaux/backups").join(timestamp.to_string());
    let writes = prepare_writes(root, plan, additional, timestamp)?;

    // Persist every preimage before the first configured path is changed.
    let mut backups = Vec::new();
    if let Err(failure) = persist_backups(root, &backup_root, &writes, &mut backups) {
        let cleanup = cleanup_backup_root(&backup_root);
        return Err(transaction_error(
            "persisting provisioning backups",
            failure,
            cleanup,
        ));
    }

    let mut applied: Vec<&PreparedWrite> = Vec::new();
    for write in &writes {
        if let Err(failure) = writer(&write.target, &write.rendered) {
            let rollback = rollback_apply(&applied, &backup_root);
            return Err(transaction_error("applying provisioning plan", failure, rollback));
        }
        applied.push(write);
    }

    let manifest = BackupManifest {
        schema_version: "soleaux.provisioning-backup/v2".to_string(),
        workspace: root.to_string_lossy().to_string(),
        created_unix_ms: timestamp,
        records: writes
            .iter()
            .map(|write| BackupRecord {
                scope: write.scope,
                path: write.path.clone(),
                backup_path: write.backup_path.clone(),
                created: write.existing.is_none(),
                preimage_sha256: write.existing.as_deref().map(sha256_hex),
                applied_sha256: sha256_hex(&write.rendered),
            })
            .collect(),
    };
    let manifest_path = root.join(".soleaux/backups/latest.json");
    if let Err(failure) = writer(&manifest_path, &serde_json::to_vec_pretty(&manifest)?) {
        let rollback = rollback_apply(&applied, &backup_root);
        return Err(transaction_error(
            "persisting provisioning manifest",
            failure,
            rollback,
        ));
    }

    Ok(ProvisionReceipt {
        workspace: root.to_string_lossy().to_string(),
        manifest_path: manifest_path.to_string_lossy().to_string(),
        written: writes
            .iter()
            .map(|write| write.receipt_path.clone())
            .collect(),
        backups,
        root_tool_inflation: false,
    })
}

fn prepare_writes(
    root: &Path,
    plan: &ProvisionPlan,
    additional: Vec<AdditionalWrite>,
    timestamp: u64,
) -> Result<Vec<PreparedWrite>> {
    let mut writes = Vec::new();
    for action in &plan.actions {
        let target = admit(root, &action.path)?;
        let existing = read_optional(&target)?;
        let observed = existing.as_deref().map(sha256_hex);
        if observed != action.preimage_sha256 {
            bail!(
                "provisioning preimage changed after planning for {}",
                action.path
            );
        }
        let rendered = render_action(root, action, existing.as_deref())?;
        writes.push(prepared_write(
            root,
            timestamp,
            BackupScope::Workspace,
            action.path.clone(),
            target,
            existing,
            rendered,
        )?);
    }
    for write in additional {
        let target = admit_scoped(root, write.scope, &write.path)?;
        let existing = read_optional(&target)?;
        writes.push(prepared_write(
            root,
            timestamp,
            write.scope,
            write.path,
            target,
            existing,
            write.rendered,
        )?);
    }
    Ok(writes)
}

fn prepared_write(
    root: &Path,
    timestamp: u64,
    scope: BackupScope,
    path: String,
    target: PathBuf,
    existing: Option<Vec<u8>>,
    rendered: Vec<u8>,
) -> Result<PreparedWrite> {
    let backup_path = existing.as_ref().map(|_| {
        let storage_path = match scope {
            BackupScope::Workspace => path.clone(),
            BackupScope::SoleauxHome => format!("external/soleaux_home/{path}"),
        };
        format!(".soleaux/backups/{timestamp}/{storage_path}")
    });
    if let Some(path) = &backup_path {
        let _ = admit(root, path)?;
    }
    let receipt_path = match scope {
        BackupScope::Workspace => path.clone(),
        BackupScope::SoleauxHome => target.to_string_lossy().to_string(),
    };
    Ok(PreparedWrite {
        scope,
        path,
        target,
        existing,
        rendered,
        backup_path,
        receipt_path,
    })
}

fn persist_backups(
    root: &Path,
    backup_root: &Path,
    writes: &[PreparedWrite],
    backups: &mut Vec<String>,
) -> Result<()> {
    fs::create_dir_all(backup_root)?;
    for write in writes {
        let Some(existing) = &write.existing else {
            continue;
        };
        let backup_path = write
            .backup_path
            .as_deref()
            .context("existing provisioning target omitted backup path")?;
        let backup = admit(root, backup_path)?;
        atomic_write(&backup, existing)?;
        backups.push(backup.to_string_lossy().to_string());
    }
    Ok(())
}

fn rollback_apply(applied: &[&PreparedWrite], backup_root: &Path) -> Result<()> {
    let mut errors = Vec::new();
    for write in applied.iter().rev() {
        let result = match &write.existing {
            Some(bytes) => atomic_write(&write.target, bytes),
            None if write.target.exists() => fs::remove_file(&write.target)
                .with_context(|| format!("removing {}", write.target.display())),
            None => Ok(()),
        };
        if let Err(error) = result {
            errors.push(format!("restoring {} failed: {error:#}", write.target.display()));
        }
    }
    if let Err(error) = cleanup_backup_root(backup_root) {
        errors.push(format!("removing backup transaction failed: {error:#}"));
    }
    if errors.is_empty() {
        Ok(())
    } else {
        bail!(errors.join("; "))
    }
}

fn rollback_reverts(changed: &[&PreparedRevert]) -> Result<()> {
    let mut errors = Vec::new();
    for entry in changed.iter().rev() {
        if let Err(error) = atomic_write(&entry.target, &entry.current) {
            errors.push(format!("restoring {} failed: {error:#}", entry.target.display()));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        bail!(errors.join("; "))
    }
}

fn cleanup_backup_root(backup_root: &Path) -> Result<()> {
    if backup_root.exists() {
        fs::remove_dir_all(backup_root)
            .with_context(|| format!("removing backup transaction {}", backup_root.display()))?;
    }
    Ok(())
}

fn transaction_error(operation: &str, failure: anyhow::Error, rollback: Result<()>) -> anyhow::Error {
    match rollback {
        Ok(()) => anyhow::anyhow!("{operation} failed and all changed paths were restored: {failure:#}"),
        Err(rollback) => anyhow::anyhow!(
            "{operation} failed: {failure:#}; rollback reconciliation failed: {rollback:#}"
        ),
    }
}

fn read_optional(path: &Path) -> Result<Option<Vec<u8>>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error)
            .with_context(|| format!("reading provisioning target {}", path.display())),
    }
}''',
    "transactional apply and revert",
)

text = text.replace(
    "    let existing = fs::read(&target).ok();",
    "    let existing = read_optional(&target)?;",
    1,
)

text = replace_between(
    text,
    "fn admit(root: &Path, relative: &str) -> Result<PathBuf> {",
    "fn atomic_write(",
    r'''fn admit(root: &Path, relative: &str) -> Result<PathBuf> {
    validate_relative_path(relative)?;
    let target = root.join(relative);
    let parent = target.parent().context("provisioning path has no parent")?;
    let mut existing = parent;
    while !existing.exists() {
        existing = existing
            .parent()
            .context("provisioning path has no existing ancestor")?;
    }
    let canonical_parent = fs::canonicalize(existing)?;
    if !canonical_parent.starts_with(root) {
        bail!("provisioning path escaped the workspace root");
    }
    if target.exists() {
        let canonical_target = fs::canonicalize(&target)?;
        if !canonical_target.starts_with(root) {
            bail!("provisioning target escaped the workspace root");
        }
    }
    Ok(target)
}

fn admit_scoped(root: &Path, scope: BackupScope, relative: &str) -> Result<PathBuf> {
    match scope {
        BackupScope::Workspace => admit(root, relative),
        BackupScope::SoleauxHome => {
            validate_relative_path(relative)?;
            let home = normalized_base(&crate::gateway::soleaux_home()?)?;
            if home.exists() {
                admit(&home, relative)
            } else {
                Ok(home.join(relative))
            }
        }
    }
}

fn validate_relative_path(relative: &str) -> Result<()> {
    let relative = Path::new(relative);
    if relative.is_absolute()
        || relative.components().any(|component| {
            matches!(
                component,
                std::path::Component::ParentDir
                    | std::path::Component::RootDir
                    | std::path::Component::Prefix(_)
            )
        })
    {
        bail!("provisioning paths must remain repository-relative");
    }
    Ok(())
}

fn normalized_base(base: &Path) -> Result<PathBuf> {
    if base.exists() {
        return fs::canonicalize(base)
            .with_context(|| format!("resolving provisioning base {}", base.display()));
    }
    let mut ancestor = base;
    let mut suffix: Vec<OsString> = Vec::new();
    while !ancestor.exists() {
        suffix.push(
            ancestor
                .file_name()
                .context("provisioning base has no existing ancestor")?
                .to_os_string(),
        );
        ancestor = ancestor
            .parent()
            .context("provisioning base has no existing ancestor")?;
    }
    let mut normalized = fs::canonicalize(ancestor)?;
    for component in suffix.iter().rev() {
        normalized.push(component);
    }
    Ok(normalized)
}''',
    "non-mutating path admission",
)

text = replace_between(
    text,
    "#[cfg(test)]\nmod tests {",
    "",
    r'''#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use tempfile::tempdir;

    #[test]
    fn adopt_is_previewable_reversible_and_does_not_add_tools() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("AGENTS.md"), "# Existing\n").expect("guidance");
        let plan = adopt_plan(directory.path()).expect("plan");
        assert!(!plan.root_tool_inflation);
        assert_eq!(plan.public_tool_ceiling, 12);
        assert!(plan.actions.iter().any(|action| action.path == ".mcp.json"));
        let receipt = apply_adopt(directory.path()).expect("apply");
        assert!(!receipt.root_tool_inflation);
        assert!(directory.path().join(".mcp.json").is_file());
        let guidance = fs::read_to_string(directory.path().join("AGENTS.md")).expect("read");
        assert!(guidance.contains(MANAGED_BEGIN));
        let restored = revert_last(directory.path()).expect("revert");
        assert!(restored.contains(&"AGENTS.md".to_string()));
        assert_eq!(
            fs::read_to_string(directory.path().join("AGENTS.md")).expect("read"),
            "# Existing\n"
        );
        assert!(!directory.path().join(".mcp.json").exists());
    }

    #[test]
    fn apply_rolls_back_all_prior_paths_when_a_late_write_fails() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        fs::write(root.join("AGENTS.md"), "# Existing\n").expect("guidance");
        let plan = adopt_plan(&root).expect("plan");
        let failure_path = root.join("soleaux.toml");
        let failed = Cell::new(false);
        let mut writer = |path: &Path, bytes: &[u8]| -> Result<()> {
            if path == failure_path && !failed.replace(true) {
                bail!("injected late provisioning write failure");
            }
            atomic_write(path, bytes)
        };
        let error = apply_plan_with_writer(&root, &plan, Vec::new(), &mut writer)
            .expect_err("transaction must fail");
        assert!(error.to_string().contains("all changed paths were restored"));
        assert_eq!(
            fs::read_to_string(root.join("AGENTS.md")).expect("guidance"),
            "# Existing\n"
        );
        for path in [".mcp.json", ".codex/config.toml", "opencode.json", "soleaux.toml"] {
            assert!(!root.join(path).exists(), "{path} should be rolled back");
        }
        assert!(!root.join(".soleaux/backups/latest.json").exists());
    }

    #[test]
    fn revert_prevalidates_every_target_before_changing_any_path() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        fs::write(root.join("AGENTS.md"), "# Existing\n").expect("guidance");
        apply_adopt(&root).expect("apply");
        let managed_guidance = fs::read(root.join("AGENTS.md")).expect("managed guidance");
        let managed_config = fs::read(root.join(".codex/config.toml")).expect("managed config");
        fs::write(root.join(".mcp.json"), b"locally modified").expect("local edit");
        let error = revert_last(&root).expect_err("revert must fail closed");
        assert!(error.to_string().contains("locally modified"));
        assert_eq!(fs::read(root.join("AGENTS.md")).expect("guidance"), managed_guidance);
        assert_eq!(
            fs::read(root.join(".codex/config.toml")).expect("config"),
            managed_config
        );
        assert!(root.join("soleaux.toml").exists());
    }

    #[test]
    fn revert_rolls_back_prior_reversions_when_restore_fails() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        fs::write(root.join("AGENTS.md"), "# Existing\n").expect("guidance");
        apply_adopt(&root).expect("apply");
        let before_guidance = fs::read(root.join("AGENTS.md")).expect("guidance");
        let before_provider = fs::read(root.join("soleaux.toml")).expect("provider");
        let failure_path = root.join("AGENTS.md");
        let failed = Cell::new(false);
        let mut writer = |path: &Path, bytes: &[u8]| -> Result<()> {
            if path == failure_path && !failed.replace(true) {
                bail!("injected revert restore failure");
            }
            atomic_write(path, bytes)
        };
        let error = revert_last_with_writer(&root, &mut writer)
            .expect_err("revert transaction must fail");
        assert!(error.to_string().contains("all changed paths were restored"));
        assert_eq!(fs::read(root.join("AGENTS.md")).expect("guidance"), before_guidance);
        assert_eq!(fs::read(root.join("soleaux.toml")).expect("provider"), before_provider);
        assert!(root.join(".mcp.json").exists());
    }

    #[test]
    fn path_traversal_is_rejected() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        assert!(admit(&root, "../escape").is_err());
        assert!(admit(&root, "/absolute").is_err());
    }
}
''',
    "transactional provisioning tests",
)

PATH.write_text(text, encoding="utf-8")
