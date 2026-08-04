"""Doctor reports stable, redacted capability data without analyzing source."""

from __future__ import annotations

import json
import pathlib
import sys

import _assertions
import pytest

import soleaux.analysis.service
import soleaux.contracts.results
import soleaux.typescript.contracts


def _data(value: object) -> dict[str, object]:
    return _assertions.object_mapping(value)


async def test_doctor_default_is_scan_free_stable_and_redacted(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)

    try:
        response = await service.doctor()
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    data = _data(response.data)
    assert data["schema_version"] == "soleaux.doctor/v1"
    workspace = _data(data["workspace"])
    assert workspace["root"] == str(tmp_path.resolve())
    config = _data(data["config"])
    assert config["source"] == "defaults"
    analysis = _data(data["analysis"])
    assert analysis == {
        "recursive_analysis_performed": False,
        "source_files_opened": 0,
        "structural_worker_started": False,
    }
    typed_providers = _assertions.object_list(data["providers"])
    provider_names = {str(_data(provider)["name"]) for provider in typed_providers}
    assert "typescript-language-server" in provider_names
    assert "pyright-langserver" in provider_names
    assert "gopls" in provider_names
    assert "pylsp" not in provider_names
    serialized = json.dumps(data, sort_keys=True)
    assert '"argv"' not in serialized
    assert '"command"' not in serialized
    assert source.read_text(encoding="utf-8").endswith("return 42\n")
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before


async def test_doctor_probe_is_bounded_and_does_not_start_analyzer(tmp_path: pathlib.Path) -> None:
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    try:
        response = await service.doctor(probe=True)
        assert service.structural_worker_started is False
    finally:
        await service.aclose()

    data = _data(response.data)
    probe = _data(data["probe"])
    assert probe["requested"] is True
    assert probe["completed"] is True
    assert probe["structural_engine_version"] == "0.45.0"
    assert "ast_grep" not in sys.modules
    assert "ast_grep_py" not in sys.modules


async def test_doctor_reports_missing_typescript_runtime_without_starting_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_prefix = tmp_path / "managed-typescript"
    monkeypatch.setenv("SOLEAUX_TYPESCRIPT_RUNTIME", str(runtime_prefix))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async with soleaux.analysis.service.SoleauxService.from_root(workspace) as service:
        response = await service.doctor()

    data = _data(response.data)
    probe = _data(data["probe"])
    runtime = _data(probe["typescript_runtime"])
    assert runtime == {
        "available": False,
        "prefix": str(runtime_prefix),
        "ts_morph_version": None,
        "native_typescript_version": None,
        "expected_ts_morph_version": soleaux.typescript.contracts.TS_MORPH_VERSION,
        "expected_native_typescript_version": (
            soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION
        ),
        "worker_started": False,
    }
    assert not runtime_prefix.exists()
