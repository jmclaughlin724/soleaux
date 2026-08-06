#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


registry = "native/daemon/ipc/src/registry.rs"
replace_once(
    registry,
    "    ClientKind, ClientRegistrationPayload, ClientWorkspaceBindingPayload,\n",
    "    ClientKind, ClientRegistrationPayload, ClientRegistrationResult,\n"
    "    ClientWorkspaceBindingPayload,\n",
    "import client registration result",
)
replace_once(
    registry,
    '''    let (client, compatibility) =
        revalidate_client(state, client_id, capabilities, Some(ttl_ms), true)?;
    let bindings = state.registry_bindings(false, None, REGISTRY_PAGE_LIMIT_DEFAULT, unix_ms())?;
    let client_bindings = bindings
        .items
        .into_iter()
        .filter(|binding| binding.payload.client_id == client_id)
        .collect::<Vec<_>>();
    let (bindings, binding_count, bindings_truncated) = bounded_children(client_bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-heartbeat/v1",
        "client":client,
''',
    '''    let (result, compatibility) =
        revalidate_client(state, client_id, capabilities, Some(ttl_ms), true)?;
    let (bindings, binding_count, bindings_truncated) = bounded_children(result.bindings)?;
    bounded_response(json!({
        "schemaVersion":"soleaux.client-heartbeat/v1",
        "client":result.client,
''',
    "return mutation-owned heartbeat bindings",
)
replace_once(
    registry,
    "    let (_client, compatibility) = revalidate_client(state, client_id, None, None, false)?;\n",
    "    let (_result, compatibility) = revalidate_client(state, client_id, None, None, false)?;\n",
    "bind revalidation result",
)
replace_once(
    registry,
    '''    CanonicalRecord<ClientRegistrationPayload>,
    crate::compatibility::CompatibilityDecision,
)> {
''',
    '''    ClientRegistrationResult,
    crate::compatibility::CompatibilityDecision,
)> {
''',
    "revalidation return type",
)
replace_once(
    registry,
    "        return Ok((existing, compatibility));\n",
    '''        return Ok((
            ClientRegistrationResult {
                client: existing,
                bindings: Vec::new(),
            },
            compatibility,
        ));
''',
    "unchanged revalidation result",
)
replace_once(
    registry,
    '''        idempotency_key: existing.idempotency_key.clone(),
        expected_revision: None,
        expires_at_unix_ms,
''',
    '''        idempotency_key: existing.idempotency_key.clone(),
        expected_revision: Some(existing.revision),
        expires_at_unix_ms,
''',
    "bind revalidation to read revision",
)
replace_once(
    registry,
    "    Ok((result.client, compatibility))\n",
    "    Ok((result, compatibility))\n",
    "return complete writer result",
)

database = "native/daemon/state/src/database.rs"
replace_once(
    database,
    '''pub(crate) fn registry_register_client(
    connection: &mut Connection,
    input: &SerializedEntityInput,
) -> Result<SerializedClientRegistrationResult> {
    if input.kind != EntityKind::ClientRegistration {
        bail!("client registration requires a client-registration payload");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let client = upsert_native_entity_tx(&transaction, input, true)?;
    let bindings = refresh_client_bindings_tx(&transaction, &client)?;
    transaction.commit()?;
    Ok(SerializedClientRegistrationResult { client, bindings })
}
''',
    '''pub(crate) fn registry_register_client(
    connection: &mut Connection,
    input: &SerializedEntityInput,
) -> Result<SerializedClientRegistrationResult> {
    if input.kind != EntityKind::ClientRegistration {
        bail!("client registration requires a client-registration payload");
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let client = if let Some(expected_revision) = input.expected_revision {
        let client_id = input
            .id
            .context("revision-aware client revalidation requires its canonical id")?;
        let existing = read_entity_tx(&transaction, client_id)?;
        ensure_active_client(&existing, unix_ms())?;
        if existing.kind != EntityKind::ClientRegistration {
            bail!("revision-aware client revalidation targeted the wrong entity kind");
        }
        if existing.revision != expected_revision {
            bail!(
                "client registration revision conflict: expected {expected_revision}, current {}",
                existing.revision
            );
        }
        if existing.workspace_id != input.workspace_id
            || existing.parent_id != input.parent_id
            || existing.origin_platform != input.origin_platform
            || existing.native_id != input.native_id
        {
            bail!("revision-aware client revalidation cannot change canonical identity");
        }
        update_entity_payload_tx(
            &transaction,
            &existing,
            &input.state,
            input.payload.clone(),
            input.expires_at_unix_ms,
            "registry.client.revalidated",
        )?
    } else {
        upsert_native_entity_tx(&transaction, input, true)?
    };
    let bindings = refresh_client_bindings_tx(&transaction, &client)?;
    transaction.commit()?;
    Ok(SerializedClientRegistrationResult { client, bindings })
}
''',
    "revision-aware serialized client mutation",
)

tests = Path("native/daemon/state/src/tests.rs")
text = tests.read_text(encoding="utf-8")
regression = r'''

#[test]
fn registry_client_revalidation_rejects_stale_revision() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let expires = now_ms() + 60_000;
    let initial = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "revision-fixture",
            "Revision fixture",
            ClientCompatibilityState::Verified,
            expires,
            json!({"sequence":"initial"}),
        ))
        .expect("initial client");

    let mut first = registry_client_input(
        ClientKind::Cli,
        "revision-fixture",
        "Revision fixture",
        ClientCompatibilityState::Verified,
        expires,
        json!({"sequence":"first"}),
    );
    first.id = Some(initial.client.id);
    first.expected_revision = Some(initial.client.revision);
    let mut stale = registry_client_input(
        ClientKind::Cli,
        "revision-fixture",
        "Revision fixture",
        ClientCompatibilityState::Verified,
        expires,
        json!({"sequence":"stale"}),
    );
    stale.id = Some(initial.client.id);
    stale.expected_revision = Some(initial.client.revision);

    let updated = store
        .registry_register_client(first)
        .expect("first revalidation");
    assert_eq!(updated.client.revision, initial.client.revision + 1);
    let error = store
        .registry_register_client(stale)
        .expect_err("stale revalidation must fail closed");
    assert!(format!("{error:#}").contains("revision conflict"));
}

#[test]
fn registry_client_revalidation_returns_owned_bindings_without_global_paging() {
    let directory = tempdir().expect("tempdir");
    let store = StateStore::open(directory.path().join("state.sqlite3")).expect("store");
    let expires = now_ms() + 60_000;
    let workspace = store
        .registry_register_workspace(registry_workspace_input(
            "/tmp/soleaux-registry-binding-page",
            WorkspaceTrustState::Trusted,
            None,
        ))
        .expect("workspace")
        .workspace;

    for index in 0..27 {
        let filler = store
            .registry_register_client(registry_client_input(
                ClientKind::Adapter,
                &format!("filler-{index}"),
                &format!("Filler {index}"),
                ClientCompatibilityState::Unprobed,
                expires,
                json!({"index":index}),
            ))
            .expect("filler client")
            .client;
        store
            .registry_bind_client_workspace(registry_binding_input(
                filler.id,
                workspace.id,
                ClientAccessMode::ReadOnly,
            ))
            .expect("filler binding");
    }

    let target = store
        .registry_register_client(registry_client_input(
            ClientKind::Cli,
            "target-client",
            "Target client",
            ClientCompatibilityState::Verified,
            expires,
            json!({"target":true}),
        ))
        .expect("target client")
        .client;
    let target_binding = store
        .registry_bind_client_workspace(registry_binding_input(
            target.id,
            workspace.id,
            ClientAccessMode::ReadWrite,
        ))
        .expect("target binding");

    let mut revalidation = registry_client_input(
        ClientKind::Cli,
        "target-client",
        "Target client",
        ClientCompatibilityState::Verified,
        expires,
        json!({"target":true,"revalidated":true}),
    );
    revalidation.id = Some(target.id);
    revalidation.expected_revision = Some(target.revision);
    let result = store
        .registry_register_client(revalidation)
        .expect("target revalidation");
    assert_eq!(result.bindings.len(), 1);
    assert_eq!(result.bindings[0].id, target_binding.id);
    assert_eq!(result.bindings[0].payload.client_id, target.id);
}
'''
if "registry_client_revalidation_rejects_stale_revision" in text:
    raise SystemExit("state revalidation regressions already exist")
tests.write_text(text + regression, encoding="utf-8")