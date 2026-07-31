# Kimi Skills Playbook

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html
- Kimi Code CLI 0.30.0 binary, for the discovery-order and root-precedence facts marked below

## Intent

Kimi Code CLI reads `.agents/skills/` directly. Every skill in this repository is already exposed to a Kimi session with no registration step, so the portable `SKILL.md` contract is a Kimi contract whether or not anyone intended that. Use this file when authoring or reviewing a skill that a Kimi session will load.

## Discovery Order

Roots are scanned most to least specific, and the first match wins:

1. Project: `.kimi-code/skills/`, then `.agents/skills/`, resolved by walking up from the `.git` directory.
2. User: `$KIMI_CODE_HOME/skills/` (default `~/.kimi-code/skills/`), then `~/.agents/skills/`.
3. Extra roots named by `extra_skill_dirs` in `config.toml`.
4. Built-in skills shipped with the CLI, lowest priority.

Binary-verified: within a scope the brand root outranks the generic one — `.kimi-code/skills` is "preferred over generic ones (`.kimi-code/skills` before `.agents/skills`)". A same-named skill placed in `.kimi-code/skills/` therefore shadows the tracked one in `.agents/skills/` and, because `.kimi-code/skills/` is untracked, does so invisibly to review. Treat an unexplained behavior difference between two machines as a shadowing check first.

`merge_all_available_skills` in `config.toml` controls whether skills from every discovered root are merged rather than resolved to one root.

## Frontmatter: What This Repository May Declare

Kimi accepts optional `type`, `whenToUse`, `disableModelInvocation`, and `arguments` alongside `name` and `description`. **None of them may appear in a repository `SKILL.md`.** `scripts/codex/audit-skills.mjs` rejects unknown top-level fields, and the portable declaration owned by [`agent-skills-spec.md`](../../bridge/agent-skills-spec.md) does not include them. Platform-only controls belong in their platform owner, and Kimi has no separate per-skill metadata file to hold them.

Three consequences follow, and they are the reason this file exists:

- **Auto-invocation rides on `description` alone.** Kimi has no `whenToUse` to fall back on here, so the description carries the entire selection decision. The trigger-oriented description rules in [`skill-frontmatter-schema.md`](../../claude/skills/skill-frontmatter-schema.md) are load-bearing for Kimi, not stylistic.
- **`disableModelInvocation` is unavailable.** A repository skill cannot be marked manual-only for Kimi. If a skill must not fire implicitly, that has to be enforced by writing a description narrow enough to not match, not by a flag.
- **`type: flow` is unavailable**, so every repository skill is an inline prompt skill to Kimi.

Directory form requires both `name` and `description` or parsing fails outright — the skill is skipped, not degraded. Flat single-file form caps `description` at 240 characters; the repository uses directory form everywhere, where the 1024-character portable limit applies.

## Body Placeholders

Kimi substitutes into the `SKILL.md` body before the model sees it:

| Placeholder | Expands to |
| --- | --- |
| `$ARGUMENTS` | the full raw argument string |
| `$0`, `$1`, `$ARGUMENTS[n]` | positional arguments, whitespace-tokenized, zero-indexed, single- and double-quoting honored |
| `$<name>` | a named parameter declared in `arguments` |
| `${KIMI_SKILL_DIR}` | the invoked skill's own directory |

When a body contains no placeholder, Kimi appends the invocation text as `\n\nARGUMENTS: <text>` instead. Repository skills declare no `arguments`, so `$<name>` never resolves here; a literal `$0` or `$ARGUMENTS` in prose or a code sample **will** be substituted. Escape or reword it.

## Invocation

- Manual: `/skill:<name>`, or the `/<name>` shorthand when it does not collide with a built-in command, or `/<parent>.<sub-skill>` for a bundle.
- Automatic: the model selects from `description` unless the skill is flow-typed or invocation-disabled, neither of which this repository can declare.
- The `Skill` tool supports **3 levels of nesting**; deeper invocation chains terminate. A skill that delegates to a skill that delegates again is at the limit.

## Review Checklist

1. The description reads as a trigger, because it is the only signal Kimi has.
2. No Kimi-only frontmatter field crept in — `pnpm skills:validate` fails on it.
3. No unescaped `$0`, `$1`, or `$ARGUMENTS` in the body.
4. Delegation depth stays within three levels.
5. Nothing in `.kimi-code/skills/` shadows the tracked skill under review.

Close out with `pnpm skills:audit`.
