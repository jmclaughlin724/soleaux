# Kimi Hooks Playbook

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/customization/hooks.html

## Intent

Use this file before assuming a Kimi session inherits any lifecycle enforcement this repository delivers. It does not. Read it also before porting a Claude or Codex handler to Kimi, because the blockable set and the failure posture are both narrower.

## This Repository Cannot Register a Kimi Hook

Kimi hooks are `[[hooks]]` array entries in `<KIMI_CODE_HOME>/config.toml` — user-level only. **No project-scoped hook registration exists**, and the only project files Kimi reads are `.kimi-code/{skills,agents,mcp.json,local.toml,AGENTS.md}`. Nothing in the tree can install a Kimi `PreToolUse` gate.

What that means in practice:

- The Codex and Claude lifecycle handlers registered under [the agent-surface ownership table](../../../../../AGENTS.md) do not run for a Kimi session.
- Repo-owned enforcement that still applies is the Git-client lane (`pnpm check:hooks`) and the owner audits, because those run on commit or on demand rather than per tool call.
- A per-machine Kimi hook is a personal setting, not a repository control. Do not describe one as a repository guarantee, and do not count it as coverage in a review.

## Events

`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionResult`, `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`, `Stop`, `StopFailure`, `Interrupt`, `PreCompact`, `PostCompact`, `Notification`.

**Only `PreToolUse`, `Stop`, and `UserPromptSubmit` are blockable.** Every other event is fire-and-forget: its return value is ignored entirely. The Claude event map in [`hooks-reference.md`](../../claude/hooks/hooks-reference.md) marks far more events as blocking, so a handler ported from that lane onto, say, `SubagentStop` or `PermissionRequest` will run and appear healthy while enforcing nothing.

## Registration

```toml
[[hooks]]
event = "PreToolUse"
command = "node .codex/hooks/PreToolUse/example.mjs"
matcher = "Bash"
timeout = 30
```

`event` and `command` are required. `matcher` is a regex against the event target; omit it to match everything. `timeout` is an integer from 1 to 600 seconds, defaulting to 30.

Multiple matching rules run in parallel, and identical commands are deduplicated to a single execution.

## Handler Contract

Every event delivers a base object on stdin, with event-specific fields added in `snake_case`:

```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "session_xyz",
  "cwd": "/project/path"
}
```

Note the mixed convention: event payload fields are `snake_case`, but the decision object a hook writes back is `camelCase`.

```json
{
  "hookSpecificOutput": {
    "permissionDecision": "deny",
    "permissionDecisionReason": "explanation"
  }
}
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | allow; stdout is appended to context |
| `2` | block, using stderr as the reason — blockable events only |
| any other non-zero | error, treated as allow |
| timeout or crash | treated as allow |

## Fail-Open Is the Design

A Kimi hook that errors, times out, or crashes **allows the operation**. It is an advisory layer, not a security boundary, and it cannot be made into one by careful handler code — the failure path is outside the handler. Where a control must hold, put it in a surface that fails closed.

This inverts the posture the repository's own Codex handlers are written to. When reviewing a handler intended to run on both platforms, verify the Kimi path separately rather than reasoning from the Codex behavior.

Handlers execute in the session's project directory, in a separate process group on non-Windows hosts.
