#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


probe = Path("native/scripts/probe_client_capabilities.py")
text = probe.read_text(encoding="utf-8")
text = text.replace(
    'VERSION_PATTERN = re.compile(r"(?<!\\d)(\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.-]+)?)")\n',
    'VERSION_PATTERN = re.compile(r"(?<!\\d)(\\d+\\.\\d+\\.\\d+(?:[-+][0-9A-Za-z.-]+)?)")\n'
    'ANSI_PATTERN = re.compile(r"\\x1b\\[[0-?]*[ -/]*[@-~]")\n',
)
old = '''    combined = f"{result['stdout']}\\n{result['stderr']}"
    missing = [token for token in expected if token not in combined]
'''
new = '''    combined = ANSI_PATTERN.sub(
        "", f"{result['stdout']}\\n{result['stderr']}"
    ).casefold()
    missing = [token for token in expected if token.casefold() not in combined]
'''
if text.count(old) != 1:
    raise SystemExit("signal normalization target drifted")
text = text.replace(old, new, 1)
text = text.replace("import argparse\n", "import argparse\nimport contextlib\n", 1)
old_kill = '''                try:
                    process.kill()
                except ProcessLookupError:
                    pass
'''
new_kill = '''                with contextlib.suppress(ProcessLookupError):
                    process.kill()
'''
if text.count(old_kill) != 1:
    raise SystemExit("bounded process termination target drifted")
probe.write_text(text.replace(old_kill, new_kill, 1), encoding="utf-8")

validator = Path("native/scripts/validate_client_capability_matrix.py")
replace_once(
    validator,
    '            if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.path != expected_path:\n'
    '                fail("OpenCode asset URL is not pinned to the exact matrix version")\n',
    '            exact_asset = (\n'
    '                parsed.scheme == "https"\n'
    '                and parsed.hostname == "github.com"\n'
    '                and parsed.path == expected_path\n'
    '            )\n'
    '            if not exact_asset:\n'
    '                fail("OpenCode asset URL is not pinned to the exact matrix version")\n',
    "OpenCode exact asset validation",
)

tests = Path("tests/test_client_capability_matrix.py")
test_text = tests.read_text(encoding="utf-8")
regression = '''

def test_probe_normalizes_ansi_and_case_for_opencode(tmp_path: Path) -> None:
    binary = tmp_path / "fake-opencode"
    binary.write_text(
        "#!/usr/bin/env python3\\n"
        "import sys\\n"
        "args = sys.argv[1:]\\n"
        "if args == ['--version']:\\n"
        "    print('1.18.14')\\n"
        "elif args == ['serve', '--help']:\\n"
        "    print('STARTS A HEADLESS OPENCODE SERVER', file=sys.stderr)\\n"
        "else:\\n"
        "    print('\\x1b[36mCOMMANDS:\\x1b[0m opencode serve', file=sys.stderr)\\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    output = tmp_path / "opencode-probe.json"
    result = run(
        str(PROBE),
        "--platform",
        "opencode",
        "--binary",
        str(binary),
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
'''
if "test_probe_normalizes_ansi_and_case_for_opencode" not in test_text:
    test_text += regression
tests.write_text(test_text, encoding="utf-8")

registry = Path("native/daemon/ipc/src/registry.rs")
replace_once(
    registry,
    '''use soleaux_state::{
    CanonicalEntityInput, CanonicalRecord, ClientAccessMode, ClientCompatibilityState,
    ClientKind, ClientRegistrationPayload, ClientWorkspaceBindingPayload,
''',
    '''use soleaux_state::{
    CanonicalEntityInput, ClientAccessMode, ClientCompatibilityState, ClientKind,
    ClientRegistrationPayload, ClientRegistrationResult, ClientWorkspaceBindingPayload,
''',
    "registry imports",
)
replace_once(
    registry,
    '''use std::{
    fs,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};
''',
    '''use std::{
    fs,
    path::Path,
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};
''',
    "registry mutation lock import",
)
replace_once(
    registry,
    '''const REGISTRY_MUTATION_CHILDREN_MAX_BYTES: usize = IPC_MAX_FRAME_BYTES / 2;
''',
    '''const REGISTRY_MUTATION_CHILDREN_MAX_BYTES: usize = IPC_MAX_FRAME_BYTES / 2;
static CLIENT_MUTATION_LOCK: Mutex<()> = Mutex::new(());
''',
    "registry mutation lock owner",
)
replace_once(
    registry,
    '''    let compatibility = evaluate_client_compatibility(
        client_kind,
''',
    '''    let _mutation_guard = client_mutation_guard()?;
    let compatibility = evaluate_client_compatibility(
        client_kind,
''',
    "serialize client registration and revalidation",
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
    "return writer-owned heartbeat bindings",
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
    let existing = state
''',
    '''    ClientRegistrationResult,
    crate::compatibility::CompatibilityDecision,
)> {
    let _mutation_guard = client_mutation_guard()?;
    let existing = state
''',
    "revision-aware revalidation return type",
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
        payload,
    };
    let result = state.registry_register_client(input)?;
    Ok((result.client, compatibility))
}
''',
    '''        idempotency_key: existing.idempotency_key.clone(),
        expected_revision: Some(existing.revision),
        expires_at_unix_ms,
        payload,
    };
    let updated = state.put(input)?;
    let refresh = CanonicalEntityInput {
        id: Some(updated.id),
        workspace_id: updated.workspace_id,
        parent_id: updated.parent_id,
        origin_platform: updated.origin_platform.clone(),
        native_id: updated.native_id.clone(),
        state: updated.state.clone(),
        sensitivity: updated.sensitivity,
        idempotency_key: updated.idempotency_key.clone(),
        expected_revision: None,
        expires_at_unix_ms: updated.expires_at_unix_ms,
        payload: updated.payload.clone(),
    };
    let result = state.registry_register_client(refresh)?;
    Ok((result, compatibility))
}

fn client_mutation_guard() -> Result<std::sync::MutexGuard<'static, ()>> {
    CLIENT_MUTATION_LOCK
        .lock()
        .map_err(|_| anyhow::anyhow!("client registry mutation lock is poisoned"))
}
''',
    "revision-aware client update and binding refresh",
)

registry_text = registry.read_text(encoding="utf-8")
registry_regressions = r'''

#[cfg(test)]
mod revalidation_atomicity_tests {
    use super::*;
    use std::{
        sync::{Arc, Barrier},
        thread,
    };
    use tempfile::tempdir;

    fn registered_workspace(state: &StateStore, root: &Path) -> Uuid {
        let workspace_path = root.join("workspace");
        fs::create_dir_all(&workspace_path).expect("workspace");
        let value = register_workspace(
            state,
            workspace_path.to_str().expect("UTF-8 workspace"),
            Some("fixture".to_string()),
            WorkspaceTrustState::Trusted,
            json!({}),
        )
        .expect("workspace registration");
        Uuid::parse_str(
            value["workspace"]["id"]
                .as_str()
                .expect("workspace id"),
        )
        .expect("workspace UUID")
    }

    fn register_fixture_client(
        state: &StateStore,
        kind: ClientKind,
        instance: &str,
    ) -> Uuid {
        let version = if kind == ClientKind::Cli {
            env!("CARGO_PKG_VERSION")
        } else {
            "unprobed-fixture"
        };
        let value = register_client(
            state,
            kind,
            instance.to_string(),
            instance.to_string(),
            version.to_string(),
            CLIENT_PROTOCOL_VERSION.to_string(),
            60_000,
            json!({"registry":true}),
            json!({}),
        )
        .expect("client registration");
        Uuid::parse_str(value["client"]["id"].as_str().expect("client id"))
            .expect("client UUID")
    }

    #[test]
    fn heartbeat_returns_its_bindings_after_the_global_page_is_full() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let workspace_id = registered_workspace(&state, directory.path());
        for index in 0..(REGISTRY_PAGE_LIMIT_DEFAULT + 3) {
            let filler = register_fixture_client(
                &state,
                ClientKind::Adapter,
                &format!("filler-{index}"),
            );
            bind_client_workspace(
                &state,
                filler,
                workspace_id,
                ClientAccessMode::ReadOnly,
                json!({}),
                json!({}),
            )
            .expect("filler binding");
        }
        let target = register_fixture_client(&state, ClientKind::Cli, "target");
        bind_client_workspace(
            &state,
            target,
            workspace_id,
            ClientAccessMode::ReadWrite,
            json!({}),
            json!({}),
        )
        .expect("target binding");

        let heartbeat = heartbeat_client(
            &state,
            target,
            60_000,
            Some(json!({"registry":true,"heartbeat":true})),
        )
        .expect("heartbeat");
        assert_eq!(heartbeat["bindingCount"], 1);
        assert_eq!(heartbeat["bindingsTruncated"], false);
        assert_eq!(heartbeat["bindings"].as_array().map(Vec::len), Some(1));
        assert_eq!(heartbeat["bindings"][0]["payload"]["clientId"], target.to_string());
    }

    #[test]
    fn concurrent_heartbeats_advance_two_distinct_revisions() {
        let directory = tempdir().expect("tempdir");
        let state = StateStore::open(directory.path().join("state.sqlite3")).expect("state");
        let target = register_fixture_client(&state, ClientKind::Cli, "concurrent");
        let initial = state
            .get::<ClientRegistrationPayload>(target)
            .expect("read initial client")
            .expect("client exists");
        let barrier = Arc::new(Barrier::new(3));
        let mut handles = Vec::new();
        for writer in ["left", "right"] {
            let state = state.clone();
            let barrier = Arc::clone(&barrier);
            handles.push(thread::spawn(move || {
                barrier.wait();
                heartbeat_client(
                    &state,
                    target,
                    60_000,
                    Some(json!({"registry":true,"writer":writer})),
                )
                .expect("concurrent heartbeat");
            }));
        }
        barrier.wait();
        for handle in handles {
            handle.join().expect("heartbeat thread");
        }
        let final_client = state
            .get::<ClientRegistrationPayload>(target)
            .expect("read final client")
            .expect("client exists");
        assert_eq!(final_client.revision, initial.revision + 2);
        assert!(final_client.payload.last_seen_at_unix_ms >= initial.payload.last_seen_at_unix_ms);
    }
}
'''
if "mod revalidation_atomicity_tests" in registry_text:
    raise SystemExit("registry atomicity regressions already exist")
registry.write_text(registry_text + registry_regressions, encoding="utf-8")
