# Prompt Guidance Playbook

Sources verified 2026-07-28:

- https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6-sol
- https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode
- https://developers.openai.com/api/docs/guides/prompt-engineering
- https://developers.openai.com/api/docs/guides/prompt-caching
- https://learn.chatgpt.com/guides/best-practices
- https://learn.chatgpt.com/docs/prompting

Supplement with `prompt-cache-and-surface-audit.md` for cross-platform prompt-cache and surface-audit guidance.

## Intent

Use prompt guidance to shape agent behavior in the moment. Prompts should tell Codex how to act, what to optimize for, and how to know when to stop.

## Realtime Prompting Pattern

For realtime agents, specify:

- Role and user relationship.
- Conversation style and brevity.
- Turn-taking and interruption behavior.
- Tool-use policy and confirmation points.
- Safety boundaries and fallback behavior.
- What to do when context is missing.

Keep realtime prompts operational. Long policy blocks degrade live interaction.

## GPT-5.6 Coding Pattern

For coding work, specify:

1. Objective and affected repo area.
2. Constraints that must not be violated.
3. Files, logs, screenshots, or tests that define the current state.
4. Authorized local actions and approval boundaries.
5. Expected validation and success criteria.
6. Failure behavior and the stop condition.

Start with the outcome. Prescribe intermediate steps only when the process is part of the contract, protects a boundary, or addresses a measured failure.

Use the lowest reasoning effort that meets the acceptance bar, and tune it on representative tasks. Low suits narrow, well-scoped work; medium or high suits complex changes and debugging. Reserve xhigh or max for the hardest work when evaluation shows a material gain.

In the Responses API, `reasoning.mode` and `reasoning.effort` are independent. Use `standard` for routine work. Evaluate `pro` only for difficult quality-first work, using the same model and effort as the baseline. Do not ask the model to "think harder" as a substitute for setting the API control.

Keep runtime choices such as reasoning effort and verbosity in Codex config or API parameters when the surface exposes them. Use prompt prose for task objective, constraints, evidence, and stopping condition.

## Agentic Prompt Rules

- Distinguish answer, review, diagnose, research, and plan requests from change, build, fix, and implement requests. Do not infer edits from an analysis request.
- Treat a named task, wave, file list, or `focus` instruction as closed scope. Persistence applies only inside that scope.
- Before each tool call, require that the path, command, and intended result are necessary for the active item. Preserve the diagnostic-discovery and edit-authorization boundary owned by root `AGENTS.md`; do not make an unexpected in-scope tool failure a reason to skip necessary investigation.
- Require persistence through explicitly requested implementation and validation, then stop when the success criteria are met.
- Do not force a plan-first response unless uncertainty is material.
- Use TODOs or checklists only when they help execution.
- Keep final outputs concise and evidence-based.
- For quality-first prompts, state the goal, relevant context, constraints, required evidence, success criteria, and output format. These requirements stay the same in standard and pro modes.
- Put repeated prompt lessons into durable repo instructions instead of repeating them manually.

## Cache Shape

Keep stable developer instructions and tool definitions byte-stable and ahead of dynamic task context. Do not pad prompts to reach a cache threshold. OpenAI API cache keys, breakpoints, TTL, and token telemetry belong in API request code, not Codex configuration.

## Repo Delivery Pattern

- User-provided constraints override local patterns.
- The latest user correction narrows the active scope immediately; do not finish already-planned outside work.
