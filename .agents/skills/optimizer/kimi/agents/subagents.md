# Kimi Subagents

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/customization/agents.html
- Kimi Code CLI 0.30.0 binary, for the project agent root marked below

## Intent

Read this before assuming a Kimi session can delegate to this repository's existing subagents. It cannot: **no Kimi-readable agent file exists here today.**

## The Gap

Kimi discovers agents from `.kimi-code/agents/` and `.agents/agents/`, scanning recursively for `.md` files. Neither directory exists in this repository. Codex subagents live in `.codex/agents/*.toml` — wrong root and wrong format, invisible to Kimi. Claude agents are equally out of scope.

So a Kimi session gets built-in agents only. Adding one means creating a new Markdown file under `.agents/agents/`, which introduces a directory the repository does not currently own — route that decision through the ownership table in root [`AGENTS.md`](../../../../../AGENTS.md) before creating it, not after.

## Discovery Precedence

Highest to lowest:

1. `--agent-file <path>`, for the current launch only.
2. Project: `.kimi-code/agents/`, `.agents/agents/`.
3. Extra roots from `extra_agent_dirs` in `config.toml`.
4. User: `$KIMI_CODE_HOME/agents/`, `~/.agents/agents/`.
5. Plugin manifests.
6. Built-in agents.

Project-scoped agent files come from the repository and are executed as privileged instructions. Review one the way you would review a hook handler.

## File Format

YAML frontmatter, then the system prompt body.

| Field | Required | Purpose |
| --- | --- | --- |
| `description` | yes | shown to the main agent when it picks a sub-agent |
| `name` | no | kebab-case; defaults to the filename |
| `whenToUse` | no | delegation hints |
| `override` | no | replace a same-named built-in agent; default `false` |
| `model_preference` | no | `primary` or `secondary` |
| `tools` | no | allowlist; **omit to allow every tool** |
| `disallowedTools` | no | denylist, applied after the allowlist |
| `subagents` | no | which sub-agents this agent may delegate to |

`tools` and `disallowedTools` take exact, case-sensitive built-in names or `mcp__<server>__*` patterns — the divergence table in [`tools.md`](../tools/tools.md#name-divergence) applies. Omitting `tools` grants everything, so an agent meant to be read-only needs an explicit list, not an empty one.

Unlike skills, agent frontmatter is not constrained by the repository's portable `SKILL.md` contract; these fields are the agent file's own schema.

## Runtime Behavior

- Each sub-agent has a fully independent context window and cannot read the parent conversation.
- Tool lists shape what the model is shown **and** are enforced again before execution.
- Resuming an existing sub-agent is exempt from the `subagents` delegation allowlist.
- An explicit `model` on the tool call beats `model_preference`.
- `[subagent].timeout_ms` bounds wall-clock time per sub-agent; default 7200000 ms (2 hours), `0` disables the limit. `KIMI_SUBAGENT_TIMEOUT_MS` overrides it.

## Main-Agent Selection and System Prompt

`--agent <name>` starts a session with a named agent as the main agent; `--agent-file <path>` loads one file at highest priority. Both apply to new sessions only and are ignored when resuming with `--session`.

`$KIMI_CODE_HOME/SYSTEM.md` permanently overrides the built-in main-agent prompt. It is a plain Markdown body with no frontmatter. Because it is user-level, it is invisible to this repository — a contributor's `SYSTEM.md` can change how every session here behaves, and no repository check will see it.

Template variables available in a prompt body: `${base_prompt}`, `${plugin_sections}`, `${skills}`, `${agents_md}`, `${cwd}`, `${cwd_listing}`, `${os}`, `${shell}`, `${now}`, `${additional_dirs_info}`. Unknown variables stay verbatim, and a bare `$` is never special.
