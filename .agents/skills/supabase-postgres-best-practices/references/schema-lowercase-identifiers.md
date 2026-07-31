---
title: Use Lowercase Identifiers for Compatibility
impact: MEDIUM
impactDescription: Avoid case-sensitivity bugs with tools, ORMs, and AI assistants
tags: naming, identifiers, case-sensitivity, schema, conventions
---

## Use Lowercase Identifiers for Compatibility

PostgreSQL folds unquoted identifiers to lowercase. Quoted mixed-case identifiers require quotes forever and cause issues with tools, ORMs, and AI assistants that may not recognize them.

**Incorrect (mixed-case identifiers):**

```sql
-- Quoted identifiers preserve case but require quotes everywhere
create table public."Users" (
  "userId" bigint primary key,
  "firstName" text,
  "lastName" text
);

-- Must always quote or queries fail
select "firstName" from public."Users" where "userId" = 1;

-- This fails - Users becomes users without quotes
select firstName from public.Users;
-- ERROR: relation "users" does not exist
```

**Correct (lowercase snake_case):**

```sql
-- Unquoted lowercase identifiers are portable and tool-friendly
create table public.users (
  user_id bigint primary key,
  first_name text,
  last_name text
);

-- Works without quotes, recognized by all tools
select first_name from public.users where user_id = 1;
```

Common sources of mixed-case identifiers:

```sql
-- ORMs often generate quoted camelCase - configure them to use snake_case
-- Migrations from other databases may preserve original casing
-- Some GUI tools quote identifiers by default - disable this

-- If stuck with mixed-case, create views as a compatibility layer
create view public.users as
select
  "userId" as user_id,
  "firstName" as first_name
from public."Users";
```

Reference: [Identifiers and Key Words](https://www.postgresql.org/docs/current/sql-syntax-lexical.html#SQL-SYNTAX-IDENTIFIERS)
