# Phase 3 Runbook

## 0. Preconditions

- Phase 2 closure receipts present.
- `STATUS.json` is `frozen_ready`.
- Model/client/parameters recorded.
- Baseline, treatment, and target commits checked out immutably.
- Tasks and rubric hashed.
- Credentials available.
- Clean isolated worktree per task and arm.

## 1. Record experiment manifest

Create:

```text
artifacts/phase3/<experiment-id>/manifest.json
```

Include commits, model/client, parameters, budgets, task/rubric/schema digests, operator, UTC time.

## 2. Baseline arm

For each task:

1. reset target repo to registered commit;
2. attach only the baseline repository-intelligence surface;
3. capture `tools/list`;
4. issue the exact task prompt;
5. retain all tool calls and context;
6. run the oracle/validation;
7. write one schema-valid run record.

## 3. Treatment arm

Repeat identically with only the native Soleaux MCP:

1. verify exactly 12 tools;
2. smoke `context.compile`;
3. validate Context Packet V2;
4. issue the same prompt;
5. retain all tool calls and context;
6. run the same oracle;
7. write the run record.

## 4. Analysis

- validate every run against `MEASUREMENT-SCHEMA.json`;
- score against the frozen rubric;
- calculate raw and aggregate results;
- list every methodological limitation;
- do not exclude failed runs.

## 5. Independent verification

Verifier checks:

- exact commits;
- task/rubric/schema digests;
- model/client parity;
- no hidden tools;
- tool count;
- packet validation;
- secrets;
- raw records and calculations;
- result report.

## 6. Stop conditions

Stop and preserve artifacts when:

- model/client differs across arms;
- tool count exceeds 12;
- task changes;
- target commit changes;
- secret leaks;
- non-native selection occurs;
- required instrumentation fails.

Do not continue with “best effort” data that cannot satisfy the comparison contract.
