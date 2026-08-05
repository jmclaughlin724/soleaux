use super::*;
use serde_json::json;
use std::{
    collections::BTreeSet,
    fs,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tempfile::tempdir;
use uuid::Uuid;

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

#[test]
fn encrypted_artifacts_are_content_addressed_redacted_and_workspace_bound() {
    let directory = tempdir().expect("tempdir");
    let keys = Arc::new(MemoryKeyStore::default());
    let vault = ArtifactVault::open(directory.path().join("vault"), keys).expect("vault");
    let workspace_id = Uuid::now_v7();
    let other_workspace = Uuid::now_v7();
    let plaintext = b"artifact payload that must never be stored in clear text";

    let descriptor = vault
        .put(
            workspace_id,
            "application/json",
            plaintext,
            ArtifactSensitivity::Confidential,
            json!({
                "source":"fixture",
                "authorization":"Bearer secret-token-value",
                "nested":{"api_key":"sk-live-fixture-secret"},
            }),
        )
        .expect("put");
    assert_eq!(descriptor.content_hash, blake3::hash(plaintext).to_hex().to_string());
    assert!(descriptor.encrypted);
    assert!(descriptor.metadata_redactions >= 2);
    assert_eq!(descriptor.metadata["authorization"], "[REDACTED]");
    assert_eq!(descriptor.metadata["nested"]["api_key"], "[REDACTED]");

    let raw = fs::read(&descriptor.storage_path).expect("raw envelope");
    assert!(!raw.windows(plaintext.len()).any(|window| window == plaintext));
    let opened = vault
        .read(workspace_id, &descriptor.content_hash)
        .expect("read");
    assert_eq!(opened.bytes, plaintext);
    assert_eq!(opened.descriptor, descriptor);

    let replay = vault
        .put(
            workspace_id,
            "application/json",
            plaintext,
            ArtifactSensitivity::Confidential,
            json!({"ignored":"content address already owns the envelope"}),
        )
        .expect("idempotent put");
    assert_eq!(replay, descriptor);

    let cross_workspace = vault
        .read(other_workspace, &descriptor.content_hash)
        .expect_err("cross-workspace read must fail");
    assert!(format!("{cross_workspace:#}").contains("reading encrypted artifact"));

    let verification = vault.verify_workspace(workspace_id).expect("verify");
    assert_eq!(verification.artifact_count, 1);
    assert_eq!(verification.plaintext_bytes, plaintext.len() as u64);
    assert_eq!(verification.key_versions, vec![1]);
}

#[test]
fn authenticated_envelopes_reject_tampering_and_rotation_preserves_plaintext() {
    let directory = tempdir().expect("tempdir");
    let keys = Arc::new(MemoryKeyStore::default());
    let vault = ArtifactVault::open(directory.path().join("vault"), keys).expect("vault");
    let workspace_id = Uuid::now_v7();
    let first = vault
        .put(
            workspace_id,
            "text/plain",
            b"first",
            ArtifactSensitivity::Internal,
            json!({}),
        )
        .expect("first");
    let second = vault
        .put(
            workspace_id,
            "text/plain",
            b"second",
            ArtifactSensitivity::Secret,
            json!({"password":"secret"}),
        )
        .expect("second");

    let rotation = vault.rotate_workspace_key(workspace_id).expect("rotate");
    assert_eq!(rotation.previous_key_version, 1);
    assert_eq!(rotation.current_key_version, 2);
    assert_eq!(rotation.rotated_artifacts, 2);
    assert_eq!(
        vault
            .read(workspace_id, &first.content_hash)
            .expect("first after rotation")
            .bytes,
        b"first"
    );
    assert_eq!(
        vault
            .read(workspace_id, &second.content_hash)
            .expect("second after rotation")
            .bytes,
        b"second"
    );
    assert_eq!(
        vault
            .verify_workspace(workspace_id)
            .expect("verify")
            .key_versions,
        vec![2]
    );

    let mut envelope = fs::read(&first.storage_path).expect("envelope");
    let last = envelope.last_mut().expect("ciphertext byte");
    *last ^= 0x80;
    fs::write(&first.storage_path, envelope).expect("tamper");
    let error = vault
        .read(workspace_id, &first.content_hash)
        .expect_err("tamper must fail authentication");
    assert!(format!("{error:#}").contains("authentication failed"));
}

#[test]
fn explicit_file_key_store_persists_versions_without_plaintext_artifacts() {
    let directory = tempdir().expect("tempdir");
    let key_path = directory.path().join("keys").join("vault.json");
    let store = FileKeyStore::new(&key_path);
    let mut ring = load_or_create(&store).expect("key ring");
    assert_eq!(ring.current_version(), 1);
    assert_eq!(ring.rotate().expect("rotate"), 2);
    store.save(&ring).expect("save");
    let restored = store.load().expect("load").expect("stored key ring");
    assert_eq!(restored.current_version(), 2);
    assert_eq!(restored.versions().collect::<Vec<_>>(), vec![1, 2]);
    let encoded = fs::read_to_string(&key_path).expect("encoded key ring");
    assert!(encoded.contains("soleaux.vault-keyring/v1"));
    assert!(!encoded.contains("["));

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(fs::metadata(&key_path).expect("metadata").permissions().mode() & 0o777, 0o600);
    }
}

fn grant(
    subject: &str,
    workspace_id: Option<Uuid>,
    capabilities: &[Capability],
) -> CapabilityGrant {
    CapabilityGrant {
        id: Uuid::now_v7(),
        subject: subject.to_string(),
        workspace_id,
        capabilities: capabilities.iter().copied().collect(),
        resource_prefixes: vec!["artifact".to_string()],
        max_risk: RiskLevel::LocalWrite,
        max_sensitivity: SensitivityLevel::Confidential,
        expires_at_unix_ms: Some(now_ms().saturating_add(60_000)),
        requires_approval: false,
        delegable: true,
        parent_grant_id: None,
        labels: BTreeSet::from(["fixture".to_string()]),
    }
}

#[test]
fn policy_is_deny_by_default_workspace_scoped_and_approval_aware() {
    let workspace_id = Uuid::now_v7();
    let other_workspace = Uuid::now_v7();
    let mut engine = PolicyEngine::new();
    let request = CapabilityRequest {
        subject: "desktop:fixture".to_string(),
        workspace_id,
        capability: Capability::ReadArtifact,
        resource: "artifact/abc123".to_string(),
        risk: RiskLevel::ReadOnly,
        sensitivity: SensitivityLevel::Internal,
        now_unix_ms: now_ms(),
        approval: None,
    };
    assert_eq!(engine.evaluate(&request).effect, PolicyEffect::Deny);

    let read_grant = grant(
        "desktop:fixture",
        Some(workspace_id),
        &[Capability::ReadArtifact],
    );
    engine.add_grant(read_grant.clone()).expect("grant");
    assert!(engine.evaluate(&request).allowed());

    let mut other = request.clone();
    other.workspace_id = other_workspace;
    assert_eq!(engine.evaluate(&other).effect, PolicyEffect::Deny);
    other.workspace_id = workspace_id;
    other.sensitivity = SensitivityLevel::Secret;
    assert_eq!(engine.evaluate(&other).effect, PolicyEffect::Deny);

    let mut write_grant = grant(
        "desktop:fixture",
        Some(workspace_id),
        &[Capability::WriteArtifact],
    );
    write_grant.requires_approval = true;
    engine.add_grant(write_grant.clone()).expect("write grant");
    let mut write = request.clone();
    write.capability = Capability::WriteArtifact;
    write.risk = RiskLevel::LocalWrite;
    let decision = engine.evaluate(&write);
    assert_eq!(decision.effect, PolicyEffect::ApprovalRequired);
    let approval = ApprovalEvidence::for_request(
        write_grant.id,
        &write,
        "user:john",
        write.now_unix_ms,
        write.now_unix_ms.saturating_add(30_000),
    )
    .expect("approval");
    write.approval = Some(approval.clone());
    let decision = engine.evaluate(&write);
    assert!(decision.allowed());
    assert_eq!(decision.approval_id, Some(approval.id));
}

#[test]
fn delegated_capabilities_must_be_strictly_attenuated() {
    let workspace_id = Uuid::now_v7();
    let mut engine = PolicyEngine::new();
    let parent = grant(
        "daemon:fixture",
        Some(workspace_id),
        &[Capability::ReadArtifact, Capability::WriteArtifact],
    );
    engine.add_grant(parent.clone()).expect("parent");

    let mut child = CapabilityGrant {
        id: Uuid::now_v7(),
        subject: parent.subject.clone(),
        workspace_id: parent.workspace_id,
        capabilities: BTreeSet::from([Capability::ReadArtifact]),
        resource_prefixes: vec!["artifact/public".to_string()],
        max_risk: RiskLevel::ReadOnly,
        max_sensitivity: SensitivityLevel::Internal,
        expires_at_unix_ms: parent.expires_at_unix_ms.map(|value| value - 1),
        requires_approval: false,
        delegable: false,
        parent_grant_id: Some(parent.id),
        labels: BTreeSet::new(),
    };
    engine.add_grant(child.clone()).expect("attenuated child");

    child.id = Uuid::now_v7();
    child.capabilities.insert(Capability::RunCommand);
    let error = engine
        .add_grant(child)
        .expect_err("broadened delegation must fail");
    assert!(format!("{error:#}").contains("subset"));

    let revoked = engine.revoke_grant(parent.id);
    assert!(revoked);
    assert!(engine.grants().is_empty());
}
