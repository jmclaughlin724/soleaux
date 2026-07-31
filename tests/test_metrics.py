"""Metrics middleware attribution, timing, counting, and fail-open emission."""

from __future__ import annotations

import asyncio
import typing

import fastmcp.tools
import httpx2
import mcp_types as mt
import pytest
from fastmcp.server.middleware import MiddlewareContext

import soleaux.contracts.config
import soleaux.metrics


class _RecordingEmitter:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.events: list[soleaux.metrics.ToolCallEvent] = []
        self.failure = failure
        self.delivered = asyncio.Event()

    async def emit(self, event: soleaux.metrics.ToolCallEvent) -> None:
        if self.failure is not None:
            raise self.failure
        self.events.append(event)
        self.delivered.set()


def _call_tool_context(tool_name: str) -> MiddlewareContext[mt.CallToolRequestParams]:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=tool_name, arguments={}),
        method="tools/call",
    )


def _list_tools_context() -> MiddlewareContext[mt.ListToolsRequest]:
    return MiddlewareContext(
        message=mt.ListToolsRequest(method="tools/list"),
        method="tools/list",
    )


def _ok_result(_context: typing.Any) -> fastmcp.tools.ToolResult:
    return fastmcp.tools.ToolResult(content="ok")


def test_backend_attribution_matches_configured_namespaces() -> None:
    middleware = soleaux.metrics.MetricsMiddleware(backends=("db", "db_admin"))
    assert middleware.backend_for("db_query") == "db"
    assert middleware.backend_for("db_admin_query") == "db_admin"
    assert middleware.backend_for("describe") == soleaux.metrics.LOCAL_BACKEND
    assert middleware.backend_for("unknown_thing") == soleaux.metrics.LOCAL_BACKEND


async def test_tool_call_records_backend_timing_and_success() -> None:
    emitter = _RecordingEmitter()
    middleware = soleaux.metrics.MetricsMiddleware(backends=("db",), emitter=emitter)

    async def call_next(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> fastmcp.tools.ToolResult:
        await asyncio.sleep(0)
        return _ok_result(context)

    result = await middleware.on_call_tool(_call_tool_context("db_query"), call_next)

    assert result.is_error is False
    snapshot = middleware.snapshot()
    backend = snapshot["backends"]["db"]
    assert backend["calls"] == 1
    assert backend["errors"] == 0
    assert backend["totalDurationMs"] > 0
    assert backend["maxDurationMs"] > 0
    assert backend["lastCallAt"] is not None
    assert backend["tools"] == {"db_query": 1}

    await asyncio.wait_for(emitter.delivered.wait(), timeout=1.0)
    event = emitter.events[0]
    assert event.operation == "tools/call"
    assert event.backend == "db"
    assert event.tool_name == "db_query"
    assert event.ok is True
    assert event.error_type is None
    assert event.duration_ms > 0


async def test_local_tool_attributes_to_local_backend() -> None:
    middleware = soleaux.metrics.MetricsMiddleware(backends=("db",))

    async def call_next(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> fastmcp.tools.ToolResult:
        return _ok_result(context)

    await middleware.on_call_tool(_call_tool_context("search"), call_next)

    assert middleware.snapshot()["backends"]["local"]["calls"] == 1


async def test_raised_tool_error_counts_and_reraises() -> None:
    emitter = _RecordingEmitter()
    middleware = soleaux.metrics.MetricsMiddleware(backends=("db",), emitter=emitter)

    async def call_next(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> fastmcp.tools.ToolResult:
        raise ValueError("backend exploded with secret detail")

    with pytest.raises(ValueError) as raised:
        await middleware.on_call_tool(_call_tool_context("db_query"), call_next)
    assert "backend exploded" in str(raised.value)

    backend = middleware.snapshot()["backends"]["db"]
    assert backend["calls"] == 1
    assert backend["errors"] == 1

    await asyncio.wait_for(emitter.delivered.wait(), timeout=1.0)
    event = emitter.events[0]
    assert event.ok is False
    assert event.error_type == "ValueError"


async def test_error_tool_result_counts_as_error_without_exception() -> None:
    emitter = _RecordingEmitter()
    middleware = soleaux.metrics.MetricsMiddleware(emitter=emitter)

    async def call_next(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> fastmcp.tools.ToolResult:
        return fastmcp.tools.ToolResult(content="nope", is_error=True)

    result = await middleware.on_call_tool(_call_tool_context("search"), call_next)

    assert result.is_error is True
    backend = middleware.snapshot()["backends"]["local"]
    assert backend["calls"] == 1
    assert backend["errors"] == 1

    await asyncio.wait_for(emitter.delivered.wait(), timeout=1.0)
    assert emitter.events[0].error_type == "error_result"


async def test_list_tools_records_under_local_backend() -> None:
    emitter = _RecordingEmitter()
    middleware = soleaux.metrics.MetricsMiddleware(backends=("db",), emitter=emitter)

    async def call_next(context: MiddlewareContext[mt.ListToolsRequest]) -> list[typing.Any]:
        _ = context
        return []

    await middleware.on_list_tools(_list_tools_context(), call_next)

    backend = middleware.snapshot()["backends"]["local"]
    assert backend["calls"] == 1
    assert backend["errors"] == 0
    assert backend["tools"] == {}

    await asyncio.wait_for(emitter.delivered.wait(), timeout=1.0)
    assert emitter.events[0].operation == "tools/list"
    assert emitter.events[0].backend == "local"


async def test_emission_failure_never_breaks_the_tool_call() -> None:
    emitter = _RecordingEmitter(failure=RuntimeError("daemon on fire"))
    middleware = soleaux.metrics.MetricsMiddleware(emitter=emitter)

    async def call_next(
        context: MiddlewareContext[mt.CallToolRequestParams],
    ) -> fastmcp.tools.ToolResult:
        return _ok_result(context)

    result = await middleware.on_call_tool(_call_tool_context("search"), call_next)

    assert result.is_error is False
    assert middleware.snapshot()["emissions"]["emitted"] == 1
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert middleware.snapshot()["emissions"]["pending"] == 0


async def test_daemon_emitter_swallows_http_failures() -> None:
    def reject(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={})

    def factory() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(reject),
            base_url="http://telemetry.test/api/v1",
        )

    emitter = soleaux.metrics.DaemonMetricsEmitter(factory)
    event = soleaux.metrics.ToolCallEvent(
        operation="tools/call",
        backend="db",
        tool_name="db_query",
        duration_ms=1.5,
        ok=True,
        error_type=None,
        at="2026-07-31T00:00:00+00:00",
    )
    await emitter.emit(event)


async def test_daemon_emitter_posts_event_payload_to_ingest_route() -> None:
    captured: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json={})

    def factory() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(record),
            base_url="http://telemetry.test/api/v1",
        )

    emitter = soleaux.metrics.DaemonMetricsEmitter(factory)
    event = soleaux.metrics.ToolCallEvent(
        operation="tools/call",
        backend="db",
        tool_name="db_query",
        duration_ms=1.5,
        ok=True,
        error_type=None,
        at="2026-07-31T00:00:00+00:00",
    )
    await emitter.emit(event)

    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/api/v1/mcp/events"
    assert captured[0].content == (
        b'{"operation":"tools/call","backend":"db","tool_name":"db_query",'
        b'"duration_ms":1.5,"ok":true,"error_type":null,'
        b'"at":"2026-07-31T00:00:00+00:00"}'
    )


def test_from_config_selects_emitter_and_backends() -> None:
    disabled = soleaux.contracts.config.ResolvedConfig.default()
    middleware = soleaux.metrics.MetricsMiddleware.from_config(disabled)
    assert isinstance(middleware._emitter, soleaux.metrics.NoopMetricsEmitter)
    assert middleware._namespaces == ()

    enabled = soleaux.contracts.config.ResolvedConfig(
        mcp={
            "db": soleaux.contracts.config.McpBackendConfig(command=["echo", "ok"]),
            "off": soleaux.contracts.config.McpBackendConfig(command=["echo", "ok"], enabled=False),
        },
        telemetry=soleaux.contracts.config.TelemetryConfig(
            enabled=True, daemon_url="http://127.0.0.1:43120"
        ),
    )
    middleware = soleaux.metrics.MetricsMiddleware.from_config(enabled)
    assert isinstance(middleware._emitter, soleaux.metrics.DaemonMetricsEmitter)
    assert middleware._namespaces == ("db",)
