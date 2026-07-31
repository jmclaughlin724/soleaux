"""D031: lazy start, concurrent initialization, and graceful shutdown."""

import asyncio
import collections.abc
import json
import os
import pathlib
import signal
import sys

import _host_root
import pydantic
import pytest

import soleaux.lsp.broker
import soleaux.lsp.contracts
import soleaux.postgresql.runtime

FAKE_SERVER = (
    pathlib.Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"
)
PINNED_POSTGRESQL_SERVER = (
    _host_root.require_host_root()
    / "node_modules"
    / ".bin"
    / ("postgres-language-server.cmd" if os.name == "nt" else "postgres-language-server")
)


def _broker(
    tmp_path: pathlib.Path, *, initialize_delay: float = 0.0, spawn_child: bool = False
) -> soleaux.lsp.broker.LspBroker:
    argv = [sys.executable, str(FAKE_SERVER), str(initialize_delay)]
    if spawn_child:
        argv.append("spawn-child")
    return soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=tuple(argv),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        workspace_root=str(tmp_path),
    )


async def test_initialize_advertises_only_normalized_diagnostic_features(
    tmp_path: pathlib.Path,
) -> None:
    broker = _broker(tmp_path)
    try:
        await broker.start()
        state = await broker.request("test/state")
        capabilities = state["initialize_params"]["capabilities"]
        text_document = capabilities["textDocument"]

        assert text_document["publishDiagnostics"] == {"versionSupport": True}
        assert text_document["diagnostic"] == {"dynamicRegistration": True}
        assert capabilities["workspace"]["diagnostics"] == {"refreshSupport": True}
        assert "relatedDocumentSupport" not in text_document["diagnostic"]
        assert "relatedInformation" not in text_document["diagnostic"]
        identity = broker.provider_identity
        assert identity is not None
        assert identity.configured_name == "fake-lsp"
        assert identity.configured_version == "1"
        assert identity.server_info is not None
        assert identity.server_info.name == "soleaux-fake-lsp"
        assert identity.server_info.version == "1.0.0"
        assert identity.process_id == broker.pid
    finally:
        await broker.shutdown()


async def test_concurrent_start_waits_for_one_complete_initialization(
    tmp_path: pathlib.Path,
) -> None:
    broker = _broker(tmp_path, initialize_delay=0.1)
    try:
        first = asyncio.create_task(broker.start())
        await asyncio.sleep(0.01)
        await broker.start()
        assert broker.state is soleaux.lsp.contracts.SessionState.INITIALIZED
        await first
        state = await broker.request("test/state")
        assert state["initialize_count"] == 1
    finally:
        await broker.shutdown()


async def test_start_sends_initial_workspace_configuration(tmp_path: pathlib.Path) -> None:
    broker = _broker(tmp_path)
    try:
        await broker.start()
        state = await broker.request("test/state")

        assert state["notifications"][:2] == [
            {"method": "initialized", "params": {}},
            {
                "method": "workspace/didChangeConfiguration",
                "params": {"settings": {}},
            },
        ]
    finally:
        await broker.shutdown()


async def test_postgresql_runtime_command_paths_environment_and_cleanup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    database_url = "postgresql://reader:lifecycle-secret@127.0.0.1/local"
    created_sessions: list[soleaux.postgresql.runtime.PostgreSqlSessionRuntime] = []
    create_runtime = soleaux.postgresql.runtime.create_postgresql_session_runtime

    def scoped_runtime(
        *,
        argv: collections.abc.Sequence[str],
        workspace_root: pathlib.Path,
        provider_environment: collections.abc.Mapping[str, str],
        logs_retention_days: int,
        temp_retention_hours: int,
    ) -> soleaux.postgresql.runtime.PostgreSqlSessionRuntime:
        session = create_runtime(
            argv=argv,
            workspace_root=workspace_root,
            provider_environment=provider_environment,
            logs_retention_days=logs_retention_days,
            temp_retention_hours=temp_retention_hours,
            runtime_root=runtime_root,
        )
        created_sessions.append(session)
        return session

    monkeypatch.setattr(
        "soleaux.postgresql.runtime.create_postgresql_session_runtime",
        scoped_runtime,
    )
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="sql",
            argv=(sys.executable, str(FAKE_SERVER), "0.0"),
            root_uri=workspace.as_uri(),
            provider_name="postgres-language-server",
            provider_version="0.25.4",
            initialization_options={"testSecret": database_url},
            environment_names=soleaux.postgresql.runtime.POSTGRESQL_ENVIRONMENT_NAMES,
            environment={"DATABASE_URL": pydantic.SecretStr(database_url)},
            logs_retention_days=9,
            temp_retention_hours=30,
        )
    )
    session: soleaux.postgresql.runtime.PostgreSqlSessionRuntime | None = None
    try:
        await broker.start()
        session = created_sessions[0]
        assert session is not None
        assert "--disable-db" not in session.argv
        assert f"--config-path={session.config_dir}" in session.argv
        assert session.environment["DATABASE_URL"] == database_url
        assert not session.config_dir.is_relative_to(workspace)
        assert not session.log_dir.is_relative_to(workspace)
        returned_state = json.dumps(await broker.request("test/state"))
        assert database_url not in returned_state
        assert "[REDACTED]" in returned_state
        (session.log_dir / "provider.log").write_text(database_url, encoding="utf-8")
    finally:
        await broker.shutdown()

    assert session is not None
    assert not session.config_dir.exists()
    retained_log = (session.log_dir / "provider.log").read_text(encoding="utf-8")
    assert database_url not in retained_log
    assert "[REDACTED]" in retained_log


async def test_real_pinned_postgresql_broker_starts_offline_and_reaps_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "schema.sql").write_text("select 1;\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    created_sessions: list[soleaux.postgresql.runtime.PostgreSqlSessionRuntime] = []
    create_runtime = soleaux.postgresql.runtime.create_postgresql_session_runtime

    def scoped_runtime(
        *,
        argv: collections.abc.Sequence[str],
        workspace_root: pathlib.Path,
        provider_environment: collections.abc.Mapping[str, str],
        logs_retention_days: int,
        temp_retention_hours: int,
    ) -> soleaux.postgresql.runtime.PostgreSqlSessionRuntime:
        session = create_runtime(
            argv=argv,
            workspace_root=workspace_root,
            provider_environment=provider_environment,
            logs_retention_days=logs_retention_days,
            temp_retention_hours=temp_retention_hours,
            runtime_root=runtime_root,
        )
        created_sessions.append(session)
        return session

    monkeypatch.setattr(
        "soleaux.postgresql.runtime.create_postgresql_session_runtime",
        scoped_runtime,
    )
    monkeypatch.setenv("PGHOST", "database.example.invalid")
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="sql",
            argv=(str(PINNED_POSTGRESQL_SERVER), "lsp-proxy"),
            root_uri=workspace.as_uri(),
            provider_name="postgres-language-server",
            provider_version="0.25.4",
            environment_names=soleaux.postgresql.runtime.POSTGRESQL_ENVIRONMENT_NAMES,
            logs_retention_days=9,
            temp_retention_hours=30,
        )
    )
    pid: int | None = None
    session: soleaux.postgresql.runtime.PostgreSqlSessionRuntime | None = None
    try:
        assert PINNED_POSTGRESQL_SERVER.is_file()
        await asyncio.wait_for(broker.start(), timeout=10.0)
        pid = broker.pid
        session = created_sessions[0]
        assert pid is not None
        assert _pid_exists(pid)
        assert broker.state is soleaux.lsp.contracts.SessionState.INITIALIZED
        assert "--disable-db" not in session.argv
        assert soleaux.postgresql.runtime.database_environment(session.environment) == {}
        config = json.loads(
            (session.config_dir / soleaux.postgresql.runtime.POSTGRESQL_CONFIG_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        database_config = config["db"]
        assert database_config == {"allowStatementExecutionsAgainst": []}
        assert "connectionString" not in database_config
        assert "host" not in database_config
    finally:
        await asyncio.wait_for(broker.shutdown(), timeout=10.0)

    assert session is not None
    assert pid is not None
    assert not session.config_dir.exists()
    assert broker.state is soleaux.lsp.contracts.SessionState.DEAD
    assert broker.pid is None
    assert not _pid_exists(pid)


async def test_publish_diagnostics_use_uri_state_not_method_retention(
    tmp_path: pathlib.Path,
) -> None:
    broker = _broker(tmp_path)
    uri = (tmp_path / "main.py").as_uri()
    try:
        await broker.start()
        broker.bind_diagnostic_generation(
            uri,
            document_version=1,
            generation_fingerprint="generation-a",
        )
        await broker.open_document(uri, "python", "value = 1\n")

        state = await broker.wait_for_diagnostics(
            uri,
            document_version=1,
            generation_fingerprint="generation-a",
            timeout=0.2,
        )

        assert state is not None
        assert state.items[0]["source"] == "push"
        assert broker.notifications_by_method("textDocument/publishDiagnostics") == ()
    finally:
        await broker.shutdown()


async def test_shutdown_reaps_the_provider(tmp_path: pathlib.Path) -> None:
    broker = _broker(tmp_path)
    await broker.start()
    pid = broker.pid
    assert pid is not None

    await asyncio.wait_for(broker.shutdown(), timeout=2.0)

    assert broker.state is soleaux.lsp.contracts.SessionState.DEAD
    assert broker.started is False


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_shutdown_reaps_descendant_processes(tmp_path: pathlib.Path) -> None:
    broker = _broker(tmp_path, spawn_child=True)
    child_pid: int | None = None
    try:
        await broker.start()
        state = await broker.request("test/state")
        child_pid = state["child_pid"]
        assert isinstance(child_pid, int)
        assert _pid_exists(child_pid)

        await broker.shutdown()
        for _ in range(100):
            if not _pid_exists(child_pid):
                break
            await asyncio.sleep(0.01)
        assert not _pid_exists(child_pid)
    finally:
        await broker.shutdown()
        if child_pid is not None and _pid_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)
