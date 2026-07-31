---
name: playwright
description: Automate browsers and author or debug Playwright tests.
---

# Playwright

## Contract

Use this skill for browser automation and Playwright tests. Use the MCP registered as `mcp_servers."playwright"` for interactive browser automation, rendered-page inspection, and live debugging. Use the repository-owned `@playwright/test` command for durable test authoring and execution. A separately installed `playwright-cli` binary is outside the repository contract: inspect its live version and help only when the user explicitly puts that binary in scope.

## Use When

- Automating a browser flow or inspecting a rendered page.
- Authoring, debugging, or running Playwright end-to-end tests.
- Diagnosing locators, storage state, requests, traces, video, or test-server behavior.

## Direct Workflow

1. Read `playwright.config.ts`, the owning test, its app or route owner, and the managed `webServer` configuration.
2. Use the registered `playwright` MCP and its live tool schemas for interactive inspection, browser actions, screenshots, console or network diagnosis, and reproducing a failing flow. Do not invent MCP tool names or route this work through a `playwright-test` alias.
3. Use the installed test runner when the outcome must be a committed or repeatable automated test. Use MCP inspection to understand or reproduce the behavior, then encode the durable assertion in the owning test and rerun it through the configured project.
4. Read [playwright-tests.md](references/playwright-tests.md) for durable tests. If an explicit task requires the separate `playwright-cli` binary, run `playwright-cli --version` and the relevant `--help`; stop on a version mismatch instead of using copied command examples or installing another version.
5. Use accessible roles, labels, and stable user-visible contracts for locators.
6. Preserve managed server and environment setup; do not silently reuse an unrelated manual dev server.
7. Run the narrowest test project or file, inspect traces and artifacts on failure, and report the exact flow verified.

## Detail Index

- Interactive browser automation and inspection: registered `playwright` MCP live tool surface.
- Test execution, traces, storage, requests, and debugging: [playwright-tests.md](references/playwright-tests.md).
- Explicit external `playwright-cli` task: the installed binary's `--version` and `--help` output.

## Boundaries

- Keep transient findings, incident notes, and task-local state out of `SKILL.md`.
- Put bulky examples, provider variants, API specifics, and edge cases in `references/**`.
- Add scripts only for deterministic repeat work that is safer to run than to retype.
- Use the exact registered MCP identity `playwright`; do not substitute `playwright-test` or an unregistered alias.
- Do not install or upgrade the separate `playwright-cli` binary; use it only when the user explicitly puts the installed binary in scope.
