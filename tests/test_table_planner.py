"""D004/D017: explicit table planning and the single AnalysisFrame assembly."""

from __future__ import annotations

import collections.abc
import datetime
import hashlib

import _assertions

import soleaux.analysis.frame
import soleaux.authority.resolver
import soleaux.catalog.tables
import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.requests
import soleaux.contracts.snapshot
import soleaux.contracts.tables
import soleaux.relations.materializer
import soleaux.relations.resolver
import soleaux.structural.snapshot
import soleaux.structural.supervisor
import soleaux.tables.evidence
import soleaux.tables.imported
import soleaux.tables.planner


def _bundle(content: bytes = b"value = 1\n") -> soleaux.structural.snapshot.SnapshotBundle:
    captured = soleaux.contracts.snapshot.CapturedFile(
        workspace_id="workspace",
        path="src/main.py",
        content_hash=hashlib.blake2b(content, digest_size=32).hexdigest(),
        byte_start=0,
        byte_end=len(content),
        start_line=0,
        start_column=0,
        end_line=content.count(b"\n"),
        end_column=0,
        encoding="utf-8",
        newline="lf",
        language="Python",
        producer_id="test",
        producer_version="1",
        producer_config_digest="test",
        claim_basis=soleaux.contracts.snapshot.ClaimBasis.SYNTAX,
    )
    snapshot = soleaux.contracts.snapshot.RepositorySnapshot(
        snapshot_id="workspace:test",
        workspace_id="workspace",
        root="/workspace",
        created_at=datetime.datetime.now(datetime.UTC),
        files=(captured,),
        source_fingerprint="snapshot-fingerprint",
    )
    return soleaux.structural.snapshot.SnapshotBundle(
        snapshot=snapshot,
        contents={"src/main.py": content},
        notes=(),
    )


class _CountingProducer:
    def __init__(
        self,
        supported_tables: frozenset[str] = frozenset(
            descriptor.name for descriptor in soleaux.contracts.tables.TABLE_CATALOG
        ),
    ) -> None:
        self.supported_tables = supported_tables
        self.calls: list[tuple[str, ...]] = []

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        upstream_tables: collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]],
    ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        del upstream_tables
        self.calls.append(table_names)
        rows: dict[str, tuple[soleaux.contracts.frame.FactRow, ...]] = {}
        for table_name in table_names:
            rows[table_name] = (
                soleaux.contracts.frame.FactRow(
                    table=table_name,
                    data={"path": "src/main.py", "semantic_mode": semantic_mode.value},
                    evidence=soleaux.tables.evidence.evidence_for_path(
                        bundle,
                        path="src/main.py",
                        table=table_name,
                        data={"path": "src/main.py"},
                        evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
                        resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
                        authority=soleaux.contracts.evidence.Authority.SOURCE,
                        provider="counting-producer",
                        provider_version="1",
                    ),
                ),
            )
        return rows


def test_excluded_prerequisite_is_a_hard_prohibition() -> None:
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("semantic.imports", "derived.consumers"),
        exclude_tables=("semantic.calls",),
        suggested_tables=("quality.diagnostics",),
    )

    assert plan.execution_order == ("semantic.imports",)
    assert plan.blocked == {
        "derived.consumers": ("excluded prerequisite semantic.calls",),
    }
    assert "quality.diagnostics" not in plan.execution_order


async def test_suggestions_trigger_zero_additional_producer_calls() -> None:
    producers = {producer: _CountingProducer() for producer in soleaux.contracts.tables.Producer}
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("repository.files",),
        exclude_tables=(),
        suggested_tables=(
            "syntax.declarations",
            "semantic.imports",
            "authority.owners",
            "derived.impact",
        ),
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
        producers=producers,
    )

    assert producers[soleaux.contracts.tables.Producer.SNAPSHOT].calls == [("repository.files",)]
    for producer, implementation in producers.items():
        if producer is not soleaux.contracts.tables.Producer.SNAPSHOT:
            assert implementation.calls == []
    assert tuple(frame.tables) == ("repository.files",)
    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE


async def test_one_producer_receives_all_selected_tables_in_one_batch() -> None:
    structural = _CountingProducer()
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("syntax.declarations", "syntax.imports"),
        exclude_tables=(),
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
        producers={soleaux.contracts.tables.Producer.STRUCTURAL: structural},
    )

    assert structural.calls == [("syntax.declarations", "syntax.imports")]
    assert tuple(frame.tables) == ("syntax.declarations", "syntax.imports")


async def test_planner_bounds_producer_coverage_notes_with_summary() -> None:
    class _NoisyProducer(_CountingProducer):
        def __init__(self) -> None:
            super().__init__(supported_tables=frozenset({"repository.files"}))

        def coverage_notes(self) -> tuple[str, ...]:
            return tuple(f"note {index}" for index in range(50))

    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("repository.files",),
        exclude_tables=(),
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
        producers={soleaux.contracts.tables.Producer.SNAPSHOT: _NoisyProducer()},
    )

    reasons = frame.coverage.omitted_reasons
    assert reasons == (
        *(f"note {index}" for index in range(8)),
        "42 further coverage notes from producer snapshot omitted",
    )
    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.PARTIAL


def test_planner_bounds_total_omitted_reasons_with_summary() -> None:
    reasons = soleaux.tables.planner._bounded_omitted_reasons(
        tuple(f"reason {index}" for index in range(70))
    )

    assert len(reasons) == soleaux.contracts.coverage.MAX_OMITTED_REASONS
    assert reasons[:63] == tuple(f"reason {index}" for index in range(63))
    assert reasons[63] == "7 further coverage reasons omitted from this generation"


def test_planner_bounds_single_coverage_note_length() -> None:
    note = soleaux.tables.planner._bounded_coverage_note("x" * 600)

    assert len(note) == 512
    assert note.endswith("…")


async def test_syntax_members_runs_through_the_structural_table_producer() -> None:
    bundle = _bundle(
        b"class Holder:\n"
        b"    annotated: int = 1\n\n"
        b"    def method(self):\n"
        b"        return self.annotated\n"
    )
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        structural = soleaux.analysis.frame.StructuralTableProducer(supervisor)
        plan = soleaux.tables.planner.TablePlanner().plan(
            include_tables=("syntax.members",), exclude_tables=()
        )
        frame = await soleaux.tables.planner.TablePlanner().execute(
            plan,
            bundle=bundle,
            semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            producers={
                soleaux.contracts.tables.Producer.STRUCTURAL: structural,
            },
        )
    finally:
        await supervisor.aclose()

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    assert tuple(frame.tables) == ("syntax.members",)
    assert {
        (
            row.data["name"],
            row.data["kind"],
            row.data["attributes"]["member_of"],
        )
        for row in frame.tables["syntax.members"]
    } == {
        ("annotated", "attribute", "Holder"),
        ("method", "method", "Holder"),
    }


async def test_syntax_only_never_invokes_a_semantic_producer() -> None:
    semantic = _CountingProducer()
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("semantic.imports",),
        exclude_tables=(),
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        producers={soleaux.contracts.tables.Producer.SEMANTIC: semantic},
    )

    assert semantic.calls == []
    assert frame.tables == {}
    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.UNSUPPORTED
    assert frame.coverage.unsupported_count == 1


async def test_producer_cannot_return_a_row_for_another_table() -> None:
    class _WrongTableProducer:
        supported_tables = frozenset({"repository.files"})

        async def produce(
            self,
            table_names: tuple[str, ...],
            bundle: soleaux.structural.snapshot.SnapshotBundle,
            semantic_mode: soleaux.contracts.requests.SemanticMode,
            upstream_tables: collections.abc.Mapping[
                str, tuple[soleaux.contracts.frame.FactRow, ...]
            ],
        ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
            del table_names, semantic_mode, upstream_tables
            return {
                "repository.files": (
                    soleaux.contracts.frame.FactRow(
                        table="syntax.imports",
                        data={},
                        evidence=soleaux.tables.evidence.evidence_for_path(
                            bundle,
                            path="src/main.py",
                            table="syntax.imports",
                            data={},
                            evidence_kind=soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
                            resolution_status=soleaux.contracts.evidence.ResolutionStatus.CANDIDATE,
                            authority=soleaux.contracts.evidence.Authority.SOURCE,
                            provider="wrong",
                            provider_version="1",
                        ),
                    ),
                )
            }

    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("repository.files",),
        exclude_tables=(),
    )
    producers: dict[soleaux.contracts.tables.Producer, soleaux.tables.planner.TableProducer] = {
        soleaux.contracts.tables.Producer.SNAPSHOT: _WrongTableProducer(),
    }

    with _assertions.raises_with_message(
        soleaux.tables.planner.TablePlanError, "returned row for syntax.imports"
    ):
        await soleaux.tables.planner.TablePlanner().execute(
            plan,
            bundle=_bundle(),
            semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
            producers=producers,
        )


async def test_derived_producer_receives_prerequisites_in_the_same_frame() -> None:
    bundle = _bundle()

    class _SemanticProducer:
        supported_tables = frozenset({"semantic.imports"})

        async def produce(
            self,
            table_names: tuple[str, ...],
            bundle: soleaux.structural.snapshot.SnapshotBundle,
            semantic_mode: soleaux.contracts.requests.SemanticMode,
            upstream_tables: collections.abc.Mapping[
                str, tuple[soleaux.contracts.frame.FactRow, ...]
            ],
        ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
            del table_names, semantic_mode
            assert upstream_tables == {}
            data = {
                "source_path": "src/main.py",
                "target_path": "src/main.py",
                "specifier": ".",
            }
            return {
                "semantic.imports": (
                    soleaux.contracts.frame.FactRow(
                        table="semantic.imports",
                        data=data,
                        evidence=soleaux.tables.evidence.evidence_for_path(
                            bundle,
                            path="src/main.py",
                            table="semantic.imports",
                            data=data,
                            evidence_kind=soleaux.contracts.evidence.EvidenceKind.SEMANTIC,
                            resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
                            authority=soleaux.contracts.evidence.Authority.SOURCE,
                            provider="semantic-test",
                            provider_version="1",
                        ),
                    ),
                )
            }

    class _DerivedProducer:
        supported_tables = frozenset({"derived.dependencies"})

        async def produce(
            self,
            table_names: tuple[str, ...],
            bundle: soleaux.structural.snapshot.SnapshotBundle,
            semantic_mode: soleaux.contracts.requests.SemanticMode,
            upstream_tables: collections.abc.Mapping[
                str, tuple[soleaux.contracts.frame.FactRow, ...]
            ],
        ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
            del bundle, semantic_mode
            source = upstream_tables["semantic.imports"][0]
            data = {
                "source_path": source.data["source_path"],
                "target_path": source.data["target_path"],
                "edge_kind": source.table,
            }
            return {
                table_names[0]: (
                    soleaux.contracts.frame.FactRow(
                        table=table_names[0],
                        data=data,
                        evidence=soleaux.tables.evidence.derived_evidence(
                            source.evidence,
                            table=table_names[0],
                            data=data,
                        ),
                    ),
                )
            }

    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("derived.dependencies",),
        exclude_tables=(),
    )
    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=bundle,
        semantic_mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
        producers={
            soleaux.contracts.tables.Producer.SEMANTIC: _SemanticProducer(),
            soleaux.contracts.tables.Producer.DERIVED: _DerivedProducer(),
        },
    )

    assert tuple(frame.tables) == ("semantic.imports", "derived.dependencies")
    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE


async def test_unconfigured_policy_request_is_unsupported_not_complete() -> None:
    producer = soleaux.analysis.frame.StructuralTableProducer(
        soleaux.structural.supervisor.StructuralWorkerSupervisor()
    )
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("quality.standards",), exclude_tables=()
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        producers={soleaux.contracts.tables.Producer.STRUCTURAL: producer},
    )

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.UNSUPPORTED
    assert frame.coverage.unsupported_count == 1
    assert "quality.standards" not in frame.tables
    assert any(
        "configured structural-policy analyzer is unavailable" in reason
        for reason in frame.coverage.omitted_reasons
    )


async def test_unconfigured_policy_degrades_a_mixed_frame_to_partial() -> None:
    structural = soleaux.analysis.frame.StructuralTableProducer(
        soleaux.structural.supervisor.StructuralWorkerSupervisor()
    )
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("repository.files", "quality.standards"),
        exclude_tables=(),
    )

    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        producers={
            soleaux.contracts.tables.Producer.SNAPSHOT: _CountingProducer(),
            soleaux.contracts.tables.Producer.STRUCTURAL: structural,
        },
    )

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.PARTIAL
    assert tuple(frame.tables) == ("repository.files",)
    assert any(
        "configured structural-policy analyzer is unavailable" in reason
        for reason in frame.coverage.omitted_reasons
    )


def test_catalog_availability_matches_immutable_producer_capabilities() -> None:
    implementation_capabilities: dict[soleaux.contracts.tables.Producer, frozenset[str]] = {
        soleaux.contracts.tables.Producer.SNAPSHOT: (
            soleaux.analysis.frame.SnapshotTableProducer.supported_tables
        ),
        soleaux.contracts.tables.Producer.CATALOG: (
            soleaux.catalog.tables.CatalogTableProducer.supported_tables
        ),
        soleaux.contracts.tables.Producer.STRUCTURAL: (
            soleaux.analysis.frame.StructuralTableProducer.supported_tables
        ),
        soleaux.contracts.tables.Producer.SEMANTIC: (
            soleaux.analysis.frame.SemanticTableProducer.supported_tables
        ),
        soleaux.contracts.tables.Producer.AUTHORITY: (
            soleaux.authority.resolver.AuthorityResolver.supported_tables
        ),
        soleaux.contracts.tables.Producer.DERIVED: (
            soleaux.relations.materializer.DerivedMaterializer.supported_tables
        ),
        soleaux.contracts.tables.Producer.IMPORTED: (
            soleaux.tables.imported.ImportedTableProducer.supported_tables
        ),
    }

    assert implementation_capabilities == soleaux.contracts.tables.PRODUCER_SUPPORTED_TABLES
    assert soleaux.relations.resolver.RelationResolver.supported_tables == frozenset(
        {"semantic.imports", "semantic.calls"}
    )
    assert (
        soleaux.relations.resolver.RelationResolver.supported_tables
        < soleaux.analysis.frame.SemanticTableProducer.supported_tables
    )
    for supported_tables in implementation_capabilities.values():
        assert isinstance(supported_tables, frozenset)
    for descriptor in soleaux.contracts.tables.TABLE_CATALOG:
        expected_available = descriptor.name in implementation_capabilities[descriptor.producer]
        assert descriptor.availability == ("available" if expected_available else "unavailable")
        assert (descriptor.unavailable_reason is None) is expected_available


async def test_registrations_report_complete_when_no_framework_is_present() -> None:
    """Zero rows under COMPLETE is the honest answer for a repo with no framework.

    The bundle holds one Python file and no manifest, so "no framework
    registrations exist" is true rather than unknown.
    """
    plan = soleaux.tables.planner.TablePlanner().plan(
        include_tables=("framework.registrations",), exclude_tables=()
    )

    structural = soleaux.analysis.frame.StructuralTableProducer(
        soleaux.structural.supervisor.StructuralWorkerSupervisor()
    )
    frame = await soleaux.tables.planner.TablePlanner().execute(
        plan,
        bundle=_bundle(),
        semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        producers={soleaux.contracts.tables.Producer.STRUCTURAL: structural},
    )

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
    assert frame.tables["framework.registrations"] == ()
    assert frame.coverage.omitted_reasons == ()
