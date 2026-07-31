"""Closed contract validation for evidence, requests, tables, and budgets."""

import datetime

import _assertions
import pydantic
import pytest

import soleaux.contracts.budget
import soleaux.contracts.context
import soleaux.contracts.coverage
import soleaux.contracts.cursor
import soleaux.contracts.evidence
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.tables
import soleaux.lsp.contracts


def _evidence(**overrides: object) -> soleaux.contracts.evidence.Evidence:
    fields: dict[str, object] = {
        "evidence_id": "e-1",
        "evidence_kind": soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
        "resolution_status": soleaux.contracts.evidence.ResolutionStatus.CANDIDATE,
        "provider": "ast-grep-py",
        "provider_version": "0.44.1",
        "authority": soleaux.contracts.evidence.Authority.SOURCE,
        "snapshot_id": "snap-1",
        "path": "src/mod.py",
        "range": soleaux.contracts.evidence.PositionRange(
            start_line=1, start_column=1, end_line=1, end_column=5
        ),
        "source_hash": "a" * 64,
        "source_fingerprint": "fp-1",
        "confidence": 0.5,
        "note": "",
    }
    fields.update(overrides)
    return soleaux.contracts.evidence.Evidence(**fields)  # type: ignore[arg-type]


def test_evidence_confidence_is_bounded() -> None:
    with pytest.raises(pydantic.ValidationError):
        _evidence(confidence=1.5)
    with pytest.raises(pydantic.ValidationError):
        _evidence(confidence=-0.1)


def test_evidence_note_refuses_absolute_paths_and_overrun() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "absolute paths"):
        _evidence(note="/etc/passwd")
    with _assertions.raises_with_message(pydantic.ValidationError, "exceeds"):
        _evidence(note="x" * 281)


def test_evidence_source_hash_must_be_lowercase_sha256() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "SHA-256"):
        _evidence(source_hash="A" * 64)
    with _assertions.raises_with_message(pydantic.ValidationError, "SHA-256"):
        _evidence(source_hash="short")


def test_evidence_path_must_be_workspace_relative() -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, "workspace-relative"):
        _evidence(path="/abs/path.py")
    with _assertions.raises_with_message(pydantic.ValidationError, "'..'"):
        _evidence(path="src/../escape.py")


def test_semantic_mode_rejects_alias_and_unknown_values() -> None:
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.SearchRequest(query="x", semantic_mode="required")  # type: ignore[arg-type]
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.SearchRequest(query="x", semantic_mode="sometimes")  # type: ignore[arg-type]
    assert (
        soleaux.contracts.requests.SearchRequest(query="x").semantic_mode
        is soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE
    )


def _coverage(**overrides: object) -> soleaux.contracts.coverage.Coverage:
    fields: dict[str, object] = {
        "status": soleaux.contracts.coverage.FrameStatus.COMPLETE,
        "eligible_files": 1,
        "examined_files": 1,
        "parse_failures": 0,
        "candidate_count": 0,
        "resolution_attempts": 0,
        "resolved_count": 1,
        "unsupported_count": 0,
        "failed_count": 0,
        "deadline": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1),
        "row_file_byte_depth_limits": soleaux.contracts.coverage.RowFileByteDepthLimits(
            max_rows=1,
            max_files=1,
            max_bytes=1,
            max_depth=1,
        ),
        "elapsed_ms": 0.0,
    }
    fields.update(overrides)
    return soleaux.contracts.coverage.Coverage(**fields)  # type: ignore[arg-type]


def test_coverage_omitted_reasons_fail_closed_above_the_contract_bound() -> None:
    with pytest.raises(pydantic.ValidationError):
        _coverage(
            omitted_reasons=tuple(
                f"reason {index}"
                for index in range(soleaux.contracts.coverage.MAX_OMITTED_REASONS + 1)
            )
        )
    assert (
        len(
            _coverage(
                omitted_reasons=tuple(
                    f"reason {index}"
                    for index in range(soleaux.contracts.coverage.MAX_OMITTED_REASONS)
                )
            ).omitted_reasons
        )
        == soleaux.contracts.coverage.MAX_OMITTED_REASONS
    )


def test_context_packet_gaps_fail_closed_above_the_contract_bound() -> None:
    def packet(gap_count: int) -> soleaux.contracts.context.TaskContextPacket:
        return soleaux.contracts.context.TaskContextPacket(
            objective="explain answer",
            retrieval_engine="test",
            ranked_candidate_count=0,
            related_fact_count=0,
            returned_item_count=0,
            coverage_complete=False,
            gaps=tuple(
                soleaux.contracts.context.ContextGap(code="gap", message=f"m{index}")
                for index in range(gap_count)
            ),
        )

    with pytest.raises(pydantic.ValidationError):
        packet(soleaux.contracts.context.MAX_PACKET_GAPS + 1)
    assert len(packet(soleaux.contracts.context.MAX_PACKET_GAPS).gaps) == (
        soleaux.contracts.context.MAX_PACKET_GAPS
    )


def test_request_wire_enums_decode_without_weakening_strict_validation() -> None:
    search = soleaux.contracts.requests.SearchRequest.model_validate(
        {
            "query": "target",
            "kinds": ["symbol", "route"],
            "semantic_mode": "syntax_only",
            "cursor": "opaque",
        },
        strict=True,
    )
    lint = soleaux.contracts.requests.LintRequest.model_validate(
        {
            "paths": ["src"],
            "rule_ids": ["no-console"],
        },
        strict=True,
    )
    preview = soleaux.contracts.requests.PreviewEditRequest.model_validate(
        {
            "operation": "rename",
            "path": "main.py",
            "target": "name",
            "symbol_name": "target",
            "new_name": "renamed",
        },
        strict=True,
    )

    assert search.kinds == [
        soleaux.contracts.requests.SearchKind.SYMBOL,
        soleaux.contracts.requests.SearchKind.ROUTE,
    ]
    assert search.semantic_mode is soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY
    assert search.cursor == "opaque"
    assert lint.rule_ids == ["no-console"]
    assert preview.operation is soleaux.contracts.requests.PreviewOperation.RENAME
    assert preview.target is soleaux.contracts.requests.RenameTarget.NAME
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.SearchRequest.model_validate(
            {"query": "target", "limit": "1"},
            strict=True,
        )
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.SearchRequest.model_validate(
            {"query": "target", "kinds": ["table"]},
        )


def test_navigation_requires_exactly_one_position_or_name_target() -> None:
    position = soleaux.lsp.contracts.NavigationRequest(
        operation=soleaux.lsp.contracts.SemanticOperation.REFERENCES,
        path="main.py",
        line=1,
        column=1,
    )
    name = soleaux.lsp.contracts.NavigationRequest(
        operation=soleaux.lsp.contracts.SemanticOperation.REFERENCES,
        symbol_name="target",
    )
    narrowed_name = soleaux.lsp.contracts.NavigationRequest(
        operation=soleaux.lsp.contracts.SemanticOperation.REFERENCES,
        path="src/main.py",
        symbol_name="target",
        symbol_kind="function",
        limit=200,
    )

    assert (position.path, position.line, position.column) == ("main.py", 1, 1)
    assert name.path is None
    assert name.limit == 50
    assert narrowed_name.symbol_kind == "function"
    assert narrowed_name.limit == 200

    invalid_requests = (
        {"operation": "references"},
        {"operation": "references", "path": "main.py", "line": 1},
        {
            "operation": "references",
            "path": "main.py",
            "line": 1,
            "column": 1,
            "symbol_name": "target",
        },
        {"operation": "references", "symbol_kind": "function"},
        {"operation": "references", "symbol_name": "target", "line": 1},
        {"operation": "references", "symbol_name": "target", "limit": 201},
    )
    for payload in invalid_requests:
        with pytest.raises(pydantic.ValidationError):
            soleaux.lsp.contracts.NavigationRequest.model_validate(payload)


def test_requests_are_closed() -> None:
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.SearchRequest(query="x", bogus=True)  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.requests.OwnershipRequest(policy="p", bogus=1)  # type: ignore[call-arg]


def test_table_catalog_is_internally_consistent() -> None:
    names = [descriptor.name for descriptor in soleaux.contracts.tables.TABLE_CATALOG]
    assert len(set(names)) == len(names)
    assert soleaux.contracts.tables.CATALOG_BY_NAME[
        "derived.dead_code_candidates"
    ].prerequisites == (
        "authority.entrypoints",
        "derived.consumers",
    )
    for descriptor in soleaux.contracts.tables.TABLE_CATALOG:
        assert descriptor.schema_version == "soleaux.tables/v1"
        assert descriptor.meaning
        assert descriptor.coverage_semantics


def test_syntax_only_materialized_tables_follow_transitive_semantic_requirements() -> None:
    tables = frozenset(soleaux.contracts.tables.SYNTAX_ONLY_MATERIALIZED_TABLES)

    assert "repository.files" in tables
    assert "repository.engines" in tables
    assert "syntax.declarations" in tables
    assert "authority.owners" in tables
    assert "semantic.symbols" not in tables
    assert "quality.diagnostics" not in tables
    assert "derived.dependencies" not in tables
    assert "derived.impact" not in tables
    assert "quality.standards" in tables
    assert "coverage" in tables


def test_table_selection_honors_hard_exclusion() -> None:
    selected = soleaux.contracts.tables.validate_table_selection(
        ["repository.files", "syntax.imports"],
        ["syntax.imports"],
    )
    assert [descriptor.name for descriptor in selected] == ["repository.files"]
    with _assertions.raises_with_message(soleaux.contracts.tables.UnknownTableError, "not.a.table"):
        soleaux.contracts.tables.validate_table_selection(["not.a.table"], [])
    with pytest.raises(soleaux.contracts.tables.UnknownTableError):
        soleaux.contracts.tables.validate_table_selection(["repository.files"], ["not.a.table"])


def test_envelope_requires_the_schema_version() -> None:
    envelope = soleaux.contracts.results.ResponseEnvelope(
        product_version="0.1.0",
        request_id="r-1",
        status=soleaux.contracts.results.ResultStatus.OK,
        data={"k": "v"},
    )
    assert envelope.schema_version == "soleaux.mcp/v1"
    dumped = envelope.model_dump(mode="json")
    assert dumped["schema_version"] == "soleaux.mcp/v1"


def test_cursor_payload_validation() -> None:
    payload = soleaux.contracts.cursor.CursorPayload(
        process_epoch="epoch-1",
        workspace_id="main",
        snapshot_id="snap-1",
        query_digest="qd",
        limit=10,
        offset=0,
    )
    assert payload.schema_version == "soleaux.cursor/v1"
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.cursor.CursorPayload(
            process_epoch="epoch-1",
            workspace_id="main",
            snapshot_id="snap-1",
            query_digest="qd",
            limit=10,
            offset=-1,
        )
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.cursor.CursorPayload.model_validate(
            {
                "process_epoch": "epoch-1",
                "workspace_id": "main",
                "snapshot_id": "snap-1",
                "query_digest": "qd",
                "limit": 10,
            }
        )


def test_budget_defaults_match_the_contract() -> None:
    rules = soleaux.contracts.budget.PackagedRuleLimits()
    assert (rules.input_paths, rules.input_paths_ceiling) == (256, 4096)
    assert (rules.output_rows, rules.output_rows_ceiling) == (200, 1000)
    assert (rules.concurrent_rules, rules.concurrent_rules_ceiling) == (1, 2)
    request = soleaux.contracts.budget.RequestBudget()
    assert (request.default_timeout_seconds, request.max_timeout_seconds) == (10.0, 55.0)
    worker = soleaux.contracts.budget.StructuralWorkerBudget()
    assert worker.max_completed_jobs == 64
    assert worker.max_rss_bytes == 256 * 1024 * 1024
    session = soleaux.contracts.budget.LspSessionBudget()
    assert session.max_open_documents == 64
    assert session.max_open_bytes == 32 * 1024 * 1024


def test_coverage_zero_rows_language() -> None:
    from soleaux.contracts.coverage import Coverage, FrameStatus, RowFileByteDepthLimits

    coverage = Coverage(
        status=FrameStatus.COMPLETE,
        eligible_files=3,
        examined_files=3,
        parse_failures=0,
        candidate_count=0,
        resolution_attempts=0,
        resolved_count=0,
        unsupported_count=0,
        failed_count=0,
        deadline=datetime.datetime.now(datetime.UTC),
        row_file_byte_depth_limits=RowFileByteDepthLimits(
            max_rows=200, max_files=4096, max_bytes=1024, max_depth=8
        ),
        elapsed_ms=1.5,
    )
    assert coverage.status is FrameStatus.COMPLETE
