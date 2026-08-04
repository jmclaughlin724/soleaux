# Subagents Playbook

Sources verified 2026-08-03:

- https://developers.openai.com/api/docs/guides/responses-multi-agent
- https://learn.chatgpt.com/docs/agent-configuration/subagents
- https://learn.chatgpt.com/docs/config-file/config-reference

## Intent

Use subagents to move noisy, parallel, or context-heavy work out of the main thread while keeping decisions, requirements, and final integration in the parent. A subagent should return usable findings, not raw exploration dumps.

Codex delegates after a direct request or when applicable `AGENTS.md` or skill instructions request it. The controlling prompt or instruction must define the split, whether to wait for every result, and the return shape.

When a repo skill standardizes a parallel research workflow, make the delegation authorization explicit in that skill and keep it aligned with upstream manual triggering: read-heavy fan-out is acceptable for exploration, tests, triage, docs research, summarization, and skeptical review; parallel implementation remains a separate write-scoped phase.

Follow the canonical [prompted delegation rules](../../../team/references/codex-subagents.md#prompted-delegation) for direct `spawn_agent` calls. Do not duplicate `fork_turns` compatibility in this playbook.

## When To Delegate

Delegate when:

- Multiple independent paths can be explored in parallel.
- Log, test, or codebase scanning would pollute the main context.
- A specialized agent has clear instructions and a bounded deliverable.
- The parent can evaluate the result without trusting hidden work.
- The work is read-heavy: exploration, upstream docs research, test or log triage, summarization, or skeptical review.

Do not delegate when:

- The task needs one linear edit path.
- The acceptance criteria are unclear.
- The parent cannot inspect or validate the output.
- Multiple agents would need to edit the same files or coordinate write-heavy changes.
- The workflow is dominated by one slow external operation or requires a fixed deterministic graph.

## Parallel Research Pattern

For exploration and research, split by evidence type rather than by vague topic. Practical roles:

- `map`: identify the relevant files, sources, symbols, flows, or document sections.
- `context`: verify upstream docs, external references, background concepts, and version-specific behavior.
- `details`: trace equations, figures, APIs, data shapes, edge cases, or call paths.
- `skeptic`: check whether evidence supports the claims and list contradictions, caveats, missing baselines, or unverified assumptions.

The parent agent must wait for all requested subagents, compare their answers, resolve contradictions, and synthesize one final result. Do not forward subagent conclusions as final truth without parent-side reconciliation.

## Custom Agent Authoring

- Store custom agents under `.codex/agents/` or `~/.codex/agents/`.
- Treat each TOML as one standalone custom agent and require nonblank `name`, `description`, and `developer_instructions` fields.
- Optional runtime fields include `nickname_candidates`, `model`, `model_reasoning_effort`, `sandbox_mode`, `mcp_servers`, and `skills.config`.
- Treat `name` as source of truth; do not rely on filename semantics.
- Keep `developer_instructions` output-oriented: scope, allowed actions, required evidence, and report format.
- Keep local `.claude/agents/**` prompts title-first and output-oriented: `# Title`, `## Mission`, `## Workflow`, and `## Output Contract`.
- For long subagent workflows, move repeatable detail to an owning skill or rule; leave the direct prompt as a bounded delegation contract.

## Runtime Controls

- Use `[agents].max_concurrent_threads_per_session` to cap concurrently open spawned-agent threads. When unset, Codex selects the runtime default; `max_threads` remains a legacy alias.
- Use `[agents].max_depth` only for the V1 agent backend; V2 ignores it.
- Use `[agents].enabled` only as an explicit multi-agent tool switch. It defaults to `true`.
- Use `[agents].interrupt_message` to control whether an interrupted agent turn records a model-visible message.
- Do not invent `[parallelization]` or `[workflow]` config tables.
- Remember subagents inherit sandbox constraints and approval behavior from the parent.
- In Responses API Multi-agent, every agent receives the request's configured tools; account for that shared tool set when configuring the request.
- Responses API Multi-agent defaults to three concurrent subagents across the full agent tree.
- The root owns the final response. It must reconcile duplicate or conflicting findings and synthesize one result rather than forwarding agent messages directly.

## Output Contract

Tell each subagent:

1. The exact slice it owns.
2. Whether it may edit or only investigate.
3. What evidence to collect.
4. The format of the result.
5. Whether to stop at blockers or continue with alternatives.

Require each result to include:

- scope covered
- key findings
- file references, source links, or command evidence
- uncertainties and contradictions
- concrete blockers, if any
- recommended next step

For implementation planning or task-list preparation, also require each subagent to return its exact change-inventory slice: files, routes, consumers, dependencies, generated outputs, and check surfaces classified as add, update, remove, unchanged, or excluded. If a category has no entries, require `none` plus the evidence that proves it.

## Repo Delivery Pattern

- Codex subagents are hand-authored standalone TOMLs under `.codex/agents/**`; this repo has no `.claude/agents/` tree and no agent sync step.
- Do not use subagents to bypass owner boundaries or approval policy.
- Treat parallel implementation as a separate phase after the parent has accepted the research synthesis.
