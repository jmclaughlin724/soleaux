# Documentation System and Source-of-Truth Policy

## Purpose

Soleaux previously had three competing status systems:

- session handoffs;
- Python-lineage root documentation;
- native workflow receipts and artifacts.

This policy replaces that split with one ordered chain.

## Authority hierarchy

| Rank | Owner | Meaning |
|---:|---|---|
| 1 | Normative JSON contracts | Wire schemas and locked invariants |
| 2 | Exact workflow receipts | What compiled and ran on an exact commit |
| 3 | Independent verification receipts | What was verified outside the producing workflow |
| 4 | `PROJECT-STATUS.json` | Current machine-readable phase state |
| 5 | `PROJECT-STATUS.md` | Human rendering of current state |
| 6 | `ROADMAP.md` | Approved phase sequence and exit gates |
| 7 | `TASKS.md` | Executable work breakdown |
| 8 | Current phase package | Frozen implementation/experiment details |
| 9 | `HANDOFF.md` and `AGENTS.md` | Cold-start and collaboration behavior |
| 10 | README/marketing/release prose | Public interpretation of verified evidence |
| 11 | History | Non-authoritative lineage record |

A lower-ranked document may explain a higher-ranked owner but may not contradict it.

## Document responsibilities

### `PROJECT-STATUS.json`

The only machine-readable current-status owner. It records:

- version;
- phase state;
- production claim;
- contract hashes;
- exact source commits and workflow runs;
- current blocker and next actions.

### `PROJECT-STATUS.md`

Readable status. It must mirror the JSON and link receipts.

### `ROADMAP.md`

Defines the one current phase model. It changes only through reviewed planning.

### `TASKS.md`

Contains task IDs and checkboxes. A checked item must name or link evidence.

### `HANDOFF.md`

Contains no independent roadmap or task list. It points to owners.

### `AGENTS.md`

Defines product boundary, hard stops, read order, validation, and documentation update protocol.

### README and marketing

May use only claims permitted by `CLAIMS-POLICY.md`.

## Status transition procedure

A phase status changes only in one reviewed change that updates:

1. phase receipt and independent verification;
2. `PROJECT-STATUS.json`;
3. `PROJECT-STATUS.md`;
4. `ROADMAP.md`;
5. `TASKS.md`;
6. `HANDOFF.md`;
7. `CHANGELOG.md`;
8. current phase status/results;
9. release and marketing claim state where applicable.

## Evidence language

Every report uses one of these labels:

- **planned** — approved but not implemented;
- **implemented** — source exists;
- **locally validated** — local checks passed;
- **exact-gate proven** — exact-commit CI passed;
- **independently verified** — artifact checked outside its producer;
- **blocked** — prerequisite absent;
- **not run** — no evidence exists.

Never use “complete” without stating the evidence level.

## Archive policy

Historical files are preserved by Git history and summarized under `docs/history`. They are not copied into active status paths because duplicated documents recreate drift.

## Consistency gate

Run:

```bash
python3 scripts/check_documentation_consistency.py
```

The gate validates:

- current version and phase;
- production claim;
- contract digests;
- exact tool list;
- required document presence;
- README/marketing prohibited claims;
- Phase 3 status and task registry;
- status markers across root documents.

The workflow `.github/workflows/documentation-consistency.yml` runs this on every documentation change.
