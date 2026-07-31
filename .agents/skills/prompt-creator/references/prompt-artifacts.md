# Prompt Artifacts

## Contract

Use this reference for production system, developer, user, tool-use, structured-output, and grounded prompts.

## Choose The Composition Mode

- **Adaptive runtime-prompt composition:** Select only the sections that change the target artifact; there is no required universal template.
- **Explicitly selected reusable prompt guide, task-spec template, or other named template:** Treat its required section set, order, placeholders, and N/A conventions as the selected artifact contract. Preserve every caller- or template-required section unless the caller authorizes a change. Do not apply that selected template as the universal default for unrelated adaptive prompts.

## Select The Artifact And Owner

| Artifact | Prompt content owns | Host or platform owns |
| --- | --- | --- |
| System or developer prompt | Identity, outcome, behavior, evidence rules, output expectations | Message priority, model selection, request settings, secrets |
| User or task prompt | Current goal, inputs, task-specific constraints, requested output | Durable policy and reusable workflow |
| Tool instructions | When and why to use available tools, fallback and stop behavior | Tool availability, argument validation, authorization, execution |
| Tool description/schema | Purpose, use conditions, arguments, returned fields, error shape | Handler correctness, retries, side effects, access control |
| Structured output | Semantic meaning of fields and incomplete/refusal behavior | JSON Schema, strictness, parsing, persistence validation |
| Grounded answer | Required sources, support threshold, citation placement, uncertainty | Retrieval quality, source access, citation rendering |

Keep a runtime prompt near its live consumer in a small code-owned module. Pass dynamic values through typed inputs or an owning schema. Remote prompt objects are deprecated: never create new ones, and migrate existing remote prompt IDs into version-controlled prompt builders. Do not create a new shared prompt package or compatibility layer without an established owner and explicit need.

## Compose Only Outcome-Changing Blocks

For a simple prompt, a goal plus useful context and output request may be sufficient. For a complex prompt, choose from:

1. **Identity** — the model's function or domain context, only when it changes decisions or voice.
2. **Personality and collaboration** — short, separate guidance for tone and task behavior when either changes the product experience.
3. **Outcome** — the user-visible result.
4. **Success criteria** — observable conditions that define completion.
5. **Context and evidence** — authoritative inputs and how to handle gaps or conflicts.
6. **Constraints** — safety, business, scope, or format invariants.
7. **Tools and actions** — routing, approvals, result handling, fallback, and stop behavior.
8. **Output** — required structure, length, tone, schema semantics, or citations.
9. **Stop rules** — when to answer, ask for a missing field, retry, abstain, or report a blocker.

State each rule once. Prefer decision rules over universal defaults. Use absolute language only for true invariants. Keep personality and collaboration style short and separate from task authority. A worked example composing these blocks is [production-prompt-example.md](../assets/production-prompt-example.md).

## Separate Stable And Dynamic Content

Keep stable instructions, tool definitions, schemas, and reusable examples byte-stable near the beginning. Put current user input, tenant or session data, timestamps, retrieval results, and other variable context near the end.

This layout improves clarity and can improve cache reuse, but caching controls and economics belong to the API client. Verify current provider behavior before recommending cache keys, breakpoints, retention, or token thresholds. Never pad a prompt to reach a cache threshold.

## Design Tools And Schemas

For each tool, make the definition usable without hidden knowledge:

- state its purpose and when it is the sufficient choice;
- distinguish it from nearby tools;
- describe arguments the model must supply, not values the host already knows;
- name important returned fields and the error or empty-result shape;
- identify approval-gated or externally visible effects.

Expose the narrowest sufficient initial tool set. Use deferred discovery or tool search when the surface is large and the runtime supports it. Tell the host to support zero, one, or multiple calls when the API can emit them.

Use programmatic tool orchestration only for a bounded, predictable reduction with named eligible tools, a compact result schema, retry limit, stop condition, and one handoff to direct judgment. Multiple calls alone do not justify it. Keep approvals, side effects, citation or native-artifact capture, semantic judgment, and final validation in direct calls, and test the reduced program output separately from the final response.

Use structured output when a downstream consumer requires schema adherence. Keep field semantics in the prompt and the actual schema in the request or typed owner. Verify the provider's supported schema subset, strict-mode requirements, refusal behavior, and incomplete-response handling from current docs. Parse and authorize before any write or side effect.

Treat generator-produced prompts, function definitions, and JSON schemas as drafts. Generation is an authoring step, not a production runtime dependency: review, clean up, type, and evaluate the result with fixtures before deployment.

Route repeatable deterministic operations such as parsing, arithmetic, formatting, lookups, and schema validation to code or a tested script when the same input has one defined answer. A prompt may select or explain that mechanism, but it must not replace the mechanism with model judgment.

## Skills As Instruction Inputs

When prompt work exposes local or hosted Skills, use `skill-creator` for the bundle and the provider or API integration owner for upload, attachment, versioning, shell mode, and product exposure. Treat mounted Skills as privileged instructions and code: inspect them before use, map them to bounded workflows, prevent arbitrary end-user selection from an open catalog, and gate write or high-impact actions in the host.

Responses API Skill instructions enter as user-prompt input; never claim they override system or developer instructions. Attachment formats, lifecycle, limits, and feature availability are current API behavior, so verify them through provider documentation rather than embedding them in the prompt artifact.

## Grounding And Security

Define what claims require evidence, which sources are allowed, where citations belong, and what to do when support is missing. Distinguish an inference from a retrieved fact. Empty retrieval is missing evidence, not proof of absence; allow one or two meaningful fallbacks when correctness requires them.

Retrieve again only for a required missing fact, an exhaustive comparison, a named artifact, or a material claim that would otherwise be unsupported. Do not retrieve again merely to improve phrasing or add optional examples.

Prompt text is not a security boundary. Keep credentials and private keys out of model-visible messages. Minimize authorized personal or proprietary data, redact examples, and enforce access, tenant isolation, tool permissions, and side-effect confirmation in the host.

Instructions against revealing a prompt may shape behavior but cannot make embedded information secret. The durable defense is to keep sensitive material out of the prompt and restrict what the runtime exposes.

## Quality Pass

Before delivery, verify:

- the artifact targets one named job and one authority layer;
- a rewrite preserves explicit user values and unchanged functional or factual contracts;
- the provider/API/model is either explicit and verified or deliberately unspecified;
- dynamic inputs have an insertion point and cannot be confused with higher-priority instructions;
- tools, schemas, failures, approvals, and stop behavior are defined only where relevant;
- examples demonstrate a real boundary, format, or voice decision;
- deterministic controls remain in code or platform configuration;
- the artifact can be evaluated with observable criteria.
