---
name: prompt-creator
description: Create or revise production prompt artifacts for a named model, tool, workflow, or future session, including system and developer prompts, tool instructions, structured output, grounded prompts, and cold-start session handoffs. Not for ordinary prose copyediting or content that no model consumes.
---

# Prompt Creator

## Contract

Create a ready-to-use prompt artifact for a named model, surface, tool, workflow, or future session. Keep behavior in the narrowest live owner, ground runtime claims in current provider evidence, and make the artifact evaluable without this conversation.

## Ownership And Routing

`prompt-creator` owns prompt content. Compose it with the controller for the surrounding surface:

- One-off response: apply `prompt-creator` directly.
- Persisted Codex owner (`.codex/**`, `AGENTS.md`, skill, or custom agent): `optimizer` controls location, persistence, current semantics, and consumer validation; `prompt-creator` controls content.
- Reusable skill: `skill-creator` controls package design; retain the Codex controls above when persisted, and use `prompt-creator` only for prompt content.
- OpenAI model, API, state, caching, Skill exposure, or agent integration: `openai-agent-guidance` controls the change; `prompt-creator` controls prompt content.

## Inputs To Inspect

1. The requested outcome, audience, target surface, output contract, and failure modes.
2. The live prompt consumer and canonical owner, when either exists.
3. Applicable root and scoped instructions, source files, schemas, tools, tests, and current in-scope state for repository-bound work.
4. Current primary provider documentation for model or API behavior that can change.

For OpenAI or Codex prompt, model, tool, schema, or agent behavior, invoke `openai-docs` before making a material runtime claim. Preserve an explicitly selected provider, API, and model. If none is selected, remain provider-neutral unless the artifact cannot be useful without that choice.

## Direct Workflow

1. Classify the artifact, request scope, and composition mode: adaptive runtime prompt or explicitly selected reusable prompt guide/task-spec template. Compose the owners above instead of handing off the prompt artifact.
2. Resolve only the inputs that change the result: goal, audience, portability and privacy boundary, success criteria, authority layer, dynamic context, output format, tools, approval boundary, failure behavior, and stop condition. Ask only when a material choice cannot be discovered or safely represented as an assumption.
3. Deliver a one-off prompt in the response. Persist only when requested, through the surrounding controller and an existing owner-defined location.
4. For repository-bound artifacts, inspect only enough evidence to ground the prompt: instructions, owners and paths, command existence, in-scope state, and first executable action. Do not perform the future session's review, research, or implementation. Separate requirements, verified facts, assumptions, questions, and blockers.
5. Load only the applicable resource from the Detail Index.
6. For a rewrite, preserve explicit user values and unchanged behavior: provider, authority, function, requested structure or length, tools, output, tone, safety, compliance, privacy, and localization. Verify material facts from live evidence. Correct only demonstrably false or stale claims, disclose the behavioral change, and mark unresolved claims as assumptions instead of silently retaining or inventing them. Baseline working behavior when safe, then make the narrowest edit tied to a named or measured failure.
7. Draft against the selected artifact contract. Preserve every caller- or template-required section for an explicitly selected reusable prompt guide or task-spec template. For adaptive runtime prompts, draft outcome-first and include only blocks that change behavior; use the applicable reference for details.
8. Keep stable policy, tool definitions, schemas, and reusable context before dynamic request, tenant, retrieval, timestamp, or session data.
9. Put deterministic guarantees in the host: authorization, secret access, schema validation, tool eligibility, retries, rate limits, and side-effect approvals are code or platform controls, not promises created by prompt prose.
10. Cold-read the artifact as a fresh session. Confirm the first action is executable, the scope is closed, facts and assumptions are distinct, path forms match the artifact's portability boundary, the output is usable, and no current-chat knowledge is required.
11. Exercise representative success, edge or failure, and nearby-negative cases when runtime access is safe and available. Otherwise provide concrete cases and expected behavior without claiming execution.

## Detail Index

| Need | Resource |
| --- | --- |
| Runtime, tool, schema, or grounded prompt design | [prompt-artifacts.md](references/prompt-artifacts.md) |
| Coding sessions and durable handoffs | [session-prompts.md](references/session-prompts.md) |
| Prompt evaluation and regression checks | [evaluation.md](references/evaluation.md) |
| Copyable cold-start session skeleton | [session-agent-prompt-template.md](assets/session-agent-prompt-template.md) |
| Worked production-prompt example | [production-prompt-example.md](assets/production-prompt-example.md) |

## Verification

- Confirm every named owner, path, command, tool, schema, and provider capability from live evidence.
- Remove repeated or contradictory rules and examples that do not change a decision.
- Scan final artifacts for unresolved placeholders, secrets, private data, stale completion claims, and unmarked assumptions. Placeholders are allowed only in a clearly identified reusable template.
- For structured outputs and tool calls, validate the schema and host handling separately from the natural-language instructions.
- For persisted artifacts, run the narrowest applicable format, syntax, or owner check and review the in-scope diff.
- When this skill changes, follow the targeted audit and fresh routing probes in [repository-package verification](references/evaluation.md#repository-package-verification).

## Output Contract

Return the complete prompt artifact, its intended owner or destination, its portability or private-handoff boundary when that affects path handling, the verified provider/API/model only when relevant, material assumptions, sources inspected, evaluation cases or results, and unresolved blockers. Include API configuration only when the request requires it.

## Boundaries

- Do not invent provider defaults, model IDs, parameters, pricing, caching behavior, or platform capabilities.
- Do not restate or reorder platform, developer, or repository authority. Add only task-specific constraints and point to the live owner for durable policy.
- Do not claim that prompt text alone enforces authentication, authorization, privacy, schema validity, or side-effect safety.
- Do not place credentials, private keys, raw secrets, full environment files, or unnecessary personal data in a prompt or example.
- Default portable or shared artifacts to repository-relative paths or a named working-directory placeholder. Use a resolved absolute path only for an explicitly private, same-machine handoff.
- Do not treat leaked, exfiltrated, or reverse-engineered prompts as authoritative source material, and do not mirror or republish them. Independently derive required behavior from legitimate public documentation, authorized requirements, owned runtime evidence, and observable behavior.
- Do not turn review, research, diagnosis, or planning prompts into implementation authority.
- Do not persist a prompt, create a progress file, or update unrelated instruction surfaces without authorization.
- Stop before drafting when the core objective, audience, authority, or side-effect boundary is materially ambiguous and cannot be discovered.
