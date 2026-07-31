---
title: Enable Row Level Security for Multi-Tenant Data
impact: CRITICAL
impactDescription: Database-enforced tenant isolation, prevent data leaks
tags: rls, row-level-security, multi-tenant, security
---

## Enable Row Level Security for Multi-Tenant Data

Row Level Security (RLS) enforces data access at the database level, ensuring users only see their own data.

**Incorrect (application-level filtering only):**

```sql
-- Relying only on application to filter
select * from orders where user_id = $current_user_id;

-- Bug or bypass means all data is exposed!
select * from orders;  -- Returns ALL orders
```

**Correct (database-enforced RLS):**

```sql
alter table public.orders enable row level security;
alter table public.orders force row level security;

create policy "Authenticated users can read their own orders."
  on public.orders
  as permissive
  for select
  to authenticated
  using ((select auth.uid()) = user_id);
```

Create separate policies for each required operation; do not use `FOR ALL` as a shortcut. See [RLS policy authoring](../rules/create-rls-policies.md).

Reference: [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
