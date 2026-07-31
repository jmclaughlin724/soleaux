# Subagent Advanced Topics

Invocation channels, session-wide agent mode, resume mechanics, background execution, managed/plugin/CLI scopes, and other subagent behaviors beyond core frontmatter. Sibling to `subagent-configuration.md`.

## Invocation Escalation Ladder

Three channels, each stricter than the last about which subagent actually runs.

| Channel | Syntax | Guarantee |
| --- | --- | --- |
| Natural language | "Use the `code-reviewer` subagent to review…" | Claude _usually_ delegates; no hard guarantee |
| `@`-mention | `@"code-reviewer (agent)"` or `@agent-code-reviewer` | The named subagent runs; Claude still writes its task prompt |
| Session-wide | `claude --agent code-reviewer` or `"agent": "code-reviewer"` in `.claude/settings.json` | The _main thread itself_ takes on that subagent's system prompt, tools, and model |

**Plugin-provided subagents** use the scoped form: `@agent-<plugin-name>:<agent-name>` or `claude --agent <plugin-name>:<agent-name>`.

**CLI flag vs settings key:** If both `--agent <name>` and `"agent": "..."` in `.claude/settings.json` are present, the CLI flag wins. The choice persists across `/resume`.

**Session-wide semantic:** `--agent` _replaces_ the default Claude Code system prompt — same as `--system-prompt` — with the subagent's body. `CLAUDE.md` and project memory still load through the normal message flow. The agent name appears as `@<name>` in the startup header.

## `initialPrompt`

Auto-submits a first user turn only when the agent runs as the main session via `--agent` or the `"agent"` setting. Does _not_ fire when spawned as a subagent through the Agent tool. Commands (`/foo`) and skills are processed. Prepended to any user-provided prompt.

See `subagent-examples.md` for a worked example.

## `claude agents` CLI

Lists all configured subagents grouped by source without starting an interactive session. Shows which are **overridden** by higher-priority definitions — the cheapest way to audit duplicate `name:` fields across scopes.

```bash
claude agents
```

## `/agents`

As of v2.1.198 `/agents` no longer opens the interactive creation wizard; it prints a reminder to ask Claude or edit `.claude/agents/` directly. Subagent files, frontmatter fields, and the `.claude/agents/` and `~/.claude/agents/` locations are unchanged. Use `claude agents` above to audit definitions.

## Disable a Specific Subagent

Block auto-invocation without deleting the definition:

```json
// .claude/settings.json (or settings.local.json)
{
  "permissions": {
    "deny": ["Agent(Explore)", "Agent(my-custom-agent)"]
  }
}
```

Or via CLI for a single session:

```bash
claude --disallowedTools "Agent(Explore)"
```

Works for built-in and custom subagents.

## Scope Priority (Authoritative)

Managed settings win over everything. This corrects an earlier priority table that omitted the managed scope.

| Priority | Location | Scope | Notes |
| --- | --- | --- | --- |
| 1 (highest) | Managed settings `.claude/agents/` | Org-wide | Deployed via managed settings directory; overrides project + user |
| 2 | `--agents` CLI flag | Session | JSON payload, not saved to disk |
| 3 | `.claude/agents/` | Project | VCS-shareable; found by walking up from cwd |
| 4 | `~/.claude/agents/` | User | Personal, across projects |
| 5 (lowest) | Plugin `agents/` | Plugin-scoped | Via installed plugins |

Directories added with `--add-dir` **are** scanned: a `.claude/agents/` folder inside an added directory loads alongside project subagents. To share subagents across projects without `--add-dir`, use `~/.claude/agents/` or a plugin.

## CLI-Defined Subagents

Pass JSON via `--agents`. Exists only for the session; not saved to disk. Useful for quick testing and automation scripts:

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer. Focus on quality and security.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  }
}'
```

Accepts the same frontmatter fields as file-based subagents. `prompt` replaces the markdown body.

## Managed Subagents

Deployed by org admins via the managed settings directory (`.claude/agents/` inside managed settings). Same frontmatter format as project/user subagents. **Take precedence over project and user definitions with the same name** — important for compliance-enforced review or deployment agents.

## Plugin Subagent Restrictions

Plugin-provided subagents **do not support** the following frontmatter fields. They are silently ignored when loading:

- `hooks`
- `mcpServers`
- `permissionMode`

To use those fields, copy the agent into `.claude/agents/` or `~/.claude/agents/`. `permissions.allow` in `settings.json` / `settings.local.json` can grant session-wide equivalents, but not per-agent scoping.

## Resume Mechanics

Each subagent invocation creates a new instance with fresh context. To continue a prior subagent instead of starting over, Claude uses the `SendMessage` tool with the agent's ID as `to`. The subagent retains its full conversation history.

Constraints:

- `SendMessage` does **not** require agent teams to be enabled. Only structured team-protocol messages such as `shutdown_request` and `plan_approval_response` do, so subagent resume is available without `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`.
- A completed or `TaskStop`-stopped subagent that receives `SendMessage` **auto-resumes in the background** with no new `Agent` invocation. As of v2.1.191 a subagent the user stopped does not auto-resume; the send returns a refusal until the user types into that subagent's transcript.
- As of v2.1.199 `SendMessage` refuses a name that now reaches a different agent than it did earlier in the conversation, and reports which agent holds the name. Address the earlier agent by the agent ID returned when it was spawned.
- Transcripts persist independently of the main conversation's compaction state.

## Transcript Paths

```
~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl
```

- Main conversation compaction does not affect subagent transcripts.
- Transcripts persist within their session; resumable after restarting Claude Code.
- Retention: `cleanupPeriodDays` setting (default 30).

## Auto-Compaction

Subagents auto-compact using the same logic as the main conversation, default ~95% capacity. Override with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (integer percent).

Compaction events appear in transcripts:

```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "compactMetadata": { "trigger": "auto", "preTokens": 167189 }
}
```

## Background Execution

Background subagents run concurrently with the main conversation. As of v2.1.198 background is the **default** when `background` is unset, so the narrowed tool set below is the ordinary path rather than the exception.

As of v2.1.186, when a background subagent reaches a tool call needing permission, the prompt surfaces in the main session and names the subagent asking. Approve to continue, or press Esc to deny that one call without stopping the subagent. Before v2.1.186 background subagents auto-denied any call that would have prompted.

Controls:

- `background: true` frontmatter always runs the agent in the background.
- `background: false` forces foreground when the result is needed in the invoking turn.
- `Ctrl+B` sends a running foreground task to the background mid-run.
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` disables background tasks entirely.

## Tool Filters

Subagents inherit the main conversation's built-in and MCP tools, narrowed by two filters. Forks skip both and receive the parent's exact tool pool.

**First filter — every subagent**, even when the tool is named in `tools`: `Agent` (only at the depth limit), `AskUserQuestion`, `EndConversation`, `EnterPlanMode`, `ExitPlanMode` (unless `permissionMode: plan`), `ScheduleWakeup`, `TaskOutput`, `WaitForMcpServers`, `Workflow`.

**Second filter — background subagents only.** A background subagent **keeps every MCP tool** and retains only these built-ins:

`Read`, `Grep`, `Glob`, `Bash`, `PowerShell`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`, `TodoWrite`, `Skill`, `ToolSearch`, `EnterWorktree`, `ExitWorktree`, `Monitor`, `TaskStop`, `SendMessage`, `Artifact`.

Every other built-in is removed, inherited or explicitly listed. MCP access is therefore not a reason to force foreground; needing a built-in outside this list is. The same definition resolves to different tools in each mode, and removal is silent unless it leaves `tools` resolving to nothing, which fails the spawn.

Because `TaskOutput` is removed by the first filter, a subagent can never poll another agent's output. A background subagent's result reaches the parent as a completion notification.

## Runtime Limits

| Limit | Default | Override | Since |
| --- | --- | --- | --- |
| Subagents per session | 200 | `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` | v2.1.212 |
| Concurrent subagents | 20 | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` | v2.1.217 |
| Spawn depth below main | 3 layers | `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` | v2.1.217 |

The session limit cannot be disabled and resets on `/clear`. Exceeding concurrency fails the spawn with `Concurrent subagent limit reached` and instructs Claude not to retry; ultracode sessions are exempt. Size a fan-out against these numbers rather than an authored heuristic.

A subagent can spawn subagents of its own by default. At the depth limit Claude Code withholds `Agent` from every subagent except a fork, where the tool remains listed but returns an error. To keep one subagent read-only, omit `Agent` from `tools` or add it to `disallowedTools`.

## MCP Context Isolation

Defining an MCP server inline under a subagent's `mcpServers:` keeps its tool descriptions _out of the main conversation's context_. The subagent gets the tools; the parent does not. This is a concrete optimization for heavy MCP servers (Playwright, database MCPs) that would otherwise consume ~2-10KB of startup context in every session.

Inline servers support the same types as `.mcp.json`: `stdio`, `http`, `sse`, `ws`. String references share the parent session's connection; inline definitions connect on spawn and disconnect on finish.

## `/btw` as a Subagent Alternative

For quick questions about something already in the current conversation, `/btw` is cheaper than spawning a subagent:

- Sees full conversation context
- No tool access
- The answer is discarded rather than added to history

Use `/btw` when the question is conversational; use a subagent when it requires tool calls or produces output you want recorded.

## Permission Mode: `auto`

In addition to the five modes in `subagent-configuration.md`, Claude Code supports a sixth mode:

- **`auto`** — a background classifier reviews commands and protected-directory writes. The classifier evaluates each tool call with the session's block/allow rules before it runs.

Parent/child override precedence:

- If the parent uses `bypassPermissions` or `acceptEdits`, that takes precedence and child subagents **cannot** override it.
- If the parent uses `auto`, the subagent's `permissionMode` frontmatter is **ignored** — the classifier evaluates its tool calls with the parent's rules.
- `bypassPermissions` writes to protected directories **without prompting**, including `.git`, `.config/git`, `.claude`, `.vscode`, `.idea`, `.husky`, `.cargo`, `.devcontainer`, `.yarn`, and `.mvn`. What still prompts under it: explicit `ask` rules, connector tools an organization set to `ask`, MCP tools marked `requiresUserInteraction`, and root or home directory removals such as `rm -rf /`.

## Sources

- [Claude Code Sub-Agents](https://code.claude.com/docs/en/sub-agents)
- [Permission Modes](https://code.claude.com/docs/en/permission-modes)
- Parent reference: `subagent-configuration.md`
