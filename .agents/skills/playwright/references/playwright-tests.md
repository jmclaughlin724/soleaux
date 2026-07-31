# Running Playwright Tests

Run Playwright tests through the repository-owned `test:e2e` script so the pinned runner and configured task boundary are used. The script already sets `PLAYWRIGHT_HTML_OPEN=never`.

```bash
# Run the configured suite
pnpm test:e2e

# Run one file or focused arguments through the same owner
pnpm test:e2e -- path/to/test.spec.ts
```

# Debugging Playwright Tests

Reproduce the failure with the narrowest configured test command and inspect the runner's trace, screenshot, video, console, and request artifacts. Use the MCP registered as `mcp_servers."playwright"` for interactive reproduction and page inspection against the same managed application boundary.

```bash
# Run one failing test with a trace when the owning configuration does not already retain one
pnpm test:e2e -- path/to/test.spec.ts --trace on
```

Treat the MCP browser as a separate diagnostic session unless live tool output proves it shares the runner's context. Recreate only the required setup through the test-owned fixtures, storage state, and managed `webServer`; do not bypass authentication or silently point the MCP at an unrelated manual server. Use the MCP's live snapshots and inspection tools to identify the failing user-visible contract, then encode the durable locator or assertion in the test.

After fixing the test, rerun the narrow test command and confirm it passes.
