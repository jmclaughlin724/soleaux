//! Cross-host materializer compatibility compiler with atomic apply/rollback.
//!
//! P5-008: canonical rules, skills, and agents compile into the documented
//! guidance surfaces of Claude Code, Codex, OpenCode, and Cursor with a
//! per-target compatibility and degradation report. Only guidance is compiled;
//! documented host enforcement surfaces (Claude Code hooks, `.codex/rules`,
//! OpenCode plugins) are reported and never written. Applies ride the
//! provisioning discipline: preimage-bound writes, backups, atomic
//! replacement, rollback, and post-apply native load verification. Every
//! materialized file or region carries origin markers, so the registry scan
//! never re-ingests materializer output as a source object.

use crate::provisioning::{
    admit, atomic_write, canonical_root, cleanup_backup_root, read_optional, transaction_error,
    unix_ms,
};
use crate::registry::sha256_hex;
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use soleaux_state::{
    AgentPayload, CanonicalEntityInput, EntityLinkInput, MaterializationPayload, RelationshipKind,
    RulePayload, SkillPayload, StateStore,
};
use std::{
    borrow::Cow,
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub const MATERIALIZER_ORIGIN: &str = "soleaux-materializer";
pub const PLAN_SCHEMA_VERSION: &str = "soleaux.materialization-plan/v1";
pub const RECEIPT_SCHEMA_VERSION: &str = "soleaux.materialization-receipt/v1";
const MANIFEST_SCHEMA_VERSION: &str = "soleaux.materializer-backup/v1";
const REVERT_SCHEMA_VERSION: &str = "soleaux.materializer-revert/v1";
const MANIFEST_RELATIVE: &str = ".soleaux/backups/materialize-latest.json";
const REVERT_RECEIPT_RELATIVE: &str = ".soleaux/backups/materialize-last-revert.json";
const BEGIN_MARKER_PREFIX: &str = "<!-- soleaux:materialized:begin";
const END_MARKER_PREFIX: &str = "<!-- soleaux:materialized:end";
const MARKER_SUFFIX: &str = "-->";
const MAX_DIFF_BYTES: usize = 32 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum TargetPlatform {
    ClaudeCode,
    Codex,
    OpenCode,
    Cursor,
}

impl TargetPlatform {
    pub const ALL: [Self; 4] = [Self::ClaudeCode, Self::Codex, Self::OpenCode, Self::Cursor];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ClaudeCode => "claude-code",
            Self::Codex => "codex",
            Self::OpenCode => "opencode",
            Self::Cursor => "cursor",
        }
    }

    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "claude-code" => Ok(Self::ClaudeCode),
            "codex" => Ok(Self::Codex),
            "opencode" => Ok(Self::OpenCode),
            "cursor" => Ok(Self::Cursor),
            other => bail!("unsupported materialization target platform: {other}"),
        }
    }

    fn guidance_surfaces(self) -> &'static [&'static str] {
        match self {
            Self::ClaudeCode => &[
                ".claude/rules",
                ".claude/skills",
                ".claude/agents",
                "CLAUDE.md",
            ],
            Self::Codex => &["AGENTS.md"],
            Self::OpenCode => &["AGENTS.md", "opencode.json"],
            Self::Cursor => &[],
        }
    }

    /// Documented host enforcement surfaces. The compiler reports these and
    /// never writes them: enforcement is host-reviewed configuration, not a
    /// guidance materialization.
    fn enforcement_surfaces(self) -> &'static [&'static str] {
        match self {
            Self::ClaudeCode => &[".claude/settings.json hooks"],
            Self::Codex => &[".codex/rules"],
            Self::OpenCode => &["opencode plugins"],
            Self::Cursor => &[],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectKind {
    Rule,
    Skill,
    Agent,
}

impl ObjectKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Rule => "rule",
            Self::Skill => "skill",
            Self::Agent => "agent",
        }
    }
}

/// One canonical object presented for materialization. The identifier is the
/// canonical entity id when the object lives in the state database; callers
/// without canonical records may pass any stable UUID and the persistence
/// step reports the missing `Materializes` link truthfully.
#[derive(Debug, Clone)]
pub enum MaterializeObject {
    Rule { id: Uuid, payload: RulePayload },
    Skill { id: Uuid, payload: SkillPayload },
    Agent { id: Uuid, payload: AgentPayload },
}

impl MaterializeObject {
    pub fn id(&self) -> Uuid {
        match self {
            Self::Rule { id, .. } | Self::Skill { id, .. } | Self::Agent { id, .. } => *id,
        }
    }

    pub const fn kind(&self) -> ObjectKind {
        match self {
            Self::Rule { .. } => ObjectKind::Rule,
            Self::Skill { .. } => ObjectKind::Skill,
            Self::Agent { .. } => ObjectKind::Agent,
        }
    }

    pub fn name(&self) -> &str {
        match self {
            Self::Rule { payload, .. } => &payload.name,
            Self::Skill { payload, .. } => &payload.name,
            Self::Agent { payload, .. } => &payload.name,
        }
    }

    pub fn revision(&self) -> &str {
        match self {
            Self::Rule { payload, .. } => &payload.object_revision,
            Self::Skill { payload, .. } => &payload.object_revision,
            Self::Agent { payload, .. } => &payload.object_revision,
        }
    }

    fn body(&self) -> &str {
        match self {
            Self::Rule { payload, .. } => &payload.guidance,
            Self::Skill { payload, .. } => &payload.instructions,
            Self::Agent { payload, .. } => &payload.instructions,
        }
    }

    fn description(&self) -> &str {
        match self {
            Self::Rule { .. } => "",
            Self::Skill { payload, .. } => &payload.description,
            Self::Agent { payload, .. } => &payload.description,
        }
    }

    fn slug(&self) -> String {
        object_slug(self.name())
    }

    /// A rule that declares enforcement beyond guidance cannot be compiled by
    /// the materializer; only its guidance is written.
    fn declared_enforcement(&self) -> Option<&str> {
        let Self::Rule { payload, .. } = self else {
            return None;
        };
        let enforcement = payload.enforcement.trim();
        if enforcement.is_empty()
            || enforcement.eq_ignore_ascii_case("none")
            || enforcement.eq_ignore_ascii_case("guidance")
        {
            None
        } else {
            Some(enforcement)
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct ObjectVerdict {
    pub object_id: Uuid,
    pub kind: String,
    pub name: String,
    pub revision: String,
    pub status: String,
    pub mechanism: String,
    pub target_path: Option<String>,
    pub guidance: String,
    pub enforcement: String,
    pub degradations: Vec<String>,
    pub idempotency_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct TargetReport {
    pub platform: String,
    pub write_mode: String,
    pub guidance_surfaces: Vec<String>,
    pub enforcement_surfaces: Vec<String>,
    pub objects: Vec<ObjectVerdict>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MaterializeAction {
    pub path: String,
    pub mode: String,
    pub platforms: Vec<String>,
    pub would_create: bool,
    pub changed: bool,
    pub preimage_sha256: Option<String>,
    pub rendered_sha256: String,
    pub diff: String,
    #[serde(skip)]
    pub rendered: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MaterializePlan {
    pub schema_version: String,
    pub workspace: String,
    pub targets: Vec<TargetReport>,
    pub actions: Vec<MaterializeAction>,
    pub root_tool_inflation: bool,
    pub public_tool_ceiling: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MaterializeVerification {
    pub path: String,
    pub expected_sha256: String,
    pub observed_sha256: Option<String>,
    pub verified: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct MaterializeReceipt {
    pub schema_version: String,
    pub workspace: String,
    pub manifest_path: Option<String>,
    pub written: Vec<String>,
    pub unchanged: Vec<String>,
    pub backups: Vec<String>,
    pub verification: Vec<MaterializeVerification>,
    pub load_verified: bool,
    pub root_tool_inflation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct PersistedMaterializationRecord {
    pub object_id: Uuid,
    pub target_platform: String,
    pub materialization_id: Option<Uuid>,
    pub materialization_state: String,
    pub linked: bool,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct MaterializerManifest {
    schema_version: String,
    workspace: String,
    created_unix_ms: u64,
    records: Vec<MaterializerBackupRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
struct MaterializerBackupRecord {
    path: String,
    backup_path: Option<String>,
    created: bool,
    preimage_sha256: Option<String>,
    applied_sha256: String,
}

enum SurfaceRoute {
    File { path: String },
    Region { path: String },
    AgentMerge { path: String },
    ReportOnly,
}

struct Routing {
    surface: SurfaceRoute,
    status: &'static str,
    mechanism: &'static str,
    degradations: Vec<String>,
}

#[derive(Default)]
struct PathPlan {
    mode: Option<&'static str>,
    platforms: BTreeSet<&'static str>,
    file: Option<(Uuid, String)>,
    blocks: BTreeMap<Uuid, String>,
    agents: BTreeMap<String, Value>,
}

struct PreparedMaterializeWrite<'plan> {
    action: &'plan MaterializeAction,
    target: PathBuf,
    existing: Option<Vec<u8>>,
    backup_path: Option<String>,
}

struct PreparedMaterializeRevert {
    path: String,
    target: PathBuf,
    current: Vec<u8>,
    restore: Option<Vec<u8>>,
}

/// Compile the compatibility plan without writing anything. The plan carries
/// per-target compatibility/degradation reports, the guidance-versus-
/// enforcement distinction, and per-path diffs against the live preimages.
pub fn compile_materialization(
    root: &Path,
    objects: &[MaterializeObject],
    targets: &[TargetPlatform],
) -> Result<MaterializePlan> {
    let root = canonical_root(root)?;
    if objects.is_empty() {
        bail!("materialization requires at least one object");
    }
    if targets.is_empty() {
        bail!("materialization requires at least one target platform");
    }
    let mut seen_objects = BTreeSet::new();
    for object in objects {
        if !seen_objects.insert(object.id()) {
            bail!("duplicate materialization object id {}", object.id());
        }
    }
    let mut ordered_targets = Vec::new();
    let mut seen_targets = BTreeSet::new();
    for target in targets {
        if seen_targets.insert(target.as_str()) {
            ordered_targets.push(*target);
        }
    }

    let mut path_plans: BTreeMap<String, PathPlan> = BTreeMap::new();
    let mut reports = Vec::new();
    for platform in &ordered_targets {
        let mut verdicts = Vec::new();
        for object in objects {
            let mut routing = route(object, *platform);
            let enforcement_label = if let Some(declared) = object.declared_enforcement() {
                if !matches!(routing.surface, SurfaceRoute::ReportOnly) {
                    routing.status = "degraded";
                }
                routing.degradations.push(format!(
                    "declared enforcement '{declared}' is not compiled; only guidance is \
                     materialized (documented enforcement surfaces: {})",
                    platform.enforcement_surfaces().join(", ")
                ));
                "not_compiled"
            } else {
                "none_declared"
            };
            let (target_path, guidance_label) = match &routing.surface {
                SurfaceRoute::ReportOnly => (None, "report_only"),
                SurfaceRoute::File { path }
                | SurfaceRoute::Region { path }
                | SurfaceRoute::AgentMerge { path } => (Some(path.clone()), "materialized"),
            };
            fold_surface(&mut path_plans, object, *platform, &routing.surface)?;
            let record_path = target_path
                .clone()
                .unwrap_or_else(|| format!("report-only:{}", platform.as_str()));
            verdicts.push(ObjectVerdict {
                object_id: object.id(),
                kind: object.kind().as_str().to_string(),
                name: object.name().to_string(),
                revision: object.revision().to_string(),
                status: routing.status.to_string(),
                mechanism: routing.mechanism.to_string(),
                target_path,
                guidance: guidance_label.to_string(),
                enforcement: enforcement_label.to_string(),
                degradations: routing.degradations,
                idempotency_key: record_idempotency_key(object, *platform, &record_path)?,
            });
        }
        reports.push(TargetReport {
            platform: platform.as_str().to_string(),
            write_mode: if *platform == TargetPlatform::Cursor {
                "report_only"
            } else {
                "files"
            }
            .to_string(),
            guidance_surfaces: platform
                .guidance_surfaces()
                .iter()
                .map(|surface| (*surface).to_string())
                .collect(),
            enforcement_surfaces: platform
                .enforcement_surfaces()
                .iter()
                .map(|surface| (*surface).to_string())
                .collect(),
            objects: verdicts,
        });
    }

    let mut actions = Vec::new();
    for (path, plan) in path_plans {
        let target = admit(&root, &path)?;
        let existing = read_optional(&target)?;
        let mode = plan
            .mode
            .context("materialization path lost its surface mode")?;
        let rendered = match mode {
            "file" => {
                let (object_id, rendered) = plan
                    .file
                    .context("file surface lost its rendered content")?;
                if let Some(existing) = &existing {
                    ensure_file_overwrite_is_materialized(existing, object_id, &path)?;
                }
                rendered.into_bytes()
            }
            "region" => render_regions(existing.as_deref(), &plan.blocks)?,
            "json_merge" => render_agent_merge(existing.as_deref(), &plan.agents)?,
            other => bail!("unsupported materialization surface mode: {other}"),
        };
        let preimage_sha256 = existing.as_deref().map(sha256_hex);
        let rendered_sha256 = sha256_hex(&rendered);
        let changed = existing.as_deref() != Some(rendered.as_slice());
        let diff = if changed {
            unified_diff(
                &path,
                &String::from_utf8_lossy(existing.as_deref().unwrap_or_default()),
                &String::from_utf8_lossy(&rendered),
            )
        } else {
            String::new()
        };
        actions.push(MaterializeAction {
            path,
            mode: mode.to_string(),
            platforms: plan
                .platforms
                .iter()
                .map(|platform| (*platform).to_string())
                .collect(),
            would_create: existing.is_none(),
            changed,
            preimage_sha256,
            rendered_sha256,
            diff,
            rendered,
        });
    }

    Ok(MaterializePlan {
        schema_version: PLAN_SCHEMA_VERSION.to_string(),
        workspace: root.to_string_lossy().to_string(),
        targets: reports,
        actions,
        root_tool_inflation: false,
        public_tool_ceiling: 12,
    })
}

/// Compile and apply in one transaction: persist every preimage backup before
/// the first write, apply each path atomically, roll every applied path back
/// on any failure, and re-read every written path to prove the native load
/// digest matches before the backup manifest is committed.
pub fn apply_materialization(
    root: &Path,
    objects: &[MaterializeObject],
    targets: &[TargetPlatform],
) -> Result<(MaterializePlan, MaterializeReceipt)> {
    let mut writer = |path: &Path, bytes: &[u8]| atomic_write(path, bytes);
    apply_materialization_with_writer(root, objects, targets, &mut writer)
}

fn apply_materialization_with_writer<F>(
    root: &Path,
    objects: &[MaterializeObject],
    targets: &[TargetPlatform],
    writer: &mut F,
) -> Result<(MaterializePlan, MaterializeReceipt)>
where
    F: FnMut(&Path, &[u8]) -> Result<()>,
{
    let root = canonical_root(root)?;
    let plan = compile_materialization(&root, objects, targets)?;

    let mut verification = Vec::new();
    let mut unchanged = Vec::new();
    for action in plan.actions.iter().filter(|action| !action.changed) {
        unchanged.push(action.path.clone());
        verification.push(MaterializeVerification {
            path: action.path.clone(),
            expected_sha256: action.rendered_sha256.clone(),
            observed_sha256: Some(action.rendered_sha256.clone()),
            verified: true,
        });
    }
    let changed: Vec<&MaterializeAction> = plan
        .actions
        .iter()
        .filter(|action| action.changed)
        .collect();
    if changed.is_empty() {
        let receipt = MaterializeReceipt {
            schema_version: RECEIPT_SCHEMA_VERSION.to_string(),
            workspace: plan.workspace.clone(),
            manifest_path: None,
            written: Vec::new(),
            unchanged,
            backups: Vec::new(),
            verification,
            load_verified: true,
            root_tool_inflation: false,
        };
        return Ok((plan, receipt));
    }

    let timestamp = unix_ms();
    let mut writes = Vec::new();
    for action in &changed {
        let target = admit(&root, &action.path)?;
        let existing = read_optional(&target)?;
        if existing.as_deref().map(sha256_hex) != action.preimage_sha256 {
            bail!(
                "materialization preimage changed after planning for {}",
                action.path
            );
        }
        let backup_path = existing
            .as_ref()
            .map(|_| format!(".soleaux/backups/materialize-{timestamp}/{}", action.path));
        if let Some(backup) = &backup_path {
            let _ = admit(&root, backup)?;
        }
        writes.push(PreparedMaterializeWrite {
            action,
            target,
            existing,
            backup_path,
        });
    }

    let backup_root = root.join(format!(".soleaux/backups/materialize-{timestamp}"));
    let mut backups = Vec::new();
    if let Err(failure) = persist_materialize_backups(&root, &backup_root, &writes, &mut backups) {
        let cleanup = cleanup_backup_root(&backup_root);
        return Err(transaction_error(
            "persisting materializer backups",
            failure,
            cleanup,
        ));
    }

    let mut applied: Vec<&PreparedMaterializeWrite<'_>> = Vec::new();
    for write in &writes {
        if let Err(failure) = writer(&write.target, &write.action.rendered) {
            let rollback = rollback_materialize(&applied, &backup_root);
            return Err(transaction_error(
                "applying materialization plan",
                failure,
                rollback,
            ));
        }
        applied.push(write);
    }

    // Native load verification: every written path is re-read and its digest
    // must match the rendered content exactly, or the whole apply rolls back.
    for write in &writes {
        let observed = match read_optional(&write.target) {
            Ok(bytes) => bytes.map(|bytes| sha256_hex(&bytes)),
            Err(failure) => {
                let rollback = rollback_materialize(&applied, &backup_root);
                return Err(transaction_error(
                    "native load verification",
                    failure,
                    rollback,
                ));
            }
        };
        if observed.as_deref() != Some(write.action.rendered_sha256.as_str()) {
            let failure = anyhow::anyhow!(
                "native load verification failed for {}: expected {}, observed {}",
                write.action.path,
                write.action.rendered_sha256,
                observed.as_deref().unwrap_or("a missing file")
            );
            let rollback = rollback_materialize(&applied, &backup_root);
            return Err(transaction_error(
                "native load verification",
                failure,
                rollback,
            ));
        }
        verification.push(MaterializeVerification {
            path: write.action.path.clone(),
            expected_sha256: write.action.rendered_sha256.clone(),
            observed_sha256: observed,
            verified: true,
        });
    }

    let manifest = MaterializerManifest {
        schema_version: MANIFEST_SCHEMA_VERSION.to_string(),
        workspace: root.to_string_lossy().to_string(),
        created_unix_ms: timestamp,
        records: writes
            .iter()
            .map(|write| MaterializerBackupRecord {
                path: write.action.path.clone(),
                backup_path: write.backup_path.clone(),
                created: write.existing.is_none(),
                preimage_sha256: write.action.preimage_sha256.clone(),
                applied_sha256: write.action.rendered_sha256.clone(),
            })
            .collect(),
    };
    let manifest_path = root.join(MANIFEST_RELATIVE);
    if let Err(failure) = writer(&manifest_path, &serde_json::to_vec_pretty(&manifest)?) {
        let rollback = rollback_materialize(&applied, &backup_root);
        return Err(transaction_error(
            "persisting materializer manifest",
            failure,
            rollback,
        ));
    }

    let receipt = MaterializeReceipt {
        schema_version: RECEIPT_SCHEMA_VERSION.to_string(),
        workspace: plan.workspace.clone(),
        manifest_path: Some(manifest_path.to_string_lossy().to_string()),
        written: writes
            .iter()
            .map(|write| write.action.path.clone())
            .collect(),
        unchanged,
        backups,
        verification,
        load_verified: true,
        root_tool_inflation: false,
    };
    Ok((plan, receipt))
}

/// Re-read every path recorded in the last materialization manifest and
/// report whether it still matches its applied digest. A hand-tampered or
/// deleted materialized file is reported as unverified.
pub fn verify_materialization(root: &Path) -> Result<Vec<MaterializeVerification>> {
    let root = canonical_root(root)?;
    let manifest = load_manifest(&root)?;
    let mut results = Vec::new();
    for record in &manifest.records {
        let target = admit(&root, &record.path)?;
        let observed = read_optional(&target)?.map(|bytes| sha256_hex(&bytes));
        let verified = observed.as_deref() == Some(record.applied_sha256.as_str());
        results.push(MaterializeVerification {
            path: record.path.clone(),
            expected_sha256: record.applied_sha256.clone(),
            observed_sha256: observed,
            verified,
        });
    }
    Ok(results)
}

/// Revert the last applied materialization. Every target is prevalidated
/// against its applied digest before any path changes, so a hand-tampered
/// materialized file fails the whole revert closed.
pub fn revert_last_materialization(root: &Path) -> Result<Vec<String>> {
    let root = canonical_root(root)?;
    let mut writer = |path: &Path, bytes: &[u8]| atomic_write(path, bytes);
    revert_last_materialization_with_writer(&root, &mut writer)
}

fn revert_last_materialization_with_writer<F>(root: &Path, writer: &mut F) -> Result<Vec<String>>
where
    F: FnMut(&Path, &[u8]) -> Result<()>,
{
    let manifest = load_manifest(root)?;
    let mut prepared = Vec::new();
    for record in manifest.records.iter().rev() {
        let target = admit(root, &record.path)?;
        let current = read_optional(&target)?
            .with_context(|| format!("materialized target {} is missing", target.display()))?;
        if sha256_hex(&current) != record.applied_sha256 {
            bail!(
                "refusing to overwrite locally modified materialized file {}",
                record.path
            );
        }
        let restore = if record.created {
            None
        } else {
            let backup = record
                .backup_path
                .as_deref()
                .context("existing materialized file omitted its backup path")?;
            Some(
                fs::read(admit(root, backup)?)
                    .with_context(|| format!("reading materializer backup {backup}"))?,
            )
        };
        prepared.push(PreparedMaterializeRevert {
            path: record.path.clone(),
            target,
            current,
            restore,
        });
    }

    let mut changed: Vec<&PreparedMaterializeRevert> = Vec::new();
    for entry in &prepared {
        let result = match &entry.restore {
            Some(bytes) => writer(&entry.target, bytes),
            None => fs::remove_file(&entry.target)
                .with_context(|| format!("removing materialized file {}", entry.target.display())),
        };
        if let Err(failure) = result {
            let rollback = rollback_materialize_reverts(&changed);
            return Err(transaction_error(
                "materialization revert",
                failure,
                rollback,
            ));
        }
        changed.push(entry);
    }

    let receipt_path = root.join(REVERT_RECEIPT_RELATIVE);
    let receipt = json!({
        "schema_version": REVERT_SCHEMA_VERSION,
        "workspace": root,
        "restored": prepared.iter().map(|entry| entry.path.clone()).collect::<Vec<_>>(),
        "created_unix_ms": unix_ms(),
    });
    if let Err(failure) = writer(&receipt_path, &serde_json::to_vec_pretty(&receipt)?) {
        let rollback = rollback_materialize_reverts(&changed);
        return Err(transaction_error(
            "persisting materialization revert receipt",
            failure,
            rollback,
        ));
    }
    Ok(prepared.into_iter().map(|entry| entry.path).collect())
}

/// Persist one `MaterializationPayload` record per object/target verdict and
/// link each canonical object to its record with `RelationshipKind::Materializes`.
/// Results are truthful per record: a missing canonical object leaves the
/// record persisted and the link reported as failed.
pub fn persist_materialization_records(
    state: &StateStore,
    workspace_id: Option<Uuid>,
    plan: &MaterializePlan,
    receipt: &MaterializeReceipt,
) -> Vec<PersistedMaterializationRecord> {
    let rendered_by_path: BTreeMap<&str, &str> = plan
        .actions
        .iter()
        .map(|action| (action.path.as_str(), action.rendered_sha256.as_str()))
        .collect();
    let mut results = Vec::new();
    for target in &plan.targets {
        for verdict in &target.objects {
            let (target_path, state_label) = match &verdict.target_path {
                Some(path) => (path.clone(), "materialized"),
                None => (format!("report-only:{}", target.platform), "degraded"),
            };
            let rendered_sha256 = verdict
                .target_path
                .as_deref()
                .and_then(|path| rendered_by_path.get(path).copied());
            let payload = MaterializationPayload {
                object_id: verdict.object_id,
                target_platform: target.platform.clone(),
                target_path,
                object_revision: verdict.revision.clone(),
                origin: MATERIALIZER_ORIGIN.to_string(),
                idempotency_key: verdict.idempotency_key.clone(),
                materialization_state: state_label.to_string(),
                report: serde_json::to_value(verdict).unwrap_or_else(|_| json!({})),
                metadata: json!({
                    "workspace": plan.workspace,
                    "rendered_sha256": rendered_sha256,
                    "load_verified": receipt.load_verified,
                }),
            };
            let mut input = CanonicalEntityInput::active(payload);
            input.workspace_id = workspace_id;
            input.idempotency_key = Some(verdict.idempotency_key.clone());
            let mut result = PersistedMaterializationRecord {
                object_id: verdict.object_id,
                target_platform: target.platform.clone(),
                materialization_id: None,
                materialization_state: state_label.to_string(),
                linked: false,
                error: None,
            };
            match state.put(input) {
                Ok(record) => {
                    result.materialization_id = Some(record.id);
                    match state.link(EntityLinkInput {
                        source_id: verdict.object_id,
                        relationship: RelationshipKind::Materializes,
                        target_id: record.id,
                        metadata: json!({
                            "target_platform": target.platform,
                            "target_path": record.payload.target_path,
                        }),
                    }) {
                        Ok(_) => result.linked = true,
                        Err(error) => {
                            result.error = Some(format!("materializes link failed: {error:#}"));
                        }
                    }
                }
                Err(error) => {
                    result.error = Some(format!("materialization record failed: {error:#}"));
                }
            }
            results.push(result);
        }
    }
    results
}

/// Echo prevention for the registry scan. Returns the source-object view of a
/// classified file: the untouched content when no materializer markers are
/// present, the content with materialized regions stripped, or `None` when
/// the file is materializer output (or carries a malformed marker) and must
/// not re-enter the registry as a source object.
pub(crate) fn registry_source_view(content: &str) -> Option<Cow<'_, str>> {
    if !content.contains(BEGIN_MARKER_PREFIX) {
        return Some(Cow::Borrowed(content));
    }
    let mut kept: Vec<&str> = Vec::new();
    let mut inside_region = false;
    let mut stripped = false;
    for line in content.lines() {
        let trimmed = line.trim_start();
        if inside_region {
            if parse_end_marker(trimmed).is_some() {
                inside_region = false;
            }
            continue;
        }
        if let Some(marker) = parse_begin_marker(trimmed) {
            if marker.mode != "region" {
                return None;
            }
            inside_region = true;
            stripped = true;
            continue;
        }
        kept.push(line);
    }
    if inside_region {
        return None;
    }
    if !stripped {
        return Some(Cow::Borrowed(content));
    }
    if kept.iter().all(|line| line.trim().is_empty()) {
        return None;
    }
    Some(Cow::Owned(kept.join("\n")))
}

fn route(object: &MaterializeObject, platform: TargetPlatform) -> Routing {
    let slug = object.slug();
    match platform {
        TargetPlatform::ClaudeCode => match object {
            MaterializeObject::Rule { payload, .. } => {
                if payload.scope.trim().eq_ignore_ascii_case("memory") {
                    Routing {
                        surface: SurfaceRoute::Region {
                            path: "CLAUDE.md".to_string(),
                        },
                        status: "supported",
                        mechanism: "claude-md-memory",
                        degradations: Vec::new(),
                    }
                } else {
                    Routing {
                        surface: SurfaceRoute::File {
                            path: format!(".claude/rules/soleaux-{slug}.md"),
                        },
                        status: "supported",
                        mechanism: "project-rules",
                        degradations: Vec::new(),
                    }
                }
            }
            MaterializeObject::Skill { .. } => Routing {
                surface: SurfaceRoute::File {
                    path: format!(".claude/skills/soleaux-{slug}/SKILL.md"),
                },
                status: "supported",
                mechanism: "project-skill",
                degradations: Vec::new(),
            },
            MaterializeObject::Agent { .. } => Routing {
                surface: SurfaceRoute::File {
                    path: format!(".claude/agents/soleaux-{slug}.md"),
                },
                status: "supported",
                mechanism: "project-subagent",
                degradations: Vec::new(),
            },
        },
        TargetPlatform::Codex => match object {
            MaterializeObject::Rule { .. } => Routing {
                surface: SurfaceRoute::Region {
                    path: "AGENTS.md".to_string(),
                },
                status: "supported",
                mechanism: "agents-md-guidance",
                degradations: Vec::new(),
            },
            MaterializeObject::Skill { .. } => Routing {
                surface: SurfaceRoute::Region {
                    path: "AGENTS.md".to_string(),
                },
                status: "degraded",
                mechanism: "agents-md-guidance",
                degradations: vec![
                    "Codex has no verified project skill file surface in the pinned capability \
                     matrix; degraded to AGENTS.md guidance"
                        .to_string(),
                ],
            },
            MaterializeObject::Agent { .. } => Routing {
                surface: SurfaceRoute::Region {
                    path: "AGENTS.md".to_string(),
                },
                status: "degraded",
                mechanism: "agents-md-guidance",
                degradations: vec![
                    "Codex has no verified project agent file surface in the pinned capability \
                     matrix; degraded to AGENTS.md guidance"
                        .to_string(),
                ],
            },
        },
        TargetPlatform::OpenCode => match object {
            MaterializeObject::Rule { .. } => Routing {
                surface: SurfaceRoute::Region {
                    path: "AGENTS.md".to_string(),
                },
                status: "supported",
                mechanism: "agents-md-guidance",
                degradations: Vec::new(),
            },
            MaterializeObject::Skill { .. } => Routing {
                surface: SurfaceRoute::Region {
                    path: "AGENTS.md".to_string(),
                },
                status: "degraded",
                mechanism: "agents-md-guidance",
                degradations: vec![
                    "OpenCode documents no project skill surface; degraded to AGENTS.md guidance"
                        .to_string(),
                ],
            },
            MaterializeObject::Agent { .. } => Routing {
                surface: SurfaceRoute::AgentMerge {
                    path: "opencode.json".to_string(),
                },
                status: "supported",
                mechanism: "opencode-config-agent",
                degradations: Vec::new(),
            },
        },
        TargetPlatform::Cursor => Routing {
            surface: SurfaceRoute::ReportOnly,
            status: "degraded",
            mechanism: "documentation-only",
            degradations: vec![
                "Cursor is a documentation-only supported surface in the client capability \
                 matrix; no write is compiled"
                    .to_string(),
            ],
        },
    }
}

fn fold_surface(
    path_plans: &mut BTreeMap<String, PathPlan>,
    object: &MaterializeObject,
    platform: TargetPlatform,
    surface: &SurfaceRoute,
) -> Result<()> {
    match surface {
        SurfaceRoute::ReportOnly => Ok(()),
        SurfaceRoute::File { path } => {
            let plan = path_plans.entry(path.clone()).or_default();
            ensure_surface_mode(plan, "file", path)?;
            if let Some((existing_object, _)) = &plan.file {
                if *existing_object != object.id() {
                    bail!(
                        "materialization path collision at {path}: objects {existing_object} \
                         and {} render the same file",
                        object.id()
                    );
                }
            } else {
                plan.file = Some((object.id(), render_file(object, path)?));
            }
            plan.platforms.insert(platform.as_str());
            Ok(())
        }
        SurfaceRoute::Region { path } => {
            let plan = path_plans.entry(path.clone()).or_default();
            ensure_surface_mode(plan, "region", path)?;
            plan.blocks
                .entry(object.id())
                .or_insert(region_block(object, path)?);
            plan.platforms.insert(platform.as_str());
            Ok(())
        }
        SurfaceRoute::AgentMerge { path } => {
            let plan = path_plans.entry(path.clone()).or_default();
            ensure_surface_mode(plan, "json_merge", path)?;
            let MaterializeObject::Agent { payload, .. } = object else {
                bail!("agent merge surface requires an agent object");
            };
            plan.agents.insert(
                format!("soleaux-{}", object.slug()),
                agent_merge_value(payload),
            );
            plan.platforms.insert(platform.as_str());
            Ok(())
        }
    }
}

fn ensure_surface_mode(plan: &mut PathPlan, mode: &'static str, path: &str) -> Result<()> {
    match plan.mode {
        None => {
            plan.mode = Some(mode);
            Ok(())
        }
        Some(existing) if existing == mode => Ok(()),
        Some(existing) => {
            bail!("materialization surface conflict at {path}: {existing} versus {mode}")
        }
    }
}

fn render_file(object: &MaterializeObject, path: &str) -> Result<String> {
    let begin = begin_marker(object, "file", path)?;
    let end = end_marker(object);
    Ok(match object {
        MaterializeObject::Rule { payload, .. } => format!(
            "{begin}\n\n# {}\n\n{}\n\n{end}\n",
            payload.name.trim(),
            payload.guidance.trim()
        ),
        MaterializeObject::Skill { payload, .. } => format!(
            "---\nname: soleaux-{}\ndescription: {}\n---\n\n{begin}\n\n{}\n\n{end}\n",
            object.slug(),
            yaml_scalar(&payload.description),
            payload.instructions.trim()
        ),
        MaterializeObject::Agent { payload, .. } => {
            let mut frontmatter = format!(
                "---\nname: soleaux-{}\ndescription: {}\n",
                object.slug(),
                yaml_scalar(&payload.description)
            );
            if !payload.allowed_tools.is_empty() {
                frontmatter.push_str(&format!(
                    "tools: {}\n",
                    yaml_scalar(&payload.allowed_tools.join(", "))
                ));
            }
            if let Some(model) = &payload.model_hint
                && !model.trim().is_empty()
            {
                frontmatter.push_str(&format!("model: {}\n", yaml_scalar(model)));
            }
            frontmatter.push_str("---\n");
            format!(
                "{frontmatter}\n{begin}\n\n{}\n\n{end}\n",
                payload.instructions.trim()
            )
        }
    })
}

fn region_block(object: &MaterializeObject, path: &str) -> Result<String> {
    let begin = begin_marker(object, "region", path)?;
    let end = end_marker(object);
    let heading = format!(
        "## {} (soleaux {})",
        object.name().trim(),
        object.kind().as_str()
    );
    let description = object.description().trim();
    let body = object.body().trim();
    let inner = if description.is_empty() {
        format!("{heading}\n\n{body}")
    } else {
        format!("{heading}\n\n{description}\n\n{body}")
    };
    Ok(format!("{begin}\n{inner}\n{end}"))
}

fn render_regions(existing: Option<&[u8]>, blocks: &BTreeMap<Uuid, String>) -> Result<Vec<u8>> {
    let mut text = match existing {
        Some(bytes) => std::str::from_utf8(bytes)
            .context("materialization region target is not UTF-8")?
            .to_string(),
        None => String::new(),
    };
    for (object_id, block) in blocks {
        text = upsert_or_append_region(&text, *object_id, block)?;
    }
    Ok(text.into_bytes())
}

fn upsert_or_append_region(text: &str, object_id: Uuid, block: &str) -> Result<String> {
    let identity = object_id.to_string();
    let lines: Vec<&str> = text.lines().collect();
    let mut begin_index = None;
    for (index, line) in lines.iter().enumerate() {
        if let Some(marker) = parse_begin_marker(line.trim_start())
            && marker.object.as_deref() == Some(identity.as_str())
        {
            begin_index = Some(index);
            break;
        }
    }
    let Some(begin) = begin_index else {
        return Ok(if text.trim().is_empty() {
            format!("{block}\n")
        } else {
            format!("{}\n\n{block}\n", text.trim_end())
        });
    };
    let mut end_index = None;
    for (index, line) in lines.iter().enumerate().skip(begin + 1) {
        if let Some(end_object) = parse_end_marker(line.trim_start())
            && (end_object.is_none() || end_object.as_deref() == Some(identity.as_str()))
        {
            end_index = Some(index);
            break;
        }
    }
    let end = end_index
        .with_context(|| format!("materialized region for object {identity} has no end marker"))?;
    let mut output: Vec<&str> = Vec::new();
    output.extend_from_slice(&lines[..begin]);
    output.extend(block.lines());
    output.extend_from_slice(&lines[end + 1..]);
    let mut joined = output.join("\n");
    if !joined.ends_with('\n') {
        joined.push('\n');
    }
    Ok(joined)
}

fn render_agent_merge(
    existing: Option<&[u8]>,
    agents: &BTreeMap<String, Value>,
) -> Result<Vec<u8>> {
    let mut value: Value = match existing {
        Some(bytes) => serde_json::from_slice(bytes)
            .context("materialization target opencode.json must contain valid JSON")?,
        None => json!({}),
    };
    let object = value
        .as_object_mut()
        .context("opencode.json root must be an object")?;
    let entry = object.entry("agent").or_insert_with(|| json!({}));
    let map = entry
        .as_object_mut()
        .context("opencode.json agent must be an object")?;
    for (name, agent) in agents {
        map.insert(name.clone(), agent.clone());
    }
    Ok(serde_json::to_vec_pretty(&value)?)
}

fn agent_merge_value(payload: &AgentPayload) -> Value {
    let mut entry = serde_json::Map::new();
    entry.insert("description".to_string(), json!(payload.description));
    entry.insert("prompt".to_string(), json!(payload.instructions));
    if let Some(model) = &payload.model_hint
        && !model.trim().is_empty()
    {
        entry.insert("model".to_string(), json!(model));
    }
    Value::Object(entry)
}

fn ensure_file_overwrite_is_materialized(
    existing: &[u8],
    object_id: Uuid,
    path: &str,
) -> Result<()> {
    let Ok(text) = std::str::from_utf8(existing) else {
        bail!("refusing to overwrite non-materialized file {path}");
    };
    let mut owner = None;
    for line in text.lines() {
        if let Some(marker) = parse_begin_marker(line.trim_start())
            && marker.mode == "file"
        {
            owner = marker.object;
            break;
        }
    }
    match owner {
        Some(owner) if owner == object_id.to_string() => Ok(()),
        Some(owner) => {
            bail!("materialization path collision at {path}: the file is owned by object {owner}")
        }
        None => bail!("refusing to overwrite non-materialized file {path}"),
    }
}

struct BeginMarker {
    mode: String,
    object: Option<String>,
}

fn parse_begin_marker(line: &str) -> Option<BeginMarker> {
    let rest = line.strip_prefix(BEGIN_MARKER_PREFIX)?;
    let rest = rest.trim_end().strip_suffix(MARKER_SUFFIX)?;
    let mut mode = String::new();
    let mut object = None;
    for token in rest.split_whitespace() {
        if let Some((key, value)) = token.split_once('=') {
            match key {
                "mode" => mode = value.to_string(),
                "object" => object = Some(value.to_string()),
                _ => {}
            }
        }
    }
    Some(BeginMarker { mode, object })
}

fn parse_end_marker(line: &str) -> Option<Option<String>> {
    let rest = line.strip_prefix(END_MARKER_PREFIX)?;
    let rest = rest.trim_end().strip_suffix(MARKER_SUFFIX)?;
    let mut object = None;
    for token in rest.split_whitespace() {
        if let Some((key, value)) = token.split_once('=')
            && key == "object"
        {
            object = Some(value.to_string());
        }
    }
    Some(object)
}

fn begin_marker(object: &MaterializeObject, mode: &str, path: &str) -> Result<String> {
    Ok(format!(
        "{BEGIN_MARKER_PREFIX} mode={mode} kind={} object={} revision={} origin={MATERIALIZER_ORIGIN} idempotency={} {MARKER_SUFFIX}",
        object.kind().as_str(),
        object.id(),
        marker_token(object.revision()),
        path_idempotency_key(object, path)?,
    ))
}

fn end_marker(object: &MaterializeObject) -> String {
    format!("{END_MARKER_PREFIX} object={} {MARKER_SUFFIX}", object.id())
}

/// Path-level idempotency for the marker: platform-independent because one
/// rendered path can serve several platforms (a shared `AGENTS.md`).
fn path_idempotency_key(object: &MaterializeObject, path: &str) -> Result<String> {
    Ok(sha256_hex(&serde_json::to_vec(&json!({
        "object_id": object.id(),
        "kind": object.kind().as_str(),
        "revision": object.revision(),
        "path": path,
        "body_sha256": sha256_hex(object.body().as_bytes()),
    }))?))
}

/// Record-level idempotency for canonical `MaterializationPayload` records:
/// one record per object, revision, platform, and target path.
fn record_idempotency_key(
    object: &MaterializeObject,
    platform: TargetPlatform,
    path: &str,
) -> Result<String> {
    Ok(sha256_hex(&serde_json::to_vec(&json!({
        "object_id": object.id(),
        "kind": object.kind().as_str(),
        "revision": object.revision(),
        "platform": platform.as_str(),
        "path": path,
        "body_sha256": sha256_hex(object.body().as_bytes()),
    }))?))
}

fn marker_token(value: &str) -> String {
    let mut token = String::new();
    for character in value.chars().take(120) {
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | ':' | '-') {
            token.push(character);
        } else {
            token.push('-');
        }
    }
    if token.is_empty() {
        token.push('-');
    }
    token
}

fn object_slug(name: &str) -> String {
    let mut slug = String::new();
    let mut previous_dash = true;
    for character in name.chars() {
        let lower = character.to_ascii_lowercase();
        if lower.is_ascii_alphanumeric() {
            slug.push(lower);
            previous_dash = false;
        } else if !previous_dash {
            slug.push('-');
            previous_dash = true;
        }
    }
    let slug = slug.trim_matches('-').to_string();
    if slug.is_empty() {
        "object".to_string()
    } else {
        slug
    }
}

fn yaml_scalar(value: &str) -> String {
    let single_line = value.replace(['\n', '\r'], " ");
    serde_json::to_string(&single_line).unwrap_or_else(|_| "\"\"".to_string())
}

fn unified_diff(path: &str, before: &str, after: &str) -> String {
    if before == after {
        return String::new();
    }
    let before_lines: Vec<&str> = if before.is_empty() {
        Vec::new()
    } else {
        before.split('\n').collect()
    };
    let after_lines: Vec<&str> = if after.is_empty() {
        Vec::new()
    } else {
        after.split('\n').collect()
    };
    let mut prefix = 0usize;
    while prefix < before_lines.len()
        && prefix < after_lines.len()
        && before_lines[prefix] == after_lines[prefix]
    {
        prefix += 1;
    }
    let mut suffix = 0usize;
    while suffix < before_lines.len().saturating_sub(prefix)
        && suffix < after_lines.len().saturating_sub(prefix)
        && before_lines[before_lines.len() - 1 - suffix]
            == after_lines[after_lines.len() - 1 - suffix]
    {
        suffix += 1;
    }
    let removed = &before_lines[prefix..before_lines.len() - suffix];
    let added = &after_lines[prefix..after_lines.len() - suffix];
    let mut output = format!(
        "--- a/{path}\n+++ b/{path}\n@@ -{},{} +{},{} @@\n",
        if removed.is_empty() {
            prefix
        } else {
            prefix + 1
        },
        removed.len(),
        if added.is_empty() { prefix } else { prefix + 1 },
        added.len(),
    );
    for line in removed {
        output.push('-');
        output.push_str(line);
        output.push('\n');
    }
    for line in added {
        output.push('+');
        output.push_str(line);
        output.push('\n');
    }
    if output.len() > MAX_DIFF_BYTES {
        let mut end = MAX_DIFF_BYTES;
        while !output.is_char_boundary(end) {
            end -= 1;
        }
        output.truncate(end);
        output.push_str("\n... diff truncated ...\n");
    }
    output
}

fn persist_materialize_backups(
    root: &Path,
    backup_root: &Path,
    writes: &[PreparedMaterializeWrite<'_>],
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
            .context("existing materialization target omitted its backup path")?;
        let backup = admit(root, backup_path)?;
        atomic_write(&backup, existing)?;
        backups.push(backup.to_string_lossy().to_string());
    }
    Ok(())
}

fn rollback_materialize(
    applied: &[&PreparedMaterializeWrite<'_>],
    backup_root: &Path,
) -> Result<()> {
    let mut errors = Vec::new();
    for write in applied.iter().rev() {
        let result = match &write.existing {
            Some(bytes) => atomic_write(&write.target, bytes),
            None if write.target.exists() => fs::remove_file(&write.target)
                .with_context(|| format!("removing {}", write.target.display())),
            None => Ok(()),
        };
        if let Err(error) = result {
            errors.push(format!(
                "restoring {} failed: {error:#}",
                write.target.display()
            ));
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

fn rollback_materialize_reverts(changed: &[&PreparedMaterializeRevert]) -> Result<()> {
    let mut errors = Vec::new();
    for entry in changed.iter().rev() {
        if let Err(error) = atomic_write(&entry.target, &entry.current) {
            errors.push(format!(
                "restoring {} failed: {error:#}",
                entry.target.display()
            ));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        bail!(errors.join("; "))
    }
}

fn load_manifest(root: &Path) -> Result<MaterializerManifest> {
    let manifest_path = root.join(MANIFEST_RELATIVE);
    let manifest: MaterializerManifest =
        serde_json::from_slice(&fs::read(&manifest_path).with_context(|| {
            format!("reading materializer manifest {}", manifest_path.display())
        })?)?;
    if Path::new(&manifest.workspace) != root {
        bail!("materializer manifest belongs to a different workspace");
    }
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry::scan_registry;
    use soleaux_intelligence::index::{IndexConfig, RepositoryIndex};
    use soleaux_storage::Store;
    use std::cell::Cell;
    use tempfile::tempdir;

    fn rule_object(id: Uuid, name: &str, scope: &str, enforcement: &str) -> MaterializeObject {
        MaterializeObject::Rule {
            id,
            payload: RulePayload {
                name: name.to_string(),
                scope: scope.to_string(),
                guidance: "Always compile context before editing.".to_string(),
                enforcement: enforcement.to_string(),
                object_revision: "1".to_string(),
                metadata: json!({}),
            },
        }
    }

    fn skill_object(id: Uuid, name: &str) -> MaterializeObject {
        MaterializeObject::Skill {
            id,
            payload: SkillPayload {
                name: name.to_string(),
                description: "Deploy helper".to_string(),
                instructions: "Run the deploy pipeline end to end.".to_string(),
                object_revision: "1".to_string(),
                compatibility: json!({}),
                metadata: json!({}),
            },
        }
    }

    fn agent_object(id: Uuid, name: &str) -> MaterializeObject {
        MaterializeObject::Agent {
            id,
            payload: AgentPayload {
                name: name.to_string(),
                description: "Reviews pull requests".to_string(),
                instructions: "Review every change for defects.".to_string(),
                object_revision: "1".to_string(),
                model_hint: None,
                allowed_tools: Vec::new(),
                compatibility: json!({}),
                metadata: json!({}),
            },
        }
    }

    fn fixture_objects() -> Vec<MaterializeObject> {
        vec![
            rule_object(Uuid::now_v7(), "Context First", "project", ""),
            skill_object(Uuid::now_v7(), "Deploy"),
            agent_object(Uuid::now_v7(), "Reviewer"),
        ]
    }

    #[test]
    fn compile_reports_compatibility_degradation_and_diffs_per_target() {
        let directory = tempdir().expect("tempdir");
        fs::write(directory.path().join("AGENTS.md"), "# Existing guidance\n").expect("seed");
        let objects = vec![
            rule_object(
                Uuid::now_v7(),
                "Context First",
                "project",
                "pre-commit hook",
            ),
            skill_object(Uuid::now_v7(), "Deploy"),
            agent_object(Uuid::now_v7(), "Reviewer"),
        ];
        let plan = compile_materialization(directory.path(), &objects, &TargetPlatform::ALL)
            .expect("plan");
        assert!(!plan.root_tool_inflation);
        assert_eq!(plan.public_tool_ceiling, 12);
        assert_eq!(plan.targets.len(), 4);

        let cursor = plan
            .targets
            .iter()
            .find(|target| target.platform == "cursor")
            .expect("cursor report");
        assert_eq!(cursor.write_mode, "report_only");
        assert!(cursor.objects.iter().all(|verdict| {
            verdict.status == "degraded"
                && verdict.target_path.is_none()
                && verdict.guidance == "report_only"
        }));

        let codex = plan
            .targets
            .iter()
            .find(|target| target.platform == "codex")
            .expect("codex report");
        let codex_skill = codex
            .objects
            .iter()
            .find(|verdict| verdict.kind == "skill")
            .expect("codex skill verdict");
        assert_eq!(codex_skill.status, "degraded");
        assert_eq!(codex_skill.target_path.as_deref(), Some("AGENTS.md"));

        let claude = plan
            .targets
            .iter()
            .find(|target| target.platform == "claude-code")
            .expect("claude report");
        let claude_rule = claude
            .objects
            .iter()
            .find(|verdict| verdict.kind == "rule")
            .expect("claude rule verdict");
        assert_eq!(
            claude_rule.target_path.as_deref(),
            Some(".claude/rules/soleaux-context-first.md")
        );
        assert_eq!(claude_rule.enforcement, "not_compiled");
        assert_eq!(claude_rule.status, "degraded");
        assert!(
            claude
                .enforcement_surfaces
                .iter()
                .any(|surface| surface.contains("hooks"))
        );

        let rule_action = plan
            .actions
            .iter()
            .find(|action| action.path == ".claude/rules/soleaux-context-first.md")
            .expect("rule action");
        assert!(rule_action.would_create);
        assert!(rule_action.changed);
        assert!(rule_action.diff.contains("+# Context First"));
        assert!(
            plan.actions
                .iter()
                .any(|action| action.path == "opencode.json" && action.mode == "json_merge")
        );
        assert!(
            plan.actions
                .iter()
                .all(|action| !action.platforms.contains(&"cursor".to_string()))
        );
        let agents_action = plan
            .actions
            .iter()
            .find(|action| action.path == "AGENTS.md")
            .expect("AGENTS.md action");
        assert!(!agents_action.would_create);
        assert!(agents_action.platforms.contains(&"codex".to_string()));
        assert!(agents_action.platforms.contains(&"opencode".to_string()));
    }

    #[test]
    fn compile_refuses_to_overwrite_a_non_materialized_file() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".claude/rules")).expect("rules dir");
        fs::write(
            directory
                .path()
                .join(".claude/rules/soleaux-context-first.md"),
            "user-authored rule\n",
        )
        .expect("user file");
        let objects = vec![rule_object(Uuid::now_v7(), "Context First", "project", "")];
        let error =
            compile_materialization(directory.path(), &objects, &[TargetPlatform::ClaudeCode])
                .expect_err("must refuse");
        assert!(error.to_string().contains("refusing to overwrite"));
    }

    #[test]
    fn registry_source_view_excludes_materialized_content() {
        let object = rule_object(Uuid::now_v7(), "Context First", "project", "");
        let file = render_file(&object, ".claude/rules/soleaux-context-first.md").expect("file");
        assert!(registry_source_view(&file).is_none());

        let block = region_block(&object, "AGENTS.md").expect("block");
        let shared = format!("# User guidance\n\n{block}\n");
        let view = registry_source_view(&shared).expect("source view");
        assert!(view.contains("# User guidance"));
        assert!(!view.contains("soleaux:materialized"));
        assert!(!view.contains("Always compile context"));
        assert!(matches!(view, Cow::Owned(_)));

        let only_block = format!("{block}\n");
        assert!(registry_source_view(&only_block).is_none());

        let unterminated = format!(
            "# User guidance\n\n{}\ncontent without an end marker\n",
            block.lines().next().expect("begin line")
        );
        assert!(registry_source_view(&unterminated).is_none());

        let plain = "# Plain guidance\n";
        assert!(matches!(
            registry_source_view(plain),
            Some(Cow::Borrowed(_))
        ));
    }

    #[tokio::test]
    async fn materialize_compile_diff_backup_apply_verify_tamper_echo_rollback_round_trip() {
        let directory = tempdir().expect("tempdir");
        let root = directory.path();
        let seed = "# Existing project guidance\n";
        fs::write(root.join("AGENTS.md"), seed).expect("seed");
        let objects = fixture_objects();
        let targets = [
            TargetPlatform::ClaudeCode,
            TargetPlatform::Codex,
            TargetPlatform::OpenCode,
        ];

        let (plan, receipt) = apply_materialization(root, &objects, &targets).expect("apply");
        assert!(receipt.load_verified);
        assert!(receipt.manifest_path.is_some());
        assert_eq!(receipt.written.len(), 5);
        for path in [
            ".claude/rules/soleaux-context-first.md",
            ".claude/skills/soleaux-deploy/SKILL.md",
            ".claude/agents/soleaux-reviewer.md",
            "AGENTS.md",
            "opencode.json",
        ] {
            assert!(receipt.written.contains(&path.to_string()), "{path}");
            assert!(root.join(path).is_file(), "{path}");
        }
        assert!(receipt.verification.iter().all(|entry| entry.verified));
        assert_eq!(plan.actions.len(), 5);

        let agents = fs::read_to_string(root.join("AGENTS.md")).expect("agents");
        assert!(agents.contains("# Existing project guidance"));
        assert!(agents.contains("soleaux:materialized:begin"));
        let opencode: Value =
            serde_json::from_slice(&fs::read(root.join("opencode.json")).expect("opencode"))
                .expect("json");
        assert!(opencode["agent"]["soleaux-reviewer"]["prompt"].is_string());

        // Echo prevention: materialized output never re-enters the registry
        // scan as a source object.
        let store = Store::open(root.join("registry-index.sqlite3")).expect("store");
        let index = RepositoryIndex::open(root, store, IndexConfig::default()).expect("index");
        index.refresh().await.expect("refresh");
        let snapshot = scan_registry(root, &index).expect("registry");
        assert!(snapshot.entries.iter().all(|entry| {
            entry
                .path
                .as_deref()
                .is_none_or(|path| !path.contains("soleaux-"))
        }));
        let agents_entry = snapshot
            .entries
            .iter()
            .find(|entry| entry.path.as_deref() == Some("AGENTS.md"))
            .expect("AGENTS.md stays a source object");
        let content = agents_entry.content.as_deref().expect("content");
        assert!(content.contains("# Existing project guidance"));
        assert!(!content.contains("soleaux:materialized"));
        assert!(!content.contains("Deploy helper"));
        assert_eq!(
            agents_entry.metadata["materialized_regions_excluded"],
            json!(true)
        );

        // Idempotency: a second apply changes nothing.
        let (_, second) = apply_materialization(root, &objects, &targets).expect("re-apply");
        assert!(second.written.is_empty());
        assert_eq!(second.unchanged.len(), 5);
        assert!(second.manifest_path.is_none());
        assert!(second.load_verified);

        // Hand-tamper detection.
        let rule_path = root.join(".claude/rules/soleaux-context-first.md");
        let applied_rule = fs::read(&rule_path).expect("applied rule");
        let mut tampered = applied_rule.clone();
        tampered.extend_from_slice(b"\nhand tampered\n");
        fs::write(&rule_path, &tampered).expect("tamper");
        let verification = verify_materialization(root).expect("verify");
        let rule_verification = verification
            .iter()
            .find(|entry| entry.path == ".claude/rules/soleaux-context-first.md")
            .expect("rule verification");
        assert!(!rule_verification.verified);
        assert!(
            verification
                .iter()
                .filter(|entry| entry.path != ".claude/rules/soleaux-context-first.md")
                .all(|entry| entry.verified)
        );

        // Rollback fails closed while the tamper persists.
        let error = revert_last_materialization(root).expect_err("revert must fail closed");
        assert!(error.to_string().contains("locally modified"));
        assert!(root.join("opencode.json").is_file());

        // Restore the applied content, then the revert restores the preimages.
        fs::write(&rule_path, &applied_rule).expect("restore applied content");
        let restored = revert_last_materialization(root).expect("revert");
        assert_eq!(restored.len(), 5);
        assert_eq!(
            fs::read_to_string(root.join("AGENTS.md")).expect("agents"),
            seed
        );
        assert!(!rule_path.exists());
        assert!(!root.join(".claude/skills/soleaux-deploy/SKILL.md").exists());
        assert!(!root.join(".claude/agents/soleaux-reviewer.md").exists());
        assert!(!root.join("opencode.json").exists());
    }

    #[test]
    fn apply_rolls_back_every_path_when_a_late_write_fails() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        let seed = "# Existing project guidance\n";
        fs::write(root.join("AGENTS.md"), seed).expect("seed");
        let objects = fixture_objects();
        let targets = [
            TargetPlatform::ClaudeCode,
            TargetPlatform::Codex,
            TargetPlatform::OpenCode,
        ];
        let failure_path = root.join("opencode.json");
        let failed = Cell::new(false);
        let mut writer = |path: &Path, bytes: &[u8]| -> Result<()> {
            if path == failure_path && !failed.replace(true) {
                bail!("injected late materialization write failure");
            }
            atomic_write(path, bytes)
        };
        let error = apply_materialization_with_writer(&root, &objects, &targets, &mut writer)
            .expect_err("transaction must fail");
        assert!(
            error
                .to_string()
                .contains("all changed paths were restored")
        );
        assert_eq!(
            fs::read_to_string(root.join("AGENTS.md")).expect("agents"),
            seed
        );
        for path in [
            ".claude/rules/soleaux-context-first.md",
            ".claude/skills/soleaux-deploy/SKILL.md",
            ".claude/agents/soleaux-reviewer.md",
            "opencode.json",
        ] {
            assert!(!root.join(path).exists(), "{path} should be rolled back");
        }
        assert!(!root.join(MANIFEST_RELATIVE).exists());
        let leftover_backups = fs::read_dir(root.join(".soleaux/backups"))
            .map(|entries| entries.count())
            .unwrap_or(0);
        assert_eq!(leftover_backups, 0);
    }

    #[test]
    fn apply_rolls_back_when_native_load_verification_fails() {
        let directory = tempdir().expect("tempdir");
        let root = canonical_root(directory.path()).expect("root");
        let seed = "# Existing project guidance\n";
        fs::write(root.join("AGENTS.md"), seed).expect("seed");
        let objects = fixture_objects();
        let targets = [TargetPlatform::Codex];
        let corrupted_path = root.join("AGENTS.md");
        let mut writer = |path: &Path, bytes: &[u8]| -> Result<()> {
            if path == corrupted_path {
                return atomic_write(path, b"corrupted bytes");
            }
            atomic_write(path, bytes)
        };
        let error = apply_materialization_with_writer(&root, &objects, &targets, &mut writer)
            .expect_err("verification must fail");
        assert!(error.to_string().contains("native load verification"));
        assert_eq!(
            fs::read_to_string(root.join("AGENTS.md")).expect("agents"),
            seed
        );
        assert!(!root.join(MANIFEST_RELATIVE).exists());
    }

    #[test]
    fn canonical_records_persist_with_materializes_links_and_replay() {
        let directory = tempdir().expect("tempdir");
        let workspace = directory.path().join("workspace");
        fs::create_dir_all(&workspace).expect("workspace");
        let state = StateStore::open(directory.path().join("canonical.sqlite3")).expect("state");

        let rule_id = Uuid::now_v7();
        let rule_payload = RulePayload {
            name: "Context First".to_string(),
            scope: "project".to_string(),
            guidance: "Always compile context before editing.".to_string(),
            enforcement: String::new(),
            object_revision: "1".to_string(),
            metadata: json!({}),
        };
        let mut rule_input = CanonicalEntityInput::active(rule_payload.clone());
        rule_input.id = Some(rule_id);
        state.put(rule_input).expect("canonical rule");

        let unknown_agent_id = Uuid::now_v7();
        let objects = vec![
            MaterializeObject::Rule {
                id: rule_id,
                payload: rule_payload,
            },
            agent_object(unknown_agent_id, "Reviewer"),
        ];
        let targets = [TargetPlatform::ClaudeCode, TargetPlatform::Cursor];
        let (plan, receipt) = apply_materialization(&workspace, &objects, &targets).expect("apply");

        let records = persist_materialization_records(&state, None, &plan, &receipt);
        assert_eq!(records.len(), 4);
        assert!(
            records
                .iter()
                .all(|record| record.materialization_id.is_some())
        );
        let rule_records: Vec<_> = records
            .iter()
            .filter(|record| record.object_id == rule_id)
            .collect();
        assert_eq!(rule_records.len(), 2);
        assert!(rule_records.iter().all(|record| record.linked));
        assert!(
            rule_records
                .iter()
                .any(|record| record.materialization_state == "degraded"
                    && record.target_platform == "cursor")
        );
        let unknown_records: Vec<_> = records
            .iter()
            .filter(|record| record.object_id == unknown_agent_id)
            .collect();
        assert!(unknown_records.iter().all(|record| !record.linked));
        assert!(unknown_records.iter().all(|record| record.error.is_some()));

        let links = state.links_from(rule_id).expect("links");
        assert_eq!(
            links
                .iter()
                .filter(|link| link.relationship == RelationshipKind::Materializes)
                .count(),
            2
        );

        // Replaying the persistence is idempotent per record.
        let replayed = persist_materialization_records(&state, None, &plan, &receipt);
        let first_ids: Vec<_> = records
            .iter()
            .map(|record| record.materialization_id)
            .collect();
        let replay_ids: Vec<_> = replayed
            .iter()
            .map(|record| record.materialization_id)
            .collect();
        assert_eq!(first_ids, replay_ids);
    }

    #[tokio::test]
    async fn server_materialize_apply_reports_records_persisted_truthfully() {
        let directory = tempdir().expect("tempdir");
        let workspace = directory.path().join("workspace");
        fs::create_dir_all(&workspace).expect("workspace");
        let server =
            crate::PublicMcpServer::with_store(&workspace, directory.path().join("index.sqlite3"))
                .expect("server");
        let objects = vec![rule_object(Uuid::now_v7(), "Context First", "project", "")];
        let targets = [TargetPlatform::ClaudeCode];

        let detached = server
            .materialize_apply(&objects, &targets)
            .await
            .expect("detached apply");
        assert_eq!(detached["recordsPersisted"], json!(false));
        assert!(
            detached["records"]
                .as_array()
                .expect("records array")
                .is_empty()
        );
        assert_eq!(detached["productionClaimAllowed"], json!(false));
        assert_eq!(detached["receipt"]["load_verified"], json!(true));

        let state_path = directory.path().join("canonical.sqlite3");
        drop(StateStore::open(&state_path).expect("create canonical state"));
        let server = server.with_canonical_state(&state_path).expect("attach");
        let attached = server
            .materialize_apply(&objects, &targets)
            .await
            .expect("attached apply");
        assert_eq!(attached["recordsPersisted"], json!(true));
        let records = attached["records"].as_array().expect("records array");
        assert!(!records.is_empty());
        assert!(
            records
                .iter()
                .all(|record| record["materialization_id"].is_string())
        );
        // The rule object has no canonical record, so the link is truthfully
        // reported as failed while the materialization record persists.
        assert!(
            records
                .iter()
                .all(|record| record["linked"] == json!(false))
        );
    }
}
