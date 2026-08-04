# Soleaux Cold-Start Handoff

Read in this order:

1. [`PROJECT-STATUS.json`](PROJECT-STATUS.json)
2. [`PROJECT-STATUS.md`](PROJECT-STATUS.md)
3. [`AGENTS.md`](AGENTS.md)
4. [`ROADMAP.md`](ROADMAP.md)
5. [`TASKS.md`](TASKS.md)
6. [`docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md`](docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-04.md)
7. [`docs/audits/TRANSCRIPT-GAP-REGISTRY.json`](docs/audits/TRANSCRIPT-GAP-REGISTRY.json)
8. [`docs/architecture/CAPABILITY-ABSORPTION-MAP.md`](docs/architecture/CAPABILITY-ABSORPTION-MAP.md)
9. the locked MCP and Context Packet contracts.

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

## Completed since the former handoff

- PR #5 made `main` canonical and preserved receipt ancestry.
- PR #6 checked the verified Rust source into `native/`, retired carriers and established direct native CI.
- PR #7 fixed truthful LSP capability advertisement and inspection/navigation degradation.
- PR #8 added the telemetry daemon bearer/CORS/redaction baseline.
- PR #10 added the daemon-served same-origin dashboard export; PR #9 was closed as unsafe/superseded.

Do not repeat those tasks.

## Exact next work

1. merge the transcript audit and expanded task registry after green CI;
2. execute `P4-017` first: validate the selected tool's locked input schema before dispatch and add negative-schema regressions;
3. continue in order through `P4-018`–`P4-024` and `P4-026`; `P4-025` is already closed by PR #7;
4. implement `P4-010`–`P4-015` for canonical state, durability/recovery, idempotency/leases, artifact/policy, exact CLI, per-user service and typed IPC;
5. produce the unsigned alpha, operational smoke, Phase 4 exact receipt and independent artifact verification;
6. proceed through the expanded Phase 5–8 registry.

## Hard constraints

- keep the exact twelve-tool contract and locked digests;
- keep `0.4.0-dev.5` and `productionClaimAllowed=false` unless explicitly reviewed;
- no force-push or squash of receipt-bearing ancestry;
- no direct writes to vendor internal databases;
- no root-tool inflation to absorb broader capabilities;
- no production claim from merge state, test count or transcript claims;
- no reactivation of Phase 3 without freezing the three-arm design.

Use the stop format in `AGENTS.md` only when a hard contract or evidence condition is actually violated. Otherwise implement the next task and retain exact validation evidence.
