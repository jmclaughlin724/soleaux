"""Detect competing MCP launch registrations in the workspace."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue

from soleaux.provisioning.contracts import DetectedMcpRegistration

# Commands that launch a competing repository-intelligence MCP (the retired
# servers that previously duplicated soleaux's analysis surface, plus generic
# LSP-launching competitors a fresh external user might have).
_COMPETING = frozenset(
    {
        "ast-grep",
        "cclsp",
        "codeatlas",
        "lsp",
        "pylsp",
        "pyright",
        "pyright-langserver",
        "typescript-language-server",
        "rust-analyzer",
        "gopls",
    }
)


def _is_competing(command: list[str]) -> bool:
    if not command:
        return False
    head = command[0]
    # `soleaux` (or `uvx soleaux`) is the desired end state, not a competitor.
    if head in {"soleaux", "uvx", "uv", "pnpm"} and "soleaux" in command:
        return False
    return head in _COMPETING


def _command(value: JsonValue) -> list[str]:
    """Flatten ``{command: "x", args: [...]}`` to ``["x", ...]``."""
    if not isinstance(value, dict):
        return []
    out: list[str] = []
    head = value.get("command")
    if isinstance(head, str) and head:
        out.append(head)
    args = value.get("args")
    if isinstance(args, list):
        out.extend(str(arg) for arg in args if isinstance(arg, (str, int)))
    return out


def _opencode_command(value: JsonValue) -> list[str]:
    """Read one direct OpenCode ``type = local`` command array."""
    if not isinstance(value, dict) or value.get("type") != "local":
        return []
    raw_command = value.get("command")
    if not isinstance(raw_command, list):
        return []
    command: list[str] = []
    for part in raw_command:
        if not isinstance(part, str) or not part:
            return []
        command.append(part)
    return command


def _dict_of(doc: JsonValue, key: str) -> dict[str, JsonValue]:
    """Narrow ``doc[key]`` to ``dict[str, JsonValue]`` or return empty."""
    if isinstance(doc, dict):
        inner = doc.get(key)
        if isinstance(inner, dict):
            return inner
    return {}


def _servers_mcp_json(doc: JsonValue) -> dict[str, JsonValue]:
    return _dict_of(doc, "mcpServers")


def _servers_codex(doc: JsonValue) -> dict[str, JsonValue]:
    return _dict_of(doc, "mcp_servers")


def _servers_opencode(doc: JsonValue) -> dict[str, JsonValue]:
    return _dict_of(doc, "mcp")


# (host label, file path relative to workspace, parser, lens).
_SOURCES: tuple[
    tuple[
        str,
        str,
        Callable[[str], JsonValue],
        Callable[[JsonValue], dict[str, JsonValue]],
        Callable[[JsonValue], list[str]],
    ],
    ...,
] = (
    (".mcp.json", ".mcp.json", json.loads, _servers_mcp_json, _command),
    (
        ".codex/config.toml",
        ".codex/config.toml",
        tomllib.loads,
        _servers_codex,
        _command,
    ),
    (
        "opencode.json",
        "opencode.json",
        json.loads,
        _servers_opencode,
        _opencode_command,
    ),
)


def _load(path: Path, parser: Callable[[str], JsonValue]) -> JsonValue:
    if not path.is_file():
        return {}
    try:
        return parser(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError:
        return {}


def detect_mcp_registrations(workspace_root: Path) -> tuple[DetectedMcpRegistration, ...]:
    """Read workspace MCP launch configs and report each registration."""
    detections: list[DetectedMcpRegistration] = []
    for host, rel, parser, lens, command_from in _SOURCES:
        servers = lens(_load(workspace_root / rel, parser))
        for name in sorted(servers):
            command = command_from(servers[name])
            if not command:
                continue
            detections.append(
                DetectedMcpRegistration(
                    host=host,
                    name=name,
                    command=tuple(command),
                    competes=_is_competing(command),
                )
            )
    detections.sort(key=lambda d: (d.host, d.name))
    return tuple(detections)
