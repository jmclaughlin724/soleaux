//! Capability-gated canonical memory lifecycle: propose, correct, validate,
//! supersede, tombstone, list, export, and import over daemon-owned claim
//! entities. These are daemon/CLI operations only and never appear in the
//! public MCP `tools/list`. Every mutation passes the deny-by-default
//! `PolicyEngine` before it reaches the canonical writer, and every canonical
//! write revalidates the memory state machine through the payload guards.

use crate::registry::{bounded_children, bounded_response, validate_json_field, validate_text};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_state::{
    CanonicalEntityInput, CanonicalPayload, CanonicalRecord, ConflictPayload, EntityLinkInput,
    MEMORY_STATE_ACTIVE, MEMORY_STATE_PROPOSED, MEMORY_STATE_REJECTED, MEMORY_STATE_SUPERSEDED,
    MEMORY_STATE_TOMBSTONED, MEMORY_STATE_VALIDATED, MemoryClaimPayload, REGISTRY_PAGE_LIMIT_MAX,
    RelationshipKind, Sensitivity, SessionPayload, StateStore, WorkspacePayload,
    validate_memory_scope, validate_memory_transition,
};
use soleaux_vault::{Capability, CapabilityRequest, PolicyEngine, RiskLevel, SensitivityLevel};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

pub(crate) const MEMORY_SCHEMA_VERSION: &str = "soleaux.memory/v1";
pub(crate) const MEMORY_EXPORT_SCHEMA_VERSION: &str = "soleaux.memory-export/v1";
const MEMORY_CONFLICT_TYPE: &str = "memory_claim_contradiction";
const MEMORY_EVIDENCE_URI_MAX: usize = 16;
const CONFLICT_SCAN_PAGE_LIMIT: usize = 4;
const CONFLICT_MAX_RECORDS: usize = 8;

/// The review dispositions `memory.validate` may write. Supersession and
/// tombstoning have their own gated operations and never ride a disposition.
const VALIDATE_DISPOSITIONS: [&str; 3] = [
    MEMORY_STATE_VALIDATED,
    MEMORY_STATE_ACTIVE,
    MEMORY_STATE_REJECTED,
];

#[derive(Debug, Clone)]
pub(crate) struct MemoryProposal {
    pub scope: String,
    pub claim_type: String,
    pub subject: String,
    pub content: String,
    pub confidence: f64,
    pub evidence_uris: Vec<String>,
    pub supersedes_id: Option<Uuid>,
    pub source_session_id: Option<Uuid>,
    pub sensitivity: Sensitivity,
    pub expires_at_unix_ms: Option<i64>,
    pub metadata: Value,
}

fn validate_limit(limit: usize) -> Result<()> {
    if limit == 0 || limit > REGISTRY_PAGE_LIMIT_MAX {
        bail!("memory page limit must be between 1 and {REGISTRY_PAGE_LIMIT_MAX}");
    }
    Ok(())
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

const fn sensitivity_level(sensitivity: Sensitivity) -> SensitivityLevel {
    match sensitivity {
        Sensitivity::Public => SensitivityLevel::Public,
        Sensitivity::Internal => SensitivityLevel::Internal,
        Sensitivity::Confidential => SensitivityLevel::Confidential,
        Sensitivity::Secret => SensitivityLevel::Secret,
    }
}

fn authorize_memory_write(
    policy: &PolicyEngine,
    actor: &str,
    workspace_id: Uuid,
    resource: &str,
    sensitivity: Sensitivity,
) -> Result<()> {
    let decision = policy.evaluate(&CapabilityRequest {
        subject: actor.to_string(),
        workspace_id,
        capability: Capability::WriteMemory,
        resource: resource.to_string(),
        risk: RiskLevel::LocalWrite,
        sensitivity: sensitivity_level(sensitivity),
        now_unix_ms: now_unix_ms(),
        approval: None,
    });
    if decision.allowed() {
        return Ok(());
    }
    bail!(
        "memory capability denied for {actor}: {}",
        decision.reasons.join("; ")
    )
}

fn live_workspace(
    state: &StateStore,
    workspace_id: Uuid,
) -> Result<CanonicalRecord<WorkspacePayload>> {
    let workspace = state
        .get::<WorkspacePayload>(workspace_id)?
        .context("workspace is not registered")?;
    if workspace.tombstoned_at_unix_ms.is_some() {
        bail!("workspace is tombstoned");
    }
    Ok(workspace)
}

fn live_claim(state: &StateStore, claim_id: Uuid) -> Result<CanonicalRecord<MemoryClaimPayload>> {
    let claim = state
        .get::<MemoryClaimPayload>(claim_id)?
        .context("memory claim does not exist")?;
    if claim.tombstoned_at_unix_ms.is_some() {
        bail!("memory claim is tombstoned");
    }
    Ok(claim)
}

/// A claim past its expiry is retention's to tombstone; no lifecycle
/// operation may advance it.
fn fresh_claim(state: &StateStore, claim_id: Uuid) -> Result<CanonicalRecord<MemoryClaimPayload>> {
    let claim = live_claim(state, claim_id)?;
    if claim
        .expires_at_unix_ms
        .is_some_and(|expires| expires <= now_unix_ms())
    {
        bail!("memory claim is expired");
    }
    Ok(claim)
}

fn claim_workspace(claim: &CanonicalRecord<MemoryClaimPayload>) -> Result<Uuid> {
    claim
        .workspace_id
        .context("memory claim is not bound to a workspace")
}

fn claim_resource(scope: &str, claim_id: Uuid) -> String {
    format!("memory/{scope}/{claim_id}")
}

fn claim_update_input(
    claim: &CanonicalRecord<MemoryClaimPayload>,
    payload: MemoryClaimPayload,
) -> CanonicalEntityInput<MemoryClaimPayload> {
    CanonicalEntityInput {
        id: Some(claim.id),
        workspace_id: claim.workspace_id,
        parent_id: claim.parent_id,
        origin_platform: claim.origin_platform.clone(),
        native_id: claim.native_id.clone(),
        state: payload.memory_state.clone(),
        sensitivity: claim.sensitivity,
        idempotency_key: claim.idempotency_key.clone(),
        expected_revision: Some(claim.revision),
        expires_at_unix_ms: claim.expires_at_unix_ms,
        payload,
    }
}

fn claim_response(claim: &CanonicalRecord<MemoryClaimPayload>) -> Result<Value> {
    bounded_response(json!({
        "schemaVersion": MEMORY_SCHEMA_VERSION,
        "claim": claim,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn propose_claim(
    state: &StateStore,
    policy: &PolicyEngine,
    workspace_id: Uuid,
    actor: &str,
    proposal: MemoryProposal,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    validate_text(&proposal.claim_type, "memory claim type")?;
    validate_text(&proposal.subject, "memory subject")?;
    validate_text(&proposal.content, "memory content")?;
    validate_memory_scope(&proposal.scope)?;
    validate_json_field(&proposal.metadata, "memory metadata")?;
    if proposal.evidence_uris.len() > MEMORY_EVIDENCE_URI_MAX {
        bail!("memory evidence list exceeds {MEMORY_EVIDENCE_URI_MAX} entries");
    }
    for uri in &proposal.evidence_uris {
        validate_text(uri, "memory evidence uri")?;
    }
    if !(0.0..=1.0).contains(&proposal.confidence) {
        bail!("memory confidence must be between zero and one");
    }
    live_workspace(state, workspace_id)?;
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &format!("memory/{}", proposal.scope),
        proposal.sensitivity,
    )?;

    if let Some(session_id) = proposal.source_session_id {
        let session = state
            .get::<SessionPayload>(session_id)?
            .context("memory source session does not exist")?;
        if session.tombstoned_at_unix_ms.is_some() {
            bail!("memory source session is tombstoned");
        }
        if session.workspace_id != Some(workspace_id) {
            bail!("memory source session belongs to another workspace");
        }
    }
    if let Some(supersedes_id) = proposal.supersedes_id {
        let target = live_claim(state, supersedes_id)?;
        if target.workspace_id != Some(workspace_id) {
            bail!("memory supersession target belongs to another workspace");
        }
        if target.payload.scope != proposal.scope {
            bail!("memory supersession target lives in another scope");
        }
    }

    let claim_id = Uuid::now_v7();
    let payload = MemoryClaimPayload {
        claim_type: proposal.claim_type,
        subject: proposal.subject,
        content: proposal.content,
        scope: proposal.scope,
        memory_state: MEMORY_STATE_PROPOSED.to_string(),
        confidence: proposal.confidence,
        evidence_uris: proposal.evidence_uris,
        supersedes_id: proposal.supersedes_id,
        source_session_id: proposal.source_session_id,
        metadata: proposal.metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.id = Some(claim_id);
    input.workspace_id = Some(workspace_id);
    input.state = MEMORY_STATE_PROPOSED.to_string();
    input.sensitivity = proposal.sensitivity;
    input.expires_at_unix_ms = proposal.expires_at_unix_ms;
    let claim = state.put(input)?;

    if let Some(session_id) = claim.payload.source_session_id {
        state.link(EntityLinkInput {
            source_id: claim.id,
            relationship: RelationshipKind::Lineage,
            target_id: session_id,
            metadata: json!({"provenance":"memory_source_session"}),
        })?;
    }

    let (conflicts, conflict_scan_truncated) = record_contradictions(state, &claim)?;
    let (conflicts, conflict_total, conflicts_truncated) = bounded_children(conflicts)?;
    bounded_response(json!({
        "schemaVersion": MEMORY_SCHEMA_VERSION,
        "claim": claim,
        "conflicts": conflicts,
        "conflictCount": conflict_total,
        "conflictsTruncated": conflicts_truncated,
        "conflictScanTruncated": conflict_scan_truncated,
        "productionClaimAllowed": false,
    }))
}

/// Writes one open `ConflictPayload` per active claim that shares the new
/// claim's scope, type, and subject but disagrees on content. The scan is
/// bounded, so a truncated scan is reported rather than silently complete.
fn record_contradictions(
    state: &StateStore,
    claim: &CanonicalRecord<MemoryClaimPayload>,
) -> Result<(Vec<CanonicalRecord<ConflictPayload>>, bool)> {
    let mut conflicts = Vec::new();
    let mut cursor = None;
    let mut truncated = false;
    'pages: for page_index in 0..CONFLICT_SCAN_PAGE_LIMIT {
        let page = state.memory_claim_page(
            claim.workspace_id,
            Some(&claim.payload.scope),
            Some(MEMORY_STATE_ACTIVE),
            cursor,
            REGISTRY_PAGE_LIMIT_MAX,
        )?;
        for existing in &page.items {
            if existing.id == claim.id
                || existing.payload.claim_type != claim.payload.claim_type
                || existing.payload.subject != claim.payload.subject
                || existing.payload.content == claim.payload.content
            {
                continue;
            }
            if conflicts.len() >= CONFLICT_MAX_RECORDS {
                truncated = true;
                break 'pages;
            }
            let payload = ConflictPayload {
                left_entity_id: claim.id,
                right_entity_id: existing.id,
                conflict_type: MEMORY_CONFLICT_TYPE.to_string(),
                conflict_state: "open".to_string(),
                resolution: None,
                metadata: json!({
                    "scope": claim.payload.scope,
                    "claimType": claim.payload.claim_type,
                    "subject": claim.payload.subject,
                }),
            };
            let mut input = CanonicalEntityInput::active(payload);
            input.workspace_id = claim.workspace_id;
            input.state = "open".to_string();
            input.idempotency_key = Some(format!("memory-conflict:{}:{}", claim.id, existing.id));
            let conflict = state.put(input)?;
            state.link(EntityLinkInput {
                source_id: claim.id,
                relationship: RelationshipKind::ConflictsWith,
                target_id: existing.id,
                metadata: json!({"conflictId": conflict.id}),
            })?;
            conflicts.push(conflict);
        }
        if !page.truncated {
            break;
        }
        match page.next_cursor {
            Some(next) => {
                if page_index + 1 == CONFLICT_SCAN_PAGE_LIMIT {
                    truncated = true;
                    break;
                }
                cursor = Some(next);
            }
            None => break,
        }
    }
    Ok((conflicts, truncated))
}

pub(crate) fn validate_claim(
    state: &StateStore,
    policy: &PolicyEngine,
    claim_id: Uuid,
    actor: &str,
    disposition: &str,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    if !VALIDATE_DISPOSITIONS.contains(&disposition) {
        bail!(
            "memory validation disposition must be one of: {}",
            VALIDATE_DISPOSITIONS.join(", ")
        );
    }
    let claim = fresh_claim(state, claim_id)?;
    let workspace_id = claim_workspace(&claim)?;
    validate_memory_transition(&claim.payload.memory_state, disposition)?;
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &claim_resource(&claim.payload.scope, claim.id),
        claim.sensitivity,
    )?;
    let mut payload = claim.payload.clone();
    payload.memory_state = disposition.to_string();
    let updated = state.put(claim_update_input(&claim, payload))?;
    claim_response(&updated)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn correct_claim(
    state: &StateStore,
    policy: &PolicyEngine,
    claim_id: Uuid,
    actor: &str,
    content: Option<String>,
    confidence: Option<f64>,
    evidence_uris: Option<Vec<String>>,
    metadata: Option<Value>,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    if content.is_none() && confidence.is_none() && evidence_uris.is_none() && metadata.is_none() {
        bail!("memory correction requires at least one changed field");
    }
    let claim = fresh_claim(state, claim_id)?;
    let workspace_id = claim_workspace(&claim)?;
    if ![
        MEMORY_STATE_PROPOSED,
        MEMORY_STATE_VALIDATED,
        MEMORY_STATE_ACTIVE,
    ]
    .contains(&claim.payload.memory_state.as_str())
    {
        bail!("terminal memory claim cannot be corrected");
    }
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &claim_resource(&claim.payload.scope, claim.id),
        claim.sensitivity,
    )?;
    let mut payload = claim.payload.clone();
    if let Some(content) = content {
        validate_text(&content, "memory content")?;
        payload.content = content;
    }
    if let Some(confidence) = confidence {
        if !(0.0..=1.0).contains(&confidence) {
            bail!("memory confidence must be between zero and one");
        }
        payload.confidence = confidence;
    }
    if let Some(evidence_uris) = evidence_uris {
        if evidence_uris.len() > MEMORY_EVIDENCE_URI_MAX {
            bail!("memory evidence list exceeds {MEMORY_EVIDENCE_URI_MAX} entries");
        }
        for uri in &evidence_uris {
            validate_text(uri, "memory evidence uri")?;
        }
        payload.evidence_uris = evidence_uris;
    }
    if let Some(metadata) = metadata {
        validate_json_field(&metadata, "memory metadata")?;
        payload.metadata = metadata;
    }
    let updated = state.put(claim_update_input(&claim, payload))?;
    claim_response(&updated)
}

pub(crate) fn supersede_claim(
    state: &StateStore,
    policy: &PolicyEngine,
    claim_id: Uuid,
    actor: &str,
    replacement_id: Uuid,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    if claim_id == replacement_id {
        bail!("memory claim cannot supersede itself");
    }
    let claim = fresh_claim(state, claim_id)?;
    let workspace_id = claim_workspace(&claim)?;
    if claim.payload.memory_state != MEMORY_STATE_ACTIVE {
        bail!("only an active memory claim can be superseded");
    }
    let replacement = fresh_claim(state, replacement_id)?;
    if replacement.workspace_id != claim.workspace_id {
        bail!("memory replacement belongs to another workspace");
    }
    if replacement.payload.scope != claim.payload.scope {
        bail!("memory replacement lives in another scope");
    }
    if ![MEMORY_STATE_VALIDATED, MEMORY_STATE_ACTIVE]
        .contains(&replacement.payload.memory_state.as_str())
    {
        bail!("memory replacement must be validated or active");
    }
    if replacement
        .payload
        .supersedes_id
        .is_some_and(|target| target != claim_id)
    {
        bail!("memory replacement already supersedes another claim");
    }
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &claim_resource(&claim.payload.scope, claim.id),
        claim.sensitivity,
    )?;
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &claim_resource(&replacement.payload.scope, replacement.id),
        replacement.sensitivity,
    )?;

    // Replacement first: a failure between the two writes leaves both claims
    // active with the supersession recorded, and rerunning the operation
    // converges; the reverse order would strand the scope with no active
    // claim and no rerunnable path.
    let mut replacement_payload = replacement.payload.clone();
    replacement_payload.supersedes_id = Some(claim_id);
    if replacement_payload.memory_state == MEMORY_STATE_VALIDATED {
        validate_memory_transition(&replacement_payload.memory_state, MEMORY_STATE_ACTIVE)?;
        replacement_payload.memory_state = MEMORY_STATE_ACTIVE.to_string();
    }
    let replacement = if replacement_payload == replacement.payload {
        replacement
    } else {
        state.put(claim_update_input(&replacement, replacement_payload))?
    };

    validate_memory_transition(&claim.payload.memory_state, MEMORY_STATE_SUPERSEDED)?;
    let mut claim_payload = claim.payload.clone();
    claim_payload.memory_state = MEMORY_STATE_SUPERSEDED.to_string();
    let claim = state.put(claim_update_input(&claim, claim_payload))?;

    state.link(EntityLinkInput {
        source_id: replacement.id,
        relationship: RelationshipKind::Supersedes,
        target_id: claim.id,
        metadata: json!({"actor": actor}),
    })?;
    bounded_response(json!({
        "schemaVersion": MEMORY_SCHEMA_VERSION,
        "claim": claim,
        "replacement": replacement,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn tombstone_claim(
    state: &StateStore,
    policy: &PolicyEngine,
    claim_id: Uuid,
    actor: &str,
    reason: &str,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    validate_text(reason, "memory tombstone reason")?;
    let claim = live_claim(state, claim_id)?;
    let workspace_id = claim_workspace(&claim)?;
    validate_memory_transition(&claim.payload.memory_state, MEMORY_STATE_TOMBSTONED)?;
    authorize_memory_write(
        policy,
        actor,
        workspace_id,
        &claim_resource(&claim.payload.scope, claim.id),
        claim.sensitivity,
    )?;
    let mut payload = claim.payload.clone();
    payload.memory_state = MEMORY_STATE_TOMBSTONED.to_string();
    let claim = state.put(claim_update_input(&claim, payload))?;
    let tombstone = state.tombstone(claim.id, reason, actor)?;
    bounded_response(json!({
        "schemaVersion": MEMORY_SCHEMA_VERSION,
        "claim": claim,
        "tombstone": tombstone,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn list_claims(
    state: &StateStore,
    workspace_id: Option<Uuid>,
    scope: Option<&str>,
    memory_state: Option<&str>,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    validate_limit(limit)?;
    let page = state.memory_claim_page(workspace_id, scope, memory_state, cursor, limit)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.memory-list/v1",
        "claims": page.items,
        "nextCursor": page.next_cursor,
        "truncated": page.truncated,
        "limit": limit,
        "productionClaimAllowed": false,
    }))
}

pub(crate) fn export_claims(
    state: &StateStore,
    workspace_id: Uuid,
    scope: Option<&str>,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    validate_limit(limit)?;
    live_workspace(state, workspace_id)?;
    let page = state.memory_claim_page(Some(workspace_id), scope, None, cursor, limit)?;
    bounded_response(json!({
        "schemaVersion": MEMORY_EXPORT_SCHEMA_VERSION,
        "workspaceId": workspace_id,
        "claims": page.items,
        "count": page.items.len(),
        "nextCursor": page.next_cursor,
        "truncated": page.truncated,
        "productionClaimAllowed": false,
    }))
}

/// Imports an exported claim page into the destination workspace under new
/// canonical ids. Lifecycle states, sensitivity, expiry, and payload bytes are
/// preserved exactly; the original id and payload hash form the idempotency
/// key, so re-importing the same document replays instead of duplicating.
pub(crate) fn import_claims(
    state: &StateStore,
    policy: &PolicyEngine,
    workspace_id: Uuid,
    actor: &str,
    document: &Value,
) -> Result<Value> {
    validate_text(actor, "memory actor")?;
    if document.get("schemaVersion").and_then(Value::as_str) != Some(MEMORY_EXPORT_SCHEMA_VERSION) {
        bail!("memory import requires a {MEMORY_EXPORT_SCHEMA_VERSION} document");
    }
    let claims = document
        .get("claims")
        .and_then(Value::as_array)
        .context("memory import document has no claims array")?;
    if claims.is_empty() {
        bail!("memory import document contains no claims");
    }
    if claims.len() > REGISTRY_PAGE_LIMIT_MAX {
        bail!("memory import exceeds the page limit of {REGISTRY_PAGE_LIMIT_MAX} claims");
    }
    live_workspace(state, workspace_id)?;

    let mut records = Vec::new();
    for claim in claims {
        let record: CanonicalRecord<MemoryClaimPayload> =
            serde_json::from_value(claim.clone()).context("memory import claim is malformed")?;
        record.payload.validate()?;
        records.push(record);
    }
    for record in &records {
        authorize_memory_write(
            policy,
            actor,
            workspace_id,
            &format!("memory/{}", record.payload.scope),
            record.sensitivity,
        )?;
    }

    let mut imported = Vec::new();
    let mut mapping = Vec::new();
    for record in records {
        let mut input = CanonicalEntityInput::active(record.payload.clone());
        input.workspace_id = Some(workspace_id);
        input.state = record.payload.memory_state.clone();
        input.sensitivity = record.sensitivity;
        input.expires_at_unix_ms = record.expires_at_unix_ms;
        input.idempotency_key = Some(format!(
            "memory-import:{}:{}",
            record.id, record.payload_hash
        ));
        let created = state.put(input)?;
        mapping.push(json!({"from": record.id, "to": created.id}));
        imported.push(created);
    }
    state.append_audit(
        "memory.import",
        Some(workspace_id),
        None,
        json!({"actor": actor, "count": imported.len(), "mapping": mapping}),
    )?;
    let (imported, total, truncated) = bounded_children(imported)?;
    bounded_response(json!({
        "schemaVersion": "soleaux.memory-import/v1",
        "workspaceId": workspace_id,
        "imported": imported,
        "count": total,
        "importedTruncated": truncated,
        "productionClaimAllowed": false,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use soleaux_state::{
        LOCKED_CONTEXT_PACKET_SHA256, LOCKED_PROFILE_SHA256, PUBLIC_TOOL_CEILING,
        WorkspaceTrustState,
    };
    use soleaux_vault::CapabilityGrant;
    use std::collections::BTreeSet;
    use tempfile::tempdir;

    fn fixture_workspace(state: &StateStore) -> Uuid {
        let workspace_id = Uuid::now_v7();
        let mut input = CanonicalEntityInput::active(WorkspacePayload {
            canonical_path: format!("/fixtures/{workspace_id}"),
            path_hash: blake3::hash(workspace_id.as_bytes()).to_hex().to_string(),
            display_name: "Memory fixture".to_string(),
            trust_state: WorkspaceTrustState::Trusted,
            profile_digest: LOCKED_PROFILE_SHA256.to_string(),
            context_digest: LOCKED_CONTEXT_PACKET_SHA256.to_string(),
            public_tool_ceiling: PUBLIC_TOOL_CEILING,
            production_claim_allowed: false,
            metadata: json!({}),
        });
        input.id = Some(workspace_id);
        input.state = "registered".to_string();
        state.put(input).expect("workspace");
        workspace_id
    }

    fn granted_policy(actor: &str) -> PolicyEngine {
        let mut policy = PolicyEngine::new();
        policy
            .add_grant(CapabilityGrant {
                id: Uuid::now_v7(),
                subject: actor.to_string(),
                workspace_id: None,
                capabilities: BTreeSet::from([Capability::WriteMemory]),
                resource_prefixes: Vec::new(),
                max_risk: RiskLevel::LocalWrite,
                max_sensitivity: SensitivityLevel::Secret,
                expires_at_unix_ms: None,
                requires_approval: false,
                delegable: false,
                parent_grant_id: None,
                labels: BTreeSet::new(),
            })
            .expect("grant");
        policy
    }

    fn proposal(scope: &str, subject: &str, content: &str) -> MemoryProposal {
        MemoryProposal {
            scope: scope.to_string(),
            claim_type: "decision".to_string(),
            subject: subject.to_string(),
            content: content.to_string(),
            confidence: 0.9,
            evidence_uris: vec!["soleaux://audit/fixture".to_string()],
            supersedes_id: None,
            source_session_id: None,
            sensitivity: Sensitivity::Internal,
            expires_at_unix_ms: None,
            metadata: json!({}),
        }
    }

    fn claim_id(value: &Value) -> Uuid {
        value["claim"]["id"]
            .as_str()
            .expect("claim id")
            .parse()
            .expect("claim uuid")
    }

    #[test]
    fn memory_lifecycle_advances_proposed_validated_active_and_supersedes() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = fixture_workspace(&state);
        let policy = granted_policy("reviewer");

        let proposed = propose_claim(
            &state,
            &policy,
            workspace_id,
            "reviewer",
            proposal("team", "database", "Use one serialized writer"),
        )
        .expect("propose");
        assert_eq!(proposed["claim"]["payload"]["memoryState"], "proposed");
        assert_eq!(proposed["conflictCount"], 0);
        let first = claim_id(&proposed);

        let validated = validate_claim(&state, &policy, first, "reviewer", MEMORY_STATE_VALIDATED)
            .expect("validate");
        assert_eq!(validated["claim"]["payload"]["memoryState"], "validated");
        let activated = validate_claim(&state, &policy, first, "reviewer", MEMORY_STATE_ACTIVE)
            .expect("activate");
        assert_eq!(activated["claim"]["payload"]["memoryState"], "active");
        assert!(
            validate_claim(&state, &policy, first, "reviewer", MEMORY_STATE_VALIDATED).is_err(),
            "active -> validated must be refused"
        );

        let corrected = correct_claim(
            &state,
            &policy,
            first,
            "reviewer",
            Some("Use one serialized writer thread".to_string()),
            Some(0.97),
            None,
            None,
        )
        .expect("correct");
        assert_eq!(corrected["claim"]["payload"]["memoryState"], "active");
        assert_eq!(corrected["claim"]["payload"]["confidence"], 0.97);
        assert_eq!(corrected["claim"]["revision"], 4);

        let contradiction = propose_claim(
            &state,
            &policy,
            workspace_id,
            "reviewer",
            proposal("team", "database", "Use two writer threads"),
        )
        .expect("contradicting propose");
        assert_eq!(contradiction["conflictCount"], 1);
        assert_eq!(
            contradiction["conflicts"][0]["payload"]["conflictType"],
            "memory_claim_contradiction"
        );
        assert_eq!(
            contradiction["conflicts"][0]["payload"]["conflictState"],
            "open"
        );
        let second = claim_id(&contradiction);
        let links = state.links_from(second).expect("links");
        assert!(
            links
                .iter()
                .any(|link| link.relationship == RelationshipKind::ConflictsWith
                    && link.target_id == first)
        );

        validate_claim(&state, &policy, second, "reviewer", MEMORY_STATE_VALIDATED)
            .expect("validate replacement");
        let superseded =
            supersede_claim(&state, &policy, first, "reviewer", second).expect("supersede");
        assert_eq!(superseded["claim"]["payload"]["memoryState"], "superseded");
        assert_eq!(
            superseded["replacement"]["payload"]["memoryState"],
            "active"
        );
        assert_eq!(
            superseded["replacement"]["payload"]["supersedesId"],
            first.to_string()
        );
        let links = state.links_from(second).expect("supersedes links");
        assert!(links.iter().any(
            |link| link.relationship == RelationshipKind::Supersedes && link.target_id == first
        ));
        assert!(
            correct_claim(
                &state,
                &policy,
                first,
                "reviewer",
                Some("late edit".to_string()),
                None,
                None,
                None,
            )
            .is_err(),
            "a superseded claim is terminal"
        );

        let tombstoned = tombstone_claim(&state, &policy, second, "reviewer", "fixture cleanup")
            .expect("tombstone");
        assert_eq!(tombstoned["claim"]["payload"]["memoryState"], "tombstoned");
        assert_eq!(tombstoned["tombstone"]["reason"], "fixture cleanup");
        assert!(
            state
                .get::<MemoryClaimPayload>(second)
                .expect("read")
                .expect("record")
                .tombstoned_at_unix_ms
                .is_some()
        );
        assert!(state.verify_audit_chain().expect("audit chain"));
    }

    #[test]
    fn memory_rejected_and_expired_paths_fail_closed() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = fixture_workspace(&state);
        let policy = granted_policy("reviewer");

        let proposed = propose_claim(
            &state,
            &policy,
            workspace_id,
            "reviewer",
            proposal("session", "assumption", "The cache is warm"),
        )
        .expect("propose");
        let rejected_id = claim_id(&proposed);
        let rejected = validate_claim(
            &state,
            &policy,
            rejected_id,
            "reviewer",
            MEMORY_STATE_REJECTED,
        )
        .expect("reject");
        assert_eq!(rejected["claim"]["payload"]["memoryState"], "rejected");
        for disposition in [MEMORY_STATE_VALIDATED, MEMORY_STATE_ACTIVE] {
            assert!(
                validate_claim(&state, &policy, rejected_id, "reviewer", disposition).is_err(),
                "rejected -> {disposition} must be refused"
            );
        }
        assert!(
            tombstone_claim(&state, &policy, rejected_id, "reviewer", "cleanup").is_err(),
            "rejected claims are outside the tombstone transition"
        );

        let mut expiring = proposal("session", "expiring", "This claim expires");
        expiring.expires_at_unix_ms = Some(now_unix_ms().saturating_sub(1));
        let expired = propose_claim(&state, &policy, workspace_id, "reviewer", expiring)
            .expect("expired propose");
        let expired_id = claim_id(&expired);
        let error = validate_claim(
            &state,
            &policy,
            expired_id,
            "reviewer",
            MEMORY_STATE_VALIDATED,
        )
        .expect_err("expired claim must refuse transitions");
        assert!(error.to_string().contains("expired"));
        assert!(
            correct_claim(
                &state,
                &policy,
                expired_id,
                "reviewer",
                Some("edit".to_string()),
                None,
                None,
                None,
            )
            .is_err()
        );
    }

    #[test]
    fn memory_mutations_are_denied_by_default_without_a_grant() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = fixture_workspace(&state);
        let denying = PolicyEngine::new();

        let error = propose_claim(
            &state,
            &denying,
            workspace_id,
            "reviewer",
            proposal("team", "database", "Use one serialized writer"),
        )
        .expect_err("ungranted propose must be denied");
        assert!(error.to_string().contains("memory capability denied"));
        assert!(
            state
                .memory_claim_page(Some(workspace_id), None, None, None, 8)
                .expect("page")
                .items
                .is_empty(),
            "a denied propose must not write"
        );

        let granting = granted_policy("reviewer");
        let proposed = propose_claim(
            &state,
            &granting,
            workspace_id,
            "reviewer",
            proposal("team", "database", "Use one serialized writer"),
        )
        .expect("granted propose");
        let id = claim_id(&proposed);
        let error = validate_claim(&state, &denying, id, "reviewer", MEMORY_STATE_VALIDATED)
            .expect_err("ungranted validate must be denied");
        assert!(error.to_string().contains("memory capability denied"));
        let unchanged = state
            .get::<MemoryClaimPayload>(id)
            .expect("read")
            .expect("record");
        assert_eq!(unchanged.payload.memory_state, MEMORY_STATE_PROPOSED);

        let other_actor = granted_policy("someone-else");
        assert!(
            validate_claim(&state, &other_actor, id, "reviewer", MEMORY_STATE_VALIDATED).is_err(),
            "a grant for another subject must not admit this actor"
        );
    }

    #[test]
    fn memory_export_import_round_trips_and_replays() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = fixture_workspace(&state);
        let policy = granted_policy("reviewer");

        let first = claim_id(
            &propose_claim(
                &state,
                &policy,
                workspace_id,
                "reviewer",
                proposal("team", "database", "Use one serialized writer"),
            )
            .expect("propose"),
        );
        validate_claim(&state, &policy, first, "reviewer", MEMORY_STATE_VALIDATED)
            .expect("validate");
        validate_claim(&state, &policy, first, "reviewer", MEMORY_STATE_ACTIVE).expect("activate");
        propose_claim(
            &state,
            &policy,
            workspace_id,
            "reviewer",
            proposal("session", "assumption", "The cache is warm"),
        )
        .expect("second propose");

        let document = export_claims(&state, workspace_id, None, None, REGISTRY_PAGE_LIMIT_MAX)
            .expect("export");
        assert_eq!(document["schemaVersion"], MEMORY_EXPORT_SCHEMA_VERSION);
        assert_eq!(document["count"], 2);

        let destination_dir = tempdir().expect("destination");
        let destination =
            StateStore::open(destination_dir.path().join("state.sqlite3")).expect("destination");
        let destination_workspace = fixture_workspace(&destination);
        let imported = import_claims(
            &destination,
            &policy,
            destination_workspace,
            "reviewer",
            &document,
        )
        .expect("import");
        assert_eq!(imported["count"], 2);

        let source_payloads: Vec<MemoryClaimPayload> = document["claims"]
            .as_array()
            .expect("claims")
            .iter()
            .map(|claim| serde_json::from_value(claim["payload"].clone()).expect("payload"))
            .collect();
        let imported_page = destination
            .memory_claim_page(Some(destination_workspace), None, None, None, 8)
            .expect("imported page");
        assert_eq!(imported_page.items.len(), 2);
        for record in &imported_page.items {
            assert!(
                source_payloads.contains(&record.payload),
                "imported payloads must match the exported payloads exactly"
            );
            assert_eq!(record.workspace_id, Some(destination_workspace));
        }

        let replay = import_claims(
            &destination,
            &policy,
            destination_workspace,
            "reviewer",
            &document,
        )
        .expect("replayed import");
        assert_eq!(replay["count"], 2);
        assert_eq!(
            destination
                .memory_claim_page(Some(destination_workspace), None, None, None, 8)
                .expect("page after replay")
                .items
                .len(),
            2,
            "re-importing the same document must replay, not duplicate"
        );

        let denied = import_claims(
            &destination,
            &PolicyEngine::new(),
            destination_workspace,
            "reviewer",
            &document,
        )
        .expect_err("ungranted import must be denied");
        assert!(denied.to_string().contains("memory capability denied"));
    }

    #[test]
    fn memory_claims_survive_a_compaction_cycle() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = fixture_workspace(&state);
        let policy = granted_policy("reviewer");

        let survivor = claim_id(
            &propose_claim(
                &state,
                &policy,
                workspace_id,
                "reviewer",
                proposal("team", "database", "Use one serialized writer"),
            )
            .expect("propose survivor"),
        );
        validate_claim(
            &state,
            &policy,
            survivor,
            "reviewer",
            MEMORY_STATE_VALIDATED,
        )
        .expect("validate");
        validate_claim(&state, &policy, survivor, "reviewer", MEMORY_STATE_ACTIVE)
            .expect("activate");
        let survivor_before = state
            .get::<MemoryClaimPayload>(survivor)
            .expect("read")
            .expect("record");

        let mut expiring = proposal("session", "expiring", "This claim expires");
        expiring.expires_at_unix_ms = Some(now_unix_ms().saturating_sub(1));
        let expired = claim_id(
            &propose_claim(&state, &policy, workspace_id, "reviewer", expiring)
                .expect("propose expiring"),
        );
        let removed = claim_id(
            &propose_claim(
                &state,
                &policy,
                workspace_id,
                "reviewer",
                proposal("compiled_context", "obsolete", "Remove this claim"),
            )
            .expect("propose removed"),
        );
        validate_claim(&state, &policy, removed, "reviewer", MEMORY_STATE_VALIDATED)
            .expect("validate removed");
        validate_claim(&state, &policy, removed, "reviewer", MEMORY_STATE_ACTIVE)
            .expect("activate removed");
        tombstone_claim(&state, &policy, removed, "reviewer", "obsolete").expect("tombstone");

        let tombstones = state
            .apply_retention(now_unix_ms(), 100)
            .expect("apply retention");
        assert!(tombstones.iter().any(|record| record.entity_id == expired));
        let purged = state
            .purge_tombstones(now_unix_ms().saturating_add(1), 100)
            .expect("purge");
        assert!(purged >= 2, "expired and tombstoned claims must purge");
        let report = state.repair().expect("repair");
        assert_eq!(report.integrity, "ok");
        assert!(report.audit_chain_valid);

        let survivor_after = state
            .get::<MemoryClaimPayload>(survivor)
            .expect("read")
            .expect("survivor remains");
        assert_eq!(survivor_after.payload, survivor_before.payload);
        assert_eq!(survivor_after.revision, survivor_before.revision);
        assert!(survivor_after.tombstoned_at_unix_ms.is_none());
        assert!(
            state
                .get::<MemoryClaimPayload>(expired)
                .expect("read expired")
                .is_none()
        );
        assert!(
            state
                .get::<MemoryClaimPayload>(removed)
                .expect("read removed")
                .is_none()
        );
        assert!(state.verify_audit_chain().expect("audit chain"));
    }
}
