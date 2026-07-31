from __future__ import annotations

import importlib.util
import socket
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import fastmcp
import pytest
from fastmcp import FastMCP

import soleaux.server
from soleaux.analysis.service import DeploymentTransport
from soleaux.contracts.config import ResolvedConfig

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
COMPOSITION_PATH = WORKSPACE_ROOT / "scripts/soleaux/http_service.py"


@pytest.fixture
def http_composition(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.setattr(sys, "path", [str(WORKSPACE_ROOT), *sys.path])
    spec = importlib.util.spec_from_file_location(
        "soleaux_http_service_test",
        COMPOSITION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load HTTP composition from {COMPOSITION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module


def _socket_deployment(socket_path: Path) -> SimpleNamespace:
    return SimpleNamespace(socket_path=socket_path)


def test_primary_factory_composes_without_authentication(
    monkeypatch: pytest.MonkeyPatch,
    http_composition: ModuleType,
) -> None:
    captured: dict[str, object] = {}
    sentinel = FastMCP("primary-composition-test")

    def fake_create_server(
        root: Path,
        *,
        config: ResolvedConfig | None = None,
        deployment_transport: DeploymentTransport = "stdio",
    ) -> FastMCP[soleaux.server.LifespanState]:
        captured["root"] = root
        captured["deployment_transport"] = deployment_transport
        return sentinel

    monkeypatch.setattr(http_composition, "create_server", fake_create_server)
    monkeypatch.setattr(
        http_composition,
        "load_deployment_config",
        lambda: SimpleNamespace(workspace_root=WORKSPACE_ROOT),
    )

    server = http_composition.create_workspace_server()

    assert server is sentinel
    assert captured["root"] == WORKSPACE_ROOT
    assert captured["deployment_transport"] == "http"
    assert server.auth is None


def test_development_factory_explicitly_uses_zero_backend_config(
    monkeypatch: pytest.MonkeyPatch,
    http_composition: ModuleType,
) -> None:
    captured: dict[str, object] = {}
    sentinel = FastMCP("zero-backend-test")

    def fake_create_server(
        root: Path,
        *,
        config: ResolvedConfig | None = None,
        deployment_transport: DeploymentTransport = "stdio",
    ) -> FastMCP[soleaux.server.LifespanState]:
        captured["root"] = root
        captured["config"] = config
        captured["deployment_transport"] = deployment_transport
        return sentinel

    monkeypatch.setattr(http_composition, "create_server", fake_create_server)
    monkeypatch.setattr(
        http_composition,
        "load_deployment_config",
        lambda: SimpleNamespace(workspace_root=WORKSPACE_ROOT),
    )
    settings_before = (
        fastmcp.settings.http_host_origin_protection,
        fastmcp.settings.stateless_http,
        fastmcp.settings.http_session_idle_timeout,
    )

    server = http_composition.create_development_server()

    assert server is sentinel
    assert captured["root"] == WORKSPACE_ROOT
    config = captured["config"]
    assert isinstance(config, ResolvedConfig)
    assert config.mcp == {}
    assert captured["deployment_transport"] == "http"
    # Development transport behavior is launcher-owned (FASTMCP_* environment);
    # composition never mutates process-global settings.
    assert (
        fastmcp.settings.http_host_origin_protection,
        fastmcp.settings.stateless_http,
        fastmcp.settings.http_session_idle_timeout,
    ) == settings_before


def test_socket_directory_is_created_user_private(
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "private" / "soleaux"

    http_composition._ensure_socket_directory(directory)

    info = directory.lstat()
    assert stat.S_ISDIR(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o700


def test_socket_directory_repairs_owned_permissive_mode(
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "soleaux"
    directory.mkdir(mode=0o755)

    http_composition._ensure_socket_directory(directory)

    assert stat.S_IMODE(directory.lstat().st_mode) == 0o700


def test_socket_directory_rejects_symlink(
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(http_composition.DeploymentError) as excinfo:
        http_composition._ensure_socket_directory(link)
    assert "symlink" in str(excinfo.value)


def test_socket_directory_rejects_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "soleaux"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(http_composition.os, "getuid", lambda: directory.lstat().st_uid + 1)

    with pytest.raises(http_composition.DeploymentError) as excinfo:
        http_composition._ensure_socket_directory(directory)
    assert "owned by the current user" in str(excinfo.value)


def test_socket_path_allows_a_fresh_path(
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    http_composition._prepare_socket_path(tmp_path / "fresh.sock")


def test_socket_path_removes_only_a_stale_owned_socket(
    http_composition: ModuleType,
    short_socket_dir: Path,
) -> None:
    socket_path = short_socket_dir / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        stale.bind(str(socket_path))
    finally:
        stale.close()

    http_composition._prepare_socket_path(socket_path)

    assert not socket_path.exists()


def test_socket_path_rejects_a_regular_file(
    http_composition: ModuleType,
    tmp_path: Path,
) -> None:
    occupant = tmp_path / "occupant.sock"
    occupant.write_text("not a socket", encoding="utf-8")

    with pytest.raises(http_composition.DeploymentError) as excinfo:
        http_composition._prepare_socket_path(occupant)
    assert "non-socket occupant" in str(excinfo.value)
    assert occupant.exists()


def test_socket_path_rejects_a_symlink(
    http_composition: ModuleType,
    short_socket_dir: Path,
) -> None:
    socket_path = short_socket_dir / "real.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        stale.bind(str(socket_path))
    finally:
        stale.close()
    link = short_socket_dir / "link.sock"
    link.symlink_to(socket_path)

    with pytest.raises(http_composition.DeploymentError) as excinfo:
        http_composition._prepare_socket_path(link)
    assert "non-socket occupant" in str(excinfo.value)
    assert socket_path.exists()


def test_prebound_socket_is_listening_with_owner_only_mode(
    http_composition: ModuleType,
    short_socket_dir: Path,
) -> None:
    socket_path = short_socket_dir / "service.sock"

    listener = http_composition._prebind_socket(socket_path)
    try:
        info = socket_path.lstat()
        assert stat.S_ISSOCK(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(socket_path))
        finally:
            client.close()
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_serve_prebinds_private_socket_and_serves_with_explicit_guard(
    monkeypatch: pytest.MonkeyPatch,
    http_composition: ModuleType,
    short_socket_dir: Path,
) -> None:
    socket_path = short_socket_dir / "private" / "service.sock"
    captured: dict[str, object] = {}

    class FakeServer:
        async def run_http_async(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        http_composition,
        "load_deployment_config",
        lambda: _socket_deployment(socket_path),
    )
    monkeypatch.setattr(
        http_composition,
        "create_workspace_server",
        lambda: FakeServer(),
    )

    await http_composition._serve()

    assert captured["transport"] == "http"
    assert captured["path"] == "/mcp"
    assert captured["stateless_http"] is True
    assert captured["host_origin_protection"] is True
    assert captured["allowed_hosts"] == ["soleaux.local"]
    assert captured["show_banner"] is False
    sockets = captured["sockets"]
    assert isinstance(sockets, list) and len(sockets) == 1
    assert sockets[0].family == socket.AF_UNIX
    assert stat.S_IMODE(socket_path.parent.lstat().st_mode) == 0o700
    assert not socket_path.exists()


def test_main_serves_only_the_serve_command(
    monkeypatch: pytest.MonkeyPatch,
    http_composition: ModuleType,
) -> None:
    served: list[bool] = []

    async def fake_serve() -> None:
        served.append(True)

    monkeypatch.setattr(http_composition, "_serve", fake_serve)

    assert http_composition.main(["serve"]) == 0
    assert served == [True]
    with pytest.raises(http_composition.DeploymentError) as excinfo:
        http_composition.main(["bogus"])
    assert "usage" in str(excinfo.value)


def test_composition_never_reads_keychain_or_tokens(
    http_composition: ModuleType,
) -> None:
    source = COMPOSITION_PATH.read_text(encoding="utf-8")

    assert "StaticTokenVerifier" not in source
    assert "load_credential" not in source
    assert "security" not in source
