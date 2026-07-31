# Prompt Evaluation

## Contract

Evaluate the artifact on representative behavior, not prose aesthetics. Prove task success, completeness, evidence correctness, boundary compliance, and output validity before optimizing tokens, latency, or cost.

## Define Cases Before Tuning

Choose the narrowest set that covers the real risk:

| Case | What it proves |
| --- | --- |
| Representative success | The common user-visible outcome completes |
| Edge or missing evidence | The prompt asks, narrows, abstains, or falls back correctly |
| Failure path | Tool, schema, or dependency failure does not fabricate success |
| Approval or side-effect boundary | The model stops before an unauthorized action |
| Nearby negative | The prompt or skill does not trigger for an adjacent job |
| Adversarial input | Untrusted content does not silently replace higher-priority instructions |

Add more cases only when distinct user segments, languages, formats, tools, or safety boundaries create materially different behavior. Use real or representative sanitized inputs; do not leak the intended answer into independent validation.

## Choose Observable Checks

- **Structured output:** schema validation, required fields, semantic field checks, refusal and incomplete handling.
- **Tool use:** correct tool choice, argument validity, zero/one/multiple-call handling, empty/error fallback, approval behavior.
- **Grounded output:** support for material claims, citation placement, source conflicts, uncertainty, and absence-of-evidence handling.
- **Session prompt:** correct mode, closed scope, executable next action, preserved state, validation provenance, and stop condition.
- **Voice or content:** rubric tied to concrete product choices, required facts, prohibited inventions, and acceptable length.

Use deterministic graders for deterministic contracts. Use rubric-based human or model grading for judgment-heavy output, and calibrate it with examples. Treat a prompt-leak refusal as behavior shaping, not proof that hidden text or secrets are protected.

## Creation And Rewrite Loops

For a new prompt:

1. Record the initial artifact and cases.
2. Run the same artifact, tools, inputs, and request settings across the cases.
3. Classify failures by prompt, tool description, schema, host control, model/API configuration, or missing evidence.
4. Change the narrowest owner and rerun the affected cases.

For a rewrite:

1. Run the current prompt as the baseline before changing it when runtime access is safe and available.
2. Preserve the model, effective reasoning effort, tools, inputs, state, cache, parser, and output contract unless one of those is explicitly in scope.
3. Compare candidate and baseline on identical cases.
4. Remove repeated rules or examples one group at a time; add a new instruction only for an observed failure.
5. Keep a regression only when it is an explicit accepted tradeoff.

If the model or API contract also changes, route the migration to the provider-specific owner. Do not attribute a result to prompt wording when several runtime variables changed together.

## Record Results

For each case, record:

- input or fixture identifier;
- model/API and relevant request settings;
- artifact version;
- pass/fail and failed criterion;
- evidence or trace supporting the judgment;
- tool calls, retries, and terminal state when relevant;
- tokens, latency, and cost only when they are decision inputs.

Static inspection proves structure, not runtime behavior. If execution is unavailable, return the cases, expected behavior, and precise unverified claims. Never label a prompt production-ready solely because it reads well.

## Repository Package Verification

When this skill package changes:

1. Run a targeted audit that fails if the exact package disappears: `node scripts/codex/audit-skills.mjs --all-workspaces --skill-path .agents/skills/prompt-creator`
2. When runtime proof is available, use a fresh consuming client for these routing probes:
   - Explicit: request `$prompt-creator` for a named production prompt and confirm the skill loads.
   - Implicit positive: request a production system prompt without naming the skill and confirm it loads.
   - Nearby negative: request ordinary prose copyediting and confirm the skill does not load.
3. When ownership routing changed, also request a persisted Codex-agent prompt and confirm `optimizer` controls the Codex surface while `prompt-creator` controls its prompt content.
4. Record invocation evidence and final behavior. If a fresh client is unavailable, mark routing behavior unverified rather than inferring it from static inspection.
