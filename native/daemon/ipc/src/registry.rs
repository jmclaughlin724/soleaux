use crate::{
    IPC_MAX_FRAME_BYTES,
    compatibility::{client_capability_matrix_summary, evaluate_client_compatibility},
};
use anyhow::{Context, Result, bail};
use serde::Serialize;
use serde_json::{Value, json};
#[cfg(test)]
use soleaux_state::ClientCompatibilityState;
use soleaux_state::{
    CanonicalEntityInput, ClientAccessMode, ClientKind, ClientRegistrationPayload,
    ClientRegistrationResult, ClientWorkspaceBindingPayload, LOCKED_CONTEXT_PACKET_SHA256,
    LOCKED_PROFILE_SHA256, PUBLIC_TOOL_CEILING, REGISTRY_JSON_FIELD_MAX_BYTES,
    REGISTRY_PAGE_LIMIT_DEFAULT, REGISTRY_TEXT_FIELD_MAX_BYTES, StateStore, WorkspacePayload,
    WorkspaceTrustState,
};
use std::{
    fs,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub const REGISTRY_SCHEMA_VERSION: &str = "soleaux.workspace-registry/v1";
pub const CLIENT_PROTOCOL_VERSION: &str = "soleaux.client/v1";
const WORKSPACE_ORIGIN: &str = "soleaux.workspace";
const CLIENT_ORIGIN: &str = "soleaux.client";
const BINDING_ORIGIN: &str = "soleaux.client-workspace";
const MIN_CLIENT_TTL_MS: u64 = 5_000;
const MAX_CLIENT_TTL_MS: u64 = 86_400_000;
const REGISTRY_RESPONSE_RESERVE_BYTES: usize = 64 * 1024;
const REGISTRY_MUTATION_CHILDREN_MAX_BYTES: usize = IPC_MAX_FRAME_BYTES / 2;

#[allow(clippy::too_many_arguments)]
pub(crate) fn status(
    state: &StateStore,
    include_stale: bool,
    limit: usize,
    workspace_cursor: Option<Uuid>,
    client_cursor: Option<Uuid>,
    binding_cursor: Option<Uuid>,
) -> Result<Value> {
    let snapshot = state.registry_snapshot(
        include_stale,
        limit,
        workspace_cursor,
        client_cursor,
        binding_cursor,
        unix_ms(),
    )?;
    bounded_response(json!({
        "schemaVersion":REGISTRY_SCHEMA_VERSION,
        "clientProtocolVersion":CLIENT_PROTOCOL_VERSION,
        "clientCapabilityMatrix":client_capability_matrix_summary()?,
        "workspaces":snapshot.workspaces.items,
        "clients":snapshot.clients.items,
        "bindings":snapshot.bindings.items,
        "pagination":{
            "limit":limit,
            "workspaces":{
                "nextCursor":snapshot.workspaces.next_cursor,
                "truncated":snapshot.workspaces.truncated,
            },
            "clients":{
                "nextCursor":snapshot.clients.next_cursor,
                "truncated":snapshot.clients.truncated,
            },
            "bindings":{
                "nextCursor":snapshot.bindings.next_cursor,
                "truncated":snapshot.bindings.truncated,
            },
        },
        "supportedClientKinds":ClientKind::ALL,
        "publicToolCeiling":PUBLIC_TOOL_CEILING,
        "profileDigest":LOCKED_PROFILE_SHA256,
        "contextDigest":LOCKED_CONTEXT_PACKET_SHA256,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn register_workspace(
    state: &StateStore,
    path: &str,
    display_name: Option<String>,
    trust_state: WorkspaceTrustState,
    metadata: Value,
) -> Result<Value> {
    validate_text(path, "workspace path")?;
    validate_json_field(&metadata, "workspace metadata")?;
    let canonical = fs::canonicalize(Path::new(path))
        .with_context(|| format!("resolving workspace path {path}"))?;
    if !canonical.is_dir() {
        bail!("workspace path is not a directory");
    }
    let canonical_path = canonical
        .to_str()
        .context("workspace path is not valid UTF-8")?
        .to_owned();
    validate_text(&canonical_path, "canonical workspace path")?;
    let path_hash = blake3::hash(canonical_path.as_bytes()).to_hex().to_string();
    let display_name = display_name
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            canonical
                .file_name()
                .and_then(|value| value.to_str())
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| canonical_path.clone());
    validate_text(&display_name, "workspace display name")?;
    let payload = WorkspacePayload {
        canonical_path,
        path_hash: path_hash.clone(),
        display_name,
        trust_state,
        profile_digest: LOCKED_PROFILE_SHA256.to_string(),
        context_digest: LOCKED_CONTEXT_PACKET_SHA256.to_string(),
        public_tool_ceiling: PUBLIC_TOOL_CEILING,
        production_claim_allowed: false,
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.state = "registered".to_string();
    input.origin_platform = Some(WORKSPACE_ORIGIN.to_string());
    input.native_id = Some(path_hash.clone());
    input.idempotency_key = Some(format!("workspace:{path_hash}"));
    let result = state.registry_register_workspace(input)?;
    let (downgraded_bindings, downgraded_binding_count, downgraded_bindings_truncated) =
        bounded_children(result.downgraded_bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.workspace-registration/v1",
        "workspace":result.workspace,
        "downgradedBindings":downgraded_bindings,
        "downgradedBindingCount":downgraded_binding_count,
        "downgradedBindingsTruncated":downgraded_bindings_truncated,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn list_workspaces(
    state: &StateStore,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    let page = state.registry_workspaces(cursor, limit)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.workspace-list/v1",
        "workspaces":page.items,
        "nextCursor":page.next_cursor,
        "truncated":page.truncated,
        "limit":limit,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn forget_workspace(state: &StateStore, workspace_id: Uuid) -> Result<Value> {
    let result =
        state.registry_forget_workspace(workspace_id, "workspace forgotten", "soleaux-registry")?;
    let (binding_ids, binding_count, bindings_truncated) = bounded_children(result.binding_ids)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.workspace-forget/v1",
        "workspaceId":workspace_id,
        "unboundClientBindings":binding_ids,
        "unboundClientBindingCount":binding_count,
        "unboundClientBindingsTruncated":bindings_truncated,
        "tombstone":result.tombstone,
        "productionClaimAllowed":false,
    }))
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn register_client(
    state: &StateStore,
    client_kind: ClientKind,
    instance_id: String,
    display_name: String,
    client_version: String,
    protocol_version: String,
    ttl_ms: u64,
    capabilities: Value,
    metadata: Value,
) -> Result<Value> {
    validate_ttl(ttl_ms)?;
    validate_text(&instance_id, "client instance id")?;
    validate_text(&display_name, "client display name")?;
    validate_text(&client_version, "client version")?;
    validate_text(&protocol_version, "client protocol version")?;
    validate_json_field(&capabilities, "client capabilities")?;
    validate_json_field(&metadata, "client metadata")?;
    if protocol_version != CLIENT_PROTOCOL_VERSION {
        bail!("unsupported Soleaux client protocol version: {protocol_version}");
    }
    let compatibility = evaluate_client_compatibility(
        client_kind,
        &client_version,
        &protocol_version,
        CLIENT_PROTOCOL_VERSION,
        &capabilities,
        &metadata,
    )?;
    let compatibility_state = compatibility.state;
    let write_capable = compatibility.write_capable;
    let now = unix_ms();
    let native_id = format!("{}:{instance_id}", client_kind.as_str());
    let payload = ClientRegistrationPayload {
        client_kind,
        instance_id,
        display_name,
        client_version,
        protocol_version,
        connection_state: "connected".to_string(),
        compatibility_state,
        write_capable,
        last_seen_at_unix_ms: now,
        capabilities,
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.state = "connected".to_string();
    input.origin_platform = Some(CLIENT_ORIGIN.to_string());
    input.native_id = Some(native_id.clone());
    input.idempotency_key = Some(format!("client:{native_id}"));
    input.expires_at_unix_ms = Some(expiration(now, ttl_ms)?);
    let result = state.registry_register_client(input)?;
    let (bindings, binding_count, bindings_truncated) = bounded_children(result.bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-registration/v1",
        "client":result.client,
        "bindings":bindings,
        "bindingCount":binding_count,
        "bindingsTruncated":bindings_truncated,
        "writeCapable":write_capable,
        "compatibilityState":compatibility_state,
        "compatibility":compatibility,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn heartbeat_client(
    state: &StateStore,
    client_id: Uuid,
    ttl_ms: u64,
    capabilities: Option<Value>,
) -> Result<Value> {
    validate_ttl(ttl_ms)?;
    if let Some(capabilities) = &capabilities {
        validate_json_field(capabilities, "client capabilities")?;
    }
    let (result, compatibility) =
        revalidate_client(state, client_id, capabilities, Some(ttl_ms), true)?;
    let (bindings, binding_count, bindings_truncated) = bounded_children(result.bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-heartbeat/v1",
        "client":result.client,
        "bindings":bindings,
        "bindingCount":binding_count,
        "bindingsTruncated":bindings_truncated,
        "writeCapable":compatibility.write_capable,
        "compatibilityState":compatibility.state,
        "compatibility":compatibility,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn list_clients(
    state: &StateStore,
    include_stale: bool,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    let page = state.registry_clients(include_stale, cursor, limit, unix_ms())?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-list/v1",
        "clients":page.items,
        "nextCursor":page.next_cursor,
        "truncated":page.truncated,
        "limit":limit,
        "supportedClientKinds":ClientKind::ALL,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn list_bindings(
    state: &StateStore,
    include_stale: bool,
    cursor: Option<Uuid>,
    limit: usize,
) -> Result<Value> {
    let page = state.registry_bindings(include_stale, cursor, limit, unix_ms())?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-workspace-binding-list/v1",
        "bindings":page.items,
        "nextCursor":page.next_cursor,
        "truncated":page.truncated,
        "limit":limit,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn disconnect_client(state: &StateStore, client_id: Uuid) -> Result<Value> {
    let result =
        state.registry_disconnect_client(client_id, "client disconnected", "soleaux-registry")?;
    let (binding_ids, binding_count, bindings_truncated) = bounded_children(result.binding_ids)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-disconnect/v1",
        "clientId":client_id,
        "unboundWorkspaceBindings":binding_ids,
        "unboundWorkspaceBindingCount":binding_count,
        "unboundWorkspaceBindingsTruncated":bindings_truncated,
        "tombstone":result.tombstone,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn bind_client_workspace(
    state: &StateStore,
    client_id: Uuid,
    workspace_id: Uuid,
    access_mode: ClientAccessMode,
    capabilities: Value,
    metadata: Value,
) -> Result<Value> {
    validate_json_field(&capabilities, "binding capabilities")?;
    validate_json_field(&metadata, "binding metadata")?;
    let (_result, compatibility) = revalidate_client(state, client_id, None, None, false)?;
    if access_mode == ClientAccessMode::ReadWrite && !compatibility.write_capable {
        bail!(
            "read-write binding requires a currently verified daemon-trusted client compatibility decision"
        );
    }
    let now = unix_ms();
    let native_id = format!("{client_id}:{workspace_id}");
    let payload = ClientWorkspaceBindingPayload {
        client_id,
        workspace_id,
        access_mode,
        binding_state: "bound".to_string(),
        attached_at_unix_ms: now,
        last_seen_at_unix_ms: now,
        capabilities,
        metadata,
    };
    let mut input = CanonicalEntityInput::active(payload);
    input.workspace_id = Some(workspace_id);
    input.parent_id = Some(client_id);
    input.state = "bound".to_string();
    input.origin_platform = Some(BINDING_ORIGIN.to_string());
    input.native_id = Some(native_id.clone());
    input.idempotency_key = Some(format!("binding:{native_id}"));
    input.expires_at_unix_ms = None;
    let record = state.registry_bind_client_workspace(input)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-workspace-binding/v1",
        "binding":record,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn unbind_client_workspace(state: &StateStore, binding_id: Uuid) -> Result<Value> {
    let binding = state
        .get::<ClientWorkspaceBindingPayload>(binding_id)?
        .context("client workspace binding does not exist")?;
    let tombstone = state.tombstone(binding.id, "client unbound", "soleaux-registry")?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-workspace-unbind/v1",
        "bindingId":binding_id,
        "tombstone":tombstone,
        "productionClaimAllowed":false,
    }))
}

fn revalidate_client(
    state: &StateStore,
    client_id: Uuid,
    capabilities: Option<Value>,
    ttl_ms: Option<u64>,
    touch_last_seen: bool,
) -> Result<(
    ClientRegistrationResult,
    crate::compatibility::CompatibilityDecision,
)> {
    let existing = state
        .get::<ClientRegistrationPayload>(client_id)?
        .context("client registration does not exist")?;
    let now = unix_ms();
    if existing.tombstoned_at_unix_ms.is_some()
        || existing.state != "connected"
        || existing
            .expires_at_unix_ms
            .is_some_and(|expires| expires <= now)
    {
        bail!("client registration is not active");
    }

    let mut payload = existing.payload.clone();
    if let Some(capabilities) = capabilities {
        payload.capabilities = capabilities;
    }
    let compatibility = evaluate_client_compatibility(
        payload.client_kind,
        &payload.client_version,
        &payload.protocol_version,
        CLIENT_PROTOCOL_VERSION,
        &payload.capabilities,
        &payload.metadata,
    )?;
    let previous_capabilities = existing.payload.capabilities.clone();
    let previous_state = payload.compatibility_state;
    let previous_write_capable = payload.write_capable;
    payload.compatibility_state = compatibility.state;
    payload.write_capable = compatibility.write_capable;
    payload.connection_state = "connected".to_string();
    if touch_last_seen {
        payload.last_seen_at_unix_ms = now;
    }
    let expires_at_unix_ms = ttl_ms
        .map(|ttl| expiration(now, ttl))
        .transpose()?
        .or(existing.expires_at_unix_ms);
    let changed = touch_last_seen
        || ttl_ms.is_some()
        || payload.capabilities != previous_capabilities
        || previous_state != payload.compatibility_state
        || previous_write_capable != payload.write_capable;
    if !changed {
        return Ok((
            ClientRegistrationResult {
                client: existing,
                bindings: Vec::new(),
            },
            compatibility,
        ));
    }

    let origin_platform = existing
        .origin_platform
        .clone()
        .context("client registration omitted its origin platform")?;
    let native_id = existing
        .native_id
        .clone()
        .context("client registration omitted its native identity")?;
    let input = CanonicalEntityInput {
        id: Some(existing.id),
        workspace_id: existing.workspace_id,
        parent_id: existing.parent_id,
        origin_platform: Some(origin_platform),
        native_id: Some(native_id),
        state: "connected".to_string(),
        sensitivity: existing.sensitivity,
        idempotency_key: existing.idempotency_key.clone(),
        expected_revision: Some(existing.revision),
        expires_at_unix_ms,
        payload,
    };
    let result = state.registry_register_client(input)?;
    Ok((result, compatibility))
}

pub(crate) fn validate_json_field(value: &Value, label: &str) -> Result<()> {
    if !value.is_object() {
        bail!("{label} must be a JSON object");
    }
    let bytes = serde_json::to_vec(value)?;
    if bytes.len() > REGISTRY_JSON_FIELD_MAX_BYTES {
        bail!(
            "{label} exceeds the {} byte registry limit",
            REGISTRY_JSON_FIELD_MAX_BYTES
        );
    }
    Ok(())
}

pub(crate) fn validate_text(value: &str, label: &str) -> Result<()> {
    if value.trim().is_empty() {
        bail!("{label} must be non-empty");
    }
    if value.len() > REGISTRY_TEXT_FIELD_MAX_BYTES {
        bail!(
            "{label} exceeds the {} byte registry limit",
            REGISTRY_TEXT_FIELD_MAX_BYTES
        );
    }
    Ok(())
}

pub(crate) fn bounded_children<T: Serialize>(items: Vec<T>) -> Result<(Vec<T>, usize, bool)> {
    let total = items.len();
    let mut bounded = Vec::new();
    let mut encoded_bytes = 2usize;
    for item in items {
        if bounded.len() >= REGISTRY_PAGE_LIMIT_DEFAULT {
            break;
        }
        let item_bytes = serde_json::to_vec(&item)?.len();
        let separator_bytes = usize::from(!bounded.is_empty());
        let next_bytes = encoded_bytes
            .saturating_add(separator_bytes)
            .saturating_add(item_bytes);
        if next_bytes > REGISTRY_MUTATION_CHILDREN_MAX_BYTES {
            break;
        }
        encoded_bytes = next_bytes;
        bounded.push(item);
    }
    let truncated = bounded.len() < total;
    Ok((bounded, total, truncated))
}

pub(crate) fn bounded_response<T: Serialize>(value: T) -> Result<Value> {
    let value = serde_json::to_value(value)?;
    let encoded = serde_json::to_vec(&value)?;
    let maximum = IPC_MAX_FRAME_BYTES.saturating_sub(REGISTRY_RESPONSE_RESERVE_BYTES);
    if encoded.len() > maximum {
        bail!(
            "registry response exceeds the IPC frame budget; request a page of at most {} records",
            REGISTRY_PAGE_LIMIT_DEFAULT
        );
    }
    Ok(value)
}

fn validate_ttl(ttl_ms: u64) -> Result<()> {
    if !(MIN_CLIENT_TTL_MS..=MAX_CLIENT_TTL_MS).contains(&ttl_ms) {
        bail!(
            "client ttl must be between {MIN_CLIENT_TTL_MS} and {MAX_CLIENT_TTL_MS} milliseconds"
        );
    }
    Ok(())
}

fn expiration(now: i64, ttl_ms: u64) -> Result<i64> {
    now.checked_add(i64::try_from(ttl_ms).context("client ttl exceeds signed time range")?)
        .context("client expiration time overflow")
}

fn unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mutation_children_are_bounded_by_serialized_size() {
        let payload = json!({
            "capabilities":{"blob":"x".repeat(REGISTRY_JSON_FIELD_MAX_BYTES / 2)},
            "metadata":{"blob":"y".repeat(REGISTRY_JSON_FIELD_MAX_BYTES / 2)}
        });
        let items = vec![payload; REGISTRY_PAGE_LIMIT_DEFAULT + 1];
        let (bounded, total, truncated) = bounded_children(items).expect("bounded children");
        assert_eq!(total, REGISTRY_PAGE_LIMIT_DEFAULT + 1);
        assert!(truncated);
        assert!(
            serde_json::to_vec(&bounded)
                .expect("serialize bounded children")
                .len()
                <= REGISTRY_MUTATION_CHILDREN_MAX_BYTES
        );
    }
}

#[cfg(test)]
mod compatibility_regression_tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn bind_revalidates_and_downgrades_a_stale_external_client() {
        let directory = tempdir().expect("tempdir");
        let workspace_path = directory.path().join("workspace");
        fs::create_dir_all(&workspace_path).expect("workspace");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace = register_workspace(
            &state,
            workspace_path.to_str().expect("utf8"),
            Some("fixture".to_string()),
            WorkspaceTrustState::Trusted,
            json!({}),
        )
        .expect("workspace registration");
        let workspace_id =
            Uuid::parse_str(workspace["workspace"]["id"].as_str().expect("workspace id"))
                .expect("workspace uuid");

        let now = unix_ms();
        let payload = ClientRegistrationPayload {
            client_kind: ClientKind::Adapter,
            instance_id: "stale-external".to_string(),
            display_name: "stale external".to_string(),
            client_version: "mcp-2025-11-25".to_string(),
            protocol_version: CLIENT_PROTOCOL_VERSION.to_string(),
            connection_state: "connected".to_string(),
            compatibility_state: ClientCompatibilityState::Verified,
            write_capable: true,
            last_seen_at_unix_ms: now,
            capabilities: json!({
                "soleauxProbe":{
                    "status":"pass",
                    "passedSignals":[
                        "initialize",
                        "tools_list",
                        "context_compile",
                        "registry_registration",
                        "read_only_binding",
                        "tool_ceiling"
                    ]
                }
            }),
            metadata: json!({"platform":"generic_mcp_host"}),
        };
        let mut input = CanonicalEntityInput::active(payload);
        input.state = "connected".to_string();
        input.origin_platform = Some(CLIENT_ORIGIN.to_string());
        input.native_id = Some("adapter:stale-external".to_string());
        input.idempotency_key = Some("client:adapter:stale-external".to_string());
        input.expires_at_unix_ms = Some(expiration(now, 60_000).expect("expiration"));
        let stale = state
            .registry_register_client(input)
            .expect("stale cached registration")
            .client;
        assert!(stale.payload.write_capable);

        let error = bind_client_workspace(
            &state,
            stale.id,
            workspace_id,
            ClientAccessMode::ReadWrite,
            json!({}),
            json!({}),
        )
        .expect_err("external stale compatibility must be rejected");
        assert!(format!("{error:#}").contains("daemon-trusted"));
        let updated = state
            .get::<ClientRegistrationPayload>(stale.id)
            .expect("client read")
            .expect("client exists");
        assert_eq!(
            updated.payload.compatibility_state,
            ClientCompatibilityState::Unprobed
        );
        assert!(!updated.payload.write_capable);
    }
}
