use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use soleaux_state::{
    CanonicalEntityInput, CanonicalRecord, ClientAccessMode, ClientKind, ClientRegistrationPayload,
    ClientWorkspaceBindingPayload, LOCKED_CONTEXT_PACKET_SHA256, LOCKED_PROFILE_SHA256,
    PUBLIC_TOOL_CEILING, StateStore, WorkspacePayload, WorkspaceTrustState,
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
const REGISTRY_LIMIT: usize = 10_000;

pub(crate) fn status(state: &StateStore, include_stale: bool) -> Result<Value> {
    let now = unix_ms();
    let workspaces = state.list_all::<WorkspacePayload>(REGISTRY_LIMIT, false)?;
    let clients = state.list_all::<ClientRegistrationPayload>(REGISTRY_LIMIT, false)?;
    let bindings = state.list_all::<ClientWorkspaceBindingPayload>(REGISTRY_LIMIT, false)?;
    let active_client_ids = clients
        .iter()
        .filter(|record| include_stale || client_is_active(record, now))
        .map(|record| record.id)
        .collect::<std::collections::BTreeSet<_>>();
    let active_workspace_ids = workspaces
        .iter()
        .filter(|record| record.state == "registered")
        .map(|record| record.id)
        .collect::<std::collections::BTreeSet<_>>();
    let clients = clients
        .into_iter()
        .filter(|record| include_stale || active_client_ids.contains(&record.id))
        .collect::<Vec<_>>();
    let bindings = bindings
        .into_iter()
        .filter(|record| {
            record.state == "bound"
                && active_client_ids.contains(&record.payload.client_id)
                && active_workspace_ids.contains(&record.payload.workspace_id)
                && (include_stale
                    || record
                        .expires_at_unix_ms
                        .is_none_or(|expires| expires > now))
        })
        .collect::<Vec<_>>();
    Ok(json!({
        "schemaVersion":REGISTRY_SCHEMA_VERSION,
        "clientProtocolVersion":CLIENT_PROTOCOL_VERSION,
        "workspaces":workspaces,
        "clients":clients,
        "bindings":bindings,
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
    let canonical = fs::canonicalize(Path::new(path))
        .with_context(|| format!("resolving workspace path {path}"))?;
    if !canonical.is_dir() {
        bail!("workspace path is not a directory");
    }
    let canonical_path = canonical.to_string_lossy().to_string();
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
    let record = state.upsert_native(input)?;
    Ok(json!({
        "schemaVersion":"soleaux.workspace-registration/v1",
        "workspace":record,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn list_workspaces(state: &StateStore) -> Result<Value> {
    Ok(json!({
        "schemaVersion":"soleaux.workspace-list/v1",
        "workspaces":state.list_all::<WorkspacePayload>(REGISTRY_LIMIT, false)?,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn forget_workspace(state: &StateStore, workspace_id: Uuid) -> Result<Value> {
    let workspace = state
        .get::<WorkspacePayload>(workspace_id)?
        .context("workspace registration does not exist")?;
    let mut unbound = Vec::new();
    for binding in state.list_all::<ClientWorkspaceBindingPayload>(REGISTRY_LIMIT, false)? {
        if binding.payload.workspace_id == workspace_id {
            state.tombstone(binding.id, "workspace forgotten", "soleaux-registry")?;
            unbound.push(binding.id);
        }
    }
    let tombstone = state.tombstone(workspace.id, "workspace forgotten", "soleaux-registry")?;
    Ok(json!({
        "schemaVersion":"soleaux.workspace-forget/v1",
        "workspaceId":workspace_id,
        "unboundClientBindings":unbound,
        "tombstone":tombstone,
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
    if protocol_version != CLIENT_PROTOCOL_VERSION {
        bail!("unsupported Soleaux client protocol version: {protocol_version}");
    }
    let now = unix_ms();
    let native_id = format!("{}:{instance_id}", client_kind.as_str());
    let payload = ClientRegistrationPayload {
        client_kind,
        instance_id,
        display_name,
        client_version,
        protocol_version,
        connection_state: "connected".to_string(),
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
    let record = state.upsert_native(input)?;
    Ok(json!({
        "schemaVersion":"soleaux.client-registration/v1",
        "client":record,
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
    let existing = state
        .get::<ClientRegistrationPayload>(client_id)?
        .context("client registration does not exist")?;
    let now = unix_ms();
    let mut payload = existing.payload;
    payload.connection_state = "connected".to_string();
    payload.last_seen_at_unix_ms = now;
    if let Some(capabilities) = capabilities {
        payload.capabilities = capabilities;
    }
    let native_id = format!("{}:{}", payload.client_kind.as_str(), payload.instance_id);
    let mut input = CanonicalEntityInput::active(payload);
    input.id = Some(existing.id);
    input.state = "connected".to_string();
    input.origin_platform = Some(CLIENT_ORIGIN.to_string());
    input.native_id = Some(native_id.clone());
    input.idempotency_key = Some(format!("client:{native_id}"));
    input.expires_at_unix_ms = Some(expiration(now, ttl_ms)?);
    let record = state.upsert_native(input)?;
    let mut refreshed_bindings = Vec::new();
    for binding in state.list_all::<ClientWorkspaceBindingPayload>(REGISTRY_LIMIT, false)? {
        if binding.payload.client_id != client_id {
            continue;
        }
        let mut payload = binding.payload;
        payload.last_seen_at_unix_ms = now;
        let native_id = binding
            .native_id
            .clone()
            .context("client workspace binding omitted its native identity")?;
        let mut input = CanonicalEntityInput::active(payload);
        input.id = Some(binding.id);
        input.workspace_id = binding.workspace_id;
        input.parent_id = binding.parent_id;
        input.state = "bound".to_string();
        input.origin_platform = Some(BINDING_ORIGIN.to_string());
        input.native_id = Some(native_id.clone());
        input.idempotency_key = Some(format!("binding:{native_id}"));
        input.expires_at_unix_ms = record.expires_at_unix_ms;
        refreshed_bindings.push(state.upsert_native(input)?);
    }
    Ok(json!({
        "schemaVersion":"soleaux.client-heartbeat/v1",
        "client":record,
        "bindings":refreshed_bindings,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn list_clients(state: &StateStore, include_stale: bool) -> Result<Value> {
    let now = unix_ms();
    let clients = state
        .list_all::<ClientRegistrationPayload>(REGISTRY_LIMIT, false)?
        .into_iter()
        .filter(|record| include_stale || client_is_active(record, now))
        .collect::<Vec<_>>();
    Ok(json!({
        "schemaVersion":"soleaux.client-list/v1",
        "clients":clients,
        "supportedClientKinds":ClientKind::ALL,
        "productionClaimAllowed":false,
    }))
}

pub(crate) fn disconnect_client(state: &StateStore, client_id: Uuid) -> Result<Value> {
    let client = state
        .get::<ClientRegistrationPayload>(client_id)?
        .context("client registration does not exist")?;
    let mut unbound = Vec::new();
    for binding in state.list_all::<ClientWorkspaceBindingPayload>(REGISTRY_LIMIT, false)? {
        if binding.payload.client_id == client_id {
            state.tombstone(binding.id, "client disconnected", "soleaux-registry")?;
            unbound.push(binding.id);
        }
    }
    let tombstone = state.tombstone(client.id, "client disconnected", "soleaux-registry")?;
    Ok(json!({
        "schemaVersion":"soleaux.client-disconnect/v1",
        "clientId":client_id,
        "unboundWorkspaceBindings":unbound,
        "tombstone":tombstone,
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
    let client = state
        .get::<ClientRegistrationPayload>(client_id)?
        .context("client registration does not exist")?;
    if !client_is_active(&client, unix_ms()) {
        bail!("client registration is stale; heartbeat or register before binding");
    }
    let workspace = state
        .get::<WorkspacePayload>(workspace_id)?
        .context("workspace registration does not exist")?;
    if workspace.state != "registered" {
        bail!("workspace registration is not active");
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
    input.expires_at_unix_ms = client.expires_at_unix_ms;
    let record = state.upsert_native(input)?;
    Ok(json!({
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
    Ok(json!({
        "schemaVersion":"soleaux.client-workspace-unbind/v1",
        "bindingId":binding_id,
        "tombstone":tombstone,
        "productionClaimAllowed":false,
    }))
}

fn client_is_active(record: &CanonicalRecord<ClientRegistrationPayload>, now: i64) -> bool {
    record.state == "connected"
        && record.payload.connection_state == "connected"
        && record
            .expires_at_unix_ms
            .is_none_or(|expires| expires > now)
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
