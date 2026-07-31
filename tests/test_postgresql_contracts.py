"""Reusable behavior contracts for every PostgreSQL implementation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _assertions import raises_with_message
from pydantic import BaseModel, ValidationError

import soleaux.postgresql.contracts as contracts
from soleaux.contracts.coverage import FrameStatus, RowFileByteDepthLimits
from soleaux.contracts.positions import Point, PointRange


def _point(*, byte: int, column: int) -> Point:
    return Point(line=0, column=column, utf16_column=column, byte=byte)


def _range() -> PointRange:
    return PointRange(start=_point(byte=0, column=0), end=_point(byte=8, column=8))


def _source(
    lane: contracts.SourceLane = contracts.SourceLane.DESIRED_STATE,
) -> contracts.SourceAnchor:
    return contracts.SourceAnchor(
        snapshot_id="snapshot-1",
        parser_generation="@libpg-query/parser@17.6.10",
        path="database/schema/example.sql",
        statement_index=0,
        source_lane=lane,
        location=contracts.SourceLocation(
            kind=contracts.LocationKind.EXACT_RANGE,
            range=_range(),
        ),
    )


def _diagnostic() -> contracts.DiagnosticFact:
    return contracts.DiagnosticFact(
        source=_source(),
        origin=contracts.DiagnosticOrigin.PARSER,
        severity=contracts.DiagnosticSeverity.ERROR,
        message="syntax error",
        code="42601",
    )


def test_a1_a2_a17_a18_freeze_exact_runtime_node_parser_delivery() -> None:
    assert contracts.POSTGRESQL_CONTRACT.toolchain.model_dump(mode="json") == {
        "dialect": "PostgreSQL",
        "dialect_major": 17,
        "provider_package": "@postgres-language-server/cli",
        "provider_version": "0.25.4",
        "parser_package": "@libpg-query/parser",
        "parser_version": "17.6.10",
        "parser_postgresql_major": 17,
        "parser_delivery": "runtime_provisioned_node_worker",
    }

    module_path = contracts.__file__
    assert module_path is not None
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module == "pglast"
        or module.startswith("pglast.")
        or module.startswith("soleaux.lsp")
        or "libpg_query" in module
        for module in imported_modules
    )


def test_a3_a4_a5_freeze_offline_connected_and_unavailable_operations() -> None:
    capabilities = {
        capability.operation: capability for capability in contracts.POSTGRESQL_CONTRACT.operations
    }
    assert set(capabilities) == set(contracts.Operation)
    assert {
        operation
        for operation, capability in capabilities.items()
        if capability.support is contracts.OperationSupport.CORE
    } == {
        contracts.Operation.SYMBOL_SEARCH,
        contracts.Operation.DEFINITION,
        contracts.Operation.REFERENCES,
        contracts.Operation.DIAGNOSTICS,
    }
    assert {
        operation
        for operation, capability in capabilities.items()
        if capability.support is contracts.OperationSupport.CONNECTED_ENRICHMENT
    } == {
        contracts.Operation.COMPLETION,
        contracts.Operation.HOVER,
        contracts.Operation.TYPE_CHECK,
        contracts.Operation.PLPGSQL_DIAGNOSTICS,
        contracts.Operation.CODE_ACTION,
        contracts.Operation.RESTART_PROVIDER,
    }
    assert {
        operation
        for operation, capability in capabilities.items()
        if capability.support is contracts.OperationSupport.UNAVAILABLE
    } == {
        contracts.Operation.SIGNATURE_HELP,
        contracts.Operation.IMPLEMENTATION,
        contracts.Operation.CALL_HIERARCHY,
        contracts.Operation.RENAME,
        contracts.Operation.FORMAT_DOCUMENT,
        contracts.Operation.FORMAT_RANGE,
        contracts.Operation.COMMAND_CODE_ACTION,
        contracts.Operation.STATEMENT_EXECUTION,
    }
    with raises_with_message(ValidationError, "requires modes"):
        contracts.OperationCapability(
            operation=contracts.Operation.COMPLETION,
            support=contracts.OperationSupport.CONNECTED_ENRICHMENT,
            semantic_modes=(contracts.SemanticMode.OFFLINE,),
        )


def test_a6_a19_a20_a21_freeze_identity_without_lane_or_type_alias_normalization() -> None:
    int_signature = contracts.RoutineSignature(input_argument_types=("int",))
    integer_signature = contracts.RoutineSignature(input_argument_types=("integer",))
    int_routine = contracts.RoutineIdentity(
        kind=contracts.ObjectKind.FUNCTION,
        schema="public",
        name="lookup",
        signature=int_signature,
    )
    integer_routine = contracts.RoutineIdentity(
        kind=contracts.ObjectKind.FUNCTION,
        schema="public",
        name="lookup",
        signature=integer_signature,
    )

    assert int_routine != integer_routine
    assert int_signature.model_dump(mode="json") == {
        "input_argument_types": ["int"],
        "type_name_comparison": "as_written",
    }
    assert "source_lane" not in int_routine.model_dump(mode="json")
    assert (
        contracts.DeclarationFact(
            source=_source(contracts.SourceLane.DESIRED_STATE),
            action=contracts.DeclarationAction.CREATE,
            identity=int_routine,
        ).identity
        == contracts.DeclarationFact(
            source=_source(contracts.SourceLane.MIGRATION_HISTORY),
            action=contracts.DeclarationAction.CREATE,
            identity=int_routine,
        ).identity
    )
    with pytest.raises(ValidationError):
        contracts.RoutineSignature.model_validate(
            {
                "input_argument_types": ["integer"],
                "output_argument_types": ["text"],
            }
        )


def test_scoped_identity_is_relation_owned_and_round_trips_through_declarations() -> None:
    relation = contracts.ObjectIdentity(
        kind=contracts.ObjectKind.TABLE,
        schema="app",
        name="accounts",
    )
    identity = contracts.ScopedObjectIdentity(
        kind=contracts.ObjectKind.POLICY,
        relation=relation,
        name="tenant",
    )
    declaration = contracts.DeclarationFact(
        source=_source(),
        action=contracts.DeclarationAction.CREATE,
        identity=identity,
    )

    restored = contracts.DeclarationFact.model_validate(
        declaration.model_dump(mode="json", by_alias=True)
    )
    assert restored == declaration
    assert isinstance(restored.identity, contracts.ScopedObjectIdentity)
    with pytest.raises(ValidationError):
        contracts.ScopedObjectIdentity.model_validate(
            {
                "kind": "index",
                "relation": relation.model_dump(mode="json", by_alias=True),
                "name": "accounts_idx",
            }
        )


def test_a7_a12_freeze_ranges_and_public_reference_fact_shape() -> None:
    source = _source()
    statement = contracts.StatementFact(source=source, statement_kind="SelectStmt")
    reference = contracts.ReferenceFact(
        source=source,
        reference_kind=contracts.ReferenceKind.RELATION,
        name_parts=("public", "accounts"),
    )
    call = contracts.CallFact(
        source=source,
        call_kind=contracts.CallKind.FUNCTION,
        callee_parts=("public", "lookup"),
        argument_count=1,
    )
    diagnostic = _diagnostic()

    assert statement.source.location.range == _range()
    assert set(reference.model_dump(mode="json")) == {
        "source",
        "reference_kind",
        "name_parts",
        "resolution",
    }
    assert reference.resolution.state is contracts.ResolutionState.CANDIDATE
    assert call.resolution.state is contracts.ResolutionState.CANDIDATE
    assert diagnostic.source.location.kind is contracts.LocationKind.EXACT_RANGE
    with raises_with_message(ValidationError, "exact scanner-derived range"):
        contracts.StatementFact(
            source=source.model_copy(
                update={
                    "location": contracts.SourceLocation(
                        kind=contracts.LocationKind.START_ONLY,
                        point=_point(byte=0, column=0),
                    )
                }
            ),
            statement_kind="SelectStmt",
        )
    with raises_with_message(ValidationError, "does not match"):
        contracts.SourceLocation(
            kind=contracts.LocationKind.LINE_ONLY,
            point=_point(byte=0, column=0),
        )


def test_a8_freezes_distinct_error_kinds() -> None:
    assert tuple(contracts.ErrorKind) == (
        contracts.ErrorKind.PARSE,
        contracts.ErrorKind.RESOLUTION,
        contracts.ErrorKind.PROVIDER,
        contracts.ErrorKind.DATABASE,
        contracts.ErrorKind.TIMEOUT,
        contracts.ErrorKind.TRUNCATION,
    )
    error = contracts.AnalysisError(
        kind=contracts.ErrorKind.TIMEOUT,
        message="parser deadline reached",
        operation=contracts.Operation.DIAGNOSTICS,
        retryable=True,
    )
    assert error.model_dump(mode="json")["kind"] == "timeout"
    with pytest.raises(ValidationError):
        contracts.AnalysisError.model_validate(
            {
                "kind": "timeout",
                "message": "parser deadline reached",
                "raw_exception": {"type": "TimeoutError"},
            }
        )


def test_a9_reuses_all_generic_coverage_states_including_changed_snapshot() -> None:
    assert contracts.POSTGRESQL_CONTRACT.coverage_states == (
        FrameStatus.COMPLETE,
        FrameStatus.PARTIAL,
        FrameStatus.TRUNCATED,
        FrameStatus.UNSUPPORTED,
        FrameStatus.FAILED,
        FrameStatus.CHANGED_DURING_ANALYSIS,
    )


def test_a10_freezes_all_source_provenance_lanes() -> None:
    assert tuple(contracts.SourceLane) == (
        contracts.SourceLane.UNCLASSIFIED,
        contracts.SourceLane.DESIRED_STATE,
        contracts.SourceLane.MIGRATION_HISTORY,
        contracts.SourceLane.TEST,
        contracts.SourceLane.GENERATED,
        contracts.SourceLane.FIXTURE,
    )
    for lane in contracts.SourceLane:
        assert _source(lane).source_lane is lane


def test_a11_freezes_parser_lsp_file_row_byte_depth_and_time_budgets() -> None:
    budgets = contracts.POSTGRESQL_CONTRACT.budgets
    assert budgets.frame_limits == RowFileByteDepthLimits(
        max_rows=200,
        max_files=4096,
        max_bytes=32 * 1024 * 1024,
        max_depth=8,
    )
    assert budgets.max_file_bytes == 4 * 1024 * 1024
    assert budgets.analysis_timeout_seconds == 10.0
    assert budgets.parser_timeout_seconds == 15.0
    assert budgets.lsp_timeout_seconds == 5.0


def test_a14_a15_a16_models_are_closed_frozen_and_reject_raw_boundary_objects() -> None:
    model_types: list[type[BaseModel]] = []
    for value in vars(contracts).values():
        if (
            isinstance(value, type)
            and issubclass(value, BaseModel)
            and value.__module__ == contracts.__name__
        ):
            model_types.append(value)

    assert model_types
    for model_type in model_types:
        assert model_type.model_config.get("frozen") is True
        assert model_type.model_config.get("extra") == "forbid"

    with pytest.raises(ValidationError):
        contracts.POSTGRESQL_CONTRACT.__setattr__("type_name_comparison", "normalized")

    raw_parser_payload = contracts.StatementFact(
        source=_source(),
        statement_kind="SelectStmt",
    ).model_dump(mode="python")
    raw_parser_payload["raw_parser_node"] = object()
    with pytest.raises(ValidationError):
        contracts.StatementFact.model_validate(raw_parser_payload)

    raw_provider_payload = _diagnostic().model_dump(mode="python")
    raw_provider_payload["raw_provider_diagnostic"] = object()
    with pytest.raises(ValidationError):
        contracts.DiagnosticFact.model_validate(raw_provider_payload)


def test_a22_a23_distinguish_applicability_and_derive_availability_from_producers() -> None:
    applicability = {
        (item.derived_table, item.prerequisite): item.applicability
        for item in contracts.POSTGRESQL_CONTRACT.derived_prerequisites
    }
    assert applicability == {
        ("derived.dependencies", "semantic.imports"): contracts.Applicability.NOT_APPLICABLE,
        ("derived.dependencies", "semantic.references"): contracts.Applicability.APPLICABLE,
        ("derived.consumers", "semantic.imports"): contracts.Applicability.NOT_APPLICABLE,
        ("derived.consumers", "semantic.references"): contracts.Applicability.APPLICABLE,
        ("derived.consumers", "semantic.calls"): contracts.Applicability.APPLICABLE,
    }
    assert contracts.Applicability.NOT_APPLICABLE is not contracts.Applicability.UNSUPPORTED
    assert all(
        item.preserves_semantic_requirement
        for item in contracts.POSTGRESQL_CONTRACT.derived_prerequisites
    )
    assert contracts.POSTGRESQL_CONTRACT.table_availability_source == "producer_capabilities"
