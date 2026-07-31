# Input & Output Validation

## Standard Schema v1

next-safe-action works with any validation library that implements the [Standard Schema](https://github.com/standard-schema/standard-schema) specification (v1). This includes Zod, Yup, Valibot, ArkType, and others — no adapters needed.

## Input Schema

### Basic usage

```ts
"use server";

import { z } from "zod";
import { actionClient } from "@/lib/safe-action";

export const createUser = actionClient
  .inputSchema(
    z.object({
      name: z.string().min(2),
      email: z.email(),
      age: z.number().int().min(18).optional(),
    })
  )
  .action(async ({ parsedInput }) => {
    // parsedInput is fully typed: { name: string; email: string; age?: number }
    const user = await db.user.create(parsedInput);
    return { id: user.id };
  });
```

### Trim before min(1) for delivery-bound text

Use `z.string().trim().min(1)` for any field that surfaces to an external delivery surface (email subject, SMS body, contact display name, etc.). Bare `z.string().min(1)` accepts a single space, tab, or any whitespace-only string because `" ".length === 1`, and the value lands in the recipient's inbox as a blank-looking record. Trim first so the length check sees the meaningful content. Internal-only string fields (UUIDs, slugs, enum literals) do not need `.trim()`.

```ts
// Email subject — must reject "" and " " alike.
subject: z.string().trim().min(1).max(500),

// File paths inside chip metadata — must reject empty strings so the UI does
// not render a blank chip whose backing storage path is "".
storagePath: z.string().min(1),
```

### With Valibot

```ts
import { email, minLength, object, pipe, string } from "valibot";

export const createUser = actionClient
  .inputSchema(
    object({
      name: pipe(string(), minLength(2)),
      email: pipe(string(), email()),
    })
  )
  .action(async ({ parsedInput }) => {
    // Same typed parsedInput
  });
```

### Async schema factory

Use an async function to dynamically extend schemas. The factory receives the previous schema (if chained) and the raw client input:

```ts
export const updateUser = actionClient
  .inputSchema(z.object({ id: z.uuid() }))
  .inputSchema(async (prevSchema, { clientInput }) => {
    // prevSchema is the z.object({ id }) from above
    // clientInput is the raw input from the client
    const user = await db.user.findById((clientInput as any).id);
    return prevSchema.extend({
      name: z
        .string()
        .min(2)
        .default(user?.name ?? ""),
    });
  })
  .action(async ({ parsedInput }) => {
    // parsedInput: { id: string; name: string }
  });
```

## Output Schema

Validates the return value of the action. If validation fails, `ActionOutputDataValidationError` is thrown.

```ts
export const getUser = actionClient
  .inputSchema(z.object({ id: z.uuid() }))
  .outputSchema(
    z.object({
      id: z.string(),
      name: z.string(),
      email: z.email(),
    })
  )
  .action(async ({ parsedInput }) => {
    const user = await db.user.findById(parsedInput.id);
    if (!user) throw new Error("User not found");
    // Return value is validated against outputSchema
    return { id: user.id, name: user.name, email: user.email };
  });
```

## Custom Validation Error Shape

Override the default validation error shape per-action:

```ts
import { flattenValidationErrors } from "next-safe-action";

export const myAction = actionClient
  .inputSchema(z.object({ email: z.email() }), {
    handleValidationErrorsShape: async (validationErrors) =>
      flattenValidationErrors(validationErrors),
  })
  .action(async ({ parsedInput }) => {
    // ...
  });

// Result shape: { formErrors: string[], fieldErrors: { email?: string[] } }
```

## No Input Schema

Actions without `.inputSchema()` accept no input:

```ts
export const getCurrentTime = actionClient.action(async () => {
  return { time: new Date().toISOString() };
});

// Client: execute() takes no arguments
```
