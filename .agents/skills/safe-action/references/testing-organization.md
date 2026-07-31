# Test File Organization

Keep next-safe-action tests beside the app-local client, action, or caller they exercise. This checkout does not currently declare a canonical next-safe-action owner, so treat the following layout as a portable shape rather than a live path.

```text
apps/<owner>/lib/
  safe-action.ts
  safe-action.test.ts

apps/<owner>/<feature>/
  actions.ts
  actions.test.ts
```

## Placement

- Put client and middleware tests near the app-local safe-action client or auth helpers they exercise.
- Put server-action tests beside the owning route or module, usually as `actions.test.ts` or `<action-name>.test.ts`.
- Keep hook tests near the consuming client component when the behavior is UI-specific.
- Reuse the owning workspace's established test setup and imports.

## Running Tests

Discover the live workspace name and test script before running commands:

```bash
pnpm --filter <workspace> test
pnpm --filter <workspace> exec vitest run <test-file>
```

Do not invent a `test` script when the owning manifest uses another command.
