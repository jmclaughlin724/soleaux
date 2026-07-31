# Runtime verification

Use this loop after every Next.js application-code change. The `next-devtools` MCP server is the framework-side owner; `agent-browser` supplies the browser-side view.

## Requirements

- Call `nextjs_docs` before any other Next.js action and read the returned installed-version guide.
- Use `nextjs_index` to discover the running development server and its actual tools. Next.js 16+ exposes the runtime MCP endpoint, but individual tools and their requirements vary by version.
- Require the tool needed to prove the task. For the complete compile/runtime loop inherited from the consolidated skills, require Next.js 16.3+, Turbopack, `get_compilation_issues`, and `compile_route` when the workflow targets a concrete route.
- Require the repository-pinned `agent-browser >= 0.31.1` for React introspection, worktree-scoped sessions, idempotent restore, and launch-flag reconciliation. Resolve it with `pnpm exec`.

If a required capability is missing, report the exact version, bundler, server, or tool gap. Do not fall back to direct `/_next/mcp` requests or weaker static evidence.

## Preflight

1. Call `nextjs_index` without a port. In a multi-app run, compare the returned servers with the ports owned by the application manifests and call `nextjs_index` explicitly for each omitted manifest port before treating that app as unavailable. If it finds no server, use the repository-owned development command when starting it is in scope, read the port from its banner, and retry `nextjs_index` with that port. Ask for the port only when the server is external or cannot be discovered from repository configuration.
2. From the returned schemas, call the available equivalents of project metadata, routes, and compilation issues through `nextjs_call`. When a concrete route is in scope, call `compile_route` with exactly one schema-supported `routeSpecifier` or `path`. Omit `args` when a tool takes no arguments.
3. Treat a `Turbopack project is not available` result as a bundler mismatch. For workflows that require Turbopack diagnostics, stop and explain the mismatch instead of silently weakening the loop.
4. Call `browser_eval` with the intended user-visible verification task. Follow its current setup instructions, then run `pnpm exec agent-browser skills get core` once for the installed CLI's command contract.
5. Derive one stable browser session for the worktree and reuse it:

   ```bash
   SESSION="$(pnpm exec agent-browser session id --scope worktree --prefix next-js)"
   export AGENT_BROWSER_SESSION="$SESSION"
   export AGENT_BROWSER_RESTORE="$SESSION"
   pnpm exec agent-browser --session "$SESSION" --restore --headed --enable react-devtools open <url>
   ```

   The browser is user-visible. If restored authentication is missing or expired, pause for the user to complete login; never automate MFA or request credentials.

## Before editing

Ask the running app for the route map and current route metadata through `nextjs_call`. Use those results as the first worklist, then confirm the relevant files with AST or language-server tooling. Static repository discovery is a fallback when the live runtime cannot identify a consumer, not a replacement for the runtime query.

## After editing

Verify four independent failure modes:

1. **Compilation:** call `compile_route` for each changed routable consumer, then call the project-wide compilation-status tool through `nextjs_call`.
2. **Framework runtime:** call the discovered errors and logs tools through `nextjs_call` after at least one browser navigation.
3. **Visible behavior:** use the scoped browser session to exercise the exact route and interaction. Assert the intended content, state transition, loading state, and navigation result.
4. **React behavior:** when boundaries, client/server ownership, Suspense, or renders matter, use React DevTools through the current `agent-browser` commands. DOM assertions alone do not prove these properties.

Use only tool names and input schemas returned by `nextjs_index` and commands returned by `pnpm exec agent-browser skills get core`. Re-run runtime and React introspection after every navigation because both are route-state dependent.

## Reconciliation and recovery

- If the browser disagrees with `nextjs_call`, first confirm the browser URL, session id, restore key, and server port. Treat stale or misdirected tooling as the leading hypothesis until the two views target the same route.
- After a click or navigation, use the current browser guide's network-idle or DOM-stability wait, then re-read the page. Do not guess a URL matcher.
- A blank read, `about:blank`, empty snapshot, or missing session immediately after open/navigation usually means stale browser state. Reopen the same scoped session; if needed, close it and reopen once before diagnosing the route.
- If a diagnostic result is inconsistent, repeat the same bounded capture two or three times and compare framework logs, browser state, and React output. One-off attachment or compilation races are not application proof.
- If a task-owned `next dev` process emits Watchpack `EMFILE` errors or repeatedly reports that its `.next/dev` directory was deleted while running inside a filesystem sandbox, stop it and repeat the same manifest-owned entrypoint outside that sandbox before changing source or Next.js configuration. When the unsandboxed process and a minimal Watchpack probe are stable, classify the failure as environment or permission related; do not compensate with watcher-limit, Turbopack-root, or application changes.

## Closeout

Run the narrow repository-owned build, type, lint, or test check that exercises the changed behavior after the live loop. Report:

- the `next-devtools` calls and server/route exercised;
- the browser interaction and observed result;
- the focused repository checks;
- any behavior not verified because of authentication, version, bundler, or environment limits.

Close the browser with the same session and restore context so its state is saved:

```bash
pnpm exec agent-browser --session "$SESSION" --restore close
```

Leave a user-started development server running. Stop only a server started solely for this task when no follow-up loop needs it.
