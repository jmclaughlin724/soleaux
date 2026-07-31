---
name: optimizer
description: "Use when designing, reviewing, optimizing, or repairing this repo's Claude, Codex, and Kimi agent surfaces: skills, hooks, rules, agents, settings, MCP registration, prompts, and AGENTS briefs. Verify platform behavior upstream and run the repository owner audits."
---

# Optimizer

## Contract

This is a direct execution contract. Load only the references needed for the matching agent surface and follow the workflow and closeout. Read official docs before changing platform-specific behavior.

## Source Order

1. Read the live controlling surface and the root `AGENTS.md` brief first. Load only the `claude/**`, `codex/**`, `kimi/**`, or `bridge/**` reference that can affect the requested outcome.
2. For Claude, Codex, or Kimi product facts, use the official docs. Local scripts and tests prove repo topology only.
3. Agent surfaces are hand-authored; there is no mirror. Root `AGENTS.md` owns the repository-wide map, while Soleaux projects evidence from canonical sources. Use [`bridge/sync-ownership.md`](bridge/sync-ownership.md) for enforcement proof and reference sweeps.

Use AST/LSP tooling for code-symbol and structural claims. Complete prerequisite research before dependent edits; do not load unrelated lanes merely because they exist.

## Ownership Rules

- Use the agent-surface ownership table in root `AGENTS.md` for platform routing; do not restate or expand it in a skill, nested brief, or secondary map. Change the mapped owner, its native counterpart when needed, and the owning proof.
- Codex execpolicy is fail-only in this repository. Add a rule only for a command that must be rejected, use `decision = "forbidden"`, and make the justification tell the agent which permitted command, workflow, or response to use instead. Leave valid or context-dependent commands unmatched; never use `allow` or `prompt`.
- Lifecycle registration and decisions remain platform-native. The registered handler or true executable owns validation, output, exit behavior, and the final decision; policy modules never read stdin or emit platform decisions. Follow [`codex/hooks/hooks.md`](codex/hooks/hooks.md) for event selection, matcher, and response contracts.
- Treat a skill MCP dependency declaration, environment readiness, and live connectivity as separate evidence. `agents/openai.yaml` may self-describe a transport, name a repository-configured server, or rely on a host-managed plugin/provider; `pnpm skills:readiness` classifies those states but does not probe connectivity.
- Root `AGENTS.md` is the canonical, user-owned project brief (`CLAUDE.md` imports it). Do not impose route-map, concision, section-order, or content-placement requirements on it; apply those authoring patterns only to nested `<subfolder>/AGENTS.md` files.
- Kimi Code CLI reads `.agents/skills/**` with no registration, so every skill is live in a Kimi session. Kimi-only frontmatter stays out of the portable declaration, and Kimi hook and permission config is user-level and outside this tree. See [`kimi/skills/skills.md`](kimi/skills/skills.md) and [`kimi/hooks/hooks.md`](kimi/hooks/hooks.md).

## Audit Workflow

When reviewing or changing agent-surface config:

1. Consume the typed Soleaux packet injected by the host before prompt processing. If no packet is present, call `soleaux_context` exactly once with the task objective and only the repository-relative paths already in scope. It returns source, canonical owners, consumers, constraints, conflicts, validation routes, configured resources, and explicit gaps from one snapshot. Begin work when coverage is complete; use `soleaux_owners` or `soleaux_search` only to close a named gap. Treat every projection as evidence, not another owner.
2. Identify the controlling config, owner, proof, registry, or brief; trace it through its test/audit and closeout.
3. Prefer deletion, consolidation, and existing checks over another enforcement or instruction layer.
4. After a rename or deletion, sweep exact links, boundary fixtures, ownership rows, registrations, package commands, and relationship evidence.
5. Use [`bridge/prompt-cache-and-surface-audit.md`](bridge/prompt-cache-and-surface-audit.md) for broad surface reviews and [`codex/tools/responses-tool-orchestration.md`](codex/tools/responses-tool-orchestration.md) for Responses API orchestration.

For hook behavior changes, verify the official event contract, execute the real entrypoint with representative JSON, and validate the event-specific schema in `references/`. A docs-only or skill-only update does not close runtime, registration, or tests.

## Validation

Run the checks that match the surfaces you touched:

- `pnpm skills:audit` — required skill closeout for declarations, relationships, and boundary fixtures. [`skill-frontmatter-schema.md`](claude/skills/skill-frontmatter-schema.md) owns focused, readiness, conformance, and model-evaluation boundaries.
- `pnpm hooks:test` — validates Codex and Claude lifecycle handlers and registrations. After a definition change, start a fresh trusted task, inspect `/hooks`, and exercise the real event.
- `pnpm execpolicy:check` — validates `.codex/rules/*.rules`.
- `pnpm check:hooks` validates Husky, while `pnpm check:structural-policy` validates ast-grep structural policy. `pnpm agent-surfaces:check` is their CI umbrella with hooks, skills, and execpolicy.

## Completion Gate

- The config and durable guidance live in the mapped owner; skill frontmatter follows the portable Agent Skills fields, while OpenAI-only metadata remains in optional `agents/openai.yaml`. [`bridge/agent-skills-spec.md`](bridge/agent-skills-spec.md#dual-surface-authoring) owns what a skill directory read by both Codex and Claude Code may declare.
- Reference sweeps cover exact skill links, boundary fixtures, registration, package commands, and ownership rows; scoped briefs remain consistent and the root brief changes only when requested.
- The focused tests and owner audits for every touched surface pass.

## Reference Index

Load a reference only for the surface you are touching.

- [`templates/hooks/`](templates/hooks/hooks.json) — inert Codex hook registration plus one silent per-event handler standard.
- `PermissionRequest` schemas: [input](references/permission-request.command.input.schema.json), [output](references/permission-request.command.output.schema.json).
- Local apply-patch maintenance schemas: [input](references/post-tool-use.apply-patch.input.schema.json), [output](references/post-tool-use.apply-patch.output.schema.json).
- `bridge/` — cross-platform ownership, disclosure, skill-spec, catalog, and prompt-surface guidance.
- `claude/` — Claude-only skills, agents, prompts, tools, rules, hooks, commands, and config.
- `codex/` — Codex-only skills, agents, prompts, tools, rules, hooks, and config.
- `kimi/` — Kimi-only skills, agents, tools, MCP, hooks, config, and session/goal surfaces.

List the relevant directory before loading from it; each file is scoped to one surface.
