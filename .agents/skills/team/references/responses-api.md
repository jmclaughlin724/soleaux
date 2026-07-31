# Responses API Multi-agent

Sources verified 2026-07-16:

- https://developers.openai.com/api/docs/guides/responses-multi-agent
- https://developers.openai.com/api/docs/guides/deployment-checklist#use-multi-agent-for-parallel-work

## Contents

- [Request Contract](#request-contract)
- [Hosted Orchestration Contract](#hosted-orchestration-contract)
- [Developer Tool Loop](#developer-tool-loop)
- [Rendering And Tracing](#rendering-and-tracing)
- [Current Limitations](#current-limitations)
- [Verification](#verification)

## Use This Reference

Use this reference when implementing or reviewing server-hosted Multi-agent orchestration through the Responses API. Invoke `openai-docs` before changing an API client because model eligibility, beta request shapes, output items, SDK entry points, and limitations may change.

Treat this feature-specific beta guide as authoritative for hosted Multi-agent behavior. Generic Responses API guidance about application-owned routing describes workflows that do not enable this hosted beta feature.

## Request Contract

As of the verification date, Multi-agent is beta and available with GPT-5.6 models.

- Set `multi_agent.enabled` to `true`.
- Start with `max_concurrent_subagents: 3`, the documented default and recommendation. The value counts active descendants across the whole tree and excludes the root.
- For the beta HTTP SDK, call `client.beta.responses` and pass `responses_multi_agent=v1` through `betas`.
- For raw HTTP or WebSocket, send `OpenAI-Beta: responses_multi_agent=v1`.
- Treat item schemas as beta. Pin and test the SDK/client version used by the application.
- Give the request a prompt that names the independent roles, requires the root to wait for their results, and defines how to reconcile and render the final answer.

Minimal Python request shape:

```python
response = client.beta.responses.create(
    model="gpt-5.6",
    input=(
        "Run three independent reviews: correctness, security, and test gaps. "
        "Wait for all three, reconcile duplicates or conflicts, and return a prioritized result."
    ),
    multi_agent={"enabled": True, "max_concurrent_subagents": 3},
    betas=["responses_multi_agent=v1"],
)
```

Use a developer message to choose the delegation posture. State either that delegation requires an explicit user request or that proactive delegation is allowed when it materially improves speed or quality. Keep this additive to the hosted root and subagent instructions.

## Hosted Orchestration Contract

When Multi-agent is enabled, the Responses API owns the root/subagent tree and exposes hosted collaboration actions: `spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `interrupt_agent`, and `list_agents`.

- Do not execute `multi_agent_call` items or submit outputs for them. The API executes hosted collaboration actions and returns `multi_agent_call_output`.
- Preserve hosted call and output items when replay or tracing requires them.
- Expect every agent to share the request model and configured tools.
- Treat API tree depth and total agent count as unbounded by a fixed product limit, but keep the workflow intentionally shallow and bounded by prompt and concurrency.

## Developer Tool Loop

Any agent may call a developer-defined function. Execute every pending function call and return a matching `function_call_output`.

For HTTP:

1. Stream or collect all completed output items and every pending function call.
2. Append output items to the continuation history.
3. Execute all pending calls, append their matching outputs, and create the next response.
4. Stop only when no developer function calls remain and the root final answer is complete.

For WebSocket:

1. Save the response ID from `response.created`.
2. Execute a function call and send its output in `response.inject` immediately.
3. Continue reading until every injection receives `response.inject.created` or `response.inject.failed` and the response completes.
4. On `response_already_completed`, continue from the completed response with the returned input.
5. Treat other injection failures as errors unless current official documentation says otherwise.

Prefer WebSocket for tool-heavy or long-running workflows because agents can resume as tool outputs arrive. HTTP can be sufficient for hosted-tool-heavy or short workflows with few developer calls.

## Rendering And Tracing

- Render only `/root` messages in `final_answer` phase as the user-facing answer unless the product deliberately exposes agent activity.
- Route subagent text to diagnostics or an inspectable activity view rather than mixing it into the final response.
- Use the `agent` attribution on output items and streaming events. `agent_message` identifies its author and recipient; response lifecycle events describe the whole response.
- Record usage and latency for the entire run, including continuations.

## Current Limitations

When Multi-agent is enabled:

- `/responses/compact` is unsupported.
- `reasoning.summary` is unsupported.
- `max_tool_calls` is unsupported.
- Automatic server-side compaction is enabled for the root and independently for each subagent; an explicit `context_management.compact_threshold` may override the threshold.

Do not infer Multi-agent behavior from the ordinary `parallel_tool_calls` field. They are separate features.

## Verification

Exercise at least:

1. A positive task with two or three genuinely independent workstreams.
2. A nearby sequential or shared-state task that should remain single-agent.
3. A developer-function case proving that calls from both root and subagents are executed.
4. The selected HTTP or WebSocket continuation and failure paths.
5. Root-only rendering, attribution, tracing, and usage collection.
6. A single-agent baseline comparison for quality, latency, tokens, and cost.
