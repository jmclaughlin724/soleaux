#!/usr/bin/env python3
"""Fail-closed consistency validation for the Soleaux documentation system."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PROFILE_SHA = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
EXPECTED_CONTEXT_SHA = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"
EXPECTED_TOOLS = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
]
FORBIDDEN_PUBLIC_PHRASES = [
    "soleaux is an agent operating system",
    "soleaux is production-ready",
    "soleaux is production ready",
    "soleaux is generally available",
    "1.0.0-rc.1",
    "fixed local catalog is ten tools",
    "version `0.1.0`",
    "soleaux is a local fastmcp server",
]

JsonObject = dict[str, Any]


class Failure(Exception):
    """Raised when documentation authority or public claims drift."""


def load_json(path: str) -> JsonObject:
    target = ROOT / path
    if not target.is_file():
        raise Failure(f"missing {path}")
    try:
        value: object = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Failure(f"expected JSON object in {path}")
    return cast(JsonObject, value)


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def require_text(path: str, text: str, message: str) -> None:
    require(text in (ROOT / path).read_text(encoding="utf-8"), message)


def main() -> int:
    manifest = load_json("docs/DOCUMENTATION-MANIFEST.json")
    required_documents = cast(list[str], manifest["required"])
    package_mode = os.environ.get("SOLEAUX_DOCS_PACKAGE_MODE") == "1"
    inherited_locked_docs = {"UNIFIED-MCP-PROFILE.md", "CONTEXT-PACKET-V2.md"}
    for path in required_documents:
        if package_mode and path in inherited_locked_docs:
            continue
        require((ROOT / path).is_file(), f"required documentation missing: {path}")

    audit_path = "docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md"
    require(manifest.get("auditOwner") == audit_path, "audit owner drift")
    require(
        manifest.get("gapRegistry") == "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
        "gap registry owner drift",
    )
    require(
        manifest.get("capabilityAbsorptionMap") == "docs/architecture/CAPABILITY-ABSORPTION-MAP.md",
        "capability absorption owner drift",
    )

    status = load_json("PROJECT-STATUS.json")
    current_phase = cast(JsonObject, status["currentPhase"])
    public_mcp = cast(JsonObject, status["publicMcp"])
    locked_contracts = cast(JsonObject, status["lockedContracts"])
    profile_contract = cast(JsonObject, locked_contracts["unifiedMcpProfile"])
    context_contract = cast(JsonObject, locked_contracts["contextPacketV2"])
    branches = cast(JsonObject, status["branches"])

    require(status["version"] == EXPECTED_VERSION, "status version drift")
    require(
        status["productionClaimAllowed"] is False,
        "production claim must remain false",
    )
    require(current_phase["number"] == 4, "current phase must be 4")
    require(current_phase["status"] == "in_progress", "Phase 4 status drift")
    require(branches["canonical"] == "main", "canonical branch drift")
    require(public_mcp["hardCeiling"] == 12, "public tool ceiling drift")
    require(public_mcp["canonicalTools"] == EXPECTED_TOOLS, "canonical tool drift")
    require(
        profile_contract["sha256"] == EXPECTED_PROFILE_SHA,
        "profile digest declaration drift",
    )
    require(
        context_contract["sha256"] == EXPECTED_CONTEXT_SHA,
        "context digest declaration drift",
    )

    if (ROOT / "contracts/unified-mcp-profile-v2.json").is_file():
        require(
            sha256("contracts/unified-mcp-profile-v2.json") == EXPECTED_PROFILE_SHA,
            "profile contract bytes drift",
        )
    if (ROOT / "contracts/context-packet-v2.schema.json").is_file():
        require(
            sha256("contracts/context-packet-v2.schema.json") == EXPECTED_CONTEXT_SHA,
            "context contract bytes drift",
        )

    gap_registry = load_json("docs/audits/TRANSCRIPT-GAP-REGISTRY.json")
    gaps = cast(list[JsonObject], gap_registry["gaps"])
    require(len(gaps) >= 16, "transcript gap registry is incomplete")
    require(
        gap_registry["schemaVersion"] == "soleaux.transcript-gap-registry/v4",
        "gap schema drift",
    )
    native_correctness = next(
        (item for item in gaps if item.get("area") == "native-correctness"),
        None,
    )
    require(native_correctness is not None, "native correctness gap missing")
    closed_tasks = cast(
        list[str],
        cast(JsonObject, native_correctness).get("closedTasks", []),
    )
    require(
        "P4-025" in closed_tasks,
        "PR #7/P4-025 closure is not represented",
    )

    phase3 = load_json("docs/experiments/phase3/STATUS.json")
    run_authorization = cast(JsonObject, phase3["runAuthorization"])
    require(
        phase3["status"] == "deferred_reconciliation_required",
        "Phase 3 must remain deferred and require reconciliation",
    )
    require(
        phase3["phase3Started"] is False,
        "Phase 3 must not be marked started",
    )
    require(
        phase3["productionClaimAllowed"] is False,
        "Phase 3 cannot change production claim",
    )
    require(
        run_authorization["allowed"] is False,
        "live Phase 3 runs cannot be authorized",
    )
    for arm in ("control", "historicalBaseline", "treatment"):
        require(arm in phase3, f"Phase 3 arm missing: {arm}")

    tasks_json = load_json("docs/experiments/phase3/TASKS.json")
    task_records = cast(list[JsonObject], tasks_json["tasks"])
    task_ids = [cast(str, item["id"]) for item in task_records]
    require(
        tasks_json["frozen"] is True,
        "Phase 3 task registry must remain frozen",
    )
    require(
        task_ids == ["P3-T01", "P3-T02", "P3-T03"],
        "Phase 3 task IDs drift",
    )
    require(len(set(task_ids)) == 3, "duplicate Phase 3 task ID")

    require_text(
        "docs/experiments/phase3/RESULTS.md",
        "DEFERRED — RECONCILIATION REQUIRED — NEVER RUN",
        "Phase 3 results must remain explicitly deferred and unrun",
    )

    public_files = [
        "README.md",
        "docs/marketing/MESSAGING.md",
        "docs/marketing/WEBSITE-COPY.md",
        "docs/marketing/FAQ.md",
    ]
    for path in public_files:
        content = (ROOT / path).read_text(encoding="utf-8").casefold()
        for phrase in FORBIDDEN_PUBLIC_PHRASES:
            require(
                phrase not in content,
                f"prohibited public claim in {path}: {phrase}",
            )

    require_text(
        "PROJECT-STATUS.md",
        "Phase 2:                     CLOSED",
        "human Phase 2 status drift",
    )
    require_text(
        "PROJECT-STATUS.md",
        "Phase 3:                     DEFERRED — RECONCILIATION REQUIRED BEFORE USE",
        "human Phase 3 status drift",
    )
    require_text(
        "PROJECT-STATUS.md",
        "Phase 4:                     IN PROGRESS",
        "human Phase 4 status drift",
    )
    require_text(
        "PROJECT-STATUS.md",
        "productionClaimAllowed:      false",
        "human production claim drift",
    )

    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    tasks_md = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    handoff = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    for phase in range(3, 9):
        require(f"Phase {phase}" in roadmap, f"roadmap missing Phase {phase}")
        require(f"Phase {phase}" in tasks_md, f"tasks missing Phase {phase}")
    for task in [
        "P4-017",
        "P4-018",
        "P4-019",
        "P4-020",
        "P4-021",
        "P4-022",
        "P4-023",
        "P4-024",
        "P4-025",
        "P4-026",
        "P5-029",
        "P6-016",
        "P7-013",
        "P8-008",
    ]:
        require(task in tasks_md, f"task registry missing {task}")
    require(
        "P4-017" in handoff,
        "handoff must identify P4-017 as the next implementation task",
    )
    next_work = handoff.split("## Exact next work", maxsplit=1)[-1]
    require(
        "P4-001" not in next_work,
        "handoff still points to completed P4-001",
    )

    summary = {
        "status": "pass",
        "version": EXPECTED_VERSION,
        "canonicalBranch": "main",
        "currentPhase": 4,
        "productionClaimAllowed": False,
        "publicToolCount": len(EXPECTED_TOOLS),
        "requiredDocuments": len(required_documents),
        "gapCount": len(gaps),
        "phase3Status": phase3["status"],
        "nextTask": "P4-017",
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as exc:
        print(f"documentation consistency failure: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
