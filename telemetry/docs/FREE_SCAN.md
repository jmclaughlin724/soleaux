# Soleaux Evidence Scan

source-id: openai-response-usage-object source-id: openai-organization-usage-api source-id: anthropic-messages-usage-report source-id: anthropic-claude-code-cli-output source-id: codex-exec-json-events source-id: codex-app-server-protocol source-id: soleaux-measurement-methodology-v1

## Current scope

The scanner reports values directly present in supported source records or deterministically derived without introducing provider assumptions.

It reports:

- provider-reported input, cached-input, output, reasoning, and total token fields when present;
- explicit tool and model events;
- status, retries, latency, and output bytes;
- process CPU and resident-memory samples;
- provider quota snapshots;
- exact normalized-signature repeats as candidates for controlled testing.

For conclusions that require more evidence, the JSON and Markdown reports now include `definitiveResultRequirements`. This section identifies the exact missing instrumentation, fixture, comparison, and acceptance criterion needed to calculate:

- tool-level token consumption;
- validated wasted tokens;
- tool/session CPU and memory;
- provider quota capacity consumed;
- measured Soleaux savings.

The complete methodology is in `tools/soleaux/telemetry/docs/DEFINITIVE_MEASUREMENT_PLAN.md`.

## Command

```bash
pnpm soleaux:telemetry:scan -- \
  --input ./exports \
  --provider auto \
  --daemon http://127.0.0.1:43120 \
  --quota ./quota-snapshot.json \
  --output .soleaux/reports/latest
```

Outputs:

```text
.soleaux/reports/latest.json
.soleaux/reports/latest.md
```

## Codex records

Codex support is limited to versioned structured JSONL or app-server event surfaces:

- `turn.completed` supplies turn-level usage;
- item lifecycle events describe actions;
- app-server token-usage notifications can provide additional turn/thread measurements;
- item events retain zero attributed tokens unless Codex itself supplies item-level usage.

The scanner does not treat rollout files or private logs as stable public APIs unless support is explicitly version-pinned and fixture-tested against upstream source.

## Claude Code records

Claude Code support is limited to documented JSON result metadata. Definitive token accounting requires the Anthropic API response, organization usage report, or an authorized gateway capture that preserves the upstream usage object.

The scanner does not recursively guess token or tool fields from arbitrary transcript structures.

## Token accounting

Provider-reported totals are authoritative. When a source has no total, Soleaux derives only:

```text
total = input + output
```

Cached-input and reasoning fields remain subcategories and are not added again.

## Categories

Categories are descriptive labels derived from explicit item types, tool names, or command strings:

- `model`
- `rg`
- `bash`
- `web-search`
- `mcp`
- `tests`
- `git`
- `file-read`
- `file-write`
- `compaction`
- `other-tool`

A category does not imply waste or causation.

## Evidence requirements returned by the scanner

The report evaluates whether input data includes:

- task IDs and versions;
- experiment IDs;
- baseline/intervention variants;
- completion assertions;
- tool execution IDs;
- cumulative process CPU counters;
- known sample intervals;
- PID start-time identity and ancestry;
- provider quota units and reset timestamps;
- active Soleaux control versions.

For each missing element, the report states the acceptance test required for a definitive result.

## Definitive calculations

Validated waste and savings use matched successful tasks, not fixed percentages:

```text
validated wasted tokens = baseline tokens - intervention tokens
token reduction = baseline tokens - Soleaux-control tokens
CPU reduction = baseline CPU seconds - Soleaux-control CPU seconds
memory reduction = baseline memory byte-seconds - Soleaux-control memory byte-seconds
```

CPU seconds must come from cumulative process CPU counter deltas. Memory byte-seconds require resident-memory samples with known intervals. Provider capacity consumed must use before/after observations from the same provider-reported quota window.

## Required implementation expansion

Before these definitive calculations can be enabled, Soleaux must add:

1. versioned provider fixtures;
2. task, experiment, assertion, variant, turn, item, and tool execution identifiers;
3. tool lifecycle and task outcome endpoints;
4. cumulative process CPU and interval memory collection;
5. immutable experiment storage;
6. reconciliation tests against provider organization usage reports;
7. matched-run analysis with uncertainty.
