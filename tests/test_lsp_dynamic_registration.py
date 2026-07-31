"""D031: dynamic registrations retain exact IDs, methods, and options."""

import asyncio
import pathlib
import sys

import _assertions
import pytest

import soleaux.lsp.broker
import soleaux.lsp.contracts

FAKE_SERVER = (
    pathlib.Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"
)


async def _wait_for_registration(broker: soleaux.lsp.broker.LspBroker) -> None:
    for _ in range(50):
        if broker.get_registration("definition-watch") is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("fake server registration was not applied")


async def _wait_for_diagnostic_registration(broker: soleaux.lsp.broker.LspBroker) -> None:
    for _ in range(50):
        if broker.registrations_by_method("textDocument/diagnostic"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("fake server diagnostic registration was not applied")


async def _wait_for_server_response(
    broker: soleaux.lsp.broker.LspBroker,
    request_id: str,
) -> dict[str, object]:
    for _ in range(50):
        state = await broker.request("test/state")
        for response in state["server_responses"]:
            if response.get("id") == request_id:
                return response
        await asyncio.sleep(0.01)
    raise AssertionError(f"fake server did not receive response {request_id!r}")


async def test_unregistration_requires_matching_id_and_method(tmp_path: pathlib.Path) -> None:
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
        await _wait_for_registration(broker)
        registration = broker.get_registration("definition-watch")
        assert registration is not None
        assert registration.method == "textDocument/definition"
        assert registration.register_options == {"documentSelector": None}
        registration_response = await _wait_for_server_response(broker, "register-1")
        assert registration_response.get("result") is None

        await broker.request("test/unregister-wrong")
        await asyncio.sleep(0.05)
        assert broker.get_registration("definition-watch") is not None

        await broker.request("test/unregister")
        unregistration_response = await _wait_for_server_response(broker, "unregister-1")
        assert unregistration_response.get("result") is None
        for _ in range(50):
            if broker.get_registration("definition-watch") is None:
                break
            await asyncio.sleep(0.01)
        assert broker.get_registration("definition-watch") is None
    finally:
        await broker.shutdown()


async def test_shutdown_clears_epoch_scoped_registrations(tmp_path: pathlib.Path) -> None:
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, str(FAKE_SERVER)),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        workspace_root=str(tmp_path),
    )
    await broker.start()
    await _wait_for_registration(broker)

    await broker.shutdown()

    assert broker.get_registration("definition-watch") is None


async def test_dynamic_diagnostic_registration_retains_exact_options(
    tmp_path: pathlib.Path,
) -> None:
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
        await _wait_for_diagnostic_registration(broker)

        registrations = broker.registrations_by_method("textDocument/diagnostic")
        assert len(registrations) == 1
        assert registrations[0].id == "diagnostic-watch"
        assert registrations[0].register_options == {
            "documentSelector": None,
            "identifier": "fake-diagnostics",
            "interFileDependencies": False,
            "workspaceDiagnostics": False,
        }
    finally:
        await broker.shutdown()


async def test_diagnostic_refresh_returns_null_and_invalidates_result_ids(
    tmp_path: pathlib.Path,
) -> None:
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, str(FAKE_SERVER)),
            provider_name="fake-lsp",
            provider_version="1",
        ),
        workspace_root=str(tmp_path),
    )
    uri = (tmp_path / "main.py").as_uri()
    try:
        await broker.start()
        broker.bind_diagnostic_generation(
            uri,
            document_version=1,
            generation_fingerprint="generation-a",
        )
        broker.apply_diagnostic_pull_report(
            uri,
            {
                "kind": "full",
                "resultId": "result-1",
                "items": [],
            },
        )
        assert (
            broker.diagnostic_previous_result_id(
                uri,
                document_version=1,
                generation_fingerprint="generation-a",
            )
            == "result-1"
        )

        await broker.request("test/diagnostic-refresh")
        response = await _wait_for_server_response(broker, "diagnostic-refresh")

        assert response.get("result") is None
        assert (
            broker.diagnostic_previous_result_id(
                uri,
                document_version=1,
                generation_fingerprint="generation-a",
            )
            is None
        )
    finally:
        await broker.shutdown()


@pytest.mark.parametrize(
    ("case", "rejected_registration_id"),
    [
        ("duplicate-existing", None),
        ("duplicate-batch", "batch-duplicate"),
        ("malformed-options", "malformed-options"),
        ("unknown-unregistration", None),
    ],
)
async def test_invalid_registration_mutations_are_rejected_atomically(
    tmp_path: pathlib.Path,
    case: str,
    rejected_registration_id: str | None,
) -> None:
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
        await _wait_for_registration(broker)

        await broker.request("test/invalid-registration", {"case": case})
        response = await _wait_for_server_response(broker, f"invalid-{case}")

        raw_error = response.get("error")
        error = _assertions.object_mapping(raw_error)
        assert error.get("code") == soleaux.lsp.broker.INTERNAL_ERROR
        registration = broker.get_registration("definition-watch")
        assert registration is not None
        assert registration.method == "textDocument/definition"
        if rejected_registration_id is not None:
            assert broker.get_registration(rejected_registration_id) is None
    finally:
        await broker.shutdown()
