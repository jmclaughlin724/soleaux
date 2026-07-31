# AGENTS.md Playbook

Sources verified 2026-05-27:

- https://developers.openai.com/codex/guides/agents-md
- https://developers.openai.com/codex/prompting

## Intent

Use the root `AGENTS.md` as the full, user-owned project instruction brief. This playbook does not prescribe its length, structure, route-map role, or policy placement. The authoring rules below apply only to nested `<subfolder>/AGENTS.md` files that add directory-scoped context.

## Placement

1. Preserve the root `AGENTS.md` content and structure unless the user explicitly requests a root-brief change.
2. Put directory-specific ownership, commands, and constraints in the closest workspace `AGENTS.md`.
3. Use `AGENTS.override.md` only when override semantics are intentional.
4. Keep `CLAUDE.md` runtime entry points short and route back to canonical sources.

Codex first checks the global Codex home for `AGENTS.override.md`, then `AGENTS.md`, and uses only the first non-empty global file. For project guidance, it reads from project root to cwd. In each directory, it checks `AGENTS.override.md`, then `AGENTS.md`, then configured fallback filenames only when `AGENTS.md` is missing. Later and closer guidance wins.

Codex skips empty instruction files and stops adding files once the combined project instruction text reaches `project_doc_max_bytes`, which defaults to 32 KiB. `project_doc_fallback_filenames` is only for alternate instruction filenames such as `TEAM_GUIDE.md`; do not add `AGENTS.md` or `AGENTS.override.md` to the fallback list.

## Nested AGENTS.md Authoring Rules

- Include `## Contract` near the top of every non-stub nested `AGENTS.md`; state the directory scope, owner behavior, and where deeper procedures live.
- State invariants, not historical rationale.
- Link to canonical `.claude/rules/**` or skills for detailed procedures.
- Include exact verification commands only when they are stable and owned by that directory.
- Avoid repeating rule bodies. If the policy is repo-wide, put it in a rule and link it.
- Avoid date-stamped incident notes unless the date is operationally necessary.
- Keep nested `AGENTS.md` files cache-friendly: stable local owner instructions first; task findings, repro output, tenant/user context, timestamps, retrieval results, and session state stay out of durable nested briefs.
- Before adding a workflow to a nested `AGENTS.md`, move it to `.claude/skills/**` when it is repeatable, `.claude/rules/**` when it is durable policy, hooks when it is event-time enforcement, or `.codex/config.toml` when it is runtime behavior.
- Large nested workspace briefs should be owner contracts, not manuals. Keep current durable owner state in the closest nested `AGENTS.md`; route repeatable procedures to skills and durable policy to rule files.
- Preferred owner brief order: `# Title`, `## Contract`, current owner sections, and `## Verification` when stable commands exist.

## Verification Pattern

- Use `codex --cd <subdir> ...` to confirm the intended directory guidance loads.
- Use `codex --ask-for-approval never ...` for deterministic noninteractive instruction checks.
- Inspect session logs only when proving instruction discovery or precedence.
- Restart Codex or start a new command when checking instruction discovery changes. Codex rebuilds the chain on each run or TUI session; there is no manual cache to clear.

## Repo Delivery Pattern

- Root `AGENTS.md` is the primary, user-owned Codex project instruction brief and is exempt from the nested authoring rules above.
- Nested `AGENTS.md` files may route detailed durable policy to `.claude/rules/**` or `.codex/rules/**`.
- Reusable procedures belong in `.agents/skills/**`.
- Skill edits close with `pnpm skills:audit` unless the user explicitly requests more.
