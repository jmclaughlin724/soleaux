# OpenAI Agent Implementation Contract

Use current official documentation as the source of truth. This checked-in contract captures the repository decisions derived from the source set reviewed on 2026-07-12.

## Contents

- [Authority and surface routing](#authority-and-surface-routing)
- [GPT-5.6 migration](#gpt-56-migration)
- [Prompt design](#prompt-design)
- [Prompt caching](#prompt-caching)
- [Citation formatting and grounding](#citation-formatting-and-grounding)
- [Tool routing and tool search](#tool-routing-and-tool-search)
- [Programmatic Tool Calling](#programmatic-tool-calling)
- [Skills](#skills)
- [Multi-agent](#multi-agent)
- [Reasoning mode and effort](#reasoning-mode-and-effort)
- [Apply Patch](#apply-patch)
- [Validation](#validation)
- [Official sources](#official-sources)

## Authority and surface routing

- Put durable repository conventions, commands, boundaries, and completion requirements in `AGENTS.md`; closer nested files override broader ones.
- Put reusable task workflows with supporting references or scripts in skills. Keep `SKILL.md` concise and use progressive disclosure.
- Put trusted-repository Codex settings in `.codex/config.toml`, lifecycle enforcement in hooks, and Codex `prefix_rule` command execution policy in `.codex/rules`. Keep each custom role as one standalone TOML with `name`, `description`, and `developer_instructions` under `.codex/agents` or `~/.codex/agents`. A request merely described as a "rule" does not select `.codex/rules`.
- Use the Responses API as the primary integration for reasoning work; reasoning models perform better through Responses than Chat Completions. Route requests through one central client or orchestration layer instead of per-feature ad hoc API calls.
- Keep OpenAI Responses API request fields separate from Codex configuration. API `reasoning.mode`, Programmatic Tool Calling, tool search, and Responses multi-agent are not implied Codex config keys.
- Preserve instruction authority: application policy belongs in developer instructions; user messages provide task input and configuration. `instructions` applies only to the current Responses call and must be supplied again on later calls, including continuations through `previous_response_id`.
- Preserve reasoning state across turns: multi-turn quality can depend on reasoning items, tool calls, tool outputs, and persisted reasoning context, not merely the visible assistant text. Give conversation state one owner that continues with `previous_response_id`, a Conversation, or complete replay — not feature-specific message-history arrays.

## GPT-5.6 migration

- The current flagship target is `gpt-5.6-sol`; the `gpt-5.6` alias routes to Sol. Use Terra for balanced cost and capability and Luna for efficient high-volume work when those roles are intentional.
- Do not perform a blind model-string replacement. Inventory active defaults, endpoints, prompts, effective reasoning, tools, schemas, caching, replay, multimodal inputs, routers, allowlists, capability metadata, UI, tests, evals, and deployment configuration.
- Preserve behavior, latency class, cost class, reasoning level, endpoint contract, tool semantics, cache behavior, output contract, and user-visible behavior before tuning.
- Classify each site as simple Sol, tier-aware family, compatibility, prompt-only, optional feature adoption, or leave unchanged. Do not migrate every low-cost or latency-sensitive route to Sol.
- Keep Pro mode, persisted reasoning, explicit caching, Programmatic Tool Calling, and multi-agent adoption separate from a baseline model migration.
- Leave historical docs, snapshots, fixtures, eval baselines, provider comparisons, pricing tables, intentionally pinned fallbacks, and ambiguous old-model usages unchanged unless explicitly in scope.

## Prompt design

- Start from a prompt and tool set that works. Remove one repeated instruction, example group, or irrelevant tool at a time and rerun representative evals.
- Put stable role, tone, and application policy in the highest appropriate instruction layer. Keep task-specific details, dynamic context, and concise examples in user input.
- State the user-visible outcome, success criteria, constraints, authority boundaries, required evidence, output shape, fallback behavior, and stopping conditions. Describe the destination instead of prescribing every reasoning step.
- Preserve explicit user values. For implicit choices, give decision criteria instead of universal defaults, keyword maps, or broad semantic shortcuts.
- Avoid repeated `ask first` rules, generic `think step by step`, blanket brevity or thoroughness commands, and giant prompt rewrites without measured evidence.
- Use Markdown headings and lists for hierarchy and XML tags when they materially clarify boundaries between instructions, examples, and context.
- Store production prompts in a small reviewed module near the feature, use typed inputs or schemas, and test representative fixtures and evals. Do not introduce deprecated reusable prompt objects for new work, and migrate existing remote prompt IDs into version-controlled code before the scheduled `v1/prompts` shutdown (November 30, 2026).
- For long workflows, name the current layer, provide one short preamble before tool work, update only at major phase changes, and preserve assistant phase values when manually replaying history.

## Prompt caching

- Prompt caching is automatic for eligible prompts of at least 1,024 tokens and requires exact prefix matches. Put stable instructions, examples, tool definitions, structured-output schemas, images, and files before variable user or request data; image detail and tool lists must remain identical for a hit.
- On GPT-5.6 and later families, set a stable `prompt_cache_key` for requests that share a long prefix to use the more reliable matching path. Keep traffic for one key near 15 requests per minute; partition higher-volume traffic with a stable mapping rather than churning keys.
- Use `prompt_cache_options.mode: "implicit"` for the default latest-message breakpoint plus any explicit breakpoints. Use `"explicit"` only when the application should read and write solely at marked `prompt_cache_breakpoint` blocks and should avoid cache writes when no marker exists.
- Put explicit breakpoints immediately after genuinely reusable content. A request can create at most four cache writes; implicit mode consumes one write slot for the latest message. For reads, the service considers up to the latest 50 conversation breakpoints and uses the longest matching prefix.
- For GPT-5.6 and later families, use `prompt_cache_options.ttl`; the current supported and default minimum lifetime is `30m`. Do not use the older `prompt_cache_retention` field as its replacement on GPT-5.6.
- Measure `cached_tokens` and, on GPT-5.6 and later, `cache_write_tokens`. Cache writes cost 1.25 times the uncached input rate, so optimize for net cost and latency from subsequent reads rather than maximum write volume.
- Preserve privacy and retention requirements: caches are organization-scoped, cannot currently be cleared manually, count toward rate limits, and follow the current data-controls documentation. Caching changes input processing, not output generation or determinism.
- Treat explicit caching as an evaluated optimization. Keep automatic behavior when code changes are unnecessary, and adopt keys or breakpoints only when representative traffic demonstrates a beneficial hit rate, latency, and cost profile.

## Citation formatting and grounding

- Define the citable unit before prompting. Prefer stable block-level units for most systems; use document-level units when coarse attribution is enough and line ranges only when exact verification justifies the added formatting burden.
- Present every citable unit with a stable source ID and readable text, plus useful metadata such as title, URL, or timestamp. Keep the model-facing source ID separate from a UI locator when the renderer can resolve precise highlights downstream.
- Define one exact, parseable citation grammar. Prefer OpenAI's familiar marker family: `\uE200cite\uE202<source_id>\uE202<optional_locator>\uE201`. Keep IDs and locators constrained to validated application formats.
- Tell the model when and where to cite, how to cite multiple supporting sources, which formats are forbidden, and what to do when support is missing. Place citations after punctuation and next to the supported claim; do not group them in a citation-only paragraph or hide them inside bold, italics, or code fences.
- Cite only retrieved or injected citable material that directly supports the claim. Never expose a raw reference ID, invent an ID or locator, turn missing evidence into a factual negative, or cite outside knowledge as if it came from the provided corpus.
- When sources conflict, cite the conflicting evidence and describe the disagreement. Label inference separately from directly supported facts, and narrow or abstain when required support is unavailable.
- Parse citations before rendering. Validate the citation family, source IDs, optional locators, source existence, character offsets, and claim proximity; resolve links or highlights and remove or replace raw private-use markers safely.
- Test single-source, multi-source, line-locator, missing-support, conflicting-source, malformed-marker, invented-ID, Markdown-boundary, streaming, and renderer cases. Evaluate citation correctness and coverage separately from answer quality.
- Use hosted-tool native citation behavior when available instead of replacing it with a custom marker layer. Use this custom contract for developer tools or injected context that do not already provide rendered citations.

## Tool routing and tool search

- Expose only task-relevant tools. Descriptions must say what the tool does, when to use it, important return fields and types, and error behavior.
- Complete prerequisite discovery, retrieval, and validation before an action. Parallelize independent reads, keep dependent work sequential, and synthesize retrieved evidence before mutation.
- If retrieval is empty, partial, or suspiciously narrow, try one or two meaningful fallbacks before concluding that evidence is unavailable.
- For deferred tools, prefer clear namespaces or MCP servers. The model sees the namespace or server name and description before loading contained functions; keep the high-level description concise and informative.
- Aim for fewer than ten functions per deferred namespace when practical. Use hosted search when the candidate set is known at request time and client-executed search when discovery depends on tenant, project, or application state.
- Dynamically loaded tools remain available in future turns. Changing the loaded set breaks the cache from that point; avoid reloading the same tool or churning the set unnecessarily.

## Programmatic Tool Calling

- Use direct calls for one lookup or action, adaptive semantic judgment, writes, approvals, citations, or final native-artifact validation.
- Use Programmatic Tool Calling only when predictable code can reduce several structured results into a smaller structured result through filtering, joining, ranking, deduplication, aggregation, or validation.
- Define the bounded stage, eligible tools, input and output schemas, required evidence, concurrency, retry limit, stopping condition, structured failure, and single handoff back to direct judgment.
- Prefer read-only and idempotent tools. Validate arguments and permissions for every program-issued call and require application-level approval for high-impact actions regardless of caller.
- Handle `program`, program-issued function calls, function outputs, and `program_output` while preserving `call_id` and `caller`. Validate the final assistant message separately from the program output.
- Compare against a direct-call baseline on correctness, completeness, evidence, tokens, latency, cost, calls, retries, and safety. Fewer calls are not an improvement if the final answer fails its quality bar.

## Skills

- Skill metadata (`name`, `description`, and path) enables discovery; the model reads the full `SKILL.md` only after selection. Make descriptions precise enough to trigger the right workflow.
- Explicitly request a skill when deterministic use matters. Treat its instructions as user-provided context and reconcile them with higher-authority instructions.
- Treat every skill as privileged code and potentially untrusted instructions. Inspect it before use, especially with network access.
- Do not expose arbitrary open skill catalogs to end users. Integrate reviewed skills into bounded product workflows, validate data-retention requirements, and require explicit approval for writes or high-impact actions.

## Multi-agent

- Use multi-agent for independent, bounded research, comparison, exploration, diagnosis, or implementation streams where parallel execution or separate context materially improves speed or coverage.
- Prefer one agent when steps form one ordered reasoning chain, the task is small, agents would contend over shared mutable state, or a fixed deterministic graph is required.
- Give each worker a closed scope, relevant context, tools, completion condition, and output contract. Cap concurrency, avoid duplicate work, wait for requested results, and require the root agent to synthesize the final answer.
- For the Responses API beta, the documented default and recommended `max_concurrent_subagents` is 3. Account for current incompatibilities: `/responses/compact`, `reasoning.summary`, and `max_tool_calls` are not supported when enabled; automatic server-side compaction is applied independently.
- Keep API multi-agent adoption optional during a model migration and implement its output items, function-call execution, replay, tracing, and beta header deliberately.

## Reasoning mode and effort

- GPT-5.6 Responses requests support standard and Pro modes. Standard is the default. Mode and effort are independent; omission defaults GPT-5.6 to medium effort.
- Reasoning tokens are real budget: they are not exposed as raw text, but they consume the context and output budget and are billed as output, and a response can end `incomplete` before any visible text is produced. Handle incomplete responses, output-budget policy, retries, and token telemetry centrally rather than per feature.
- Preserve the old effective effort for the baseline, then compare the same setting and one lower on representative workloads. Treat effort as a tuning knob, not a substitute for a clear goal and output contract.
- Enable Pro mode only in the Responses API for difficult, quality-first work that tolerates higher latency and token usage. Keep the same model slug; do not invent a separate `gpt-5.6-pro` model.
- Evaluate standard versus Pro with the same prompt and task set. Measure task success, completeness, evidence, total tokens, latency, and cost.

## Apply Patch

- Use apply patch for precise bug fixes, focused multi-file refactors, tests and docs, and mechanical migrations.
- Restrict allowed paths and prevent traversal. Decide whether the harness is atomic or per-file and return `failed` with a clear diagnostic for missing files, invalid context, or conflicts.
- Provide current file context or filesystem exploration tools. Keep diffs small and targeted.
- Do not treat a `Done` or successful call status as correctness. Inspect the resulting diff and run the narrowest relevant tests, type checks, lint checks, build, or smoke test.

## Validation

For migrations, compare:

1. old model, prompt, and settings;
2. GPT-5.6 target with the same prompt and preserved effective reasoning;
3. the target with one lower effort;
4. the narrowest prompt or API fix required by a measured failure;
5. optional capabilities as isolated treatments.

Measure task success, user-visible quality, parser validity, tool choice and arguments, retries, completion rate, latency, timeouts, token categories, cost per successful task, cache behavior, replay and compaction, multimodal accuracy, citations, preserved behavior, and validation evidence.

## Official sources

- [Latest model](https://developers.openai.com/api/docs/guides/latest-model)
- [Prompting](https://developers.openai.com/api/docs/guides/prompting)
- [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Citation formatting](https://developers.openai.com/api/docs/guides/citation-formatting)
- [GPT-5.6 Sol migration](https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol)
- [GPT-5.6 prompting guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)
- [Skills](https://developers.openai.com/api/docs/guides/tools-skills)
- [Responses multi-agent](https://developers.openai.com/api/docs/guides/responses-multi-agent)
- [Reasoning](https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode)
- [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
- [Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
- [Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
- [Apply Patch](https://developers.openai.com/api/docs/guides/tools-apply-patch)
- [Codex manual](https://developers.openai.com/codex/codex-manual.md)
