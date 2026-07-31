# pyright: reportUnknownMemberType=none, reportUnknownVariableType=none, reportUnknownArgumentType=none, reportUnknownLambdaType=none
"""Detect VS Code ``settings.json`` keys that select a language server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import json5

from soleaux.provisioning.contracts import DetectedEditorConfig

# (settings.json key, language, disable value). "None" disables the language
# server outright without uninstalling the extension.
VSCODE_KEYS: tuple[tuple[str, str, str], ...] = (
    ("python.languageServer", "python", "None"),
    ("python.analysis.indexing", "python", "false"),
    ("typescript.tsdk", "typescript", ""),
    ("rust-analyzer.enable", "rust", "false"),
    ("gopls", "go", "false"),
)


def _load_json5(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded: object = json5.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def detect_editor_configs(workspace_root: Path) -> tuple[DetectedEditorConfig, ...]:
    """Read ``.vscode/settings.json`` and report each language-server selection."""
    settings_path = workspace_root / ".vscode" / "settings.json"
    settings = _load_json5(settings_path)
    if settings is None:
        return ()

    rel = settings_path.relative_to(workspace_root).as_posix()
    detections: list[DetectedEditorConfig] = []
    for key, language, disable_value in VSCODE_KEYS:
        if key not in settings:
            continue
        current_raw = settings[key]
        current = (
            json.dumps(current_raw, sort_keys=True, separators=(",", ":"))
            if isinstance(current_raw, (list, dict))
            else str(current_raw)
        )
        if current.casefold() == disable_value.casefold():
            continue
        detections.append(
            DetectedEditorConfig(
                path=rel,
                language=language,
                key=key,
                current=current,
                disable_value=disable_value,
            )
        )

    detections.sort(key=lambda d: (d.language, d.key))
    return tuple(detections)
