---
name: next-js
description: Build and debug Next.js apps with DevTools, Cache Components, and prefetching.
---

# Next.js

## Contract

Build, diagnose, migrate, optimize, and verify Next.js applications against the installed framework version and a live runtime. Always use the `next-devtools` MCP server on every invocation: call `nextjs_docs` first, call `nextjs_index` before inspecting or changing a running application, use `nextjs_call` for framework diagnostics, and call `browser_eval` before browser automation. Treat returned tool names and schemas as authoritative; do not guess them or call `/_next/mcp` directly.

If `next-devtools` is unavailable, stop and report that exact blocker. Do not replace it with model memory, generic web results, direct endpoint calls, or build-only evidence. Load only the reference for the selected workflow, read the complete applicable `AGENTS.md` chain, and read the version-matched guide returned by `nextjs_docs` before writing Next.js code.

Close a change only after the relevant live framework checks, browser behavior, and the narrowest repository-owned validation agree. For a docs-only answer, the version-matched documentation is the closeout evidence.

## Use When

- Implementing, debugging, refactoring, or reviewing a Next.js application.
- Verifying a Next.js change in `next dev` rather than relying only on compilation.
- Enabling or migrating Cache Components.
- Improving a Cache Components page shell or in-app navigation.
- Enabling Partial Prefetching or auditing route prefetch behavior.

## Direct Workflow

1. Call `nextjs_docs` with the project path and exact task. Read the returned installed-version documentation. Treat a returned declared version such as `catalog:` or `workspace:` as package-manager indirection, not an upgrade signal: resolve the consumer's installed `next` package through its workspace, read that package's `dist/docs/`, and report the MCP detection limitation. Never run an upgrade solely because `nextjs_docs` echoed a dependency protocol. If a requested API is absent from the resolved installed docs, verify the version requirement and stop or propose the explicit upgrade separately.
2. Classify the task as general implementation/runtime verification, Cache Components adoption, Cache Components optimization, or Partial Prefetching adoption.
3. For application code or runtime behavior, call `nextjs_index` before static discovery or edits. When a server is found, inspect its tool schemas and use `nextjs_call` for the narrowest relevant route, metadata, compilation, error, or log query.
4. After live runtime evidence, invoke `$ast-grep` for bounded static imports, calls, JSX, directives, exports, and source literals.
5. Load and follow the matching reference below. Use [runtime verification](references/runtime-verification.md) for every code-changing workflow in addition to the selected feature reference.
6. Use `browser_eval` before driving the browser. Verify the user-visible behavior and reconcile it with `nextjs_call`; neither view is sufficient alone for runtime-sensitive work.
7. Run the narrow repository checks owned by the changed boundary, review the diff, and report the runtime evidence, browser evidence, checks, and any version or environment limit.

## Detail Index

- [Runtime verification](references/runtime-verification.md): mandatory live edit/verify loop, DevTools discovery, browser session, diagnostics, and teardown.
- [Cache Components adoption](references/cache-components-adoption.md): enable the flag, choose a rollout, resolve blocking routes, and verify each feature.
- [Cache Components optimization](references/cache-components-optimization.md): grow a page shell or improve A-to-B navigation with visible before/after proof.
- [Partial Prefetching adoption](references/partial-prefetching-adoption.md): audit links, enable the flag, sweep runtime insights, and evaluate runtime prefetching.
- [Per-page decisions](references/per-page-decisions.md): product, security, and deliberate blocking decisions that require user input.

## Boundaries

- Never bypass the `next-devtools` dependency or claim runtime behavior from static prose alone.
- Never apply a Next.js API or codemod until the installed docs and executable help confirm it for the target version.
- Never treat a passing build as browser proof when streaming, navigation, prefetching, or Suspense behavior is in scope.
- Never move an auth gate, cache personalized data, select freshness, or accept a deliberate route opt-out without the product or security decision the change requires.
- Preserve unrelated work, public behavior, and user-authored comments. Do not hide diagnostics, weaken validation, or leave migration TODOs undocumented.
