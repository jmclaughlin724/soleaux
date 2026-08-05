use anyhow::{Context, Result, bail};
use serde_json::json;
use soleaux_vault::{
    ApprovalEvidence, ArtifactSensitivity, ArtifactVault, Capability, CapabilityGrant,
    CapabilityRequest, FileKeyStore, PolicyEffect, PolicyEngine, RiskLevel, SensitivityLevel,
};
use std::{
    collections::BTreeSet,
    env, fs,
    path::PathBuf,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

fn main() -> Result<()> {
    let output = env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .context("usage: vault_smoke <output-directory>")?;
    fs::create_dir_all(&output)?;
    let vault_root = output.join("vault");
    let key_path = output.join("development-keyring.json");
    let key_store = Arc::new(FileKeyStore::new(&key_path));
    let vault = ArtifactVault::open(&vault_root, key_store)?;
    let workspace_id = Uuid::now_v7();
    let other_workspace_id = Uuid::now_v7();
    let plaintext = b"Soleaux encrypted artifact smoke payload";

    let descriptor = vault.put(
        workspace_id,
        "application/octet-stream",
        plaintext,
        ArtifactSensitivity::Confidential,
        json!({
            "source":"compiled-vault-smoke",
            "authorization":"Bearer smoke-secret",
            "nested":{"api_key":"sk-live-smoke-secret"},
        }),
    )?;
    if descriptor.metadata_redactions < 2
        || descriptor.metadata["authorization"] != "[REDACTED]"
        || descriptor.metadata["nested"]["api_key"] != "[REDACTED]"
    {
        bail!("artifact metadata was not comprehensively redacted");
    }
    let raw_before_rotation = fs::read(&descriptor.storage_path)?;
    if raw_before_rotation
        .windows(plaintext.len())
        .any(|window| window == plaintext)
    {
        bail!("artifact plaintext was found in the encrypted envelope");
    }
    let opened = vault.read(workspace_id, &descriptor.content_hash)?;
    if opened.bytes != plaintext {
        bail!("artifact plaintext did not round-trip");
    }
    if vault
        .read(other_workspace_id, &descriptor.content_hash)
        .is_ok()
    {
        bail!("cross-workspace artifact access unexpectedly succeeded");
    }

    let rotation = vault.rotate_workspace_key(workspace_id)?;
    if rotation.previous_key_version != 1 || rotation.current_key_version != 2 {
        bail!("artifact key rotation did not advance exactly one version");
    }
    let rotated = vault.read(workspace_id, &descriptor.content_hash)?;
    if rotated.bytes != plaintext || rotated.descriptor.key_version != 2 {
        bail!("rotated artifact did not preserve authenticated plaintext");
    }
    let raw_after_rotation = fs::read(&descriptor.storage_path)?;
    if raw_after_rotation == raw_before_rotation {
        bail!("artifact envelope did not change after key rotation");
    }
    let last = raw_after_rotation.len().saturating_sub(1);
    let mut tampered = raw_after_rotation.clone();
    tampered[last] ^= 0x40;
    fs::write(&descriptor.storage_path, &tampered)?;
    let tamper_rejected = vault
        .read(workspace_id, &descriptor.content_hash)
        .is_err();
    fs::write(&descriptor.storage_path, &raw_after_rotation)?;
    if !tamper_rejected {
        bail!("tampered artifact was not rejected");
    }
    let verification = vault.verify_workspace(workspace_id)?;

    let mut policy = PolicyEngine::new();
    let request = CapabilityRequest {
        subject: "desktop:smoke".to_string(),
        workspace_id,
        capability: Capability::WriteArtifact,
        resource: format!("artifact/{}", descriptor.content_hash),
        risk: RiskLevel::LocalWrite,
        sensitivity: SensitivityLevel::Confidential,
        now_unix_ms: unix_ms(),
        approval: None,
    };
    let default_decision = policy.evaluate(&request);
    if default_decision.effect != PolicyEffect::Deny {
        bail!("policy did not deny the ungranted request");
    }
    let grant = CapabilityGrant {
        id: Uuid::now_v7(),
        subject: request.subject.clone(),
        workspace_id: Some(workspace_id),
        capabilities: BTreeSet::from([Capability::WriteArtifact]),
        resource_prefixes: vec!["artifact".to_string()],
        max_risk: RiskLevel::LocalWrite,
        max_sensitivity: SensitivityLevel::Confidential,
        expires_at_unix_ms: Some(request.now_unix_ms.saturating_add(60_000)),
        requires_approval: true,
        delegable: false,
        parent_grant_id: None,
        labels: BTreeSet::from(["compiled-smoke".to_string()]),
    };
    policy.add_grant(grant.clone())?;
    let approval_required = policy.evaluate(&request);
    if approval_required.effect != PolicyEffect::ApprovalRequired {
        bail!("policy did not require approval for the bounded write");
    }
    let approval = ApprovalEvidence::for_request(
        grant.id,
        &request,
        "user:smoke",
        request.now_unix_ms,
        request.now_unix_ms.saturating_add(30_000),
    )?;
    let mut approved_request = request.clone();
    approved_request.approval = Some(approval.clone());
    let approved = policy.evaluate(&approved_request);
    if !approved.allowed() || approved.approval_id != Some(approval.id) {
        bail!("matching bounded approval did not authorize the request");
    }

    let report = json!({
        "schemaVersion":"soleaux.vault-smoke/v1",
        "workspaceId":workspace_id,
        "otherWorkspaceId":other_workspace_id,
        "contentHash":descriptor.content_hash,
        "mediaType":descriptor.media_type,
        "plaintextBytes":plaintext.len(),
        "encryptedBytes":raw_after_rotation.len(),
        "plaintextAbsentAtRest":true,
        "metadataRedactions":descriptor.metadata_redactions,
        "workspaceIsolation":true,
        "tamperRejected":tamper_rejected,
        "previousKeyVersion":rotation.previous_key_version,
        "currentKeyVersion":rotation.current_key_version,
        "rotatedArtifacts":rotation.rotated_artifacts,
        "verification":verification,
        "defaultPolicyEffect":default_decision.effect,
        "approvalRequiredEffect":approval_required.effect,
        "approvedPolicyEffect":approved.effect,
        "approvalId":approval.id,
        "productionClaimAllowed":false,
        "status":"pass",
    });
    fs::write(
        output.join("vault-smoke.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

fn unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}
