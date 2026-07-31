"""Tests for MCP server suggestion scanning."""

from __future__ import annotations

import json
import pathlib

import pytest

import soleaux.suggestions


@pytest.fixture
def repo_with_signals(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temp repo with known detection signals."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "15.0.0", "@playwright/test": "1.40.0"},
                "devDependencies": {"eslint": "9.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "components.json").write_text("{}", encoding="utf-8")
    (tmp_path / "supabase").mkdir()
    (tmp_path / "supabase" / "config.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_catalog_has_entries() -> None:
    assert len(soleaux.suggestions.CATALOG) >= 10
    for entry in soleaux.suggestions.CATALOG:
        assert entry.name
        assert entry.rationale
        assert entry.command or entry.url


def test_scan_detects_file_signals(repo_with_signals: pathlib.Path) -> None:
    results = soleaux.suggestions.scan_for_suggestions(repo_with_signals)
    names = {r.name for r in results}
    assert "shadcn" in names  # components.json
    assert "supabase" in names  # supabase/config.toml


def test_scan_detects_dependency_signals(repo_with_signals: pathlib.Path) -> None:
    results = soleaux.suggestions.scan_for_suggestions(repo_with_signals)
    names = {r.name for r in results}
    assert "next-devtools" in names  # next dep
    assert "playwright" in names  # @playwright/test dep + playwright.config.ts missing
    assert "eslint" in names  # eslint dep


def test_scan_returns_empty_for_bare_repo(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()
    results = soleaux.suggestions.scan_for_suggestions(tmp_path)
    assert results == []


def test_scan_reads_pyproject_deps(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastmcp>=3.0"]\n',
        encoding="utf-8",
    )
    results = soleaux.suggestions.scan_for_suggestions(tmp_path)
    # fastmcp is not in the catalog, so nothing should match
    # unless there's a catalog entry for it — verify it doesn't crash
    assert isinstance(results, list)


def test_suggestion_to_dict_excludes_empty_fields() -> None:
    s = soleaux.suggestions.McpSuggestion(name="test", rationale="test rationale")
    d = s.to_dict()
    assert d == {"name": "test", "rationale": "test rationale"}


def test_suggestion_to_dict_includes_command() -> None:
    s = soleaux.suggestions.McpSuggestion(
        name="test",
        rationale="test",
        command=["npx", "test-server"],
    )
    d = s.to_dict()
    assert d["command"] == ["npx", "test-server"]


def test_suggestion_to_dict_includes_url_and_auth() -> None:
    s = soleaux.suggestions.McpSuggestion(
        name="test",
        rationale="test",
        url="https://mcp.example.com/mcp",
        auth_token_env_hint="EXAMPLE_TOKEN",
    )
    d = s.to_dict()
    assert d["url"] == "https://mcp.example.com/mcp"
    assert d["auth_token_env_hint"] == "EXAMPLE_TOKEN"
