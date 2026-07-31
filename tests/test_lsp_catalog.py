"""Tests for LSP provider catalog, merge semantics, and install gating."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from _assertions import raises_with_message

from soleaux.contracts.config import HealthConfig, ProviderConfig, ResolvedConfig
from soleaux.lsp.providers import BUILTIN_PROVIDERS, ConfiguredProvider, ProviderRegistry
from soleaux.postgresql.runtime import (
    GO_TOOLCHAIN_ENVIRONMENT_NAMES,
    POSTGRESQL_ENVIRONMENT_NAMES,
)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a minimal repo root with .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _configured_postgres(
    repo_root: Path,
    *,
    argv: tuple[str, ...] = ("postgres-language-server", "lsp-proxy"),
) -> ConfiguredProvider:
    return ConfiguredProvider(
        provider_name="postgres-language-server",
        provider_version="0.25.4",
        argv=argv,
        extensions=("sql",),
        root=repo_root,
        config_digest="postgres-test",
    )


def test_builtin_catalog_has_expected_servers() -> None:
    names = {p.name for p in BUILTIN_PROVIDERS}
    assert "typescript-language-server" in names
    assert "pyright" in names
    assert "gopls" in names
    assert "rust-analyzer" in names
    assert "deno" in names
    assert "yaml-language-server" in names
    assert "postgres-language-server" in names
    assert "pylsp" not in names


def test_postgres_provider_uses_pinned_command_and_version() -> None:
    postgres = next(p for p in BUILTIN_PROVIDERS if p.name == "postgres-language-server")
    assert postgres.argv == ("postgres-language-server", "lsp-proxy")
    assert postgres.extensions == ("sql",)
    assert postgres.version == "0.25.4"
    assert postgres.display_name == "PostgreSQL"
    assert postgres.install_hint == "npm install -g @postgres-language-server/cli"
    assert postgres.install_command == ("npm", "install", "-g", "@postgres-language-server/cli")
    assert postgres.environment_names == POSTGRESQL_ENVIRONMENT_NAMES


def test_typescript_provisioning_pins_a_tsserver_capable_compiler() -> None:
    typescript = next(p for p in BUILTIN_PROVIDERS if p.name == "typescript-language-server")
    assert "typescript@6" in typescript.install_hint
    assert typescript.install_command == (
        "npm",
        "install",
        "-g",
        "typescript-language-server",
        "typescript@6",
    )


def test_go_provider_declares_only_toolchain_runtime_inputs() -> None:
    go = next(provider for provider in BUILTIN_PROVIDERS if provider.name == "gopls")
    assert go.environment_names == GO_TOOLCHAIN_ENVIRONMENT_NAMES


def test_postgres_provider_detects_legacy_config_and_package_names() -> None:
    """0.25.4 still reads `postgrestools.jsonc`, and the CLI is dual-published."""
    postgres = next(p for p in BUILTIN_PROVIDERS if p.name == "postgres-language-server")
    assert "postgres-language-server.jsonc" in postgres.detect_files
    assert "postgrestools.jsonc" in postgres.detect_files
    assert "supabase/config.toml" in postgres.detect_files
    assert "@postgres-language-server/cli" in postgres.detect_deps
    assert "@postgrestools/postgrestools" in postgres.detect_deps


def test_registry_rejects_multiple_providers_for_sql(repo_root: Path) -> None:
    postgres = _configured_postgres(repo_root)
    duplicate = ConfiguredProvider(
        provider_name="custom-sql-lsp",
        provider_version="unprobed",
        argv=("custom-sql-lsp",),
        extensions=("sql",),
        root=repo_root,
        config_digest="duplicate-sql-test",
    )

    with raises_with_message(ValueError, "multiple providers configured for extension 'sql'"):
        ProviderRegistry((postgres, duplicate))


def test_postgres_provider_command_is_portable(repo_root: Path) -> None:
    """The published package must not depend on a monorepo-specific launcher."""
    postgres = next(p for p in BUILTIN_PROVIDERS if p.name == "postgres-language-server")
    assert "pnpm" not in postgres.argv
    assert "--workspace-root" not in postgres.argv
    registry = ProviderRegistry.default(repo_root)
    sql = registry.configured_for_path("schema.sql")
    assert sql is not None
    assert sql.provider_name == "postgres-language-server"


def test_postgres_provider_carries_values_but_digests_and_serializes_names_only(
    repo_root: Path,
) -> None:
    first_url = "postgresql://reader:first-secret@127.0.0.1/local"
    second_url = "postgresql://reader:second-secret@127.0.0.1/local"
    with patch.dict(
        "os.environ",
        {"DATABASE_URL": first_url, "PGSSLMODE": "require"},
        clear=True,
    ):
        first = ProviderRegistry.default(repo_root).configured_for_path("schema.sql")
    with patch.dict(
        "os.environ",
        {"DATABASE_URL": second_url, "PGSSLMODE": "require"},
        clear=True,
    ):
        second = ProviderRegistry.default(repo_root).configured_for_path("schema.sql")

    assert first is not None
    assert second is not None
    assert first.environment_names == POSTGRESQL_ENVIRONMENT_NAMES
    assert first.process_environment() == {"DATABASE_URL": first_url}
    assert second.process_environment() == {"DATABASE_URL": second_url}
    assert first.config_digest == second.config_digest
    assert "environment" not in first.model_dump(mode="json")
    assert first_url not in repr(first)
    spec = first.to_spec("sql")
    assert spec.process_environment() == {"DATABASE_URL": first_url}
    assert "environment" not in spec.model_dump(mode="json")
    assert first_url not in repr(spec)


def test_frame_passes_health_retention_to_postgres_runtime(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig(
        health=HealthConfig(
            logs_retention_days=23,
            temp_retention_hours=41,
        )
    )
    postgres = build_provider_registry(repo_root, config).configured_for_path("schema.sql")

    assert postgres is not None
    assert postgres.logs_retention_days == 23
    assert postgres.temp_retention_hours == 41


def test_postgres_direct_provider_resolves_from_path(repo_root: Path) -> None:
    provider = _configured_postgres(repo_root)
    executable = "/installed/bin/postgres-language-server"

    with patch("soleaux.lsp.providers.shutil.which", return_value=executable):
        assert provider.executable_available()
        spec = provider.to_spec("sql")

    assert spec.argv == (executable, "lsp-proxy")


def test_postgres_direct_provider_resolves_from_workspace_node_bin(repo_root: Path) -> None:
    provider = _configured_postgres(repo_root)
    local_bin = repo_root / "node_modules" / ".bin"
    executable = str(local_bin / "postgres-language-server")

    def fake_which(command: str, *, path: str | None = None) -> str | None:
        if command == "postgres-language-server" and path == str(local_bin):
            return executable
        return None

    with patch("soleaux.lsp.providers.shutil.which", side_effect=fake_which):
        assert provider.executable_available()
        spec = provider.to_spec(".sql")

    assert spec.argv == (executable, "lsp-proxy")


def test_provider_prefers_nearest_ancestor_node_bin_over_path(repo_root: Path) -> None:
    workspace = repo_root / "apps" / "web"
    workspace.mkdir(parents=True)
    provider = _configured_postgres(workspace)
    repository_bin = repo_root / "node_modules" / ".bin"
    repository_executable = str(repository_bin / "postgres-language-server")

    def fake_which(command: str, *, path: str | None = None) -> str | None:
        if command == "postgres-language-server" and path == str(repository_bin):
            return repository_executable
        if command == "postgres-language-server" and path is None:
            return "/global/bin/postgres-language-server"
        return None

    with patch("soleaux.lsp.providers.shutil.which", side_effect=fake_which):
        spec = provider.to_spec("sql")

    assert spec.argv == (repository_executable, "lsp-proxy")


def test_provider_uses_ancestor_python_environment_before_path(repo_root: Path) -> None:
    workspace = repo_root / "packages" / "python"
    workspace.mkdir(parents=True)
    provider = ConfiguredProvider(
        provider_name="pyright-langserver",
        provider_version="unprobed",
        argv=("pyright-langserver", "--stdio"),
        extensions=("py",),
        root=workspace,
        config_digest="python-environment-test",
    )
    environment_bin = repo_root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    environment_executable = str(environment_bin / "pyright-langserver")

    def fake_which(command: str, *, path: str | None = None) -> str | None:
        if command == "pyright-langserver" and path == str(environment_bin):
            return environment_executable
        if command == "pyright-langserver" and path is None:
            return "/global/bin/pyright-langserver"
        return None

    with patch("soleaux.lsp.providers.shutil.which", side_effect=fake_which):
        spec = provider.to_spec("py")

    assert spec.argv == (environment_executable, "--stdio")


def test_postgres_managed_provider_resolves_configured_pnpm_install(repo_root: Path) -> None:
    provider = _configured_postgres(
        repo_root,
        argv=("pnpm", "exec", "postgres-language-server", "lsp-proxy"),
    )
    local_bin = repo_root / "node_modules" / ".bin"
    pnpm = "/installed/bin/pnpm"

    def fake_which(command: str, *, path: str | None = None) -> str | None:
        if command == "pnpm" and path is None:
            return pnpm
        if command == "postgres-language-server" and path == str(local_bin):
            return str(local_bin / command)
        return None

    with patch("soleaux.lsp.providers.shutil.which", side_effect=fake_which):
        assert provider.executable_available()
        spec = provider.to_spec("sql")

    assert spec.argv == (pnpm, "exec", "postgres-language-server", "lsp-proxy")


def test_default_registry_excludes_deno_without_deno_json(repo_root: Path) -> None:
    registry = ProviderRegistry.default(repo_root)
    names = {p.provider_name for p in registry.providers}
    assert "deno" not in names
    assert "typescript-language-server" in names
    ts = registry.configured_for_path("example.ts")
    assert ts is not None
    assert "ts" in ts.extensions


def test_default_registry_includes_deno_with_deno_json(repo_root: Path) -> None:
    (repo_root / "deno.json").write_text("{}", encoding="utf-8")
    registry = ProviderRegistry.default(repo_root)
    names = {p.provider_name for p in registry.providers}
    assert "deno" in names
    ts = registry.configured_for_path("example.ts")
    assert ts is not None
    assert ts.provider_name == "deno"
    cjs = registry.configured_for_path("example.cjs")
    assert cjs is not None
    assert cjs.provider_name == "typescript-language-server"


def test_merge_preserves_defaults_when_config_empty(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig.default()
    registry = build_provider_registry(repo_root, config)
    names = {p.provider_name for p in registry.providers}
    assert "typescript-language-server" in names
    assert "pyright-langserver" in names
    assert "gopls" in names


def test_merge_disables_builtin_via_config(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig(
        providers={"gopls": ProviderConfig(enabled=False)},
    )
    registry = build_provider_registry(repo_root, config)
    names = {p.provider_name for p in registry.providers}
    assert "gopls" not in names
    assert "typescript-language-server" in names


def test_merge_appends_custom_provider(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig(
        providers={
            "custom-lsp": ProviderConfig(
                command=["custom-language-server", "--stdio"],
                extensions=("xyz",),
            ),
        },
    )
    registry = build_provider_registry(repo_root, config)
    names = {p.provider_name for p in registry.providers}
    assert "custom-language-server" in names
    assert "typescript-language-server" in names
    custom = registry.configured_for_path("example.xyz")
    assert custom is not None
    assert custom.provider_name == "custom-language-server"


def test_merge_replaces_postgres_builtin_with_custom_override(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig(
        providers={
            "postgres-language-server": ProviderConfig(
                command=["custom-postgres-lsp", "--stdio"],
                extensions=("sql",),
            ),
        },
    )
    registry = build_provider_registry(repo_root, config)

    sql = registry.configured_for_path("schema.sql")
    assert sql is not None
    assert sql.provider_name == "custom-postgres-lsp"
    assert sql.argv == ("custom-postgres-lsp", "--stdio")
    assert sql.environment_names == POSTGRESQL_ENVIRONMENT_NAMES


def test_merge_disables_postgres_builtin(repo_root: Path) -> None:
    from soleaux.analysis.frame import build_provider_registry

    config = ResolvedConfig(
        providers={"postgres-language-server": ProviderConfig(enabled=False)},
    )
    registry = build_provider_registry(repo_root, config)

    assert registry.configured_for_path("schema.sql") is None
