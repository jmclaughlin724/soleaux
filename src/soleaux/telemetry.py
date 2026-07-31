"""Optional read-only telemetry daemon tools.

Mirrors the skills provider contract: ``telemetry_*`` tools attach only when
the workspace explicitly enables ``[telemetry]``. The daemon base URL is the
bare origin; this module owns the ``/api/v1`` prefix, matching the telemetry
workspace's single base-URL convention. All tools are read-only HTTP GET
projections of the daemon API; ingest stays with the telemetry CLI, SDK, and
provider sync.
"""

from __future__ import annotations

import collections.abc
import typing

import fastmcp
import fastmcp.exceptions
import fastmcp.tools
import httpx2

import soleaux.contracts.config
import soleaux.surface

TELEMETRY_API_PREFIX = "/api/v1"

ClientFactory = collections.abc.Callable[[], httpx2.AsyncClient]


def build_client_factory(
    config: soleaux.contracts.config.ResolvedConfig,
) -> ClientFactory:
    """Return a factory for per-call clients bound to the configured daemon."""
    base_url = f"{config.telemetry.daemon_url.rstrip('/')}{TELEMETRY_API_PREFIX}"
    timeout = httpx2.Timeout(config.telemetry.timeout_seconds)

    def create() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(base_url=base_url, timeout=timeout)

    return create


def attach_telemetry_tools[LifespanT](
    server: fastmcp.FastMCP[LifespanT],
    config: soleaux.contracts.config.ResolvedConfig,
    *,
    client_factory: ClientFactory | None = None,
) -> bool:
    """Attach the read-only telemetry tools when ``[telemetry]`` is enabled."""
    if not config.telemetry.enabled:
        return False
    factory = client_factory or build_client_factory(config)
    daemon_origin = config.telemetry.daemon_url

    async def _get(path: str) -> typing.Any:
        try:
            async with factory() as client:
                response = await client.get(path)
                response.raise_for_status()
                return response.json()
        except httpx2.HTTPError as exc:
            raise fastmcp.exceptions.ToolError(
                f"telemetry daemon unavailable at {daemon_origin}: {exc}"
            ) from exc

    def _telemetry_tool(
        name: str, description: str, summary: str
    ) -> collections.abc.Callable[
        [collections.abc.Callable[..., typing.Any]],
        collections.abc.Callable[..., typing.Any],
    ]:
        return fastmcp.tools.tool(
            name=name,
            description=description,
            annotations=soleaux.surface.readonly_annotations(),
            meta=soleaux.surface.soleaux_tool_meta(summary=summary, external=True),
        )

    @_telemetry_tool(
        "telemetry_list_sessions",
        "List agent sessions currently known to the telemetry daemon.",
        "Telemetry daemon sessions",
    )
    async def telemetry_list_sessions() -> list[dict[str, typing.Any]]:
        return await _get("/sessions")

    async def _list_processes(session_id: str | None) -> list[dict[str, typing.Any]]:
        processes = await _get("/processes")
        if session_id is None:
            return processes
        return [process for process in processes if process.get("sessionId") == session_id]

    @_telemetry_tool(
        "telemetry_list_processes",
        "List daemon-observed processes, optionally restricted to one session.",
        "Telemetry daemon processes",
    )
    async def telemetry_list_processes(
        session_id: str | None = None,
    ) -> list[dict[str, typing.Any]]:
        return await _list_processes(session_id)

    @_telemetry_tool(
        "telemetry_inspect_session",
        "Return one session with attributed processes, LLM requests, and resource totals.",
        "Telemetry session detail",
    )
    async def telemetry_inspect_session(session_id: str) -> dict[str, typing.Any]:
        sessions = await _get("/sessions")
        session = next((item for item in sessions if item.get("id") == session_id), None)
        if session is None:
            raise fastmcp.exceptions.ToolError(f"unknown telemetry session: {session_id}")
        processes = await _list_processes(session_id)
        events = await _get("/usage/events")
        usage = [event for event in events if event.get("sessionId") == session_id]
        return {
            "session": session,
            "processes": processes,
            "usage": usage,
            "totals": {
                "cpuPercent": sum(float(process.get("cpuPercent", 0)) for process in processes),
                "residentMemoryBytes": sum(
                    int(process.get("residentMemoryBytes", 0)) for process in processes
                ),
                "processCount": len(processes),
                "requests": len(usage),
                "tokens": sum(int(event.get("usage", {}).get("totalTokens", 0)) for event in usage),
            },
        }

    @_telemetry_tool(
        "telemetry_provider_usage",
        "Aggregated API tokens, costs, credits, errors, latency, TTFT, "
        "throughput, and quota windows per provider.",
        "Telemetry provider usage",
    )
    async def telemetry_provider_usage(
        provider_id: str | None = None,
    ) -> list[dict[str, typing.Any]]:
        summaries = await _get("/usage/summary")
        if provider_id is None:
            return summaries
        return [summary for summary in summaries if summary.get("providerId") == provider_id]

    @_telemetry_tool(
        "telemetry_subscription_limits",
        "Provider-reported five-hour, weekly, model-specific, credit, and other quota snapshots.",
        "Telemetry quota windows",
    )
    async def telemetry_subscription_limits(
        provider_id: str | None = None,
    ) -> list[dict[str, typing.Any]]:
        quotas = await _get("/quotas")
        if provider_id is None:
            return quotas
        return [quota for quota in quotas if quota.get("providerId") == provider_id]

    @_telemetry_tool(
        "telemetry_context_pressure",
        "Find requests approaching their model context-window limits.",
        "Telemetry context pressure",
    )
    async def telemetry_context_pressure(
        minimum_percent: float = 80.0,
    ) -> list[dict[str, typing.Any]]:
        events = await _get("/usage/events")
        pressured = [
            event
            for event in events
            if float(event.get("contextUtilizationPercent") or 0) >= minimum_percent
        ]
        return sorted(
            pressured,
            key=lambda event: float(event.get("contextUtilizationPercent") or 0),
            reverse=True,
        )

    @_telemetry_tool(
        "telemetry_compare_models",
        "Aggregate latency, TTFT, throughput, failures, token use, and cost by model.",
        "Telemetry model comparison",
    )
    async def telemetry_compare_models(
        provider_id: str | None = None,
    ) -> list[dict[str, typing.Any]]:
        events = await _get("/usage/events")
        if provider_id is not None:
            events = [event for event in events if event.get("providerId") == provider_id]
        grouped: dict[str, list[dict[str, typing.Any]]] = {}
        for event in events:
            grouped.setdefault(str(event.get("modelId", "unknown")), []).append(event)
        result: list[dict[str, typing.Any]] = []
        for model, model_events in grouped.items():
            latencies = [
                float(event["performance"]["latencyMs"])
                for event in model_events
                if event.get("performance", {}).get("latencyMs") is not None
            ]
            ttfts = [
                float(event["performance"]["timeToFirstTokenMs"])
                for event in model_events
                if event.get("performance", {}).get("timeToFirstTokenMs") is not None
            ]
            rates = [
                float(event["performance"]["tokensPerSecond"])
                for event in model_events
                if event.get("performance", {}).get("tokensPerSecond") is not None
            ]
            result.append(
                {
                    "modelId": model,
                    "requests": len(model_events),
                    "failures": sum(
                        event.get("performance", {}).get("status") == "failed"
                        for event in model_events
                    ),
                    "tokens": sum(
                        int(event.get("usage", {}).get("totalTokens", 0)) for event in model_events
                    ),
                    "estimatedCostUsd": sum(
                        float(event.get("estimatedCostUsd") or 0) for event in model_events
                    ),
                    "averageLatencyMs": (sum(latencies) / len(latencies) if latencies else None),
                    "averageTimeToFirstTokenMs": (sum(ttfts) / len(ttfts) if ttfts else None),
                    "averageTokensPerSecond": (sum(rates) / len(rates) if rates else None),
                }
            )
        return sorted(result, key=lambda item: item["tokens"], reverse=True)

    @_telemetry_tool(
        "telemetry_resource_drain",
        "Identify the highest CPU and memory consumers visible to the daemon.",
        "Telemetry resource drain",
    )
    async def telemetry_resource_drain(
        session_id: str | None = None,
    ) -> dict[str, typing.Any]:
        processes = await _list_processes(session_id)
        by_cpu = sorted(
            processes,
            key=lambda process: float(process.get("cpuPercent", 0)),
            reverse=True,
        )[:10]
        by_memory = sorted(
            processes,
            key=lambda process: int(process.get("residentMemoryBytes", 0)),
            reverse=True,
        )[:10]
        return {"sessionId": session_id, "topCpu": by_cpu, "topMemory": by_memory}

    for telemetry_tool in (
        telemetry_list_sessions,
        telemetry_list_processes,
        telemetry_inspect_session,
        telemetry_provider_usage,
        telemetry_subscription_limits,
        telemetry_context_pressure,
        telemetry_compare_models,
        telemetry_resource_drain,
    ):
        server.add_tool(telemetry_tool)
    return True


__all__: tuple[str, ...] = (
    "TELEMETRY_API_PREFIX",
    "ClientFactory",
    "attach_telemetry_tools",
    "build_client_factory",
)
