"""D031: real project providers resolve content and leave no processes."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import _assertions
import pytest

from soleaux.analysis.budgets import LSP_FIRST_START_MS, LSP_WARM_P95_MS
from soleaux.contracts.coverage import FrameStatus
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.lsp.contracts import LspCapability, NavigationRequest, SemanticOperation
from soleaux.lsp.providers import ProviderRegistry
from soleaux.lsp.resolvers import SemanticResolver
from soleaux.structural.snapshot import RepositorySnapshotter

FIXTURES = Path(__file__).parent / "fixtures" / "repositories"
MILLISECONDS_PER_SECOND = 1000.0
FIRST_START_DEADLINE_SECONDS = LSP_FIRST_START_MS / MILLISECONDS_PER_SECOND
WARM_DEADLINE_SECONDS = LSP_WARM_P95_MS / MILLISECONDS_PER_SECOND
DIAGNOSTIC_COLD_DEADLINE_SECONDS = LSP_FIRST_START_MS / MILLISECONDS_PER_SECOND
DIAGNOSTIC_WARM_DEADLINE_SECONDS = LSP_WARM_P95_MS / MILLISECONDS_PER_SECOND


@dataclass(frozen=True)
class _ProviderCase:
    directory: str
    source_files: tuple[str, ...]
    request_path: str
    line: int
    column: int
    definition_path: str


@pytest.mark.parametrize(
    "case",
    [
        _ProviderCase(
            directory="lsp-typescript",
            source_files=("dep.ts", "main.ts", "tsconfig.json"),
            request_path="main.ts",
            line=3,
            column=2,
            definition_path="dep.ts",
        ),
        _ProviderCase(
            directory="lsp-python",
            source_files=("dep.py", "main.py", "pyproject.toml"),
            request_path="main.py",
            line=3,
            column=2,
            definition_path="dep.py",
        ),
        _ProviderCase(
            directory="lsp-go",
            source_files=("dep.go", "go.mod", "main.go"),
            request_path="main.go",
            line=4,
            column=12,
            definition_path="dep.go",
        ),
        _ProviderCase(
            directory="lsp-next",
            source_files=(
                "app/page.ts",
                "app/title.ts",
                "package.json",
                "tsconfig.json",
            ),
            request_path="app/page.ts",
            line=6,
            column=10,
            definition_path="title.ts",
        ),
    ],
    ids=("typescript", "python", "go", "next"),
)
async def test_real_provider_definition_first_start_and_warm_budget(
    case: _ProviderCase,
) -> None:
    root = FIXTURES / case.directory
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="real-provider-test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(scope=case.source_files)
    registry = ProviderRegistry.default(root)
    provider = registry.configured_for_path(case.request_path)
    assert provider is not None
    assert provider.executable_available()
    spec = provider.to_spec(Path(case.request_path).suffix)
    assert Path(spec.argv[0]).is_absolute()
    assert Path(spec.argv[0]).resolve(strict=True).is_file()
    resolver = SemanticResolver(registry)
    request = NavigationRequest(
        operation=SemanticOperation.DEFINITION,
        path=case.request_path,
        line=case.line,
        column=case.column,
    )

    try:
        first_started = time.perf_counter()
        first = await asyncio.wait_for(
            resolver.navigate(request, bundle),
            timeout=FIRST_START_DEADLINE_SECONDS,
        )
        first_elapsed = time.perf_counter() - first_started

        warm_started = time.perf_counter()
        warm = await asyncio.wait_for(
            resolver.navigate(request, bundle),
            timeout=WARM_DEADLINE_SECONDS,
        )
        warm_elapsed = time.perf_counter() - warm_started

        assert first.status is FrameStatus.COMPLETE
        assert first.locations
        assert Path(first.locations[0].uri.removeprefix("file://")).name == case.definition_path
        assert first.provider_identity is not None
        assert first.provider_identity.configured_name == provider.provider_name
        assert first.provider_identity.configured_version == provider.provider_version
        if first.provider_identity.server_info is not None:
            assert first.provider_identity.server_info.version
        assert warm.locations == first.locations
        assert resolver.active_session_count == 1
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0
        assert first_elapsed < FIRST_START_DEADLINE_SECONDS
        assert warm_elapsed < WARM_DEADLINE_SECONDS
    finally:
        await resolver.shutdown()
        assert resolver.active_session_count == 0
        assert resolver.active_provider_pids == ()
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0


async def test_real_yaml_diagnostics_and_cleanup() -> None:
    root = FIXTURES / "lsp-yaml"
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="real-yaml-test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(scope=("invalid.yml",))
    registry = ProviderRegistry.default(root)
    provider = registry.configured_for_path("invalid.yml")
    assert provider is not None
    assert provider.provider_name == "yaml-language-server"
    assert provider.executable_available()
    resolver = SemanticResolver(registry)

    try:
        result = await asyncio.wait_for(
            resolver.execute_capability(
                LspCapability.DIAGNOSTICS,
                bundle,
                path="invalid.yml",
            ),
            timeout=DIAGNOSTIC_COLD_DEADLINE_SECONDS,
        )

        assert result.status is FrameStatus.COMPLETE
        assert isinstance(result.payload, list)
        assert result.payload
        assert result.provider_identity is not None
        assert result.provider_identity.configured_name == "yaml-language-server"
        assert result.provider_identity.configured_version == provider.provider_version
        if result.provider_identity.server_info is not None:
            assert result.provider_identity.server_info.version
        assert resolver.active_session_count == 1
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0
    finally:
        await resolver.shutdown()
        assert resolver.active_session_count == 0
        assert resolver.active_provider_pids == ()
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0


async def test_real_pyright_diagnostics_first_start_and_warm_budget(tmp_path: Path) -> None:
    fixture_root = FIXTURES / "lsp-python-diagnostics"
    root = tmp_path
    (root / "diagnostic_bad.py").write_text(
        (fixture_root / "diagnostic_bad.py.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_bytes((fixture_root / "pyproject.toml").read_bytes())
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="real-pyright-diagnostic-test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(
        scope=("diagnostic_bad.py", "pyproject.toml")
    )
    resolver = SemanticResolver(ProviderRegistry.default(root))

    try:
        first_started = time.perf_counter()
        first = await asyncio.wait_for(
            resolver.execute_capability(
                LspCapability.DIAGNOSTICS,
                bundle,
                path="diagnostic_bad.py",
            ),
            timeout=DIAGNOSTIC_COLD_DEADLINE_SECONDS,
        )
        first_elapsed = time.perf_counter() - first_started

        warm_started = time.perf_counter()
        warm = await asyncio.wait_for(
            resolver.execute_capability(
                LspCapability.DIAGNOSTICS,
                bundle,
                path="diagnostic_bad.py",
            ),
            timeout=DIAGNOSTIC_WARM_DEADLINE_SECONDS,
        )
        warm_elapsed = time.perf_counter() - warm_started

        assert first.status is FrameStatus.COMPLETE
        assert isinstance(first.payload, list)
        assert first.payload
        assert first.provider_identity is not None
        assert first.provider_identity.configured_version == "1.1.411"
        if first.provider_identity.server_info is not None:
            assert first.provider_identity.server_info.version
        diagnostic_items = [
            _assertions.object_mapping(item) for item in _assertions.object_list(first.payload)
        ]
        assert any(item.get("severity") == 1 for item in diagnostic_items)
        assert warm.payload == first.payload
        assert resolver.active_session_count == 1
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0
        assert first_elapsed < DIAGNOSTIC_COLD_DEADLINE_SECONDS
        assert warm_elapsed < DIAGNOSTIC_WARM_DEADLINE_SECONDS
    finally:
        await resolver.shutdown()
        assert resolver.active_session_count == 0
        assert resolver.active_provider_pids == ()
        assert resolver.pending_request_count == 0
        assert resolver.in_flight_task_count == 0
