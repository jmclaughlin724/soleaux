"""Background MCP backend health tracker contracts."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from _assertions import object_list, object_mapping
from fastmcp import Client, FastMCP

from soleaux import gateway as gateway_module
from soleaux import mcp_health as mcp_health_module
from soleaux.analysis.service import SoleauxService
from soleaux.contracts.config import McpBackendConfig, ResolvedConfig
from soleaux.contracts.requests import DescribeRequest
from soleaux.mcp_health import MCP_HEALTH_SCHEMA_VERSION, McpHealthTracker
from soleaux.server import create_server


def _backend_server(*tool_names: str) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(name="health-backend")

    def ping() -> str:
        return "pong"

    for tool_name in tool_names:
        server.tool(name=tool_name)(ping)
    return server


def _recording_factory(
    server: FastMCP[None],
    calls: list[str],
) -> Callable[..., Callable[[], FastMCP[None]]]:
    def transport_factory(
        _backend: McpBackendConfig,
        _root: Path,
        *,
        backend_name: str,
    ) -> Callable[[], FastMCP[None]]:
        calls.append(backend_name)
        return lambda: server

    return transport_factory


def _failing_factory(
    failure: BaseException,
) -> Callable[..., Callable[[], FastMCP[None]]]:
    def transport_factory(
        _backend: McpBackendConfig,
        _root: Path,
        *,
        backend_name: str,
    ) -> Callable[[], FastMCP[None]]:
        _ = backend_name
        raise failure

    return transport_factory


def _command_config(*names: str) -> ResolvedConfig:
    return ResolvedConfig(
        mcp={name: McpBackendConfig(command=["unused-program"]) for name in names}
    )


def _snapshot(tracker: McpHealthTracker, name: str) -> dict[str, object]:
    snapshots = {snapshot.name: snapshot.payload() for snapshot in tracker.snapshots()}
    return snapshots[name]


async def _wait_for_state(tracker: McpHealthTracker, name: str, state: str) -> None:
    for _ in range(200):
        if _snapshot(tracker, name)["state"] == state:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"backend {name!r} never reached state {state!r}")


def test_tracker_seeds_unknown_snapshots_for_configured_backends(tmp_path: Path) -> None:
    config = ResolvedConfig(
        mcp={
            "enabled_backend": McpBackendConfig(command=["unused-program"]),
            "disabled_backend": McpBackendConfig(command=["unused-program"], enabled=False),
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    payload = tracker.payload()

    assert payload["schema_version"] == MCP_HEALTH_SCHEMA_VERSION
    assert payload["backend_count"] == 2
    assert [backend["name"] for backend in payload["backends"]] == [
        "disabled_backend",
        "enabled_backend",
    ]
    assert _snapshot(tracker, "enabled_backend") == {
        "name": "enabled_backend",
        "enabled": True,
        "transport": "command",
        "lifecycle": "on_demand",
        "auth": "none",
        "state": "unknown",
        "tool_count": None,
        "catalog_digest": None,
        "server_version": None,
        "last_probe_at": None,
        "last_error": None,
        "elapsed_ms": None,
    }
    assert _snapshot(tracker, "disabled_backend")["enabled"] is False


async def test_probe_once_records_ok_with_tool_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha", "beta"), calls),
    )
    tracker = McpHealthTracker(tmp_path, _command_config("fixture"))

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "fixture")
    assert calls == ["fixture"]
    assert snapshot["state"] == "ok"
    assert snapshot["tool_count"] == 2
    assert snapshot["last_error"] is None
    assert isinstance(snapshot["last_probe_at"], str)
    assert isinstance(snapshot["elapsed_ms"], float)


async def test_probe_once_records_down_when_the_backend_is_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _failing_factory(ConnectionError("connection refused")),
    )
    tracker = McpHealthTracker(tmp_path, _command_config("fixture"))

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "fixture")
    assert snapshot["state"] == "down"
    assert snapshot["last_error"] == "connection refused"
    assert snapshot["tool_count"] is None


async def test_previously_ok_backend_regresses_to_degraded_not_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    tracker = McpHealthTracker(tmp_path, _command_config("fixture"))
    await tracker.probe_once()
    assert _snapshot(tracker, "fixture")["state"] == "ok"

    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _failing_factory(TimeoutError("backend timed out")),
    )
    await tracker.probe_once()

    snapshot = _snapshot(tracker, "fixture")
    assert snapshot["state"] == "degraded"
    assert snapshot["last_error"] == "backend timed out"


async def test_oauth_backend_without_stored_tokens_is_unauthenticated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )

    async def no_tokens(_backend: McpBackendConfig, *, backend_name: str) -> bool:
        _ = backend_name
        return False

    monkeypatch.setattr(mcp_health_module, "_has_stored_tokens", no_tokens)
    config = ResolvedConfig(
        mcp={
            "remote": McpBackendConfig(url="https://backend.invalid/mcp", auth="oauth"),
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "remote")
    assert calls == []
    assert snapshot["state"] == "unauthenticated"
    assert snapshot["last_error"] == "not authenticated; run `soleaux mcp login remote`"


async def test_oauth_backend_with_stored_tokens_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )

    async def stored_tokens(_backend: McpBackendConfig, *, backend_name: str) -> bool:
        _ = backend_name
        return True

    monkeypatch.setattr(mcp_health_module, "_has_stored_tokens", stored_tokens)
    config = ResolvedConfig(
        mcp={
            "remote": McpBackendConfig(url="https://backend.invalid/mcp", auth="oauth"),
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "remote")
    assert calls == ["remote"]
    assert snapshot["state"] == "ok"
    assert snapshot["transport"] == "url"


async def test_bearer_env_backend_without_token_is_unauthenticated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    monkeypatch.delenv("SOLEAUX_HEALTH_TEST_TOKEN", raising=False)
    config = ResolvedConfig(
        mcp={
            "remote": McpBackendConfig(
                url="https://backend.invalid/mcp",
                auth="bearer_env",
                auth_token_env="SOLEAUX_HEALTH_TEST_TOKEN",
            ),
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "remote")
    assert calls == []
    assert snapshot["state"] == "unauthenticated"
    assert snapshot["last_error"] == (
        "MCP auth token environment variable is missing or empty: SOLEAUX_HEALTH_TEST_TOKEN"
    )


async def test_probe_once_is_fail_open_across_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy = _backend_server("alpha")

    def transport_factory(
        _backend: McpBackendConfig,
        _root: Path,
        *,
        backend_name: str,
    ) -> Callable[[], FastMCP[None]]:
        if backend_name == "healthy":
            return lambda: healthy
        raise ConnectionError("refused")

    monkeypatch.setattr(gateway_module, "_transport_factory", transport_factory)
    tracker = McpHealthTracker(tmp_path, _command_config("broken", "healthy"))

    await tracker.probe_once()

    assert _snapshot(tracker, "healthy")["state"] == "ok"
    assert _snapshot(tracker, "broken")["state"] == "down"


async def test_disabled_backends_are_never_probed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    config = ResolvedConfig(
        mcp={"fixture": McpBackendConfig(command=["unused-program"], enabled=False)}
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    assert calls == []
    assert _snapshot(tracker, "fixture")["state"] == "unknown"


async def test_start_runs_the_first_probe_in_background_and_aclose_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    tracker = McpHealthTracker(tmp_path, _command_config("fixture"), probe_interval_seconds=600)

    started = time.perf_counter()
    await tracker.start()
    start_elapsed = time.perf_counter() - started
    await _wait_for_state(tracker, "fixture", "ok")
    await tracker.aclose()

    assert start_elapsed < 1
    assert tracker._task is None
    await tracker.aclose()


async def test_start_is_a_noop_without_enabled_backends(tmp_path: Path) -> None:
    config = ResolvedConfig(
        mcp={"fixture": McpBackendConfig(command=["unused-program"], enabled=False)}
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.start()

    assert tracker._task is None
    await tracker.aclose()


async def test_describe_surfaces_mcp_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha", "beta", "gamma"), calls),
    )
    config = _command_config("fixture")
    async with SoleauxService.from_root(tmp_path, config=config) as service:
        await service._mcp_health.probe_once()
        response = await service.describe(DescribeRequest())

    data = object_mapping(response.data or {})
    mcp_backends = object_mapping(data.get("mcp_backends"))
    assert mcp_backends["schema_version"] == MCP_HEALTH_SCHEMA_VERSION
    backends = object_list(mcp_backends["backends"])
    assert len(backends) == 1
    backend = object_mapping(backends[0])
    assert backend["name"] == "fixture"
    assert backend["state"] == "ok"
    assert backend["tool_count"] == 3


async def test_about_resource_surfaces_mcp_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    server = create_server(tmp_path, config=_command_config("fixture"))

    def about_backend(payload: dict[str, object]) -> dict[str, object]:
        mcp_backends = object_mapping(payload.get("mcp_backends"))
        backends = object_list(mcp_backends["backends"])
        return object_mapping(backends[0])

    about_payload: dict[str, object] = {}
    async with Client(server) as client:
        for _ in range(200):
            about = await client.read_resource("soleaux://about")
            text = getattr(about[0], "text", None)
            assert isinstance(text, str)
            about_payload = object_mapping(json.loads(text))
            if about_backend(about_payload)["state"] == "ok":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("about never reported the fixture backend as ok")

    mcp_backends = object_mapping(about_payload["mcp_backends"])
    assert mcp_backends["schema_version"] == MCP_HEALTH_SCHEMA_VERSION
    backend = about_backend(about_payload)
    assert backend["name"] == "fixture"
    assert backend["state"] == "ok"


async def test_probe_once_probes_backends_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(_backend_server("alpha"), calls),
    )
    reached = {"aaa": asyncio.Event(), "bbb": asyncio.Event()}

    async def gated_tokens(_backend: McpBackendConfig, *, backend_name: str) -> bool:
        reached[backend_name].set()
        if backend_name == "aaa":
            # Serial probing would deadlock here: bbb would never be reached.
            await asyncio.wait_for(reached["bbb"].wait(), timeout=5)
        return True

    monkeypatch.setattr(mcp_health_module, "_has_stored_tokens", gated_tokens)
    config = ResolvedConfig(
        mcp={
            "aaa": McpBackendConfig(url="https://aaa.invalid/mcp", auth="oauth"),
            "bbb": McpBackendConfig(url="https://bbb.invalid/mcp", auth="oauth"),
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    await asyncio.wait_for(tracker.probe_once(), timeout=10)

    assert _snapshot(tracker, "aaa")["state"] == "ok"
    assert _snapshot(tracker, "bbb")["state"] == "ok"


async def test_rejected_oauth_credentials_classify_as_unauthenticated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from soleaux.gateway import McpBackendAuthRequiredError

    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _failing_factory(
            McpBackendAuthRequiredError(
                "MCP backend 'remote' is not authenticated; run `soleaux mcp login remote`"
            )
        ),
    )

    async def stored_tokens(_backend: McpBackendConfig, *, backend_name: str) -> bool:
        _ = backend_name
        return True

    monkeypatch.setattr(mcp_health_module, "_has_stored_tokens", stored_tokens)
    config = ResolvedConfig(
        mcp={"remote": McpBackendConfig(url="https://backend.invalid/mcp", auth="oauth")}
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    snapshot = _snapshot(tracker, "remote")
    assert snapshot["state"] == "unauthenticated"
    assert "soleaux mcp login remote" in str(snapshot["last_error"])


async def test_rejected_bearer_token_classifies_as_unauthenticated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2

    request = httpx2.Request("GET", "https://backend.invalid/mcp")
    rejected = httpx2.HTTPStatusError(
        "401 Unauthorized",
        request=request,
        response=httpx2.Response(401, request=request),
    )
    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _failing_factory(ExceptionGroup("probe failed", [rejected])),
    )
    monkeypatch.setenv("SOLEAUX_HEALTH_TEST_TOKEN", "stored-token")
    config = ResolvedConfig(
        mcp={
            "remote": McpBackendConfig(
                url="https://backend.invalid/mcp",
                auth="bearer_env",
                auth_token_env="SOLEAUX_HEALTH_TEST_TOKEN",
            )
        }
    )
    tracker = McpHealthTracker(tmp_path, config)

    await tracker.probe_once()

    assert _snapshot(tracker, "remote")["state"] == "unauthenticated"


async def test_catalog_digest_binds_complete_tool_definitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def server_with_description(description: str) -> FastMCP[None]:
        server: FastMCP[None] = FastMCP(name="health-backend")

        def ping() -> str:
            return "pong"

        server.tool(name="alpha", description=description)(ping)
        return server

    calls: list[str] = []
    tracker = McpHealthTracker(tmp_path, _command_config("fixture"))

    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(server_with_description("first"), calls),
    )
    await tracker.probe_once()
    first_digest = _snapshot(tracker, "fixture")["catalog_digest"]

    monkeypatch.setattr(
        gateway_module,
        "_transport_factory",
        _recording_factory(server_with_description("second"), calls),
    )
    await tracker.probe_once()
    second_digest = _snapshot(tracker, "fixture")["catalog_digest"]

    assert isinstance(first_digest, str)
    assert isinstance(second_digest, str)
    assert first_digest != second_digest
