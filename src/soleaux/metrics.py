"""Per-backend MCP tool-call metrics middleware (D034 attribution).

One FastMCP middleware instance per server times tool calls, attributes each
to its backend namespace (``fastmcp.server.transforms.Namespace`` renders
proxied tools as ``<namespace>_<tool>``; local catalog tools carry no prefix
and attribute to ``local``), and counts successes and errors. Each completed
operation is forwarded toward the telemetry daemon's MCP-ingest route
(``/api/v1/mcp/events``, owned by GW-13) through a bounded fire-and-forget
emitter: emission is fail-open and never raises into, or blocks, the request
path. ``MetricsMiddleware.snapshot`` exposes the recent in-memory aggregate
per backend for the ``soleaux://mcp/v1`` resource.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import typing
from collections.abc import Sequence
from datetime import UTC, datetime

import fastmcp.tools
import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

import soleaux.contracts.config
import soleaux.telemetry

logger = logging.getLogger(__name__)

LOCAL_BACKEND = "local"
# Relative to the client base URL, which already carries the "/api/v1" prefix
# (soleaux.telemetry.TELEMETRY_API_PREFIX); the daemon route is
# "/api/v1/mcp/events".
MCP_EVENT_INGEST_PATH = "/mcp/events"
_MAX_PENDING_EMISSIONS = 64


@dataclasses.dataclass(frozen=True)
class ToolCallEvent:
    """One completed tool operation, serialized for the daemon ingest route."""

    operation: str
    backend: str
    tool_name: str
    duration_ms: float
    ok: bool
    error_type: str | None
    at: str

    def payload(self) -> dict[str, typing.Any]:
        return dataclasses.asdict(self)


class MetricsEmitter(typing.Protocol):
    """Emission sink for tool-call events; implementations must not raise."""

    async def emit(self, event: ToolCallEvent) -> None: ...


class NoopMetricsEmitter:
    """Default sink when ``[telemetry]`` is disabled; drops every event."""

    async def emit(self, event: ToolCallEvent) -> None:
        _ = event


class DaemonMetricsEmitter:
    """POST each event to the daemon MCP-ingest route; fail-open by contract."""

    def __init__(self, client_factory: soleaux.telemetry.ClientFactory) -> None:
        self._client_factory = client_factory

    async def emit(self, event: ToolCallEvent) -> None:
        try:
            async with self._client_factory() as client:
                await client.post(MCP_EVENT_INGEST_PATH, json=event.payload())
        except Exception:
            logger.debug("soleaux metrics event dropped: daemon unreachable", exc_info=True)


@dataclasses.dataclass
class _BackendAggregate:
    calls: int = 0
    errors: int = 0
    total_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    last_call_at: str | None = None
    tool_calls: dict[str, int] = dataclasses.field(default_factory=dict[str, int])


class MetricsMiddleware(Middleware):
    """Time and attribute tool calls per backend, emitting fail-open events."""

    def __init__(
        self,
        *,
        backends: tuple[str, ...] = (),
        emitter: MetricsEmitter | None = None,
    ) -> None:
        # Longest first so a namespace that prefixes another still wins.
        self._namespaces = tuple(sorted(backends, key=len, reverse=True))
        self._emitter: MetricsEmitter = emitter if emitter is not None else NoopMetricsEmitter()
        self._aggregates: dict[str, _BackendAggregate] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._emitted = 0
        self._dropped = 0

    @classmethod
    def from_config(cls, config: soleaux.contracts.config.ResolvedConfig) -> MetricsMiddleware:
        emitter: MetricsEmitter = (
            DaemonMetricsEmitter(soleaux.telemetry.build_client_factory(config))
            if config.telemetry.enabled
            else NoopMetricsEmitter()
        )
        backends = tuple(name for name, backend in config.mcp.items() if backend.enabled)
        return cls(backends=backends, emitter=emitter)

    def backend_for(self, tool_name: str) -> str:
        for namespace in self._namespaces:
            if tool_name.startswith(f"{namespace}_"):
                return namespace
        return LOCAL_BACKEND

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, fastmcp.tools.ToolResult],
    ) -> fastmcp.tools.ToolResult:
        tool_name = context.message.name
        started = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            self._record("tools/call", tool_name, started, ok=False, error=exc)
            raise
        self._record("tools/call", tool_name, started, ok=not result.is_error, error=None)
        return result

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[fastmcp.tools.Tool]],
    ) -> Sequence[fastmcp.tools.Tool]:
        started = time.perf_counter()
        try:
            tools = await call_next(context)
        except Exception as exc:
            self._record("tools/list", "tools/list", started, ok=False, error=exc)
            raise
        self._record("tools/list", "tools/list", started, ok=True, error=None)
        return tools

    def _record(
        self,
        operation: str,
        tool_name: str,
        started: float,
        *,
        ok: bool,
        error: BaseException | None,
    ) -> None:
        duration_ms = (time.perf_counter() - started) * 1000
        backend = LOCAL_BACKEND if operation == "tools/list" else self.backend_for(tool_name)
        at = datetime.now(UTC).isoformat()
        aggregate = self._aggregates.setdefault(backend, _BackendAggregate())
        aggregate.calls += 1
        aggregate.errors += 0 if ok else 1
        aggregate.total_duration_ms += duration_ms
        aggregate.max_duration_ms = max(aggregate.max_duration_ms, duration_ms)
        aggregate.last_call_at = at
        if operation == "tools/call":
            aggregate.tool_calls[tool_name] = aggregate.tool_calls.get(tool_name, 0) + 1
        self._emit_detached(
            ToolCallEvent(
                operation=operation,
                backend=backend,
                tool_name=tool_name,
                duration_ms=duration_ms,
                ok=ok,
                # Exception type only; messages can carry backend internals.
                error_type=None if ok else (type(error).__name__ if error else "error_result"),
                at=at,
            )
        )

    def _emit_detached(self, event: ToolCallEvent) -> None:
        if len(self._pending) >= _MAX_PENDING_EMISSIONS:
            self._dropped += 1
            return
        self._emitted += 1
        task = asyncio.create_task(self._emit_safely(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _emit_safely(self, event: ToolCallEvent) -> None:
        try:
            await self._emitter.emit(event)
        except Exception:
            logger.debug("soleaux metrics emission failed", exc_info=True)

    def snapshot(self) -> dict[str, typing.Any]:
        """Recent per-backend aggregate plus emission counters, JSON-safe."""
        return {
            "schema_version": "soleaux.mcp-metrics/v1",
            "backends": {
                backend: {
                    "calls": aggregate.calls,
                    "errors": aggregate.errors,
                    "totalDurationMs": round(aggregate.total_duration_ms, 3),
                    "maxDurationMs": round(aggregate.max_duration_ms, 3),
                    "averageDurationMs": round(aggregate.total_duration_ms / aggregate.calls, 3),
                    "lastCallAt": aggregate.last_call_at,
                    "tools": dict(
                        sorted(
                            aggregate.tool_calls.items(),
                            key=lambda item: item[1],
                            reverse=True,
                        )
                    ),
                }
                for backend, aggregate in sorted(self._aggregates.items())
            },
            "emissions": {
                "emitted": self._emitted,
                "dropped": self._dropped,
                "pending": len(self._pending),
            },
        }


__all__: tuple[str, ...] = (
    "LOCAL_BACKEND",
    "MCP_EVENT_INGEST_PATH",
    "DaemonMetricsEmitter",
    "MetricsEmitter",
    "MetricsMiddleware",
    "NoopMetricsEmitter",
    "ToolCallEvent",
)
