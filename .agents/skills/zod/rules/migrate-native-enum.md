---
title: "v4: Unified z.enum()"
impact: MEDIUM
description: Replace z.nativeEnum(); use z.enum() with literal tuples or as const objects, and pass TypeScript enums only when they are existing external or legacy inputs.
tags: migration, v4, enum, nativeEnum
---

# v4: Unified z.enum()

## Problem

Zod v3 had separate `z.enum()` (string arrays) and `z.nativeEnum()` (TypeScript/JS enums). In v4, `z.enum()` handles literal tuples, enum-like object literals, and externally declared TypeScript enums. `z.nativeEnum()` is removed.

For repo-owned value sets, follow TypeScript's `as const` object/tuple pattern instead of creating new TypeScript `enum` declarations. Use `z.enum(existingEnum)` only for existing external or legacy enums that cannot be converted at the owner.

## Incorrect

```typescript
// BAD: z.nativeEnum() is removed in v4.
const Role = {
  Admin: "admin",
  User: "user",
  Guest: "guest",
} as const;

const RoleSchema = z.nativeEnum(Role); // Error: z.nativeEnum is not a function
```

## Correct

```typescript
// GOOD: repo-owned value set uses an enum-like object.
const Role = {
  Admin: "admin",
  User: "user",
  Guest: "guest",
} as const;
type Role = (typeof Role)[keyof typeof Role];
const RoleSchema = z.enum(Role);

// GOOD: direct literal tuple also preserves values.
const StatusSchema = z.enum(["active", "inactive", "pending"]);

// LEGACY/EXTERNAL ONLY: bridge an existing enum you do not own.
const ExternalRoleSchema = z.enum(ExternalRole);
```

## Why

Zod v4 unifies enum handling and deprecates `z.nativeEnum()`. Official Zod docs recommend direct literals or `as const` when values are declared in variables, support enum-like object literals, and reserve TypeScript enum input for externally declared enums. TypeScript's own enum reference documents `as const` objects as the modern enum alternative and uses `(typeof Values)[keyof typeof Values]` to derive the value union.
