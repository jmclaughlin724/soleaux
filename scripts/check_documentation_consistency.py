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
    """Load one required JSON object from the repository."""
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
    """Return the SHA-256 digest for one repository-relative file."""
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    """Fail with a stable diagnostic when a required invariant is false."""
    if not condition:
        raise Failure(message)


def main() -> int:
    """Validate the unified documentation hierarchy and current phase state."""
    manifest = load_json("docs/DOCUMENTATION-MANIFEST.json")
    required_documents = cast(list[str], manifest["required"])
    package_mode = os.environ.get("SOLEAUX_DOCS_PACKAGE_MODE") == "1"
    inherited_locked_docs = {"UNIFIED-MCP-PROFILE.md", "CONTEXT-PACKET-V2.md"}
    for path in required_documents:
        if package_mode and path in inherited_locked_docs:
            continue
        require((ROOT / path).is_file(), f"required documentation missing: {path}")

    status = load_json("PROJECT-STATUS.json")
    current_phase = cast(JsonObject, status["currentPhase"])
    public_mcp = cast(JsonObject, status["publicMcp"])
    locked_contracts = cast(JsonObject, status["lockedContracts"])
    profile_contract = cast(JsonObject, locked_contracts["unifiedMcpProfile"])
    context_contract = cast(JsonObject, locked_contracts["contextPacketV2"])

    require(status["version"] == EXPECTED_VERSION, "status version drift")
    require(
        status["productionClaimAllowed"] is False,
        "production claim must remain false",
    )
    require(current_phase["number"] == 4, "current phase must be 4")
    require(
        current_phase["status"] == "in_progress",
        "Phase 4 status drift",
    )
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

    phase3 = load_json("docs/experiments/phase3/STATUS.json")
    run_authorization = cast(JsonObject, phase3["runAuthorization"])
    require(
        phase3["status"] == "deferred_reconciliation_required",
        "Phase 3 must remain deferred until owner reactivation and three-arm freeze",
    )
    require(phase3["phase3Started"] is False, "Phase 3 must not be marked started")
    require(
        phase3["productionClaimAllowed"] is False,
        "Phase 3 cannot change production claim",
    )
    require(run_authorization["allowed"] is False, "live runs cannot be authorized yet")
    require("control" in phase3, "Phase 3 missing no-Soleaux control")
    require("historicalBaseline" in phase3, "Phase 3 missing historical baseline")
    require("treatment" in phase3, "Phase 3 missing native treatment")

    tasks = load_json("docs/experiments/phase3/TASKS.json")
    task_records = cast(list[JsonObject], tasks["tasks"])
    task_ids = [cast(str, item["id"]) for item in task_records]
    require(tasks["frozen"] is True, "Phase 3 task registry must be frozen")
    require(task_ids == ["P3-T01", "P3-T02", "P3-T03"], "task IDs drift")
    require(len(set(task_ids)) == 3, "duplicate Phase 3 task ID")

    for audit_path in (
        "docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md",
        "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
        "docs/architecture/CAPABILITY-ABSORPTION-MAP.md",
        "docs/operations/BRANCH-CONSOLIDATION-PLAN.md",
    ):
        require((ROOT / audit_path).is_file(), f"transcript reconciliation file missing: {audit_path}")

    results = (ROOT / "docs/experiments/phase3/RESULTS.md").read_text(encoding="utf-8")
    require("Status: NOT RUN — DEFERRED" in results, "Phase 3 results must remain deferred and not run")

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

    status_md = (ROOT / "PROJECT-STATUS.md").read_text(encoding="utf-8")
    require(
        "Phase 2:                     CLOSED" in status_md,
        "human status Phase 2 drift",
    )
    require(
        "Phase 3:                     DEFERRED — RECONCILIATION REQUIRED BEFORE USE" in status_md,
        "human deferred Phase 3 status drift",
    )
    require(
        "productionClaimAllowed:      false" in status_md,
        "human production claim drift",
    )

    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    tasks_md = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    handoff = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    for phase in range(3, 9):
        require(f"Phase {phase}" in roadmap, f"roadmap missing Phase {phase}")
        require(f"Phase {phase}" in tasks_md, f"tasks missing Phase {phase}")
    require("P4-002" in handoff and "audit" in handoff.casefold(), "handoff must identify audit convergence and exact Phase 4 next work")

    gap_registry = load_json("docs/audits/TRANSCRIPT-GAP-REGISTRY.json")
    require(len(cast(list[JsonObject], gap_registry["gaps"])) >= 15, "gap registry incomplete")
    absorption = (ROOT / "docs/architecture/CAPABILITY-ABSORPTION-MAP.md").read_text(encoding="utf-8")
    for capability in ("session.handoff", "memory.propose", "turborepo.affected", "next.list_server_actions"):
        require(capability in absorption, f"capability absorption missing: {capability}")

    summary = {
        "status": "pass",
        "version": EXPECTED_VERSION,
        "currentPhase": 4,
        "productionClaimAllowed": False,
        "publicToolCount": len(EXPECTED_TOOLS),
        "requiredDocuments": len(required_documents),
        "phase3Status": phase3["status"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as exc:
        print(f"documentation consistency failure: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
