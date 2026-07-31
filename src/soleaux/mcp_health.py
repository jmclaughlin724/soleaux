"""Bounded background health tracking for enabled ``[mcp.*]`` backends (D035).

One tracker per service lifespan probes each enabled MCP proxy backend on a
bounded interval from a lifespan-owned task; request-path surfaces
(``describe``, ``soleaux://about``) only read the latest in-memory snapshot.
Probing is fail-open: a dead backend never affects the tracker or the server,
and an OAuth backend without stored tokens reports ``unauthenticated``
(distinct from ``down``) instead of triggering the interactive login flow.
A backend that has probed ``ok`` at least once and then fails reports
``degraded``; one that has never probed ``ok`` reports ``down``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import os
import pathlib
import time
import typing
from datetime import UTC, datetime

import soleaux.contracts.config

logger = logging.getLogger(__name__)

MCP_HEALTH_SCHEMA_VERSION = "soleaux.mcp-health/v1"
DEFAULT_PROBE_INTERVAL_SECONDS = 300.0
_CLOSE_JOIN_TIMEOUT_SECONDS = 5.0
_MAX_ERROR_LENGTH = 200

type BackendHealthState = typing.Literal["ok", "degraded", "unauthenticated", "down", "unknown"]


@dataclasses.dataclass(frozen=True)
class BackendHealthSnapshot:
    """The latest known health of one configured MCP backend."""

    name: str
    enabled: bool
    transport: str
    lifecycle: str
    auth: str
    state: BackendHealthState
    tool_count: int | None
    catalog_digest: str | None
    server_version: str | None
    last_probe_at: str | None
    last_error: str | None
    elapsed_ms: float | None

    def payload(self) -> dict[str, typing.Any]:
        return dataclasses.asdict(self)


async def _has_stored_tokens(
    backend: soleaux.contracts.config.McpBackendConfig,
    *,
    backend_name: str,
) -> bool:
    """True when the shared token store holds tokens for one OAuth backend."""
    from fastmcp.client.auth.oauth import TokenStorageAdapter

    import soleaux.credentials

    store = soleaux.credentials.build_token_store(backend, backend_name=backend_name)
    adapter = TokenStorageAdapter(async_key_value=store, server_url=str(backend.url))
    return await adapter.get_tokens() is not None


class McpHealthTracker:
    """Own the bounded background probe loop for one service lifespan."""

    def __init__(
        self,
        root: pathlib.Path,
        config: soleaux.contracts.config.ResolvedConfig,
        *,
        probe_interval_seconds: float = DEFAULT_PROBE_INTERVAL_SECONDS,
    ) -> None:
        self._root = root
        self._config = config
        self._probe_interval_seconds = probe_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._ever_ok: set[str] = set()
        self._snapshots: dict[str, BackendHealthSnapshot] = {
            name: self._seed(name, backend) for name, backend in config.mcp.items()
        }

    @staticmethod
    def _seed(
        name: str,
        backend: soleaux.contracts.config.McpBackendConfig,
    ) -> BackendHealthSnapshot:
        return BackendHealthSnapshot(
            name=name,
            enabled=backend.enabled,
            transport="command" if backend.command is not None else "url",
            lifecycle=backend.lifecycle,
            auth=backend.auth,
            state="unknown",
            tool_count=None,
            catalog_digest=None,
            server_version=None,
            last_probe_at=None,
            last_error=None,
            elapsed_ms=None,
        )

    def snapshots(self) -> tuple[BackendHealthSnapshot, ...]:
        """Latest per-backend snapshots in name order; never blocks."""
        return tuple(self._snapshots[name] for name in sorted(self._snapshots))

    def payload(self) -> dict[str, typing.Any]:
        """JSON-safe health surface for ``describe`` and ``soleaux://about``."""
        return {
            "schema_version": MCP_HEALTH_SCHEMA_VERSION,
            "probe_interval_seconds": self._probe_interval_seconds,
            "backend_count": len(self._snapshots),
            "backends": [snapshot.payload() for snapshot in self.snapshots()],
        }

    async def start(self) -> None:
        """Start the background probe loop; the first probe never blocks start."""
        if self._closed:
            raise RuntimeError("MCP health tracker is closed")
        if self._task is not None:
            return
        if not any(backend.enabled for backend in self._config.mcp.values()):
            return
        self._task = asyncio.create_task(self._run(), name="soleaux-mcp-health")

    async def aclose(self) -> None:
        """Cancel the probe loop and bound the join so shutdown never stalls."""
        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, timeout=_CLOSE_JOIN_TIMEOUT_SECONDS)

    async def probe_once(self) -> None:
        """Probe every enabled backend once; fail-open per backend."""
        for name, backend in sorted(self._config.mcp.items()):
            if not backend.enabled:
                continue
            try:
                await self._probe_backend(name, backend)
            except Exception as exc:  # one backend cannot terminate the tracker
                self._record_failure(name, error=str(exc)[:_MAX_ERROR_LENGTH])

    async def _run(self) -> None:
        while True:
            try:
                await self.probe_once()
            except Exception:  # the loop survives an unexpected probe-round failure
                logger.debug("soleaux MCP health probe round failed", exc_info=True)
            await asyncio.sleep(self._probe_interval_seconds)

    async def _probe_backend(
        self,
        name: str,
        backend: soleaux.contracts.config.McpBackendConfig,
    ) -> None:
        from fastmcp import Client

        from soleaux.gateway import _transport_factory

        if backend.auth == "oauth" and not await _has_stored_tokens(backend, backend_name=name):
            # Probing an unauthenticated OAuth backend would trigger the
            # interactive flow; report the login action instead.
            self._record(
                name,
                state="unauthenticated",
                error=f"not authenticated; run `soleaux mcp login {name}`",
                tool_count=None,
                elapsed_ms=None,
            )
            return
        if (
            backend.auth == "bearer_env"
            and backend.auth_token_env is not None
            and not os.environ.get(backend.auth_token_env)
        ):
            self._record(
                name,
                state="unauthenticated",
                error=(
                    "MCP auth token environment variable is missing or empty: "
                    f"{backend.auth_token_env}"
                ),
                tool_count=None,
                elapsed_ms=None,
            )
            return
        started = time.perf_counter()
        client: Client[typing.Any] | None = None
        try:
            transport = _transport_factory(backend, self._root, backend_name=name)()
            client = Client(
                transport,
                init_timeout=backend.init_timeout_seconds,
                timeout=backend.request_timeout_seconds,
            )
            async with client:
                tools = await client.list_tools()
                server_info = client.server_info
        except asyncio.CancelledError:
            # Tracker shutdown cancelled the probe while the backend was still
            # connecting (e.g. a hanging command backend). `Client.__aenter__`
            # never completed, so `__aexit__` never runs and the spawned child
            # would be orphaned; force the disconnect before propagating.
            if client is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.close(), timeout=_CLOSE_JOIN_TIMEOUT_SECONDS)
            raise
        except Exception as exc:
            self._record_failure(name, error=str(exc)[:_MAX_ERROR_LENGTH])
            return
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        digest = hashlib.sha256("\n".join(sorted(tool.name for tool in tools)).encode()).hexdigest()
        server_version = server_info.version if server_info is not None else None
        self._ever_ok.add(name)
        self._record(
            name,
            state="ok",
            error=None,
            tool_count=len(tools),
            elapsed_ms=elapsed_ms,
            catalog_digest=digest,
            server_version=server_version,
        )

    def _record_failure(self, name: str, *, error: str) -> None:
        # A backend that probed ok before regressed; one that never did is down.
        state: BackendHealthState = "degraded" if name in self._ever_ok else "down"
        self._record(name, state=state, error=error, tool_count=None, elapsed_ms=None)

    def _record(
        self,
        name: str,
        *,
        state: BackendHealthState,
        error: str | None,
        tool_count: int | None,
        elapsed_ms: float | None,
        catalog_digest: str | None = None,
        server_version: str | None = None,
    ) -> None:
        previous = self._snapshots[name]
        self._snapshots[name] = dataclasses.replace(
            previous,
            state=state,
            tool_count=tool_count,
            catalog_digest=catalog_digest if state == "ok" else previous.catalog_digest,
            server_version=server_version if state == "ok" else previous.server_version,
            last_probe_at=datetime.now(UTC).isoformat(),
            last_error=error,
            elapsed_ms=elapsed_ms,
        )


__all__: tuple[str, ...] = (
    "DEFAULT_PROBE_INTERVAL_SECONDS",
    "MCP_HEALTH_SCHEMA_VERSION",
    "BackendHealthSnapshot",
    "BackendHealthState",
    "McpHealthTracker",
)
