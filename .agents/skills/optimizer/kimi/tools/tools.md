# Kimi Built-in Tools

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/reference/tools.html

## Intent

Use this file whenever a name is being written into a `tools` or `disallowedTools` allowlist, a `[tools]` policy, or a `[[permission.rules]]` pattern. Built-in tool names are matched **case-sensitive and exact**, so a name borrowed from the Claude or Codex lane silently matches nothing — the allowlist appears configured and constrains nothing.

## Name Divergence

The traps, before the full inventory:

| Kimi | Claude equivalent | Note |
| --- | --- | --- |
| `FetchURL` | `WebFetch` | different name, same job |
| `TodoList` | `TodoWrite` | one tool for the whole list |
| `ReadMediaFile` | image read via `Read` | separate tool in Kimi |
| `Agent` | `Task` / `Agent` | spawns one sub-agent |
| `AgentSwarm` | no equivalent | item-based fan-out |
| `TaskList`, `TaskOutput`, `TaskStop` | background-shell tools | Kimi models them as tasks |
| `CronCreate`, `CronList`, `CronDelete` | scheduling varies | see limits below |

`Read`, `Write`, `Edit`, `Grep`, `Glob`, `Bash`, `WebSearch`, `EnterPlanMode`, `ExitPlanMode`, `Skill`, and `AskUserQuestion` carry the names you expect.

## Inventory and Default Approval

| Tool | Approval | Behavior |
| --- | --- | --- |
| `Read` | auto | text file read, optional line offset and count |
| `Write` | approval | create or overwrite, parent directories created |
| `Edit` | approval | exact-string replacement, multi-match mode available |
| `Grep` | auto | ripgrep-backed regex with file filtering |
| `Glob` | auto | honors `.gitignore`, sorts by modification time |
| `ReadMediaFile` | auto | image and video to 100 MB, needs a vision-capable model |
| `Bash` | approval | `command`, `cwd`, `timeout`, `run_in_background`, `description` |
| `WebSearch` | auto | requires a host implementation |
| `FetchURL` | auto | fetch with HTML text extraction |
| `EnterPlanMode` | auto | restricts writes to the plan file |
| `ExitPlanMode` | user confirmation | submits the plan |
| `TodoList` | auto | pending / in-progress / done |
| `Agent` | auto | one sub-agent, foreground or background |
| `AgentSwarm` | auto in swarm mode, otherwise approval | up to 128 item-based sub-agents from one template |
| `AskUserQuestion` | auto | 1–4 structured questions |
| `Skill` | auto | inline skill invocation, 3-level nesting cap |
| `TaskList` | auto | list background tasks |
| `TaskOutput` | auto | read a background task's output; full log on disk |
| `TaskStop` | approval | stop a task, optional reason |
| `CronCreate` | approval | 5-field cron expression |
| `CronList` | auto | active tasks with fire times |
| `CronDelete` | approval | cancel a scheduled task |

Approval defaults are the starting point; `default_permission_mode` and `[[permission.rules]]` in `config.toml` move them, and `/yolo` discards them.

## Limits Worth Knowing

- `Bash` defaults to 60 s in the foreground and 600 s in the background. `description` is required for a background task. `[background].bash_auto_background_on_timeout` can promote a timed-out foreground command.
- Scheduled tasks cap at 50 active per session; recurring tasks go stale after 7 days. `KIMI_DISABLE_CRON=1` turns the surface off.
- `AgentSwarm` fans out to 128. Combined with auto-approval in swarm mode, that is a large blast radius on a repository — see the `/swarm` and `/yolo` notes in [`sessions-goals-and-commands.md`](../commands/sessions-goals-and-commands.md).

## Filtering

`[tools].enabled` is an allowlist; a non-empty value restricts availability. `[tools].disabled` is a denylist applied after it. Per-agent `tools` and `disallowedTools` compose the same way, and Kimi enforces the list a second time before execution rather than trusting the tool set it advertised to the model.

Built-in tools match by exact name. MCP tools match by glob against `mcp__<server>__<tool>`, covered in [`mcp.md`](mcp.md).
