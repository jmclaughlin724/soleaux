"""Contained workspace rule loading from one configured ast-grep project.

Only the explicitly configured project configuration and its declared rule
directories are read, with file-count and byte bounds. Documents keep the
official ast-grep fields; matching fields are validated against the
`soleaux.structural/v1` vocabulary and experimental rewriters are rejected.
Loading is lenient per document — a malformed rule becomes a warning, never a
crash — but referencing a rule that failed to load stays a typed error.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

from soleaux.contracts.structural import StructuralUnsupportedError, validate_rule_fields

MAX_RULE_DIRS = 16
MAX_RULE_FILES = 512
MAX_RULE_FILE_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 256 * 1024

_DOCUMENT_FIELDS = frozenset(
    {
        "id",
        "language",
        "severity",
        "message",
        "note",
        "url",
        "metadata",
        "labels",
        "files",
        "ignores",
        "rule",
        "constraints",
        "utils",
        "fix",
        "transform",
        "rewriters",
    }
)


@dataclass(frozen=True)
class WorkspaceRule:
    """One workspace-configured rule normalized for engine execution."""

    rule_id: str
    language: str
    severity: str
    message: str
    note: str
    rule: dict[str, Any]
    constraints: dict[str, Any]
    utils: dict[str, Any]
    fix: str | dict[str, Any] | None
    transforms: dict[str, Any] | None
    files: tuple[str, ...]
    ignores: tuple[str, ...]
    source_path: str
    digest: str


def _contained(root: Path, configured: str) -> Path | None:
    candidate = PurePosixPath(configured)
    if candidate.is_absolute() or ".." in candidate.parts or "\x00" in configured:
        return None
    return root / configured


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("dict[object, object]", value)
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in cast("list[object]", value) if isinstance(item, str) and item)


def _rule_files(directory: Path, *, budget: int) -> tuple[Path, ...]:
    collected: list[Path] = []
    for current_root, directories, names in os.walk(directory):
        directories.sort()
        for name in sorted(names):
            if not name.endswith((".yml", ".yaml")):
                continue
            collected.append(Path(current_root) / name)
            if len(collected) >= budget:
                return tuple(collected)
    return tuple(collected)


def load_workspace_rules(
    root: Path,
    project_config: str,
) -> tuple[dict[str, WorkspaceRule], tuple[str, ...]]:
    """Load `(rules_by_id, warnings)` from one contained project configuration."""
    warnings: list[str] = []
    config_path = _contained(root, project_config)
    if config_path is None or not config_path.is_file():
        return {}, (f"{project_config}: structural project configuration not found",)
    raw_config = config_path.read_bytes()
    if len(raw_config) > MAX_CONFIG_BYTES:
        return {}, (f"{project_config}: configuration exceeds {MAX_CONFIG_BYTES} bytes",)
    try:
        parsed = yaml.safe_load(raw_config)
    except yaml.YAMLError as exc:
        return {}, (f"{project_config}: {exc}",)
    rule_directories = _strings(_object(parsed).get("ruleDirs"))[:MAX_RULE_DIRS]
    if not rule_directories:
        return {}, (f"{project_config}: no ruleDirs declared",)

    rules: dict[str, WorkspaceRule] = {}
    remaining = MAX_RULE_FILES
    for configured_directory in rule_directories:
        directory = _contained(root, configured_directory)
        if directory is None or not directory.is_dir():
            warnings.append(f"{configured_directory}: rule directory not found")
            continue
        for rule_path in _rule_files(directory, budget=remaining):
            remaining -= 1
            raw = rule_path.read_bytes()
            relative = rule_path.relative_to(root).as_posix()
            if len(raw) > MAX_RULE_FILE_BYTES:
                warnings.append(f"{relative}: rule file exceeds {MAX_RULE_FILE_BYTES} bytes")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            try:
                documents = tuple(yaml.safe_load_all(raw))
            except yaml.YAMLError as exc:
                warnings.append(f"{relative}: {exc}")
                continue
            for document in documents:
                record = _object(document)
                if not record:
                    continue
                unknown = set(record) - _DOCUMENT_FIELDS
                if unknown:
                    warnings.append(f"{relative}: unknown rule fields {sorted(unknown)}")
                    continue
                if "rewriters" in record:
                    warnings.append(
                        f"{relative}: experimental rewriters are outside soleaux.structural/v1"
                    )
                    continue
                rule_id = record.get("id")
                language = record.get("language")
                rule = _object(record.get("rule"))
                if not isinstance(rule_id, str) or not isinstance(language, str) or not rule:
                    warnings.append(f"{relative}: rule requires id, language, and a rule mapping")
                    continue
                try:
                    validate_rule_fields(rule)
                except StructuralUnsupportedError as exc:
                    warnings.append(f"{relative}: {exc.reason}")
                    continue
                if rule_id in rules:
                    warnings.append(f"{relative}: duplicate rule id {rule_id!r}")
                    continue
                fix = record.get("fix")
                transform = record.get("transform")
                rules[rule_id] = WorkspaceRule(
                    rule_id=rule_id,
                    language=language,
                    severity=str(record.get("severity", "hint")),
                    message=str(record.get("message", "")),
                    note=str(record.get("note", "")),
                    rule=rule,
                    constraints=_object(record.get("constraints")),
                    utils=_object(record.get("utils")),
                    fix=fix if isinstance(fix, str) else _object(fix) or None,
                    transforms=_object(transform) or None,
                    files=_strings(record.get("files")),
                    ignores=_strings(record.get("ignores")),
                    source_path=relative,
                    digest=digest,
                )
        if remaining <= 0:
            warnings.append(f"workspace rules truncated at {MAX_RULE_FILES} files")
            break
    return rules, tuple(warnings)
