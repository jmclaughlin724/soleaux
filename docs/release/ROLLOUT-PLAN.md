# Rollout Plan

## Development

Current: `0.4.0-dev.5`.

Audience:

- maintainers;
- internal evaluators;
- controlled experiment operators.

Allowed:

- source review;
- exact CI builds;
- Phase 3 experiments.

Not allowed:

- production claims;
- public installer marketing;
- unreviewed client deployments.

## Alpha

Entry:

- Phase 3 closed;
- native source normalised in repository;
- unsigned/signed development installer as defined;
- install/doctor/uninstall smoke.

Audience:

- approved design partners.

Controls:

- explicit opt-in;
- known limitations;
- support channel;
- rapid rollback;
- no automatic broad update.

## Beta

Entry:

- live client/LSP matrices;
- three design-partner repositories;
- shared service and adapters;
- stable migration and rollback.

Rollout:

1. internal;
2. one design partner;
3. three design partners;
4. invite-only cohort.

## Release candidate

Entry:

- Phase 7 assurance;
- signed artifacts;
- external reviews;
- reproducible builds;
- complete release notes and support policy.

RC rollout:

- staged percentage;
- crash/error/rollback thresholds;
- compatibility monitoring;
- no silent protocol upgrade.

## General availability

Entry:

- successful RC cohort;
- explicit production-claim approval;
- signed/notarized/store artifacts;
- incident response on call;
- support and deprecation policy.

Rollback triggers:

- contract or profile drift;
- secret leakage;
- path/shell policy bypass;
- data corruption;
- client crash loop;
- update verification failure;
- significant correctness regression.
