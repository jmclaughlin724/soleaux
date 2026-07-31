"""Real producer coverage for every retained relation-table capability."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _assertions import raises_with_message

import soleaux.analysis.frame
import soleaux.contracts.config
import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.requests
import soleaux.contracts.tables
import soleaux.contracts.workspace
import soleaux.lsp.providers
import soleaux.lsp.resolvers
import soleaux.structural.engines
import soleaux.structural.snapshot
import soleaux.structural.standards
import soleaux.structural.supervisor
import soleaux.tables.evidence
import soleaux.tables.imported
import soleaux.tables.planner


async def _bundle(root: Path) -> soleaux.structural.snapshot.SnapshotBundle:
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="table-producers-test",
    ).get("workspace")
    return await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture()


def _catalog_row(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    table: str,
    data: dict[str, object],
) -> soleaux.contracts.frame.FactRow:
    return soleaux.contracts.frame.FactRow(
        table=table,
        data=data,
        evidence=soleaux.tables.evidence.evidence_for_path(
            bundle,
            path="src/main.py",
            table=table,
            data=data,
            evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
            resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
            authority=soleaux.contracts.evidence.Authority.MANIFEST,
            provider="catalog-fixture",
            provider_version="1",
        ),
    )


async def test_quality_standards_runs_configured_rules_through_structural_owner(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "rules").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('visible')\n", encoding="utf-8")
    (tmp_path / "src" / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
    (tmp_path / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
    (tmp_path / "rules" / "no-print.yml").write_text(
        "\n".join(
            (
                "id: no-print",
                "language: Python",
                "severity: warning",
                "message: avoid print",
                "files:",
                "  - src/**/*.py",
                "ignores:",
                "  - src/ignored.py",
                "rule:",
                "  pattern: print($A)",
                "",
            )
        ),
        encoding="utf-8",
    )
    bundle = await _bundle(tmp_path)
    config = soleaux.contracts.config.StructuralConfig(
        backend="python",
        project_config="sgconfig.yml",
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    engines = soleaux.structural.engines.StructuralEngines(
        supervisor,
        root=tmp_path,
        config=config,
    )
    producer = soleaux.analysis.frame.StructuralTableProducer(
        supervisor,
        policy=soleaux.structural.standards.WorkspaceStandardsAnalyzer(
            root=tmp_path,
            config=config,
            engines=engines,
        ),
    )
    try:
        plan = soleaux.tables.planner.TablePlanner().plan(
            include_tables=("quality.standards",),
            exclude_tables=(),
        )
        frame = await soleaux.tables.planner.TablePlanner().execute(
            plan,
            bundle=bundle,
            semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            producers={soleaux.contracts.tables.Producer.STRUCTURAL: producer},
        )
    finally:
        await engines.aclose()
        await supervisor.aclose()

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    assert [row.data["path"] for row in frame.tables["quality.standards"]] == ["src/main.py"]
    assert frame.tables["quality.standards"][0].data["rule_id"] == "no-print"


async def test_semantic_tables_project_only_normalized_provider_facts(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("target = 1\n", encoding="utf-8")
    bundle = await _bundle(tmp_path)
    engine_id = "loaded:python:fixture:semantic:api"
    engine = _catalog_row(
        bundle,
        table="repository.engines",
        data={
            "project_id": "python:fixture",
            "engine_id": engine_id,
            "available": True,
            "capabilities": (
                "checker_symbols",
                "definitions",
                "references",
                "implementations",
                "calls",
                "diagnostics",
            ),
        },
    )
    location = {
        "path": "src/main.py",
        "byte_start": 0,
        "byte_end": 6,
        "kind": "variable",
        "name": "target",
    }
    symbol = _catalog_row(
        bundle,
        table="repository.symbols",
        data={
            "symbol_id": "a" * 64,
            "revision_id": "b" * 64,
            "project_id": "python:fixture",
            "path": "src/main.py",
            "name": "target",
            "symbol_kind": "variable",
            "byte_start": 0,
            "byte_end": 6,
            "engine_id": engine_id,
            "coverage": "semantic",
            "definitions": (location,),
            "references": (location,),
            "implementations": (location,),
            "calls": (
                {
                    **location,
                    "callee": "target",
                    "signature_text": None,
                    "return_type_text": None,
                },
            ),
        },
    )
    diagnostic = _catalog_row(
        bundle,
        table="repository.diagnostics",
        data={
            "diagnostic_id": "c" * 64,
            "project_id": "python:fixture",
            "path": "src/main.py",
            "engine_id": engine_id,
            "category": "warning",
            "message": "fixture",
            "byte_start": 0,
            "byte_end": 6,
            "coverage": "semantic",
        },
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    resolver = soleaux.lsp.resolvers.SemanticResolver(
        soleaux.lsp.providers.ProviderRegistry.default(tmp_path)
    )
    producer = soleaux.analysis.frame.SemanticTableProducer(
        soleaux.analysis.frame.StructuralTableProducer(supervisor),
        resolver,
    )
    try:
        output = await producer.produce(
            (
                "semantic.symbols",
                "semantic.definitions",
                "semantic.references",
                "semantic.implementations",
                "semantic.calls",
                "quality.diagnostics",
            ),
            bundle,
            soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
            {
                "repository.engines": (engine,),
                "repository.symbols": (symbol,),
                "repository.diagnostics": (diagnostic,),
            },
        )
    finally:
        await resolver.shutdown()
        await supervisor.aclose()

    assert tuple(output) == (
        "semantic.symbols",
        "semantic.definitions",
        "semantic.references",
        "semantic.implementations",
        "semantic.calls",
        "quality.diagnostics",
    )
    assert all(len(rows) == 1 for rows in output.values())
    assert all(
        row.evidence.resolution_status is soleaux.contracts.evidence.ResolutionStatus.RESOLVED
        for rows in output.values()
        for row in rows
    )
    assert producer.coverage_notes() == ()


async def test_missing_semantic_provider_is_unsupported_not_empty_complete(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("target = 1\n", encoding="utf-8")
    bundle = await _bundle(tmp_path)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    resolver = soleaux.lsp.resolvers.SemanticResolver(
        soleaux.lsp.providers.ProviderRegistry.default(tmp_path)
    )
    semantic = soleaux.analysis.frame.SemanticTableProducer(
        soleaux.analysis.frame.StructuralTableProducer(supervisor),
        resolver,
    )

    class EmptyCatalog:
        supported_tables = soleaux.contracts.tables.PRODUCER_SUPPORTED_TABLES[
            soleaux.contracts.tables.Producer.CATALOG
        ]

        async def produce(
            self,
            table_names: tuple[str, ...],
            bundle: soleaux.structural.snapshot.SnapshotBundle,
            semantic_mode: soleaux.contracts.requests.SemanticMode,
            upstream_tables: object,
        ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
            del bundle, semantic_mode, upstream_tables
            return {table_name: () for table_name in table_names}

    try:
        plan = soleaux.tables.planner.TablePlanner().plan(
            include_tables=("semantic.symbols",),
            exclude_tables=(),
        )
        frame = await soleaux.tables.planner.TablePlanner().execute(
            plan,
            bundle=bundle,
            semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
            producers={
                soleaux.contracts.tables.Producer.CATALOG: EmptyCatalog(),
                soleaux.contracts.tables.Producer.SEMANTIC: semantic,
            },
        )
    finally:
        await resolver.shutdown()
        await supervisor.aclose()

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.UNSUPPORTED
    assert "semantic.symbols" not in frame.tables
    assert any(
        "no normalized semantic symbol provider" in reason
        for reason in frame.coverage.omitted_reasons
    )


async def test_coverage_import_binds_rows_to_configured_artifact_and_snapshot(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")
    bundle = await _bundle(tmp_path)
    artifact_path = tmp_path / "ci" / "coverage.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text(
        json.dumps(
            {
                "schema_version": "soleaux.coverage/v1",
                "run_id": "ci-42",
                "snapshot_id": bundle.snapshot.snapshot_id,
                "source_fingerprint": bundle.snapshot.source_fingerprint,
                "records": [
                    {
                        "subject": "src/main.py",
                        "metric": "line",
                        "value": 1.0,
                        "hits": 1,
                        "total": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = soleaux.contracts.config.CoverageImportConfig(
        artifacts=(
            soleaux.contracts.config.CoverageArtifactConfig(
                path="ci/coverage.json",
            ),
        )
    )
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("coverage",),
        exclude_tables=(),
    )
    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=bundle,
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        producers={
            soleaux.contracts.tables.Producer.IMPORTED: (
                soleaux.tables.imported.ImportedTableProducer(tmp_path, config)
            )
        },
    )

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    row = frame.tables["coverage"][0]
    assert row.data["snapshot_match"] is True
    assert row.data["run_id"] == "ci-42"
    assert row.evidence.path == "ci/coverage.json"


async def test_coverage_import_rejection_note_does_not_disclose_artifact_content(
    tmp_path: Path,
) -> None:
    sentinel = "private-pydantic-rejected-input-sentinel"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")
    artifact_path = tmp_path / "ci" / "coverage.json"
    artifact_path.parent.mkdir()
    artifact_path.write_text(
        json.dumps(
            {
                "schema_version": "soleaux.coverage/v1",
                "run_id": "ci-rejected",
                "records": [
                    {
                        "subject": "src/main.py",
                        "metric": "line",
                        "value": 1.0,
                        "rejected": sentinel,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bundle = await _bundle(tmp_path)
    config = soleaux.contracts.config.CoverageImportConfig(
        artifacts=(soleaux.contracts.config.CoverageArtifactConfig(path="ci/coverage.json"),)
    )
    producer = soleaux.tables.imported.ImportedTableProducer(tmp_path, config)

    result = await producer.produce(
        ("coverage",),
        bundle,
        soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        {},
    )

    assert result == {}
    assert producer.coverage_notes() == (
        "coverage artifact 'ci/coverage.json' does not match the required schema",
    )
    assert sentinel not in "\n".join(producer.coverage_notes())


async def test_coverage_import_reads_only_limit_plus_one_and_rejects_before_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")
    bundle = await _bundle(tmp_path)
    artifact_path = tmp_path / "ci" / "coverage.json"
    artifact_path.parent.mkdir()
    with artifact_path.open("wb") as artifact_file:
        artifact_file.truncate(soleaux.tables.imported.MAX_COVERAGE_ARTIFACT_BYTES + 2)
    assert artifact_path.stat().st_size == soleaux.tables.imported.MAX_COVERAGE_ARTIFACT_BYTES + 2

    original_open = Path.open
    real_stream = original_open(artifact_path, "rb")
    read_probe = MagicMock(wraps=real_stream)
    read_probe.__enter__.return_value = read_probe

    def close_stream(*_args: object) -> None:
        real_stream.close()

    read_probe.__exit__.side_effect = close_stream

    def bounded_open(path: Path, mode: str) -> MagicMock:
        assert path == artifact_path.resolve()
        assert mode == "rb"
        return read_probe

    def reject_parse(_value: object) -> object:
        raise AssertionError("oversized artifact reached JSON parsing")

    monkeypatch.setattr(Path, "open", bounded_open)
    monkeypatch.setattr(soleaux.tables.imported.json, "loads", reject_parse)
    config = soleaux.contracts.config.CoverageImportConfig(
        artifacts=(soleaux.contracts.config.CoverageArtifactConfig(path="ci/coverage.json"),)
    )
    producer = soleaux.tables.imported.ImportedTableProducer(tmp_path, config)

    result = await producer.produce(
        ("coverage",),
        bundle,
        soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        {},
    )

    assert result == {}
    read_probe.read.assert_called_once_with(soleaux.tables.imported.MAX_COVERAGE_ARTIFACT_BYTES + 1)
    assert producer.coverage_notes() == (
        "coverage artifact 'ci/coverage.json' exceeds the 4194304-byte limit",
    )


@pytest.mark.parametrize("path", ["../coverage.json", "/tmp/coverage.json"])
def test_coverage_artifact_paths_must_be_workspace_relative(path: str) -> None:
    with raises_with_message(ValueError, "must stay in the workspace"):
        soleaux.contracts.config.CoverageArtifactConfig(path=path)


async def test_unsupported_projections_aggregate_coverage_notes_per_projection_and_language(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    for index in range(5):
        (tmp_path / "src" / f"mod{index}.py").write_text("value = 1\n", encoding="utf-8")
    bundle = await _bundle(tmp_path)

    class UnsupportedSupervisor(soleaux.structural.supervisor.StructuralWorkerSupervisor):
        async def extract(
            self,
            *,
            language: str,
            path: str,
            content: bytes,
            projections: tuple[str, ...],
            rules: tuple[str, ...] = (),
            symbol_query: str | None = None,
            symbol_max_results: int | None = None,
            postgresql_catalog: object = None,
            timeout: float = 30.0,
            workspace_id: str = "standalone",
        ) -> soleaux.structural.supervisor.ExtractResult:
            del language, path, content, rules, symbol_query, symbol_max_results
            del postgresql_catalog, timeout, workspace_id
            return soleaux.structural.supervisor.ExtractResult(
                fragments=(),
                diagnostics=(),
                parses=1,
                parse_ms=0.0,
                truncated=False,
                unsupported=projections,
            )

    producer = soleaux.analysis.frame.StructuralTableProducer(UnsupportedSupervisor())

    await producer.prepare(bundle, ("syntax.references",))

    assert producer.coverage_notes() == (
        "structural projection 'syntax.references' is unsupported for Python "
        "(5 files; e.g. src/mod0.py, src/mod1.py, src/mod2.py)",
    )
