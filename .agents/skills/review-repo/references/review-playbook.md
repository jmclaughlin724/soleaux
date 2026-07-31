# Review Playbook

## Review Angles

Run independent angles before deduplication so one early conclusion does not suppress another. Scale the number and depth of angles to the diff; a small documentation change does not need the same sweep as an authentication or migration change.

### Correctness and changed behavior

- Read each hunk and its enclosing control flow for inverted conditions, boundary errors, missing awaits, falsy-value mistakes, wrong-variable copy/paste, swallowed errors, unsafe defaults, incomplete cleanup, and platform or timezone assumptions.
- For every removed or replaced line, name the invariant it previously enforced and locate where the new code preserves it.
- Trace changed functions through direct callers and callees for new preconditions, return shapes, exceptions, timing, ordering, and wrapper or proxy routing errors.

### State, concurrency, and failure handling

- Check retries, duplicate delivery, cancellation, teardown, stale state, partial failure, lock or transaction scope, cache invalidation, hydration, and restart behavior when relevant.
- Confirm failure paths preserve cleanup and do not turn a recoverable error into corruption, silent success, or an infinite retry.

### Security, privacy, and permissions

- Trace untrusted input through parsing, authorization, storage, rendering, subprocesses, network requests, and logs.
- Check tenant and ownership boundaries, approval behavior, secret exposure, path traversal, injection, unsafe deserialization, open redirects, confused-deputy routes, and overly broad external effects.
- Require a concrete attack or exposure path; do not label generic hardening advice as a finding.

### Contracts and consumers

- Compare the change with public exports, routes, schemas, manifests, provider contracts, feature flags, configuration precedence, and generated owners.
- Resolve configured consumers from their own manifests or configuration. Check aliases and alternate routes that could bypass the canonical owner.

### Documentation quality

- Review heading hierarchy, terminology, formatting, rendered structure, links, examples, and procedural clarity when the change affects documentation.
- Keep a documentation finding when it can mislead a reader, obscure a prerequisite or warning, break rendering or navigation, misstate a contract, reduce accessibility, or cause an unsafe or incorrect action. Leave preference-only wording and formatting to the applicable editorial or automated owner.

### Pull request mergeability

- For a live pull request, resolve current base and head revisions, draft state, merge conflicts or provider mergeability, required reviews, and required checks through the available read-only hosted route.
- Distinguish CI withheld by a fork approval, credential, or trust boundary as `fork-restricted`. Do not collapse that state into passed, failed, pending, or generic skipped CI; report the observed restriction and the repository-policy consequence.

### Verification and tests

- Identify changed behavior not exercised by focused success and failure coverage.
- Inspect whether fixtures accurately model production configuration and whether assertions can pass without invoking the changed owner.
- Flag a test gap only when it protects a named regression or contract.

### Reuse, simplicity, performance, and conventions

- Look for reimplementation of an existing canonical helper, duplicated schema or policy, avoidable nesting, dead code, and a new abstraction that increases total complexity.
- Flag performance only with a plausible workload and causal path such as unbounded growth, repeated I/O, N+1 access, synchronous blocking, or hot-path allocation.
- Apply repository and framework conventions where violating them creates a real correctness, ownership, operability, or maintenance risk. Leave formatting and taste to automated owners.

## Candidate Verification

For each candidate, answer:

1. What exact changed line or deleted invariant causes the issue?
2. Which accepted contract or direct consumer is affected?
3. What concrete input, state, timing, permission, or platform reproduces it?
4. Does another owner, guard, caller, test, or framework guarantee invalidate the claim?
5. What is the narrowest correction that preserves the requested behavior?

Discard the candidate when these questions cannot establish a changed, actionable defect. Record an uncertainty separately only when missing evidence is material to the review verdict.

## Finding Shape

Use a compact finding:

`[P1] Short imperative title — path/to/file.ts:123`

Then explain the triggering scenario, observed or inevitable consequence, and why the current guard or test does not prevent it. End with the narrowest correction when it is not obvious. Keep the cited range tight enough that a maintainer can act without searching.

For live PR feedback, add the source thread or comment identifier to the internal worklist, but do not expose a large process ledger unless the user needs to select items.
