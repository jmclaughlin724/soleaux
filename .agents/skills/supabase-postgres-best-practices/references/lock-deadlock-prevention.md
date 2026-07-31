---
title: Prevent Deadlocks with Consistent Lock Ordering
impact: MEDIUM-HIGH
impactDescription: Eliminate deadlock errors, improve reliability
tags: deadlocks, locking, transactions, ordering
---

## Prevent Deadlocks with Consistent Lock Ordering

Deadlocks occur when transactions lock resources in different orders. Always acquire locks in a consistent order.

**Incorrect (inconsistent lock ordering):**

```sql
-- Transaction A                    -- Transaction B
begin;                              begin;
update accounts                     update accounts
set balance = balance - 100         set balance = balance - 50
where id = 1;                       where id = 2;  -- B locks row 2

update accounts                     update accounts
set balance = balance + 100         set balance = balance + 50
where id = 2;  -- A waits for B     where id = 1;  -- B waits for A

-- DEADLOCK! Both waiting for each other
```

**Correct (lock rows in consistent order first):**

```sql
-- Explicitly acquire locks in ID order before updating
begin;
select * from accounts where id in (1, 2) order by id for update;

-- Now perform updates in any order - locks already held
update accounts set balance = balance - 100 where id = 1;
update accounts set balance = balance + 100 where id = 2;
commit;
```

Alternative: use a single statement to update atomically:

```sql
-- Single statement acquires all locks atomically
begin;
update accounts
set balance = balance + case id
  when 1 then -100
  when 2 then 100
end
where id in (1, 2);
commit;
```

Detect deadlocks and configure logging:

```sql
-- Check for recent deadlocks
select * from pg_stat_database where deadlocks > 0;

-- Supabase supports these reserved settings at role scope
alter role postgres set log_lock_waits = on;
alter role postgres set deadlock_timeout = '1s';
```

On self-managed Postgres, set these through an appropriately privileged configuration scope. Reconnect before verifying role defaults.

Reference: [Deadlocks](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-DEADLOCKS)

Supabase: [Customizing Postgres configs](https://supabase.com/docs/guides/database/custom-postgres-config)
