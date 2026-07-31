# pyright: reportUnknownMemberType=none, reportUnknownVariableType=none, reportUnknownArgumentType=none, reportUnknownLambdaType=none
"""Apply the rendered host policy bundle to host configuration files.

``soleaux.toml`` owns policy effects; ``policy_render.render_all`` renders
them, and this module is the owned application path that merges the bundle
into the host surfaces the renderers target:

- ``.codex/config.toml`` — ``[mcp_servers.soleaux]`` approval keys
- ``opencode.json`` — top-level ``permission`` rules
- ``.claude/settings.json`` — ``permissions.deny`` entries

Policy-owned keys are replaced wholesale; every unrelated key in the host
file is preserved. Writes go through the provisioning descriptor I/O, so
existing files are backed up and created files are recorded for revert.
"""

from __future__ import annotations

import collections.abc
import json
import pathlib
import tomllib
import typing
from dataclasses import dataclass

import tomlkit

import soleaux.contracts.config
import soleaux.policy_render
import soleaux.provisioning.contracts
from soleaux.provisioning import backup

_CODEX_TARGET = ".codex/config.toml"
_OPENCODE_TARGET = "opencode.json"
_CLAUDE_TARGET = ".claude/settings.json"
_OPENCODE_MANAGED_PREFIX = "soleaux_"
_CLAUDE_MANAGED_PREFIX = "mcp__soleaux__"

POLICY_TARGETS: tuple[str, ...] = (_CODEX_TARGET, _OPENCODE_TARGET, _CLAUDE_TARGET)


@dataclass(frozen=True)
class PolicyApplyResult:
    """The outcome of one policy application pass."""

    written: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    backups: tuple[soleaux.provisioning.contracts.BackupRecord, ...] = ()
    created: tuple[str, ...] = ()


def _render_json(data: dict[str, typing.Any]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def render_codex_policy(
    current: bytes | None,
    bundle: soleaux.policy_render.HostPolicyBundle,
) -> bytes | None:
    """Merge the Codex approval fragment into ``[mcp_servers.soleaux]``.

    Registration owns creating the server entry; policy only merges into an
    existing one, so a workspace without a soleaux registration is skipped.
    """
    if current is None:
        return None
    doc = tomlkit.parse(current.decode("utf-8"))
    servers = doc.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    server = servers.get("soleaux")
    if not isinstance(server, dict):
        return None

    codex = bundle.codex
    changed = False
    if server.get("default_tools_approval_mode") != codex["default_tools_approval_mode"]:
        server["default_tools_approval_mode"] = codex["default_tools_approval_mode"]
        changed = True

    desired_tools = {name: entry["approval_mode"] for name, entry in codex["tools"].items()}
    existing_tools: dict[str, str] = {}
    raw_tools = server.get("tools")
    if isinstance(raw_tools, dict):
        for tool_name, tool_entry in raw_tools.items():
            if isinstance(tool_entry, dict) and isinstance(tool_entry.get("approval_mode"), str):
                existing_tools[str(tool_name)] = tool_entry["approval_mode"]
    if existing_tools != desired_tools:
        tools_table = tomlkit.table()
        for tool_name in sorted(desired_tools):
            entry = tomlkit.table()
            entry["approval_mode"] = desired_tools[tool_name]
            tools_table[tool_name] = entry
        server["tools"] = tools_table
        changed = True

    disabled = codex.get("disabled_tools")
    if disabled is not None:
        if list(server.get("disabled_tools") or []) != list(disabled):
            server["disabled_tools"] = list(disabled)
            changed = True
    elif server.get("disabled_tools") is not None:
        del server["disabled_tools"]
        changed = True

    if not changed:
        return None
    rendered = tomlkit.dumps(doc).encode("utf-8")
    tomllib.loads(rendered.decode("utf-8"))
    return rendered


def render_opencode_policy(
    current: bytes | None,
    bundle: soleaux.policy_render.HostPolicyBundle,
) -> bytes | None:
    """Replace the managed ``soleaux_*`` permission rules in ``opencode.json``.

    Sorted key order is load-bearing: OpenCode evaluates last-match-wins, so
    the rendered general-to-specific order must survive serialization.
    """
    if current is None:
        return None
    try:
        loaded: object = json.loads(current)
    except json.JSONDecodeError, UnicodeDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    mcp = loaded.get("mcp")
    if not isinstance(mcp, dict) or "soleaux" not in mcp:
        return None

    permission = loaded.get("permission")
    existing: dict[str, str] = dict(permission) if isinstance(permission, dict) else {}
    merged = {
        key: value
        for key, value in existing.items()
        if not key.startswith(_OPENCODE_MANAGED_PREFIX)
    }
    merged.update(bundle.opencode)
    if merged == existing and "permission" in loaded:
        return None
    loaded["permission"] = merged
    return _render_json(loaded)


def render_claude_policy(
    current: bytes | None,
    bundle: soleaux.policy_render.HostPolicyBundle,
) -> bytes | None:
    """Replace the managed ``mcp__soleaux__*`` entries in ``permissions.deny``."""
    data: dict[str, typing.Any] = {}
    if current is not None:
        try:
            loaded: object = json.loads(current)
        except json.JSONDecodeError, UnicodeDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    raw_deny = permissions.get("deny")
    existing_deny = (
        [entry for entry in raw_deny if isinstance(entry, str)]
        if isinstance(raw_deny, list)
        else []
    )
    kept = [entry for entry in existing_deny if not entry.startswith(_CLAUDE_MANAGED_PREFIX)]
    merged_deny = [*kept, *bundle.claude_deny]
    if current is None and not merged_deny:
        return None
    if merged_deny == existing_deny:
        return None
    permissions["deny"] = merged_deny
    data["permissions"] = permissions
    return _render_json(data)


_RENDERERS: dict[
    str,
    collections.abc.Callable[[bytes | None, soleaux.policy_render.HostPolicyBundle], bytes | None],
] = {
    _CODEX_TARGET: render_codex_policy,
    _OPENCODE_TARGET: render_opencode_policy,
    _CLAUDE_TARGET: render_claude_policy,
}


def apply_host_policy(
    workspace_root: pathlib.Path,
    config: soleaux.contracts.config.ResolvedConfig,
) -> PolicyApplyResult:
    """Render and merge the canonical policy into every registered host file.

    Renders are computed before any write; existing targets are backed up and
    newly created targets are recorded so ``adopt --revert`` restores the
    pre-application state.
    """
    bundle = soleaux.policy_render.render_all(config)
    written: list[str] = []
    skipped: list[str] = []
    created: list[backup.AdmittedPath] = []
    with backup.WorkspaceIo(workspace_root) as workspace_io:
        prepared: list[tuple[backup.AdmittedPath, bytes, bool]] = []
        for relative in POLICY_TARGETS:
            renderer = _RENDERERS[relative]
            target = workspace_io.admit_target(
                workspace_root / relative,
                role="host policy target path",
            )
            existed = workspace_io.is_file(target)
            snapshot = workspace_io.read_optional(target)
            rendered = renderer(snapshot.data if snapshot is not None else None, bundle)
            if rendered is None:
                skipped.append(relative)
                continue
            prepared.append((target, rendered, existed))

        backups = backup._backup_files(
            workspace_io,
            [target for target, _rendered, existed in prepared if existed],
        )
        for target, rendered, existed in prepared:
            workspace_io.write_bytes_atomic(target, rendered)
            written.append(target.as_posix)
            if not existed:
                created.append(target)
        backup.record_created(workspace_io, created)

    return PolicyApplyResult(
        written=tuple(written),
        skipped=tuple(skipped),
        backups=tuple(backups),
        created=tuple(path.as_posix for path in created),
    )


__all__: tuple[str, ...] = (
    "POLICY_TARGETS",
    "PolicyApplyResult",
    "apply_host_policy",
    "render_claude_policy",
    "render_codex_policy",
    "render_opencode_policy",
)
