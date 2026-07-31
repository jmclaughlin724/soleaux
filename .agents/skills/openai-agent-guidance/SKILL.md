---
name: openai-agent-guidance
description: Build or migrate OpenAI agent workflows using current official guidance.
---

# OpenAI Agent Guidance

## Contract

Review, design, migrate, or implement OpenAI agent workflows using current official documentation. Invoke `openai-docs` before material decisions and read only the affected sections of [implementation-contract.md](references/implementation-contract.md). Current official guidance and verified runtime behavior win over checked-in examples or memory.

## Use When

- Migrating an OpenAI model or prompt.
- Designing Responses API state, reasoning, tools, caching, citations, or grounded output.
- Evaluating tool search, Programmatic Tool Calling, multi-agent orchestration, skill exposure, or apply-patch harnesses.
- Updating durable Codex-facing instruction surfaces for an OpenAI workflow.

## Direct Workflow

1. Invoke `openai-docs` and fetch the exact current guide for the requested surface. For latest or default model work, resolve the latest-model page and its linked migration and prompting guides. Preserve an explicitly named target.
2. Inventory active usage sites: model role, endpoint, prompt, effective reasoning, tools, output contract, caching, state or replay, multimodal inputs, consumers, and validation.
3. Classify each site as a behavior-preserving migration, compatibility change, prompt-only change, optional feature experiment, or leave-unchanged surface.
4. Establish a representative baseline before tuning. Preserve current reasoning effort, then compare the same effort and one lower only when the task calls for evaluation.
5. Keep Pro mode, persisted reasoning, explicit caching, Programmatic Tool Calling, and multi-agent adoption isolated from the baseline.
6. Make the narrowest focused change and preserve explicit values, historical fixtures, eval baselines, provider comparisons, and pinned fallbacks unless they are in scope.
7. Validate the final user-visible behavior, error path, citations or artifacts, and changed runtime contract.

## Core Decisions

- Keep prompts outcome-first. Put stable application policy before dynamic request data and remove repeated or contradictory scaffolding.
- Define grounded-output source IDs, citation placement, unsupported-claim behavior, parsing, and renderer validation.
- Use direct tool calls for adaptive judgment, approvals, writes, citations, and artifact validation. Use Programmatic Tool Calling only for bounded, deterministic reductions with schemas, limits, retries, and direct handoff.
- Use multi-agent only for independent, bounded workstreams with isolated state, capped concurrency, closed outputs, and root-agent synthesis.
- Treat skills as privileged instructions and code; expose only vetted skills through bounded workflows.
- Keep Responses API features separate from Codex configuration. Never invent a Codex config key for an API-only field.

## Boundaries

- Do not widen a model-and-prompt migration into SDK, auth, provider, or tool rewiring without explicit scope.
- Do not invent current model IDs, parameters, defaults, pricing, or availability.
- State conflicts between official docs, repository constraints, and verified runtime behavior.
- Report changed behavior, preserved contracts, official guides consulted, validation, and optional features deliberately deferred.
