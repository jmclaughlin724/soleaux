"""Per-URI diagnostic state freshness and push/pull normalization."""

from __future__ import annotations

import asyncio

import _assertions

import soleaux.lsp.diagnostics

URI_A = "file:///workspace/a.py"
URI_B = "file:///workspace/b.py"
EPOCH = 7
GENERATION = "generation-a"


def _diagnostic(message: str) -> dict[str, object]:
    return {
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
        "message": message,
        "severity": 1,
    }


def _bind(
    store: soleaux.lsp.diagnostics.DiagnosticStateStore,
    uri: str,
    *,
    version: int = 1,
    epoch: int = EPOCH,
    generation: str = GENERATION,
) -> None:
    store.bind(
        uri,
        document_version=version,
        provider_epoch=epoch,
        generation_fingerprint=generation,
    )


def _current(
    store: soleaux.lsp.diagnostics.DiagnosticStateStore,
    uri: str,
    *,
    version: int = 1,
    epoch: int = EPOCH,
    generation: str = GENERATION,
) -> soleaux.lsp.diagnostics.DiagnosticState | None:
    return store.current(
        uri,
        document_version=version,
        provider_epoch=epoch,
        generation_fingerprint=generation,
    )


def _required_current(
    store: soleaux.lsp.diagnostics.DiagnosticStateStore,
    uri: str,
    *,
    version: int = 1,
    epoch: int = EPOCH,
    generation: str = GENERATION,
) -> soleaux.lsp.diagnostics.DiagnosticState:
    state = _current(
        store,
        uri,
        version=version,
        epoch=epoch,
        generation=generation,
    )
    assert state is not None
    return state


def test_push_diagnostics_are_uri_isolated_replaced_cleared_and_version_ordered() -> None:
    store = soleaux.lsp.diagnostics.DiagnosticStateStore()
    _bind(store, URI_A)
    _bind(store, URI_B)

    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("a")]})
    assert store.publish({"uri": URI_B, "version": 1, "diagnostics": [_diagnostic("b")]})
    assert not store.publish({"uri": URI_A, "version": 0, "diagnostics": [_diagnostic("stale")]})
    assert _required_current(store, URI_A).items == (_diagnostic("a"),)

    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("replacement")]})
    assert _required_current(store, URI_A).items == (_diagnostic("replacement"),)
    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": []})
    assert _required_current(store, URI_A).items == ()
    assert _required_current(store, URI_B).items == (_diagnostic("b"),)


def test_generation_or_provider_epoch_change_invalidates_retained_state() -> None:
    store = soleaux.lsp.diagnostics.DiagnosticStateStore()
    _bind(store, URI_A)
    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("old")]})

    _bind(store, URI_A, generation="generation-b")
    assert _current(store, URI_A, generation="generation-b") is None
    assert (
        store.previous_result_id(
            URI_A,
            document_version=1,
            provider_epoch=EPOCH,
            generation_fingerprint="generation-b",
        )
        is None
    )
    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("new")]})
    assert _required_current(store, URI_A, generation="generation-b").items == (_diagnostic("new"),)

    _bind(store, URI_A, epoch=EPOCH + 1, generation="generation-b")
    assert _current(store, URI_A, epoch=EPOCH + 1, generation="generation-b") is None


def test_pull_full_replaces_and_unchanged_retains_items_and_result_id() -> None:
    store = soleaux.lsp.diagnostics.DiagnosticStateStore()
    _bind(store, URI_A)

    full = store.apply_pull_report(
        URI_A,
        {
            "kind": "full",
            "resultId": "result-1",
            "items": [_diagnostic("full")],
        },
    )
    assert full.items == (_diagnostic("full"),)
    assert full.result_id == "result-1"
    assert (
        store.previous_result_id(
            URI_A,
            document_version=1,
            provider_epoch=EPOCH,
            generation_fingerprint=GENERATION,
        )
        == "result-1"
    )

    unchanged = store.apply_pull_report(
        URI_A,
        {"kind": "unchanged", "resultId": "result-2"},
    )
    assert unchanged.items == full.items
    assert unchanged.result_id == "result-2"
    assert unchanged.updated_at >= full.updated_at

    _bind(store, URI_B)
    with _assertions.raises_with_message(
        soleaux.lsp.diagnostics.DiagnosticProtocolError, "prior compatible state"
    ):
        store.apply_pull_report(
            URI_B,
            {"kind": "unchanged", "resultId": "orphan"},
        )


def test_refresh_invalidates_results_but_preserves_the_uri_binding() -> None:
    store = soleaux.lsp.diagnostics.DiagnosticStateStore()
    _bind(store, URI_A)
    store.apply_pull_report(
        URI_A,
        {
            "kind": "full",
            "resultId": "result-1",
            "items": [_diagnostic("full")],
        },
    )

    store.invalidate()

    assert _current(store, URI_A) is None
    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("push")]})
    assert _required_current(store, URI_A).items == (_diagnostic("push"),)


async def test_wait_is_uri_specific_and_times_out_without_compatible_publication() -> None:
    store = soleaux.lsp.diagnostics.DiagnosticStateStore()
    _bind(store, URI_A)
    _bind(store, URI_B)
    waiting = asyncio.create_task(
        store.wait(
            URI_A,
            document_version=1,
            provider_epoch=EPOCH,
            generation_fingerprint=GENERATION,
            timeout=0.2,
        )
    )

    assert store.publish({"uri": URI_B, "version": 1, "diagnostics": [_diagnostic("other")]})
    await asyncio.sleep(0)
    assert not waiting.done()

    assert store.publish({"uri": URI_A, "version": 1, "diagnostics": [_diagnostic("target")]})
    state = await waiting
    assert state is not None
    assert state.items == (_diagnostic("target"),)

    _bind(store, URI_A, version=2, generation="generation-b")
    assert (
        await store.wait(
            URI_A,
            document_version=2,
            provider_epoch=EPOCH,
            generation_fingerprint="generation-b",
            timeout=0.01,
        )
        is None
    )
