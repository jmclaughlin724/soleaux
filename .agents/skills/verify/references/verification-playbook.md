# Verification Playbook

## Surface Mapping

Map the changed behavior to the narrowest observable route that a real consumer uses. Screen each operational dimension, then capture deeper evidence only for an accepted criterion or the dimension most likely to regress.

| Change surface | Functional evidence | Relevant operational evidence |
| --- | --- | --- |
| CLI or TUI | Invoke the installed or manifest-owned command and observe terminal behavior and exit status. | Cold or warm time, repeated invocation, prompt count, cleanup, and avoidable stderr or log noise. |
| API, action, or handler | Send a real local or sandbox request through the registered route and inspect response plus decisive server evidence. | Latency, request count, payload size, retry behavior, resource use, and recovery. |
| Browser or GUI | Drive the affected interaction and inspect pixels, state, console, network, and backing response only as needed. | Interaction timing, duplicate requests, layout stability, error recovery, and console or network noise. |
| Library or package | Import through the public package boundary from a real consumer or isolated consumer fixture. | Import or call cost, repeated-use behavior, allocation or process growth, and warnings. |
| Prompt, skill, agent, or hook | Exercise the actual registered runtime path and inspect its delivered prompt, decision, or tool behavior. | Tool-call count, redundant context, unnecessary approvals or prompts, latency, and false-positive noise. |
| Configuration or manifest | Start or query the configured consumer without cwd or environment compensation that production does not use. | Startup cost, duplicate registrations, manual setup, credential friction, and recovery after restart. |
| CI or deployment | Use the owner-provided local simulator, dry run, preview, or isolated workflow route; do not mutate production for verification. | Duration, cache behavior, resource use, retries, and actionable output. |
| Structural policy | Run the configured policy owner over positive, negative, scope, and consumer fixtures. | Scan duration, false-positive noise, deterministic output, and bypass resistance. |

For a cross-layer flow, trace only the boundaries the claim depends on:

1. user or caller input;
2. registered entrypoint;
3. validation and transformation;
4. persistence, dependency, or side effect;
5. response or rendered result.

Stop at the first boundary whose observed behavior contradicts the requirement. Do not keep checking downstream layers that cannot repair or disambiguate that boundary.

## Operational Fitness

Screen every runnable claim for latency and cold or warm behavior; repeated-use state and cleanup; throughput, memory, process, connection, or external-call cost; reliability and recovery; prompt and credential friction; duplicate work; and consumer-visible logs or warnings. Deepen at most the dimension required by an accepted plan, named in the completion claim, or presenting the highest material regression risk. Record `no material operational exposure` when the screen identifies none.

Resolve the comparison in this order:

1. repository-owned invariant, budget, SLO, or acceptance criterion;
2. accepted task or plan target;
3. versioned benchmark or documented prior result;
4. controlled before-and-after measurement through the same configured route.

Do not invent a number or substitute a mocked route. If optimization is central and no decisive criterion or comparison can be established safely, return `BLOCKED`. Claims using "optimal," "fastest," or "simplest" require a measured or structural comparison across the accepted alternatives, including setup steps and steady-state overhead. A single correct response cannot pass when the route violates an accepted operational invariant, materially regresses a known target, or regresses a controlled baseline.

The default repeated-use invariant is zero repeated requests for unchanged setup, credentials, or approvals and zero duplicated registrations, work, warnings, or retries, unless the accepted owner contract requires them. Verify this with one repeated invocation or a structural ownership trace, not a broad search.

Keep the challenge bounded. The configured success route plus the one selected risk probe is the total default evidence budget; the operational challenge may be that probe. Do not run a generic benchmark suite.

## Probe Selection

Choose one plausible risk not already covered by the focused owner checks. Prefer the probe most likely to distinguish a correct implementation from a superficially passing one.

| Risk | Useful probe |
| --- | --- |
| Validation | Malformed, partial, boundary, or unsupported input. |
| Failure handling | Dependency rejection, timeout, unavailable service, or nonzero subprocess. |
| Consumer wiring | Invoke the configured entrypoint from its real manifest or configuration. |
| Bypass | Use an alternate route, alias, or direct import that could skip the owner. |
| State | Retry, duplicate delivery, stale state, cancellation, cleanup, or restart. |
| Permissions | Missing authorization, wrong tenant, denied capability, or approval rejection. |
| Compatibility | One protected public input or output at the changed boundary. |
| Ownership | Exact sweep for a removed path, duplicate owner, or stale registration. |
| Presentation | Narrow viewport, empty or error state, clipping, focus, or interaction timing. |
| Latency | Cold and warm samples through the configured consumer against the owned target or controlled baseline. |
| Repeated use | A second invocation, reconnect, retry, or restart that exposes duplicate setup, stale state, or accumulated work. |
| Resources | Bounded process, memory, file, connection, or request inventory before and after the route. |
| UX or DX | Count required prompts, approvals, credentials, manual steps, warnings, and duplicate registrations. |
| Recovery and noise | Exercise one expected failure or restart and inspect cleanup plus actionable, non-duplicated output. |

Do not probe every row. Prefer a temporary input, isolated directory, existing owner fixture, local preview, or read-only invocation. Delete disposable artifacts when finished.

## Failure Classification

Before editing production code, answer:

1. Did the route invoke the configured consumer and canonical owner?
2. Is the expected result required by the frozen claim?
3. Did cwd, environment, fixture construction, dependency availability, permissions, stale state, or the probe itself cause the observation?
4. Can the observation be reproduced through the narrowest owner-provided route?

Classify the result as product, fixture, probe, tool, dependency, environment, permission, or unresolved. Repair only a confirmed, in-scope product defect.

## Evidence Format

Use one row per claim or boundary only when a single paragraph would be ambiguous.

| Claim | Consumer and route | Check or probe | Target or baseline | Observed and delta | Result |
| --- | --- | --- | --- | --- | --- |
| `<claim>` | `<configured entrypoint>` | `<command or action>` | `<functional result and operational target>` | `<result and comparison>` | `PASS`, `FAIL`, `BLOCKED`, or `SKIP` |

Then state the selected risk, failure classification, repair if any, reruns, and residual risk. Avoid a separate ledger, handoff template, or duplicated downstream-skill output unless a real consumer requires it.
