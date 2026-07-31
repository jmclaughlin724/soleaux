"""Catalog-indexer publication identity and lifecycle-settlement contracts."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import pathlib
import threading
from typing import Never, cast

import pytest
from _assertions import raises_with_message

from soleaux.analysis.frame import AnalysisFrameBuilder, FrameBuild
from soleaux.catalog.generation import CatalogGeneration, CatalogGenerationBuilder
from soleaux.catalog.indexer import CatalogIndexer, CatalogPublicationProfile
from soleaux.catalog.store import (
    CatalogLifecycleState,
    CatalogStore,
    CatalogStoreError,
    MaterializedPublication,
    PreparedMaterializedPublication,
)
from soleaux.contracts.config import (
    CatalogConfig,
    CatalogMode,
    ResolvedConfig,
    config_digest,
    resolved_config_bytes,
)
from soleaux.contracts.coverage import Coverage, FrameStatus, RowFileByteDepthLimits
from soleaux.contracts.evidence import (
    Authority,
    Evidence,
    EvidenceKind,
    PositionRange,
    ResolutionStatus,
)
from soleaux.contracts.frame import AnalysisFrame, FactRow
from soleaux.contracts.repository import content_digest
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.snapshot import RepositorySnapshot
from soleaux.contracts.tables import (
    CATALOG_BY_NAME,
    SYNTAX_ONLY_MATERIALIZED_TABLES,
    Producer,
)
from soleaux.contracts.workspace import AllowedWorkspaceSet, WorkspaceRoot
from soleaux.structural.snapshot import SnapshotBundle
from soleaux.structural.supervisor import WorkerUnavailableError
from soleaux.tables.planner import TablePlanner


class _RecordingStore:
    def __init__(
        self,
        *,
        mode: CatalogMode = CatalogMode.MEMORY,
        persisted: MaterializedPublication | None = None,
    ) -> None:
        self.mode = mode
        self.persisted = persisted
        self.building_calls = 0
        self.failures: list[str] = []
        self.publications: list[tuple[int, FrameStatus]] = []
        self.enrichment_states: list[bool] = []
        self.settled = asyncio.Event()
        self.prepare_started: threading.Event | None = None
        self.allow_prepare: threading.Event | None = None

    def open(self) -> None:
        pass

    def mark_building(self, _workspace_id: str) -> None:
        self.building_calls += 1

    def mark_failure(self, _workspace_id: str, error: str) -> None:
        self.failures.append(error)

    def materialized_publication(
        self,
        _workspace_id: str,
    ) -> MaterializedPublication | None:
        return self.persisted

    def publish_materialized(
        self,
        frame: AnalysisFrame,
        *,
        generation: int,
        source_fingerprint: str,
        rows: tuple[FactRow, ...],
        kinds: dict[str, str],
        relationships: tuple[tuple[str, str, str], ...],
        retained_generations: int,
        enrichment_settled: bool = True,
        attempted_tables: tuple[str, ...] = (),
    ) -> None:
        _ = (
            source_fingerprint,
            rows,
            kinds,
            relationships,
            retained_generations,
            attempted_tables,
        )
        self.publications.append((generation, frame.coverage.status))
        self.enrichment_states.append(enrichment_settled)
        if enrichment_settled:
            self.settled.set()

    def prepare_materialized(
        self,
        frame: AnalysisFrame,
        *,
        generation: int,
        source_fingerprint: str,
        rows: tuple[FactRow, ...],
        kinds: dict[str, str],
        relationships: tuple[tuple[str, str, str], ...],
        enrichment_settled: bool = True,
        attempted_tables: tuple[str, ...] = (),
    ) -> PreparedMaterializedPublication:
        _ = rows, kinds, relationships
        if self.prepare_started is not None:
            self.prepare_started.set()
        if self.allow_prepare is not None and not self.allow_prepare.wait(timeout=1):
            raise RuntimeError("timed out waiting to release materialized preparation")
        return PreparedMaterializedPublication(
            workspace_id=frame.workspace_id,
            generation=generation,
            snapshot_id=frame.snapshot_id,
            source_fingerprint=source_fingerprint,
            semantic_mode=frame.semantic_mode,
            coverage_json=frame.coverage.model_dump_json(),
            enrichment_settled=enrichment_settled,
            warnings_json="[]",
            context_rows=(),
            seed_rows=(),
            ownership_rows=(),
            fts_rows=(),
            relationship_rows=(),
            published_tables_json="[]",
            attempted_tables_json="[]",
        )

    def publish_prepared_materialized(
        self,
        prepared: PreparedMaterializedPublication,
        *,
        retained_generations: int,
    ) -> None:
        _ = retained_generations
        coverage = Coverage.model_validate_json(prepared.coverage_json)
        self.publications.append((prepared.generation, coverage.status))
        self.enrichment_states.append(prepared.enrichment_settled)
        if prepared.enrichment_settled:
            self.settled.set()


class _RecoveringFrames:
    def __init__(
        self,
        *,
        bundle: SnapshotBundle,
        first: CatalogGeneration,
        recovered: CatalogGeneration,
        frame: AnalysisFrame,
        store: _RecordingStore,
        advance_at_catalog_call: int = 4,
        build_started: asyncio.Event | None = None,
        allow_build: asyncio.Event | None = None,
    ) -> None:
        self._bundle = bundle
        self._first = first
        self._recovered = recovered
        self._frame = frame
        self._store = store
        self._advance_at_catalog_call = advance_at_catalog_call
        self._build_started = build_started
        self._allow_build = allow_build
        self.catalog_calls = 0
        self.base_catalog_calls = 0
        self.base_build_calls = 0
        self.full_build_calls = 0
        self.build_calls = 0
        self.build_generations: list[int] = []
        self.build_row_limits: list[int] = []
        self.restored_generations: list[int] = []
        self.worker_failures_remaining = 0
        self.structural_enrichment_settled = True

    def ensure_catalog_store(self, _workspace: WorkspaceRoot) -> _RecordingStore:
        return self._store

    async def catalog_bundle(
        self,
        _workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        _ = validate
        self.catalog_calls += 1
        generation = (
            self._recovered if self.catalog_calls >= self._advance_at_catalog_call else self._first
        )
        return generation, self._bundle

    async def base_catalog_bundle(
        self,
        _workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        _ = validate
        self.base_catalog_calls += 1
        return self._first, self._bundle

    async def bootstrap_catalog_bundle(
        self,
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        return await self.base_catalog_bundle(workspace, validate=validate)

    def mark_materialized_generation_restored(self, generation: CatalogGeneration) -> None:
        self.restored_generations.append(generation.number)

    def structural_catalog_enrichment_settled(
        self,
        _generation: CatalogGeneration,
    ) -> bool:
        return self.structural_enrichment_settled

    async def build(self, workspace: WorkspaceRoot, **kwargs: object) -> FrameBuild:
        self.build_calls += 1
        self.build_row_limits.append(cast(int, kwargs["row_limit"]))
        producer_scope = kwargs.get("producer_scope")
        is_base_build = producer_scope is not None
        if is_base_build:
            self.base_build_calls += 1
        else:
            self.full_build_calls += 1
        if not is_base_build and self.worker_failures_remaining:
            self.worker_failures_remaining -= 1
            raise WorkerUnavailableError("structural worker unavailable")
        if not is_base_build and self._build_started is not None:
            self._build_started.set()
        if not is_base_build and self._allow_build is not None:
            await self._allow_build.wait()
        if is_base_build:
            generation, bundle = await self.base_catalog_bundle(workspace)
            scope = cast(frozenset[Producer], producer_scope)
            planned = TablePlanner().plan(
                include_tables=cast(tuple[str, ...], kwargs["include_tables"]),
                exclude_tables=(),
            )
            tables = {
                table_name: self._frame.tables.get(table_name, ())
                for table_name in planned.execution_order
                if CATALOG_BY_NAME[table_name].producer in scope
            }
            build_frame = self._frame.model_copy(
                update={
                    "coverage": self._frame.coverage.model_copy(
                        update={"status": FrameStatus.PARTIAL}
                    ),
                    "tables": tables,
                }
            )
        else:
            generation, bundle = await self.catalog_bundle(workspace)
            build_frame = self._frame
        self.build_generations.append(generation.number)
        return FrameBuild(
            frame=build_frame,
            bundle=bundle,
            catalog_generation=generation,
        )


def _catalog_fixture(
    tmp_path: pathlib.Path,
) -> tuple[
    AllowedWorkspaceSet,
    SnapshotBundle,
    CatalogGeneration,
    CatalogGeneration,
    AnalysisFrame,
]:
    root = tmp_path
    workspace_id = "main"
    fingerprint = content_digest(b"")
    bundle = SnapshotBundle(
        snapshot=RepositorySnapshot(
            snapshot_id=f"{workspace_id}:{fingerprint[:16]}",
            workspace_id=workspace_id,
            root=str(root),
            created_at=datetime.datetime.now(datetime.UTC),
            files=(),
            source_fingerprint=fingerprint,
        ),
        contents={},
        notes=(),
    )
    generation_builder = CatalogGenerationBuilder()
    first = generation_builder.build(bundle, generation=1)
    recovered = generation_builder.build(bundle, generation=2)
    frame = AnalysisFrame(
        snapshot_id=bundle.snapshot.snapshot_id,
        workspace_id=workspace_id,
        semantic_mode=SemanticMode.SYNTAX_ONLY,
        coverage=Coverage(
            status=FrameStatus.COMPLETE,
            eligible_files=0,
            examined_files=0,
            parse_failures=0,
            candidate_count=0,
            resolution_attempts=0,
            resolved_count=0,
            unsupported_count=0,
            failed_count=0,
            deadline=datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1),
            row_file_byte_depth_limits=RowFileByteDepthLimits(
                max_rows=1,
                max_files=1,
                max_bytes=1,
                max_depth=1,
            ),
            elapsed_ms=0.0,
        ),
    )
    workspaces = AllowedWorkspaceSet.from_launch(
        [(workspace_id, str(root))],
        config_digest="d" * 64,
    )
    return workspaces, bundle, first, recovered, frame


def _frame_with_file_rows(
    frame: AnalysisFrame,
    bundle: SnapshotBundle,
    *,
    row_count: int,
) -> AnalysisFrame:
    source_hash = content_digest(b"")
    rows = tuple(
        FactRow(
            table="repository.files",
            data={"path": f"record-{index:04}.txt"},
            evidence=Evidence(
                evidence_id=f"file-{index}",
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                provider="catalog-indexer-test",
                provider_version="1",
                authority=Authority.SOURCE,
                snapshot_id=bundle.snapshot.snapshot_id,
                path=f"record-{index:04}.txt",
                range=PositionRange(
                    start_line=1,
                    start_column=1,
                    end_line=1,
                    end_column=1,
                ),
                source_hash=source_hash,
                source_fingerprint=bundle.snapshot.source_fingerprint,
                confidence=1.0,
            ),
        )
        for index in range(row_count)
    )
    coverage = frame.coverage.model_copy(
        update={
            "eligible_files": row_count,
            "examined_files": row_count,
            "candidate_count": row_count,
            "resolution_attempts": row_count,
            "resolved_count": row_count,
            "row_file_byte_depth_limits": RowFileByteDepthLimits(
                max_rows=row_count,
                max_files=row_count,
                max_bytes=row_count,
                max_depth=1,
            ),
        }
    )
    return frame.model_copy(
        update={
            "coverage": coverage,
            "tables": {"repository.files": rows},
        }
    )


def _persisted_publication(
    generation: CatalogGeneration,
    frame: AnalysisFrame,
    *,
    status: FrameStatus = FrameStatus.COMPLETE,
    enrichment_settled: bool = True,
    published_tables: tuple[str, ...] = SYNTAX_ONLY_MATERIALIZED_TABLES,
    attempted_tables: tuple[str, ...] = SYNTAX_ONLY_MATERIALIZED_TABLES,
) -> MaterializedPublication:
    return MaterializedPublication(
        generation=generation.number,
        publication_revision=1,
        snapshot_id=generation.snapshot_id,
        source_fingerprint=generation.source_fingerprint,
        state=CatalogLifecycleState.READY,
        semantic_mode=SemanticMode.SYNTAX_ONLY,
        coverage=frame.coverage.model_copy(update={"status": status}),
        enrichment_settled=enrichment_settled,
        row_count=7,
        published_tables=published_tables,
        attempted_tables=attempted_tables,
    )


@pytest.mark.asyncio
async def test_start_publishes_base_before_background_enrichment_completes(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    prepare_started = threading.Event()
    allow_prepare = threading.Event()
    build_started = asyncio.Event()
    allow_build = asyncio.Event()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=2,
        build_started=build_started,
        allow_build=allow_build,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    await indexer.start()
    await build_started.wait()
    store.prepare_started = prepare_started
    store.allow_prepare = allow_prepare
    assert indexer.started is True
    assert store.publications == [(1, FrameStatus.PARTIAL)]
    assert store.enrichment_states == [False]
    assert indexer._publications["main"].enrichment_complete is False
    base = await asyncio.wait_for(
        indexer.wait_for_tables("main", ("repository.files",)),
        timeout=0.1,
    )
    assert base is not None
    assert base.generation == 1
    enriched = asyncio.create_task(indexer.wait_for_tables("main", ("repository.projects",)))
    await asyncio.sleep(0)
    assert enriched.done() is False

    allow_build.set()
    try:
        assert await asyncio.wait_for(
            asyncio.to_thread(prepare_started.wait),
            timeout=1,
        )
        base_during_preparation = await asyncio.wait_for(
            indexer.wait_for_tables("main", ("repository.files",)),
            timeout=0.1,
        )
        assert base_during_preparation is not None
        assert base_during_preparation.generation == 1
        allow_prepare.set()
        await asyncio.wait_for(store.settled.wait(), timeout=1)
        reconciled = await asyncio.wait_for(enriched, timeout=1)
        assert reconciled is not None
        assert reconciled.enrichment_complete is True
        assert indexer.started is True
        assert store.publications == [
            (1, FrameStatus.PARTIAL),
            (2, FrameStatus.COMPLETE),
        ]
        assert store.enrichment_states == [False, True]
        assert frames.build_generations == [1, 2]
        assert indexer._publications["main"].enrichment_complete is True
        assert (
            indexer.published_bundle(
                "main",
                generation=2,
                snapshot_id=frame.snapshot_id,
                source_fingerprint=recovered.source_fingerprint,
            )
            is bundle
        )
    finally:
        allow_prepare.set()
        await indexer.aclose()


@pytest.mark.asyncio
async def test_settle_retries_until_enrichment_completes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    build_started = asyncio.Event()
    allow_build = asyncio.Event()
    allow_build.set()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=2,
        build_started=build_started,
        allow_build=allow_build,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    await indexer.start()
    await build_started.wait()
    base_publication = indexer._publications["main"]
    assert base_publication.enrichment_complete is False

    calls = 0

    async def staged_wait(
        _workspace_id: str,
        _requested_tables: object,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return base_publication
        return dataclasses.replace(base_publication, enrichment_complete=True)

    monkeypatch.setattr(indexer, "wait_for_tables", staged_wait)
    try:
        await asyncio.wait_for(indexer.settle(), timeout=5)
    finally:
        await indexer.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_readiness_waiter_does_not_cancel_the_reconciler(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    build_started = asyncio.Event()
    allow_build = asyncio.Event()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=2,
        build_started=build_started,
        allow_build=allow_build,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    await indexer.start()
    await build_started.wait()
    waiter = asyncio.create_task(indexer.wait_for_tables("main", ("repository.projects",)))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    task = indexer._task
    assert task is not None
    assert task.done() is False
    allow_build.set()
    try:
        await asyncio.wait_for(store.settled.wait(), timeout=1)
        assert indexer._publications["main"].enrichment_complete is True
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_start_restores_exact_settled_disk_publication_without_rebuilding(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    restored_bundle = SnapshotBundle(
        snapshot=bundle.snapshot,
        contents={"restored.py": b"restored = True\n"},
        notes=bundle.notes,
    )
    store = _RecordingStore(
        mode=CatalogMode.DISK,
        persisted=_persisted_publication(
            first,
            frame,
            status=FrameStatus.PARTIAL,
            enrichment_settled=True,
        ),
    )
    frames = _RecoveringFrames(
        bundle=restored_bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )

    await indexer.start()
    try:
        assert indexer.started is True
        assert frames.base_catalog_calls == 1
        assert frames.catalog_calls == 0
        assert frames.build_calls == 0
        assert frames.restored_generations == [first.number]
        assert store.building_calls == 0
        assert store.publications == []
        published_bundle = indexer.published_bundle(
            "main",
            generation=first.number,
            snapshot_id=first.snapshot_id,
            source_fingerprint=first.source_fingerprint,
        )
        assert published_bundle is not None
        assert published_bundle is restored_bundle
        assert published_bundle.contents == {"restored.py": b"restored = True\n"}

        reconciled = await indexer.refresh(workspaces.get(None), force=True)

        assert reconciled is None
        assert frames.catalog_calls == 1
        assert frames.build_calls == 0
        assert store.publications == []
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_start_restores_unsettled_full_disk_base_before_reconciling(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore(mode=CatalogMode.DISK)
    build_started = asyncio.Event()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=100,
        build_started=build_started,
        allow_build=asyncio.Event(),
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    base_tables = indexer._base_plans[CatalogPublicationProfile.FULL].attempted_tables
    store.persisted = _persisted_publication(
        first,
        frame,
        status=FrameStatus.PARTIAL,
        enrichment_settled=False,
        published_tables=(*base_tables, "source.context"),
        attempted_tables=base_tables,
    )

    await indexer.start()
    try:
        await build_started.wait()
        restored = indexer._publications["main"]
        assert restored.enrichment_complete is False
        assert restored.attempted_tables == base_tables
        assert frames.base_catalog_calls == 1
        assert frames.base_build_calls == 0
        assert frames.full_build_calls == 1
        assert store.publications == []
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_reconcile_loop_contains_one_workspace_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    workspaces = AllowedWorkspaceSet.from_launch(
        [("first", str(first_root)), ("second", str(second_root))],
        config_digest="d" * 64,
    )
    _unused, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    reconciled = asyncio.Event()

    async def injected_refresh(
        workspace: WorkspaceRoot,
        *,
        force: bool = False,
    ) -> None:
        _ = force
        if workspace.workspace_id == "first":
            raise RuntimeError("injected reconciliation failure")
        reconciled.set()

    monkeypatch.setattr(indexer, "refresh", injected_refresh)
    indexer._started = True
    indexer._task = asyncio.create_task(indexer._reconcile_loop())
    indexer._wake.set()
    try:
        await asyncio.wait_for(reconciled.wait(), timeout=1)
        assert indexer._task.done() is False
        assert store.failures == ["injected reconciliation failure"]
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_authority_restore_rebuilds_when_required_published_closure_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore(mode=CatalogMode.DISK)
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=100,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
        authority_requested_tables=(
            "authority.policies",
            "authority.bindings",
            "authority.conflicts",
        ),
    )
    closure = indexer.attempted_tables
    frames._frame = frame.model_copy(
        update={"tables": {table: () for table in closure}},
    )
    store.persisted = _persisted_publication(
        first,
        frame,
        published_tables=closure[:-1],
        attempted_tables=closure,
    )

    await indexer.start()
    try:
        await indexer.settle()
        assert frames.build_calls == 1
        assert store.publications == [(first.number, FrameStatus.COMPLETE)]
        assert indexer._publications["main"].enrichment_complete is True
    finally:
        await indexer.aclose()


def test_authority_settlement_requires_every_published_closure_table(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
        authority_requested_tables=(
            "authority.policies",
            "authority.bindings",
            "authority.conflicts",
        ),
    )
    plan = indexer._plans[CatalogPublicationProfile.AUTHORITY]
    incomplete = FrameBuild(
        frame=frame.model_copy(
            update={"tables": {table: () for table in plan.required_published_tables[:-1]}}
        ),
        bundle=bundle,
        catalog_generation=first,
    )
    complete = FrameBuild(
        frame=frame.model_copy(
            update={"tables": {table: () for table in plan.required_published_tables}}
        ),
        bundle=bundle,
        catalog_generation=first,
    )

    assert indexer._enrichment_settled(incomplete, plan=plan) is False
    assert indexer._enrichment_settled(complete, plan=plan) is True
    frames.structural_enrichment_settled = False
    assert indexer._enrichment_settled(complete, plan=plan) is True


@pytest.mark.asyncio
async def test_real_unconfigured_disk_restart_reuses_attempted_lifecycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("value = 1\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    config_content = resolved_config_bytes(config)
    digest = config_digest(config_content)
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=digest,
    )

    first_frames = AnalysisFrameBuilder(
        config=config,
        config_content_digest=digest,
    )
    first_indexer = CatalogIndexer(
        workspaces,
        first_frames,
        retained_generations=2,
    )
    await first_indexer.start()
    await first_indexer.settle()
    first_publication = first_indexer._publications["main"]
    await first_indexer.aclose()
    await first_frames.aclose()

    restored_frames = AnalysisFrameBuilder(
        config=config,
        config_content_digest=digest,
    )
    restored_indexer = CatalogIndexer(
        workspaces,
        restored_frames,
        retained_generations=2,
    )
    try:
        await restored_indexer.start()

        restored = restored_indexer._publications["main"]
        store = restored_frames.existing_catalog_store("main")
        assert store is not None
        persisted = store.materialized_publication("main")
        assert persisted is not None
        assert "coverage" not in persisted.published_tables
        assert frozenset(SYNTAX_ONLY_MATERIALIZED_TABLES).issubset(persisted.attempted_tables)
        assert restored == first_publication
        assert restored_frames.catalog_status("main")["loaded_from_sqlite"] is True
        assert restored_frames.structural_worker_started is False
        assert restored_frames.structural_completed_jobs == 0
        bundle = restored_indexer.published_bundle(
            "main",
            generation=restored.generation,
            snapshot_id=restored.snapshot_id,
            source_fingerprint=restored.source_fingerprint,
        )
        assert bundle is not None
        assert bundle.contents["main.py"] == b"value = 1\n"
    finally:
        await restored_indexer.aclose()
        await restored_frames.aclose()


@pytest.mark.asyncio
async def test_interrupted_disk_reconciliation_restores_the_active_base(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("value = 1\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    digest = config_digest(resolved_config_bytes(config))
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=digest,
    )
    reconciliation_started = asyncio.Event()
    never_release = asyncio.Event()

    async def blocked_reconciliation(
        *_args: object,
        **_kwargs: object,
    ) -> Never:
        reconciliation_started.set()
        await never_release.wait()
        raise AssertionError("blocked reconciliation unexpectedly resumed")

    first_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)

    async def unchanged_catalog_bundle(
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        return await first_frames.base_catalog_bundle(workspace, validate=validate)

    def structural_enrichment_settled(_generation: CatalogGeneration) -> bool:
        return True

    monkeypatch.setattr(first_frames, "catalog_bundle", unchanged_catalog_bundle)
    monkeypatch.setattr(
        first_frames,
        "structural_catalog_enrichment_settled",
        structural_enrichment_settled,
    )
    first_indexer = CatalogIndexer(workspaces, first_frames, retained_generations=2)
    monkeypatch.setattr(first_indexer, "_complete_frame", blocked_reconciliation)
    await first_indexer.start()
    await reconciliation_started.wait()
    first_publication = first_indexer._publications["main"]
    first_store = first_frames.existing_catalog_store("main")
    assert first_store is not None
    interrupted = first_store.materialized_publication("main")
    assert interrupted is not None
    assert interrupted.state is CatalogLifecycleState.RECONCILING
    assert interrupted.enrichment_settled is False
    assert interrupted.published_tables == first_publication.published_tables
    await first_indexer.aclose()
    await first_frames.aclose()

    restored_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)
    restored_indexer = CatalogIndexer(workspaces, restored_frames, retained_generations=2)
    try:
        await restored_indexer.start()

        restored = restored_indexer._publications["main"]
        restored_store = restored_frames.existing_catalog_store("main")
        assert restored_store is not None
        persisted = restored_store.materialized_publication("main")
        assert persisted is not None
        assert persisted.publication_revision == interrupted.publication_revision
        assert restored.generation == first_publication.generation
        assert restored.snapshot_id == first_publication.snapshot_id
        assert restored.source_fingerprint == first_publication.source_fingerprint
        assert restored.row_count == first_publication.row_count
        assert restored.attempted_tables == first_publication.attempted_tables
        assert restored.published_tables == first_publication.published_tables
        assert restored_frames.catalog_status("main")["loaded_from_sqlite"] is True
        assert restored_frames.structural_worker_started is False
    finally:
        await restored_indexer.aclose()
        await restored_frames.aclose()


@pytest.mark.asyncio
async def test_changed_disk_restart_selects_exact_content_addressed_generation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    changed_path = workspace_root / "changed.py"
    stable_path = workspace_root / "stable.py"
    changed_path.write_text("changed = 1\n", encoding="utf-8")
    stable_path.write_text("stable = 1\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    digest = config_digest(resolved_config_bytes(config))
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=digest,
    )

    first_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)
    first_indexer = CatalogIndexer(workspaces, first_frames, retained_generations=2)
    await first_indexer.start()
    await first_indexer.settle()
    first_publication = first_indexer._publications["main"]
    await first_indexer.aclose()
    await first_frames.aclose()

    changed_path.write_text("changed = 2\n", encoding="utf-8")
    changed_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)
    changed_indexer = CatalogIndexer(workspaces, changed_frames, retained_generations=2)
    try:
        await changed_indexer.start()

        bootstrap = changed_indexer._publications["main"]
        assert bootstrap.enrichment_complete is False
        assert bootstrap.generation == 1
        assert bootstrap.source_fingerprint != first_publication.source_fingerprint
        assert "main" not in changed_frames._catalog_incremental_bases
        assert "main" not in changed_frames._structural_extracted
        store = changed_frames.existing_catalog_store("main")
        assert store is not None
        assert store.path is not None
        assert store.path.stem == bootstrap.source_fingerprint
        assert changed_frames.catalog_status("main")["loaded_from_sqlite"] is False
    finally:
        await changed_indexer.aclose()
        await changed_frames.aclose()


@pytest.mark.asyncio
async def test_changed_disk_restart_does_not_hydrate_cross_fingerprint_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    changed_path = workspace_root / "changed.py"
    stable_python = workspace_root / "stable.py"
    stable_postgresql = workspace_root / "schema.sql"
    changed_path.write_text("changed = 1\n", encoding="utf-8")
    stable_python.write_text("stable = 1\n", encoding="utf-8")
    stable_postgresql.write_text("CREATE TABLE app.stable (id integer);\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    digest = config_digest(resolved_config_bytes(config))
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=digest,
    )
    workspace = workspaces.get(None)

    first_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)
    first_indexer = CatalogIndexer(workspaces, first_frames, retained_generations=2)
    await first_indexer.start()
    await first_indexer.settle()
    first_source_fingerprint = first_indexer._publications["main"].source_fingerprint
    await first_indexer.aclose()
    await first_frames.aclose()

    changed_path.write_text("changed = 2\n", encoding="utf-8")
    changed_frames = AnalysisFrameBuilder(config=config, config_content_digest=digest)
    try:
        generation, _bundle = await changed_frames.bootstrap_catalog_bundle(
            workspace,
            validate=True,
        )

        assert generation.source_fingerprint != first_source_fingerprint
        assert changed_frames._structural_extracted.get("main", {}) == {}
    finally:
        await changed_frames.aclose()


@pytest.mark.asyncio
async def test_explicit_disk_bootstrap_fails_closed_on_configuration_mismatch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "main.py").write_text("value = 1\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    first_digest = "a" * 64
    second_digest = "b" * 64
    first_workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=first_digest,
    )
    first_frames = AnalysisFrameBuilder(
        config=config,
        config_content_digest=first_digest,
    )
    first_indexer = CatalogIndexer(first_workspaces, first_frames, retained_generations=2)
    await first_indexer.start()
    await first_indexer.settle()
    first_generation = first_frames._catalog_generations["main"]
    await first_indexer.aclose()
    await first_frames.aclose()

    second_workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=second_digest,
    )
    second_frames = AnalysisFrameBuilder(
        config=config,
        config_content_digest=second_digest,
    )
    try:
        with raises_with_message(CatalogStoreError, "configuration identity"):
            await second_frames.bootstrap_catalog_bundle(
                second_workspaces.get(None),
                validate=True,
            )
    finally:
        await second_frames.aclose()

    store = CatalogStore(
        workspace_root,
        mode=CatalogMode.DISK,
        config_digest=first_digest,
    )
    try:
        restored = store.load()
        assert restored is not None
        assert restored.source_fingerprint == first_generation.source_fingerprint
    finally:
        store.close()


@pytest.mark.asyncio
async def test_changed_source_does_not_open_an_unrelated_configuration_cache(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_path = workspace_root / "main.py"
    source_path.write_text("value = 1\n", encoding="utf-8")
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        return cache_root / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )
    first_digest = "a" * 64
    first_workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=first_digest,
    )
    first_frames = AnalysisFrameBuilder(config=config, config_content_digest=first_digest)
    first_indexer = CatalogIndexer(first_workspaces, first_frames, retained_generations=2)
    await first_indexer.start()
    await first_indexer.settle()
    first_source_fingerprint = first_indexer._publications["main"].source_fingerprint
    await first_indexer.aclose()
    await first_frames.aclose()

    source_path.write_text("value = 2\n", encoding="utf-8")
    second_digest = "b" * 64
    second_workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(workspace_root))],
        config_digest=second_digest,
    )
    second_frames = AnalysisFrameBuilder(config=config, config_content_digest=second_digest)
    try:
        generation, bundle = await second_frames.base_catalog_bundle(
            second_workspaces.get(None),
            validate=True,
        )

        assert generation.source_fingerprint == bundle.snapshot.source_fingerprint
        assert generation.source_fingerprint != first_source_fingerprint
        store = second_frames.existing_catalog_store("main")
        assert store is not None
        assert store.path is not None
        assert store.path.stem == generation.source_fingerprint
    finally:
        await second_frames.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["generation", "unsettled"])
async def test_start_rebuilds_mismatched_or_unsettled_disk_publication(
    tmp_path: pathlib.Path,
    mismatch: str,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    persisted = (
        _persisted_publication(recovered, frame)
        if mismatch == "generation"
        else _persisted_publication(
            first,
            frame,
            status=FrameStatus.PARTIAL,
            enrichment_settled=False,
            published_tables=(),
        )
    )
    store = _RecordingStore(
        mode=CatalogMode.DISK,
        persisted=persisted,
    )
    build_started = asyncio.Event()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=100,
        build_started=build_started,
        allow_build=asyncio.Event(),
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )

    await indexer.start()
    try:
        await build_started.wait()
        assert indexer.started is True
        assert frames.base_catalog_calls == 2
        assert frames.base_build_calls == 1
        assert frames.full_build_calls == 1
        assert store.publications == [(1, FrameStatus.PARTIAL)]
        assert store.enrichment_states == [False]
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_start_retries_degraded_publication_before_settling(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    partial_frame = frame.model_copy(
        update={
            "coverage": frame.coverage.model_copy(
                update={
                    "status": FrameStatus.PARTIAL,
                    "omitted_reasons": ("catalog table enrichment degraded: worker failed",),
                }
            )
        }
    )
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=partial_frame,
        store=store,
        advance_at_catalog_call=100,
    )
    frames.worker_failures_remaining = 1
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )

    await indexer.start()
    try:
        while frames.build_calls < 2:
            await asyncio.sleep(0)
        initial = indexer._publications["main"]
        assert indexer.started is True
        assert initial.enrichment_complete is False
        frames._frame = frame

        reconciled = await indexer.refresh(workspaces.get(None), force=True)

        assert reconciled is not None
        assert reconciled.enrichment_complete is True
        assert frames.build_calls >= 3
        assert store.publications == [
            (first.number, FrameStatus.PARTIAL),
            (first.number, FrameStatus.COMPLETE),
        ]
        assert store.enrichment_states == [False, True]
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_off_mode_starts_without_publishing(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore(mode=CatalogMode.OFF)
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )

    await indexer.start()
    try:
        assert indexer.started is True
        assert frames.base_catalog_calls == 0
        assert frames.catalog_calls == 0
        assert frames.build_calls == 0
        assert store.publications == []
    finally:
        await indexer.aclose()


@pytest.mark.asyncio
async def test_same_enriched_generation_is_not_rematerialized(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=100,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    workspace = workspaces.get(None)

    initial = await indexer.refresh(workspace)
    settled = await indexer.refresh(workspace)
    still_settled = await indexer.refresh(workspace)

    assert initial is not None
    assert initial.generation == 1
    assert initial.enrichment_complete is True
    assert settled is None
    assert still_settled is None
    assert first.source_fingerprint == recovered.source_fingerprint
    assert store.publications == [(1, FrameStatus.COMPLETE)]
    assert store.building_calls == 1
    assert store.failures == []
    assert frames.build_calls == 1
    assert frames.build_generations == [1]
    assert frames.catalog_calls == 4


@pytest.mark.asyncio
async def test_large_materialization_uses_one_bounded_frame_build(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=_frame_with_file_rows(frame, bundle, row_count=1001),
        store=store,
        advance_at_catalog_call=100,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )

    publication = await indexer.refresh(workspaces.get(None))

    assert publication is not None
    assert publication.row_count == 1001
    assert frames.build_calls == 1
    assert frames.build_row_limits == [2_147_483_647]


@pytest.mark.asyncio
async def test_changed_candidate_is_not_published_before_stable_retry(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    changed_bundle = SnapshotBundle(
        snapshot=bundle.snapshot.model_copy(update={"changed_during_analysis": True}),
        contents=bundle.contents,
        notes=bundle.notes,
    )
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=changed_bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=100,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    workspace = workspaces.get(None)

    changed = await indexer.refresh(workspace)
    frames._bundle = bundle
    settled = await indexer.refresh(workspace)

    assert changed is None
    assert settled is not None
    assert settled.enrichment_complete is True
    assert store.enrichment_states == [True]


@pytest.mark.asyncio
async def test_real_partial_materialization_settles_and_reconcile_is_noop(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    )
    frames = AnalysisFrameBuilder()
    indexer = CatalogIndexer(workspaces, frames, retained_generations=2)
    workspace = workspaces.get(None)

    try:
        published = await indexer.refresh(workspace, force=True)
        reconciled = await indexer.refresh(workspace, force=True)

        assert published is not None
        store = frames.existing_catalog_store("main")
        assert store is not None
        materialized = store.read_materialized("main", limit=1)
        assert materialized.frame.coverage.status is FrameStatus.PARTIAL
        assert materialized.frame.coverage.examined_files == 1
        assert published.enrichment_complete is True
        assert reconciled is None
        assert indexer._publications["main"] == published
    finally:
        await indexer.aclose()
        await frames.aclose()


@pytest.mark.asyncio
async def test_real_malformed_structural_source_is_partial_but_settled(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "broken.ts").write_bytes(
        b"export function good(): number { return 1; }\n"
        b"export function broken( { const value = ; }\n"
    )
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    )
    frames = AnalysisFrameBuilder()
    indexer = CatalogIndexer(workspaces, frames, retained_generations=2)
    workspace = workspaces.get(None)

    try:
        published = await indexer.refresh(workspace, force=True)
        reconciled = await indexer.refresh(workspace, force=True)

        assert published is not None
        store = frames.existing_catalog_store("main")
        assert store is not None
        materialized = store.read_materialized("main", limit=1)
        assert materialized.frame.coverage.status is FrameStatus.PARTIAL
        assert any(
            "broken.ts: structural error: syntax error" in reason
            for reason in materialized.frame.coverage.omitted_reasons
        )
        assert published.enrichment_complete is True
        assert reconciled is None
    finally:
        await indexer.aclose()
        await frames.aclose()


@pytest.mark.asyncio
async def test_new_enriched_generation_replaces_the_previous_publication_once(
    tmp_path: pathlib.Path,
) -> None:
    workspaces, bundle, first, recovered, frame = _catalog_fixture(tmp_path)
    store = _RecordingStore()
    frames = _RecoveringFrames(
        bundle=bundle,
        first=first,
        recovered=recovered,
        frame=frame,
        store=store,
        advance_at_catalog_call=3,
    )
    indexer = CatalogIndexer(
        workspaces,
        cast(AnalysisFrameBuilder, frames),
        retained_generations=2,
    )
    workspace = workspaces.get(None)

    initial = await indexer.refresh(workspace)
    recovery = await indexer.refresh(workspace)
    settled = await indexer.refresh(workspace)

    assert initial is not None
    assert initial.generation == 1
    assert initial.enrichment_complete is True
    assert recovery is not None
    assert recovery.generation == 2
    assert recovery.enrichment_complete is True
    assert settled is None
    assert first.source_fingerprint == recovered.source_fingerprint
    assert store.publications == [
        (1, FrameStatus.COMPLETE),
        (2, FrameStatus.COMPLETE),
    ]
    assert store.building_calls == 2
    assert store.failures == []
    assert frames.build_calls == 2
    assert frames.build_generations == [1, 2]
    assert frames.catalog_calls == 5
