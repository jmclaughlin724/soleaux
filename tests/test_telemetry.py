"""Telemetry tool attachment gating and daemon API projection."""

from __future__ import annotations

import typing

import fastmcp
import httpx2
import pydantic
import pytest
from fastmcp.exceptions import ToolError

import soleaux.contracts.config
import soleaux.telemetry

_SESSION = {
    "id": "s-1",
    "providerId": "anthropic",
    "displayName": "anthropic · repo",
    "state": "active",
}
_OTHER_SESSION = {"id": "s-2", "providerId": "openai", "displayName": "openai · repo"}
_PROCESSES = [
    {"identity": {"pid": 10}, "sessionId": "s-1", "cpuPercent": 90.0, "residentMemoryBytes": 500},
    {"identity": {"pid": 11}, "sessionId": "s-1", "cpuPercent": 10.0, "residentMemoryBytes": 900},
    {"identity": {"pid": 12}, "sessionId": "s-2", "cpuPercent": 50.0, "residentMemoryBytes": 100},
]
_EVENT = {
    "id": "e-1",
    "providerId": "anthropic",
    "sessionId": "s-1",
    "modelId": "claude-test",
    "contextUtilizationPercent": 92.0,
    "estimatedCostUsd": 0.25,
    "usage": {"totalTokens": 1200},
    "performance": {"status": "completed", "latencyMs": 800.0, "tokensPerSecond": 40.0},
}
_FAILED_EVENT = {
    "id": "e-2",
    "providerId": "anthropic",
    "sessionId": "s-1",
    "modelId": "claude-test",
    "contextUtilizationPercent": 10.0,
    "usage": {"totalTokens": 300},
    "performance": {"status": "failed", "latencyMs": 100.0},
}
_SUMMARY = {"providerId": "anthropic", "requestCount": 2, "tokens": {"totalTokens": 1500}}
_QUOTA = {"providerId": "anthropic", "label": "5h window", "utilizationPercent": 81.0}


def _daemon_handler(request: httpx2.Request) -> httpx2.Response:
    routes: dict[str, typing.Any] = {
        "/api/v1/sessions": [_SESSION, _OTHER_SESSION],
        "/api/v1/processes": _PROCESSES,
        "/api/v1/usage/events": [_EVENT, _FAILED_EVENT],
        "/api/v1/usage/summary": [_SUMMARY],
        "/api/v1/quotas": [_QUOTA],
    }
    payload = routes.get(request.url.path)
    if payload is None:
        return httpx2.Response(404, json={})
    return httpx2.Response(200, json=payload)


def _stub_factory() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(_daemon_handler),
        base_url="http://telemetry.test/api/v1",
    )


def _enabled_config() -> soleaux.contracts.config.ResolvedConfig:
    return soleaux.contracts.config.ResolvedConfig(
        telemetry=soleaux.contracts.config.TelemetryConfig(
            enabled=True, daemon_url="http://127.0.0.1:43120"
        )
    )


def _server_with_tools(
    config: soleaux.contracts.config.ResolvedConfig,
    *,
    client_factory: soleaux.telemetry.ClientFactory | None = _stub_factory,
) -> fastmcp.FastMCP[typing.Any]:
    server = fastmcp.FastMCP(name="telemetry-test")
    attached = soleaux.telemetry.attach_telemetry_tools(
        server, config, client_factory=client_factory
    )
    assert attached is True
    return server


def test_default_config_disables_telemetry() -> None:
    config = soleaux.contracts.config.ResolvedConfig.default()
    assert config.telemetry.enabled is False
    assert "telemetry" not in config.public_payload()


def test_telemetry_config_parses_when_enabled() -> None:
    config = _enabled_config()
    assert config.telemetry.enabled is True
    assert config.telemetry.daemon_url == "http://127.0.0.1:43120"
    assert config.public_payload()["telemetry"] == {
        "enabled": True,
        "daemon_url": "http://127.0.0.1:43120",
        "timeout_seconds": 5.0,
    }


def test_daemon_url_must_be_a_bare_origin() -> None:
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.ResolvedConfig(
            telemetry=soleaux.contracts.config.TelemetryConfig(
                enabled=True, daemon_url="http://127.0.0.1:43120/api/v1"
            )
        )


def test_attach_disabled_adds_no_tools() -> None:
    server = fastmcp.FastMCP(name="telemetry-disabled")
    attached = soleaux.telemetry.attach_telemetry_tools(
        server, soleaux.contracts.config.ResolvedConfig.default()
    )
    assert attached is False


async def test_attach_enabled_registers_all_tools() -> None:
    server = _server_with_tools(_enabled_config())
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "telemetry_list_sessions",
        "telemetry_list_processes",
        "telemetry_inspect_session",
        "telemetry_provider_usage",
        "telemetry_subscription_limits",
        "telemetry_context_pressure",
        "telemetry_compare_models",
        "telemetry_resource_drain",
    }


async def test_list_processes_filters_by_session() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        all_processes = (await client.call_tool("telemetry_list_processes", {})).data
        filtered = (await client.call_tool("telemetry_list_processes", {"session_id": "s-1"})).data
    assert len(all_processes) == 3
    assert len(filtered) == 2
    assert {process["sessionId"] for process in filtered} == {"s-1"}


async def test_inspect_session_computes_totals() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        result = (await client.call_tool("telemetry_inspect_session", {"session_id": "s-1"})).data
    assert result["session"]["id"] == "s-1"
    assert result["totals"] == {
        "cpuPercent": 100.0,
        "residentMemoryBytes": 1400,
        "processCount": 2,
        "requests": 2,
        "tokens": 1500,
    }


async def test_inspect_session_rejects_unknown_session() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        with pytest.raises(ToolError):
            await client.call_tool("telemetry_inspect_session", {"session_id": "s-9"})


async def test_context_pressure_filters_and_sorts() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        result = (await client.call_tool("telemetry_context_pressure", {})).data
    assert [event["id"] for event in result] == ["e-1"]


async def test_compare_models_aggregates_by_model() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        result = (await client.call_tool("telemetry_compare_models", {})).data
    assert len(result) == 1
    model = result[0]
    assert model["modelId"] == "claude-test"
    assert model["requests"] == 2
    assert model["failures"] == 1
    assert model["tokens"] == 1500
    assert model["estimatedCostUsd"] == 0.25


async def test_resource_drain_ranks_consumers() -> None:
    async with fastmcp.Client(_server_with_tools(_enabled_config())) as client:
        result = (await client.call_tool("telemetry_resource_drain", {})).data
    assert result["topCpu"][0]["cpuPercent"] == 90.0
    assert result["topMemory"][0]["residentMemoryBytes"] == 900


async def test_daemon_failure_surfaces_typed_error() -> None:
    def failing_factory() -> httpx2.AsyncClient:
        def refuse(_request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("refused")

        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(refuse),
            base_url="http://telemetry.test/api/v1",
        )

    server = _server_with_tools(_enabled_config(), client_factory=failing_factory)
    async with fastmcp.Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("telemetry_list_sessions", {})
