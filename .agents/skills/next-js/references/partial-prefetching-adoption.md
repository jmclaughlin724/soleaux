# Partial Prefetching adoption

Enable Partial Prefetching only after Cache Components adoption is complete. The version-matched guide returned by `nextjs_docs` owns API semantics and per-insight recipes; this reference owns the rollout and verification sequence.

## Preconditions

- Confirm `cacheComponents: true` and no unresolved blocking-route errors. Those errors replace the prefetch insights and hide this workflow's signal.
- Confirm the installed release documents `partialPrefetching`, the relevant `prefetch` route config values, and the dev insights. The source workflow requires Next.js 16.3+. If the installed docs do not expose them, stop and separate the framework upgrade.
- Complete [runtime verification](runtime-verification.md). This workflow has no build-only fallback: link prefetch and shell validation happen in `next dev` during browser navigation.

Tell the user what changes in product terms: links fetch a shared App Shell, and an explicit full prefetch no longer implies the same payload as before. Keep insight slugs and internal step names out of user-facing status unless they help diagnose a blocker.

## Audit explicit full-prefetch links before enabling

If `partialPrefetching: true` is already set, skip to the runtime sweep. Otherwise keep the global flag off while auditing, because enabling it first can silence the signal that identifies unadopted destinations.

Ask whether to ship the audit on one branch or destination by destination. When there are only a few links, one branch is a reasonable default; for a broad shared-link surface, prefer review-sized destination changes and report the choice.

Use TypeScript/JSX AST tooling across the whole source tree, including shared components and packages, to enumerate `next/link` elements whose `prefetch` prop is `true` or bare. Exclude false or non-true values. Resolve custom wrappers structurally: find their `next/link` import and trace the forwarded/defaulted prop to call sites. Do not use a text or regex scan for this code search.

For every audited link:

1. Navigate through it in the headed development browser. The validation signal fires on navigation, not merely when a link enters the viewport.
2. Adopt the destination with the version-documented temporary route export, historically `export const prefetch = 'partial'`. One destination export clears all links that target it.
3. If the route reads `params` or `searchParams`, preserve the full-prefetch intent and mark the later product decision:

   ```tsx
   // TODO(runtime-prefetch): assess with the user (prefetch = 'allow-runtime')
   export const prefetch = "partial";
   ```

4. Read the version-matched audit table through `nextjs_docs` and preserve what the old prefetch delivered. Ask the user before caching previously uncached content or selecting freshness. Defer URL-dependent runtime prefetch decisions to the optional phase below.

## Enable the global flag

After every explicit full-prefetch destination is audited:

1. Set `partialPrefetching: true` beside `cacheComponents: true`.
2. Use the release-documented first-party `remove-partial-prefetch` codemod only when external codemod execution is explicitly authorized. Verify the installed documentation and executable help first; do not run the upstream canary package from this reference.

   Preserve every other `prefetch` value and each `TODO(runtime-prefetch)` marker. If the codemod is unavailable, remove only the exact export through structured source edits.

3. Restart the development server after changing configuration and call `nextjs_index` again so the MCP view targets the new process and tool surface.

## Runtime sweep

Build a concrete route queue from `nextjs_call` route discovery. Navigate every reachable route in `next dev`, using framework logs/errors and the dev overlay together. The expected clean route may show no Insights tab at all.

For each distinct insight:

1. Fetch its version-matched documentation through `nextjs_docs`.
2. Fix URL data reads so the shared shell is independent of a specific `params` or `searchParams` value. Keep Suspense boundaries close to the dependent region; wrapping the whole page can pass validation while producing an empty shell.
3. Treat runtime-data or uncached-data errors as unfinished Cache Components adoption and return to that workflow rather than relabeling them as prefetch issues.
4. Batch rare product decisions: a route whose whole visible surface is URL-dependent, a route that may deliberately remain blocking, or data whose cacheability/freshness is unclear.

An empty sweep can be correct after thorough Cache Components adoption. Prove the pipeline is live by confirming the flag, version, restarted server, route navigation, and DevTools calls. If still uncertain, introduce and immediately revert one bounded URL read outside Suspense, observe the expected validation signal, and leave no probe change behind.

## Adoption closeout

- Re-run the framework compilation/error/log tools through `nextjs_call`.
- In the browser, confirm each changed destination shows a meaningful shared shell and every URL-specific fallback resolves.
- Run the full build.
- Run a production server and exercise at least one representative link because development does not reproduce production prefetch traffic.
- Report which links and destinations changed, what appears immediately, what streams, and any deferred runtime-prefetch decisions. Keep adoption and optional runtime prefetching as separate review units.

## Optional runtime prefetching

Use exact-literal lookup for `TODO(runtime-prefetch)` and review every candidate with the user. The decision is whether URL- or session-specific content should be fetched while the link is visible or may stream after the click. Include the server-invocation cost and freshness/security implications.

For approved candidates, follow the installed runtime-prefetching guide: use the documented route config, historically `prefetch = 'allow-runtime'`, and put the documented cache directive on the scope that actually reads the runtime value. A helper cache does not cover a `cookies()` read that still occurs in its caller. For per-user data, use only the version-documented private cache model; never place personalized results in a shared server cache.

For rejected candidates, remove the marker and keep the default. No `TODO(runtime-prefetch)` marker may survive the decision pass. Verify approved routes against a production build/start and a live browser link transition.
