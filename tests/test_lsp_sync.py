"""D023: bounded document state follows negotiated open/close and change semantics."""

import asyncio
import pathlib
import sys

import soleaux.contracts.budget
import soleaux.contracts.workspace
import soleaux.lsp.broker
import soleaux.lsp.contracts
import soleaux.lsp.generation
import soleaux.lsp.providers
import soleaux.lsp.sessions
import soleaux.structural.snapshot

FAKE_SERVER = (
    pathlib.Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"
)


def _broker(
    tmp_path: pathlib.Path, budget: soleaux.contracts.budget.LspSessionBudget
) -> soleaux.lsp.broker.LspBroker:
    return soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, str(FAKE_SERVER)),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        budget,
        workspace_root=str(tmp_path),
    )


async def test_document_lru_evicts_without_lock_reentry(tmp_path: pathlib.Path) -> None:
    broker = _broker(tmp_path, soleaux.contracts.budget.LspSessionBudget(max_open_documents=1))
    try:
        await broker.start()
        await broker.open_document("file:///first.py", "python", "first = 1\n")
        await asyncio.wait_for(
            broker.open_document("file:///second.py", "python", "second = 2\n"),
            timeout=1.0,
        )
        assert broker.open_document_count == 1
        state = await broker.request("test/state")
        methods = [entry["method"] for entry in state["notifications"]]
        assert methods.count("textDocument/didOpen") == 2
        assert methods.count("textDocument/didClose") == 1
    finally:
        await broker.shutdown()


async def test_update_of_unknown_document_does_not_deadlock(tmp_path: pathlib.Path) -> None:
    broker = _broker(tmp_path, soleaux.contracts.budget.LspSessionBudget())
    try:
        await broker.start()
        await asyncio.wait_for(broker.update_document("file:///new.py", "value = 2\n"), timeout=1.0)
        assert broker.open_document_count == 1
    finally:
        await broker.shutdown()


async def test_incremental_sync_uses_negotiated_utf16_and_crlf_range(
    tmp_path: pathlib.Path,
) -> None:
    broker = _broker(tmp_path, soleaux.contracts.budget.LspSessionBudget())
    try:
        await broker.start()
        await broker.open_document("file:///unicode.py", "python", "a😀\r\nb́")
        await broker.update_document("file:///unicode.py", "changed\n")
        state = await broker.request("test/state")
        change = next(
            entry for entry in state["notifications"] if entry["method"] == "textDocument/didChange"
        )
        content_change = change["params"]["contentChanges"][0]
        assert content_change["range"] == {
            "start": {"line": 0, "character": 0},
            "end": {"line": 1, "character": 2},
        }
        assert content_change["text"] == "changed\n"
    finally:
        await broker.shutdown()


async def test_session_reopens_a_document_after_broker_lru_eviction(tmp_path: pathlib.Path) -> None:
    for path in ("first.py", "second.py"):
        (tmp_path / path).write_text(f"{path.removesuffix('.py')} = 1\n", encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="lru-test",
    ).get("workspace")
    bundle = await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=("first.py", "second.py")
    )
    provider = soleaux.lsp.providers.ConfiguredProvider(
        provider_name="fake-lsp",
        provider_version="1",
        argv=(sys.executable, str(FAKE_SERVER)),
        extensions=("py",),
        root=tmp_path,
        config_digest="fake-config",
    )
    sessions = soleaux.lsp.sessions.LspSessionManager(
        soleaux.contracts.budget.LspSessionBudget(max_open_documents=1)
    )
    broker: soleaux.lsp.broker.LspBroker | None = None
    try:
        for path in ("first.py", "second.py", "first.py"):
            generation = soleaux.lsp.generation.SemanticGeneration.from_snapshot(
                bundle,
                provider_name=provider.provider_name,
                provider_config_digest=provider.config_digest,
                process_epoch=0,
                requested_file=path,
            )
            broker = await sessions.prepare(
                provider=provider,
                spec=provider.to_spec(".py"),
                generation=generation,
                bundle=bundle,
            )
            assert broker is not None

        state = await broker.request("test/state")
        methods = [entry["method"] for entry in state["notifications"]]
        assert methods.count("textDocument/didOpen") == 3
        assert methods.count("textDocument/didClose") == 2
    finally:
        await sessions.shutdown()
