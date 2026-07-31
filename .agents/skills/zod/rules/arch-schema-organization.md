---
title: Co-locate Schemas with Their Boundary Layer
impact: HIGH
description: Co-locate non-database schemas with their owning boundary and derive types from them.
tags: architecture, organization, co-location, project-structure
---

# Co-locate Schemas with Their Boundary Layer

This rule applies only to non-database schemas owned by Zod. Route database contracts through the repository's [Supabase Schema Ownership](../../../../supabase/AGENTS.md) owner instead of redefining them here.

## Problem

Dumping all schemas into a single `schemas/` folder creates a grab bag of unrelated schemas with no indication of where they're used. Schemas for API routes, form validation, and env parsing all mix together, making it unclear which boundary owns which schema.

## Incorrect

```
// BUG: everything in one folder — no layering, unclear ownership
src/
  schemas/
    user.ts          // used by API AND form AND env?
    order.ts
    config.ts
    form-profile.ts
    api-response.ts
    db-result.ts
```

```typescript
// BUG: domain module imports and re-exports raw schemas
// src/domain/user.ts
import { UserSchema } from "../schemas/user";
export { UserSchema }; // leaking parsing into domain
```

## Correct

```
src/
  api/
    users/
      route.ts
      schemas.ts       // API request/response schemas for /users
    orders/
      route.ts
      schemas.ts       // API schemas for /orders
  profile/
    form-schema.ts     // form validation schema
    ProfileForm.tsx
  config/
    env.ts             // env var schema, parsed at startup
  domain/
    types.ts           // z.output/z.input types only for non-DB schemas — no raw schemas
```

```typescript
// api/users/schemas.ts — co-located with the route
import { z } from "zod";

export const CreateUserBody = z.object({
  name: z.string().min(1),
  email: z.email(),
});

export const UserResponse = z.object({
  id: z.string(),
  name: z.string(),
  email: z.email(),
});

// domain/types.ts — inferred non-DB types only, no raw schemas
import type { CreateUserBody, UserResponse } from "../api/users/schemas";
import type { z } from "zod";

export type CreateUser = z.input<typeof CreateUserBody>;
export type User = z.output<typeof UserResponse>;
```

## Why

Co-location makes ownership clear: the API route handler and its schemas live in the same directory. When a route changes, the schema changes with it. Non-DB domain types are derived via `z.input`, `z.output`, or `z.infer` so business logic depends on schema-derived TypeScript types, not duplicate manual interfaces. This keeps parsing at the boundary and business logic free of runtime validation concerns.
