# Cache Components adoption

Enable Cache Components and walk the App Router to a passing build and verified runtime. Use the version-matched migration guide returned by `nextjs_docs` as the API and error-recipe authority; use this reference for sequencing and decision gates.

## Preconditions

- Confirm an `app/` or `src/app/` tree. Cache Components does not migrate a Pages Router-only app. A hybrid app is valid; the flag affects only App Router routes.
- Confirm the installed release supports every required surface. The source workflows require Next.js 16.3+ for top-level `cacheComponents`, `export const instant`, instant-navigation validation, and the adoption codemods. If the installed docs do not contain those APIs, stop and make the framework upgrade a separate explicit change.
- Translate incompatible `dynamic`, `revalidate`, and `fetchCache` segment exports to their documented Cache Components equivalents. Do not delete behavior. When a value cannot yet be translated, preserve the intended value in a `TODO: Cache Components adoption` marker.
- Replace the removed `experimental.dynamicIO` key with top-level `cacheComponents`. Remove the deprecated `experimental.useCache` alias only after the top-level flag owns the behavior.
- Do not require a passing pre-flag build when existing code already uses `"use cache"`; the flag may be required before such code can build. Record that starting condition.

## Choose the rollout

Ask the user in delivery terms:

- First ship a mechanical change that enables Cache Components and opts every route out, then adopt routes feature by feature in follow-up changes.
- Enable the flag and fix every exposed route on one branch.

Do not choose for an interactive user. Without an available user, choose the first option because it is reviewable and reversible, and report the choice.

## Mechanical opt-out milestone

Skip this section for the one-branch strategy.

Before the codemod, use the repository's structural source tooling across every component reachable from App Router layouts to find render-time calls to `new Date()`, `Date.now()`, `Math.random()`, and `crypto.randomUUID()`. These sync-I/O blockers fail even under `instant = false`. Apply the version-matched error-card recipe, normally an `await io()` behind a granular Suspense boundary, and leave this exact marker above the temporary escape hatch:

```tsx
// TODO: Cache Components adoption. Added to unblock the build: remove this io() to re-trigger the error and review the fix options.
```

Structurally find and translate incompatible segment exports before running a codemod. Then verify the transform name and behavior against the installed documentation and executable help. Do not run the upstream canary package from this reference; using an external codemod is a separate, explicitly authorized dependency execution.

The upstream `cache-components-instant-false` transform adds `instant = false` plus a `TODO: Cache Components adoption` marker to server `page`, `layout`, and `default` modules. It skips modules with `"use client"`/`"use server"` and modules that already export `instant`. If the transform is unavailable, reproduce only that bounded behavior structurally; do not use a text replacement.

Set `cacheComponents: true`, then run the app's build. Confirm the root layout has the opt-out because it covers framework routes such as `/_not-found`. Fix synthetic-route errors at the owning layout, not by inventing a synthetic page file.

Stop at this shippable milestone and ask whether to preserve it as its own change before adopting routes. Do not silently continue into behavioral refactors.

## Route adoption loop

Define one feature as a small product surface, not an entire application area. Walk layouts before pages and ancestors before descendants. Under the mechanical strategy, remove one opt-out at a time; under the direct strategy, target the first failing route.

For each route:

1. Run the [runtime verification](runtime-verification.md) preflight. Use `nextjs_call` for current compilation issues, errors, route metadata, and logs, and use the headed browser for the route.
2. Fetch the distinct error's version-matched guide through `nextjs_docs`. Error text is a summary, not the recipe.
3. Fix the narrowest blocking read:
   - Push `cookies()`, `headers()`, `params`, and `searchParams` reads into granular Suspense-wrapped children. Forward `params`/`searchParams` promises and await them inside the child rather than at the page top.
   - Move sync-I/O behind the documented request-time boundary.
   - Remove a file-level `"use cache"` directive when that module reads request data and therefore cannot be a cache scope.
   - Use a cache boundary only when the data is actually shared and the user-approved freshness is explicit.
4. Verify the first paint contains the intended static shell, each fallback resolves, and shared siblings still work. A route returning HTTP 200 or a clean DOM alone is not enough; reconcile the browser with `nextjs_call`.
5. Run the narrow route build when supported, then the full application build before calling the feature complete. Treat build path flags as filesystem route patterns, not URL paths, and verify a non-empty route set was built.

Fix all descendants before calling a layout clean. A descendant opt-out can shadow an ancestor and make a mid-walk build pass without validating the layout's real reads.

## Decision gates

Read [per-page decisions](per-page-decisions.md) before changing an auth gate, selecting caching, accepting an empty shell, or leaving a route blocking. A deliberate `instant = false` may remain, but replace its migration TODO with the user-approved reason.

## Feature closeout

- The live DevTools/runtime loop reports no relevant compilation or runtime errors.
- The browser shows the intended shell, fallbacks, resolved content, and sibling behavior.
- The full `next build` passes.
- Exact-literal lookup finds no unresolved `TODO: Cache Components adoption` marker in the feature.
- Every remaining `instant = false`, `await io()`, or `await connection()` has a deliberate reason rather than a migration placeholder. Prefer `io()` unless a real user request is required.

Report the changed routes in user language, describe what appears immediately and what streams, and provide a short click-through list. For a non-trivial feature, ask whether to ship it before moving to the next feature. A feature that only removed an opt-out with no visible change can continue without a checkpoint.

After all features are adopted, use [Cache Components optimization](cache-components-optimization.md) only when the user wants a larger static shell, and use [Partial Prefetching adoption](partial-prefetching-adoption.md) only as a separate requested phase.
