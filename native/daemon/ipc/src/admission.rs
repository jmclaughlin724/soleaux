//! Daemon-trusted admission receipts for external write paths.
//!
//! The daemon is the only issuer and the only verifier: a receipt is a keyed
//! BLAKE3 MAC over every bound field, keyed by an admission key derived on
//! demand from the vault key ring behind the boot-time key-store handle. A
//! caller-computed hash or probe report is never authorization; presenting a
//! receipt the daemon did not mint fails verification. Keystore unavailability
//! fails closed with a typed error.

use crate::{
    compatibility::{admission_matrix_entry, client_capability_matrix_sha256},
    registry::{CLIENT_PROTOCOL_VERSION, bounded_response, unix_ms},
};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use soleaux_state::{ClientRegistrationPayload, StateStore, WorkspacePayload};
use soleaux_vault::{KeyRing, KeyStore, load_or_create};
use uuid::Uuid;

pub const ADMISSION_RECEIPT_SCHEMA_VERSION: &str = "soleaux.admission-receipt/v1";
const ADMISSION_ISSUE_SCHEMA_VERSION: &str = "soleaux.admission-issue/v1";
const ADMISSION_VERIFY_SCHEMA_VERSION: &str = "soleaux.admission-verify/v1";
const ADMISSION_MIN_TTL_MS: u64 = 5_000;
const ADMISSION_MAX_TTL_MS: u64 = 86_400_000;

/// A daemon-issued admission receipt. Every field participates in the MAC, so
/// any mutation after issuance fails verification.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AdmissionReceipt {
    pub schema_version: String,
    pub client_id: Uuid,
    pub platform: String,
    pub client_version: String,
    pub matrix_sha256: String,
    pub workspace_id: Uuid,
    pub issued_at_unix_ms: i64,
    pub expires_at_unix_ms: i64,
    pub probe_evidence_sha256: String,
    pub key_version: u32,
    pub mac: String,
}

#[derive(Debug)]
pub enum AdmissionError {
    /// The daemon keystore could not provide the admission key ring.
    KeyStoreUnavailable(String),
    /// An issuance precondition failed before any key material was touched.
    Refused(String),
    /// A presented receipt failed verification.
    Rejected(&'static str),
}

impl std::fmt::Display for AdmissionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::KeyStoreUnavailable(detail) => {
                write!(formatter, "admission key store is unavailable: {detail}")
            }
            Self::Refused(detail) => write!(formatter, "admission issuance refused: {detail}"),
            Self::Rejected(reason) => write!(formatter, "admission receipt rejected: {reason}"),
        }
    }
}

impl std::error::Error for AdmissionError {}

pub(crate) fn issue(
    state: &StateStore,
    key_store: &dyn KeyStore,
    client_id: Uuid,
    workspace_id: Uuid,
    probe_evidence_sha256: &str,
    ttl_ms: u64,
) -> Result<Value> {
    let receipt = issue_receipt(
        state,
        key_store,
        client_id,
        workspace_id,
        probe_evidence_sha256,
        ttl_ms,
        unix_ms(),
    )?;
    bounded_response(json!({
        "schemaVersion":ADMISSION_ISSUE_SCHEMA_VERSION,
        "receipt":receipt,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn verify(
    state: &StateStore,
    key_store: &dyn KeyStore,
    receipt: &AdmissionReceipt,
) -> Result<Value> {
    verify_receipt(
        state,
        key_store,
        receipt,
        receipt.client_id,
        receipt.workspace_id,
        unix_ms(),
    )?;
    bounded_response(json!({
        "schemaVersion":ADMISSION_VERIFY_SCHEMA_VERSION,
        "verified":true,
        "clientId":receipt.client_id,
        "workspaceId":receipt.workspace_id,
        "platform":receipt.platform,
        "clientVersion":receipt.client_version,
        "issuedAtUnixMs":receipt.issued_at_unix_ms,
        "expiresAtUnixMs":receipt.expires_at_unix_ms,
        "probeEvidenceSha256":receipt.probe_evidence_sha256,
        "productionClaimAllowed":false,
    }))
}

fn issue_receipt(
    state: &StateStore,
    key_store: &dyn KeyStore,
    client_id: Uuid,
    workspace_id: Uuid,
    probe_evidence_sha256: &str,
    ttl_ms: u64,
    now: i64,
) -> Result<AdmissionReceipt, AdmissionError> {
    if !(ADMISSION_MIN_TTL_MS..=ADMISSION_MAX_TTL_MS).contains(&ttl_ms) {
        return Err(AdmissionError::Refused(format!(
            "admission ttl must be between {ADMISSION_MIN_TTL_MS} and {ADMISSION_MAX_TTL_MS} milliseconds"
        )));
    }
    if !is_lower_hex_digest(probe_evidence_sha256) {
        return Err(AdmissionError::Refused(
            "probe evidence digest must be a 64-character lowercase hex digest".to_string(),
        ));
    }
    let client = active_client(state, client_id, now).map_err(AdmissionError::Refused)?;
    if client.payload.protocol_version != CLIENT_PROTOCOL_VERSION {
        return Err(AdmissionError::Refused(
            "client registration does not speak the supported client protocol".to_string(),
        ));
    }
    let platform_id = client
        .payload
        .metadata
        .get("platform")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            AdmissionError::Refused(
                "client registration does not declare a capability matrix platform".to_string(),
            )
        })?;
    let entry = admission_matrix_entry(
        client.payload.client_kind,
        &client.payload.client_version,
        platform_id,
    )
    .map_err(|error| AdmissionError::Refused(format!("{error:#}")))?;
    active_workspace(state, workspace_id).map_err(AdmissionError::Refused)?;
    let expires_at_unix_ms = i64::try_from(ttl_ms)
        .ok()
        .and_then(|ttl| now.checked_add(ttl))
        .ok_or_else(|| AdmissionError::Refused("admission expiry overflow".to_string()))?;
    let key_ring = load_or_create(key_store)
        .map_err(|error| AdmissionError::KeyStoreUnavailable(format!("{error:#}")))?;
    let key_version = key_ring.current_version();
    let mut receipt = AdmissionReceipt {
        schema_version: ADMISSION_RECEIPT_SCHEMA_VERSION.to_string(),
        client_id,
        platform: entry.platform,
        client_version: client.payload.client_version.clone(),
        matrix_sha256: entry.matrix_sha256,
        workspace_id,
        issued_at_unix_ms: now,
        expires_at_unix_ms,
        probe_evidence_sha256: probe_evidence_sha256.to_string(),
        key_version,
        mac: String::new(),
    };
    receipt.mac = expected_mac(&key_ring, &receipt)?.to_hex().to_string();
    Ok(receipt)
}

pub(crate) fn verify_receipt(
    state: &StateStore,
    key_store: &dyn KeyStore,
    receipt: &AdmissionReceipt,
    expected_client_id: Uuid,
    expected_workspace_id: Uuid,
    now: i64,
) -> Result<(), AdmissionError> {
    if receipt.schema_version != ADMISSION_RECEIPT_SCHEMA_VERSION {
        return Err(AdmissionError::Rejected(
            "unsupported admission receipt schema",
        ));
    }
    let key_ring = match key_store.load() {
        Ok(Some(key_ring)) => key_ring,
        Ok(None) => {
            return Err(AdmissionError::Rejected(
                "the daemon admission key ring does not exist",
            ));
        }
        Err(error) => return Err(AdmissionError::KeyStoreUnavailable(format!("{error:#}"))),
    };
    let presented = blake3::Hash::from_hex(receipt.mac.as_bytes())
        .map_err(|_| AdmissionError::Rejected("the admission receipt MAC is malformed"))?;
    // Constant-time comparison through blake3::Hash equality.
    if expected_mac(&key_ring, receipt)? != presented {
        return Err(AdmissionError::Rejected(
            "the admission receipt MAC does not verify",
        ));
    }
    if receipt.client_id != expected_client_id {
        return Err(AdmissionError::Rejected(
            "the admission receipt does not name this client",
        ));
    }
    if receipt.workspace_id != expected_workspace_id {
        return Err(AdmissionError::Rejected(
            "the admission receipt does not name this workspace",
        ));
    }
    if receipt.matrix_sha256 != client_capability_matrix_sha256() {
        return Err(AdmissionError::Rejected(
            "the admission receipt matrix digest does not match the embedded capability matrix",
        ));
    }
    if receipt.issued_at_unix_ms > now {
        return Err(AdmissionError::Rejected(
            "the admission receipt is not yet valid",
        ));
    }
    if receipt.expires_at_unix_ms <= now {
        return Err(AdmissionError::Rejected("the admission receipt is expired"));
    }
    if !is_lower_hex_digest(&receipt.probe_evidence_sha256) {
        return Err(AdmissionError::Rejected(
            "the admission receipt probe evidence digest is malformed",
        ));
    }
    let client = active_client(state, receipt.client_id, now).map_err(|_| {
        AdmissionError::Rejected("the admission receipt client registration is not active")
    })?;
    if client.payload.client_version != receipt.client_version {
        return Err(AdmissionError::Rejected(
            "the admission receipt does not match the registered client version",
        ));
    }
    let platform_id = client
        .payload
        .metadata
        .get("platform")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if platform_id != receipt.platform {
        return Err(AdmissionError::Rejected(
            "the admission receipt does not match the registered client platform",
        ));
    }
    admission_matrix_entry(
        client.payload.client_kind,
        &receipt.client_version,
        &receipt.platform,
    )
    .map_err(|_| {
        AdmissionError::Rejected("the admission receipt is not an exact capability matrix entry")
    })?;
    active_workspace(state, receipt.workspace_id).map_err(|_| {
        AdmissionError::Rejected("the admission receipt workspace is not registered")
    })?;
    Ok(())
}

fn expected_mac(
    key_ring: &KeyRing,
    receipt: &AdmissionReceipt,
) -> Result<blake3::Hash, AdmissionError> {
    let key = key_ring
        .derive_admission_key(receipt.key_version)
        .map_err(|_| AdmissionError::Rejected("the admission key version is unavailable"))?;
    Ok(blake3::keyed_hash(&key, &mac_input(receipt)))
}

fn mac_input(receipt: &AdmissionReceipt) -> Vec<u8> {
    let mut input = Vec::with_capacity(320);
    push_field(&mut input, receipt.schema_version.as_bytes());
    push_field(&mut input, receipt.client_id.as_bytes());
    push_field(&mut input, receipt.platform.as_bytes());
    push_field(&mut input, receipt.client_version.as_bytes());
    push_field(&mut input, receipt.matrix_sha256.as_bytes());
    push_field(&mut input, receipt.workspace_id.as_bytes());
    push_field(&mut input, &receipt.issued_at_unix_ms.to_le_bytes());
    push_field(&mut input, &receipt.expires_at_unix_ms.to_le_bytes());
    push_field(&mut input, receipt.probe_evidence_sha256.as_bytes());
    push_field(&mut input, &receipt.key_version.to_le_bytes());
    input
}

// Length-prefixed encoding: no field boundary can be shifted between fields.
fn push_field(input: &mut Vec<u8>, bytes: &[u8]) {
    input.extend_from_slice(&(bytes.len() as u64).to_le_bytes());
    input.extend_from_slice(bytes);
}

fn active_client(
    state: &StateStore,
    client_id: Uuid,
    now: i64,
) -> Result<soleaux_state::CanonicalRecord<ClientRegistrationPayload>, String> {
    let record = state
        .get::<ClientRegistrationPayload>(client_id)
        .map_err(|error| format!("reading the client registration: {error:#}"))?
        .ok_or_else(|| "client registration does not exist".to_string())?;
    if record.tombstoned_at_unix_ms.is_some()
        || record.state != "connected"
        || record
            .expires_at_unix_ms
            .is_some_and(|expires| expires <= now)
    {
        return Err("client registration is not active".to_string());
    }
    Ok(record)
}

fn active_workspace(state: &StateStore, workspace_id: Uuid) -> Result<(), String> {
    let record = state
        .get::<WorkspacePayload>(workspace_id)
        .map_err(|error| format!("reading the workspace registration: {error:#}"))?
        .ok_or_else(|| "workspace registration does not exist".to_string())?;
    if record.tombstoned_at_unix_ms.is_some() || record.state != "registered" {
        return Err("workspace registration is not active".to_string());
    }
    Ok(())
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry;
    use serde_json::json;
    use soleaux_state::{ClientKind, WorkspaceTrustState};
    use soleaux_vault::MemoryKeyStore;
    use std::fs;
    use tempfile::TempDir;

    const EXTERNAL_PLATFORM: &str = "generic_mcp_host";
    const EXTERNAL_VERSION: &str = "mcp-2025-11-25";

    #[derive(Debug)]
    struct FailingKeyStore;

    impl KeyStore for FailingKeyStore {
        fn load(&self) -> Result<Option<KeyRing>> {
            anyhow::bail!("fixture keychain offline")
        }

        fn save(&self, _key_ring: &KeyRing) -> Result<()> {
            anyhow::bail!("fixture keychain offline")
        }
    }

    fn fixture_state() -> (TempDir, StateStore) {
        let directory = TempDir::new().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        (directory, state)
    }

    fn register_fixture_workspace(directory: &TempDir, state: &StateStore, name: &str) -> Uuid {
        let path = directory.path().join(name);
        fs::create_dir_all(&path).expect("workspace directory");
        let value = registry::register_workspace(
            state,
            path.to_str().expect("utf8"),
            Some(name.to_string()),
            WorkspaceTrustState::Trusted,
            json!({}),
        )
        .expect("workspace registration");
        serde_json::from_value(value["workspace"]["id"].clone()).expect("workspace id")
    }

    fn register_external_client(state: &StateStore, instance: &str) -> Uuid {
        let value = registry::register_client(
            state,
            ClientKind::Adapter,
            instance.to_string(),
            format!("fixture {instance}"),
            EXTERNAL_VERSION.to_string(),
            CLIENT_PROTOCOL_VERSION.to_string(),
            60_000,
            json!({}),
            json!({"platform":EXTERNAL_PLATFORM}),
        )
        .expect("external client registration");
        assert_eq!(value["writeCapable"], false);
        serde_json::from_value(value["client"]["id"].clone()).expect("client id")
    }

    fn resign(key_store: &dyn KeyStore, receipt: &mut AdmissionReceipt) {
        let key_ring = load_or_create(key_store).expect("key ring");
        receipt.mac = expected_mac(&key_ring, receipt)
            .expect("mac")
            .to_hex()
            .to_string();
    }

    fn rejection(error: &AdmissionError) -> &'static str {
        match error {
            AdmissionError::Rejected(reason) => reason,
            other => panic!("expected a rejection, got {other:?}"),
        }
    }

    #[test]
    fn issue_and_verify_round_trip_binds_every_field() {
        let (directory, state) = fixture_state();
        let workspace_id = register_fixture_workspace(&directory, &state, "workspace");
        let client_id = register_external_client(&state, "round-trip");
        let key_store = MemoryKeyStore::default();
        let now = unix_ms();
        let evidence = "a".repeat(64);

        let receipt = issue_receipt(
            &state,
            &key_store,
            client_id,
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect("issue");
        assert_eq!(receipt.schema_version, ADMISSION_RECEIPT_SCHEMA_VERSION);
        assert_eq!(receipt.platform, EXTERNAL_PLATFORM);
        assert_eq!(receipt.client_version, EXTERNAL_VERSION);
        assert_eq!(receipt.matrix_sha256, client_capability_matrix_sha256());
        assert_eq!(receipt.expires_at_unix_ms, now + 60_000);
        assert_eq!(receipt.probe_evidence_sha256, evidence);

        verify_receipt(&state, &key_store, &receipt, client_id, workspace_id, now)
            .expect("verify for binding");
        let report = verify(&state, &key_store, &receipt).expect("verify report");
        assert_eq!(report["schemaVersion"], ADMISSION_VERIFY_SCHEMA_VERSION);
        assert_eq!(report["verified"], true);
        assert_eq!(report["platform"], EXTERNAL_PLATFORM);
        assert_eq!(report["productionClaimAllowed"], false);

        let encoded = serde_json::to_value(&receipt).expect("encode receipt");
        let decoded: AdmissionReceipt = serde_json::from_value(encoded).expect("decode receipt");
        assert_eq!(decoded, receipt);
    }

    #[test]
    fn issuance_refuses_unknown_clients_internal_cli_and_malformed_inputs() {
        let (directory, state) = fixture_state();
        let workspace_id = register_fixture_workspace(&directory, &state, "workspace");
        let client_id = register_external_client(&state, "refusals");
        let key_store = MemoryKeyStore::default();
        let now = unix_ms();
        let evidence = "b".repeat(64);

        let unknown = issue_receipt(
            &state,
            &key_store,
            Uuid::now_v7(),
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect_err("unknown client");
        assert!(matches!(unknown, AdmissionError::Refused(_)));

        let cli_value = registry::register_client(
            &state,
            ClientKind::Cli,
            "internal-cli".to_string(),
            "internal CLI".to_string(),
            env!("CARGO_PKG_VERSION").to_string(),
            CLIENT_PROTOCOL_VERSION.to_string(),
            60_000,
            json!({}),
            json!({}),
        )
        .expect("cli registration");
        let cli_id: Uuid =
            serde_json::from_value(cli_value["client"]["id"].clone()).expect("cli id");
        let cli = issue_receipt(
            &state,
            &key_store,
            cli_id,
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect_err("internal CLI does not take receipts");
        assert!(format!("{cli}").contains("capability matrix platform"));

        let bad_ttl = issue_receipt(
            &state,
            &key_store,
            client_id,
            workspace_id,
            &evidence,
            1_000,
            now,
        )
        .expect_err("ttl below the floor");
        assert!(format!("{bad_ttl}").contains("admission ttl"));

        let bad_digest = issue_receipt(
            &state,
            &key_store,
            client_id,
            workspace_id,
            "not-a-digest",
            60_000,
            now,
        )
        .expect_err("malformed probe digest");
        assert!(format!("{bad_digest}").contains("probe evidence digest"));

        let bad_workspace = issue_receipt(
            &state,
            &key_store,
            client_id,
            Uuid::now_v7(),
            &evidence,
            60_000,
            now,
        )
        .expect_err("unknown workspace");
        assert!(format!("{bad_workspace}").contains("workspace registration"));
    }

    #[test]
    fn forged_and_tampered_receipts_are_rejected() {
        let (directory, state) = fixture_state();
        let workspace_id = register_fixture_workspace(&directory, &state, "workspace");
        let client_id = register_external_client(&state, "forgery");
        let key_store = MemoryKeyStore::default();
        let now = unix_ms();
        let evidence = "c".repeat(64);
        let receipt = issue_receipt(
            &state,
            &key_store,
            client_id,
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect("issue");

        let mut foreign = receipt.clone();
        let foreign_store = MemoryKeyStore::default();
        resign(&foreign_store, &mut foreign);
        let error = verify_receipt(&state, &key_store, &foreign, client_id, workspace_id, now)
            .expect_err("a MAC minted under another key is a forgery");
        assert_eq!(
            rejection(&error),
            "the admission receipt MAC does not verify"
        );

        let mut flipped = receipt.clone();
        let mut mac = flipped.mac.into_bytes();
        mac[0] = if mac[0] == b'0' { b'1' } else { b'0' };
        flipped.mac = String::from_utf8(mac).expect("hex mac");
        let error = verify_receipt(&state, &key_store, &flipped, client_id, workspace_id, now)
            .expect_err("a flipped MAC bit must fail");
        assert_eq!(
            rejection(&error),
            "the admission receipt MAC does not verify"
        );

        let mut extended = receipt.clone();
        extended.expires_at_unix_ms += 86_400_000;
        let error = verify_receipt(&state, &key_store, &extended, client_id, workspace_id, now)
            .expect_err("a tampered expiry must fail the MAC");
        assert_eq!(
            rejection(&error),
            "the admission receipt MAC does not verify"
        );

        let mut stale_matrix = receipt.clone();
        stale_matrix.matrix_sha256 = "d".repeat(64);
        resign(&key_store, &mut stale_matrix);
        let error = verify_receipt(
            &state,
            &key_store,
            &stale_matrix,
            client_id,
            workspace_id,
            now,
        )
        .expect_err("a stale matrix digest must fail after the MAC");
        assert_eq!(
            rejection(&error),
            "the admission receipt matrix digest does not match the embedded capability matrix"
        );

        let mut wrong_platform = receipt.clone();
        wrong_platform.platform = "claude_code".to_string();
        resign(&key_store, &mut wrong_platform);
        let error = verify_receipt(
            &state,
            &key_store,
            &wrong_platform,
            client_id,
            workspace_id,
            now,
        )
        .expect_err("a receipt for another platform must fail");
        assert_eq!(
            rejection(&error),
            "the admission receipt does not match the registered client platform"
        );

        let mut unknown_key = receipt.clone();
        unknown_key.key_version = 99;
        let error = verify_receipt(
            &state,
            &key_store,
            &unknown_key,
            client_id,
            workspace_id,
            now,
        )
        .expect_err("an unknown key version must fail");
        assert_eq!(
            rejection(&error),
            "the admission key version is unavailable"
        );
    }

    #[test]
    fn expired_wrong_party_and_inactive_client_receipts_are_rejected() {
        let (directory, state) = fixture_state();
        let workspace_id = register_fixture_workspace(&directory, &state, "workspace");
        let other_workspace = register_fixture_workspace(&directory, &state, "other");
        let client_id = register_external_client(&state, "expiry");
        let other_client = register_external_client(&state, "other-client");
        let key_store = MemoryKeyStore::default();
        let now = unix_ms();
        let evidence = "e".repeat(64);
        let receipt = issue_receipt(
            &state,
            &key_store,
            client_id,
            workspace_id,
            &evidence,
            5_000,
            now,
        )
        .expect("issue");

        let error = verify_receipt(
            &state,
            &key_store,
            &receipt,
            client_id,
            workspace_id,
            now + 5_000,
        )
        .expect_err("expired");
        assert_eq!(rejection(&error), "the admission receipt is expired");

        let error = verify_receipt(
            &state,
            &key_store,
            &receipt,
            client_id,
            workspace_id,
            now - 1,
        )
        .expect_err("issued in the future");
        assert_eq!(rejection(&error), "the admission receipt is not yet valid");

        let error = verify_receipt(
            &state,
            &key_store,
            &receipt,
            client_id,
            other_workspace,
            now,
        )
        .expect_err("wrong workspace");
        assert_eq!(
            rejection(&error),
            "the admission receipt does not name this workspace"
        );

        let error = verify_receipt(
            &state,
            &key_store,
            &receipt,
            other_client,
            workspace_id,
            now,
        )
        .expect_err("wrong client");
        assert_eq!(
            rejection(&error),
            "the admission receipt does not name this client"
        );

        registry::disconnect_client(&state, client_id).expect("disconnect");
        let error = verify_receipt(&state, &key_store, &receipt, client_id, workspace_id, now)
            .expect_err("disconnected client");
        assert_eq!(
            rejection(&error),
            "the admission receipt client registration is not active"
        );
    }

    #[test]
    fn keystore_unavailability_fails_closed_with_a_typed_error() {
        let (directory, state) = fixture_state();
        let workspace_id = register_fixture_workspace(&directory, &state, "workspace");
        let client_id = register_external_client(&state, "keystore");
        let now = unix_ms();
        let evidence = "f".repeat(64);

        let error = issue_receipt(
            &state,
            &FailingKeyStore,
            client_id,
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect_err("issuance without a keystore must fail closed");
        assert!(matches!(error, AdmissionError::KeyStoreUnavailable(_)));

        let working = MemoryKeyStore::default();
        let receipt = issue_receipt(
            &state,
            &working,
            client_id,
            workspace_id,
            &evidence,
            60_000,
            now,
        )
        .expect("issue");
        let error = verify_receipt(
            &state,
            &FailingKeyStore,
            &receipt,
            client_id,
            workspace_id,
            now,
        )
        .expect_err("verification without a keystore must fail closed");
        assert!(matches!(error, AdmissionError::KeyStoreUnavailable(_)));
        let typed = anyhow::Error::from(error);
        assert!(matches!(
            typed.downcast_ref::<AdmissionError>(),
            Some(AdmissionError::KeyStoreUnavailable(_))
        ));

        let empty = MemoryKeyStore::default();
        let error = verify_receipt(&state, &empty, &receipt, client_id, workspace_id, now)
            .expect_err("an empty keystore has issued nothing");
        assert_eq!(
            rejection(&error),
            "the daemon admission key ring does not exist"
        );
    }
}
