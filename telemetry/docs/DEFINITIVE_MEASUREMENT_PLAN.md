# Soleaux definitive measurement plan

source-id: openai-response-usage-object source-id: openai-organization-usage-api source-id: anthropic-messages-usage-report source-id: anthropic-pricing-and-usage-fields source-id: anthropic-claude-code-cli-output source-id: anthropic-claude-code-gateway source-id: codex-exec-json-events source-id: codex-app-server-protocol source-id: sysinfo-process-metrics source-id: soleaux-measurement-methodology-v1

A Soleaux result is definitive only when it is reported directly by an upstream system or calculated from measured inputs with a reproducible formula. A heuristic alone cannot produce a definitive result.

## Provider token usage

### OpenAI API

Required upstream fields:

- `usage.input_tokens`;
- `usage.input_tokens_details.cached_tokens`;
- `usage.output_tokens`;
- `usage.output_tokens_details.reasoning_tokens`;
- `usage.total_tokens`.

Acceptance criteria:

- preserve provider totals exactly;
- treat cached tokens as a breakdown of input;
- treat reasoning tokens as a breakdown of output;
- reconcile request totals with the corresponding organization Usage API bucket when account and time-window data permit it.

### Anthropic API

Required upstream fields:

- `usage.input_tokens`;
- `usage.output_tokens`;
- `usage.cache_creation_input_tokens`;
- `usage.cache_read_input_tokens`;
- organization usage-report dimensions when available.

Acceptance criteria:

- retain every provider field without inventing a combined total that changes its meaning;
- reconcile request records with the organization Messages Usage Report.

### Codex

The supported structured surface is the versioned `codex exec --json`, TypeScript SDK, or app-server protocol. Current JSONL turn usage can provide input, cached-input, and output totals, while item events describe actions. Item events must not inherit turn tokens unless Codex supplies item-level usage.

Required implementation:

- capture the installed Codex version;
- consume only event variants defined by that version;
- preserve raw `turn.completed.usage` independently from item events;
- capture app-server token-usage notifications when available and correlate by thread and turn;
- retain raw fixtures for each supported version.

Acceptance criteria:

- every parsed field reconciles field-for-field with a fixture from the exact supported Codex version;
- absent reasoning detail remains absent rather than estimated.

### Claude Code

Claude Code CLI result metadata may be captured from its documented JSON surfaces. Definitive token accounting must come from the Anthropic API response, organization usage report, or an authorized versioned gateway capture that preserves the upstream usage object.

Required implementation:

- capture installed Claude Code version;
- capture documented CLI result metadata;
- instrument an authorized gateway or API-client boundary for token usage;
- correlate request IDs with Claude Code session IDs without retaining prompts or source content.

Acceptance criteria:

- request usage reconciles with gateway records and the organization usage report;
- no arbitrary transcript traversal is used as a substitute for a defined schema.

## Tool-level token consumption

A definitive result requires either provider-native item-level token fields or a controlled counterfactual experiment.

Required capture:

- task ID, task version, and completion assertion;
- repository commit and clean/dirty state;
- provider, model, client version, configuration hash, and context state;
- tool execution ID, tool type, normalized argument hash, timestamps, output bytes, and exit status;
- provider usage before and after the tool result enters context;
- a matched run in which the result is removed, cached, truncated, or replaced by a validated equivalent;
- successful completion of both runs.

Formula:

```text
incremental tool-related tokens =
  tokens with original tool result
  - tokens with validated alternative
```

Acceptance criteria:

- both variants satisfy the same completion assertion;
- the intervention is the only intended change;
- repeated runs and uncertainty are reported;
- the result is scoped to the tested task class, provider, model, client version, and intervention.

## Wasted tokens

A token is definitively wasted only when the associated work can be removed without reducing the validated task outcome.

Required capture:

- task and experiment identifiers;
- completion assertion and result;
- event lineage from request to tool call to later request;
- baseline and intervention totals;
- matched environment metadata;
- failure, retry, cancellation, and user-abort reasons.

Formula:

```text
validated wasted tokens = baseline tokens - intervention tokens
```

Duplicate calls, failures, retries, compaction, and large outputs are candidates for testing, not automatic waste.

## CPU and memory attribution

### Session attribution

Required capture:

- PID plus process start time;
- registered root process identity;
- observed parent identity at every sample;
- process group or session identifier where exposed by the operating system;
- explicit Soleaux session ID propagated to launched processes;
- sample timestamp and interval;
- cumulative user and system CPU counters;
- resident memory at every sample;
- process exit event.

Formulas:

```text
CPU seconds = delta cumulative user CPU + delta cumulative system CPU
memory byte-seconds = sum(resident bytes × sample interval seconds)
peak resident memory = maximum observed resident bytes
```

Acceptance criteria:

- reject PID reuse through start-time identity;
- attribute descendants only while ancestry or explicit registration links them;
- mark shared processes as shared rather than fully charging one session;
- validate each platform collector against canonical operating-system or runtime behavior.

### Tool attribution

Required capture:

- unique `toolExecutionId` before process launch;
- ID propagated in child environment;
- child PID and start time registered immediately;
- descendant samples linked to the tool execution;
- process exit and final cumulative CPU counters.

This produces direct tool-level CPU and memory measurements without executable-name guessing.

## Subscription or quota capacity

Definitive remaining capacity must come from an official provider API or authenticated provider surface. Token counts alone cannot reconstruct undisclosed or shared plan weighting.

Required capture:

- provider window identifier or stable label;
- reported used and remaining values;
- metric or unit;
- reset timestamp;
- observation timestamp;
- account and plan identifiers where permitted;
- capture method and client version.

Capacity consumed by an intervention is the provider-reported before/after delta in the same window. Soleaux must not convert token ratios into days.

## Soleaux savings

A definitive savings result requires a feature-specific controlled comparison.

Required fields per run:

- `taskId`, `taskVersion`, and `completionAssertionId`;
- `experimentId`;
- `variant`: baseline or named Soleaux control;
- provider, model, client version, repository commit, and configuration hash;
- measured tokens, request count, tool-call count, cumulative CPU seconds, memory byte-seconds, elapsed time, and quota observations;
- active Soleaux controls and configuration versions.

For successful matched tasks:

```text
token reduction = baseline tokens - intervention tokens
CPU reduction = baseline CPU seconds - intervention CPU seconds
memory reduction = baseline memory byte-seconds - intervention memory byte-seconds
elapsed reduction = baseline elapsed seconds - intervention elapsed seconds
```

Report sample count, median, distribution, and uncertainty. Do not extrapolate beyond the tested task class and environment.

## Implementation required for definitive results

1. Add versioned raw-event fixtures for Codex, Claude Code/gateway, OpenAI, and Anthropic.
2. Add request, turn, item, tool execution, task, experiment, assertion, and variant identifiers to the protocol.
3. Add tool-lifecycle and task-outcome registration endpoints to the daemon.
4. Capture cumulative OS CPU counters and interval-based resident-memory samples.
5. Persist raw measurements and immutable experiment metadata.
6. Add machine-verifiable completion assertions.
7. Add reconciliation tests between raw provider records, normalized events, and organization usage APIs.
8. Add matched baseline/intervention analysis with uncertainty.
9. Enable each result only after its evidence and acceptance tests pass.
