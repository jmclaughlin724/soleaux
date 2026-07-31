# Enforcement Chain and Reference Sweeps

The agent-surface ownership table in root `AGENTS.md` is the only repository-wide routing map. Despite this file's historical name, it does not define synchronization or repeat that map. It defines how to prove a change at its mapped owner and how to sweep references after an owner is renamed or deleted.

## Enforcement chain

1. **Owner file** — the hand-authored native surface or directly registered executable.
2. **Owning test** — `scripts/codex/__tests__/`, `.codex/hooks/__tests__/`, `.claude/hooks/__tests__/`, and focused mechanism tests under `scripts/hooks/**`.
3. **Skill audit graph** — `pnpm skills:validate` proves declarations, `pnpm skills:relationships` proves connections and owners, and `pnpm skills:boundaries` proves deterministic fixture coverage. `pnpm skills:audit` is their required aggregate owner and shares one immutable discovery snapshot across all three analyses.
4. **Lifecycle lane** — `pnpm hooks:test` proves Codex and Claude handlers and registrations. `pnpm execpolicy:check` separately proves `.codex/rules/*.rules`.
5. **Git-client lane** — Husky pre-commit delivers affected checks and post-commit delivers advisory affected package typechecks and unit tests; `pnpm check:hooks` validates that separate surface.
6. **CI lane** — CI invokes `pnpm agent-surfaces:check` once as the umbrella for lifecycle hooks, execpolicy, skill audits, and structural policy.

## Rename and delete sweep

After renaming or deleting a skill, hook, or agent, sweep every referrer in the same change: exact paths in `scripts/codex/skill-boundaries.json`, test fixtures, `.codex/hooks/AGENTS.md`, registration files, package commands and imports, distribution manifests, and Markdown links inside `.agents/skills/**`. Discovery is source-driven; do not add a parallel required-skill inventory.
