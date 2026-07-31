"""Provider failures remain typed, named, and lifecycle-safe."""

from __future__ import annotations

import pathlib
import sys
import typing
import unittest.mock

import pydantic
import pytest

import soleaux.contracts.coverage
import soleaux.contracts.workspace
import soleaux.lsp.broker
import soleaux.lsp.contracts
import soleaux.lsp.generation
import soleaux.lsp.providers
import soleaux.lsp.resolvers
import soleaux.lsp.sessions
import soleaux.postgresql.runtime
import soleaux.structural.snapshot

FailureMode = typing.Literal["crash", "timeout", "malformed"]


class _ReadyBroker(soleaux.lsp.broker.LspBroker):
    def __init__(self) -> None:
        super().__init__(
            soleaux.lsp.contracts.LanguageServerSpec(
                language="Python",
                argv=("unused-provider",),
                provider_name="failing-lsp",
                provider_version="1",
            )
        )
        self._capabilities = soleaux.lsp.contracts.ServerCapabilities(definition_provider=True)


class _FailureSessions(soleaux.lsp.sessions.LspSessionManager):
    def __init__(self, failure_mode: FailureMode, malformed_payload: object) -> None:
        super().__init__()
        self.failure_mode = failure_mode
        self.malformed_payload = malformed_payload
        self.restarted = False
        self.broker = _ReadyBroker()

    async def prepare(
        self,
        *,
        provider: soleaux.lsp.providers.ConfiguredProvider,
        spec: soleaux.lsp.contracts.LanguageServerSpec,
        generation: soleaux.lsp.generation.SemanticGeneration,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
    ) -> soleaux.lsp.broker.LspBroker | None:
        _ = provider, spec, generation, bundle
        if self.failure_mode == "crash":
            raise soleaux.lsp.broker.ProviderUnavailableError("provider stream closed")
        return self.broker

    async def request(
        self,
        *,
        broker: soleaux.lsp.broker.LspBroker,
        generation: soleaux.lsp.generation.SemanticGeneration,
        method: str,
        params: dict[str, typing.Any],
        response_schema: str,
        timeout: float = 10.0,
    ) -> object:
        _ = broker, generation, method, params, response_schema, timeout
        if self.failure_mode == "timeout":
            raise soleaux.lsp.broker.OperationTimeoutError("LSP request timed out")
        return self.malformed_payload

    async def restart(self, key: soleaux.lsp.sessions.SessionBaseKey) -> None:
        _ = key
        self.restarted = True


async def _snapshot(tmp_path: pathlib.Path) -> soleaux.structural.snapshot.SnapshotBundle:
    (tmp_path / "main.py").write_text("def target() -> int:\n    return 1\n", encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="test",
    ).get("workspace")
    return await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=("main.py",)
    )


async def test_postgres_spawn_error_redacts_carried_environment_values(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_url = "postgresql://reader:spawn-secret@127.0.0.1/local"
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        unittest.mock.AsyncMock(side_effect=OSError(f"failed with {database_url}")),
    )
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="sql",
            argv=("postgres-language-server", "lsp-proxy"),
            root_uri=workspace.as_uri(),
            provider_name="postgres-language-server",
            provider_version="0.25.4",
            environment_names=soleaux.postgresql.runtime.POSTGRESQL_ENVIRONMENT_NAMES,
            environment={"DATABASE_URL": pydantic.SecretStr(database_url)},
        )
    )

    with pytest.raises(soleaux.lsp.broker.ProviderUnavailableError) as caught:
        await broker.start()

    message = str(caught.value)
    assert database_url not in message
    assert "spawn-secret" not in message
    assert "[REDACTED]" in message


async def test_standard_provider_spawn_excludes_unlisted_host_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "host-secret-must-not-reach-language-server"
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", secret)
    spawn = unittest.mock.AsyncMock(side_effect=OSError("fixture spawn failure"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    broker = soleaux.lsp.broker.LspBroker(
        soleaux.lsp.contracts.LanguageServerSpec(
            language="Python",
            argv=(sys.executable, "-c", "raise SystemExit"),
            provider_name="pyright-langserver",
            provider_version="1.1.411",
        )
    )

    with pytest.raises(soleaux.lsp.broker.ProviderUnavailableError):
        await broker.start()

    environment = spawn.call_args.kwargs["env"]
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment
    assert secret not in environment.values()
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {},
        environment_names=(),
    )


@pytest.mark.parametrize(
    ("failure_mode", "expected_status", "expected_reason", "expected_restart"),
    [
        ("crash", soleaux.contracts.coverage.FrameStatus.PARTIAL, "provider stream closed", False),
        ("timeout", soleaux.contracts.coverage.FrameStatus.FAILED, "LSP request timed out", True),
        (
            "malformed",
            soleaux.contracts.coverage.FrameStatus.FAILED,
            "provider location has an invalid range",
            True,
        ),
    ],
)
async def test_provider_failure_is_typed_and_names_the_provider(
    tmp_path: pathlib.Path,
    failure_mode: FailureMode,
    expected_status: soleaux.contracts.coverage.FrameStatus,
    expected_reason: str,
    expected_restart: bool,
) -> None:
    bundle = await _snapshot(tmp_path)
    malformed_payload = {
        "uri": (tmp_path / "main.py").as_uri(),
        "range": {
            "start": {"line": -1, "character": 0},
            "end": {"line": 0, "character": 1},
        },
    }
    sessions = _FailureSessions(failure_mode, malformed_payload)
    provider = soleaux.lsp.providers.ConfiguredProvider(
        provider_name="failing-lsp",
        provider_version="1",
        argv=(sys.executable,),
        extensions=("py",),
        root=tmp_path,
        config_digest="failing-config",
    )
    resolver = soleaux.lsp.resolvers.SemanticResolver(
        soleaux.lsp.providers.ProviderRegistry((provider,)), sessions=sessions
    )

    try:
        resolution = await resolver.execute_capability(
            soleaux.lsp.contracts.LspCapability.DEFINITION,
            bundle,
            path="main.py",
            line=1,
            column=1,
        )
    finally:
        await resolver.shutdown()

    assert resolution.status is expected_status
    assert resolution.generation is not None
    assert resolution.generation.provider_name == "failing-lsp"
    assert resolution.omitted_reasons
    assert "provider 'failing-lsp' failure" in resolution.omitted_reasons[0]
    assert expected_reason in resolution.omitted_reasons[0]
    assert sessions.restarted is expected_restart
