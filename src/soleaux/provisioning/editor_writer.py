"""Disable one VS Code settings.json key through its parsed syntax tree."""

from __future__ import annotations

import json
from pathlib import Path

from soleaux.provisioning.backup import WorkspaceIo
from soleaux.structural.ast_runtime import replace_json_value


def _disable_render(value: str) -> str:
    if value == "":
        return '""'
    if value in {"None", "null"}:
        return "null"
    if value in {"true", "false"}:
        return value
    return json.dumps(value)


def render_disabled_editor_setting(
    current: bytes | None,
    key: str,
    disable_value: str,
) -> bytes | None:
    """Render one parsed settings change without performing filesystem I/O."""
    if current is None:
        return None
    text = current.decode("utf-8")
    updated = replace_json_value(text, key, _disable_render(disable_value))
    if updated is None:
        return None
    return updated.encode("utf-8")


def disable_editor_setting(target_path: Path, key: str, disable_value: str) -> bool:
    """Set one editor key through descriptor-confined atomic replacement."""
    workspace_io, target = WorkspaceIo.for_target(target_path)
    with workspace_io:
        snapshot = workspace_io.read_optional(target)
        rendered = render_disabled_editor_setting(
            snapshot.data if snapshot is not None else None,
            key,
            disable_value,
        )
        if rendered is None:
            return False
        workspace_io.write_bytes_atomic(target, rendered)
    return True
