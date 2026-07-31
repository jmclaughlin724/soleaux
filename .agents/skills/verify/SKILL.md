---
name: verify
description: Verify completed changes through owner checks, real configured consumers, a bounded operational-fitness screen, and one risk-selected probe before claiming completion.
---

# Verify

## Contract

Try to falsify one frozen completion claim with fresh evidence from the changed owner and its real configured consumer. The claim includes both functional correctness and operational fitness. Screen each runnable claim for performance, repeated-use state and cleanup, resource or external-call cost, reliability and recovery, and UX or DX friction and noise. Deepen only the dimension governed by an accepted criterion, invariant, target, comparison, or the highest material regression risk; record `no material operational exposure` when none applies. A correct result that violates an accepted operational criterion is not complete. Treat repeated requests for unchanged setup, credentials, or approvals and duplicated registrations, work, warnings, or retries as regressions unless the accepted owner contract requires them.

Verification is read-only by default. Repair only when the current request already authorizes implementation and the repair remains inside the accepted scope, owner, contracts, permissions, and side-effect boundary. An explicit verification-only request never authorizes repair.

Use owner tests, type checks, linters, and structural checks when they directly exercise the claim, but do not treat them as substitutes for an observable consumer route. Do not ban useful checks merely because a runtime surface exists. Verify through a local, test, sandbox, fixture, preview, or rollback-safe route; never mutate shared or production state solely to gain confidence.

## Direct Workflow

1. Freeze the active functional requirements, operational criteria, exclusions, changed owners, direct consumers, diff range, and exact completion claim. Exclude unrelated dirty or concurrent work.
2. Map each claim to its observable surface and configured entrypoint. Use the surface catalog in [the verification playbook](references/verification-playbook.md#surface-mapping). If the change crosses boundaries, identify the input, transformation, persistence or side effect, and returned result before running probes.
3. Select the narrowest evidence set that proves the claim:
   - focused owner checks for the changed contract;
   - one invocation through the consumer's real manifest, configuration, package boundary, route, hook, agent, or UI; and
   - a bounded operational-fitness screen and the relevant criterion or comparison through that same route; and
   - one highest-risk failure or degradation mode not already covered by those checks.
4. Establish the expected functional result and selected operational criterion before driving the route. Prefer, in order, a repository-owned invariant, budget, or SLO; an accepted task target; a versioned benchmark; or a controlled before-and-after baseline. Do not invent a threshold. An explicit comparative claim such as "optimal," "fastest," or "simplest" requires an accepted comparison set; use `BLOCKED` when that comparison is material but undefined or unavailable.
5. Drive the success route and capture both the result and the selected operational evidence. For browser-visible behavior, inspect the rendered state, relevant interaction, console, network request, timing, and server evidence needed by the claim. For nonvisual behavior, capture the exact exit status, stdout or stderr, response, state transition, artifact, timing, repeated-use behavior, prompts, or resource signal needed by the claim.
6. Run the selected risk probe from [the probe catalog](references/verification-playbook.md#probe-selection). The operational challenge may be this probe when it is the highest risk. Reuse an owner test when it already covers the risk; otherwise add at most one disposable discriminating probe. Do not create a durable harness, registry, snapshot, fixture, or abstraction for a one-time challenge.
7. Compare expected and observed behavior at every in-scope boundary. A functionally correct route that violates an accepted invariant or criterion, materially misses its target, or regresses its controlled baseline is a failure. Stop broad investigation at the first broken boundary. After two consecutive layers produce no relevant signal, stop and classify the route as blocked instead of repeating adjacent checks.
8. Classify every failure as product, test fixture, probe, tool, dependency, environment, permission, or unresolved before changing production code. An invalid probe or unavailable dependency is not a product defect.
9. When repair is authorized, confirm the root cause, patch the canonical owner, and rerun the affected owner checks, configured-consumer route, operational comparison, and risk probe after the last edit. Invoke `$debug` only when diagnosis remains genuinely uncertain and `$upstream` only when external semantics decide correctness.
10. Return one verdict and stop. Do not trigger another workflow solely because verification found a failure.

## Verdicts

- `PASS`: every in-scope functional claim and bounded operational-fitness screen holds through its focused owner evidence, configured consumer, selected criterion or comparison, and risk probe.
- `FAIL`: observed product behavior contradicts an accepted functional requirement or operational invariant, materially misses an applicable target, or regresses a controlled baseline through a valid route.
- `BLOCKED`: a required safe route, authority, dependency, consumer, target comparison, or decisive signal is unavailable.
- `SKIP`: every changed surface is documentation, test-only evidence, or another non-runnable artifact with no behavioral or configured-consumer claim to exercise.

Ambiguity is not automatically failure. Resolve it through the narrowest discriminating evidence; use `BLOCKED` when decisive evidence remains unavailable. A mixed result cannot pass: report the most consequential non-pass verdict and identify each affected claim.

## Output

Lead with the verdict. State the frozen claim, scope, configured consumers, owner checks, success route, operational-fitness screen, selected criterion or baseline, observed result and delta, selected risk, failure classification, any authorized repair, reruns, and residual risk. Identify prompts, duplicate work, retries, or log noise when they affect an accepted criterion. Use the compact [evidence format](references/verification-playbook.md#evidence-format) when several claims or boundaries would otherwise be ambiguous.

Fresh evidence must follow the last relevant edit and name the command or tool action plus the real consumer route. A passing adjacent suite, mocked substitute, or source inspection alone is not runtime evidence for a runnable claim.

## Boundaries

- Verification does not expand task authority or permit external, irreversible, or production effects.
- Do not bootstrap another verification skill, persist a transient recipe, or create repository infrastructure solely because a convenient route is absent.
- Do not run every available suite or probe every risk category; choose the narrowest decisive evidence.
- Do not turn the operational-fitness screen into an unbounded optimization audit. Deepen at most the highest-risk applicable dimension unless the accepted claim names more.
- Do not require debugging, simplification, documentation, planning, or upstream handoffs when the evidence is already decisive.
- Stop when the active functional and operational claim is falsified, verified, legitimately skipped, or precisely blocked.
