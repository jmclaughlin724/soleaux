---
name: team
description: "Run parallel multi-agent and subagent workflows across OpenAI runtimes."
---

# Team

## Contract

Design or run bounded parallel agent workflows while keeping scope, authority, orchestration policy, reconciliation, final validation, and the completion claim with the coordinating parent or application. Select the user-facing reply owner explicitly: keep it with the manager for manager-style workflows, or transfer it to the specialist for an intentional handoff. Explicit invocation authorizes subagent delegation inside the current task only when the decision gate passes; it does not authorize new side effects, external writes, broader permissions, or work outside the user's request.

Load only the reference for the active runtime. Finish when every requested workstream is accounted for, contradictions are resolved, and the parent has validated the integrated result.

## Runtime Selection

Classify the host before designing the workflow:

| Runtime | Load | Owner |
| --- | --- | --- |
| Responses API Multi-agent | [responses-api.md](references/responses-api.md) | Hosted collaboration tree; client request, developer tool loop, continuation, and rendering |
| OpenAI Agents SDK | [agents-sdk.md](references/agents-sdk.md) | Application runner, reply ownership, state, approvals, and tracing |
| ChatGPT Work | [chatgpt-work.md](references/chatgpt-work.md) | Hosted task prompt and available hosted tools |
| Codex app, CLI, or IDE | [codex-subagents.md](references/codex-subagents.md) | Prompt, `AGENTS.md` or skill, `.codex/agents/**`, and Codex config |

Do not translate settings between these runtimes. Responses API Multi-agent hosts its collaboration actions while the client owns developer-defined tool execution and continuation. The Agents SDK is code-first application orchestration. ChatGPT Work is hosted; local Codex consumes local agent and sandbox configuration.

## Decision Gate

Use parallel agents only when all of the following are true:

- At least two workstreams can proceed independently now.
- Each workstream has a concrete, bounded deliverable.
- Separate context improves speed, focus, or review coverage enough to justify added tokens.
- The parent can inspect the evidence and validate the combined outcome.
- Concurrent workers will not contend over the same files, records, credentials, or mutable state.

Prefer one agent for short work, a dependency chain where each step needs the prior result, a fixed deterministic graph, one dominant slow external operation, or any task whose ownership is still unclear.

## Direct Workflow

1. State the requested outcome, active runtime, closed scope, authority boundary, evidence, and stop condition.
2. Build a small dependency graph. Group independent nodes into parallel waves and keep dependent nodes sequential.
3. Assign the narrowest useful number of agents. Preserve one parent slot for coordination and integration; do not fill concurrency merely because it is available.
4. Give each agent a non-overlapping slice using the task contract below. Pass only the context it needs, including applicable owner instructions and exact source artifacts.
5. Spawn the independent workstreams together. Keep useful parent-side integration or discovery moving while workers run.
6. Collect every result required by the contract. Use the active runtime's messaging, follow-up, resume, or interruption controls; interrupt only when a worker is stale, unsafe, or obsolete.
7. Compare evidence, resolve contradictions, deduplicate findings, and reject unsupported child conclusions. Never forward a worker summary as final truth without parent review.
8. For implementation, re-read all changed owners, integrate in dependency order, and run the coordinator-owned validation. The coordinator owns the completion claim; the selected manager or handoff specialist owns the user-facing reply.

## Agent Task Contract

Give every worker this information:

```text
Objective: <one bounded outcome>
Scope: <exact paths, sources, records, or cases>
Mode: <investigate only | edit only these non-overlapping owners>
Context: <minimum required artifacts and prior decisions>
Instructions: <applicable owner rules and required skills>
Evidence: <facts, references, logs, or diffs to collect>
Validation: <focused checks the worker must run>
Return: <required result shape>
Stop: <completion condition and blockers that must return to the parent>
```

Require the result to identify scope covered, findings or changes, evidence, validation, uncertainty or contradictions, blockers, and the recommended next step. For implementation planning, also require an explicit add/update/remove/unchanged/excluded inventory; use `none` for empty categories.

## Safe Parallel Patterns

- **Evidence fan-out:** split repository mapping, official documentation, logs, tests, and skeptical review by evidence type, then reconcile before deciding.
- **Competing hypotheses:** assign independent failure theories and require reproduction evidence; do not tell workers the preferred conclusion.
- **Sharded review:** divide by risk category or non-overlapping component and require exact references plus severity.
- **Phased implementation:** run read-only discovery first, let the parent accept a design and partition ownership, then allow parallel edits only to non-overlapping mutable owners.
- **Structured batch:** use a row-per-item batch only for many homogeneous independent items with a schema, stable IDs, bounded concurrency, timeouts, and explicit worker reporting.

## Verification

Before closeout, verify:

1. Every requested workstream returned, failed explicitly, or was intentionally cancelled.
2. No two workers changed the same mutable owner without a deliberate parent integration step.
3. Material claims trace to source evidence, and conflicting evidence has a stated resolution.
4. Parent-owned tests or behavioral checks cover the integrated outcome.
5. When the runtime supports traces, inspect the complete fan-out/fan-in path and grade routing, handoffs, specialist and tool selection, approvals, guardrails, and instruction adherence.
6. When adopting a new parallel workflow, compare it with a single-agent baseline on representative tasks and measure end-to-end quality, latency, token use, and cost.

## Output Contract

Return the integrated outcome, the workstream split, evidence used, validation results, material uncertainty, and any precise blocker. Describe agent activity only when it helps the user verify the result.

## Boundaries

- Do not delegate vague ownership, approvals, final judgment, or external side effects.
- Do not use agents to bypass sandboxing, permissions, policy, or unavailable authority.
- Do not run overlapping write-heavy agents against shared mutable state.
- Do not equate ordinary Responses API parallel tool calls with hosted Responses Multi-agent. In the Agents SDK, parallel tool calls may invoke agents-as-tools; follow the runtime reference.
- Do not pin models, reasoning effort, beta headers, or concurrency beyond current official support without first invoking `openai-docs` and checking the active runtime.
- Stop when the split is not genuinely independent or the parent cannot validate the result.
