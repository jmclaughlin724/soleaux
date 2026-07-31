# pyright: reportUnknownMemberType=none, reportUnknownVariableType=none, reportUnknownArgumentType=none, reportUnknownLambdaType=none
"""Register soleaux in workspace MCP launch configs.

Writes a portable ``uvx soleaux`` entry to:
- ``.mcp.json`` (Claude Code / OpenCode / compatible clients) through FastMCP's
  canonical config models
- ``.codex/config.toml`` (Codex; comments preserved via tomlkit)
- ``opencode.json`` (OpenCode)

Registrations declare only how to launch the server. Approval-mode keys are
owned by ``policy_writer``; this writer never creates them, but a registration
rewrite preserves any that already exist. Idempotent: refuses to clobber a
healthy existing soleaux registration unless ``force=True``.
"""

from __future__ import annotations

import json
import pathlib
import tomllib
import typing

import fastmcp.mcp_config
import tomlkit

from soleaux.provisioning.backup import WorkspaceIo

NAME = "soleaux"
COMMAND = "uvx"
ARGS: list[str] = ["soleaux"]


def _is_healthy(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return entry.get("command") == COMMAND and list(entry.get("args") or [])[:1] == ARGS


def _is_healthy_opencode(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return (
        entry.get("type") == "local"
        and entry.get("command") == [COMMAND, *ARGS]
        and entry.get("enabled") is True
    )


def _read_json(current: bytes | None) -> dict[str, typing.Any]:
    if current is None:
        return {}
    try:
        loaded: object = json.loads(current)
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _render_json(data: dict[str, typing.Any]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render_mcp_json(current: bytes | None, *, force: bool = False) -> bytes | None:
    existing = _read_json(current)
    servers = existing.get("mcpServers")
    if isinstance(servers, dict) and _is_healthy(servers.get(NAME)) and not force:
        return None
    entry = fastmcp.mcp_config.StdioMCPServer(command=COMMAND, args=list(ARGS))
    if not isinstance(servers, dict):
        servers = {}
        existing["mcpServers"] = servers
    servers[NAME] = entry.model_dump(mode="json", exclude_none=True)
    fastmcp.mcp_config.CanonicalMCPConfig.model_validate(
        {"mcpServers": servers},
    )
    return _render_json(existing)


def render_opencode_json(current: bytes | None, *, force: bool = False) -> bytes | None:
    data = _read_json(current)
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        data["mcp"] = mcp

    legacy_servers = mcp.get("servers")
    legacy_soleaux = isinstance(legacy_servers, dict) and NAME in legacy_servers
    if _is_healthy_opencode(mcp.get(NAME)) and not legacy_soleaux and not force:
        return None

    if isinstance(legacy_servers, dict) and NAME in legacy_servers:
        del legacy_servers[NAME]
        if not legacy_servers:
            del mcp["servers"]

    mcp[NAME] = {
        "type": "local",
        "command": [COMMAND, *ARGS],
        "enabled": True,
    }
    return _render_json(data)


def render_codex_config(current: bytes | None, *, force: bool = False) -> bytes | None:
    text = current.decode("utf-8") if current is not None else ""
    doc = tomlkit.parse(text) if text else tomlkit.document()
    servers = doc.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = tomlkit.table(is_super_table=True)
        doc["mcp_servers"] = servers  # type: ignore[index]

    existing = servers.get(NAME)  # type: ignore[union-attr]
    if isinstance(existing, dict) and existing.get("command") == COMMAND and not force:
        existing_args = existing.get("args")
        if isinstance(existing_args, list) and list(existing_args)[:1] == ARGS:
            return None

    table = tomlkit.table()
    table["command"] = COMMAND
    table["args"] = list(ARGS)
    table["enabled"] = True
    # Policy-owned keys belong to provisioning.policy_writer; a registration
    # rewrite carries them forward instead of silently dropping policy.
    if isinstance(existing, dict):
        for policy_key in ("default_tools_approval_mode", "tools", "disabled_tools"):
            if policy_key in existing:
                table[policy_key] = existing[policy_key]
    servers[NAME] = table  # type: ignore[index]

    rendered = tomlkit.dumps(doc).encode("utf-8")
    tomllib.loads(rendered.decode("utf-8"))
    return rendered


def render_registration(
    target_name: str,
    current: bytes | None,
    *,
    force: bool = False,
) -> bytes | None:
    """Render a host registration without reopening its admitted path."""
    if target_name == ".mcp.json":
        return render_mcp_json(current, force=force)
    if target_name == "config.toml":
        return render_codex_config(current, force=force)
    if target_name == "opencode.json":
        return render_opencode_json(current, force=force)
    return None


def _register_path(
    target_path: pathlib.Path,
    *,
    force: bool,
) -> bool:
    workspace_io, target = WorkspaceIo.for_target(target_path)
    with workspace_io:
        snapshot = workspace_io.read_optional(target)
        rendered = render_registration(
            target_path.name,
            snapshot.data if snapshot is not None else None,
            force=force,
        )
        if rendered is None:
            return False
        workspace_io.write_bytes_atomic(target, rendered)
        return True


def register_in_mcp_json(path: pathlib.Path, *, force: bool = False) -> bool:
    return _register_path(path, force=force)


def register_in_opencode_json(path: pathlib.Path, *, force: bool = False) -> bool:
    return _register_path(path, force=force)


def register_in_codex_config(path: pathlib.Path, *, force: bool = False) -> bool:
    return _register_path(path, force=force)


def register(target_path: pathlib.Path, *, force: bool = False) -> bool:
    """Dispatch on host file basename."""
    if target_path.name not in {".mcp.json", "config.toml", "opencode.json"}:
        return False
    return _register_path(target_path, force=force)
