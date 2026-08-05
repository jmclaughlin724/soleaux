#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PHASE = 5
PROFILE_SHA = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
CONTEXT_SHA = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"
CANONICAL_TOOLS = [
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


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def task_id_from_line(line: str) -> str | None:
    marker_start = line.find("**")
    if marker_start < 0:
        return None
    marker_end = line.find("**", marker_start + 2)
    if marker_end < 0:
        return None
    candidate = line[marker_start + 2 : marker_end]
    prefix, separator, number = candidate.rpartition("-")
    if separator != "-" or len(number) != 3 or not number.isdigit():
        return None
    if prefix in {"BR", "DOC"}:
        return candidate
    if prefix.startswith("P") and prefix[1:].isdigit():
        return candidate
    return None


manifest = load_json("docs/DOCUMENTATION-MANIFEST.json")
for relative in manifest["required"]:
    if not (ROOT / relative).is_file():
        fail(f"required documentation missing: {relative}")

status = load_json("PROJECT-STATUS.json")
if status.get("version") != EXPECTED_VERSION:
    fail("status version drifted")
if status.get("productionClaimAllowed") is not False:
    fail("production claim must remain false")
if status.get("currentPhase", {}).get("number") != EXPECTED_PHASE:
    fail("current phase must be 5")
if status.get("currentPhase", {}).get("status") != "in_progress":
    fail("Phase 5 must be in progress")
phase_by_number = {phase["number"]: phase for phase in status["phases"]}
if phase_by_number[4]["status"] != "closed":
    fail("Phase 4 must be closed")
if phase_by_number[3]["status"] != "deferred_reconciliation_required":
    fail("Phase 3 must remain deferred")
if status["publicMcp"]["hardCeiling"] != 12:
    fail("public ceiling drifted")
if status["publicMcp"]["canonicalTools"] != CANONICAL_TOOLS:
    fail("canonical tool order drifted")

if sha256("native/contracts/unified-mcp-profile-v2.json") != PROFILE_SHA:
    fail("unified profile digest drifted")
if sha256("native/contracts/context-packet-v2.schema.json") != CONTEXT_SHA:
    fail("Context Packet V2 digest drifted")

phase4 = load_json("PHASE4-CLOSURE-RECEIPT.json")
alpha = load_json("PHASE4-ALPHA-CLOSURE-RECEIPT.json")
independent = load_json("PHASE4-INDEPENDENT-VERIFICATION.json")
if phase4.get("status") != "closed":
    fail("Phase 4 closure receipt is not closed")
if alpha.get("conclusion") != "success":
    fail("Phase 4 alpha receipt is not successful")
if independent.get("status") != "pass":
    fail("Phase 4 independent verification did not pass")
for value in (phase4, alpha, independent):
    if value.get("productionClaimAllowed") is not False:
        fail("Phase 4 evidence changed the production claim")
    if value.get("publicToolCeiling") != 12:
        fail("Phase 4 evidence changed the public ceiling")

expected_p4 = {f"P4-{number:03d}" for number in range(1, 27)}
if set(phase4.get("closedTasks", [])) != expected_p4:
    fail("Phase 4 closure task set is incomplete")

branch_report = load_json("docs/operations/BRANCH-CONSOLIDATION-2026-08-05.json")
if branch_report.get("status") != "pass":
    fail("branch consolidation did not pass")
if branch_report.get("canonicalBranch") != "main":
    fail("branch consolidation canonical branch drifted")

gap = load_json("docs/audits/TRANSCRIPT-GAP-REGISTRY.json")
if gap.get("schemaVersion") != "soleaux.transcript-gap-registry/v5":
    fail("gap registry schema drifted")
gaps = {item["id"]: item for item in gap["gaps"]}
if gaps["GAP-003"]["status"] != "closed":
    fail("native correctness gap must be closed")
if set(gaps["GAP-003"].get("closedTasks", [])) != {
    f"P4-{number:03d}" for number in range(17, 27)
}:
    fail("native correctness closed-task set drifted")
if gaps["GAP-006"]["status"] != "closed":
    fail("CLI/service/IPC gap must be closed")

phase3 = load_json("docs/experiments/phase3/STATUS.json")
if phase3.get("status") != "deferred_reconciliation_required":
    fail("Phase 3 status drifted")
if phase3.get("phase3Started") is not False:
    fail("Phase 3 must remain unstarted")
if phase3.get("productionClaimAllowed") is not False:
    fail("Phase 3 changed the production claim")
arms = [arm["id"] for arm in phase3.get("arms", [])]
if arms != ["control_no_soleaux", "historical_python", "native_treatment"]:
    fail("Phase 3 arms drifted")
phase3_tasks = load_json("docs/experiments/phase3/TASKS.json")
if [task["id"] for task in phase3_tasks["tasks"]] != [
    "P3-T01",
    "P3-T02",
    "P3-T03",
]:
    fail("Phase 3 task registry drifted")

tasks_text = (ROOT / "TASKS.md").read_text(encoding="utf-8")
task_ids = [
    task_id
    for line in tasks_text.splitlines()
    if (task_id := task_id_from_line(line)) is not None
]
if len(task_ids) != len(set(task_ids)):
    fail("duplicate task IDs found")
for task in sorted(expected_p4):
    if f"- [x] **{task}**" not in tasks_text:
        fail(f"closed Phase 4 task is unchecked: {task}")
if "- [ ] **P5-001**" not in tasks_text:
    fail("P5-001 must be the first open implementation task")

required_markers = {
    "README.md": ["Phase 4", "Phase 5", "productionClaimAllowed"],
    "PROJECT-STATUS.md": [
        "Phase 4:                     CLOSED",
        "Phase 5:                     IN PROGRESS",
    ],
    "ROADMAP.md": ["current_phase=5", "Phase 4 — closed", "Phase 5 — current"],
    "TASKS.md": ["current_phase=5", "## Current phase", "P5-001"],
    "HANDOFF.md": ["Phase 4:                     CLOSED", "P5-001"],
    "AGENTS.md": [
        "Phase 4 is closed",
        "Phase 5 is the active implementation phase",
    ],
    "CHANGELOG.md": ["Phase 4", "Phase 5"],
    "RELEASE-CHECKLIST.md": [
        "Phase 4:                     CLOSED",
        "Phase 5:                     IN PROGRESS",
    ],
}
for relative, markers in required_markers.items():
    text = (ROOT / relative).read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            fail(f"{relative} missing marker: {marker}")

public_files = [
    "README.md",
    "docs/marketing/MESSAGING.md",
    "docs/marketing/WEBSITE-COPY.md",
    "docs/marketing/FAQ.md",
]
for relative in public_files:
    text = (ROOT / relative).read_text(encoding="utf-8").lower()
    for phrase in (
        "soleaux is production-ready",
        "soleaux is generally available",
        "guaranteed context reduction",
        "universal native session resume",
        "works with every client",
    ):
        if phrase in text:
            fail(f"prohibited public claim in {relative}: {phrase}")

print(
    json.dumps(
        {
            "schemaVersion": "soleaux.documentation-consistency/v3",
            "version": EXPECTED_VERSION,
            "currentPhase": EXPECTED_PHASE,
            "nextTask": "P5-001",
            "phase3Status": phase3["status"],
            "phase4Status": phase_by_number[4]["status"],
            "requiredDocuments": len(manifest["required"]),
            "publicTools": len(CANONICAL_TOOLS),
            "productionClaimAllowed": False,
            "status": "pass",
        },
        indent=2,
        sort_keys=True,
    )
)
