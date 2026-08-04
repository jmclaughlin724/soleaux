# Local Codex Subagents

Source verified 2026-08-03:

- https://learn.chatgpt.com/docs/agent-configuration/subagents
- https://learn.chatgpt.com/docs/config-file/config-reference

## Contents

- [Local Codex Subagents](#local-codex-subagents)
  - [Prompted Delegation](#prompted-delegation)
  - [Custom Agents](#custom-agents)
  - [Global Controls](#global-controls)
  - [Permissions And Lifecycle](#permissions-and-lifecycle)
  - [Parallel Implementation](#parallel-implementation)
  - [Verification](#verification)

## Prompted Delegation

Current local Codex releases enable subagent workflows by default and delegate when you ask directly or when an applicable `AGENTS.md` or skill instruction requests it. Do not invent a feature flag.

A good subagent prompt explains how to divide the work, whether Codex must wait for all agents before continuing, and what summary or output to return:

```text
Review this change with parallel subagents. Spawn one subagent for security risks, one for test
gaps, and one for maintainability. Wait for all three, then summarize the findings by category
with file references.
```

Use built-in `explorer` for read-heavy mapping, `worker` for bounded implementation, and `default` as the general fallback. Codex can also choose a model that balances intelligence, speed, and price for the task when you do not pin one.

### Why subagent workflows help

Even with large context windows, models degrade when the main thread fills with noisy intermediate output (exploration notes, test logs, stack traces, command output). This is often called **context pollution** or **context rot**. Subagent workflows help by moving noisy work off the main thread: keep the main agent focused on requirements, decisions, and final outputs; run specialized subagents in parallel for exploration, tests, or log analysis; return summaries instead of raw intermediate output.

As a starting point, use parallel agents for read-heavy tasks such as exploration, tests, triage, and summarization. Be more careful with parallel write-heavy workflows, because agents editing code at once can create conflicts and increase coordination overhead.

## Custom Agents

Store personal roles under `~/.codex/agents/` and project roles under `.codex/agents/`. Each standalone TOML file defines one custom agent and must define nonblank `name`, `description`, and `developer_instructions`. Codex loads these files as configuration layers for spawned sessions, so custom agents can override the same settings as a normal session config.

```toml
name = "reviewer"
description = "Read-only reviewer for correctness, security, and missing tests."
model = "gpt-5.6"
model_reasoning_effort = "high"
sandbox_mode = "read-only"
developer_instructions = """
Inspect the assigned slice only.
Lead with evidence-backed findings and exact file references.
Return validation performed, uncertainty, and blockers; do not edit files.
"""
```

If a custom agent file sets `model` or `model_reasoning_effort`, the value in the file takes precedence. Otherwise Codex resolves each setting independently: an explicit spawn value, then the corresponding `[agents]` default, then the parent's value. Other session settings, such as `sandbox_mode`, `mcp_servers`, and `skills.config`, inherit from the parent when the custom agent file omits them.

A custom role whose `name` matches `default`, `worker`, or `explorer` overrides that built-in role. Codex identifies the custom agent by its `name` field; matching the filename is the simplest convention.

### Choosing models and reasoning

For most tasks in Codex, start with `gpt-5.6`. Use `gpt-5.6-terra` when you want a faster, lower-cost option for lighter subagent work such as exploration, read-heavy scans, or large-file review.

Reasoning effort (`model_reasoning_effort`): `ultra` for the deepest reasoning; `max` or `xhigh` for especially demanding reasoning; `high` for tracing complex logic or checking assumptions; `medium` as a balanced default; `low` when the task is straightforward and speed matters. Higher reasoning effort increases response time and token usage but can improve quality for complex work.

Preserve inherited model and `model_reasoning_effort` unless a role-specific measured reason justifies pinning them. Invoke `openai-docs` before adding or changing model, reasoning, MCP, sandbox, or other Codex configuration keys.

## Global Controls

Global subagent settings live under `[agents]` in Codex configuration:

```toml
[agents]
max_concurrent_threads_per_session = 6
```

| Field | Type | Required | Purpose |
| --- | --- | :-: | --- |
| `agents.enabled` | boolean | No | Enable or disable multi-agent tools. Defaults to `true`. |
| `agents.max_concurrent_threads_per_session` | number | No | Cap concurrently open spawned-agent threads, excluding the primary. Codex chooses the default when unset. `max_threads` remains accepted as a legacy alias. |
| `agents.default_subagent_model` | string | No | Set the default model for spawned agents. |
| `agents.default_subagent_reasoning_effort` | string | No | Set the default reasoning effort for spawned agents. |
| `agents.interrupt_message` | boolean | No | Record a model-visible message when an agent turn is interrupted. Defaults to `true`. |

Explicit spawn values override `agents.default_subagent_model` and `agents.default_subagent_reasoning_effort`.

## Permissions And Lifecycle

- Subagents inherit the current parent sandbox and permission mode. A custom agent sandbox is a configuration default, not authority to exceed live parent-turn restrictions.
- Interactive live overrides can supersede role defaults. Inspect effective behavior instead of inferring it from TOML alone.
- A noninteractive child cannot obtain a fresh approval that its runner cannot surface; require it to finish within pre-authorized permissions or return the blocker.
- Use the available task activity UI or `/agent` in the CLI to inspect agent threads. Ask the parent to steer, stop, or close agents; do not send user input directly to a child thread.
- Codex waits until all requested results are available, then returns a consolidated response.

## Parallel Implementation

Treat writes as a separate phase to avoid conflicts and coordination overhead:

1. Fan out read-only discovery and test planning.
2. Let the parent accept the synthesis and partition exact, non-overlapping owners.
3. Assign each worker only its named files or boundary.
4. Prevent workers from rewriting shared manifests, lockfiles, generated owners, or the same source file concurrently.
5. Let the parent integrate, re-read the combined diff, and run final validation.

If safe ownership cannot be partitioned, keep implementation with one agent. Prefer one agent for short work, a dependency chain where each step needs the prior result, a fixed deterministic graph, one dominant slow external operation, or any task whose ownership is still unclear.

### Serial objective with parallel subagents

A persistent serial objective (such as a Codex Goal or a multi-step plan) can still use parallel subagents as a tactic within individual steps. The coordinator holds the objective and the iteration loop; at each step, if independent workstreams exist, it dispatches parallel subagents, then integrates their results and continues. Parallelism is optional per step, not a structural requirement of the objective itself.

## Verification

- Confirm every requested agent returned, failed explicitly, or was intentionally cancelled, and completed threads were closed.
- Inspect role selection, applied instructions, effective sandbox and permission behavior, and the required return shape in a fresh task.
- For custom agents, validate TOML with the repository's owning command and run `codex --strict-config doctor --json`; require successful config loading and no agent-role startup warning.
- Exercise a live parent permission override and relevant noninteractive behavior when permission semantics changed.
