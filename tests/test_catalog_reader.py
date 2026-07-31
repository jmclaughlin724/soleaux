"""Acceptance contracts for lifecycle-published catalog reads."""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
import sqlite3
from collections.abc import Callable, Sequence
from typing import cast

import pytest

import soleaux.analysis.frame
import soleaux.analysis.service
import soleaux.catalog.generation
import soleaux.catalog.store
import soleaux.contracts.config
import soleaux.contracts.context
import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.structural.snapshot
import soleaux.structural.supervisor


def _materialized_generation(
    generation: int,
) -> tuple[soleaux.contracts.frame.AnalysisFrame, soleaux.contracts.frame.FactRow]:
    text = f"generation {generation}"
    source_fingerprint = soleaux.contracts.repository.content_digest(text.encode("utf-8"))
    snapshot_id = f"main:{source_fingerprint[:16]}"
    row = soleaux.contracts.frame.FactRow(
        table="source.context",
        data={
            "generation": generation,
            "path": "record.txt",
            "snippet": text,
        },
        evidence=soleaux.contracts.evidence.Evidence(
            evidence_id=soleaux.contracts.repository.content_digest(
                f"{generation}\0{text}".encode()
            ),
            evidence_kind=soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
            resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
            provider="test",
            provider_version=str(generation),
            authority=soleaux.contracts.evidence.Authority.SOURCE,
            snapshot_id=snapshot_id,
            path="record.txt",
            range=soleaux.contracts.evidence.PositionRange(
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=len(text) + 1,
            ),
            source_hash=source_fingerprint,
            source_fingerprint=source_fingerprint,
            confidence=1.0,
        ),
    )
    frame = soleaux.contracts.frame.AnalysisFrame(
        snapshot_id=snapshot_id,
        workspace_id="main",
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        coverage=soleaux.contracts.coverage.Coverage(
            status=soleaux.contracts.coverage.FrameStatus.COMPLETE,
            eligible_files=1,
            examined_files=1,
            parse_failures=0,
            candidate_count=0,
            resolution_attempts=0,
            resolved_count=1,
            unsupported_count=0,
            failed_count=0,
            deadline=datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1),
            row_file_byte_depth_limits=(
                soleaux.contracts.coverage.RowFileByteDepthLimits(
                    max_rows=1,
                    max_files=1,
                    max_bytes=len(text),
                    max_depth=1,
                )
            ),
            elapsed_ms=0.0,
        ),
        tables={"source.context": (row,)},
    )
    return frame, row


def _publish(
    store: soleaux.catalog.store.CatalogStore,
    generation: int,
) -> soleaux.contracts.frame.FactRow:
    frame, row = _materialized_generation(generation)
    store.publish_materialized(
        frame,
        generation=generation,
        source_fingerprint=row.evidence.source_fingerprint,
        rows=(row,),
        kinds={row.evidence.evidence_id: "chunk"},
        relationships=(),
        retained_generations=2,
    )
    return row


def _context_row(
    template: soleaux.contracts.frame.FactRow,
    table: str,
    path: str,
    data: dict[str, object],
) -> soleaux.contracts.frame.FactRow:
    return template.model_copy(
        update={
            "table": table,
            "data": data,
            "evidence": template.evidence.model_copy(
                update={
                    "evidence_id": soleaux.contracts.repository.content_digest(path.encode()),
                    "path": path,
                }
            ),
        }
    )


def _stub_materialized_rows(
    monkeypatch: pytest.MonkeyPatch,
    ordered_rows: tuple[soleaux.contracts.frame.FactRow, ...],
) -> None:
    original_read = cast(
        Callable[..., soleaux.catalog.store.MaterializedRead],
        soleaux.catalog.store.CatalogStore.read_materialized,
    )

    def stubbed_read(
        store: soleaux.catalog.store.CatalogStore,
        *args: object,
        **kwargs: object,
    ) -> soleaux.catalog.store.MaterializedRead:
        read = original_read(store, *args, **kwargs)
        return dataclasses.replace(
            read,
            rows=tuple(
                soleaux.catalog.store.MaterializedRow(
                    row=row,
                    fact_key=f"fact-{index}",
                    kind="chunk",
                    score=1.0,
                    relation_distance=0,
                )
                for index, row in enumerate(ordered_rows)
            ),
        )

    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "read_materialized",
        stubbed_read,
    )


def test_same_generation_republication_advances_materialization_revision(
    tmp_path: pathlib.Path,
) -> None:
    store = soleaux.catalog.store.CatalogStore(tmp_path)
    store.open()
    try:
        _publish(store, 1)
        first = store.read_materialized("main", limit=1)

        _publish(store, 1)
        second = store.read_materialized("main", limit=1)
    finally:
        store.close()

    assert first.generation == second.generation == 1
    assert first.publication_revision == 1
    assert second.publication_revision == 2


async def test_context_before_lifecycle_publication_is_retryable_catalog_not_ready(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "record.txt").write_text("published later\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(objective="published later")
        )
    finally:
        await service.aclose()

    assert isinstance(response, soleaux.contracts.results.TaskContextEnvelope)
    assert response.status is soleaux.contracts.results.ResultStatus.ERROR
    assert response.error is not None
    assert response.error.error_type == "catalog_not_ready"
    assert response.error.retryable is True


async def test_search_reads_lifecycle_published_base_with_scoped_coverage(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "record.py").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()

    try:
        response = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="record",
                kinds=[soleaux.contracts.requests.SearchKind.FILE],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.coverage is not None
    assert response.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    assert response.coverage.unsupported_count == 0
    assert response.rows
    assert {row["path"] for row in response.rows} == {"record.py"}


async def test_context_reads_enriched_sqlite_without_request_path_repository_work(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    async def unexpected_async(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("context must use only the published SQLite generation")

    def unexpected_sync(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("context must use only the published SQLite generation")

    monkeypatch.setattr(
        soleaux.structural.snapshot.RepositorySnapshotter,
        "capture",
        unexpected_async,
    )
    monkeypatch.setattr(
        soleaux.structural.snapshot.RepositorySnapshotter,
        "inventory",
        unexpected_async,
    )
    monkeypatch.setattr(
        soleaux.analysis.frame.AnalysisFrameBuilder,
        "capture",
        unexpected_async,
    )
    monkeypatch.setattr(
        soleaux.analysis.frame.AnalysisFrameBuilder,
        "catalog_bundle",
        unexpected_async,
    )
    monkeypatch.setattr(
        soleaux.analysis.frame.AnalysisFrameBuilder,
        "build",
        unexpected_async,
    )
    monkeypatch.setattr(
        soleaux.catalog.generation.CatalogGenerationBuilder,
        "build",
        unexpected_sync,
    )
    monkeypatch.setattr(
        soleaux.catalog.generation.CatalogGenerationBuilder,
        "update",
        unexpected_sync,
    )
    monkeypatch.setattr(
        soleaux.structural.supervisor.StructuralWorkerSupervisor,
        "extract",
        unexpected_async,
    )
    read_options: list[bool] = []
    original_read = cast(
        Callable[..., soleaux.catalog.store.MaterializedRead],
        soleaux.catalog.store.CatalogStore.read_materialized,
    )

    def observe_read(
        store: soleaux.catalog.store.CatalogStore,
        *args: object,
        **kwargs: object,
    ) -> soleaux.catalog.store.MaterializedRead:
        read_options.append(bool(kwargs.get("count_total_rows", True)))
        return original_read(store, *args, **kwargs)

    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "read_materialized",
        observe_read,
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                paths=["record.txt"],
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.data is not None
    assert response.data.retrieval_engine == "sqlite-fts5"
    assert read_options == [False]
    assert any(
        item.table == "source.context" and "answer = 42" in item.data["snippet"]
        for item in response.data.sources
    )


async def test_enriched_read_tools_share_one_published_identity_without_repository_work(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.py").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()
    identities: list[tuple[int, str, str]] = []

    def record_read(read: soleaux.catalog.store.MaterializedRead) -> None:
        identities.append(
            (
                read.generation,
                read.snapshot_id,
                read.source_fingerprint,
            )
        )

    original_search = service._catalog_reader.search
    original_context = service._catalog_reader.context
    original_tables = service._catalog_reader.tables

    def recorded_search(
        workspace_id: str,
        *,
        query: str,
        kinds: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        read = original_search(
            workspace_id,
            query=query,
            kinds=kinds,
            path_prefixes=path_prefixes,
            limit=limit,
            offset=offset,
        )
        record_read(read)
        return read

    def recorded_context(
        workspace_id: str,
        *,
        objective: str,
        terms: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        limit: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        read = original_context(
            workspace_id,
            objective=objective,
            terms=terms,
            path_prefixes=path_prefixes,
            limit=limit,
        )
        record_read(read)
        return read

    def recorded_tables(
        workspace_id: str,
        *,
        include_tables: tuple[str, ...],
        path_prefixes: tuple[str, ...] = (),
        policy_ids: tuple[str, ...] = (),
        seed_keys: tuple[str, ...] = (),
        ownership_selector: str | None = None,
        relation_depth: int = 0,
        limit: int,
        offset: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        read = original_tables(
            workspace_id,
            include_tables=include_tables,
            path_prefixes=path_prefixes,
            policy_ids=policy_ids,
            seed_keys=seed_keys,
            ownership_selector=ownership_selector,
            relation_depth=relation_depth,
            limit=limit,
            offset=offset,
        )
        record_read(read)
        return read

    async def unexpected_async(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("base reads must use only the published SQLite generation")

    def unexpected_sync(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("base reads must not publish a catalog generation")

    monkeypatch.setattr(service._catalog_reader, "search", recorded_search)
    monkeypatch.setattr(service._catalog_reader, "context", recorded_context)
    monkeypatch.setattr(service._catalog_reader, "tables", recorded_tables)
    monkeypatch.setattr(service._frames, "capture", unexpected_async)
    monkeypatch.setattr(service._frames, "catalog_bundle", unexpected_async)
    monkeypatch.setattr(service._frames, "build", unexpected_async)
    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "publish",
        unexpected_sync,
    )
    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "publish_materialized",
        unexpected_sync,
    )

    try:
        responses = (
            await service.search(
                soleaux.contracts.requests.SearchRequest(
                    query="answer",
                    semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                )
            ),
            await service.context(
                soleaux.contracts.requests.ContextRequest(objective="explain answer")
            ),
            await service.query(
                soleaux.contracts.requests.QueryRequest(
                    include_tables=["repository.files"],
                    seed_keys=["path:record.py"],
                )
            ),
            await service.ownership(
                soleaux.contracts.requests.OwnershipRequest(
                    policy="policy:none-declared",
                )
            ),
        )
    finally:
        await service.aclose()

    assert all(
        response.status is soleaux.contracts.results.ResultStatus.OK for response in responses
    )
    assert len(identities) >= 4
    assert len(set(identities)) == 1
    _generation, snapshot_id, source_fingerprint = identities[0]
    assert {response.snapshot_id for response in responses} == {snapshot_id}
    assert {
        evidence.source_fingerprint for response in responses for evidence in response.evidence
    } == {source_fingerprint}


def test_empty_materialized_table_preserves_presence_and_complete_coverage(
    tmp_path: pathlib.Path,
) -> None:
    store = soleaux.catalog.store.CatalogStore(tmp_path)
    frame, template = _materialized_generation(1)
    empty_frame = frame.model_copy(update={"tables": {"semantic.symbols": ()}})
    store.publish_materialized(
        empty_frame,
        generation=1,
        source_fingerprint=template.evidence.source_fingerprint,
        rows=(),
        kinds={},
        relationships=(),
        retained_generations=2,
    )
    try:
        read = store.read_materialized(
            "main",
            tables=("semantic.symbols",),
            limit=10,
        )
    finally:
        store.close()

    coverage = soleaux.analysis.service.SoleauxService._coverage_for_tables(
        read.frame.coverage,
        requested_tables=("semantic.symbols",),
        published_tables=read.published_tables,
    )
    assert read.rows == ()
    assert read.frame.tables == {"semantic.symbols": ()}
    assert "semantic.symbols" in read.published_tables
    assert coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    assert coverage.unsupported_count == 0
    assert coverage.omitted_reasons == ()


def test_seeded_sqlite_read_never_deserializes_unselected_rows(
    tmp_path: pathlib.Path,
) -> None:
    store = soleaux.catalog.store.CatalogStore(tmp_path)
    matching = _publish(store, 1)
    connection = store._connection
    assert connection is not None
    connection.executemany(
        """
        INSERT INTO context_rows(
            generation, row_key, table_name, kind, path, policy_id, project_id, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                1,
                f"unselected-{index}",
                "source.context",
                "chunk",
                f"other-{index}.txt",
                "",
                "",
                "not-json",
            )
            for index in range(2_000)
        ),
    )
    try:
        read = store.read_materialized(
            "main",
            tables=("source.context",),
            seed_keys=("path:record.txt",),
            limit=1,
        )
    finally:
        store.close()

    assert read.total_rows == 1
    assert read.has_more is False
    assert [item.row.evidence.evidence_id for item in read.rows] == [matching.evidence.evidence_id]


def test_every_canonical_search_fact_key_roundtrips_through_sqlite_seed(
    tmp_path: pathlib.Path,
) -> None:
    store = soleaux.catalog.store.CatalogStore(tmp_path)
    frame, template = _materialized_generation(1)
    cases: tuple[tuple[str, str, dict[str, str]], ...] = (
        ("chunk", "source.context", {"chunk_id": "chunk-1"}),
        ("project", "repository.projects", {"project_id": "project-1"}),
        (
            "dependency",
            "repository.dependencies",
            {"project_id": "project-1", "package_name": "dependency-1"},
        ),
        (
            "script",
            "repository.scripts",
            {"project_id": "project-1", "name": "build"},
        ),
        (
            "task",
            "repository.tasks",
            {"project_id": "project-1", "task_id": "test"},
        ),
        (
            "config",
            "repository.configurations",
            {"project_id": "project-1", "config_path": "config.toml"},
        ),
        ("rule", "repository.rules", {"rule_id": "rule-1"}),
        ("policy", "authority.policies", {"policy_id": "policy-1"}),
        (
            "symbol",
            "repository.symbols",
            {"project_id": "project-1", "symbol_id": "symbol-1"},
        ),
        (
            "import",
            "repository.imports",
            {"project_id": "project-1", "import_id": "import-1"},
        ),
        (
            "diagnostic",
            "repository.diagnostics",
            {"project_id": "project-1", "diagnostic_id": "diagnostic-1"},
        ),
        ("change", "repository.changes", {"change_id": "change-1"}),
        (
            "route",
            "repository.routes",
            {"project_id": "project-1", "route_id": "route-1"},
        ),
        ("file", "repository.files", {}),
    )
    rows = tuple(
        template.model_copy(
            update={
                "table": table,
                "data": data,
                "evidence": template.evidence.model_copy(
                    update={
                        "evidence_id": f"fact-{index}",
                        "path": f"facts/{index}.txt",
                    }
                ),
            }
        )
        for index, (_kind, table, data) in enumerate(cases)
    )
    rows_by_table: dict[str, list[soleaux.contracts.frame.FactRow]] = {}
    for row in rows:
        rows_by_table.setdefault(row.table, []).append(row)
    store.publish_materialized(
        frame.model_copy(
            update={
                "tables": {table: tuple(table_rows) for table, table_rows in rows_by_table.items()}
            }
        ),
        generation=1,
        source_fingerprint=template.evidence.source_fingerprint,
        rows=rows,
        kinds={
            row.evidence.evidence_id: kind
            for row, (kind, _table, _data) in zip(rows, cases, strict=True)
        },
        relationships=(),
        retained_generations=2,
    )
    try:
        published = store.read_materialized("main", limit=len(rows))
        assert {item.kind for item in published.rows} == {kind for kind, _table, _data in cases}
        for item in published.rows:
            seeded = store.read_materialized(
                "main",
                seed_keys=(item.fact_key,),
                limit=len(rows),
            )
            assert item.row.evidence.evidence_id in {
                selected.row.evidence.evidence_id for selected in seeded.rows
            }
    finally:
        store.close()


def test_sqlite_ownership_selector_preserves_precedence_and_punctuation(
    tmp_path: pathlib.Path,
) -> None:
    store = soleaux.catalog.store.CatalogStore(tmp_path)
    frame, template = _materialized_generation(1)
    policies = (
        ("selector", "docs/exact.md", "Exact title"),
        ("alias-policy", "docs/alias.md", "selector"),
        ("path-policy", "docs/target.md", "Path title"),
        ("path-alias-policy", "docs/other.md", "docs/target.md"),
        ("dotted-policy", "docs/dotted.md", "foo.bar"),
        ("compact-policy", "docs/compact.md", "foobar"),
    )
    rows = tuple(
        template.model_copy(
            update={
                "table": "authority.policies",
                "data": {
                    "policy_id": policy_id,
                    "source_path": source_path,
                    "title": title,
                    "identity_value": title,
                    "aliases": (),
                    "scope": (),
                },
                "evidence": template.evidence.model_copy(
                    update={
                        "evidence_id": f"policy-{index}",
                        "path": source_path,
                    }
                ),
            }
        )
        for index, (policy_id, source_path, title) in enumerate(policies)
    )
    store.publish_materialized(
        frame.model_copy(update={"tables": {"authority.policies": rows}}),
        generation=1,
        source_fingerprint=template.evidence.source_fingerprint,
        rows=rows,
        kinds={row.evidence.evidence_id: "policy" for row in rows},
        relationships=(),
        retained_generations=2,
    )

    def selected_policy_ids(selector: str) -> set[str]:
        read = store.read_materialized(
            "main",
            tables=("authority.policies",),
            ownership_selector=selector,
            limit=len(rows),
        )
        return {
            policy_id
            for item in read.rows
            if isinstance((policy_id := item.row.data.get("policy_id")), str)
        }

    try:
        assert selected_policy_ids("selector") == {"selector"}
        assert selected_policy_ids("docs/target.md") == {"path-policy"}
        assert selected_policy_ids("foo.bar") == {"dotted-policy"}
        assert selected_policy_ids("foobar") == {"compact-policy"}
    finally:
        store.close()


@pytest.mark.parametrize(
    ("base_status", "reason", "expected_status"),
    (
        (
            soleaux.contracts.coverage.FrameStatus.PARTIAL,
            "catalog table enrichment degraded: structural worker unavailable",
            soleaux.contracts.coverage.FrameStatus.PARTIAL,
        ),
        (
            soleaux.contracts.coverage.FrameStatus.TRUNCATED,
            "semantic.symbols: row limit 1000 reached",
            soleaux.contracts.coverage.FrameStatus.TRUNCATED,
        ),
    ),
)
def test_table_coverage_preserves_relevant_degradation(
    base_status: soleaux.contracts.coverage.FrameStatus,
    reason: str,
    expected_status: soleaux.contracts.coverage.FrameStatus,
) -> None:
    coverage = _materialized_generation(1)[0].coverage.model_copy(
        update={
            "status": base_status,
            "omitted_reasons": (reason,),
        }
    )

    scoped = soleaux.analysis.service.SoleauxService._coverage_for_tables(
        coverage,
        requested_tables=("semantic.symbols",),
        published_tables=("semantic.symbols",),
    )

    assert scoped.status is expected_status
    assert scoped.omitted_reasons == (reason,)


def test_materialized_read_pins_one_atomic_generation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path.parent / f"{tmp_path.name}-catalog.sqlite3"
    writer = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    )
    reader = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    )
    writer.open()
    reader.open()
    _publish(writer, 1)
    original_rows_by_key = soleaux.catalog.store.CatalogStore._rows_by_key
    published_second = False

    def publish_second_before_row_hydration(
        connection: sqlite3.Connection,
        *,
        generation: int,
        row_keys: Sequence[str],
    ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, str]]:
        nonlocal published_second
        if not published_second:
            published_second = True
            _publish(writer, 2)
        return original_rows_by_key(
            connection,
            generation=generation,
            row_keys=row_keys,
        )

    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "_rows_by_key",
        staticmethod(publish_second_before_row_hydration),
    )
    try:
        pinned = reader.read_materialized("main", limit=10)
        current = reader.read_materialized("main", limit=10)
    finally:
        reader.close()
        writer.close()

    assert published_second is True
    assert pinned.generation == 1
    assert pinned.snapshot_id != current.snapshot_id
    assert {row.row.data["generation"] for row in pinned.rows} == {1}
    assert current.generation == 2
    assert {row.row.data["generation"] for row in current.rows} == {2}


def test_relation_expansion_reserves_capacity_when_primary_limit_is_saturated(
    tmp_path: pathlib.Path,
) -> None:
    database = tmp_path.parent / f"{tmp_path.name}-catalog.sqlite3"
    store = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    )
    frame, template = _materialized_generation(1)
    rows = tuple(
        template.model_copy(
            update={
                "data": {
                    "generation": 1,
                    "path": f"record-{index}.txt",
                    "snippet": f"shared objective {index}",
                },
                "evidence": template.evidence.model_copy(
                    update={
                        "evidence_id": soleaux.contracts.repository.content_digest(
                            f"relation-row-{index}".encode()
                        ),
                        "path": f"record-{index}.txt",
                    }
                ),
            }
        )
        for index in range(4)
    )
    store.publish_materialized(
        frame.model_copy(update={"tables": {"source.context": rows}}),
        generation=1,
        source_fingerprint=template.evidence.source_fingerprint,
        rows=rows,
        kinds={row.evidence.evidence_id: "chunk" for row in rows},
        relationships=(
            (
                rows[0].evidence.evidence_id,
                rows[1].evidence.evidence_id,
                "overlapping direct relation",
            ),
            (
                rows[0].evidence.evidence_id,
                rows[3].evidence.evidence_id,
                "test relation",
            ),
        ),
        retained_generations=2,
    )
    try:
        baseline = store.read_materialized(
            "main",
            match_expression='"shared" OR "objective"',
            limit=3,
        )
        result = store.read_materialized(
            "main",
            match_expression='"shared" OR "objective"',
            limit=3,
            relation_depth=1,
        )
    finally:
        store.close()

    baseline_by_path = {
        materialized.row.evidence.path: materialized for materialized in baseline.rows
    }
    returned_by_path = {
        materialized.row.evidence.path: materialized for materialized in result.rows
    }
    assert returned_by_path["record-0.txt"].relation_distance == 0
    assert returned_by_path["record-1.txt"].relation_distance == 0
    assert returned_by_path["record-1.txt"].score == baseline_by_path["record-1.txt"].score
    assert returned_by_path["record-3.txt"].relation_distance == 1
    assert len(returned_by_path) == 3
    assert result.has_more is True


def test_context_fact_selection_preserves_each_available_semantic_section() -> None:
    _frame, template = _materialized_generation(1)
    tables = (
        "repository.files",
        "repository.files",
        "authority.policies",
        "authority.bindings",
        "authority.conflicts",
        "repository.scripts",
        "repository.engines",
        "repository.projects",
    )
    rows = tuple(
        template.model_copy(
            update={
                "table": table,
                "evidence": template.evidence.model_copy(
                    update={
                        "evidence_id": soleaux.contracts.repository.content_digest(
                            f"context-section-{index}".encode()
                        ),
                        "path": f"context-section-{index}.txt",
                    }
                ),
            }
        )
        for index, table in enumerate(tables)
    )

    selected = soleaux.analysis.service.SoleauxService._select_context_facts(
        rows,
        limit=6,
    )

    assert [row.table for row in selected] == [
        "authority.policies",
        "authority.bindings",
        "authority.conflicts",
        "repository.scripts",
        "repository.engines",
        "repository.files",
    ]

    engine = next(row for row in selected if row.table == "repository.engines")
    engine = engine.model_copy(
        update={
            "data": {
                "available": False,
                "engine_id": "lsp:main:typescript-language-server",
                "omitted_reasons": ["runtime identity not loaded"],
            }
        }
    )
    (gap,) = soleaux.analysis.service.SoleauxService._context_fact_gaps((engine,))
    assert gap.code == "runtime_identity_unavailable"
    assert gap.table == "repository.engines"
    assert gap.path == engine.evidence.path
    assert "runtime identity not loaded" in gap.message


async def test_context_bounds_coverage_omission_gaps_with_summary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    original_read = cast(
        Callable[..., soleaux.catalog.store.MaterializedRead],
        soleaux.catalog.store.CatalogStore.read_materialized,
    )
    amplified_reasons = tuple(f"generation omission {index}" for index in range(100))

    def amplify_omissions(
        store: soleaux.catalog.store.CatalogStore,
        *args: object,
        **kwargs: object,
    ) -> soleaux.catalog.store.MaterializedRead:
        read = original_read(store, *args, **kwargs)
        coverage = read.frame.coverage.model_copy(
            update={
                "status": soleaux.contracts.coverage.FrameStatus.PARTIAL,
                "omitted_reasons": amplified_reasons,
            }
        )
        return dataclasses.replace(
            read,
            frame=read.frame.model_copy(update={"coverage": coverage}),
        )

    monkeypatch.setattr(
        soleaux.catalog.store.CatalogStore,
        "read_materialized",
        amplify_omissions,
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(objective="explain answer")
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.data is not None
    omission_gaps = [gap for gap in response.data.gaps if gap.code == "coverage_omission"]
    assert len(omission_gaps) == 33
    assert {gap.message for gap in omission_gaps[:32]} == set(amplified_reasons[:32])
    assert "68 further generation coverage omissions" in omission_gaps[32].message
    assert response.data.coverage_complete is False


async def test_context_scan_continues_past_exhausted_source_budget(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    _frame, template = _materialized_generation(1)
    _stub_materialized_rows(
        monkeypatch,
        (
            *(
                _context_row(
                    template,
                    "source.context",
                    f"fat-{index}.txt",
                    {"path": f"fat-{index}.txt", "snippet": "x" * 8192},
                )
                for index in range(4)
            ),
            _context_row(
                template,
                "source.context",
                "later-source.txt",
                {"path": "later-source.txt", "snippet": "y" * 8192},
            ),
            _context_row(template, "authority.policies", "owner.txt", {"policy": "owner-policy"}),
            _context_row(template, "repository.scripts", "validate.txt", {"script": "test"}),
        ),
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                max_bytes=32768,
                limit=20,
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    packet = response.data
    assert packet is not None
    assert "later-source.txt" not in {item.path for item in packet.sources}
    assert [item.path for item in packet.canonical_owners] == ["owner.txt"]
    assert [item.path for item in packet.validation_routes] == ["validate.txt"]
    assert any(gap.code == "source_excerpt_limit" for gap in packet.gaps)


async def test_context_source_quota_is_not_refilled_with_leftover_capacity(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    _frame, template = _materialized_generation(1)
    _stub_materialized_rows(
        monkeypatch,
        (
            *(
                _context_row(
                    template,
                    "source.context",
                    f"source-{index}.txt",
                    {"path": f"source-{index}.txt", "snippet": f"snippet {index}"},
                )
                for index in range(6)
            ),
            _context_row(template, "authority.policies", "owner.txt", {"policy": "owner-policy"}),
            _context_row(template, "repository.scripts", "validate.txt", {"script": "test"}),
        ),
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                max_bytes=65535,
                limit=10,
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    packet = response.data
    assert packet is not None
    assert len(packet.sources) == 3
    assert [item.path for item in packet.canonical_owners] == ["owner.txt"]
    assert [item.path for item in packet.validation_routes] == ["validate.txt"]
    assert packet.returned_item_count == 5


async def test_context_envelope_serializes_each_fact_once(tmp_path: pathlib.Path) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(objective="explain answer")
        )

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.data is not None
    assert response.data.items
    assert response.rows is None
    assert response.evidence == []


async def test_context_envelope_obeys_the_serialized_byte_contract(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    _frame, template = _materialized_generation(1)
    _stub_materialized_rows(
        monkeypatch,
        (
            _context_row(template, "authority.policies", "owner.txt", {"policy": "owner-policy"}),
            *tuple(
                _context_row(
                    template,
                    "repository.files",
                    f"fat-{index}.txt",
                    {"path": f"fat-{index}.txt", "blob": "x" * 8192},
                )
                for index in range(30)
            ),
        ),
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                max_bytes=65535,
                limit=120,
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert len(response.model_dump_json().encode("utf-8")) <= 65535
    packet = response.data
    assert packet is not None
    assert packet.response_truncated is True
    assert packet.coverage_complete is False
    assert any(gap.code == "response_byte_limit" for gap in packet.gaps)
    assert [item.path for item in packet.canonical_owners] == ["owner.txt"]
    assert packet.returned_item_count < 31


async def test_context_envelope_sheds_configured_references_at_the_byte_limit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    await service.start()
    await service._catalog_indexer.settle()

    _frame, template = _materialized_generation(1)
    _stub_materialized_rows(
        monkeypatch,
        (
            _context_row(template, "authority.policies", "owner.txt", {"policy": "owner-policy"}),
            _context_row(template, "repository.scripts", "validate.txt", {"script": "test"}),
        ),
    )
    try:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                max_bytes=8192,
                references=[
                    soleaux.contracts.context.ContextReference(
                        uri="https://example.test/large",
                        content="x" * 16000,
                    ),
                    soleaux.contracts.context.ContextReference(
                        uri="https://example.test/small",
                        content="ok",
                    ),
                ],
            )
        )
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert len(response.model_dump_json().encode("utf-8")) <= 8192
    packet = response.data
    assert packet is not None
    assert [item.path for item in packet.canonical_owners] == ["owner.txt"]
    assert len(packet.external_references) < 2
    assert any(gap.code == "resource_content_limit" for gap in packet.gaps)
    assert any(gap.code == "response_byte_limit" for gap in packet.gaps)


async def test_context_envelope_fails_closed_when_required_sections_cannot_fit(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "record.txt").write_text("answer = 42\n", encoding="utf-8")
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain answer",
                max_bytes=1,
            )
        )

    assert response.status is soleaux.contracts.results.ResultStatus.ERROR
    assert response.error is not None
    assert response.error.error_type == "context_response_too_large"
