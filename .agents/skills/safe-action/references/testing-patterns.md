# Test Patterns

Vitest patterns for testing next-safe-action server actions, middleware, and hooks.

## Testing Actions Directly

Server actions are async functions -- call them directly in tests:

```ts
// src/__tests__/actions.test.ts
import { describe, it, expect, vi } from "vitest";
import { createUser } from "@/app/actions";

describe("createUser", () => {
  it("returns user data on valid input", async () => {
    const result = await createUser({
      name: "Alice",
      email: "alice@example.com",
    });

    expect(result.data).toEqual({
      id: expect.any(String),
      name: "Alice",
    });
    expect(result.serverError).toBeUndefined();
    expect(result.validationErrors).toBeUndefined();
  });
});
```

## Testing Actions with Bind Args

```ts
import { updatePost } from "@/app/actions";

describe("updatePost", () => {
  it("updates the post", async () => {
    const postId = "123e4567-e89b-12d3-a456-426614174000";
    const boundAction = updatePost.bind(null, postId);

    const result = await boundAction({
      title: "Updated Title",
      content: "Updated content",
    });

    expect(result.data).toEqual({ success: true });
  });

  it("returns validation error for invalid postId", async () => {
    const boundAction = updatePost.bind(null, "not-a-uuid");

    // Bind args validation errors throw ActionBindArgsValidationError
    await expect(
      boundAction({ title: "Test", content: "Test" })
    ).rejects.toThrow();
  });
});
```

## Testing Middleware

Test middleware behavior by creating actions with specific middleware chains:

```ts
import { describe, it, expect, vi } from "vitest";
import { createSafeActionClient } from "next-safe-action";
import { z } from "zod";

// Mock auth
vi.mock("@/lib/auth", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/auth";

const authClient = createSafeActionClient().use(async ({ next }) => {
  const session = await getSession();
  if (!session?.user) throw new Error("Unauthorized");
  return next({ ctx: { userId: session.user.id } });
});

const testAction = authClient.action(async ({ ctx }) => {
  return { userId: ctx.userId };
});

describe("auth middleware", () => {
  it("passes userId to action when authenticated", async () => {
    vi.mocked(getSession).mockResolvedValue({
      user: { id: "user-1", role: "user" },
    });

    const result = await testAction();
    expect(result.data).toEqual({ userId: "user-1" });
  });

  it("returns server error when unauthenticated", async () => {
    vi.mocked(getSession).mockResolvedValue(null);

    const result = await testAction();
    expect(result.serverError).toBeDefined();
  });
});
```

## Testing Hooks with React Testing Library

Use `renderHook` from React Testing Library:

```tsx
import { describe, it, expect, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useAction } from "next-safe-action/hooks";

const mockAction = vi.fn();

describe("useAction", () => {
  it("starts idle", () => {
    const { result } = renderHook(() => useAction(mockAction));

    expect(result.current.isIdle).toBe(true);
    expect(result.current.isExecuting).toBe(false);
    expect(result.current.result).toEqual({});
  });

  it("executes and returns data", async () => {
    mockAction.mockResolvedValue({ data: { id: "1" } });

    const { result } = renderHook(() =>
      useAction(mockAction, {
        onSuccess: vi.fn(),
      })
    );

    act(() => {
      result.current.execute({ name: "Alice" });
    });

    await waitFor(() => {
      expect(result.current.hasSucceeded).toBe(true);
    });

    expect(result.current.result.data).toEqual({ id: "1" });
  });

  it("handles server errors", async () => {
    mockAction.mockResolvedValue({ serverError: "Something went wrong" });

    const onError = vi.fn();
    const { result } = renderHook(() => useAction(mockAction, { onError }));

    act(() => {
      result.current.execute({});
    });

    await waitFor(() => {
      expect(result.current.hasErrored).toBe(true);
    });

    expect(result.current.result.serverError).toBe("Something went wrong");
    expect(onError).toHaveBeenCalled();
  });

  it("resets state", async () => {
    mockAction.mockResolvedValue({ data: { id: "1" } });

    const { result } = renderHook(() => useAction(mockAction));

    act(() => {
      result.current.execute({});
    });

    await waitFor(() => {
      expect(result.current.hasSucceeded).toBe(true);
    });

    act(() => {
      result.current.reset();
    });

    expect(result.current.isIdle).toBe(true);
    expect(result.current.result).toEqual({});
  });
});
```

## Hoisting mock fns referenced inside `vi.mock()`

Every `vi.mock()` factory runs at module-load time before any top-level `const` declarations execute. A mock fn declared at module scope and referenced inside a `vi.mock()` factory throws `ReferenceError: Cannot access 'mockX' before initialization`.

Anti-pattern:

```ts
const mockGetSession = vi.fn(); // ← evaluated AFTER the vi.mock factory runs

vi.mock("@/lib/auth", () => ({
  getSession: mockGetSession, // ← ReferenceError at test load
}));
```

Correct: declare every mock fn referenced inside a `vi.mock()` factory inside a single `vi.hoisted()` block. Vitest hoists `vi.hoisted()` calls above every `vi.mock()` factory at parse time.

```ts
const { mockGetSession, mockRedirect } = vi.hoisted(() => ({
  mockGetSession: vi.fn(),
  mockRedirect: vi.fn(() => {
    throw Object.assign(new Error("NEXT_REDIRECT"), {
      digest: "NEXT_REDIRECT",
    });
  }),
}));

vi.mock("@/lib/auth", () => ({ getSession: mockGetSession }));
vi.mock("next/navigation", () => ({ redirect: mockRedirect }));
```

When a test fails with `Cannot access 'mockX' before initialization`, the fix is always the same: move that `mockX` into the same `vi.hoisted()` block as the others. Do not split mocks across multiple `vi.hoisted()` calls — one block per test file.

## Mocking Framework Errors

```ts
import { vi } from "vitest";

// Mock Next.js navigation
vi.mock("next/navigation", () => ({
  // Digest formats are Next.js internals -- may change across versions
  redirect: vi.fn((url: string) => {
    throw Object.assign(new Error("NEXT_REDIRECT"), {
      digest: `NEXT_REDIRECT;push;${url};303;`,
    });
  }),
  notFound: vi.fn(() => {
    throw Object.assign(new Error("NEXT_NOT_FOUND"), {
      digest: "NEXT_HTTP_ERROR_FALLBACK;404",
    });
  }),
}));
```
