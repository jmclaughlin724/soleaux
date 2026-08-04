"""Standalone repository MCP gateway ownership contract."""

from __future__ import annotations

import pathlib
import tomllib

import soleaux.contracts.config

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_codex_registers_only_the_soleaux_gateway() -> None:
    codex_config = tomllib.loads(
        (REPOSITORY_ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    )

    assert set(codex_config["mcp_servers"]) == {"soleaux"}


def test_repository_routes_documentation_backends_through_soleaux() -> None:
    resolved = soleaux.contracts.config.load_config(REPOSITORY_ROOT)

    assert set(resolved.mcp) == {"context7", "next-devtools", "openai-docs", "zod"}
    assert resolved.mcp["context7"].url == "https://mcp.context7.com/mcp"
    assert resolved.mcp["openai-docs"].url == "https://developers.openai.com/mcp"
    assert resolved.mcp["zod"].url == "https://mcp.inkeep.com/zod/mcp"
    assert resolved.mcp["next-devtools"].command == ["npx", "-y", "next-devtools-mcp@0.4.0"]


def test_repository_exposes_its_shared_skills_through_soleaux() -> None:
    resolved = soleaux.contracts.config.load_config(REPOSITORY_ROOT)

    assert resolved.skills.enabled is True
    assert resolved.skills.roots == (".agents/skills",)
