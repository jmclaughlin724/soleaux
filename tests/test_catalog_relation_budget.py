"""Focused contracts for bounded relation expansion under lexical saturation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from soleaux.catalog.indexer import CatalogIndexer
from soleaux.catalog.store import CatalogStore
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

_OBJECTIVE = "lexicalsaturationtoken"


def _fact_row(
    *,
    table: str,
    path: str,
    data: dict[str, object],
    snapshot_id: str,
    source_fingerprint: str,
) -> FactRow:
    return FactRow(
        table=table,
        data=data,
        evidence=Evidence(
            evidence_id=content_digest(f"{table}\0{path}".encode()),
            evidence_kind=EvidenceKind.STRUCTURAL,
            resolution_status=ResolutionStatus.RESOLVED,
            provider="relation-budget-test",
            provider_version="1",
            authority=Authority.SOURCE,
            snapshot_id=snapshot_id,
            path=path,
            range=PositionRange(
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=2,
            ),
            source_hash=content_digest(path.encode()),
            source_fingerprint=source_fingerprint,
            confidence=1.0,
        ),
    )


def test_relation_beam_reaches_owner_and_consumer_before_lexical_overflow(
    tmp_path: Path,
) -> None:
    source_fingerprint = content_digest(b"relation-budget-snapshot")
    snapshot_id = f"main:{source_fingerprint[:16]}"
    lexical_rows = tuple(
        _fact_row(
            table="source.context",
            path=f"source-{index}.txt",
            data={"path": f"source-{index}.txt", "snippet": _OBJECTIVE},
            snapshot_id=snapshot_id,
            source_fingerprint=source_fingerprint,
        )
        for index in range(6)
    )
    owner = _fact_row(
        table="authority.owners",
        path="governance-owner.toml",
        data={"owner": "canonical authority"},
        snapshot_id=snapshot_id,
        source_fingerprint=source_fingerprint,
    )
    consumer = _fact_row(
        table="authority.bindings",
        path="consumer-binding.json",
        data={"consumer": "direct binding"},
        snapshot_id=snapshot_id,
        source_fingerprint=source_fingerprint,
    )
    rows = (*lexical_rows, owner, consumer)
    frame = AnalysisFrame(
        snapshot_id=snapshot_id,
        workspace_id="main",
        semantic_mode=SemanticMode.SYNTAX_ONLY,
        coverage=Coverage(
            status=FrameStatus.COMPLETE,
            eligible_files=len(rows),
            examined_files=len(rows),
            parse_failures=0,
            candidate_count=len(rows),
            resolution_attempts=0,
            resolved_count=len(rows),
            unsupported_count=0,
            failed_count=0,
            deadline=datetime.now(UTC) + timedelta(seconds=1),
            row_file_byte_depth_limits=RowFileByteDepthLimits(
                max_rows=len(rows),
                max_files=len(rows),
                max_bytes=1024,
                max_depth=2,
            ),
            elapsed_ms=0.0,
        ),
        tables={
            "authority.bindings": (consumer,),
            "authority.owners": (owner,),
            "source.context": lexical_rows,
        },
    )
    store = CatalogStore(tmp_path)
    store.publish_materialized(
        frame,
        generation=1,
        source_fingerprint=source_fingerprint,
        rows=rows,
        kinds={
            **{row.evidence.evidence_id: "chunk" for row in lexical_rows},
            owner.evidence.evidence_id: "fact",
            consumer.evidence.evidence_id: "fact",
        },
        relationships=(
            (
                lexical_rows[0].evidence.evidence_id,
                lexical_rows[1].evidence.evidence_id,
                "a-overlapping-lexical",
            ),
            (
                lexical_rows[0].evidence.evidence_id,
                lexical_rows[2].evidence.evidence_id,
                "b-overlapping-lexical",
            ),
            (
                lexical_rows[0].evidence.evidence_id,
                owner.evidence.evidence_id,
                "z-canonical-owner",
            ),
            (
                owner.evidence.evidence_id,
                consumer.evidence.evidence_id,
                "canonical-consumer",
            ),
        ),
        retained_generations=2,
    )

    try:
        assert store.fts_available is True
        baseline = store.read_materialized(
            "main",
            match_expression=f'"{_OBJECTIVE}"',
            limit=4,
        )
        first = store.read_materialized(
            "main",
            match_expression=f'"{_OBJECTIVE}"',
            limit=4,
            relation_depth=2,
        )
        second = store.read_materialized(
            "main",
            match_expression=f'"{_OBJECTIVE}"',
            limit=4,
            relation_depth=2,
        )
        connection = store._connection  # pyright: ignore[reportPrivateUsage]
        assert connection is not None
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        bounded = store.read_materialized(
            "main",
            match_expression=f'"{_OBJECTIVE}"',
            limit=4,
            relation_depth=2,
            count_total_rows=False,
        )
        connection.set_trace_callback(None)
    finally:
        store.close()

    first_ranking = tuple(
        (item.fact_key, item.relation_distance, item.score) for item in first.rows
    )
    second_ranking = tuple(
        (item.fact_key, item.relation_distance, item.score) for item in second.rows
    )
    baseline_by_path = {item.row.evidence.path: item for item in baseline.rows}
    by_path = {item.row.evidence.path: item for item in first.rows}

    assert first_ranking == second_ranking
    assert len(first.rows) == 4
    assert first.total_rows == len(lexical_rows)
    assert first.total_rows_exact is True
    assert first.has_more is True
    assert bounded.total_rows == 5
    assert bounded.total_rows_exact is False
    assert bounded.has_more is True
    assert not any("SELECT COUNT(*) FROM context_fts" in statement for statement in statements)
    assert set(by_path) == {
        lexical_rows[0].evidence.path,
        lexical_rows[1].evidence.path,
        owner.evidence.path,
        consumer.evidence.path,
    }
    assert by_path[lexical_rows[0].evidence.path].relation_distance == 0
    assert (
        by_path[lexical_rows[1].evidence.path].score
        == baseline_by_path[lexical_rows[1].evidence.path].score
    )
    assert by_path[owner.evidence.path].relation_distance == 1
    assert by_path[consumer.evidence.path].relation_distance == 2


def test_high_cardinality_relation_token_materializes_a_bounded_star() -> None:
    source_fingerprint = content_digest(b"high-cardinality-relation")
    snapshot_id = f"main:{source_fingerprint[:16]}"
    rows = tuple(
        _fact_row(
            table="repository.scripts",
            path=f"script-{index:03}.json",
            data={
                "project_id": "main:node:.",
                "name": f"script-{index:03}",
                "command": f"command-{index:03}",
            },
            snapshot_id=snapshot_id,
            source_fingerprint=source_fingerprint,
        )
        for index in range(257)
    )

    relationships = CatalogIndexer._relationships(rows)
    project_edges = tuple(
        (source, target) for source, target, basis in relationships if basis == "value:main:node:."
    )

    assert len(project_edges) == len(rows) - 1
    assert {key for edge in project_edges for key in edge} == {
        row.evidence.evidence_id for row in rows
    }
