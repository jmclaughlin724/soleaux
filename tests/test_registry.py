"""The machine workspace registry: schema, parse, and frozen-at-launch composition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from _assertions import raises_with_message

import soleaux.contracts.requests
import soleaux.contracts.results
from soleaux.analysis.service import SoleauxService
from soleaux.contracts.workspace import UnauthorizedRootError
from soleaux.service.registry import (
    WORKSPACE_REGISTRY_SCHEMA,
    RegistryError,
    WorkspaceEntry,
    load_workspace_registry,
    parse_workspace_registry,
    registry_path,
    write_workspace_registry,
)


def _workspace(tmp_path: Path, name: str, config: str | None = None) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / ".git").mkdir()
    if config is not None:
        (root / "soleaux.toml").write_text(config, encoding="utf-8")
    return root


def _entries(*roots: Path) -> tuple[WorkspaceEntry, ...]:
    return tuple(WorkspaceEntry(workspace_id=root.name, root=root) for root in roots)


def test_parse_validates_schema_ids_and_absolute_roots(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "alpha")
    rendered = (
        json.dumps(
            {
                "schema_version": WORKSPACE_REGISTRY_SCHEMA,
                "workspaces": [{"workspace_id": "alpha", "root": str(root)}],
            }
        )
    ).encode()
    registry = parse_workspace_registry(rendered)
    assert registry.workspace_ids == ("alpha",)

    with raises_with_message(RegistryError, "unsupported schema"):
        parse_workspace_registry(b'{"schema_version": "other", "workspaces": []}')
    with raises_with_message(RegistryError, "duplicate workspace_id"):
        parse_workspace_registry(
            json.dumps(
                {
                    "schema_version": WORKSPACE_REGISTRY_SCHEMA,
                    "workspaces": [
                        {"workspace_id": "alpha", "root": str(root)},
                        {"workspace_id": "alpha", "root": str(root)},
                    ],
                }
            ).encode()
        )
    with raises_with_message(RegistryError, "absolute"):
        parse_workspace_registry(
            json.dumps(
                {
                    "schema_version": WORKSPACE_REGISTRY_SCHEMA,
                    "workspaces": [{"workspace_id": "alpha", "root": "relative/path"}],
                }
            ).encode()
        )
    with raises_with_message(RegistryError, "not valid JSON"):
        parse_workspace_registry(b"not json")


def test_missing_registry_loads_empty(tmp_path: Path) -> None:
    registry = load_workspace_registry(tmp_path / "workspaces.json")
    assert registry.entries == ()


def test_write_is_atomic_and_round_trips(tmp_path: Path) -> None:
    root = _workspace(tmp_path, "alpha")
    path = tmp_path / "registry" / "workspaces.json"

    written = write_workspace_registry(_entries(root), path)

    assert written == path
    loaded = load_workspace_registry(path)
    assert loaded.workspace_ids == ("alpha",)
    assert loaded.entries[0].root == root
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "custom.json"
    monkeypatch.setenv("SOLEAUX_WORKSPACE_REGISTRY", str(override))
    assert registry_path() == override


def test_from_registry_loads_each_workspaces_own_config(tmp_path: Path) -> None:
    configured = _workspace(
        tmp_path,
        "configured",
        'structural = { backend = "python", project_config = "pyproject.toml" }\n',
    )
    plain = _workspace(tmp_path, "plain")
    registry_file = tmp_path / "workspaces.json"
    write_workspace_registry(_entries(configured, plain), registry_file)

    service = SoleauxService.from_registry(registry_file)

    assert service.workspace_ids == ("configured", "plain")
    configured_ws = service._workspaces.get("configured")
    plain_ws = service._workspaces.get("plain")
    assert service._config_for(configured_ws).structural.project_config == "pyproject.toml"
    assert service._config_for(plain_ws).structural.project_config is None
    assert service._config_digest_for(configured_ws) != service._config_digest_for(plain_ws)


async def test_describe_projects_per_workspace_configuration(tmp_path: Path) -> None:
    configured = _workspace(
        tmp_path,
        "configured",
        'structural = { backend = "python", project_config = "pyproject.toml" }\n',
    )
    plain = _workspace(tmp_path, "plain")
    registry_file = tmp_path / "workspaces.json"
    write_workspace_registry(_entries(configured, plain), registry_file)

    service = SoleauxService.from_registry(registry_file)
    async with service:
        for workspace_id, expected in (("configured", "pyproject.toml"), ("plain", None)):
            envelope = await service.describe(
                soleaux.contracts.requests.DescribeRequest(workspace_id=workspace_id)
            )
            assert envelope.status is soleaux.contracts.results.ResultStatus.OK
            assert envelope.data is not None
            configuration = envelope.data["configuration"]
            assert isinstance(configuration, dict)
            value = cast("dict[str, object]", configuration)["value"]
            assert isinstance(value, dict)
            structural = cast("dict[str, object]", value)["structural"]
            assert isinstance(structural, dict)
            assert cast("dict[str, object]", structural)["project_config"] == expected


def test_from_registry_rejects_missing_duplicate_and_aliased_roots(tmp_path: Path) -> None:
    registry_file = tmp_path / "workspaces.json"
    write_workspace_registry(
        (WorkspaceEntry(workspace_id="ghost", root=tmp_path / "missing"),),
        registry_file,
    )
    with raises_with_message(UnauthorizedRootError, "does not exist"):
        SoleauxService.from_registry(registry_file)

    root = _workspace(tmp_path, "alpha")
    write_workspace_registry(
        (
            WorkspaceEntry(workspace_id="first", root=root),
            WorkspaceEntry(workspace_id="second", root=root),
        ),
        registry_file,
    )
    with raises_with_message(UnauthorizedRootError, "duplicate"):
        SoleauxService.from_registry(registry_file)

    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    write_workspace_registry(
        (
            WorkspaceEntry(workspace_id="alpha", root=root),
            WorkspaceEntry(workspace_id="alias", root=alias),
        ),
        registry_file,
    )
    with raises_with_message(UnauthorizedRootError, "duplicate"):
        SoleauxService.from_registry(registry_file)


def test_from_registry_is_frozen_at_launch(tmp_path: Path) -> None:
    first = _workspace(tmp_path, "first")
    registry_file = tmp_path / "workspaces.json"
    write_workspace_registry(_entries(first), registry_file)
    service = SoleauxService.from_registry(registry_file)

    second = _workspace(tmp_path, "second")
    write_workspace_registry(_entries(first, second), registry_file)

    assert service.workspace_ids == ("first",)
    with raises_with_message(UnauthorizedRootError, "not in the AllowedWorkspaceSet"):
        service._workspaces.get("second")
