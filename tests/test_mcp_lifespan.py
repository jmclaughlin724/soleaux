"""FastMCP lifespan owns exactly one service and always closes it."""

from __future__ import annotations

import asyncio

import fastmcp
import pytest

import soleaux.server
from soleaux.analysis.service import SoleauxService


class _TrackingService(SoleauxService):
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.start_calls = 0
        self.close_calls = 0
        self.close_checkpoint = asyncio.Event()

    async def start(self) -> None:
        self.start_calls += 1

    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.close_calls += 1
        if self.events is not None:
            self.events.append("service")
        self.close_checkpoint.set()


async def test_lifespan_constructs_and_closes_service_exactly_once() -> None:
    services: list[_TrackingService] = []

    def factory() -> _TrackingService:
        service = _TrackingService()
        services.append(service)
        return service

    server = soleaux.server.create_server(service_factory=factory)
    async with fastmcp.Client(server, mode="legacy") as client:
        await client.ping()

    assert len(services) == 1
    assert services[0].start_calls == 1
    assert services[0].close_calls == 1


async def test_lifespan_cleanup_runs_when_cancelled_at_async_checkpoint() -> None:
    service = _TrackingService()
    server = soleaux.server.create_server(service_factory=lambda: service)
    entered = asyncio.Event()

    async def connected() -> None:
        async with fastmcp.Client(server, mode="legacy") as client:
            await client.ping()
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(connected())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.close_checkpoint.is_set()
    assert service.close_calls == 1
