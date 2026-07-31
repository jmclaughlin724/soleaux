"""Selected provider restart advances epochs without eager recreation."""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

import soleaux.contracts.workspace
import soleaux.lsp.contracts
import soleaux.lsp.providers
import soleaux.lsp.resolvers
import soleaux.structural.snapshot

FAKE_SERVER = (
    pathlib.Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"
)


async def _resolver(
    tmp_path: pathlib.Path,
) -> tuple[soleaux.lsp.resolvers.SemanticResolver, soleaux.structural.snapshot.SnapshotBundle]:
    (tmp_path / "main.py").write_text(
        "def target():\n    return 1\n\ntarget()\n",
        encoding="utf-8",
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="restart-test",
    ).get("workspace")
    bundle = await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=("main.py",)
    )
    provider = soleaux.lsp.providers.ConfiguredProvider(
        provider_name="fake-lsp",
        provider_version="1",
        argv=(sys.executable, str(FAKE_SERVER)),
        extensions=("py",),
        root=tmp_path,
        config_digest="fake-config",
    )
    return soleaux.lsp.resolvers.SemanticResolver(
        soleaux.lsp.providers.ProviderRegistry((provider,))
    ), bundle


async def test_selected_restart_reaps_old_pid_and_keeps_replacement_lazy(
    tmp_path: pathlib.Path,
) -> None:
    resolver, bundle = await _resolver(tmp_path)
    try:
        await resolver.navigate(
            soleaux.lsp.contracts.NavigationRequest(
                operation=soleaux.lsp.contracts.SemanticOperation.DEFINITION,
                path="main.py",
                line=4,
                column=2,
            ),
            bundle,
        )
        old_pid = resolver.active_provider_pids[0]

        restarted = await resolver.restart_selected(
            workspace_id="workspace",
            path="main.py",
        )

        assert restarted.restarted_sessions == 1
        assert restarted.sessions[0].status is soleaux.lsp.contracts.RestartStatus.RESTARTED
        assert restarted.sessions[0].old_epoch == 0
        assert restarted.sessions[0].new_epoch == 1
        assert restarted.sessions[0].old_pid == old_pid
        assert restarted.sessions[0].new_pid is None
        assert resolver.active_session_count == 0
        assert not _pid_exists(old_pid)

        await resolver.navigate(
            soleaux.lsp.contracts.NavigationRequest(
                operation=soleaux.lsp.contracts.SemanticOperation.DEFINITION,
                path="main.py",
                line=4,
                column=2,
            ),
            bundle,
        )
        assert resolver.active_session_count == 1
        assert resolver.active_provider_pids[0] != old_pid
    finally:
        await resolver.shutdown()


async def test_not_running_and_unavailable_selections_start_nothing(
    tmp_path: pathlib.Path,
) -> None:
    resolver, _bundle = await _resolver(tmp_path)
    try:
        not_running = await resolver.restart_selected(
            workspace_id="workspace",
            language="python",
        )
        unavailable = await resolver.restart_selected(
            workspace_id="workspace",
            provider_name="missing-lsp",
        )
        assert not_running.sessions[0].status is soleaux.lsp.contracts.RestartStatus.NOT_RUNNING
        assert not_running.sessions[0].old_epoch == not_running.sessions[0].new_epoch == 0
        assert unavailable.sessions[0].status is soleaux.lsp.contracts.RestartStatus.UNAVAILABLE
        assert resolver.active_session_count == 0
    finally:
        await resolver.shutdown()


@pytest.mark.parametrize(
    "selector",
    (
        pytest.param(
            {"provider_name": "postgres-language-server"},
            id="provider",
        ),
        pytest.param({"language": "sql"}, id="language"),
        pytest.param({"path": "schema.sql"}, id="sql-path"),
    ),
)
async def test_postgres_restart_selection(
    tmp_path: pathlib.Path,
    selector: dict[str, str],
) -> None:
    provider = soleaux.lsp.providers.ConfiguredProvider(
        provider_name="postgres-language-server",
        provider_version="0.25.4",
        argv=(sys.executable,),
        extensions=("sql",),
        root=tmp_path,
        config_digest="postgres-restart-test",
    )
    resolver = soleaux.lsp.resolvers.SemanticResolver(
        soleaux.lsp.providers.ProviderRegistry((provider,))
    )
    try:
        result = await resolver.restart_selected(
            workspace_id="workspace",
            **selector,
        )

        assert result.restarted_sessions == 0
        assert result.sessions[0].provider_name == "postgres-language-server"
        assert result.sessions[0].status is soleaux.lsp.contracts.RestartStatus.NOT_RUNNING
        assert resolver.active_session_count == 0
    finally:
        await resolver.shutdown()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
