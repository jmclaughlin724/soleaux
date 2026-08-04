# P4-019 source validation

This record identifies the normal Rust source submitted to the exact P4-019 gate. It is not a closure receipt and makes no production-readiness claim.

Validated implementation scope:

- every edit backup is persisted before the first repository mutation;
- a file-write or post-write bookkeeping failure restores every successfully written preimage;
- the native structural index is refreshed after restoration;
- the preview returns to an unconsumed, no-write state;
- rollback attempts produce a hash-chained audit event and a `soleaux.editor-rollback/v1` reconciliation receipt;
- a deterministic regression injects a post-write failure and proves source, preview, and receipt restoration;
- the public MCP tool ceiling remains 12;
- version remains `0.4.0-dev.5`;
- `productionClaimAllowed` remains `false`.

P4-019 may be marked complete only after P4-018 is merged, the branch is reconciled with current `main`, the exact-head workflow succeeds, and its evidence artifact passes independent verification.
