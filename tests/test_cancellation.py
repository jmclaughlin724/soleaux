"""AC23: cancellation propagates to the provider and clears pending state."""

import asyncio
import pathlib
import sys

import pytest

import soleaux.analysis.task_registry
import soleaux.lsp.broker
import soleaux.lsp.contracts

FAKE_SERVER = (
    pathlib.Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"
)


async def test_caller_cancellation_sends_cancel_request(tmp_path: pathlib.Path) -> None:
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, str(FAKE_SERVER)),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        workspace_root=str(tmp_path),
    )
    try:
        await broker.start()
        request = asyncio.create_task(broker.request("test/sleep", timeout=10.0))
        await asyncio.sleep(0.05)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        state = await broker.request("test/state")
        assert state["cancelled"]
        assert broker.pending_request_count == 0
    finally:
        await broker.shutdown()


async def test_late_response_after_cancellation_consumes_tombstone(tmp_path: pathlib.Path) -> None:
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, str(FAKE_SERVER)),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        workspace_root=str(tmp_path),
    )
    try:
        await broker.start()
        request = asyncio.create_task(broker.request("test/late", timeout=10.0))
        await asyncio.sleep(0.05)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        for _ in range(50):
            if broker.late_response_count:
                break
            await asyncio.sleep(0.01)
        assert broker.late_response_count == 1
        assert broker.pending_request_count == 0
    finally:
        await broker.shutdown()


async def test_same_key_waiters_share_one_error() -> None:
    keyed = soleaux.analysis.task_registry.TaskRegistry()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fail() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        raise RuntimeError("shared failure")

    first = asyncio.create_task(keyed.share("same", fail))
    second = asyncio.create_task(keyed.share("same", fail))
    await started.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(result, RuntimeError) for result in results)
    assert {str(result) for result in results} == {"shared failure"}
    assert keyed.in_flight == 0


async def test_final_waiter_departure_cancels_shared_work() -> None:
    keyed = soleaux.analysis.task_registry.TaskRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def wait_forever() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    waiter = asyncio.create_task(keyed.share("final", wait_forever))
    await started.wait()
    waiter.cancel()
    await asyncio.wait_for(cancelled.wait(), timeout=0.25)
    assert waiter.done() is False
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cleanup_finished.is_set()
    assert keyed.in_flight == 0
