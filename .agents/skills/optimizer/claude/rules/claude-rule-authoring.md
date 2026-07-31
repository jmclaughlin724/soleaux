# Claude rules and the CLAUDE.md entrypoint

## Surface selection (Claude side)

| Surface | Loaded when | Use for |
| --- | --- | --- |
| `AGENTS.md` | session start | always-needed repo guidance |
| `CLAUDE.md` | session start (Claude only) | runtime entry point that imports `@AGENTS.md` |
| `.claude/rules/*.md` (no `paths`) | session start | global project rules |
| `.claude/rules/*.md` (with `paths`) | matching files read | scoped persistent guidance |
| `.claude/skills/**` | when invoked | reusable workflows |
| `.claude/hooks/**` | lifecycle events | harness-enforced behavior |
| `.claude/agents/**` | subagent spawn | isolated execution |

## CLAUDE.md import

Claude Code reads `CLAUDE.md`, not `AGENTS.md`, so `CLAUDE.md` MUST import `@AGENTS.md` for the AGENTS-first repo pattern. Relative paths resolve from the importing file; recursion is capped at five hops. On Windows prefer the import form over `ln -s AGENTS.md CLAUDE.md` (needs Administrator/Developer Mode). `claudeMdExcludes` can skip an interfering ancestor entry point; managed-policy CLAUDE.md cannot be excluded. The `InstructionsLoaded` hook logs which entry point + rules actually loaded.

`CLAUDE.md` exists only as the Claude runtime entry point. Unique durable policy belongs in `AGENTS.md` or scoped `.claude/rules/**`.

## Rule shape

Every non-stub rule includes: frontmatter with a short `description`; `## Contract` near the top; direct rules before examples; verification and failure behavior when the surface is executable; no unresolved placeholders, no machine-local absolute paths, no stale plan text.

- Durable policy, boundaries, STOP gates → `.claude/rules/**`
- Reusable workflows, examples, references, scripts → `.agents/skills/**` (read by Claude Code through the `.claude/skills` symlink)
- Deterministic lifecycle enforcement → hooks registered in `.claude/settings.json` (Claude) and in `.codex/hooks.json` with handlers under `.codex/hooks/<Event>/` (Codex)

Keep core files short; move examples, variants, and long rationale into references. See [`../../bridge/progressive-disclosure.md`](../../bridge/progressive-disclosure.md) for the cross-platform surface-selection principle.
