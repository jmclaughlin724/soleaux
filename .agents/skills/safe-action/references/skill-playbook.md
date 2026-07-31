# next-safe-action Playbook

Original detailed skill body moved from `.agents/skills/safe-action/SKILL.md` so `SKILL.md` stays focused on trigger, workflow, and closeout. Read only the sections needed for the current task.

# next-safe-action

Portable client, server-action, React hook, and Vitest patterns for `next-safe-action`. Identify the actual installed version and app-local client before applying an example; this checkout does not currently declare a next-safe-action owner. Do not invent a shared `packages/next-safe-action` workspace.

Repository ownership, authentication, authorization, `"use server"` placement, and test expectations come from the complete applicable `AGENTS.md` chain plus the live client and action owners. This playbook supplies API patterns, not repository policy.

---

## Server side

### Quick start

```ts
// src/lib/safe-action.ts
import { createSafeActionClient } from "next-safe-action";

export const actionClient = createSafeActionClient();
```

```ts
// src/app/actions.ts
"use server";

import { z } from "zod";
import { actionClient } from "@/lib/safe-action";

export const greetUser = actionClient
  .inputSchema(z.object({ name: z.string().min(1) }))
  .action(async ({ parsedInput: { name } }) => {
    return { greeting: `Hello, ${name}!` };
  });
```

### Chainable API

```
createSafeActionClient(opts?)
  .use(middleware)             // repeatable
  .metadata(data)              // required if defineMetadataSchema is set
  .inputSchema(schema, utils?) // Standard Schema or async factory
  .bindArgsSchemas([...])      // schemas for .bind() arguments
  .outputSchema(schema)        // validates return value
  .action(serverCodeFn, utils?)      // creates SafeActionFn
  .stateAction(serverCodeFn, utils?) // creates SafeStateActionFn
```

Each method returns a new immutable client instance.

### Entry points

| Entry point | Environment | Key exports |
| --- | --- | --- |
| `next-safe-action` | Server | `createSafeActionClient`, `createMiddleware`, `returnValidationErrors`, error classes |
| `next-safe-action/hooks` | Client | `useAction`, `useOptimisticAction` |
| `next-safe-action/stateful-hooks` | Client | `useStateAction` (deprecated — use React's `useActionState`) |

### Server code function parameters

```ts
.action(async ({ parsedInput, clientInput, bindArgsParsedInputs, ctx, metadata }) => {
  // return data
});
```

`.stateAction()` adds a second arg `{ prevResult }`.

### Middleware

`.use(...)` runs top-to-bottom; results flow bottom-to-top. Context accumulates via `next({ ctx })` with deep-merge.

```ts
const authClient = actionClient.use(async ({ next }) => {
  const session = await getSession();
  if (!session?.user) throw new Error("Unauthorized");
  return next({ ctx: { userId: session.user.id } });
});
```

Middleware receives `{ clientInput, bindArgsClientInputs, ctx, metadata, next }`.

### Validation errors

Two sources: automatic (schema mismatch) and manual (`returnValidationErrors`). Both produce the same client-side structure.

```ts
import { returnValidationErrors } from "next-safe-action";

returnValidationErrors(schema, {
  email: { _errors: ["Already registered"] },
});

// Root-level form errors
returnValidationErrors(schema, {
  _errors: ["Rate limit exceeded"],
});
```

Default shape mirrors the schema with `_errors` arrays. Use `flattenValidationErrors()` for `{ formErrors, fieldErrors }`.

### Server-level callbacks

Second argument to `.action()` accepts server-side callbacks (distinct from hook callbacks):

```ts
.action(serverCodeFn, {
  onSuccess: async ({ data, ctx, metadata }) => { /* ... */ },
  onError: async ({ error, ctx }) => { /* ... */ },
  onSettled: async ({ result }) => { /* always runs */ },
  onNavigation: async ({ navigationKind }) => { /* redirect/notFound */ },
  throwServerError: true,
});
```

### Server-side references

- [Client setup & configuration](./server-client-setup.md)
- [Input & output validation](./server-input-output-validation.md)
- [Server error handling](./server-error-handling.md)
- [Auth & authorization middleware](./server-auth-patterns.md)
- [Logging & monitoring middleware](./server-logging-monitoring.md)
- [Standalone middleware with createMiddleware()](./server-standalone-middleware.md)
- [Bind arguments](./server-bind-arguments.md)
- [Framework errors (redirect, notFound)](./server-framework-errors.md)
- [Metadata schemas](./server-metadata.md)
- [Custom validation errors](./server-custom-errors.md)
- [Error shape formats](./server-error-shapes.md)
- [Type inference utilities](./type-utilities.md)

### Server anti-patterns

```ts
// BAD: Missing "use server" — action won't work
export const myAction = actionClient.action(async () => {});

// BAD: Forgetting to return next() in middleware — action hangs
.use(async ({ next }) => { next({ ctx: {} }); })

// BAD: Catching framework errors (swallows redirect/notFound)
.use(async ({ next }) => {
  try { return await next({ ctx: {} }); }
  catch { return { serverError: "fail" }; } // swallows redirect!
})

// GOOD: Re-throw framework errors
.use(async ({ next }) => {
  try { return await next({ ctx: {} }); }
  catch (error) {
    if (error instanceof Error && "digest" in error) throw error;
    return { serverError: "fail" };
  }
})
```

---

## React hooks & forms

### useAction

```tsx
"use client";
import { useAction } from "next-safe-action/hooks";
import { createUser } from "@/app/actions";

export function CreateUserForm() {
  const { execute, result, isPending } = useAction(createUser, {
    onSuccess: ({ data }) => console.log("Created:", data),
    onError: ({ error }) => console.error(error.serverError),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        execute({ name: new FormData(e.currentTarget).get("name") as string });
      }}
    >
      <input name="name" required />
      <button type="submit" disabled={isPending}>
        {isPending ? "Creating..." : "Create"}
      </button>
      {result.serverError && <p>{result.serverError}</p>}
    </form>
  );
}
```

### useOptimisticAction

```tsx
import { useOptimisticAction } from "next-safe-action/hooks";

const { execute, optimisticState } = useOptimisticAction(toggleTodo, {
  currentState: todo,
  updateFn: (state, input) => ({ ...state, completed: !state.completed }),
});
```

### Hook return shape

| Property | Description |
| --- | --- |
| `execute(input)` | Fire-and-forget execution |
| `executeAsync(input)` | Returns `Promise<Result>` |
| `result` | `{ data?, serverError?, validationErrors? }` |
| `reset()` | Resets all state |
| `status` | `HookActionStatus` string |
| `isIdle` / `isExecuting` / `isPending` | Boolean status flags |
| `hasSucceeded` / `hasErrored` | Outcome flags |

`useOptimisticAction` also returns `optimisticState`.

### React Hook Form adapter

```tsx
import { useHookFormAction } from "@next-safe-action/adapter-react-hook-form/hooks";
import { zodResolver } from "@hookform/resolvers/zod";

const { form, handleSubmitWithAction, action } = useHookFormAction(
  submitContact,
  zodResolver(schema),
  { actionProps: { onSuccess: () => toast.success("Sent!") } }
);
```

`form` is the react-hook-form instance; `action` is the `useAction` return value.

### Hook entry points

| Package | Exports |
| --- | --- |
| `next-safe-action/hooks` | `useAction`, `useOptimisticAction` |
| `@next-safe-action/adapter-react-hook-form/hooks` | `useHookFormAction`, `useHookFormOptimisticAction` |

### React references

- [execute vs executeAsync, result handling](./react-use-action.md)
- [Optimistic updates](./react-optimistic-updates.md)
- [Status lifecycle and callbacks](./react-status-callbacks.md)
- [Native form patterns](./react-form-actions.md)
- [React Hook Form adapter](./react-hook-form.md)
- [File uploads](./react-file-uploads.md)

### React anti-patterns

```tsx
// BAD: bare executeAsync — unhandled throw on redirect/notFound
const result = await executeAsync({ id });
doSomething(result.data);

// GOOD: try/catch lets you handle the result AND propagate navigation
try {
  const result = await executeAsync({ id });
  if (result.data) router.push(`/items/${result.data.id}`);
  if (result.serverError) toast.error(result.serverError);
} catch (e) {
  throw e; // Navigation errors must propagate to Next.js
}

// BETTER: use execute() with callbacks when you don't need the result inline
const { execute } = useAction(myAction, {
  onSuccess: ({ data }) => router.push(`/items/${data.id}`),
  onError: ({ error }) => toast.error(error.serverError),
});
```

---

## Testing

TDD: write failing tests first, then implement the action.

### What to test

| Surface | Key assertions |
| --- | --- |
| **Action (happy path)** | `result.data` defined, `serverError` and `validationErrors` undefined |
| **Validation errors** | `result.validationErrors?.field?._errors` contains expected messages |
| **Server errors** | `result.serverError` is a string from `handleServerError` |
| **Bind args** | Invalid bind args throw `ActionBindArgsValidationError` |
| **Middleware** | Auth context passed through; unauthenticated returns `serverError` |
| **Hooks** | `isIdle` / `isExecuting` / `hasSucceeded` / `hasErrored` lifecycle |
| **Framework errors** | `redirect` / `notFound` rethrow with the correct digest format |

### Repo model

- Keep safe-action clients app-local. Do not assume a shared `packages/next-safe-action` workspace.
- Test the action or hook where it lives.
- Derive auth and middleware behavior from the scoped app's live exported client; this checkout does not currently declare a canonical next-safe-action client.

### Quick: testing an action

```ts
import { describe, expect, it } from "vitest";

import { createUser } from "@/app/actions";

describe("createUser", () => {
  it("returns user data on valid input", async () => {
    const result = await createUser({
      email: "alice@example.com",
      name: "Alice",
    });

    expect(result.data).toEqual({ id: expect.any(String), name: "Alice" });
    expect(result.serverError).toBeUndefined();
    expect(result.validationErrors).toBeUndefined();
  });

  it("returns validation errors on invalid input", async () => {
    const result = await createUser({ email: "not-an-email", name: "" });

    expect(result.validationErrors?.email?._errors).toContain("Invalid email");
  });
});
```

### Quick: testing middleware

Mock the app-local auth/session layer:

```ts
vi.mock("@/lib/auth/portal-session", () => ({
  getPortalSession: vi.fn(),
}));

import { getPortalSession } from "@/lib/auth/portal-session";

vi.mocked(getPortalSession).mockResolvedValue({ user: { id: "user-1" } });
vi.mocked(getPortalSession).mockResolvedValue(null);
```

### Quick: testing hooks

```tsx
import { act, renderHook, waitFor } from "@testing-library/react";
import { useAction } from "next-safe-action/hooks";

const mockAction = vi.fn().mockResolvedValue({ data: { id: "1" } });
const { result } = renderHook(() => useAction(mockAction));

act(() => {
  result.current.execute({ name: "Alice" });
});

await waitFor(() => {
  expect(result.current.hasSucceeded).toBe(true);
});
```

### Running tests

```bash
pnpm --filter portal test
pnpm --filter portal exec vitest run app/[locale]/(public)/auth/_lib/portal-sign-in-action.test.ts
```

Use the owning workspace test script. Prefer targeted Vitest runs for the action or hook under test.

### Testing references

- [Vitest patterns for actions, bind args, middleware, hooks, framework errors](./testing-patterns.md)
- [Validation error testing, returnValidationErrors, error utilities](./testing-validation.md)
- [Test file placement for app-local safe-action tests](./testing-organization.md)
