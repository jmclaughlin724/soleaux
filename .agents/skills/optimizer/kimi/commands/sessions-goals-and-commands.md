# Kimi Sessions, Goals, and Commands

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/guides/sessions.html
- https://www.kimi.com/code/docs/en/kimi-code-cli/guides/goals.html
- https://www.kimi.com/code/docs/en/kimi-code-cli/reference/slash-commands.html

## Intent

The runtime surface: how a session is started and resumed, how an autonomous goal reports its outcome, and which commands can damage repository state or leak it.

## Commands That Need Care Here

| Command | Why |
| --- | --- |
| `/init` | analyzes the codebase and generates `AGENTS.md`. Root `AGENTS.md` is the canonical, user-owned project brief — regenerating it discards hand-authored ownership. Never run against this repository. |
| `/yolo`, `/swarm` | `/yolo` skips tool approval and **auto-approves every MCP call**, including `soleaux` mutations. Swarm mode auto-approves `AgentSwarm`, which fans out to 128 sub-agents. |
| `/export-debug-zip` | bundles session logs and filesystem paths. Do not attach it to an issue or commit it. |
| `/add-dir` | writes `.kimi-code/local.toml` with absolute machine paths; that file stays gitignored. |
| `/mcp-config` | edits an MCP registry. For the project registry, edit `.kimi-code/mcp.json` deliberately — see [`mcp.md`](../tools/mcp.md). |

## Sessions

Sessions persist under `$KIMI_CODE_HOME/sessions/`, keyed by working directory, holding `state.json` and `agents/*/wire.jsonl`. **Hand-editing these files can corrupt restoration** — use the commands.

Launch and resume:

```
kimi                   # new session
kimi --continue        # most recent session in this directory
kimi --session <id>    # a specific session
kimi --session         # browse and pick
```

`--continue` and `--session` cannot be combined. `--agent` and `--agent-file` apply to new sessions only and are ignored when resuming.

In-session, while the agent is idle: `/new` (alias `/clear`), `/sessions` (`/resume`), `/fork`, `/title` (`/rename`, 200 characters), `/compact [hint]`, `/undo [count]`, `/reload`, `/reload-tui`, `/copy`, `/web`, `/tasks`.

Context compaction is automatic as the window fills, governed by `[loop_control].reserved_context_size`. `/compact` forces it early and takes a hint naming what to preserve.

Export with `/export-md [path]` or, outside the TUI, `kimi export <sessionId> [-o path] [--no-include-global-log]`. Omitting the id exports the most recent session; `-y` skips confirmation. Browser exports cap at 64 MiB, so large sessions must go through the CLI.

## Goals

`/goal <objective>` saves an objective, sends it as the next message, and enters goal mode; after each turn Kimi judges whether the objective is complete, blocked, paused, or still active. Write goals that name a finish line and the evidence proving it — a passing suite, a clean check — not a topic.

| Command                     | Effect                          |
| --------------------------- | ------------------------------- |
| `/goal`, `/goal status`     | current goal and progress       |
| `/goal pause`               | pause without deleting          |
| `/goal resume`              | resume a paused or blocked goal |
| `/goal cancel`              | remove the current goal         |
| `/goal replace <objective>` | substitute a new objective      |
| `/goal next <objective>`    | queue the next goal             |
| `/goal next manage`         | manage the queue                |

Prefix the objective with `--` when it starts with a reserved word (`status`, `pause`, `resume`, `cancel`, `replace`, `next`, `manage`).

**Non-interactive exit codes:** `0` complete, `3` blocked, `6` paused. Any automation that runs Kimi with a goal must distinguish these — a blocked goal exits non-zero without having failed, and treating `3` as an error inverts the meaning. Blocked covers needing input, being impossible as stated, and hitting a token budget.

Goals do not suit open-ended discussion, ambiguous objectives, or work with no verifiable finish.

## Mode and Status Commands

Modes: `/permission`, `/auto`, `/plan [on|off]`, `/plan clear`, `/yolo [on|off]`, `/swarm on|off`, `/swarm <task>`.

Status: `/status` (version, model, directory, base URL), `/usage`, `/mcp`, `/plugins`, `/version`, `/help`, `/btw [question]` for a side conversation in a forked sub-agent, `/feedback`.

Account and model: `/login`, `/logout`, `/provider`, `/model`, `/secondary_model`, `/settings` (`/config`), `/experiments`, `/editor`, `/theme`.

## Skill Commands

Built-in skills appear without a prefix: `/mcp-config`, `/update-config`, `/import-from-cc-codex`, `/sub-skill`, `/check-kimi-code-docs`, `/custom-theme`.

Repository skills activate as `/skill:<name>`, or `/<name>` when the shorthand does not collide with a built-in command, or `/<parent>.<sub-skill>` for a bundle. Collision matters: a skill named after a built-in loses the shorthand silently. See [`skills.md`](../skills/skills.md#invocation).
