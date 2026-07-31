# Prompt Cache And Surface Audit

Sources verified 2026-07-12:

- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/docs/guides/tools-skills
- https://developers.openai.com/api/docs/guides/responses-multi-agent
- https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode
- https://developers.openai.com/api/docs/guides/prompt-engineering
- https://developers.openai.com/api/docs/guides/prompt-caching
- https://developers.openai.com/api/docs/guides/tools-tool-search
- https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling
- https://developers.openai.com/api/docs/guides/tools-apply-patch
- https://learn.chatgpt.com/docs/config-file/config-reference

## Intent

Use this playbook when optimizing Codex-facing prompts, instruction files, hooks, skills, rules, agents, or nested `AGENTS.md` briefs. The goal is stronger task completion with less global prompt bulk, better cache shape, clearer trigger surfaces, and deterministic policy enforcement. The root `AGENTS.md` is user-owned and exempt from content-shape, length, route-map, and prompt-bulk prescriptions unless the user explicitly requests a root-brief rewrite.

## OpenAI Findings

- GPT-5.6 performs better with lean prompts focused on behavior it does not already provide. Heavy prompts encourage extra exploration, repeated validation, and accumulated context.
- Coding-agent prompts should define action mode, closed scope, tool workflow, approval boundaries, required evidence, success criteria, failure behavior, and the stop condition.
- Skills are versioned instruction bundles. Keep only discovery metadata in the available-skill context, load the full `SKILL.md` on demand, and treat skill contents as privileged instructions or code that require review before networked or high-impact use.
- Multi-agent work should divide into concrete, independent slices with bounded context and parent-side synthesis. Prefer one agent for linear reasoning, small tasks, shared mutable state, or workflows dominated by one slow operation.
- Reasoning mode and effort are separate controls. Keep `standard` as the routine mode; evaluate `pro` only for difficult quality-first work where measured gains justify higher latency and token use.
- Tool search should defer broad inventories behind clear namespaces or MCP server descriptions. Load only the relevant subset, and keep each namespace below roughly ten functions when practical.
- Programmatic Tool Calling belongs only to bounded stages with predictable control flow, documented return schemas, explicit stop and retry limits, and compact structured output. Keep semantic judgment, approvals, citations, native artifacts, and final validation direct.
- Apply Patch workflows need current file context, path restrictions, focused diffs, explicit success or failure results, and tests or linters after the patches apply. A patch tool acknowledgement is not behavioral proof.
- Persistence applies only to the accepted scope. A skill, tool failure, dependency, generated drift, or concurrent change never grants permission to expand it.
- Prompt caching is automatic for eligible OpenAI API prompts at 1024 tokens or more. Cache hits require exact prefix matches, so stable instructions, tools, schemas, and examples belong at the beginning; dynamic user, tenant, timestamp, retrieval, or session context belongs near the end.
- On GPT-5.6 and later, cache writes cost 1.25x uncached input. Use a stable `prompt_cache_key`, explicit breakpoints only around reusable prefixes, and `cached_tokens` / `cache_write_tokens` telemetry to prove net benefit. Do not pad prompts for caching.
- `prompt_cache_key`, `prompt_cache_options`, explicit breakpoints, and cache telemetry are API request controls. Codex config does not expose them; optimize Codex by stabilizing its developer prefix and enabled tool set.
- Repeatable corrections should become narrow skills, hooks, rules, or owner briefs. Do not keep solving repeatable behavior with longer one-off prompts.
- Codex runtime choices such as model, reasoning effort, verbosity, `model_instructions_file`, `project_doc_max_bytes`, tool output limits, MCP servers, hooks, skills, subagents, sandboxing, and approvals belong in `.codex/config.toml` only when they are deliberate runtime configuration.
- GPT-5.6 migration starts from the current reasoning effort and compares the same level with one lower on representative work. Do not change a working effort or enable pro, multi-agent, explicit caching, or programmatic calling merely because the capability exists.

## Findings To Apply

- Preserve the root `AGENTS.md` content and structure. Apply AGENTS-specific content placement, length, and route-map guidance only to nested `<subfolder>/AGENTS.md` files.
- Put a compact closed-scope contract in developer instructions only when it must outrank user or task content. Do not copy the same contract into every rule, skill, and brief.
- Large nested workspace `AGENTS.md` files are allowed when they are true owner briefs. Keep current durable owner state there, move reusable workflows into `.agents/skills/**`, and move durable cross-owner policy into a rule owner (`.claude/rules/**` for prose, `.codex/rules/**` for command policy).
- Every skill should stay scoped to one job, with a trigger-oriented description, 2-3 representative use cases, clear inputs/outputs, and bulky variants in `references/**`.
- Runtime enforcement belongs in the native handler or directly registered true executable owner. When an upstream matcher covers several applicable policies, the event owner may import narrow structured parsers and deterministic policy modules, but it must make the final decision and emit the platform contract; modules never read stdin or emit platform decisions, and pure stdin-forwarding adapters are prohibited. Classification of shell syntax, source code, imports, patches, SQL, hook tool selection, or mutation intent must use ASTs or structured parsers. Plain text matching is acceptable only for literal labels or test assertions that are not proving source classification.
- Rules should remain durable policy with scoped `paths` where useful. Codex execpolicy is repository-owned fail-only command policy: every hand-authored `.codex/rules/*.rules` entry uses `decision = "forbidden"` with an actionable corrective alternative, while valid or context-dependent commands remain unmatched. `pnpm execpolicy:check` enforces the invariant.
- Reasoning effort, verbosity, prompt document byte limits, tool output limits, and instruction-file overrides are runtime config levers. Change them in `.codex/config.toml` only when the request is explicitly about Codex runtime behavior or latency/cost/reliability tradeoffs. API prompt-cache controls are not Codex config levers.
- Keep tool descriptions precise and outcome-oriented. Defer broad tool inventories through namespaces or MCP and load only the relevant subset.
- For tool-heavy prompts, name one orchestration route per stage: direct, deferred discovery, or programmatic processing. State the allowed tools, output shape, evidence, concurrency, retry, stop, approval, and fallback behavior once.
- Prefer deleting duplicate instruction layers before adding a new one. Add a hook only when an event-time deterministic check is required; add a skill only when the workflow is repeatable; add a rule only when command policy can be expressed as executable shell policy.
- Direct context surfaces use a literal `## Contract` heading near the top; the skill audit enforces it for `SKILL.md` files. The root `AGENTS.md` is exempt.
- Relocated Markdown must have links rewritten for the new location. `pnpm skills:audit` validates skill-internal links and heading anchors; treat link relocation as part of the compaction workflow.
- Mechanical audits and rewrites should use Markdown AST and TypeScript/structured parsing. Do not reintroduce regex-based source classification for this surface-audit workflow.

## Audit Procedure

1. Classify each candidate finding by owner: root brief, workspace brief, rule, skill, hook, agent, runtime config, or MCP registry.
2. Decide whether the finding is a prompt-cache shape issue, a trigger-quality issue, a parser/enforcement issue, a runtime config issue, or an ownership issue.
3. If the finding is instruction bloat outside the root `AGENTS.md`, move detail to the narrow owner instead of adding global prose. Do not rewrite the root brief for length or content-shape reasons.
4. If the finding is parser/enforcement quality, prefer AST or structured parsers and add focused tests around the resulting structure.
5. If the finding spans both platforms, patch each hand-authored owner in the same change — there is no sync step — and extend the owning test.
6. Close with this skill's validation matrix: `pnpm skills:audit` for skill surfaces, `pnpm hooks:test` for lifecycle hooks, and `pnpm execpolicy:check` for Codex rule surfaces.
