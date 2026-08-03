# Soleaux Cold-Start Handoff

This file is intentionally short. It points to canonical owners instead of duplicating project status.

## Read in this order

1. [`PROJECT-STATUS.json`](PROJECT-STATUS.json) — machine-readable current state.
2. [`PROJECT-STATUS.md`](PROJECT-STATUS.md) — human status and evidence.
3. [`AGENTS.md`](AGENTS.md) — product and collaboration constraints.
4. [`ROADMAP.md`](ROADMAP.md) — phase sequence.
5. [`TASKS.md`](TASKS.md) — current executable tasks.
6. [`UNIFIED-MCP-PROFILE.md`](UNIFIED-MCP-PROFILE.md) and [`CONTEXT-PACKET-V2.md`](CONTEXT-PACKET-V2.md) — locked contracts.
7. [`docs/experiments/phase3/EXPERIMENT-PLAN.md`](docs/experiments/phase3/EXPERIMENT-PLAN.md) — current work.

## Current state

```text
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     UNBLOCKED, NOT STARTED
productionClaimAllowed:      false
Public tool ceiling:         12
```

## Immediate action

Complete Phase 3 task `P3-005`: record the exact model/client identity and sampling parameters. Then perform the oracle dry-run and freeze the experiment before the first live model call.

Do not:

- change the 12-tool contract or contract digests;
- start Phase 4 before Phase 3 closes;
- merge to `main` or force-push;
- claim production readiness;
- fabricate model results when credentials are unavailable.

Use the stop format in `AGENTS.md` when blocked.
