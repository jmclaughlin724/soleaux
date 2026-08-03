# Soleaux Roadmap

<!-- soleaux-docs:roadmap current_phase=3 version=0.4.0-dev.5 -->

This roadmap is the sole phase model for the unified native product. Historical Python task stages and earlier 18-stage plans are mapped into this sequence rather than maintained as competing roadmaps.

## Program objective

Deliver one local-first repository-intelligence product that:

- exposes one lean MCP server;
- compiles accurate bounded context;
- governs one skills/rules/agents/backends catalog;
- uses native parsers and LSPs when selected;
- supports safe edits and repository governance;
- integrates with existing agent clients without replacing them;
- ships only after live product proof and release assurance.

## Phase overview

| Phase | Name | Status | Version posture | Exit evidence |
|---:|---|---|---|---|
| 0 | Contract lock and native foundation | **Closed** | `0.4.0-dev.5` | Exact native gate receipt |
| 1 | Unified public surface and Context Packet V2 | **Closed** | `0.4.0-dev.5` | Exact 12-tool smoke + schema validation |
| 2 | Gateway, catalog, provisioning, governance | **Closed** | `0.4.0-dev.5` | Exact native gate + independent artifact verification |
| 3 | Live same-model / same-task product proof | **Unblocked, not started** | `0.4.0-dev.5` | Equal-or-better correctness + lower context waste |
| 4 | Canonical source consolidation and alpha foundation | Blocked | eligible for `0.4.0-alpha.x` after gate | Normal native source tree + reviewed default-branch PR |
| 5 | Shared service, live adapters, and consumer onboarding | Blocked | alpha/beta | Live client/repository matrix |
| 6 | Desktop, mobile, installers, and operations | Blocked | beta | Signed development distributions and device flows |
| 7 | Assurance and cross-platform parity | Blocked | beta/RC | Benchmarks, security, privacy, accessibility, OS matrix |
| 8 | RC and GA rollout | Blocked | `1.0.0-rc.x` → `1.0.0` | Signed release, external reviews, rollout evidence |

## Phase 3 — live product proof

**Purpose:** verify the product's central promise rather than adding more surface area.

Required:

- fixed baseline and treatment implementations;
- fixed repository commit and task set;
- identical model/client/sampling parameters;
- identical scoring and measurement;
- all runs retained;
- equal-or-better aggregate task correctness;
- measurable tool-schema/file-read/context reduction;
- exact 12-tool profile in every treatment run;
- Context Packet V2 validity, native selections, and no secret leakage;
- exact receipt and independent verification.

Phase 3 does not change `productionClaimAllowed`.

## Phase 4 — canonical source consolidation and alpha foundation

**Purpose:** eliminate the split between historical default-branch documentation and the proven native implementation.

Work:

- materialize the native Rust source as normal repository files;
- establish a reviewed source-of-truth branch;
- archive Python runtime code as fixture/history where still useful;
- update build, license, packaging, and developer workflows;
- remove carrier-only source as the long-term development mechanism;
- preserve exact Phase 0–3 receipts and contracts;
- produce a reproducible unsigned alpha package;
- update default-branch README, changelog, docs, and release metadata.

Exit:

- native source builds directly from checkout;
- no client-visible Python product mode;
- normal PR CI runs the native gate set;
- clean install/doctor/uninstall smoke on at least one supported OS;
- documentation consistency gate green;
- version may advance only through reviewed release policy.

## Phase 5 — shared service, live adapters, and consumer onboarding

**Purpose:** prove the unified product across real clients and repositories.

Work:

- per-user service and workspace registry;
- live Claude Code, Claude Desktop, Codex, OpenCode, and Cursor capability probes;
- supported read-only/session/handoff adapter behavior;
- shared memory and handoff lifecycle with provenance;
- materialization to native rules/skills/agents files with backup, diff, rollback, and echo guards;
- consumer onboarding via `soleaux attach`;
- design-partner repositories: `anilize`, then additional approved repositories;
- real LSP and Turbo/Next compatibility matrix.

Exit:

- at least three design-partner repositories;
- adapter matrix green for declared versions;
- unknown versions enter safe/read-only mode;
- no writes to vendor internal stores;
- beta readiness review.

## Phase 6 — desktop, mobile, installers, and operations

**Purpose:** turn the core into a usable distributed product without redefining the wedge.

Work:

- Tauri + React desktop;
- one Expo/React Native mobile app;
- daemon lifecycle, context inspector, catalog, diagnostics, approvals;
- secure pairing, direct LAN, encrypted relay fallback, revocation;
- native installers, upgrade, repair, rollback, uninstall;
- keychain/keystore integration;
- updater and support bundle.

Exit:

- development builds on target platforms;
- end-to-end desktop/mobile control of the same daemon;
- no parser stack on mobile;
- install/upgrade/uninstall evidence.

## Phase 7 — assurance and cross-platform parity

Work:

- defined-hardware cold/warm p50/p95/p99;
- parser/LSP corpus and fuzz tests;
- shell/path-jail/redaction penetration tests;
- external security review;
- privacy and licensing review;
- WCAG/accessibility audit;
- macOS, Windows, and Linux parity;
- signed SBOM and provenance;
- incident-response and rollback exercises.

Exit: all release-blocking findings resolved or explicitly accepted.

## Phase 8 — RC and GA rollout

Work:

- release-candidate freeze;
- signed/notarized desktop distributions;
- TestFlight and Play internal/staged release;
- staged design-partner and public rollout;
- release notes, support plan, rollback thresholds;
- external claims review;
- explicit decision on `productionClaimAllowed`;
- GA publication.

## Historical plan mapping

| Historical work | Standardized location |
|---|---|
| Python extraction/build identity/bridge | History; capabilities absorbed by Phases 0–2 |
| Python `attach` and shared-service stages | Native Phase 2 and remaining Phase 5 |
| Earlier Stage 0–13 native core | Phases 0–2 |
| Earlier desktop/mobile stages | Phase 6 |
| Earlier eval/compatibility/signing stages | Phases 3, 7, and 8 |
| Consumer onboarding | Phase 5 |
| Public release and PyPI discussion | Replaced by Phase 8 release policy |

No historical checklist may independently advance current status.
