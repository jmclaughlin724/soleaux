# Phase 3 Results

```text
Status: DEFERRED — RECONCILIATION REQUIRED — NEVER RUN
Phase 3: DEFERRED
productionClaimAllowed: false
```

No live model results exist. The phase does not block implementation.

Before execution, the experiment must be re-frozen with:

```text
control_no_soleaux
historical_python
native_treatment
```

This file may be populated only after owner reactivation, complete model/client/protocol/budget lock, oracle dry-runs, package hashes, all three arms, scoring and independent verification.

Required result sections:

1. frozen experiment manifest and hashes;
2. model/client/protocol/parameters/budgets;
3. repositories and commits;
4. raw task results for all three arms and every failure;
5. market-value gate versus no-Soleaux control;
6. compatibility gate versus historical Python;
7. context/tool/file-read measurements;
8. treatment-integrity and fail-closed incidents;
9. limitations;
10. exact receipt and independent verification;
11. conclusion without changing `productionClaimAllowed`.
