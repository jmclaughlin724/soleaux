# execute vs executeAsync

## execute — Fire and Forget

`execute(input)` triggers the action and updates hook state reactively. It does not return a value — use callbacks or `result` to react to the outcome.

```tsx
const { execute, result, isPending } = useAction(myAction, {
  onSuccess: ({ data }) => {
    toast.success("Done!");
    router.refresh();
  },
  onError: ({ error }) => {
    toast.error(error.serverError ?? "Something went wrong");
  },
});

// Trigger
<button onClick={() => execute({ name: "Alice" })}>Submit</button>;
```

Internally, `execute` runs inside `React.startTransition` with a `setTimeout(0)` for deferred state updates.

## executeAsync — Awaitable Result

`executeAsync(input)` returns a `Promise<SafeActionResult>`. Useful when you need the result inline (e.g., in a multi-step flow).

```tsx
const { executeAsync, isPending } = useAction(myAction);

const handleSubmit = async () => {
  const result = await executeAsync({ name: "Alice" });

  if (result.data) {
    // Navigate, show toast, etc.
    router.push(`/users/${result.data.id}`);
  }
  if (result.serverError) {
    toast.error(result.serverError);
  }
};
```

**Important:** If the action triggers a navigation error (redirect, notFound, etc.), `executeAsync` will **throw**. Always wrap in try/catch if your action might navigate:

```tsx
const handleSubmit = async () => {
  try {
    const result = await executeAsync(input);
    // Handle result...
  } catch (e) {
    // Navigation errors propagate to Next.js — rethrow them
    throw e;
  }
};
```

## Result Object

```ts
const { result } = useAction(myAction);

// result.data         — action return value (typed)
// result.serverError  — error from handleServerError (typed)
// result.validationErrors — input validation errors (typed)
```

The result is `{}` (empty object) initially and after `reset()`.

## Sequential Actions

Use `executeAsync` for dependent calls:

```tsx
const uploadAction = useAction(uploadFile);
const createPostAction = useAction(createPost);

const handlePublish = async () => {
  const uploadResult = await uploadAction.executeAsync({ file });
  if (!uploadResult.data) return;

  const postResult = await createPostAction.executeAsync({
    title,
    imageUrl: uploadResult.data.url,
  });
  if (postResult.data) {
    router.push(`/posts/${postResult.data.id}`);
  }
};
```

## reset()

Resets the hook to its initial state — clears result, input, status, and sets `isIdle` to `true`.

```tsx
const { execute, result, reset } = useAction(myAction);

// After showing a success message
useEffect(() => {
  if (result.data) {
    const timer = setTimeout(reset, 3000);
    return () => clearTimeout(timer);
  }
}, [result.data, reset]);
```

## Polymorphic action prop (delete-button pattern)

A reusable component that accepts any safe-action variant taking the same input shape (e.g. a delete button that works against `deleteMessageTemplateAction`, `deleteChecklistTemplateAction`, `deleteDocumentTemplateBundleAction`) cannot share one precise generic. Each surface produces a structurally equivalent but nominally distinct `HookSafeActionFn<...>` and `useAction`'s first parameter is invariant in the input position. Type the prop as `Parameters<typeof useAction>[0]` (or `unknown` plus an internal cast), accept the action argument loosely, and at the call site cast `execute({ id } as never)` to satisfy the inferred input shape:

```tsx
type SafeActionLike = Parameters<typeof useAction>[0];

interface DeleteButtonProps {
  readonly deleteAction?: unknown;
  readonly id: string;
}

export function DeleteButton({
  deleteAction = defaultDeleteAction,
  id,
}: DeleteButtonProps) {
  const { execute, isPending } = useAction(deleteAction as SafeActionLike, {
    onError: ({ error }) => {
      const message =
        typeof error.serverError === "string"
          ? error.serverError
          : "Failed to delete.";
      toast.error(message);
    },
  });
  return (
    <Button disabled={isPending} onClick={() => execute({ id } as never)}>
      Delete
    </Button>
  );
}
```

The two `as` casts are the price of polymorphism — narrowing each side breaks the generic-variance check. Document the contract on the prop ("an action that accepts `{id}` and resolves to `{ ok: true } | { error: string; ok: false }`") so consumers don't pass an action with a different input shape.

Use the nearest live caller in the owning workspace as the canonical example.
