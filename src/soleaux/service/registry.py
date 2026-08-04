"""The canonical owner of the machine workspace registry.

The registry lives at ``~/Library/Application Support/Soleaux/workspaces.json``
(schema ``soleaux.workspace-registry/v1``) and lists every workspace the
shared per-machine service may serve. It is mutated only by ``soleaux
attach`` / ``soleaux detach``; the service loads it once at launch, so a
registry change takes effect on restart (frozen-at-launch semantics).
``SOLEAUX_WORKSPACE_REGISTRY`` overrides the path for tests and tooling.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import cast

import platformdirs

WORKSPACE_REGISTRY_SCHEMA = "soleaux.workspace-registry/v1"


class RegistryError(RuntimeError):
    """A bounded registry failure safe to surface to an operator."""


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """One authorized workspace in the machine registry."""

    workspace_id: str
    root: Path


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRegistry:
    """The frozen-at-load registry document."""

    entries: tuple[WorkspaceEntry, ...]
    raw: bytes

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return tuple(entry.workspace_id for entry in self.entries)


def registry_path() -> Path:
    override = os.environ.get("SOLEAUX_WORKSPACE_REGISTRY")
    if override:
        return Path(override)
    return platformdirs.user_config_path("Soleaux", appauthor=False) / "workspaces.json"


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"registry {label} must be a nonempty string")
    if "\x00" in value:
        raise RegistryError(f"registry {label} must be NUL-free")
    return value


def parse_workspace_registry(raw: bytes) -> WorkspaceRegistry:
    """Parse and validate the registry document; never touches the filesystem."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegistryError("the Soleaux workspace registry is not valid JSON") from error
    if not isinstance(payload, dict):
        raise RegistryError("the Soleaux workspace registry must be an object")
    record = cast("dict[str, object]", payload)
    if record.get("schema_version") != WORKSPACE_REGISTRY_SCHEMA:
        raise RegistryError(
            "the Soleaux workspace registry has an unsupported schema "
            f"({record.get('schema_version')!r}); expected {WORKSPACE_REGISTRY_SCHEMA}"
        )
    raw_workspaces = record.get("workspaces")
    if not isinstance(raw_workspaces, list):
        raise RegistryError("the Soleaux workspace registry must list workspaces")
    workspace_items = cast("list[object]", raw_workspaces)

    seen_ids: set[str] = set()
    entries: list[WorkspaceEntry] = []
    for index, item in enumerate(workspace_items):
        if not isinstance(item, dict):
            raise RegistryError(f"registry workspaces[{index}] must be an object")
        entry = cast("dict[str, object]", item)
        workspace_id = _required_string(entry.get("workspace_id"), "workspace_id")
        if workspace_id in seen_ids:
            raise RegistryError(f"duplicate workspace_id in the registry: {workspace_id}")
        seen_ids.add(workspace_id)
        root_value = _required_string(entry.get("root"), "root")
        root = Path(root_value)
        if not root.is_absolute():
            raise RegistryError(f"registry root must be absolute: {root_value}")
        entries.append(WorkspaceEntry(workspace_id=workspace_id, root=root))
    return WorkspaceRegistry(entries=tuple(entries), raw=raw)


def load_workspace_registry(path: Path | None = None) -> WorkspaceRegistry:
    """Load the registry from disk; a missing file yields an empty registry."""
    resolved_path = path if path is not None else registry_path()
    try:
        raw = resolved_path.read_bytes()
    except FileNotFoundError:
        return WorkspaceRegistry(entries=(), raw=b"")
    except OSError as error:
        raise RegistryError(f"the Soleaux workspace registry cannot be read: {error}") from error
    return parse_workspace_registry(raw)


def render_workspace_registry(entries: tuple[WorkspaceEntry, ...]) -> bytes:
    """Render the canonical registry document for one entry set."""
    document = {
        "schema_version": WORKSPACE_REGISTRY_SCHEMA,
        "workspaces": [
            {"root": str(entry.root), "workspace_id": entry.workspace_id} for entry in entries
        ],
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_workspace_registry(
    entries: tuple[WorkspaceEntry, ...],
    path: Path | None = None,
) -> Path:
    """Atomically replace the registry document (attach/detach are the callers)."""
    resolved_path = path if path is not None else registry_path()
    rendered = render_workspace_registry(entries)
    parse_workspace_registry(rendered)  # never write a document we cannot load
    resolved_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=resolved_path.parent,
        prefix=f".{resolved_path.name}.",
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
        temporary_path.chmod(0o600)
        temporary_path.replace(resolved_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return resolved_path
