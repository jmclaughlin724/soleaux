"""D029: syntax-only requests never invoke a resolver or language server."""

import sys
from pathlib import Path

from soleaux.contracts.coverage import FrameStatus
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.lsp.contracts import NavigationRequest, SemanticOperation
from soleaux.lsp.providers import ConfiguredProvider, ProviderRegistry
from soleaux.lsp.resolvers import SemanticResolver
from soleaux.structural.snapshot import RepositorySnapshotter


async def test_syntax_only_navigation_starts_no_provider(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(scope=("main.py",))
    provider = ConfiguredProvider(
        provider_name="fake-lsp",
        provider_version="1",
        argv=(sys.executable, "-c", "raise SystemExit(99)"),
        extensions=("py",),
        root=tmp_path,
        config_digest="fake-config",
    )
    resolver = SemanticResolver(ProviderRegistry((provider,)))

    result = await resolver.navigate(
        NavigationRequest(
            operation=SemanticOperation.DEFINITION,
            path="main.py",
            line=1,
            column=1,
            semantic_mode=SemanticMode.SYNTAX_ONLY,
        ),
        bundle,
    )

    assert result.status is FrameStatus.UNSUPPORTED
    assert result.generation is None
    assert resolver.active_session_count == 0
    await resolver.shutdown()
