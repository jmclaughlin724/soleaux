# Kimi Configuration and Data Locations

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files.html
- https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/data-locations.html
- Kimi Code CLI 0.30.0 binary, for the project-scope facts marked below

## Intent

Read this before adding a file under `.kimi-code/`, before reasoning about where a Kimi setting takes effect, and before any command that could put Kimi state into a diff, a log, or a report.

## What Is Project-Scoped

Binary-verified: the CLI reads exactly five project paths.

| Path | Purpose | Tracked here |
| --- | --- | --- |
| `.kimi-code/mcp.json` | project MCP registry | yes — see [`mcp.md`](../tools/mcp.md) |
| `.kimi-code/skills/` | project skills, ranked above `.agents/skills/` | no; absent |
| `.kimi-code/agents/` | project agents | no; absent |
| `.kimi-code/local.toml` | `[workspace].additional_dir` absolute paths | no; gitignored |
| `.kimi-code/AGENTS.md` | project brief | no; absent |

**There is no project-scope `config.toml` discovery.** The path resolves as `input.configPath ?? join(resolveKimiHome(homeDir), "config.toml")`, and `resolveKimiHome` is `KIMI_CODE_HOME` or the home default — no `cwd`-relative lookup exists anywhere in the binary. Upstream says it outright: "There is **no** project-level `config.toml`." The project-local file is `local.toml`, restricted to the `[workspace]` table, and project settings "do not override user settings; they supplement them."

A `config.toml` under `.kimi-code/` is therefore read only when something points at it — `KIMI_CODE_HOME` set to that directory, or an explicit config path passed at launch. Verify one of those before treating a setting there as live, and check `<KIMI_CODE_HOME>/config.toml` for the value that is actually in effect.

`.kimi-code/local.toml` stays gitignored because it stores absolute machine paths; upstream recommends the same. `/add-dir` writes it.

### The undocumented project brief

`.kimi-code/AGENTS.md` is read as a project brief. **This appears in the 0.30.0 binary and in none of the published documentation**, so treat it as version-sensitive and re-verify before relying on it.

It does not exist here, and creating one would put a second brief owner beside root `AGENTS.md` — Kimi-only, invisible to every other agent surface, and outside the canonical map. Extend the root brief instead.

## Configuration Files

| File | Scope |
| --- | --- |
| `<KIMI_CODE_HOME>/config.toml` | provider, model, permission, hook, and runtime settings |
| `<KIMI_CODE_HOME>/tui.toml` | theme, editor, notifications, status line, auto-update |
| `<KIMI_CODE_HOME>/mcp.json` | user MCP registry |

`KIMI_CODE_HOME` relocates the whole data directory; it defaults to `~/.kimi-code`. Resolution is `KIMI_CODE_HOME` first, then the home-directory default — never assume the literal default path when writing tooling.

Precedence, highest first: CLI flags, environment variables, `[models."<alias>".overrides]`, `config.toml`, built-in defaults.

## `config.toml` Sections

Top-level: `default_model`, `default_permission_mode` (`manual`, `yolo`, `auto`), `default_plan_mode`, `merge_all_available_skills`, `telemetry`.

Tables: `[providers.<name>]` and `[models.<alias>]` (see [`providers-and-interop.md`](providers-and-interop.md)), `[thinking]`, `[loop_control]`, `[background]`, `[subagent]`, `[mcp]`, `[tools]`, `[image]`, `[services.<service>]`, `[secondary_model]`, and `[[hooks]]` (see [`hooks.md`](../hooks/hooks.md)).

Selected keys worth knowing when diagnosing behavior rather than editing it:

- `[loop_control]`: `max_steps_per_turn` (`0` unlimited), `max_retries_per_step` (default 10), `reserved_context_size` — the threshold that triggers automatic compaction.
- `[background]`: `max_running_tasks`, `keep_alive_on_exit`, `bash_task_timeout_s` (default 600), `bash_auto_background_on_timeout`, `print_background_mode` (`exit`, `drain`, `steer`).
- `[tools]`: `enabled` allowlist and `disabled` denylist, exact names for built-ins and globs for MCP.
- `[secondary_model]`: gated behind `KIMI_CODE_EXPERIMENTAL_SECONDARY_MODEL=1`.

Environment overrides exist for most of these — `KIMI_LOOP_*`, `KIMI_CODE_BACKGROUND_*`, `KIMI_SUBAGENT_TIMEOUT_MS`, `KIMI_MCP_*`, `KIMI_IMAGE_*`, `KIMI_SECONDARY_*`, `KIMI_MODEL_THINKING_KEEP`.

## Permission Rules

```toml
[[permission.rules]]
decision = "deny"
pattern = "Bash(rm -rf*)"
scope = "project"
reason = "Destructive removal requires an explicit human step."
```

Rules are evaluated in declaration order. `decision` is `allow`, `deny`, or `ask`. `scope` is `turn-override`, `session-runtime`, `project`, or `user` (default). `pattern` is a tool name or `ToolName(arg-pattern)`; MCP and custom tools support **name matching only**.

Kimi rules do support `allow`. The repository's fail-only discipline is scoped to `.codex/rules/*.rules` execpolicy and does not transfer — but note where these rules live: `config.toml`, which is user-level. A Kimi permission rule is a personal setting, not a repository control, and cannot be reviewed or enforced from the tree.

## Data Layout

Under `$KIMI_CODE_HOME`:

- `sessions/<workDirKey>/<sessionId>/` — `state.json`, `agents/*/wire.jsonl`, `logs/`, `tasks/`, `cron/`, `upcoming-goals.json`.
- `session_index.jsonl` — one record per session.
- `credentials/` — OAuth material, directory `0700`, files `0600`, written atomically. MCP credentials under `credentials/mcp/`.
- `bin/` — cached `rg` and `fd`.
- `logs/kimi-code.log`, `updates/`, `user-history/`.

## Secrets

`[providers.<name>].api_key` is stored **in plaintext** in `config.toml`, and the `[providers.<name>.env]` fallback holds a literal value rather than a variable name — a declared provider always embeds its key. The rule that follows is simple: no Kimi credential, no `credentials/` content, and no raw `config.toml` goes into a commit, a diff, a fixture, a log, or a report. Redact before quoting.

The way to hold a key only in the environment is the `KIMI_MODEL_*` synthesized provider in [`providers-and-interop.md`](providers-and-interop.md#environment-synthesized-provider), which is never serialized to disk. Its credential variable is `KIMI_MODEL_API_KEY`.

`/export-debug-zip` bundles session logs and paths. Treat its output as sensitive.

## Validation

`kimi doctor` runs the CLI's own parser and schema against a config file without starting the TUI. Pass a candidate path to check an edit before it replaces the live file; with no path it checks the active `config.toml` and `tui.toml`.

Apply changes with `/reload` after editing `config.toml`, or `/reload-tui` for `tui.toml` alone.
