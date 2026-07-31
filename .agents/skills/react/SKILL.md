---
name: react
description: Build, optimize, compose, animate, or review React interfaces.
---

# React

## Contract

Use this skill to deliver or review React interfaces across rendering boundaries, performance, component APIs, accessible interaction, and meaningful motion. Start from the exact component, route, failure, or review scope and load only the matching rule or reference below. In change mode, finish with focused static and runtime evidence; in review mode, report findings without editing.

For Next.js work, also follow the `next-js` skill and the relevant installed guide under `node_modules/next/dist/docs/`. Installed documentation and local owner contracts override generic or upstream examples.

## Use When

- Building or reviewing React components, hooks, context, state, Suspense, hydration, or Server and Client Component boundaries.
- Designing compound, controlled, polymorphic, typed, semantic, or accessible component APIs.
- Fixing waterfalls, render churn, bundle cost, client I/O, or browser rendering work.
- Adding React view transitions, shared-element motion, route animation, or Suspense reveals.
- Auditing React UI files against the current Vercel Web Interface Guidelines.

## Direct Workflow

1. Identify the owner, exact consumers, current React and framework versions, applicable instructions, focused tests, and evidence that will prove the requested outcome. Invoke `$ast-grep` for bounded imports, JSX, hooks, directives, component declarations, and call-shape discovery.
2. For Next.js, read the relevant installed documentation before choosing an API, flag, route convention, or cache behavior.
3. Select the narrowest route from the Detail Index. Read only the rule or reference needed for the active decision; do not load the whole tree by default.
4. Preserve semantic HTML, keyboard and focus behavior, accessible names and relationships, controlled or uncontrolled state contracts, and public component compatibility.
5. Keep request-bound and heavy work on the server unless interaction or a browser API requires a client boundary. Start independent async work early and await it only where correctness needs it.
6. Prefer explicit composition and variants over boolean modes. Add context only when multiple descendants need a stable shared contract.
7. For view transitions, audit navigation and Suspense paths first, communicate a real spatial or continuity relationship, and always provide reduced-motion behavior.
8. For a UI-guideline review, follow [web-interface-review.md](references/web-interface-review.md), fetch the living source, and keep the task read-only unless the user asks for fixes.
9. Verify with the owning workspace's focused tests, lint, and typecheck. Render and inspect changed UI behavior with the registered Next.js or browser tooling.

## Detail Index

- Async, bundle, server, client, rerender, rendering, or JavaScript performance: the matching `rules/async-*`, `bundle-*`, `server-*`, `client-*`, `rerender-*`, `rendering-*`, or `js-*` file
- Component architecture, variants, providers, or React 19 API choices: the matching `rules/architecture-*`, `patterns-*`, `state-*`, or `react19-*` file
- Effect events, stable callbacks, refs, or one-time initialization: the matching `rules/advanced-*` file
- Component file conventions or TypeScript component contracts: the matching `rules/structure-*` or `rules/ts-*` file
- Compound components and public API structure: [composition.md](references/composition/composition.md)
- Semantics, keyboard, focus, ARIA, and live regions: [accessibility.md](references/composition/accessibility.md)
- Controlled and uncontrolled state: [state.md](references/composition/state.md)
- `asChild`, polymorphism, refs, or TypeScript contracts: [as-child.md](references/composition/as-child.md), [polymorphism.md](references/composition/polymorphism.md), and [types.md](references/composition/types.md)
- Data attributes, styling, or token composition: the matching file under `references/composition/`
- React view-transition implementation: [implementation.md](references/view-transitions/implementation.md), then only the matching [patterns](references/view-transitions/patterns.md), [CSS recipes](references/view-transitions/css-recipes.md), or [Next.js](references/view-transitions/nextjs.md) detail
- Current web-interface compliance review: [web-interface-review.md](references/web-interface-review.md)
- Imported-source provenance and update boundaries: [upstream-sources.md](references/upstream-sources.md)

## Boundaries

- Do not add memoization without measured or established value.
- Do not move app-specific routing, auth, data access, or state machines into shared UI packages.
- Do not introduce client-side data infrastructure for ordinary server-owned initial reads.
- Do not replace native semantics with ARIA or add context merely to avoid a few props.
- Do not add motion without an articulated relationship, interruptible behavior, and a `prefers-reduced-motion` path.
- Do not apply Vercel-specific copy preferences as universal product requirements.
- Do not claim current Web Interface Guidelines compliance from a stale local checklist.
