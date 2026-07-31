# OpenAI Agents SDK Multi-Agent

Sources verified 2026-07-16:

- https://developers.openai.com/api/docs/guides/agents
- https://developers.openai.com/api/docs/guides/agents/orchestration
- https://developers.openai.com/cookbook/examples/agents_sdk/parallel_agents
- https://developers.openai.com/api/docs/guides/agents/running-agents
- https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- https://developers.openai.com/api/docs/guides/agents/results
- https://developers.openai.com/api/docs/guides/agents/integrations-observability
- https://developers.openai.com/api/docs/guides/agent-evals

## Use This Reference

Use this reference for application-owned, code-first multi-agent workflows built with the OpenAI Agents SDK for TypeScript or Python. Invoke `openai-docs`, then use `openai-agent-guidance` for current implementation wiring. This reference owns orchestration choices and verification, not a duplicate SDK API reference.

## Choose The Reply Owner

Choose this before defining specialists:

- **Handoff:** transfer control when a specialist should own the next user-facing response. The application still owns policy, validation, and the completion decision.
- **Agents as tools:** keep a manager in control when specialists provide bounded results that the manager must synthesize into the user-facing response.

Add a specialist only when it materially improves capability or policy isolation, prompt clarity, or trace legibility. Splitting too early adds prompts, traces, and approval surfaces.

## Choose The Parallelism Owner

- **Application fan-out/fan-in:** run a fixed set of independent specialists concurrently in the host runtime, collect every result, then pass the labeled outputs to a manager or deterministic reducer. Prefer this for a known graph, explicit timeouts and cancellation, and direct latency control.
- **Planner-driven agents-as-tools:** expose specialists as tools and, when current SDK and model support is verified, allow parallel tool calls. Prefer this when the manager should choose which specialists are needed dynamically. Account for the planning call, tool-call context, and less deterministic routing.

The application owns concurrency, timeouts, cancellation, and fan-in for deterministic parallel runs. Do not translate these controls into Responses API Multi-agent fields or Codex `[agents]` configuration.

## Run And Continue

The runner loops through model output, tool execution, handoffs, and final output until it reaches a real stopping point. Treat a handoff as a change of current agent, not as a detached summary.

Choose one continuation strategy per conversation: application-owned replay, an SDK session, an OpenAI conversation ID, or response-ID chaining. Mixing local replay with server-managed state can duplicate context. Preserve the final output, last active agent, interruptions and resumable state, item records, and usage needed by the application's next turn, audit, or renderer.

## Guardrails And Approvals

- Input guardrails apply to the first agent in the chain.
- Output guardrails apply to the agent that produces the final output.
- Tool guardrails apply only to the function tools that own them.
- Sensitive side effects should pause for human approval. Resolve the returned interruptions and resume the same saved run state instead of starting a new user turn.

Put validation beside each sensitive tool rather than assuming a root guardrail covers nested or post-handoff calls.

## Trace And Evaluate

Tracing is enabled by default in the normal server-side SDK path. Wrap the full fan-out/fan-in in one trace and inspect model calls, tool calls and outputs, handoffs, guardrails, approvals, and custom spans. Add trace graders for routing, handoff timing, specialist and tool selection, instruction and safety adherence, and final synthesis. Move representative cases into datasets and repeatable eval runs before treating the workflow as stable.

## Verification

Exercise at least:

1. A fixed parallel fan-out/fan-in with two independent specialists and complete result collection.
2. A manager-style agents-as-tools path and an intentional handoff path, verifying reply ownership.
3. A nearby sequential or shared-state task that should stay single-agent.
4. A sensitive tool interruption, approval or rejection, and resumption from the same run state.
5. One complete trace and graders for routing, tools, handoffs, guardrails, and synthesis.
6. A single-agent baseline comparison for quality, latency, tokens, and cost.
