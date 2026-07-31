# Per-page decisions

Read this before refactoring a route whose correct Cache Components or prefetch behavior depends on product intent, security guarantees, cache scope, or an intentionally blocking experience.

## Blocking reads

Read the full installed-version guide returned by `nextjs_docs`, not only the inline error or overlay card. Then frame uncertainty as a user-experience and data-scope decision:

- Must this content appear in the immediate shell, or may it stream?
- Is the result shared, per-user, or per-request?
- What freshness and revalidation policy does the product require?
- Is a pre-click server invocation worth the faster navigation?

Tie the answer to the technical option: shared cache, private/runtime cache, granular Suspense, or a deliberate request-time block. Do not ask the user to choose an API without explaining the visible and data-handling consequence.

## Security and authorization gates

Stop before moving an access check, auth redirect, feature gate, or other top-of-page guard behind Suspense. Wrapping a security gate can change what renders or executes before authorization. Only a deliberate architecture decision can choose among:

- keeping the route or ancestor blocking;
- restructuring the shell so protected data remains behind an authorized boundary;
- moving routing checks to the installed version's documented proxy/middleware surface;
- centralizing data access authorization while keeping the UI shell static.

When every child route shares the gate, a documented ancestor opt-out can be the correct end state. Moving the gate to another architecture is a separate change unless the user explicitly includes it.

## Deliberate blocking

A route may remain blocking when it is inherently request-specific, has no useful public shell, or the correct refactor is outside the approved scope. Confirm that choice with the user, keep the version-documented opt-out, and replace a migration TODO with a reason such as:

```ts
// instant = false: kept intentionally — the dashboard is fully request-time and access-gated.
```

An explicit, reviewed reason is acceptable. An unexplained `instant = false`, connection bailout, or migration TODO is not.

## Evidence for the decision

Keep the live browser on the affected route while asking when possible. Capture the current shell or transition when a headed browser cannot remain visible. Record the selected experience, data scope, freshness, and security constraint in the task summary; add a source comment only when the reason cannot be expressed by clear code and established ownership.
