"""D006/D031: trusted CCLSP migration manifest and capability parity."""

from __future__ import annotations

import json
import pathlib

import _assertions
import _host_root

import soleaux.lsp.contracts
import soleaux.lsp.providers


def _repository_root() -> pathlib.Path:
    return _host_root.require_host_root()


def test_archived_cclsp_manifest_records_all_migrated_providers() -> None:
    root = _repository_root()

    registry = soleaux.lsp.providers.ProviderRegistry.from_cclsp(
        manifest_path=root / "standards/registry-history/cclsp.json",
        workspace_root=root,
    )

    assert len(registry.providers) == 11
    assert {provider.provider_name for provider in registry.providers} >= {
        "gopls",
        "pylsp",
        "typescript-language-server",
    }
    assert registry.configured_for_path("src/example.ts") is not None
    assert registry.configured_for_path("src/example.py") is not None
    assert registry.configured_for_path("src/example.go") is not None
    assert registry.configured_for_path("src/example.unknown") is None


def test_builtin_registry_is_independent_of_the_migration_manifest() -> None:
    registry = soleaux.lsp.providers.ProviderRegistry.default(_repository_root())

    provider_names = {provider.provider_name for provider in registry.providers}
    assert "typescript-language-server" in provider_names
    assert "pyright-langserver" in provider_names
    assert "gopls" in provider_names
    assert "rust-analyzer" in provider_names
    assert "yaml-language-server" in provider_names
    assert "bash-language-server" in provider_names
    assert "deno" not in provider_names

    typescript = registry.configured_for_path("src/example.ts")
    assert typescript is not None
    assert typescript.initialization_options["tsserver"]["useSyntaxServer"] == "never"


def test_capability_enum_is_sourced_by_the_17_row_migration_ledger() -> None:
    evidence_path = (
        _repository_root()
        / "plans"
        / "2026-07-22-soleaux-final"
        / "evidence"
        / "cclsp-capability-map.json"
    )
    mapping = _assertions.object_mapping(json.loads(evidence_path.read_text(encoding="utf-8")))
    typed_rows = _assertions.object_list(mapping.get("capabilities"))

    legacy_tools: list[str] = []
    for raw_row in typed_rows:
        row = _assertions.object_mapping(raw_row)
        legacy_tool = row.get("legacy_tool")
        assert isinstance(legacy_tool, str)
        legacy_tools.append(legacy_tool)

    assert len(typed_rows) == len(soleaux.lsp.contracts.LspCapability) == 17
    assert len(set(legacy_tools)) == 17
    assert set(soleaux.lsp.contracts.CAPABILITY_LSP_METHOD) == set(
        soleaux.lsp.contracts.LspCapability
    )
