---
title: Infer Non-DB Types from Schemas
impact: CRITICAL
description: Use z.output/z.input/z.infer over typeof Schema for non-DB schema-owned types. Never manually duplicate types.
tags: inference, types, typescript, DRY
---

# Infer Non-DB Types from Schemas

This rule applies only to non-database schemas owned by Zod. Route database contracts through the repository's [Supabase Schema Ownership](../../../../supabase/AGENTS.md) owner instead of redefining them here.

## Problem

Manually defining TypeScript interfaces alongside Zod schemas creates duplicate type definitions that drift apart. When the schema changes, the manual type is forgotten.

## Incorrect

```typescript
// BUG: manual type will drift from schema
interface User {
  name: string;
  email: string;
  age: number;
}

const UserSchema = z.object({
  name: z.string(),
  email: z.email(),
  age: z.number().min(0),
});

// These can silently diverge when schema is updated
```

## Correct

```typescript
const UserSchema = z.object({
  name: z.string(),
  email: z.email(),
  age: z.number().min(0),
});

// Output type (after parsing/transforms)
type User = z.output<typeof UserSchema>;

// Input type (before transforms — useful for forms)
type UserInput = z.input<typeof UserSchema>;

// Equivalent to z.output for ordinary output inference
type UserInferred = z.infer<typeof UserSchema>;
```

## Why

`z.output` and `z.infer` extract the output type after transforms. `z.input` extracts the input type before transforms. These always stay in sync with the schema. Use `z.output` when output-vs-input clarity matters, `z.infer` for ordinary output inference, and `z.input` when you need the pre-transform shape such as form state where a date field is a string before being transformed to `Date`.
