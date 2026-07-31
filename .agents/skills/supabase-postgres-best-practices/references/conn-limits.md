---
title: Set Appropriate Connection Limits
impact: CRITICAL
impactDescription: Prevent database crashes and memory exhaustion
tags: connections, max-connections, limits, stability
---

## Set Appropriate Connection Limits

Too many backend connections exhaust memory and degrade throughput. Budget direct connections and every pooler's backend connections together, then size application pools from observed concurrency and the project's compute limits.

**Incorrect (unlimited or excessive connections):**

```sql
-- Default max_connections = 100, but often increased blindly
show max_connections;  -- 500 (way too high for 4GB RAM)

-- Every backend and its active query can consume memory.
-- A blind increase can exhaust memory and reduce throughput under load.
```

**Correct (inspect and budget before changing settings):**

```sql
-- Inspect the server limit and current usage
show max_connections;

select application_name, usename, state, count(*)
from pg_stat_activity
group by application_name, usename, state
order by count(*) desc;
```

On hosted Supabase, `max_connections` is a restart-requiring CLI configuration. Discover the current command first and change it only with authority for the verified project:

```bash
supabase postgres-config update --help
```

`work_mem` can be consumed by multiple plan nodes and parallel workers in one query, so `work_mem * max_connections` is not a safe upper-bound formula. Tune it only from representative plans and concurrency, using the supported database, role, or Supabase CLI scope.

```sql
-- Example database-level scope; choose a measured value
alter database postgres set work_mem = '8MB';
```

Monitor connection usage:

```sql
select count(*), state from pg_stat_activity group by state;
```

Reference: [Connection management](https://supabase.com/docs/guides/database/connection-management)

Configuration: [Customizing Postgres configs](https://supabase.com/docs/guides/database/custom-postgres-config)
