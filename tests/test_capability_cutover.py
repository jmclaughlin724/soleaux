"""Accepted replacement ledgers remain complete after the live cutover."""

from __future__ import annotations

import json
import pathlib

import _assertions
import _host_root

import soleaux
import soleaux.surface

REPOSITORY_ROOT = _host_root.require_host_root()
EVIDENCE_ROOT = REPOSITORY_ROOT / "plans/2026-07-22-soleaux-final/evidence"
CONTRACT_FIXTURE_ROOT = pathlib.Path(__file__).parent / "fixtures/contracts"

type CapabilityTarget = tuple[str, str | None, bool | None]

_EXPECTED_CCLSP_TARGETS: dict[str, tuple[CapabilityTarget, ...]] = {
    "find_definition": (("navigate", "definition", None),),
    "find_implementation": (("navigate", "implementation", None),),
    "find_references": (("navigate", "references", None),),
    "find_workspace_symbols": (("search", None, None),),
    "format_document": (("preview", "format_document", None),),
    "format_range": (("preview", "format_range", None),),
    "get_code_actions": (
        ("inspect", "code_actions", None),
        ("preview", "code_action", None),
    ),
    "get_completions": (("inspect", "completion", None),),
    "get_diagnostics": (("inspect", "diagnostics", None),),
    "get_hover": (("navigate", "hover", None),),
    "get_incoming_calls": (("navigate", "incoming_calls", None),),
    "get_outgoing_calls": (("navigate", "outgoing_calls", None),),
    "get_signature_help": (("inspect", "signature_help", None),),
    "prepare_call_hierarchy": (("navigate", "call_hierarchy", None),),
    "rename_symbol": (("preview", "rename", None),),
    "rename_symbol_strict": (("preview", "rename", True),),
    "restart_server": (("restart_lsp", None, None),),
}


def _load(name: str) -> dict[str, object]:
    return _assertions.object_mapping(
        json.loads(EVIDENCE_ROOT.joinpath(name).read_text(encoding="utf-8"))
    )


def _load_contract(name: str) -> dict[str, object]:
    return _assertions.object_mapping(
        json.loads(CONTRACT_FIXTURE_ROOT.joinpath(name).read_text(encoding="utf-8"))
    )


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    return [_assertions.object_mapping(row) for row in _assertions.object_list(payload[key])]


def _targets(row: dict[str, object]) -> tuple[CapabilityTarget, ...]:
    targets: list[CapabilityTarget] = []
    for raw_target in _assertions.object_list(row["soleaux_targets"]):
        target = _assertions.object_mapping(raw_target)
        tool = target.get("tool")
        operation = target.get("operation")
        strict = target.get("strict")
        assert set(target) <= {"tool", "operation", "strict"}
        assert isinstance(tool, str)
        assert operation is None or isinstance(operation, str)
        assert strict is None or isinstance(strict, bool)
        targets.append((tool, operation, strict))
    return tuple(targets)


def test_cclsp_capability_ledger_maps_all_seventeen_tools() -> None:
    payload = _load_contract("cclsp-capability-map.json")
    rows = _rows(payload, "capabilities")
    tool_names = set(soleaux.surface.tool_names())

    assert tool_names == {
        "describe",
        "search",
        "context",
        "query",
        "owners",
        "navigate",
        "inspect",
        "preview",
        "edit",
        "restart_lsp",
    }

    targets_by_legacy_tool: dict[str, tuple[CapabilityTarget, ...]] = {}
    for row in rows:
        legacy_tool = row.get("legacy_tool")
        assert isinstance(legacy_tool, str)
        targets = _targets(row)
        assert all(tool in tool_names for tool, _operation, _strict in targets)
        targets_by_legacy_tool[legacy_tool] = targets

    assert targets_by_legacy_tool == _EXPECTED_CCLSP_TARGETS


def test_ast_grep_mcp_ledger_preserves_the_cli_owner() -> None:
    payload = _load("ast-grep-mcp-capability-map.json")
    rows = _rows(payload, "capabilities")
    dispositions = {row["disposition"] for row in rows}

    assert len(rows) == 5
    assert dispositions == {"partially_replaced", "replaced", "retired_from_soleaux"}
    for row in rows:
        assert isinstance(row.get("soleaux_target") or row.get("replacement_owner"), str)

    workspace = (REPOSITORY_ROOT / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8"))
    assert '"@ast-grep/cli": "0.44.1"' in workspace
    assert package["devDependencies"]["@ast-grep/cli"] == "catalog:"
    assert "ast-grep:validate" in package["scripts"]


def test_codeatlas_ledger_has_no_unresolved_human_workflow() -> None:
    payload = _load("codeatlas-capability-survey.json")
    rows = _rows(payload, "capability_ledger")
    dispositions = {row["disposition"] for row in rows}

    assert len(rows) == 8
    assert dispositions == {
        "deliberately_retired",
        "replaced",
        "replaced_when_repository_policy_exists",
    }
    assert all(row.get("disposition") != "unresolved" for row in rows)
    replaced = [
        row
        for row in rows
        if isinstance(row["disposition"], str) and row["disposition"].startswith("replaced")
    ]
    assert all(isinstance(row.get("soleaux_target"), str) for row in replaced)

    validation = _assertions.object_mapping(payload["survey_validation"])
    consumer_search = _assertions.object_mapping(validation["bounded_direct_consumer_search"])
    assert consumer_search["application_tool_calls"] == 0
    assert not (REPOSITORY_ROOT / ".codeatlas").exists()
    assert not (REPOSITORY_ROOT / ".codeatlas-sa").exists()
