#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.4.0-dev.5"


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def import_reviewed(path: str) -> None:
    refs = [
        "origin/audit/transcript-gap-plan-v5-0.4.0-dev.5",
        "origin/audit/transcript-gap-plan-v4-0.4.0-dev.5",
        "origin/audit/transcript-gap-plan-0.4.0-dev.5",
    ]
    for ref in refs:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            target = ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result.stdout)
            return
    raise SystemExit(f"reviewed audit artifact is not available on known branches: {path}")


for reviewed in [
    "docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md",
    "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
    "docs/architecture/CAPABILITY-ABSORPTION-MAP.md",
]:
    import_reviewed(reviewed)

status = {
    "schemaVersion": "soleaux.project-status/v3",
    "asOf": "2026-08-04",
    "product": "Soleaux",
    "definition": "Unified repository intelligence",
    "version": VERSION,
    "canonicalBranch": "main",
    "nativeSourceRoot": "native",
    "productionClaimAllowed": False,
    "productionReadinessClaim": "prohibited",
    "publicMcp": {
        "hardCeiling": 12,
        "canonicalOrder": [
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
        ],
        "optionalSubstitutionCandidates": [
            "parse_and_validate_postgres_sql",
            "turborepo.packages",
            "next.get_routes",
        ],
    },
    "lockedContracts": {
        "unifiedMcpProfileSha256": "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc",
        "contextPacketV2Sha256": "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f",
    },
    "phases": {
        "0": {"status": "closed", "evidence": "PHASE0-NATIVE-GATE-RECEIPT.json"},
        "1": {"status": "closed", "evidence": "PHASE1-NATIVE-GATE-RECEIPT.json"},
        "2": {"status": "closed", "evidence": "PHASE2-CLOSURE-RECEIPT.json"},
        "3": {
            "status": "deferred_reconciliation_required",
            "blocksImplementation": False,
            "blocksQuantifiedEfficacyClaims": True,
            "requiredArms": [
                "control_no_soleaux",
                "historical_python",
                "native_treatment",
            ],
        },
        "4": {
            "status": "in_progress",
            "goal": "Canonical source, durable core, security/CLI/service foundations, and an independently verified unsigned alpha artifact",
        },
        "5": {"status": "blocked_by_phase_4"},
        "6": {"status": "blocked_by_phase_5"},
        "7": {"status": "blocked_by_phase_6"},
        "8": {"status": "blocked_by_phase_7"},
    },
    "activeWork": [
        {"task": "DOC-006C", "summary": "Merge the transcript/repository gap audit and expanded executable roadmap"},
        {"task": "P4-017", "summary": "Validate every locked MCP input schema before dispatch"},
        {"task": "P4-018", "summary": "Refresh structural state before repository reads"},
        {"task": "P4-025", "summary": "Advertise LSP methods only after successful initialize capability confirmation"},
    ],
    "nextSequence": [
        "Merge the documentation reconciliation after its exact CI is green",
        "Close and merge P4-017, P4-018, and P4-025 with focused and full native gates",
        "Execute P4-019 through P4-026 without weakening the twelve-tool or Context Packet contracts",
        "Implement P4-010 through P4-015 canonical state, durability, security, CLI, and per-user service",
        "Produce and independently verify the unsigned Phase 4 alpha artifact",
        "Proceed through Phases 5 through 8; re-freeze Phase 3 before efficacy or production claims",
    ],
    "documentationAuthority": [
        "locked contracts",
        "exact receipts and independent verification",
        "PROJECT-STATUS.json",
        "PROJECT-STATUS.md",
        "ROADMAP.md",
        "TASKS.md",
        "HANDOFF.md",
        "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
        "historical transcripts",
    ],
}
write("PROJECT-STATUS.json", json.dumps(status, indent=2))

write(
    "PROJECT-STATUS.md",
    '''# Soleaux Project Status

<!-- soleaux-docs:status current_phase=4 phase2=closed phase3=deferred_reconciliation_required phase4=in_progress version=0.4.0-dev.5 production_claim_allowed=false -->

**As of:** 2026-08-04  
**Machine-readable owner:** [`PROJECT-STATUS.json`](PROJECT-STATUS.json)

## Executive state

```text
Product:                     Soleaux
Definition:                  Unified repository intelligence
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED — RECONCILIATION REQUIRED BEFORE USE
Phase 4:                     IN PROGRESS
Canonical branch:            main
Native source:               native/
Public MCP ceiling:          12
productionClaimAllowed:      false
Production readiness:        prohibited
```

Phases 0–2 proved the native Rust binaries, exact twelve-slot public profile, Context Packet V2, structural index, parser/LSP foundations, transports, editor safety, gateway, catalog domains, adopt/attach, and governance foundations. Normal Rust source now lives under `native/` and direct-checkout native CI is authoritative.

The transcript/repository audit distinguishes these proven foundations from the still-open canonical session/memory/handoff/run/artifact/policy data plane, live adapters, deeper intelligence, materializers, SDK/editor surfaces, desktop/mobile control plane, assurance, and signed release work.

## Evidence owners

| Phase | Outcome | Evidence |
|---:|---|---|
| 0 | Contracts locked; native foundation green | `PHASE0-NATIVE-GATE-RECEIPT.json` |
| 1 | Exact twelve-tool catalog and `soleaux.context/v2` green | `PHASE1-NATIVE-GATE-RECEIPT.json` |
| 2 | Gateway, catalog domains, adopt/attach, governance green | `PHASE2-CLOSURE-RECEIPT.json` + independent verification |

## Current Phase 4 finish line

1. keep the normal Rust workspace and direct native CI green;
2. close P4-017 through P4-026 correctness blockers with focused regressions;
3. expand canonical state for sessions, messages, memory, handoffs, runs/subagents, approvals, conflicts, artifacts, materializations, cursors, audit, tombstones, and retention;
4. implement crash durability, operation reservation, recovery, encrypted artifacts, keychain, and policy/capability foundations;
5. implement the exact CLI, per-user service, typed IPC, install, doctor, backup, restore, repair, and uninstall lifecycle;
6. produce an independently verified unsigned alpha artifact.

## Deferred Phase 3

Phase 3 does not block implementation, but it still gates quantified efficacy claims and any later reviewed decision to set `productionClaimAllowed=true`. Before execution it must be re-frozen as:

```text
control_no_soleaux
historical_python
native_treatment
```

## Audit and capability mapping

- [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md)
- [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
- [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md)

The twelve-tool contract is not permission to omit a capability. Broader behavior belongs behind existing tool modes, resources, gateway namespaces, daemon APIs, CLI, desktop, mobile, hooks, plugins, and generated native files.

## Locked invariants

```text
Unified MCP profile:
89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc

Context Packet V2:
3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f

PRODUCT_VERSION:              0.4.0-dev.5
PRODUCTION_CLAIM_ALLOWED:     false
HARD_CEILING:                 12
```
''',
)

write(
    "HANDOFF.md",
    '''# Soleaux Cold-Start Handoff

Read in this order:

1. `PROJECT-STATUS.json`
2. `PROJECT-STATUS.md`
3. `AGENTS.md`
4. `ROADMAP.md`
5. `TASKS.md`
6. `docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md`
7. `docs/audits/TRANSCRIPT-GAP-REGISTRY.json`
8. `docs/architecture/CAPABILITY-ABSORPTION-MAP.md`
9. locked MCP and Context Packet contracts

## Current state

```text
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED — reconciliation required before use
Phase 4:                     IN PROGRESS
Canonical branch:            main
Native source:               normal files under native/
productionClaimAllowed:      false
Public tool ceiling:         12
```

## Binding product definition

Soleaux is unified repository intelligence—not an agent operating system and not a replacement for Claude, Codex, OpenCode, Cursor, or an IDE. The primary product path is one lean MCP (`soleaux serve .`), one governed catalog, and bounded provenance-tagged context. Sessions, memory, handoffs, runs, approvals, artifacts, desktop, mobile, and remote operations are supporting daemon/application surfaces and may not inflate `tools/list`.

## Exact next action

1. merge the transcript gap audit after green CI;
2. close P4-017, P4-018, and P4-025 with exact native evidence;
3. execute P4-019 through P4-026;
4. implement P4-010 through P4-015 canonical state, durability, security, CLI, and service foundations;
5. build and independently verify the unsigned Phase 4 alpha artifact;
6. proceed through the expanded Phase 5–8 registry.

Phase 3 is deferred, but before any efficacy claim it must be re-frozen as no-Soleaux control, historical Python compatibility baseline, and native treatment using one exact authenticated model/client/build.

## Hard stops

- no thirteenth root tool;
- no changed locked contract bytes without reviewed contract update;
- no `productionClaimAllowed=true` before the reconciled live proof and release gates;
- no direct vendor internal database writes;
- no client-visible Python/Rust product split;
- no false cross-platform native resume;
- no claim that implementation, local validation, gate proof, and independent verification are the same state.
''',
)

write(
    "ROADMAP.md",
    '''# Soleaux Roadmap

<!-- soleaux-docs:roadmap current_phase=4 version=0.4.0-dev.5 -->

This is the sole phase model for the unified native product. The machine gap registry and capability absorption map are part of this roadmap.

## Program objective

Deliver one local-first product that:

- turns a repository into one lean twelve-slot MCP server;
- compiles accurate bounded context with trust, provenance, ownership, constraints, validation routes, and explicit gaps;
- governs one catalog of skills, rules, agents, ownership, artifacts, and registered backends;
- uses native parsers, LSPs, and framework providers when selected;
- provides canonical sessions, memory, handoffs, runs, approvals, artifacts, policy, and audit without mutating vendor internal stores;
- exposes non-model operations through the native app, CLI, typed API, editor integration, and mobile control plane;
- ships only after live product proof and release assurance.

## Phase overview

| Phase | Name | Status | Exit evidence |
|---:|---|---|---|
| 0 | Contract lock and native foundation | **Closed** | Exact native gate receipt |
| 1 | Unified public surface and Context Packet V2 | **Closed** | Exact twelve-tool smoke + schema validation |
| 2 | Gateway, catalog, provisioning, governance | **Closed** | Exact native gate + independent artifact verification |
| 3 | Three-arm real-client product proof | **Deferred; reconciliation required before use** | Market-value + historical-compatibility gates and independent verification |
| 4 | Canonical source, durable core, and alpha foundation | **In progress** | Direct native CI, correctness blockers, durable state/service/CLI, alpha artifact |
| 5 | Live adapters, data lifecycle, intelligence depth, and extensibility | Blocked by Phase 4 | Client/LSP/framework/design-partner matrices and beta receipt |
| 6 | Desktop, mobile, remote control, installers, and operations | Blocked by Phase 5 | Full app/device/install E2E |
| 7 | Assurance, scale, parity, and enterprise readiness | Blocked by Phase 6 | Benchmarks, external assurance, OS/security/scale matrix |
| 8 | Release candidate and general availability | Blocked by Phase 7 | Signed staged release and explicit production-claim decision |

## Phase 3 — deferred three-arm product proof

Before any live call, the experiment must be re-frozen with identical model/client/build, parameters, budgets, model-free oracles, retained failures, and these arms:

| Arm | Question answered |
|---|---|
| `control_no_soleaux` | Does Soleaux beat ordinary client repository access at equal-or-better correctness and lower waste context? |
| `historical_python` | Did native unification preserve useful Python/FastMCP behavior? |
| `native_treatment` | Does the locked native twelve-tool surface satisfy both gates? |

## Phase 4 — canonical source and durable core

Workstreams:

1. direct native CI and source/provenance/license/SBOM correctness;
2. P4-017 through P4-026 current-source correctness blockers;
3. canonical accounts/workspaces/sessions/messages/memory/catalog/runs/subagents/approvals/conflicts/artifacts/materializations/cursors/audit/tombstones/retention;
4. migrations, serialized writes, replay, backup/repair, idempotency reservation, leases, restart reconciliation, approval recovery;
5. encrypted artifact vault, keychain, workspace key separation, policy/capability service, redaction;
6. exact CLI, per-user service, typed IPC, install/doctor/backup/restore/uninstall, unsigned alpha artifact.

## Phase 5 — adapters, lifecycle, intelligence, and extensibility

Workstreams:

1. Claude Code SDK/SessionStore/hooks; Claude Desktop connector/export-import; Codex generated app-server client; OpenCode generated OpenAPI/SSE/plugin client;
2. memory validation/supersession/tombstones, session history, supported native resume/fork, signed handoffs, durable runs/subagents/approvals;
3. compatibility-aware materializers with diff/backup/atomic apply/rollback, origin/revision/idempotency/echo guards, and native load verification;
4. real Oxc extraction, Tree-sitter query/injection corpus, LibCST, `mvdan.cc/sh`, real LSP matrix, Turbo documented CLI/probes, Next static/runtime merge;
5. versioned provider APIs, Rust API, generated Python/TypeScript daemon SDKs, CI mode, editor extension, webhook/event export;
6. optional licensed hybrid search and `anilize` plus two additional design partners.

## Phase 6 — product applications and remote operations

- Tauri/React desktop with onboarding, workspaces, sessions, Context Inspector, memory/catalog, run/subagent graph, approvals/conflicts, intelligence health, Turbo/Next views, devices, backups, updates, and diagnostics.
- One Expo/React Native mobile app using the same typed daemon/remote API; no parsers on device.
- Pairing, hardware-backed identity, direct LAN, encrypted relay fallback, replay-safe commands, capability/risk tiers, biometrics, push, revocation, and audit.
- Development installers, update/repair/rollback/uninstall, keychain/keystore, support bundle, and opt-in redacted crash reporting.

## Phase 7 — assurance, scale, parity, and enterprise readiness

- native cold/warm performance against every declared p95 target;
- real client, LSP, parser, framework, OS, architecture, large-repository, pathological-file, concurrent-client, and worker-crash matrices;
- protocol/parser fuzzing and prompt-injection/redaction/path/shell/capability penetration tests;
- relay hardening and outage/recovery exercises;
- external security, privacy, licensing, accessibility, internationalization, and incident-response reviews;
- signed SBOM/provenance and compatibility table;
- air-gap, SSO, audit export, and retention only after local gates.

## Phase 8 — release and rollout

- `1.0.0-rc.1` only after Phase 7;
- signed/notarized desktop and signed Windows artifacts;
- TestFlight and Play internal/staged delivery;
- design-partner then public staged rollout with rollback thresholds;
- release notes, support policy, privacy disclosures, and known limitations;
- explicit reviewed `productionClaimAllowed` decision;
- GA verification and `1.0.0`.

## Historical proposal handling

Historical plans are research evidence, not status authority. Agent-OS positioning, premature RC labels, a production Node daemon, parallel native mobile products, standalone SSE, GPL parser dependencies in core, a large root tool catalog, false cross-platform resume, and direct vendor-store writes remain prohibited.
''',
)

# Preserve the comprehensive task registry from the reviewed branch if present, then append any
# Phase 4 correctness tasks missing from older copies. The reviewed branch already contains the
# expanded Phase 5–8 task set.
import_reviewed("TASKS.md")
tasks_path = ROOT / "TASKS.md"
tasks = tasks_path.read_text(encoding="utf-8")
required_task_block = '''

## Current-source Phase 4 correctness blockers

- [ ] **P4-017** Reject missing required, unknown, wrong-type, and out-of-range MCP arguments against the locked input schema before handler dispatch; JSON-RPC returns `-32602`.
- [ ] **P4-018** Refresh structural index and registry state before structural/context reads; prove external mutations and deletions cannot return stale evidence.
- [ ] **P4-019** Make edit filesystem writes, index/database projection, diagnostics, and audit receipt transactional or fully rolled back.
- [ ] **P4-020** Reserve idempotency keys transactionally before any side effect and replay the original terminal receipt.
- [ ] **P4-021** Redact common provider tokens, private keys, authorization headers, environment assignments, structured secrets, and logs before context/artifact/mobile exposure.
- [ ] **P4-022** Make adopt/apply/revert multi-file writes transactional with backup and complete rollback.
- [ ] **P4-023** Return typed coverage gaps for empty, unavailable, excluded, timed-out, or partial index/search/memory results; never infer completeness from absence.
- [ ] **P4-024** Return the locked typed PostgreSQL validation/error semantics for invalid SQL and multi-statement policy violations.
- [ ] **P4-025** Advertise LSP operations only after successful initialize confirms each capability; initialization failure must remain visible and fail closed.
- [ ] **P4-026** Bind every preview to workspace, path, source revision, preimage hash, expiration, formatter/diagnostic plan, and one-time consumption; reject concurrent drift.
'''
if "**P4-017**" not in tasks:
    tasks += required_task_block
if "<!-- soleaux-docs:tasks current_phase=4 -->" not in tasks:
    tasks = "# Soleaux Executable Task List\n\n<!-- soleaux-docs:tasks current_phase=4 -->\n\n" + re.sub(r"^#.*?\n", "", tasks, count=1)
tasks_path.write_text(tasks.rstrip() + "\n", encoding="utf-8")

changelog_path = ROOT / "CHANGELOG.md"
old_changelog = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else "# Changelog\n"
entry = '''## Transcript/repository reconciliation — 2026-08-04

- Audited both complete implementation transcripts against current `main`, locked contracts, receipts, normal native source, and active fixes.
- Added a machine gap registry and capability absorption map so the complete feature set remains required without expanding the twelve-tool root catalog.
- Expanded Phase 4–8 work for canonical sessions/memory/handoffs/runs/artifacts/policy, live adapters, intelligence depth, materializers, SDK/provider/editor/event APIs, desktop/mobile/remote operations, assurance, and signed rollout.
- Added explicit P4-017 through P4-026 current-source correctness blockers.
- Kept Phase 3 deferred and corrected its future design to no-Soleaux control, historical Python compatibility, and native treatment.
- Kept version `0.4.0-dev.5`, both locked contract digests, ceiling 12, and `productionClaimAllowed=false` unchanged.
'''
if "## Transcript/repository reconciliation — 2026-08-04" not in old_changelog:
    lines = old_changelog.splitlines()
    insertion = 1
    while insertion < len(lines) and not lines[insertion].strip():
        insertion += 1
    lines[insertion:insertion] = ["", entry.rstrip(), ""]
    old_changelog = "\n".join(lines)
write("CHANGELOG.md", old_changelog)

manifest = {
    "schemaVersion": "soleaux.documentation-manifest/v2",
    "version": VERSION,
    "statusOwner": "PROJECT-STATUS.json",
    "authoritative": [
        "PROJECT-STATUS.json",
        "PROJECT-STATUS.md",
        "AGENTS.md",
        "ROADMAP.md",
        "TASKS.md",
        "HANDOFF.md",
        "TESTING.md",
        "ROLLOUT.md",
        "RELEASE.md",
        "MARKETING.md",
        "docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md",
        "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
        "docs/architecture/CAPABILITY-ABSORPTION-MAP.md",
        "UNIFIED-MCP-PROFILE.md",
        "CONTEXT-PACKET-V2.md",
    ],
    "historical": ["docs/history", "research", "transcripts"],
    "rules": {
        "historicalCannotAdvanceStatus": True,
        "phaseClosureRequiresReceiptAndIndependentVerification": True,
        "publicMcpCeiling": 12,
        "productionClaimAllowed": False,
    },
}
write("docs/DOCUMENTATION-MANIFEST.json", json.dumps(manifest, indent=2))

write(
    "docs/README.md",
    '''# Soleaux Documentation

Start with `PROJECT-STATUS.json`, then `PROJECT-STATUS.md`, `ROADMAP.md`, `TASKS.md`, and `HANDOFF.md` at the repository root.

The transcript audit and machine gap registry are authoritative planning inputs but cannot override locked contracts or exact receipts:

- `docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md`
- `docs/audits/TRANSCRIPT-GAP-REGISTRY.json`
- `docs/architecture/CAPABILITY-ABSORPTION-MAP.md`

Historical proposals remain available for research and cannot advance phase or release status.
''',
)
