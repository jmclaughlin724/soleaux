"""Lifecycle-owned publication of immutable SQLite catalog generations."""

from __future__ import annotations

import asyncio
import collections
import collections.abc
import contextlib
import dataclasses
import enum
import time
import typing

import soleaux.analysis.frame
import soleaux.catalog.generation
import soleaux.catalog.store
import soleaux.contracts.config
import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.requests
import soleaux.contracts.tables
import soleaux.contracts.workspace
import soleaux.structural.snapshot
import soleaux.structural.supervisor
import soleaux.tables.evidence
import soleaux.tables.planner

_RECONCILE_SECONDS = 2.0
# Bounded below the test faulthandler timeout so a genuinely stuck reconcile
# fails with the named workspace instead of a bare pytest timeout dump.
_SETTLE_DEADLINE_SECONDS = 45.0
_MATERIALIZATION_ROW_LIMIT = 2_147_483_647
_STARTUP_TABLES = ("repository.files", "repository.chunks")
_BASE_PRODUCERS = frozenset(
    {soleaux.contracts.tables.Producer.SNAPSHOT, soleaux.contracts.tables.Producer.CATALOG}
)
_RESTORABLE_STATES = frozenset(
    {
        soleaux.catalog.store.CatalogLifecycleState.READY,
        soleaux.catalog.store.CatalogLifecycleState.RECONCILING,
        soleaux.catalog.store.CatalogLifecycleState.FAILED,
    }
)
_DEGRADED_PRODUCERS = frozenset(
    {
        soleaux.contracts.tables.Producer.SNAPSHOT,
        soleaux.contracts.tables.Producer.CATALOG,
        soleaux.contracts.tables.Producer.AUTHORITY,
        soleaux.contracts.tables.Producer.IMPORTED,
    }
)
_IDENTITY_SUFFIXES = (
    "_id",
    "_key",
    "_path",
    "_uri",
    "_owner",
    "_consumer",
    "_source",
    "_target",
    "_record",
    "_reference",
)
_IDENTITY_FIELDS = frozenset(
    {
        "path",
        "uri",
        "owner",
        "consumer",
        "source",
        "target",
        "record",
        "reference",
        "dependency",
        "package",
        "project",
        "rule",
        "route",
        "binding",
        "policy",
        "entrypoint",
        "script",
        "config",
        "command",
        "handler",
        "registration",
    }
)


def _is_object_mapping(value: object) -> typing.TypeGuard[collections.abc.Mapping[object, object]]:
    return isinstance(value, collections.abc.Mapping)


def _is_object_sequence(value: object) -> typing.TypeGuard[collections.abc.Sequence[object]]:
    return isinstance(value, collections.abc.Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


class CatalogPublicationProfile(enum.StrEnum):
    """Internal materialization scope for one catalog indexer lifespan."""

    FULL = "full"
    AUTHORITY = "authority"


@dataclasses.dataclass(frozen=True, slots=True)
class _PublicationPlan:
    profile: CatalogPublicationProfile
    requested_tables: tuple[str, ...]
    attempted_tables: tuple[str, ...]
    required_published_tables: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogPublication:
    """One atomically materialized generation and its retry state.

    ``enrichment_complete`` means no lifecycle enrichment is retryable for the
    exact generation; evidence coverage can still be honestly partial.
    """

    workspace_id: str
    generation: int
    snapshot_id: str
    source_fingerprint: str
    row_count: int
    enrichment_complete: bool
    profile: CatalogPublicationProfile
    attempted_tables: tuple[str, ...]
    published_tables: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _PreparedPublication:
    workspace: soleaux.contracts.workspace.WorkspaceRoot
    store: soleaux.catalog.store.CatalogStore
    built: soleaux.analysis.frame.FrameBuild
    enrichment_complete: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _RetainedPublication:
    store: soleaux.catalog.store.CatalogStore
    publication: CatalogPublication
    bundle: soleaux.structural.snapshot.SnapshotBundle
    frame: soleaux.contracts.frame.AnalysisFrame
    rows: tuple[soleaux.contracts.frame.FactRow, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _PreparedBuildPublication:
    generation: soleaux.catalog.generation.CatalogGeneration
    rows: tuple[soleaux.contracts.frame.FactRow, ...]
    published_tables: tuple[str, ...]
    store_publication: soleaux.catalog.store.PreparedMaterializedPublication


class CatalogIndexer:
    """The only runtime owner allowed to build and publish catalog facts."""

    def __init__(
        self,
        workspaces: soleaux.contracts.workspace.AllowedWorkspaceSet,
        frames: soleaux.analysis.frame.AnalysisFrameBuilder,
        *,
        retained_generations: int,
        publication_profile: CatalogPublicationProfile = CatalogPublicationProfile.FULL,
        authority_requested_tables: collections.abc.Sequence[str] = (),
    ) -> None:
        self._workspaces = workspaces
        self._frames = frames
        self._retained_generations = retained_generations
        self._plans = self._publication_plans(
            authority_requested_tables=authority_requested_tables,
        )
        self._base_plans = {
            profile: self._base_publication_plan(profile) for profile in self._plans
        }
        if publication_profile not in self._plans:
            raise ValueError("authority publication requires canonical authority_requested_tables")
        self._publication_profile = publication_profile
        self._publications: dict[str, CatalogPublication] = {}
        self._published_bundles: dict[str, soleaux.structural.snapshot.SnapshotBundle] = {}
        self._start_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._promotion_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._reconciled: dict[str, asyncio.Event] = {}
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def publication_profile(self) -> CatalogPublicationProfile:
        """Return the active profile for newly admitted reads."""
        return self._publication_profile

    @property
    def attempted_tables(self) -> tuple[str, ...]:
        """Return the planner-derived closure for the active profile."""
        return self._plans[self._publication_profile].attempted_tables

    def publication_status(self, workspace_id: str) -> dict[str, object]:
        """Expose the current materialized publication without starting work."""
        publication = self._publications.get(workspace_id)
        return {
            "materialized_generation": (
                publication.generation if publication is not None else None
            ),
            "enrichment_settled": (
                publication.enrichment_complete if publication is not None else False
            ),
            "published_table_count": (
                len(publication.published_tables) if publication is not None else 0
            ),
            "attempted_table_count": (
                len(publication.attempted_tables) if publication is not None else 0
            ),
        }

    async def start(self) -> None:
        """Publish a bounded SQLite generation, then reconcile in the background."""
        async with self._start_lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("catalog indexer is closed")
            enrichment_pending = False
            for workspace_id in self._workspaces.workspace_ids:
                workspace = self._workspaces.get(workspace_id)
                store = self._frames.ensure_catalog_store(workspace)
                if store.mode is soleaux.contracts.config.CatalogMode.OFF:
                    continue
                restored = await self._restore_materialized_publication(
                    workspace,
                    plan=self._plans[self._publication_profile],
                )
                if restored is None:
                    plan = self._plans[self._publication_profile]
                    if plan.profile is CatalogPublicationProfile.AUTHORITY:
                        prepared = await self._prepare_publication(
                            workspace,
                            store,
                            plan=plan,
                        )
                        restored = await self._publish_build(
                            workspace,
                            store,
                            prepared.built,
                            plan=plan,
                            enrichment_complete=prepared.enrichment_complete,
                        )
                    else:
                        restored = await self._publish_base_generation(
                            workspace,
                            plan=plan,
                        )
                publication = self._publications.get(workspace_id)
                if publication is None:
                    raise RuntimeError(
                        f"catalog startup did not publish a readable generation for {workspace_id}"
                    )
                enrichment_pending = enrichment_pending or not publication.enrichment_complete
            self._task = asyncio.create_task(
                self._reconcile_loop(),
                name="soleaux-catalog-indexer",
            )
            self._started = True
            if enrichment_pending:
                self._wake.set()

    async def _restore_materialized_publication(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        plan: _PublicationPlan,
    ) -> CatalogPublication | None:
        async with self._refresh_lock:
            store = self._frames.ensure_catalog_store(workspace)
            if store.mode is not soleaux.contracts.config.CatalogMode.DISK:
                return None
            try:
                generation, bundle = await self._frames.bootstrap_catalog_bundle(
                    workspace, validate=True
                )
                persisted = store.materialized_publication(workspace.workspace_id)
            except soleaux.catalog.store.CatalogStoreError:
                if store.requested_mode is soleaux.contracts.config.CatalogMode.DISK:
                    raise
                return None
            expected_plan = (
                plan
                if persisted is not None and persisted.enrichment_settled
                else self._base_plans[plan.profile]
            )
            if (
                persisted is None
                or persisted.state not in _RESTORABLE_STATES
                or persisted.semantic_mode
                is not soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY
                or persisted.generation != generation.number
                or persisted.snapshot_id != generation.snapshot_id
                or persisted.source_fingerprint != generation.source_fingerprint
                or bundle.snapshot.snapshot_id != generation.snapshot_id
                or bundle.snapshot.source_fingerprint != generation.source_fingerprint
                or frozenset(persisted.attempted_tables)
                != frozenset(expected_plan.attempted_tables)
                or len(persisted.attempted_tables) != len(expected_plan.attempted_tables)
                or (
                    persisted.enrichment_settled
                    and not self._published_closure_matches(
                        persisted.published_tables,
                        plan=expected_plan,
                    )
                )
                or (
                    not persisted.enrichment_settled
                    and not self._base_closure_matches(
                        persisted.published_tables,
                        plan=expected_plan,
                    )
                )
            ):
                return None
            if persisted.enrichment_settled and plan.profile is CatalogPublicationProfile.FULL:
                self._frames.mark_materialized_generation_restored(generation)
            publication = CatalogPublication(
                workspace_id=workspace.workspace_id,
                generation=generation.number,
                snapshot_id=generation.snapshot_id,
                source_fingerprint=generation.source_fingerprint,
                row_count=persisted.row_count,
                enrichment_complete=persisted.enrichment_settled,
                profile=plan.profile,
                attempted_tables=expected_plan.attempted_tables,
                published_tables=persisted.published_tables,
            )
            self._publications[workspace.workspace_id] = publication
            self._published_bundles[workspace.workspace_id] = bundle
            if publication.enrichment_complete:
                self._reconciliation_event(workspace.workspace_id).set()
            return publication

    async def _publish_base_generation(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        plan: _PublicationPlan,
    ) -> CatalogPublication:
        """Publish only bounded generic producers before wire clients connect."""
        store = self._frames.ensure_catalog_store(workspace)
        base_plan = self._base_plans[plan.profile]
        try:
            store.mark_building(workspace.workspace_id)
            built = await self._frames.build(
                workspace,
                include_tables=base_plan.requested_tables,
                exclude_tables=(),
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                row_limit=_MATERIALIZATION_ROW_LIMIT,
                validate=False,
                producer_scope=_BASE_PRODUCERS,
                enrich_catalog=False,
                bootstrap_catalog=True,
            )
            if not self._base_closure_matches(
                tuple(built.frame.tables),
                plan=base_plan,
            ):
                raise RuntimeError(
                    f"catalog base publication is incomplete for {workspace.workspace_id}"
                )
            return await self._publish_build(
                workspace,
                store,
                built,
                plan=base_plan,
                enrichment_complete=False,
            )
        except (
            soleaux.catalog.store.CatalogStoreError,
            OSError,
            RuntimeError,
            ValueError,
            soleaux.structural.supervisor.WorkerJobError,
            soleaux.structural.supervisor.WorkerUnavailableError,
        ) as exc:
            with contextlib.suppress(soleaux.catalog.store.CatalogStoreError):
                store.mark_failure(workspace.workspace_id, str(exc))
            raise RuntimeError(
                f"catalog base publication failed for {workspace.workspace_id}: {exc}"
            ) from exc

    async def refresh(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        force: bool = False,
    ) -> CatalogPublication | None:
        """Reconcile one workspace and publish only a settled generation."""
        return await self._refresh(workspace, force=force)

    async def settle(self) -> None:
        """Wait for one explicit full reconciliation outside request handlers."""
        if not self._started:
            await self.start()
        deadline = time.monotonic() + _SETTLE_DEADLINE_SECONDS
        for workspace_id in self._workspaces.workspace_ids:
            workspace = self._workspaces.get(workspace_id)
            store = self._frames.ensure_catalog_store(workspace)
            if store.mode is soleaux.contracts.config.CatalogMode.OFF:
                continue
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"catalog reconciliation did not settle for {workspace_id}")
                try:
                    publication = await asyncio.wait_for(
                        self.wait_for_tables(
                            workspace_id,
                            self._plans[self._publication_profile].attempted_tables,
                        ),
                        timeout=remaining,
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"catalog reconciliation did not settle for {workspace_id}"
                    ) from exc
                if publication is not None and publication.enrichment_complete:
                    break

    async def wait_for_tables(
        self,
        workspace_id: str,
        requested_tables: collections.abc.Sequence[str],
    ) -> CatalogPublication | None:
        """Wait for at most one owned reconciliation attempt, then return its publication."""
        if not self._started:
            return self._publications.get(workspace_id)
        if self._closed:
            raise RuntimeError("catalog indexer is closed")
        workspace = self._workspaces.get(workspace_id)
        store = self._frames.ensure_catalog_store(workspace)
        if store.mode is soleaux.contracts.config.CatalogMode.OFF:
            return None
        requested = frozenset(requested_tables)
        publication = self._publications.get(workspace_id)
        if self._publication_satisfies(publication, requested):
            return publication

        reconciled = self._reconciliation_event(workspace_id)
        reconciled.clear()
        publication = self._publications.get(workspace_id)
        if self._publication_satisfies(publication, requested):
            return publication
        self._wake.set()
        await reconciled.wait()
        if self._closed:
            raise RuntimeError("catalog indexer is closed")
        return self._publications.get(workspace_id)

    @staticmethod
    def _publication_satisfies(
        publication: CatalogPublication | None,
        requested_tables: frozenset[str],
    ) -> bool:
        return publication is not None and (
            requested_tables.issubset(publication.published_tables)
            or publication.enrichment_complete
        )

    def _reconciliation_event(self, workspace_id: str) -> asyncio.Event:
        event = self._reconciled.get(workspace_id)
        if event is None:
            event = asyncio.Event()
            self._reconciled[workspace_id] = event
        return event

    async def _refresh(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        force: bool,
    ) -> CatalogPublication | None:
        async with self._refresh_lock:
            return await self._refresh_locked(
                workspace,
                force=force,
                plan=self._plans[self._publication_profile],
            )

    async def _refresh_locked(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        force: bool,
        plan: _PublicationPlan,
    ) -> CatalogPublication | None:
        store = self._frames.ensure_catalog_store(workspace)
        if store.mode is soleaux.contracts.config.CatalogMode.OFF:
            return None
        published = self._publications.get(workspace.workspace_id)
        try:
            if published is None:
                store.mark_building(workspace.workspace_id)
            if plan.profile is CatalogPublicationProfile.AUTHORITY:
                generation, bundle = await self._frames.base_catalog_bundle(
                    workspace,
                    validate=force or published is not None,
                )
            else:
                generation, bundle = await self._frames.catalog_bundle(
                    workspace,
                    validate=force or published is not None,
                )
            if (
                published is not None
                and published.profile is plan.profile
                and published.enrichment_complete
                and published.generation == generation.number
                and published.source_fingerprint == generation.source_fingerprint
            ):
                self._published_bundles[workspace.workspace_id] = bundle
                return None
            if (
                plan.profile is CatalogPublicationProfile.FULL
                and not self._frames.structural_catalog_enrichment_settled(generation)
            ):
                self._published_bundles[workspace.workspace_id] = bundle
                return None
            if published is not None:
                store.mark_building(workspace.workspace_id)
            built, enrichment_complete = await self._complete_frame(
                workspace,
                plan=plan,
            )
            if not enrichment_complete:
                return None
            return await self._publish_build(
                workspace,
                store,
                built,
                plan=plan,
                enrichment_complete=enrichment_complete,
            )
        except (
            soleaux.catalog.store.CatalogStoreError,
            OSError,
            RuntimeError,
            ValueError,
            soleaux.structural.supervisor.WorkerJobError,
            soleaux.structural.supervisor.WorkerUnavailableError,
        ) as exc:
            with contextlib.suppress(soleaux.catalog.store.CatalogStoreError):
                store.mark_failure(workspace.workspace_id, str(exc))
            return None

    async def promote_to_full(self) -> None:
        """Atomically switch this indexer to FULL before a non-authority read."""
        async with self._promotion_lock:
            if self.publication_profile is CatalogPublicationProfile.FULL:
                return
            plan = self._plans[CatalogPublicationProfile.FULL]
            async with self._refresh_lock:
                retained: dict[str, _RetainedPublication] = {}
                prepared: list[_PreparedPublication] = []
                for workspace_id in self._workspaces.workspace_ids:
                    workspace = self._workspaces.get(workspace_id)
                    store = self._frames.ensure_catalog_store(workspace)
                    if store.mode is soleaux.contracts.config.CatalogMode.OFF:
                        continue
                    retained[workspace_id] = self._retain_publication(workspace, store)
                for workspace_id in self._workspaces.workspace_ids:
                    workspace = self._workspaces.get(workspace_id)
                    store = self._frames.ensure_catalog_store(workspace)
                    if store.mode is soleaux.contracts.config.CatalogMode.OFF:
                        continue
                    try:
                        prepared.append(
                            await self._prepare_publication(
                                workspace,
                                store,
                                plan=plan,
                            )
                        )
                    except (
                        soleaux.catalog.store.CatalogStoreError,
                        OSError,
                        RuntimeError,
                        ValueError,
                        soleaux.structural.supervisor.WorkerJobError,
                        soleaux.structural.supervisor.WorkerUnavailableError,
                    ) as exc:
                        raise RuntimeError(
                            f"catalog FULL preparation failed for {workspace_id}: {exc}"
                        ) from exc
                touched: list[_RetainedPublication] = []
                try:
                    for candidate in prepared:
                        previous = retained[candidate.workspace.workspace_id]
                        touched.append(previous)
                        await self._publish_build(
                            candidate.workspace,
                            candidate.store,
                            candidate.built,
                            plan=plan,
                            enrichment_complete=candidate.enrichment_complete,
                        )
                except BaseException as exc:
                    rollback_errors: list[str] = []
                    for previous in reversed(touched):
                        try:
                            self._restore_retained_publication(previous)
                        except (
                            soleaux.catalog.store.CatalogStoreError,
                            OSError,
                            RuntimeError,
                            ValueError,
                        ) as rollback_exc:
                            rollback_errors.append(
                                f"{previous.publication.workspace_id}: {rollback_exc}"
                            )
                    if rollback_errors:
                        details = "; ".join(rollback_errors)
                        raise RuntimeError(
                            "catalog FULL publication failed and AUTHORITY rollback "
                            f"was incomplete: {details}"
                        ) from exc
                    raise
                self._publication_profile = CatalogPublicationProfile.FULL

    async def _prepare_publication(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        store: soleaux.catalog.store.CatalogStore,
        *,
        plan: _PublicationPlan,
    ) -> _PreparedPublication:
        if plan.profile is CatalogPublicationProfile.AUTHORITY:
            await self._frames.base_catalog_bundle(workspace, validate=True)
        else:
            await self._frames.catalog_bundle(workspace, validate=True)
        built, enrichment_complete = await self._complete_frame(
            workspace,
            plan=plan,
        )
        self._validated_generation(built)
        if not enrichment_complete:
            raise RuntimeError(
                f"catalog FULL preparation did not settle for {workspace.workspace_id}"
            )
        return _PreparedPublication(
            workspace=workspace,
            store=store,
            built=built,
            enrichment_complete=enrichment_complete,
        )

    def _retain_publication(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        store: soleaux.catalog.store.CatalogStore,
    ) -> _RetainedPublication:
        publication = self._publications.get(workspace.workspace_id)
        bundle = self._published_bundles.get(workspace.workspace_id)
        if (
            publication is None
            or bundle is None
            or publication.profile is not self._publication_profile
            or bundle.snapshot.snapshot_id != publication.snapshot_id
            or bundle.snapshot.source_fingerprint != publication.source_fingerprint
        ):
            raise RuntimeError(
                f"catalog promotion has no retained {self._publication_profile.value} "
                f"publication for {workspace.workspace_id}"
            )
        read = store.read_materialized(
            workspace.workspace_id,
            limit=_MATERIALIZATION_ROW_LIMIT,
        )
        rows = tuple(item.row for item in read.rows)
        if (
            read.generation != publication.generation
            or read.snapshot_id != publication.snapshot_id
            or read.source_fingerprint != publication.source_fingerprint
            or read.has_more
            or read.total_rows != len(rows)
            or publication.row_count != len(rows)
        ):
            raise RuntimeError(
                f"catalog promotion cannot retain the active publication for "
                f"{workspace.workspace_id}"
            )
        return _RetainedPublication(
            store=store,
            publication=publication,
            bundle=bundle,
            frame=read.frame,
            rows=rows,
        )

    def _restore_retained_publication(
        self,
        retained: _RetainedPublication,
    ) -> None:
        publication = retained.publication
        rows = retained.rows
        retained.store.publish_materialized(
            retained.frame,
            generation=publication.generation,
            source_fingerprint=publication.source_fingerprint,
            rows=rows,
            kinds={row.evidence.evidence_id: self._kind_for_row(row) for row in rows},
            relationships=self._relationships(rows),
            retained_generations=self._retained_generations,
            enrichment_settled=publication.enrichment_complete,
            attempted_tables=publication.attempted_tables,
        )
        self._publications[publication.workspace_id] = publication
        self._published_bundles[publication.workspace_id] = retained.bundle

    async def _publish_build(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        store: soleaux.catalog.store.CatalogStore,
        built: soleaux.analysis.frame.FrameBuild,
        *,
        plan: _PublicationPlan,
        enrichment_complete: bool,
    ) -> CatalogPublication:
        store.open()
        prepared = await asyncio.to_thread(
            self._prepare_build_publication,
            store,
            built,
            plan=plan,
            enrichment_complete=enrichment_complete,
        )
        store.publish_prepared_materialized(
            prepared.store_publication,
            retained_generations=self._retained_generations,
        )
        generation = prepared.generation
        rows = prepared.rows
        publication = CatalogPublication(
            workspace_id=workspace.workspace_id,
            generation=generation.number,
            snapshot_id=built.frame.snapshot_id,
            source_fingerprint=generation.source_fingerprint,
            row_count=len(rows),
            enrichment_complete=enrichment_complete,
            profile=plan.profile,
            attempted_tables=plan.attempted_tables,
            published_tables=prepared.published_tables,
        )
        self._publications[workspace.workspace_id] = publication
        self._published_bundles[workspace.workspace_id] = built.bundle
        if enrichment_complete:
            self._reconciliation_event(workspace.workspace_id).set()
        return publication

    def _prepare_build_publication(
        self,
        store: soleaux.catalog.store.CatalogStore,
        built: soleaux.analysis.frame.FrameBuild,
        *,
        plan: _PublicationPlan,
        enrichment_complete: bool,
    ) -> _PreparedBuildPublication:
        """Prepare CPU-heavy materialization away from the MCP event loop."""
        generation = self._validated_generation(built)
        bundle = built.bundle
        rows = self._materialized_rows(
            generation,
            bundle,
            built.frame,
            include_source_context=plan.profile is CatalogPublicationProfile.FULL,
        )
        published_tables = tuple(sorted({*built.frame.tables, *(row.table for row in rows)}))
        kinds = {row.evidence.evidence_id: self._kind_for_row(row) for row in rows}
        relationships = self._relationships(rows)
        store_publication = store.prepare_materialized(
            built.frame,
            generation=generation.number,
            source_fingerprint=generation.source_fingerprint,
            rows=rows,
            kinds=kinds,
            relationships=relationships,
            enrichment_settled=enrichment_complete,
            attempted_tables=plan.attempted_tables,
        )
        return _PreparedBuildPublication(
            generation=generation,
            rows=rows,
            published_tables=published_tables,
            store_publication=store_publication,
        )

    async def _complete_frame(
        self,
        workspace: soleaux.contracts.workspace.WorkspaceRoot,
        *,
        plan: _PublicationPlan,
    ) -> tuple[soleaux.analysis.frame.FrameBuild, bool]:
        try:
            built = await self._frames.build(
                workspace,
                include_tables=plan.requested_tables,
                exclude_tables=(),
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                row_limit=_MATERIALIZATION_ROW_LIMIT,
                validate=False,
            )
            return built, self._enrichment_settled(built, plan=plan)
        except (
            soleaux.structural.supervisor.WorkerJobError,
            soleaux.structural.supervisor.WorkerUnavailableError,
        ) as exc:
            fallback = await self._frames.build(
                workspace,
                include_tables=plan.requested_tables,
                exclude_tables=(),
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                row_limit=_MATERIALIZATION_ROW_LIMIT,
                validate=False,
                producer_scope=_DEGRADED_PRODUCERS,
            )
            reason = f"catalog table enrichment degraded: {' '.join(str(exc).split())[:512]}"
            coverage = fallback.frame.coverage.model_copy(
                update={
                    "status": soleaux.contracts.coverage.FrameStatus.PARTIAL,
                    "omitted_reasons": tuple(
                        dict.fromkeys((*fallback.frame.coverage.omitted_reasons, reason))
                    ),
                }
            )
            return (
                soleaux.analysis.frame.FrameBuild(
                    frame=fallback.frame.model_copy(
                        update={
                            "coverage": coverage,
                            "warnings": tuple(dict.fromkeys((*fallback.frame.warnings, reason))),
                        }
                    ),
                    bundle=fallback.bundle,
                    catalog_generation=fallback.catalog_generation,
                ),
                False,
            )

    def _enrichment_settled(
        self,
        built: soleaux.analysis.frame.FrameBuild,
        *,
        plan: _PublicationPlan,
    ) -> bool:
        generation = built.catalog_generation
        return (
            generation is not None
            and not built.bundle.snapshot.changed_during_analysis
            and (
                plan.profile is CatalogPublicationProfile.AUTHORITY
                or self._frames.structural_catalog_enrichment_settled(generation)
            )
            and self._published_closure_matches(
                tuple(built.frame.tables),
                plan=plan,
            )
        )

    @staticmethod
    def _published_closure_matches(
        published_tables: collections.abc.Sequence[str],
        *,
        plan: _PublicationPlan,
    ) -> bool:
        required = plan.required_published_tables
        return not required or (
            len(published_tables) == len(required)
            and frozenset(published_tables) == frozenset(required)
        )

    @staticmethod
    def _base_closure_matches(
        published_tables: collections.abc.Sequence[str],
        *,
        plan: _PublicationPlan,
    ) -> bool:
        published = frozenset(published_tables)
        required = frozenset(plan.required_published_tables)
        synthetic: frozenset[str] = (
            frozenset({"source.context"})
            if plan.profile is CatalogPublicationProfile.FULL
            else frozenset()
        )
        return (
            len(published_tables) == len(published)
            and required.issubset(published)
            and published.issubset(required | synthetic)
        )

    @staticmethod
    def _base_publication_plan(
        profile: CatalogPublicationProfile,
    ) -> _PublicationPlan:
        planned = soleaux.tables.planner.TablePlanner().plan(
            include_tables=_STARTUP_TABLES,
            exclude_tables=(),
        )
        if planned.blocked:
            blocked = ", ".join(sorted(planned.blocked))
            raise ValueError(f"{profile.value} base publication has blocked tables: {blocked}")
        return _PublicationPlan(
            profile=profile,
            requested_tables=planned.requested,
            attempted_tables=planned.execution_order,
            required_published_tables=planned.execution_order,
        )

    @staticmethod
    def _validated_generation(
        built: soleaux.analysis.frame.FrameBuild,
    ) -> soleaux.catalog.generation.CatalogGeneration:
        generation = built.catalog_generation
        if generation is None:
            raise RuntimeError("catalog publication frame is missing its producing generation")
        bundle = built.bundle
        if (
            built.frame.snapshot_id != generation.snapshot_id
            or bundle.snapshot.snapshot_id != generation.snapshot_id
            or bundle.snapshot.source_fingerprint != generation.source_fingerprint
        ):
            raise RuntimeError(
                "catalog publication frame and producing generation identities diverged"
            )
        return generation

    @staticmethod
    def _publication_plans(
        *,
        authority_requested_tables: collections.abc.Sequence[str],
    ) -> dict[CatalogPublicationProfile, _PublicationPlan]:
        planner = soleaux.tables.planner.TablePlanner()

        def build_plan(
            profile: CatalogPublicationProfile,
            requested_tables: collections.abc.Sequence[str],
        ) -> _PublicationPlan:
            planned = planner.plan(
                include_tables=requested_tables,
                exclude_tables=(),
            )
            if planned.blocked:
                blocked = ", ".join(sorted(planned.blocked))
                raise ValueError(f"{profile.value} publication has blocked tables: {blocked}")
            return _PublicationPlan(
                profile=profile,
                requested_tables=planned.requested,
                attempted_tables=planned.execution_order,
                required_published_tables=(
                    planned.execution_order
                    if profile is CatalogPublicationProfile.AUTHORITY
                    else ()
                ),
            )

        plans = {
            CatalogPublicationProfile.FULL: build_plan(
                CatalogPublicationProfile.FULL,
                soleaux.contracts.tables.SYNTAX_ONLY_MATERIALIZED_TABLES,
            )
        }
        if authority_requested_tables:
            plans[CatalogPublicationProfile.AUTHORITY] = build_plan(
                CatalogPublicationProfile.AUTHORITY,
                authority_requested_tables,
            )
        return plans

    def published_bundle(
        self,
        workspace_id: str,
        *,
        generation: int,
        snapshot_id: str,
        source_fingerprint: str,
    ) -> soleaux.structural.snapshot.SnapshotBundle | None:
        """Return exact lifespan-captured bytes for an active published read."""
        publication = self._publications.get(workspace_id)
        bundle = self._published_bundles.get(workspace_id)
        if (
            publication is None
            or bundle is None
            or publication.generation != generation
            or publication.snapshot_id != snapshot_id
            or publication.source_fingerprint != source_fingerprint
            or bundle.snapshot.snapshot_id != snapshot_id
            or bundle.snapshot.source_fingerprint != source_fingerprint
        ):
            return None
        return bundle

    def notify_dirty(self) -> None:
        """Wake the owned reconciler after an observed repository mutation."""
        if self._started and not self._closed:
            self._wake.set()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for event in self._reconciled.values():
            event.set()
        self._published_bundles.clear()

    async def _reconcile_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=_RECONCILE_SECONDS)
            self._wake.clear()
            for workspace_id in self._workspaces.workspace_ids:
                try:
                    await self.refresh(self._workspaces.get(workspace_id))
                except Exception as exc:  # one workspace cannot terminate the shared reconciler
                    try:
                        store = self._frames.ensure_catalog_store(
                            self._workspaces.get(workspace_id)
                        )
                        store.mark_failure(workspace_id, str(exc))
                    except Exception:  # failure reporting is subordinate to reconciliation
                        continue
                finally:
                    self._reconciliation_event(workspace_id).set()

    @staticmethod
    def _materialized_rows(
        generation: soleaux.catalog.generation.CatalogGeneration,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        frame: soleaux.contracts.frame.AnalysisFrame,
        *,
        include_source_context: bool,
    ) -> tuple[soleaux.contracts.frame.FactRow, ...]:
        rows = [row for table_rows in frame.tables.values() for row in table_rows]
        if not include_source_context:
            return tuple(rows)
        for chunk in generation.facts.chunks:
            data = {
                "chunk_id": chunk.chunk_id,
                "path": chunk.path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "snippet": chunk.text,
                "generation": generation.number,
                "truncated": False,
            }
            rows.append(
                soleaux.contracts.frame.FactRow(
                    table="source.context",
                    data=data,
                    evidence=soleaux.tables.evidence.evidence_for_path(
                        bundle,
                        path=chunk.path,
                        table="source.context",
                        data=data,
                        evidence_kind=soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
                        resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
                        authority=soleaux.contracts.evidence.Authority.SOURCE,
                        provider="soleaux-catalog-indexer",
                        provider_version=str(generation.number),
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                    ),
                )
            )
        return tuple(rows)

    @staticmethod
    def _kind_for_row(row: soleaux.contracts.frame.FactRow) -> str:
        if row.table == "source.context":
            return "chunk"
        if row.table == "repository.files":
            return "file"
        suffix = row.table.rpartition(".")[2]
        singular = {
            "projects": "project",
            "dependencies": "dependency",
            "scripts": "script",
            "configurations": "config",
            "tasks": "task",
            "routes": "route",
            "rules": "rule",
            "symbols": "symbol",
            "imports": "import",
            "diagnostics": "diagnostic",
            "changes": "change",
            "policies": "policy",
        }.get(suffix)
        return singular or "fact"

    @classmethod
    def _relationships(
        cls,
        rows: collections.abc.Sequence[soleaux.contracts.frame.FactRow],
    ) -> tuple[tuple[str, str, str], ...]:
        memberships: dict[str, list[str]] = collections.defaultdict(list)
        for row in rows:
            row_key = row.evidence.evidence_id
            for token in cls._relation_tokens(row):
                memberships[token].append(row_key)
        edges: list[tuple[str, str, str]] = []
        for token, members in sorted(memberships.items()):
            ordered = tuple(dict.fromkeys(sorted(members)))
            if len(ordered) < 2:
                continue
            anchor = ordered[0]
            edges.extend((anchor, target, token) for target in ordered[1:])
        return tuple(edges)

    @classmethod
    def _relation_tokens(cls, row: soleaux.contracts.frame.FactRow) -> frozenset[str]:
        tokens = {f"path:{row.evidence.path.casefold()}"}

        def visit(value: object, *, field: str | None = None) -> None:
            if _is_object_mapping(value):
                for key, child in value.items():
                    visit(child, field=str(key).casefold())
                return
            if _is_object_sequence(value):
                for child in value:
                    visit(child, field=field)
                return
            if not isinstance(value, str) or not value:
                return
            normalized_field = field or ""
            if normalized_field not in _IDENTITY_FIELDS and not any(
                normalized_field.endswith(suffix) for suffix in _IDENTITY_SUFFIXES
            ):
                return
            normalized_value = value.strip().casefold()
            if normalized_value:
                tokens.add(f"value:{normalized_value}")

        visit(row.data)
        return frozenset(tokens)
