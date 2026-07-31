# Agent System Prompt Patterns

10 proven patterns for writing effective agent system prompts, derived from official Anthropic documentation and verified codebase patterns.

## Pattern 1: Role + Domain

Define a narrow expert identity: `You are a [role] specializing in [narrow domain].`

Narrow roles produce focused, expert behavior. Broad roles ("helpful assistant") cause attention spread.

## Pattern 2: Mission + Boundaries

```markdown
### You do:

- [Primary responsibility 1-3]

### You do NOT:

- [Explicit exclusion 1-3]
```

Explicit boundaries prevent scope creep. The "do NOT" list is especially important.

## Pattern 3: Tool-Usage

```markdown
Use these in order:

1. **Read** - Load relevant files before making suggestions
2. **Grep** - Search for patterns across the codebase
3. **Bash** - Execute commands for verification

**Rule:** Always gather context before answering. Never guess about code structure.
```

## Pattern 4: Action-Default

**Proactive agents:** `<default_to_action>` — implement changes rather than suggesting.

**Cautious agents:** `<do_not_act_before_instructions>` — analyze before recommending.

## Pattern 5: Guardrail

```markdown
**STOP and ask before:**

- Deleting files, destructive git commands, modifying production config

**NEVER do:**

- Access secrets, push directly to main
```

## Pattern 6: Style + Attitude

| Role        | Recommended Attitude                             |
| ----------- | ------------------------------------------------ |
| Reviewer    | Critical, thorough, assumes nothing              |
| Debugger    | Methodical, evidence-based, hypothesis-driven    |
| Architect   | Thoughtful, considers trade-offs, asks questions |
| Implementer | Action-oriented, pragmatic, ships quickly        |

## Pattern 7: Process/Output-Shape

```markdown
## Process

1. **Restate** - Summarize the task
2. **Gather** - Find relevant code/context
3. **Analyze** - Identify issues
4. **Execute** - Make changes
5. **Verify** - Confirm success
6. **Report** - Summarize results

## Output Format

**Summary:** [overview] **Changes Made:** - [File:line] - [Change] **Verification:** - [Command]: [Result] **Concerns:** - [Issues or follow-ups]
```

## Pattern 8: Permission/Delegation

```markdown
## Delegation

- **security-auditor** - For vulnerability analysis
- **database-admin** - For schema changes

Use the `Agent` tool with a clear, focused prompt and explicit ownership.
```

## Pattern 9: Narrow Tools + Context

```markdown
**Focus on:** `src/`, `tests/`, files matching error stack trace **Ignore:** `node_modules/`, `dist/`, `.next/`

Read only what's needed.
```

## Pattern 10: Self-Critique

```markdown
Before claiming completion:

- Re-read changes, confirm type-checking, check for side effects

If uncertain:

- Say "I'm not sure" rather than guessing
- Flag assumptions explicitly
```

---

## Agent Hooks

Agents define lifecycle hooks in YAML frontmatter, scoped to the agent's lifetime.

| Event         | When Fired                           | Can Block? |
| ------------- | ------------------------------------ | ---------- |
| `PreToolUse`  | Before agent executes a tool         | Yes        |
| `PostToolUse` | After a tool completes               | No         |
| `Stop`        | When agent finishes (→ SubagentStop) | Yes        |

Hook types: `command` (shell), `prompt` (single-turn LLM yes/no), `agent` (multi-turn subagent with tools).

```yaml
---
name: thorough-developer
hooks:
  PreToolUse:
    - matcher: "Write|Edit"
      hooks:
        - type: command
          command: ".claude/hooks/lint-check.sh"
  Stop:
    - hooks:
        - type: prompt
          prompt: "Check if all tasks are complete. $ARGUMENTS"
---
```

## Agent Frontmatter Reference

[`subagent-configuration.md`](subagent-configuration.md) owns the field table. Two repo constraints apply on top of it: do not set `memory:` (see [subagent-memory.md](subagent-memory.md)), and preloading via `skills:` puts the full `SKILL.md` body in startup context, so runtime invocation through `Skill` in `tools:` is usually cheaper (see [subagent-skill-runtime.md](subagent-skill-runtime.md)).

### Model Selection

| Model  | Best For                         |
| ------ | -------------------------------- |
| haiku  | Fast searches, simple analysis   |
| sonnet | General coding, complex analysis |
| opus   | Architecture, nuanced reasoning  |

## Permission Modes

[`subagent-configuration.md`](subagent-configuration.md) owns the mode table, and [`subagent-advanced.md`](subagent-advanced.md) owns parent/child override precedence. See [permissions-and-settings.md](../config/permissions-and-settings.md) for rule syntax, path patterns, sandbox interaction, and managed-only settings.

## disallowedTools

Restrict which tools an agent can use by listing them in the `disallowedTools` field. This is the inverse of `tools` — everything is allowed except what you list.

```yaml
disallowedTools: Bash(rm *), Bash(git push --force*)
```

Use `disallowedTools` when you want broad access with specific command-family exclusions.

## Spawn Restrictions

The `Agent(agent_type)` allowlist applies **only** to an agent running as the main thread through `claude --agent`:

```yaml
tools: Agent(worker, researcher)
```

Inside a subagent definition the type list in parentheses is ignored: listing `Agent` lets that subagent spawn subagents of its own while the depth limit allows it, with no restriction on which types. To stop a subagent from spawning at all, omit `Agent` from `tools` or add it to `disallowedTools`. See [subagent-advanced.md](subagent-advanced.md) for the depth limit.

## MCP Tool Filtering

Include specific MCP tools alongside built-in tools in the `tools` field:

```yaml
tools: Read, Grep, mcp__supabase_main__list_tables
```

List individual MCP tools to grant surgical access without exposing a server's full toolset. [`subagent-mcp.md`](subagent-mcp.md) owns the naming patterns and scoping forms.

## Background vs Foreground Agents

Background is the default as of v2.1.198. A background subagent keeps every MCP tool and loses most built-ins; [`subagent-advanced.md`](subagent-advanced.md) owns the two tool filters, the retained built-in list, permission-prompt surfacing, and the runtime limits.

## Memory-Aware Agent Design

- Keep the agent prompt focused on role, boundaries, tools, and output shape.
- Put reusable project rules in `CLAUDE.md` or `.claude/rules`, not in every agent prompt.
- Preload `skills` only when the agent needs them on nearly every run; otherwise let the agent invoke them when relevant.
- Do not enable `memory:` on agents in this repo. Promote durable lessons into rules, hooks, skills, or the relevant `AGENTS.md` instead.

Background agents suit independent work whose result the parent does not need in the same turn: running tests, searching code, generating reports. Force foreground with `background: false` when the parent needs the result in the invoking turn, or when the agent needs a built-in tool the background filter removes. MCP access is not a reason to force foreground.

## Anti-Patterns

| Anti-Pattern | Fix |
| --- | --- |
| "Be helpful" | Define specific role and expertise |
| No scope boundaries | Add explicit "do NOT" list |
| "Use your judgment" | Provide clear decision criteria |
| Missing output format | Specify exact structure |
| Tool list without guidance | Explain when to use each tool |
| Missing `skills` preload | Add `skills` for deterministic preloading; keep description for discovery |
| Explicit `tools:` allowlist without `Skill` | Subagent cannot load a skill at runtime; add `Skill` to `tools:`, omit `tools:` entirely, or inline the needed skill context in the spawn prompt. See [subagent-skill-runtime.md](subagent-skill-runtime.md) |
| No verification step | Add quality check before completion |
| Overly complex process | Keep to 5-7 clear steps |

## Sources

- [Claude Code Agents](https://code.claude.com/docs/en/agents)
- [Agent Skills Best Practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Claude Code Hooks](https://code.claude.com/docs/en/hooks-guide)
