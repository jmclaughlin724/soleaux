# Config Playbook

Sources verified 2026-08-03:

- https://learn.chatgpt.com/docs/config-file/config-basic
- https://learn.chatgpt.com/docs/config-file/config-advanced
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://learn.chatgpt.com/docs/config-file/config-sample

## Intent

Use Codex config to control runtime behavior that must be available before the agent starts: model defaults, sandboxing, approvals, MCP registration, feature gates, project trust, instruction discovery limits, skills, and subagents. Do not use config as a dumping ground for workflow prose that belongs in `AGENTS.md`, rules, or skills.

## Choose The Right Layer

1. Use `~/.codex/config.toml` for personal machine settings: auth, provider endpoints, telemetry, notification, profiles, and local preferences. Put persistent personal guidance in `~/.codex/AGENTS.md` and standalone personal custom agents in `~/.codex/agents/`.
2. Use `.codex/config.toml` only for trusted project-scoped runtime behavior that every Codex user in the repo should inherit.
3. Use CLI flags or `-c key=value` overrides for one-off runs.
4. Use profiles when the same operator needs named variants.

Configuration precedence is: CLI flags/overrides, trusted project config from repo root to cwd with closest wins, selected profile, user config, system config, then built-ins.

## Authoring Rules

- Before adding a key to `.codex/config.toml`, confirm it is allowed in project config. Project config must not set machine-local provider, auth, notification, profile, realtime base URL, or telemetry keys.
- Keep project config declarative. Avoid comments that restate upstream docs unless they protect a repo-specific invariant.
- Define custom agents as standalone TOMLs under `.codex/agents/` or `~/.codex/agents/`, with the upstream-required `name`, `description`, and `developer_instructions` fields. Do not replace that schema with explicit role registration or `model_instructions_file` indirection.
- Put dynamic task paths, current plan state, examples, and session findings in user or task context.
- Do not add `prompt_cache_key`, `prompt_cache_options`, explicit breakpoints, or cache telemetry fields to Codex config; they are Responses or Chat Completions API request controls.
- Prefer narrow feature gates such as `features.multi_agent`, `features.goals`, or `features.hooks` over broad prompt guidance.
- `features.goals` enables Goal mode; it does not create a task-list API or task metadata store.
- `history.persistence` and `sqlite_home` preserve transcripts and resumable runtime or agent-job state. Do not cite them as evidence of local task-list persistence.
- `features.multi_agent` exposes multi-agent collaboration tools; it does not authorize every workflow to spawn subagents or make parallel implementation safe.
- Set sandbox and approval defaults conservatively. Treat `approval_policy = "never"` and full-access sandboxes as deliberate exceptions, not convenience defaults.
- Keep `project_doc_max_bytes` and fallback instruction filenames intentional; they directly affect how much persistent instruction context Codex receives. Fallback filenames are additional instruction names Codex checks when `AGENTS.md` is missing, not replacements for the primary `AGENTS.md` path. Do not include built-in discovery names such as `AGENTS.md` or `AGENTS.override.md` in `project_doc_fallback_filenames`.

## Repo Delivery Pattern

- `.codex/config.toml` is repo-owned runtime config and owns the `mcp_servers` registry for Codex; do not hand-duplicate registry entries in other config files.
- `.codex/agents/*.toml` owns project custom agents through Codex's standalone discovery contract; `.codex/config.toml` owns only shared subagent runtime controls when those controls are needed.
- `.codex/hooks.json` and `.codex/hooks/**` are hand-authored Codex runtime surfaces owned by `.codex/hooks/AGENTS.md` and its registration test — not generated.
- `.claude/settings.json` and `.claude/rules/**` own the Claude runtime surface. `.agents/skills/**` is the shared canonical skill tree, read by Claude Code through the `.claude/skills` symlink.
- `.codex/rules/**` holds hand-authored Codex-native command policy (execpolicy) with short pointers to prose owners; durable policy prose stays in `AGENTS.md` and `.claude/rules/**`.

## Closeout

- For `.codex/config.toml` or root `AGENTS.md` edits, start a fresh Codex run to rebuild the instruction chain and verify the change through its real consumer.
- Run only owner checks that exist in the current repository. For Codex agent changes, run `uv run --locked pytest -q tests/test_codex_agent_config.py` and `codex --strict-config doctor --json`; do not invent package scripts.
- In the final report, name the config owner changed and any runtime behavior affected.
