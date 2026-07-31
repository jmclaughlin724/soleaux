# Agent Skills Specification

Sources verified 2026-07-27:

- [Agent Skills specification](https://agentskills.io/specification)
- [Codex skills manual](https://learn.chatgpt.com/docs/codex-manual.md)
- [Codex skill authoring](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref)

## Portable Contract

A skill is a directory containing one required `SKILL.md`. The file starts with YAML frontmatter and continues with unrestricted Markdown instructions. Optional `scripts/`, `references/`, `assets/`, and other skill-owned resources support progressive disclosure.

The portable frontmatter fields are:

| Field | Required | Contract |
| --- | --- | --- |
| `name` | Yes | 1–64 lowercase ASCII letters, digits, or single interior hyphens; no leading, trailing, or consecutive hyphens; must equal the parent directory name |
| `description` | Yes | 1–1024 characters describing both what the skill does and when to use it |
| `license` | No | Short license name or reference to a bundled license file |
| `compatibility` | No | 1–500 characters describing actual environment requirements |
| `metadata` | No | Mapping from string keys to string values |
| `allowed-tools` | No | Experimental, space-separated string of pre-approved tools; client support varies |

Do not turn recommendations into portable validation rules. Third-person voice, trigger keywords, focused references, a body under 500 lines, and one-level-deep resource links improve routing and context use, but the portable specification does not impose a body outline or forbid angle brackets.

```yaml
---
name: database-workflow
description: Applies verified database schema workflows. Use when generating, checking, or reviewing a database migration.
license: MIT
compatibility: Requires PostgreSQL 14+ and the repository package manager.
allowed-tools: Read Bash(git:*)
metadata:
  owner: platform
---
```

## Progressive Disclosure

Clients initially load `name` and `description`, load the complete `SKILL.md` after activation, and load referenced resources only when needed. Keep the entrypoint focused, make every referenced path relative to the skill root, and move detailed variants into small skill-owned references.

## Codex Runtime Extensions

Codex discovers repository skills by scanning `.agents/skills` from the current working directory upward to the repository root. Duplicate names are not merged; each exact path can appear independently. A repository-wide audit therefore needs an explicit all-workspaces mode, while a runtime-scoped audit must follow the current-working-directory ancestor chain.

Optional `agents/openai.yaml` metadata controls the OpenAI-facing interface, implicit invocation policy, and tool dependency declarations. An MCP dependency can be:

- self-describing through a declared transport and connection details;
- supplied by a repository `.codex/config.toml` server entry; or
- host-managed and resolved by an installed plugin or provider.

Declaration validity, environment readiness, and live connectivity are different checks. A valid dependency must not be rejected merely because it is absent from repository config; Codex can install and wire a self-describing dependency automatically.

## Dual-Surface Authoring

One `.agents/skills/<name>/` directory serves both clients: Codex reads it natively and Claude Code reads it through the `.claude/skills` symlink. Delivery is owned by [`../codex/skills/skills.md`](../codex/skills/skills.md); this section owns what that shared directory may declare.

The two clients extend the portable format in opposite places. Codex extends through the `agents/openai.yaml` sidecar and adds no frontmatter fields; Claude Code extends through frontmatter and reads no sidecar. Only `name` and `description` reach both, so the shared declaration stays inside the portable table above and platform controls stay in their own lane.

| Portable field | Cross-surface behavior |
| --- | --- |
| `name` | Read by both. Claude derives the command from the directory name and treats frontmatter `name` as a display label outside plugins, so the portable rule that `name` equals the directory also keeps `/name` and `$name` identical. |
| `description` | The only routing signal both clients receive. |
| `license`, `compatibility`, `metadata` | Valid portable declarations that Claude ignores. Safe to keep; do not expect either client to route on them. |
| `allowed-tools` | Declared portably but honored only by Claude, as a per-turn grant that does not restrict tool access. Use the space-separated string; the comma and YAML-list forms are Claude extensions the repository validator rejects. |

Put triggers in `description`. Claude's `when_to_use` has no Codex counterpart and is rejected in `SKILL.md`, so a shared skill carries capability and trigger text in one field. Claude's per-listing cap is not the binding limit: Codex budgets the whole catalog and shortens descriptions before omitting skills, so one verbose description degrades routing for every skill in the tree. Budget a shared description against the catalog total, not the per-listing cap.

Keep Claude-only substitutions out of a shared body. `$ARGUMENTS`, positional `$0`, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PROJECT_DIR}`, and the `!` command-injection forms are expanded by Claude and passed through as literal text by Codex. A shared body that depends on them is silently wrong on one surface.

Two structural constraints follow:

- `policy.allow_implicit_invocation: false` and Claude's `disable-model-invocation: true` are the only controls expressing the same intent on both surfaces, and only the sidecar half is expressible under the portable contract. A shared skill that must not auto-invoke is therefore gated on Codex and open on Claude. State that limitation where it matters rather than adding a non-portable field.
- Do not add `.claude-plugin/plugin.json` to a shared skill directory. Claude then loads the folder as a skills-directory plugin and reads `agents/` as Claude subagent definitions, which `agents/openai.yaml` is not.

Claude documents the symlink at the skill entry rather than the skills root. The repository links the whole root, which is the lower-maintenance form and picks up new skills automatically; per-entry links are the documented form and are the option to take if one skill ever needs a real Claude-side directory.

## Repository Extensions

This repository deliberately adds contracts that are not part of the portable specification:

- the first body heading is H1 and `## Contract` follows it;
- Markdown links, heading fragments, and documented root `pnpm` scripts resolve;
- portable text does not embed user-specific absolute paths;
- `.rules` remains reserved for `.codex/rules/**`;
- `agents/openai.yaml`, when present, follows the locally supported OpenAI metadata subset.

[`../claude/skills/skill-frontmatter-schema.md`](../claude/skills/skill-frontmatter-schema.md) owns the exact local validator contract. Do not describe a local extension as an upstream requirement.

## Evaluation And Validation

Treat declaration validity, graph integrity, activation boundaries, environment readiness, and model behavior as separate evidence:

- `pnpm skills:validate` checks portable declarations plus repository-local static contracts.
- `pnpm skills:relationships` reconciles discovered links and dependencies with canonical Soleaux ownership policies, lifecycle registration, package scripts, and CI.
- `pnpm skills:boundaries` verifies complete deterministic trigger, non-trigger, and near-miss fixtures for every exact skill path. It does not execute a model.
- `pnpm skills:audit` runs the first three required skill checks from one immutable discovery snapshot. The focused commands remain available for diagnosis.
- `pnpm skills:readiness` classifies MCP declarations against self-describing, repository-configured, and host-managed resolution. It does not probe live connectivity.
- `pnpm skills:conformance` runs the upstream `skills-ref` validator from a pinned commit as an optional oracle.

The upstream `skills-ref` repository explicitly labels its library demonstration-only, so it is not the production validator owner. For model routing quality, maintain representative positive, negative, and near-miss prompts and execute model evaluations separately when runtime activation behavior must be proven.
