# Parallel Agent Dispatch Patterns

Best practices for dispatching parallel Claude agents, including file conflict prevention, optimal agent counts, and timeout configuration.

## Core Principles

| Principle | Rule | Why |
| --- | --- | --- |
| **Wave size** | Bounded by the 20-concurrent runtime limit | Excess spawns fail rather than queue |
| **File isolation** | No two agents touch the same file within a wave | Prevents merge conflicts and lost edits |
| **Single dispatch** | Send all of a wave's `Agent` calls in one message | They run concurrently; sequential calls do not |
| **Wait for the wave** | Synthesize only after every agent in the wave reports | Enables cross-validation |
| **Verification agent** | Always dispatch after parallel work completes | Validates integration of all changes |
| **Result validation** | Spot-check 2-3 claims from each agent | Reject hallucinated or inaccurate findings |
| **Contradiction flagging** | Explicitly note when agents disagree | Resolve before presenting to user |

## When to Use

**✅ Use when:**

- 3+ independent problems with no shared state
- Each agent works on different file domains
- Understanding one doesn't require context from others

**❌ Don't use when:**

- Fewer than 3 tasks (overhead not worth it)
- Multiple agents edit same files (use wave pattern instead)
- Sequential dependencies exist (Task B needs Task A's output)

## Wave Size

Size a wave against the runtime limits in [subagent-advanced.md](subagent-advanced.md), not an authored number: 20 concurrent subagents, 200 per session, three spawn layers below the main conversation. Exceeding concurrency fails the spawn with `Concurrent subagent limit reached` and tells Claude not to retry, so a wave larger than the limit loses work rather than queueing it.

Below that ceiling the practical constraint is synthesis cost: every agent's report is context the parent must read and cross-check. Prefer the smallest wave that covers the independent work.

## Dispatch and Collection

There is no polling API. `TaskOutput` is removed from every subagent by the first tool filter and is deprecated in favor of reading the task's output file. A background agent's result arrives as a completion notification in a later turn; a `background: false` agent returns its result in the invoking turn.

Issue every `Agent` call for a wave in a single message — that is what makes them concurrent. Separate messages run them one at a time.

Hold all output until the wave completes and validation finishes, so the user sees one consolidated report rather than a running commentary. Never predict or fabricate a pending agent's result.

## Result Validation

**Validation checklist:**

| Check | Action | If Fails |
| --- | --- | --- |
| **Consensus** | Do agents agree? | Flag contradictions |
| **Evidence** | Each finding cites file:line or URL? | Reject uncited claims |
| **Accuracy** | Spot-check 2-3 claims from each agent | Reject if source doesn't match |
| **Completeness** | Did agents answer the query? | Re-dispatch with clarification |

**Spot-check:** For each agent claim (e.g., "pattern at file:line"), use Read tool to verify. Reject if source doesn't match.

## Contradiction Resolution

1. Identify conflicting claims
2. Read source directly for ground truth
3. Reject wrong claims, keep verified claim
4. Present resolution with confidence level

## File Conflict Prevention

**Rule: Tasks in same wave MUST NOT touch same files.**

**Validation:** Check that no file appears in multiple tasks' `creates` or `modifies` arrays within the same wave. If conflict detected, move one task to next wave.

**Handling shared files (package.json, index.ts):**

- **Coordinator pattern:** Wave 2 (parallel) reports changes → Wave 3 (sequential) applies all atomically
- **Sequential wave:** Move shared file edits to separate wave

## Wave Execution Pattern

For tasks with dependencies:

```
Wave 1: Foundation (sequential)
Wave 2: Parallel work (no overlapping files, under the concurrency limit)
Wave 3: Shared updates (sequential, configs touched by Wave 2)
Wave 4: Verification (sequential)
```

**Rules:**

1. Tasks within wave: Must not edit same files
2. Waves execute: Sequentially
3. Tasks within waves: Execute in parallel
4. Shared configs: Must be in own wave
5. Always end with: Verification wave

## Verification Agent Pattern

**Always dispatch verification agent after parallel work.** Runs `typecheck test lint --force` on affected packages. Reports pass/fail before proceeding to next wave.

Metadata: `wave: N+1`, `agentType: "verification"`, `parallelSafe: false`

## Task Metadata for Parallel Execution

Required fields:

- `wave`: Execution order (1 = foundation, 2+ = depends on prior)
- `files.creates`/`files.modifies`: For conflict validation
- `parallelSafe: true`: Can run with other same-wave tasks
- `maxConcurrentAgents`: keep under the runtime limit of 20
- `requiresVerificationAgent: true`: Dispatch verification after
- `requiredSkills`: Skills agent MUST invoke before work

**Wave assignment:**

| Wave | Criteria                    | Example                        |
| ---- | --------------------------- | ------------------------------ |
| 1    | No dependencies, foundation | Migrations, schemas, types     |
| 2    | Depends on Wave 1           | Server Actions using new types |
| 3    | Depends on Wave 2           | UI using Server Actions        |
| 4    | Final integration           | Tests, documentation           |

## Quick Reference

| Aspect | Pattern | Why |
| --- | --- | --- |
| **Wave size** | Under the 20-concurrent limit; smallest that covers the work | Excess spawns fail; each report costs parent context |
| **Dispatch** | All of a wave's `Agent` calls in one message | Separate messages run sequentially |
| **Collection** | Wait for every agent's report | Validate before presentation |
| **Output timing** | AFTER all agents + validation | One complete report |
| **File conflicts** | Validate within waves | Prevents merge conflicts |
| **Shared files** | Coordinator pattern or separate wave | One atomic write |
| **Verification** | Always dispatch verification agent | Validates integration |
| **Contradictions** | Read source directly, resolve explicitly | Ground truth over agent claims |
| **Spot-check** | 2-3 claims per agent | Reject hallucinations |

## Pre-Dispatch Requirements

Before dispatching parallel agents, verify:

| Requirement           | Minimum                          |
| --------------------- | -------------------------------- |
| Independent tasks     | 3+ per wave                      |
| File domain isolation | No overlapping edits within wave |
| Agent count           | Under 20 concurrent              |
| Dispatch              | One message per wave             |
| Verification agent    | Included in final wave           |

## Foreground vs Background Execution

Background is the default when `background` is unset (v2.1.198+). [`subagent-advanced.md`](subagent-advanced.md) owns the two tool filters and the retained built-in list.

For orchestration, only two consequences matter:

- A background agent **keeps every MCP tool**. Needing `mcp__context7__*` or a database MCP is not a reason to force foreground.
- A background agent loses most built-ins. Force `background: false` when a worker needs one outside the retained set, or when the parent needs the result inside the invoking turn.

Permission prompts from a background agent surface in the main session and name the asking agent (v2.1.186+), so a background worker no longer silently auto-denies.

## Resume Subagent Pattern

Resume a previously completed subagent to continue work with full conversation history preserved. Resume is a `SendMessage` to the agent's ID or name — there is no `resume` parameter on the `Agent` tool, and a new `Agent` call always starts fresh.

```typescript
SendMessage({
  to: "agent-id-or-name",
  summary: "continue after migration",
  message:
    "Continue from where you left off. The migration has been applied — now update the Server Actions.",
});
```

[`subagent-advanced.md`](subagent-advanced.md) owns the resume constraints, including the user-stop and name-collision refusals.

**How it works:**

- Full conversation history is preserved from the prior run
- The resumed agent picks up exactly where it left off
- New prompt is appended to the existing history
- No re-reading of files or re-gathering of context needed

**Transcript storage:**

Subagent transcripts are stored at:

```
~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl
```

Each line is a JSON event (tool call, tool result, assistant message). Useful for:

- Debugging agent behavior after the fact
- Auditing what files an agent read or modified
- Understanding why an agent produced unexpected results

**When to use resume:**

| Scenario | Use Resume? | Why |
| --- | --- | --- |
| Agent completed Phase 1, needs Phase 2 | Yes | Avoids re-reading all Phase 1 context |
| Agent timed out mid-task | Yes | Continues from last checkpoint |
| Agent produced wrong results | No | Fresh start avoids compounding errors |
| Different task, same domain | No | New task needs clean context |

**Anti-pattern:** Do not resume an agent that produced incorrect output. Errors in history may compound. Dispatch a new agent with corrected instructions instead.

## Context Warnings

Parallel agents generate significant output. Without care, collecting results from many agents can exhaust the parent's context window.

### The Problem

Every agent's report lands in full in the parent conversation. With 4 agents each producing 2000+ tokens, one wave can consume 8000+ tokens of context.

### Mitigation Strategies

| Strategy | How | When |
| --- | --- | --- |
| **Request summaries** | Include "Return a 3-5 sentence summary" in agent prompt | Always for 3+ agents |
| **Structured output** | Request JSON or table format, not prose | When parsing results programmatically |
| **File-based output** | Agent writes to file, parent reads selectively | Large outputs (analysis reports) |
| **Incremental collection** | Process one agent's output before collecting next | When synthesis is sequential |

### Agent Prompt Pattern for Compact Output

```typescript
Agent({
  description: "Analyze auth patterns",
  prompt: `
    Search for authentication patterns in apps/web-admin/.

    Return ONLY a structured summary:
    - Pattern name: [name]
    - Files: [comma-separated paths]
    - Issues: [brief list]

    Do NOT include full file contents or lengthy explanations.
    Maximum 500 words.
  `,
});
```

### Independent Compaction

Each subagent maintains its own context window and compacts independently:

- Parent compaction does NOT affect running subagents
- Subagent compaction does NOT affect the parent
- Completed subagent output is frozen at collection time
- If a subagent compacts mid-run, it loses early context (same as main session)

**Implication:** Long-running agents (5+ minutes) may compact and lose early file reads. For critical context, instruct agents to re-read key files before producing final output.

## Path-Trigger Skill Gate Friction

When parallel workers are dispatched against slices spanning multiple subdirectories, each worker may touch files governed by different skills. Workers inherit no skill context, so name the relevant skills or inline the needed `SKILL.md` bodies in each spawn prompt. A worker whose `tools:` allowlist omits both `Skill` and `Read` cannot load a skill body at runtime at all; give such a worker the context inline or have it report findings for the orchestrator to apply. See [subagent-skill-runtime.md](subagent-skill-runtime.md).

The structural fix lives in agent definitions, not in orchestration prompts. See [subagent-skill-runtime.md](subagent-skill-runtime.md) for:

- The two skill-loading paths (preload vs. runtime)
- Compliant `tools:` configurations
- The fallback "report findings, orchestrator applies" pattern when an agent is intentionally locked out
- A repo-state audit of which agents currently lack runtime skill invocation

Before designing a parallel orchestration that spans path-trigger globs, verify the spawned agent type's `tools:` field includes `Skill` (or omits `tools:` entirely). A worker whose definition drops `Skill` cannot act on the gate's advisory and falls back to reporting findings for the orchestrator to apply.

## Related

- [workflow-patterns.md](../prompting/workflow-patterns.md) - Wave and execution details
- [agent-teams-patterns.md](agent-teams-patterns.md) - Official Agent Teams for inter-agent communication and collaborative work (experimental)
- [subagent-skill-runtime.md](subagent-skill-runtime.md) - skill loading paths, runtime invocation precondition, parallel-orchestration friction
