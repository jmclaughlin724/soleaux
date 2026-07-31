"""Demand-driven table planning and single AnalysisFrame assembly (D004, D017)."""

from __future__ import annotations

import collections.abc
import datetime
import time
import typing

import pydantic

import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.requests
import soleaux.contracts.tables
import soleaux.structural.snapshot


class TablePlanError(ValueError):
    """A producer violated the fixed table or evidence contract."""


_MAX_PRODUCER_COVERAGE_NOTES = 8
_MAX_COVERAGE_NOTE_CHARS = 512


def _bounded_coverage_note(note: str) -> str:
    """Keep one coverage note within the serialized coverage budget."""
    if len(note) <= _MAX_COVERAGE_NOTE_CHARS:
        return note
    return f"{note[: _MAX_COVERAGE_NOTE_CHARS - 1]}…"


def _bounded_producer_notes(
    producer: soleaux.contracts.tables.Producer, notes: tuple[str, ...]
) -> list[str]:
    """Keep bounded verbatim producer notes plus a truthful omission summary."""
    bounded = [_bounded_coverage_note(note) for note in notes[:_MAX_PRODUCER_COVERAGE_NOTES]]
    if len(notes) > _MAX_PRODUCER_COVERAGE_NOTES:
        bounded.append(
            f"{len(notes) - _MAX_PRODUCER_COVERAGE_NOTES} further coverage notes "
            f"from producer {producer.value} omitted"
        )
    return bounded


def _bounded_omitted_reasons(reasons: collections.abc.Sequence[str]) -> tuple[str, ...]:
    """Keep the generation omission list inside the Coverage contract bound."""
    bounded = [_bounded_coverage_note(reason) for reason in reasons]
    if len(bounded) <= soleaux.contracts.coverage.MAX_OMITTED_REASONS:
        return tuple(bounded)
    kept = soleaux.contracts.coverage.MAX_OMITTED_REASONS - 1
    return (
        *bounded[:kept],
        f"{len(bounded) - kept} further coverage reasons omitted from this generation",
    )


class TablePlan(pydantic.BaseModel):
    """One immutable, explicit producer plan."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    requested: tuple[str, ...]
    execution_order: tuple[str, ...]
    blocked: dict[str, tuple[str, ...]] = pydantic.Field(default_factory=dict[str, tuple[str, ...]])
    producer_tables: dict[soleaux.contracts.tables.Producer, tuple[str, ...]] = pydantic.Field(
        default_factory=dict[soleaux.contracts.tables.Producer, tuple[str, ...]]
    )


@typing.runtime_checkable
class TableProducer(typing.Protocol):
    """One demand-driven producer plane."""

    supported_tables: frozenset[str]

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        upstream_tables: collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]],
    ) -> collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        """Produce planned tables from request-local prerequisite rows."""
        ...


@typing.runtime_checkable
class TableCoverageReporter(typing.Protocol):
    """A producer that can explain why the rows it returned are not complete.

    `produce` alone can only say "here are rows", so a producer that knows its
    own coverage was degraded has no way to say so. Implementing this lets it
    contribute omitted reasons and force `PARTIAL` instead of claiming an
    authoritative result.
    """

    def coverage_notes(self) -> tuple[str, ...]:
        """Reasons the rows from the last `produce` call are not authoritative."""
        ...


class TablePlanner:
    """Compute prerequisite closure and assemble the canonical AnalysisFrame."""

    def plan(
        self,
        *,
        include_tables: collections.abc.Sequence[str],
        exclude_tables: collections.abc.Sequence[str],
        suggested_tables: collections.abc.Sequence[str] = (),
    ) -> TablePlan:
        """Plan explicit requests; suggestions are deliberately inert."""
        del suggested_tables
        selected = soleaux.contracts.tables.validate_table_selection(
            list(include_tables), list(exclude_tables)
        )
        excluded = frozenset(exclude_tables)
        requested = tuple(descriptor.name for descriptor in selected)
        planned: set[str] = set()
        blocked: dict[str, tuple[str, ...]] = {}

        for table_name in requested:
            closure, reasons = self._closure(table_name, excluded=excluded, visiting=frozenset())
            if reasons:
                blocked[table_name] = reasons
                continue
            planned.update(closure)

        execution_order = tuple(
            descriptor.name
            for descriptor in soleaux.contracts.tables.TABLE_CATALOG
            if descriptor.name in planned
        )
        producer_tables = {
            producer: tuple(
                table_name
                for table_name in execution_order
                if soleaux.contracts.tables.CATALOG_BY_NAME[table_name].producer is producer
            )
            for producer in soleaux.contracts.tables.Producer
            if any(
                soleaux.contracts.tables.CATALOG_BY_NAME[name].producer is producer
                for name in execution_order
            )
        }
        return TablePlan(
            requested=requested,
            execution_order=execution_order,
            blocked=blocked,
            producer_tables=producer_tables,
        )

    def _closure(
        self,
        table_name: str,
        *,
        excluded: frozenset[str],
        visiting: frozenset[str],
    ) -> tuple[frozenset[str], tuple[str, ...]]:
        if table_name in excluded:
            return frozenset(), (f"excluded prerequisite {table_name}",)
        if table_name in visiting:
            raise TablePlanError(f"cyclic table prerequisite: {table_name}")
        descriptor = soleaux.contracts.tables.CATALOG_BY_NAME[table_name]
        closure = {table_name}
        next_visiting = visiting | {table_name}
        for prerequisite in descriptor.prerequisites:
            prerequisite_closure, reasons = self._closure(
                prerequisite,
                excluded=excluded,
                visiting=next_visiting,
            )
            if reasons:
                return frozenset(), reasons
            closure.update(prerequisite_closure)
        return frozenset(closure), ()

    async def execute(
        self,
        plan: TablePlan,
        *,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        producers: collections.abc.Mapping[soleaux.contracts.tables.Producer, TableProducer],
        row_limit: int = 200,
        max_depth: int = 8,
        deadline_seconds: float = 10.0,
    ) -> soleaux.contracts.frame.AnalysisFrame:
        """Run each needed producer once and assemble the one canonical frame."""
        started = time.perf_counter()
        produced: dict[str, tuple[soleaux.contracts.frame.FactRow, ...]] = {}
        omitted_reasons = [
            reason for table_name in plan.requested for reason in plan.blocked.get(table_name, ())
        ]
        unsupported_count = len(plan.blocked)
        failed_count = 0
        was_truncated = False
        degraded = False

        for producer in soleaux.contracts.tables.Producer:
            table_names = plan.producer_tables.get(producer, ())
            if not table_names:
                continue
            implementation = producers.get(producer)
            if implementation is None:
                unsupported_count += len(table_names)
                omitted_reasons.extend(
                    self._unsupported_reason(
                        table_name,
                        producer,
                        implementation_missing=True,
                    )
                    for table_name in table_names
                )
                continue
            unsupported = tuple(
                table_name
                for table_name in table_names
                if table_name not in implementation.supported_tables
            )
            if unsupported:
                unsupported_count += len(unsupported)
                omitted_reasons.extend(
                    self._unsupported_reason(table_name, producer) for table_name in unsupported
                )
            supported = tuple(
                table_name
                for table_name in table_names
                if table_name in implementation.supported_tables
            )
            runnable = tuple(
                table_name
                for table_name in supported
                if not (
                    semantic_mode is soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY
                    and self._requires_semantic(table_name)
                )
            )
            skipped = len(supported) - len(runnable)
            if skipped:
                unsupported_count += skipped
                omitted_reasons.extend(
                    f"{table_name}: semantic_mode=syntax_only"
                    for table_name in supported
                    if table_name not in runnable
                )
            if not runnable:
                continue
            output = await implementation.produce(
                runnable,
                bundle,
                semantic_mode,
                dict(produced),
            )
            unknown_output = set(output) - set(runnable)
            if unknown_output:
                names = ", ".join(sorted(unknown_output))
                raise TablePlanError(
                    f"producer {producer.value} returned unplanned table(s): {names}"
                )
            for table_name in runnable:
                rows = output.get(table_name)
                if rows is None:
                    unsupported_count += 1
                    omitted_reasons.append(
                        f"{table_name}: producer {producer.value} returned no result"
                    )
                    continue
                self._validate_rows(table_name, rows)
                bounded_rows = rows[:row_limit]
                if len(rows) > row_limit:
                    was_truncated = True
                    omitted_reasons.append(f"{table_name}: row limit {row_limit} reached")
                produced[table_name] = bounded_rows
            if isinstance(implementation, TableCoverageReporter):
                producer_notes = implementation.coverage_notes()
                if producer_notes:
                    omitted_reasons.extend(_bounded_producer_notes(producer, producer_notes))
                    degraded = True

        ordered_tables = {
            table_name: produced[table_name]
            for table_name in plan.execution_order
            if table_name in produced
        }
        requested_outputs = tuple(
            table_name for table_name in plan.requested if table_name in produced
        )
        all_rows = tuple(row for rows in ordered_tables.values() for row in rows)
        if bundle.snapshot.changed_during_analysis:
            status = soleaux.contracts.coverage.FrameStatus.CHANGED_DURING_ANALYSIS
        elif was_truncated:
            status = soleaux.contracts.coverage.FrameStatus.TRUNCATED
        elif unsupported_count and not requested_outputs:
            status = soleaux.contracts.coverage.FrameStatus.UNSUPPORTED
        elif unsupported_count or failed_count or degraded:
            status = soleaux.contracts.coverage.FrameStatus.PARTIAL
        else:
            status = soleaux.contracts.coverage.FrameStatus.COMPLETE
        total_bytes = sum(len(content) for content in bundle.contents.values())
        coverage = soleaux.contracts.coverage.Coverage(
            status=status,
            eligible_files=len(bundle.snapshot.files),
            examined_files=len(bundle.snapshot.files) if plan.execution_order else 0,
            parse_failures=0,
            candidate_count=sum(
                row.evidence.resolution_status
                is soleaux.contracts.evidence.ResolutionStatus.CANDIDATE
                for row in all_rows
            ),
            resolution_attempts=sum(
                soleaux.contracts.tables.CATALOG_BY_NAME[table_name].producer
                in {
                    soleaux.contracts.tables.Producer.SEMANTIC,
                    soleaux.contracts.tables.Producer.AUTHORITY,
                    soleaux.contracts.tables.Producer.DERIVED,
                }
                for table_name in ordered_tables
            ),
            resolved_count=sum(
                row.evidence.resolution_status
                is soleaux.contracts.evidence.ResolutionStatus.RESOLVED
                for row in all_rows
            ),
            unsupported_count=unsupported_count,
            failed_count=failed_count,
            omitted_reasons=_bounded_omitted_reasons(omitted_reasons),
            deadline=datetime.datetime.now(datetime.UTC)
            + datetime.timedelta(seconds=deadline_seconds),
            row_file_byte_depth_limits=soleaux.contracts.coverage.RowFileByteDepthLimits(
                max_rows=row_limit,
                max_files=max(len(bundle.snapshot.files), 1),
                max_bytes=max(total_bytes, 1),
                max_depth=max_depth,
            ),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return soleaux.contracts.frame.AnalysisFrame(
            snapshot_id=bundle.snapshot.snapshot_id,
            workspace_id=bundle.snapshot.workspace_id,
            semantic_mode=semantic_mode,
            coverage=coverage,
            tables=ordered_tables,
            warnings=(*bundle.notes, *omitted_reasons),
        )

    @staticmethod
    def _unsupported_reason(
        table_name: str,
        producer: soleaux.contracts.tables.Producer,
        *,
        implementation_missing: bool = False,
    ) -> str:
        catalog_reason = soleaux.contracts.tables.CATALOG_BY_NAME[table_name].unavailable_reason
        if catalog_reason is not None:
            return catalog_reason
        if implementation_missing:
            return f"{table_name}: producer {producer.value} is unavailable"
        return f"{table_name}: producer {producer.value} does not advertise support"

    def _requires_semantic(self, table_name: str) -> bool:
        descriptor = soleaux.contracts.tables.CATALOG_BY_NAME[table_name]
        return descriptor.producer is soleaux.contracts.tables.Producer.SEMANTIC or any(
            self._requires_semantic(prerequisite) for prerequisite in descriptor.prerequisites
        )

    @staticmethod
    def _validate_rows(table_name: str, rows: tuple[soleaux.contracts.frame.FactRow, ...]) -> None:
        for row in rows:
            if row.table != table_name:
                raise TablePlanError(f"producer for {table_name} returned row for {row.table}")
            is_structural_derived_row = (
                table_name.startswith("derived.")
                and row.evidence.evidence_kind is soleaux.contracts.evidence.EvidenceKind.STRUCTURAL
            )
            if is_structural_derived_row:
                raise TablePlanError(f"structural candidate cannot be projected into {table_name}")
