"""Normal analysis creates no repository-local runtime state."""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest

from soleaux.analysis.service import SoleauxService
from soleaux.contracts.requests import (
    ContextRequest,
    DescribeRequest,
    SearchRequest,
    SemanticMode,
)
from soleaux.contracts.results import ResultStatus


def _inventory(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


async def test_zero_config_analysis_writes_nothing_to_workspace(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    before = _inventory(tmp_path)
    service = SoleauxService.from_root(tmp_path)
    try:
        await service.start()
        doctor = await service.doctor()
        described = await service.describe(DescribeRequest())
        searched = await service.search(
            SearchRequest(query="answer", semantic_mode=SemanticMode.SYNTAX_ONLY)
        )
    finally:
        await service.aclose()

    assert doctor.status is ResultStatus.OK
    assert described.status is ResultStatus.OK
    assert isinstance(doctor.data, dict)
    assert isinstance(described.data, dict)
    doctor_storage = doctor.data["storage"]
    describe_storage = described.data["storage"]
    assert isinstance(doctor_storage, dict)
    assert isinstance(describe_storage, dict)
    assert doctor_storage["requested_mode"] == "memory"
    assert doctor_storage["effective_mode"] == "memory"
    assert doctor_storage["fallback_reason"] is None
    assert doctor_storage["repository_local"] is False
    assert describe_storage["mode"] == doctor_storage["effective_mode"]
    assert describe_storage["fallback_reason"] is None
    assert describe_storage["repository_local"] is False
    assert searched.status is ResultStatus.OK
    assert _inventory(tmp_path) == before
    assert not (tmp_path / ".soleaux").exists()


async def test_default_memory_lifecycle_leaves_platform_cache_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_cache = tmp_path / "private-cache"
    private_cache.mkdir()
    (private_cache / "sentinel").write_text("unchanged", encoding="utf-8")
    before = (_inventory(private_cache), _file_snapshot(private_cache))
    calls: list[tuple[str, bool]] = []

    def private_cache_path(appname: str, *, appauthor: bool) -> Path:
        calls.append((appname, appauthor))
        return private_cache

    monkeypatch.setattr(platformdirs, "user_cache_path", private_cache_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("answer = 42\n", encoding="utf-8")

    service = SoleauxService.from_root(workspace)
    try:
        await service.start()
        searched = await service.search(
            SearchRequest(query="answer", semantic_mode=SemanticMode.SYNTAX_ONLY)
        )
        context = await service.context(
            ContextRequest(
                objective="explain answer",
                paths=["main.py"],
            )
        )
    finally:
        await service.aclose()

    assert searched.status is ResultStatus.OK
    assert context.status is ResultStatus.OK
    assert service.closed is True
    assert calls
    assert set(calls) == {("soleaux", False)}
    assert (_inventory(private_cache), _file_snapshot(private_cache)) == before


async def test_describe_and_doctor_derive_repository_local_from_reported_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SoleauxService.from_root(tmp_path)
    repository_path = tmp_path / "state" / "catalog.sqlite3"

    def repository_local_status(_workspace_id: str) -> dict[str, object]:
        return {
            "requested_mode": "disk",
            "mode": "disk",
            "path": str(repository_path),
            "fallback_reason": None,
        }

    monkeypatch.setattr(service._frames, "catalog_status", repository_local_status)
    try:
        described = await service.describe(DescribeRequest())
        doctor = await service.doctor()
    finally:
        await service.aclose()

    assert described.status is ResultStatus.OK
    assert doctor.status is ResultStatus.OK
    assert isinstance(described.data, dict)
    assert isinstance(doctor.data, dict)
    describe_storage = described.data["storage"]
    doctor_storage = doctor.data["storage"]
    assert isinstance(describe_storage, dict)
    assert isinstance(doctor_storage, dict)
    assert describe_storage["repository_local"] is True
    assert doctor_storage["repository_local"] is True
