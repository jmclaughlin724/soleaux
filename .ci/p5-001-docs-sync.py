#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-08-05"
PR = 36
PRODUCT_SOURCE = "1744424444d08d6ee380dc40c948db86a626ee04"
PR_HEAD = "6ffd0f8af9ff44b0dcb5392f3e52963e309c1cc5"
MERGE = "f231b86f581b7f3d5d081ed4b8d235a72758342a"
PARALLEL_RUN = 31068987326
FINAL_VALIDATION_RUN = 31070227364
PUBLICATION_RUN = 31071131814
EXACT_SERVICE_RUN = 31071496501
POST_MERGE_CI_RUN = 31072279038
POST_MERGE_DOCS_RUN = 31072279070
ARTIFACT_ID = 8955671128
ARTIFACT_SHA = "7cee5ddd84571718119447322fcf88a9ca2161c556b9af1a93ea4794ebd39dc8"


def path(name: str) -> Path:
    return ROOT / name


def read(name: str) -> str:
    return path(name).read_text(encoding="utf-8")


def write(name: str, text: str) -> None:
    path(name).write_text(text, encoding="utf-8")


def replace(name: str, before: str, after: str, expected: int = 1) -> None:
    text = read(name)
    count = text.count(before)
    if count != expected:
        raise SystemExit(
            f"{name}: expected {expected} occurrences, found {count}: {before!r}"
        )
    write(name, text.replace(before, after))


receipt = {
    "schemaVersion": "soleaux.phase5-task-receipt/v1",
    "task": "P5-001",
    "status": "closed",
    "date": DATE,
    "productSourceCommit": PRODUCT_SOURCE,
    "pullRequestHeadCommit": PR_HEAD,
    "mergeCommit": MERGE,
    "pullRequest": PR,
    "parallelWorkflowRunId": PARALLEL_RUN,
    "finalValidationWorkflowRunId": FINAL_VALIDATION_RUN,
    "publicationWorkflowRunId": PUBLICATION_RUN,
    "exactServiceWorkflowRunId": EXACT_SERVICE_RUN,
    "postMergeCiWorkflowRunId": POST_MERGE_CI_RUN,
    "postMergeDocumentationWorkflowRunId": POST_MERGE_DOCS_RUN,
    "artifactId": ARTIFACT_ID,
    "artifactSha256": ARTIFACT_SHA,
    "artifactIntegrity": "independently_verified",
    "reviewThreadsResolved": 24,
    "platforms": ["linux-x86_64", "macos-arm64"],
    "validatedClientKinds": ["cli", "desktop", "editor", "adapter"],
    "capabilities": [
        "one daemon-owned canonical workspace registry",
        "durable client registrations and client/workspace bindings",
        "lease heartbeat, expiry, disconnect, unbind, forget, and revival",
        "canonical UTF-8 workspace identity with non-UTF-8 fail-closed rejection",
        "workspace trust and client compatibility read-only enforcement",
        "atomic writer-owned registration, binding, heartbeat, disconnect, and forget operations",
        "bounded read pages and serialized-size-bounded mutation child responses",
        "canonical attach marker UUID and transactional attach/revert convergence",
        "legacy provisioning manifest scope compatibility",
        "state persistence across daemon restart",
    ],
    "tests": {
        "python": {"passed": 929, "skipped": 17, "failed": 0},
        "ruff": "pass",
        "pyright": "pass",
        "rustfmt": "pass",
        "cargoCheck": "pass",
        "clippyWarningsDenied": "pass",
        "cargoTest": "pass",
        "releaseBuild": "pass",
        "phase1Smoke": "pass",
        "phase2Smoke": "pass",
        "p5RegistryLifecycleSmoke": "pass",
        "cargoAudit": "pass",
    },
    "publicToolCeiling": 12,
    "productionClaimAllowed": False,
    "conclusion": "success",
}
write("P5-001-CLOSURE-RECEIPT.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")

status = json.loads(read("PROJECT-STATUS.json"))
status["asOfDate"] = DATE
status["branches"]["consolidation"] = (
    status["branches"]["consolidation"].rstrip(".")
    + f". P5-001 workspace/client registry convergence was merged through PR #{PR} at {MERGE}."
)
status["currentPhase"]["primaryBlocker"] = (
    "Execute P5-002 through P5-029: live client capability matrices, canonical lifecycle and "
    "materialization, intelligence depth, SDK/provider interfaces, design-partner validation, "
    "and an exact Phase 5 receipt."
)
status["nextActions"] = [
    "Execute P5-002 through P5-006: live Claude Code, Claude Desktop, Codex, OpenCode, Cursor, and generic MCP-host capability matrices.",
    *[
        value
        for value in status["nextActions"]
        if not value.startswith("Execute P5-002 through P5-006:")
    ],
]
phase5 = next(phase for phase in status["phases"] if phase["number"] == 5)
phase5["completedTasks"] = sorted(set(phase5.get("completedTasks", [])) | {"P5-001"})
phase5["currentTask"] = "P5-002"
phase5["receipts"] = sorted(
    set(phase5.get("receipts", [])) | {"P5-001-CLOSURE-RECEIPT.json"}
)
status["phase5Evidence"] = {
    "completedTask": "P5-001",
    "receipt": "P5-001-CLOSURE-RECEIPT.json",
    "productSourceCommit": PRODUCT_SOURCE,
    "pullRequestHeadCommit": PR_HEAD,
    "mergeCommit": MERGE,
    "pullRequest": PR,
    "parallelWorkflowRunId": PARALLEL_RUN,
    "finalValidationWorkflowRunId": FINAL_VALIDATION_RUN,
    "publicationWorkflowRunId": PUBLICATION_RUN,
    "exactServiceWorkflowRunId": EXACT_SERVICE_RUN,
    "postMergeCiWorkflowRunId": POST_MERGE_CI_RUN,
    "postMergeDocumentationWorkflowRunId": POST_MERGE_DOCS_RUN,
    "artifactId": ARTIFACT_ID,
    "artifactSha256": ARTIFACT_SHA,
    "currentTask": "P5-002",
    "status": "in_progress",
}
write("PROJECT-STATUS.json", json.dumps(status, indent=2, sort_keys=True) + "\n")

replace("TASKS.md", "- [ ] **P5-001**", "- [x] **P5-001**")
replace(
    "TASKS.md",
    "- [ ] **P5-006** Cursor and generic MCP-host verification.\n",
    "- [ ] **P5-006** Cursor and generic MCP-host verification.\n\n"
    "P5-001 evidence: [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json). "
    "The next open implementation task is P5-002.\n",
)

replace(
    "HANDOFF.md",
    "8. [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)\n9. the locked MCP and Context Packet contracts.",
    "8. [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json)\n"
    "9. [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)\n"
    "10. the locked MCP and Context Packet contracts.",
)
replace(
    "HANDOFF.md",
    "1. **P5-001** — converge the installed service and workspace registry across concurrent CLI, desktop, editor, and adapter clients.\n2. **P5-002 through P5-006**",
    "1. **P5-002 through P5-006**",
)
replace(
    "HANDOFF.md",
    "Otherwise implement P5-001 and continue top-down.",
    "P5-001 is closed and must not be reimplemented. Continue with P5-002 and proceed top-down.",
)

replace(
    "AGENTS.md",
    "Execute P5-001 through P5-029 top-down unless a dependency requires a documented parallel workstream.",
    "P5-001 is closed. Execute P5-002 through P5-029 top-down unless a dependency requires a documented parallel workstream.",
)

replace(
    "PROJECT-STATUS.md",
    "## Locked invariants\n",
    f"""## Phase 5 progress

P5-001 is closed on product source `{PRODUCT_SOURCE}` and merge `{MERGE}`. The daemon-owned registry now converges canonical workspace identity, CLI/desktop/editor/adapter registrations, leases, trust and compatibility restrictions, restart persistence, bounded IPC pages and mutation summaries, and transactional attach/revert behavior across Linux and macOS.

Evidence: [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json). The next open implementation task is **P5-002**.

## Locked invariants
""",
)

replace(
    "ROADMAP.md",
    "- Phase 4 closure is recorded in [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json).\n",
    "- Phase 4 closure is recorded in [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json).\n"
    f"- PR #{PR} closed P5-001 workspace/client registry convergence; evidence is recorded in [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json).\n",
)
replace(
    "ROADMAP.md",
    "## Phase 5 — current\n",
    "## Phase 5 — current\n\nP5-001 is closed. The next open task is **P5-002**, followed by the remaining platform matrices and Phase 5 lifecycle and intelligence work.\n",
)

replace(
    "README.md",
    "Evidence:\n\n- [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json)",
    "P5-001 is also closed: the daemon-owned workspace/client registry, restart persistence, trust and compatibility safe mode, bounded IPC responses, and transactional attach/revert convergence passed Linux and macOS gates. The next task is P5-002.\n\nEvidence:\n\n- [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json)\n- [`PHASE4-CLOSURE-RECEIPT.json`](PHASE4-CLOSURE-RECEIPT.json)",
)

replace(
    "CHANGELOG.md",
    "### Phase 5 — activated\n\n",
    f"""### Phase 5 — P5-001 registry convergence closed — {DATE}

- Merged PR #{PR} with one daemon-owned canonical workspace/client registry across CLI, desktop, editor, and adapter clients.
- Added atomic lease, heartbeat, binding, disconnect, forget, revival, trust-downgrade, compatibility-safe-mode, pagination, and mutation-response bounds.
- Unified public `attach` and `adopt --revert` with the canonical registry, canonical UUID markers, rollback recovery, and legacy manifest-scope compatibility.
- Passed the complete Python conformance suite, full native Rust gates, Linux/macOS lifecycle smokes, and Cargo audit while retaining ceiling 12 and `productionClaimAllowed=false`.
- Persisted [`P5-001-CLOSURE-RECEIPT.json`](P5-001-CLOSURE-RECEIPT.json).

### Phase 5 — activated

""",
)

replace(
    "RELEASE-CHECKLIST.md",
    "- [ ] Installed service/workspace registry converges across concurrent client classes.",
    "- [x] Installed service/workspace registry converges across concurrent client classes. Evidence: `P5-001-CLOSURE-RECEIPT.json`.",
)

manifest = json.loads(read("docs/DOCUMENTATION-MANIFEST.json"))
required = manifest["required"]
if "P5-001-CLOSURE-RECEIPT.json" not in required:
    required.append("P5-001-CLOSURE-RECEIPT.json")
manifest["required"] = sorted(required)
write("docs/DOCUMENTATION-MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

gaps = json.loads(read("docs/audits/TRANSCRIPT-GAP-REGISTRY.json"))
gaps["asOfDate"] = DATE
gap4 = next(item for item in gaps["gaps"] if item["id"] == "GAP-004")
gap4["closedTasks"] = sorted(set(gap4.get("closedTasks", [])) | {"P5-001"})
gap4["status"] = "phase5_registry_closed_lifecycle_open"
gap4["summary"] = (
    "Phase 4 implemented canonical ownership, persistence, migrations, leases, audit, retention, "
    "backup, restore, and repair. P5-001 closed the daemon-owned concurrent workspace/client "
    "registry and transactional attachment convergence. Phase 5 still owns full cross-client "
    "session/history/memory/handoff/run lifecycle and materialization."
)
source_label = f"merged PR #{PR} closing P5-001"
if source_label not in gaps["reviewedSources"]:
    gaps["reviewedSources"].append(source_label)
write(
    "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
    json.dumps(gaps, indent=2, sort_keys=True) + "\n",
)

checker = read("scripts/check_documentation_consistency.py")
checker = checker.replace(
    'independent = load_json("PHASE4-INDEPENDENT-VERIFICATION.json")\n',
    'independent = load_json("PHASE4-INDEPENDENT-VERIFICATION.json")\n'
    'p5_001 = load_json("P5-001-CLOSURE-RECEIPT.json")\n',
)
checker = checker.replace(
    "for value in (phase4, alpha, independent):\n",
    'if p5_001.get("status") != "closed" or p5_001.get("task") != "P5-001":\n'
    '    fail("P5-001 receipt is not closed")\n'
    'if p5_001.get("productionClaimAllowed") is not False or p5_001.get("publicToolCeiling") != 12:\n'
    '    fail("P5-001 receipt changed a locked invariant")\n'
    "for value in (phase4, alpha, independent):\n",
)
checker = checker.replace(
    'if not re.search(r"- \\[ \\] \\*\\*P5-001\\*\\*", tasks_text):\n    fail("P5-001 must be the first open implementation task")\n',
    'if not re.search(r"- \\[x\\] \\*\\*P5-001\\*\\*", tasks_text):\n'
    '    fail("P5-001 must be closed")\n'
    'if not re.search(r"- \\[ \\] \\*\\*P5-002\\*\\*", tasks_text):\n'
    '    fail("P5-002 must be the first open implementation task")\n',
)
checker = checker.replace(
    '"TASKS.md": ["current_phase=5", "## Current phase", "P5-001"],\n'
    '    "HANDOFF.md": ["Phase 4:                     CLOSED", "P5-001"],\n',
    '"TASKS.md": ["current_phase=5", "## Current phase", "P5-001", "P5-002"],\n'
    '    "HANDOFF.md": ["Phase 4:                     CLOSED", "P5-001", "P5-002"],\n',
)
checker = checker.replace('"nextTask": "P5-001",', '"nextTask": "P5-002",')
write("scripts/check_documentation_consistency.py", checker)

print("P5-001 documentation convergence applied")
