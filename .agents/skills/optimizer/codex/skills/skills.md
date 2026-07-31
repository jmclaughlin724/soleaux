# Skills Playbook

Sources verified 2026-07-12:

- https://developers.openai.com/api/docs/guides/tools-skills
- https://learn.chatgpt.com/docs/build-skills

## Intent

Use a skill for repeatable work that benefits from reusable instructions, scripts, references, assets, or agent metadata. A skill should help Codex deliver a surface, not merely summarize docs.

## Skill Shape

- `SKILL.md`: the decision workflow and core instructions.
- Frontmatter: `name` and `description` are required. Keep `description` short, trigger-oriented, and outcome-oriented.
- `## Contract`: the near-top execution contract that states when to use the skill, how little context to load, and what closeout means.
- `references/**`: deeper playbooks, examples, source-specific procedures, and edge cases.
- `scripts/**`: deterministic tooling that the skill can run instead of retyping logic.
- `assets/**`: templates or static artifacts.
- `agents/openai.yaml`: optional OpenAI-specific metadata such as interface, implicit invocation policy, and tool dependencies.

For Responses API use, hosted skills are uploaded versioned bundles referenced by `skill_id`; local shell uses developer-controlled file paths. Do not present those attachment formats as interchangeable.

## Authoring Rules

1. Write the description so Codex can decide when to use the skill.
2. Put the normal workflow in `SKILL.md`.
3. Put `## Contract` immediately after the H1, then move bulky variant guidance into focused reference files.
4. Make references actionable: when to use the surface, what to edit, what to avoid, how to verify, and how repo ownership maps.
5. Prefer imperative steps over background explanation.
6. Add scripts only when they reduce error-prone manual work.

## Optimization Rules

- Scope each skill to one repeatable job. Start with 2-3 representative use cases, concrete inputs, expected outputs, and the closeout command.
- Keep `SKILL.md` as the stable prefix: role, trigger, owner map, normal workflow, and validation. Put volatile examples, incident notes, provider-specific variants, and long references under `references/**`.
- Keep the direct body in this order when compacting large skills: `# Title`, `## Contract`, `## Use When`, `## Direct Workflow`, `## Detail Index`, `## Boundaries`.
- For manual-length skills, move the original body to `references/skill-playbook.md` and keep frontmatter unchanged so invocation metadata remains stable.
- Rewrite relative links after moving the body. Links that pointed from `SKILL.md` to `references/foo.md` must usually point from `references/skill-playbook.md` to `foo.md`.
- Write trigger metadata in user language. Include keywords and file triggers only when they point to the actual job the skill performs.
- Do not make a skill a catch-all rule file. If the instruction is durable policy, move it to `.claude/rules/**`; if it is event-time enforcement, move it to a hook.
- Add `scripts/**` only for deterministic repeat work that is safer to run than to retype.
- For API prompt skills, keep stable instructions/tools/schemas before dynamic request, tenant, timestamp, retrieval, or session context.

## Invocation Behavior

- Cross-platform client discovery, catalog placement, activation wrapping, and resource inventory are owned by [skill-catalog.md](../../bridge/skill-catalog.md); keep this file to Codex-specific runtime behavior.
- Codex initially sees the skill name, description, and path, not the full body. On invoke, Codex loads only the full `SKILL.md` body; `references/`, `scripts/`, and `assets/` are loaded on demand (read when needed), not injected up front.
- In the Responses API, the skill metadata is user-prompt input and the full `SKILL.md` keeps user-level authority. An explicit instruction to use the named skill makes selection more deterministic.
- The initial skill list is capped at roughly 2% of the model context window (≈8,000 characters when the window is unknown); Codex shortens descriptions first and may omit skills with a warning. Front-load the key use case and trigger words in `description`.
- Codex documents no eager `@`-mention import inside `SKILL.md` (unlike CLAUDE.md's `@path` import). Reference files stay lazy, so keep must-have context in the body or a script rather than behind a reference Codex has not read.
- Explicit invocation via `/skills` or `$skill-name` should always work.
- Implicit invocation depends on the frontmatter description and `policy.allow_implicit_invocation` in `agents/openai.yaml`.
- Repo metadata such as `metadata.keywords` and `metadata.file-triggers` supports the local hook matcher; it is not a Codex runtime matcher.
- `[[skills.config]]` can enable, disable, or point at a specific `SKILL.md`; it does not preload the skill body.

## Security And Versioning

- Inspect every skill as privileged instructions and executable content before enabling it.
- Do not expose an arbitrary open skill catalog to end users. Map reviewed skills to bounded workflows.
- Require explicit approval for writes or high-impact actions initiated through a skill.
- Pin hosted versions for reproducible workflows; use the default or latest pointer only when intentional update tracking is acceptable.
- Validate network access, residency, and retention before mounting hosted skills. Prefer local execution when work must stay on developer-managed infrastructure.

## Copyable Example

Follow the repo-enforced schema in [skill-frontmatter-schema.md](../../claude/skills/skill-frontmatter-schema.md) for repo-managed skills. Keep other examples in their existing agent, hook, rule, command, or `AGENTS.md` reference owner instead of adding a parallel template tree.

## Repo Delivery Pattern

- `.agents/skills/**` is the canonical skill tree, read natively by Codex and by Claude Code through the `.claude/skills` symlink.
- Edit skills in place; there is no generated mirror or sync step.
- For skill edits, run `pnpm skills:audit`. Target one exact declaration with `node scripts/codex/audit-skills.mjs --all-workspaces --skill-path <path>`; use `pnpm skills:readiness` separately when MCP environment resolution matters.
- `pnpm skills:boundaries` validates deterministic fixture coverage, not model behavior. Run a model evaluation separately when activation quality itself must be proven.
