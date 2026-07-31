# Responses Tool Orchestration

Sources verified 2026-07-12:

- https://developers.openai.com/api/docs/guides/tools-tool-search
- https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling
- https://developers.openai.com/api/docs/guides/tools-apply-patch
- https://developers.openai.com/api/docs/guides/latest-model

## Intent

Choose one explicit route for each tool-using stage: direct model calls, deferred discovery through tool search, or bounded JavaScript processing through Programmatic Tool Calling. Preserve approval, evidence, and final validation as direct application or model responsibilities.

## Route Selection

Use direct calls when one call is enough, the next action depends on semantic judgment, an approval may be required, or the result must preserve citations or native artifacts.

Use tool search when the available function or MCP inventory is too broad to load up front. Prefer clear namespaces or MCP servers, expose concise discovery descriptions, and keep namespaces below roughly ten functions when practical. Use hosted search for an inventory known at request creation and client-executed search when discovery depends on tenant, project, or runtime state. Deferred tools are unavailable to a program until a top-level tool-search step loads them.

Use Programmatic Tool Calling only for predictable filtering, joining, ranking, deduplication, aggregation, validation, or dependent data flow that can return a smaller structured result. Define:

- the bounded stage and eligible tools
- documented input and output fields
- the exact result schema and required evidence
- concurrency, retry, failure, and stop limits
- the single handoff back to direct model judgment
- application-level approval for every high-impact action

Keep unknown return shapes, adaptive search, writes, approvals, citation checks, and final artifact validation direct. Preserve every `call_id` and `caller` relationship, continue until the final assistant message arrives, and test `program_output` separately from that message.

## Apply Patch

- Give the model current file context or filesystem exploration tools before patching.
- Restrict paths and decide whether patch application is atomic or per-file.
- Prefer small, focused diffs and return exactly one explicit success or failure result for each patch call.
- Treat the patch acknowledgement as application evidence only. Run the narrowest tests, linter, parser, or guard that proves behavior after the patch.

## Prompt Contract

When more than one route is available, state the route once per stage: allowed tools, expected schema, evidence, concurrency, retries, stopping condition, side-effect boundary, and fallback. Do not switch routes or repeat completed calls without an explicit recovery rule.

## Evaluation

Start from direct calls as the baseline. Compare correctness, completeness, evidence coverage, tokens, latency, cost, calls, retries, recovery, and approval behavior on representative tasks. Lower resource use counts as an improvement only when the final answer still passes the existing quality bar.
