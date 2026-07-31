"""AC16 / D018: one parse per content hash and keyed single-flight sharing."""

import asyncio

import pytest

import soleaux.analysis.task_registry
import soleaux.structural.supervisor

FIXTURE = b"""import os


def one():
    return os.name
"""


@pytest.fixture
async def supervisor():
    instance = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_multiple_projections_parse_the_content_exactly_once(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="Python",
        path="one.py",
        content=FIXTURE,
        projections=("syntax.declarations", "syntax.imports", "syntax.exports"),
    )
    assert result.parses == 1
    assert supervisor.completed_jobs == 1
    projections = {row.projection for row in result.fragments}
    assert "syntax.declarations" in projections
    assert "syntax.imports" in projections


async def test_identical_structural_work_reuses_the_bounded_memory_cache(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    first = await supervisor.extract(
        language="Python",
        path="one.py",
        content=FIXTURE,
        projections=("syntax.declarations",),
        workspace_id="workspace",
    )
    second = await supervisor.extract(
        language="Python",
        path="one.py",
        content=FIXTURE,
        projections=("syntax.declarations",),
        workspace_id="workspace",
    )

    assert second == first
    assert supervisor.total_completed_jobs == 1


async def test_equivalent_requests_await_one_keyed_task() -> None:
    keyed = soleaux.analysis.task_registry.TaskRegistry()
    calls = 0

    async def work() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "result"

    first, second = await asyncio.gather(
        keyed.share("key", work),
        keyed.share("key", work),
    )
    assert (first, second) == ("result", "result")
    assert calls == 1
    assert keyed.in_flight == 0


async def test_different_keys_and_failure_retry() -> None:
    keyed = soleaux.analysis.task_registry.TaskRegistry()
    calls = 0

    async def work() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    async def failing() -> str:
        raise RuntimeError("boom")

    await asyncio.gather(keyed.share("a", work), keyed.share("b", work))
    assert calls == 2
    with pytest.raises(RuntimeError):
        await keyed.share("bad", failing)
    with pytest.raises(RuntimeError):
        await keyed.share("bad", failing)
    assert keyed.in_flight == 0


async def test_one_waiter_leaving_does_not_cancel_shared_work() -> None:
    keyed = soleaux.analysis.task_registry.TaskRegistry()
    finished = False

    async def work() -> str:
        nonlocal finished
        await asyncio.sleep(0.05)
        finished = True
        return "done"

    waiter_one = asyncio.ensure_future(keyed.share("key", work))
    waiter_two = asyncio.ensure_future(keyed.share("key", work))
    await asyncio.sleep(0.01)
    waiter_one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_one
    assert await waiter_two == "done"
    assert finished is True
