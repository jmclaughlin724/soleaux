# Soleaux Cold-Start Handoff

This file is intentionally short. It points to canonical owners instead of duplicating project status.

## Read in this order

1. [`PROJECT-STATUS.json`](PROJECT-STATUS.json) — machine-readable current state.
2. [`PROJECT-STATUS.md`](PROJECT-STATUS.md) — human status and evidence.
3. [`AGENTS.md`](AGENTS.md) — product and collaboration constraints.
4. [`ROADMAP.md`](ROADMAP.md) — phase sequence.
5. [`TASKS.md`](TASKS.md) — current executable tasks.
6. [`UNIFIED-MCP-PROFILE.md`](UNIFIED-MCP-PROFILE.md) and [`CONTEXT-PACKET-V2.md`](CONTEXT-PACKET-V2.md) — locked contracts.

## Current state

```text
Version:                     0.4.0-dev.5
Phase 0:                     CLOSED
Phase 1:                     CLOSED
Phase 2:                     CLOSED
Phase 3:                     DEFERRED (optional claims-gate)
Phase 4:                     IN PROGRESS
productionClaimAllowed:      false
Public tool ceiling:         12
```

## Immediate action

Execute Phase 4 from `P4-001`: materialize the verified Phase 2 native source
as a normal in-tree Rust workspace at `native/` from the preserved evidence
artifact `8858165328`, prove parity against the closure receipt, then replace
carrier assembly with direct-checkout native CI.

Do not:

- change the 12-tool contract or contract digests;
- squash or force-push the consolidation lineage;
- claim production readiness;
- re-block any phase on the deferred Phase 3 experiment without an explicit
  owner decision;
- fabricate results when evidence is unavailable.

Use the stop format in `AGENTS.md` when blocked.
