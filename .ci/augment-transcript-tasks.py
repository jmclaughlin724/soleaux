#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "TASKS.md"
text = PATH.read_text(encoding="utf-8")
present = set(re.findall(r"\*\*([A-Z]+-\d+[A-Z]?)\*\*", text))

sections = {
    "Branch and documentation closure": {
        "BR-001": "Merge the standardized documentation system into the native lineage.",
        "BR-003": "Close obsolete draft PR #3 as superseded.",
        "BR-004": "Merge the receipt-bearing native lineage to `main` without squashing evidence ancestry.",
        "BR-005": "Preserve receipts/archive tags before deleting historical phase branches.",
        "BR-006": "Delete merged transcript-audit branches after the final documentation PR and evidence review.",
        "DOC-006A": "Audit both complete transcripts against current repository source and receipts.",
        "DOC-006C": "Merge the transcript gap registry, capability map, status, roadmap, tasks, handoff, and consistency gate.",
    },
    "Deferred Phase 3 reconciliation": {
        "P3-005": "Reconcile the obsolete synthetic/GitHub-Models harness with the real-repository plan and record superseded artifacts.",
        "P3-006": "Freeze `control_no_soleaux`, `historical_python`, and `native_treatment` arms.",
        "P3-007": "Record exact authenticated model/client/build, protocol, parameters, budgets, retry policy, and credential availability.",
        "P3-008": "Freeze model-free task oracles, commands, expected facts, and scoring before any live call.",
        "P3-009": "Hash the complete experiment package and mark it `frozen_ready` before execution.",
    },
    "Phase 4 canonical source and durable alpha foundation": {
        "P4-001": "Keep verified native source as normal checked-in files under `native/`.",
        "P4-002": "Eliminate carrier-only development paths from authoritative source.",
        "P4-004": "Run the complete native gate directly from checkout on every material change.",
        "P4-010": "Expand canonical storage to accounts, workspaces, mappings, sessions, turns, messages, content parts, memory claims, rules, skills, agents, runs, subagents, approvals, conflicts, materializations, artifacts, cursors, audit, tombstones, and retention.",
        "P4-011": "Implement versioned migrations, serialized writer/read pool, crash recovery, replay, backup, integrity repair, and downgrade refusal.",
        "P4-012": "Implement transactional idempotency reservation, execution leases, process/native-session reconciliation, cancellation, and pending-approval recovery.",
        "P4-013": "Implement encrypted content-addressed artifact vault, OS-keychain master keys, per-workspace keys, redaction, and policy/capability foundation.",
        "P4-014": "Implement exact JSON-capable CLI commands for serve/install/service/doctor/ci/cache/index/integrate/handoff/backup/restore/export/repair/uninstall with mutating dry runs.",
        "P4-015": "Implement the installed per-user daemon lifecycle and typed local IPC with peer-credential checks.",
        "P4-017": "Reject missing required, unknown, wrong-type, and out-of-range MCP arguments against the locked input schema before dispatch; JSON-RPC returns `-32602`.",
        "P4-018": "Refresh structural index and registry state before structural/context reads; prove external mutations and deletions cannot return stale evidence.",
        "P4-019": "Make edit filesystem writes, index/database projection, diagnostics, and audit receipt transactional or fully rolled back.",
        "P4-020": "Reserve idempotency keys transactionally before any side effect and replay the original terminal receipt.",
        "P4-021": "Redact provider tokens, private keys, authorization headers, environment assignments, structured secrets, and logs before exposure.",
        "P4-022": "Make adopt/apply/revert multi-file writes transactional with backup and complete rollback.",
        "P4-023": "Return typed coverage gaps for empty, unavailable, excluded, timed-out, or partial results; never infer completeness from absence.",
        "P4-024": "Return locked typed PostgreSQL validation/error semantics for invalid SQL and multi-statement policy violations.",
        "P4-025": "Advertise LSP operations only after successful initialize confirms each capability; initialization failure remains visible.",
        "P4-026": "Bind every preview to workspace, path, source revision, preimage hash, expiration, formatter/diagnostic plan, and one-time consumption.",
    },
    "Phase 5 adapters, lifecycle, intelligence, and extensibility": {
        "P5-002": "Probe and integration-test Claude Code Agent SDK/CLI capabilities and compatibility.",
        "P5-003": "Implement Claude Desktop supported connector plus explicit export/import boundary and matrix.",
        "P5-004": "Implement Codex app-server schema probe and generated-client matrix.",
        "P5-005": "Implement OpenCode OpenAPI/SSE/plugin capability probe and matrix.",
        "P5-007": "Implement canonical session/history service with supported same-platform resume, fork, and archive.",
        "P5-008": "Implement materializer compatibility compiler, diff/backup/atomic apply/rollback, echo guards, and load verification.",
        "P5-009": "Run real LSP compatibility matrix across the declared language servers.",
        "P5-010": "Run real Turborepo and Next.js compatibility matrices across repository and tool versions.",
        "P5-014": "Implement Claude SDK execution host, external SessionStore, hooks, permissions, compaction, and restart reconciliation.",
        "P5-015": "Implement Claude Desktop user-facing import/export and supported local connector workflows while retaining hosted CRUD non-goal.",
        "P5-016": "Implement Codex generated app-server client, approvals, steering, compaction, archive, cursors, reconnect, and safe mode.",
        "P5-017": "Implement OpenCode generated client, persistent event cursor/reconciliation, permissions, fork/abort/summarize/revert, and plugin compatibility.",
        "P5-018": "Implement scoped memory lifecycle Proposed→Validated→Active→Superseded/Tombstoned/Rejected with conflicts, provenance, expiry, and sensitivity.",
        "P5-019": "Implement signed handoff manifests with objective, decisions, tasks, Git/code state, artifacts, exclusions, permissions, and destination lineage.",
        "P5-020": "Implement durable run/subagent orchestration with approvals, budgets, leases, attenuation, recovery, cancellation propagation, and aggregation.",
        "P5-021": "Complete Oxc symbol/module/route/server-action extraction and Tree-sitter query/injection/watcher/damaged-file corpus.",
        "P5-022": "Integrate LibCST Python writes and `mvdan.cc/sh` shell semantics with effects, sandbox, process tree, diagnostics, and optional ShellCheck.",
        "P5-023": "Complete LSP events, multi-root/versioned documents, diagnostics, workspace edits, health/backoff/quarantine, and resource limits.",
        "P5-024": "Complete Turbo static graph plus documented version-probed CLI and optional LSP compatibility gates.",
        "P5-025": "Complete Next static Oxc routes/actions/boundaries plus advertised DevTools init/index/runtime evidence merge.",
        "P5-026": "Publish versioned provider interfaces for parsers, workspace graphs, routes, context sources, materializers, and gateway backends.",
        "P5-027": "Publish stable Rust API and generated Python/TypeScript SDKs that call the daemon, plus deterministic `soleaux ci`.",
        "P5-028": "Implement editor extension MVP and capability-gated webhook/SIEM event export.",
        "P5-029": "Implement optional licensed hybrid lexical/vector/graph search with sensitivity exclusions, pinned model/hash, migration, rebuild, and recovery.",
    },
    "Phase 6 desktop, mobile, remote operations, and installers": {
        "P6-001": "Implement Tauri desktop shell and daemon lifecycle.",
        "P6-002": "Implement Context Inspector, catalog, sessions, run graph, health, and diagnostics UX.",
        "P6-003": "Implement one Expo/React Native mobile app; parser/LSP execution remains server-side.",
        "P6-004": "Implement pairing, direct LAN, E2E-encrypted relay fallback, revoke, replay defense, and audit.",
        "P6-005": "Integrate keychain/keystore and artifact encryption.",
        "P6-006": "Build macOS, Windows, and Linux development installers.",
        "P6-007": "Implement upgrade, repair, rollback, uninstall, and native-file restoration.",
        "P6-008": "Implement update channels, version alignment, support bundle, and opt-in redacted crash reporting.",
        "P6-009": "Produce desktop/device/install E2E evidence.",
        "P6-010": "Implement first-run detection, trust, operating mode, indexing progress/cancel/partial availability, and capability matrix UI.",
        "P6-011": "Implement unified sessions/transcripts/events/artifacts/lineage, live run console, subagent graph, and handoff UX.",
        "P6-012": "Implement approval inbox, permission profiles, risk preview, biometric/desktop confirmation, and conflict resolution.",
        "P6-013": "Implement memory review and rule/skill/agent compatibility/materialization UX.",
        "P6-014": "Implement versioned mobile/remote API, device certificates, replay-safe commands, cursor event stream, opaque push, and expiry policy.",
        "P6-015": "Implement backup/restore, integrations, updates, diagnostics, device management, and accessibility/i18n-ready design system.",
        "P6-016": "Gate optional offline mobile library on encrypted replication, tombstones, quotas, key rotation, and conflict rules.",
    },
    "Phase 7 assurance, scale, parity, and enterprise readiness": {
        "P7-001": "Run defined-hardware cold/warm p50/p95/p99 benchmarks for all declared paths.",
        "P7-002": "Build parser/LSP malformed-input corpora and protocol/parser fuzzing.",
        "P7-003": "Run path, shell, redaction, prompt-injection, pairing, MCP, update, and cross-workspace security tests.",
        "P7-004": "Complete external penetration test.",
        "P7-005": "Complete privacy, retention, deletion, legal, and licensing review.",
        "P7-006": "Complete desktop/mobile accessibility and internationalization audit.",
        "P7-007": "Produce signed SBOM and build provenance.",
        "P7-008": "Complete macOS/Windows/Linux compatibility including case-insensitive paths and symlink cycles.",
        "P7-009": "Run incident response, backup/restore, relay outage, upgrade/downgrade, and rollback exercises.",
        "P7-010": "Make the Stage-17-style readiness decision.",
        "P7-011": "Run 10k+ file, generated/minified/multi-MB, concurrent-client, memory-pressure, and worker-crash tests.",
        "P7-012": "Harden relay tenant routing, queue/expiry/DLQ, abuse controls, multi-region replay defense, push rotation, SLOs, self-hosting, and DR.",
        "P7-013": "Implement enterprise audit export, retention, air-gap, and SSO only after local gates pass.",
    },
    "Phase 8 release candidate and general availability": {
        "P8-001": "Freeze `1.0.0-rc.1` only after Phase 7.",
        "P8-002": "Sign/notarize desktop artifacts and sign Windows packages.",
        "P8-003": "Run TestFlight and Play internal rollout.",
        "P8-004": "Run design-partner staged release.",
        "P8-005": "Run public staged release with rollback thresholds.",
        "P8-006": "Review and explicitly decide `productionClaimAllowed`.",
        "P8-007": "Publish release notes, support policy, compatibility table, privacy disclosures, and known limitations.",
        "P8-008": "Complete GA verification and release `1.0.0`.",
    },
}

append: list[str] = []
for section, tasks in sections.items():
    missing = [(task_id, description) for task_id, description in tasks.items() if task_id not in present]
    if not missing:
        continue
    append.append(f"## {section}")
    append.append("")
    for task_id, description in missing:
        append.append(f"- [ ] **{task_id}** {description}")
    append.append("")

if append:
    text = text.rstrip() + "\n\n" + "\n".join(append).rstrip() + "\n"
PATH.write_text(text, encoding="utf-8")
