# Cache Components optimization

Optimize an already-adopted Cache Components application through one of two loops:

- **Page render:** grow the static shell of one route on initial load.
- **In-app navigation:** make the newly mounted part of route B appear immediately when navigating from route A, with real chrome and content-shaped fallbacks rather than holding A's UI.

Run one loop end to end. If the complaint is ambiguous, ask whether it concerns a cold page load or an A-to-B transition.

## Preconditions

- Confirm `cacheComponents: true` and a completed [runtime-verification](runtime-verification.md) preflight.
- Require the user to place the headed browser on the route the loop needs, with authentication and state prepared. Never automate SSO or MFA.
- Anchor the route with the current `agent-browser` URL command returned by `browser_eval`.
- Use `nextjs_call` for the running server's discovered logs and errors tools. Do not call the runtime endpoint directly.

## Shared shell capture

Both loops use the `next-instant-navigation-testing` cookie to freeze dynamic writes so the visible page is the static shell plus Suspense fallbacks. Use a unique pending-lock tuple and the current browser CLI syntax, historically:

```bash
pnpm exec agent-browser cookies set next-instant-navigation-testing '[0,"p<unique-id>"]' --url <origin>
```

For navigation, set the cookie after route A is loaded but before navigating to B. Setting it before a direct load freezes that page rather than the transition under test.

Hide the development overlay before each screenshot and restore it immediately afterward:

```bash
pnpm exec agent-browser eval "document.querySelector('nextjs-portal').style.display='none'"
pnpm exec agent-browser screenshot <path>
pnpm exec agent-browser eval "document.querySelector('nextjs-portal').style.display=''"
```

Capture a baseline before editing. The after capture must visibly improve: a fallback area shrinks, real content moves into the shell, or a target fallback becomes content-shaped. An identical result means the refactor did not land or the environment never resolved the data; distinguish those cases with `nextjs_call` logs before changing more code.

## Shared levers

1. **Push I/O down.** Extract only the I/O-dependent JSX into a granular Suspense-wrapped child and lift static siblings into the shell. When a boundary already wraps mixed static/dynamic content, recurse inside it instead of adding another blind wrapper.
2. **Cache shared data.** Use the installed version's cache directive plus an explicit freshness profile. Ask the user for freshness and map it to a documented profile; never infer a business staleness policy.
3. **Compose them.** Push-down can expose more static structure while caching removes the remaining shared-data gap.

Do not replace granular boundaries with a segment-wide `loading.tsx`, a root Suspense wrapper, or a `fallback={null}` that blanks meaningful content. Those changes can silence a symptom while making the shell experience worse.

## No-shell bailout

If framework logs report a blocking-route/static-generation bailout, or a visibly rendered page has no Suspense boundary to rank, there is no shell to optimize. Report the structural blocker and return to Cache Components adoption. Do not pretend an optimization loop can compensate for unwrapped request-time work.

## Page-render loop

1. With the user on the target route, set the shell cookie and reload.
2. Call the current React Suspense introspection command with dynamic-only structured output. Each candidate should expose its JSX source and blocker/owner stack.
3. Resolve those frames against the development server using the current framework/browser tool contract. Use source frames only to identify the owning boundary; inspect the code before editing.
4. Rank candidates by visible pixel area using the larger of the shell fallback rectangle and the fully rendered subtree rectangle. A tiny spinner can hide a large missing region.
5. If the largest candidate is below the viewport or visually marginal, report that the shell is already healthy and offer a different route instead of forcing a refactor.
6. If one boundary dominates, inspect inside it, enumerate its awaits, and rank the inner regions.
7. Apply one shared lever through a user-visible implementation proposal, then re-run the shell capture. The targeted region must shrink or disappear and the route must still have a valid shell.

## In-app navigation loop

### Preflight

1. Route A is the current route. Ask the user to perform the real interaction to B, then record B's URL and return to A with the current client-side navigation command.
2. Set the shell cookie while on A, then perform the client-side A-to-B navigation. Wait for the DOM to stabilize before React or screenshot capture.

### Diagnose

1. Capture B's dynamic Suspense boundaries after the client navigation.
2. Drop a boundary only when every blocker is an SSR-only client router hook such as `usePathname`, `useSearchParams`, `useRouter`, `useSelectedLayoutSegment(s)`, or `useParams`. These resolve from the router store during client navigation and are not click-to-paint blockers.
3. Keep boundaries with request API, server fetch, or cache blockers. If all remaining regions are below the viewport, the transition is already visually healthy.
4. Resolve owner stacks and select the highest candidate in B's newly mounted route tree, nearest the point where A and B diverge. Shared ancestor layouts remain mounted and are not the target.
5. When no Suspense candidate exists but the navigation still holds A, directly load B with the shell cookie and inspect framework logs for unwrapped async work. Filter to segments after the A/B divergence; a shared-layout bailout belongs to the page-render/adoption workflow.

### Apply

Use the shared push-down/cache levers first. For request-specific content, a third lever may be available in the installed release: a private browser cache plus runtime prefetching. Follow the version-matched docs rather than copying names blindly. In the source workflow this was:

- `"use cache: private"` on the scope that encloses `cookies()`, `headers()`, or URL-data access;
- an explicit `cacheLife({ stale: N })` decision;
- `export const prefetch = 'allow-runtime'` on the narrowest segment that owns the private content.

Putting a private-cache directive only on a downstream fetch helper does not cover a runtime read in its caller. Move the cache scope to the reader or move the read into the helper. Verify at runtime; types and compilation do not prove the correct scope.

Private data must remain session-scoped and must never enter a shared server cache. Runtime prefetching adds server work per prefetchable link, does not improve cold loads, does not make an uncacheable segment cacheable by itself, and does not override a deliberate connection bailout.

### Verify

Repeat the same cookie-locked A-to-B client navigation and screenshot before and after. The after state must show more of B immediately, a content-shaped fallback, or resolved approved private content. Then repeat the dynamic-boundary capture: the target blocker should disappear or its fallback should improve. Re-run the no-shell check and reconcile framework logs.

## Stability rules

- Development does not reproduce production prefetch traffic and first visits may compile routes. Wait for DOM stability across consecutive reads rather than a fixed short delay.
- When a candidate appears inconsistently, repeat the bounded diagnose capture two or three times. Consistent boundaries are evidence; one-off attachment/compile artifacts are noise.
- Do not inspect the development network tab to prove prefetch behavior. Use the cookie-locked navigation for shell quality and a production build/start for actual prefetch traffic.
- Run edits only after a coherent proposal identifies the owning file, blocker, chosen lever, freshness decision, boundary placement, expected visible delta, and verification route.

## Teardown and closeout

Expire only the shell-test cookie; never clear all cookies because that destroys authentication:

```bash
pnpm exec agent-browser cookies set next-instant-navigation-testing x --url <origin> --expires 1
```

Report baseline and after screenshot paths, framework diagnostics, the visible delta, repository checks, and any remaining candidate. If the captures are identical or the data path failed in both, do not claim success.
