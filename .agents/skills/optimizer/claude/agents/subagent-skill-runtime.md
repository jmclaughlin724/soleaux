# Subagent Skill Runtime Contract

How subagents load skills at startup versus runtime, and how to convey skill context reliably during fan-out.

> **Sources:** [Claude Code sub-agents](https://code.claude.com/docs/en/sub-agents), [Claude Code skills](https://code.claude.com/docs/en/skills), [Claude Code hooks](https://code.claude.com/docs/en/hooks).

## Fresh subagents inherit nothing

A subagent spawned with a `subagent_type` — through the Task/Agent tool or a Workflow `agent()` call — starts at zero context. It does not carry the parent's loaded `SKILL.md` bodies, the parent's conversation, or any reference file the parent read. A `fork` subagent inherits the parent's conversation transcript but still not references the parent only linked and never opened. Skill context reaches a subagent only through one of the explicit paths below; nothing is implicit.

## The two skill-loading paths

| Path | Trigger | When it fires | Visibility to subagent |
| --- | --- | --- | --- |
| Preload | `skills:` field in `.claude/agents/<name>.md` frontmatter | At subagent startup, before its first turn | Full `SKILL.md` body injected into the prompt |
| Runtime | `Skill({ skill: "..." })` tool call | During the subagent's turn | Loads on demand, same shape as the parent |

Preload is deterministic and front-loads context cost. Runtime is on demand and depends on the `Skill` tool being callable. This repo defines no `.claude/agents/` tree, so the preload path applies only if a custom agent definition is added; built-in agent types rely on the runtime path or on context supplied in the spawn prompt.

## Runtime invocation precondition

A subagent can call `Skill({ skill: "..." })` only when the agent's `tools:` field either omits the allowlist entirely (the subagent inherits every parent tool, including `Skill`) or lists `Skill` explicitly. When `tools:` is an explicit allowlist that omits `Skill`, the framework filters `Skill` out of the tool inventory and the subagent cannot load a skill at runtime.

When adding a custom agent definition, keep `Skill` (and `Read`) in its `tools:` list or omit `tools:` to inherit all, so a fan-out worker can still load the skill bodies its slice needs.

## Hook visibility inside subagents

Claude Code fires `PreToolUse` for tool calls made inside a subagent and marks them with an `agent_id` field that is present only for in-subagent calls; `SubagentStart` is observability-only and cannot block. A hook that hard-denies tool calls should account for subagents that lack `Skill`/`Read` in their allowlist — inside such a worker there is no way to load a missing skill, so denial deadlocks it. Prefer advisory `additionalContext` output for in-subagent calls and keep hard enforcement in the main session.

## Conveying skill context to a subagent

Because nothing is inherited, deliver skill context explicitly, in rough order of reliability:

1. **Inline it in the spawn prompt.** Paste the `SKILL.md` body (or the exact reference the worker needs) into the prompt. Works even if the subagent lacks the `Skill` tool — the most portable method.
2. **Preload via `skills:`.** List the skill in the agent definition; the full body is injected at startup.
3. **Name the skill to invoke.** Instruct the worker to call `Skill({ skill: "x" })`. Requires the `Skill` tool.
4. **Give exact `Read` paths.** Point the worker at `.agents/skills/<name>/SKILL.md` and any reference paths. Requires the `Read` tool.

References are lazy everywhere (a markdown link is a `Read` on demand) and `@`-mentions are not expanded inside `SKILL.md`, so a reference the worker must have has to be inlined (1) or read by path (4).

## When a worker is locked out of runtime loading

If an agent is intentionally narrow (for example a read-only reviewer that must not write files) and it still lacks the skill body it needs:

1. The worker reports findings in its final message.
2. The orchestrator applies the change in the main session, where `Skill` is callable.
3. The orchestrator commits the slice with the worker named in the trailer.

This loses the parallelism benefit for that slice but never deadlocks.

## Review checklist

When creating or auditing an agent that may receive fan-out work:

- Does the agent's `tools:` list include `Skill` (or omit `tools:` entirely) so it can load skills at runtime?
- Does `skills:` preload the agent's known-required skills?
- If the agent is locked out of runtime loading, does the spawn prompt inline the needed context or include a "report findings instead of applying" escape clause?
