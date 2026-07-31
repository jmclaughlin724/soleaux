---
title: Configure Idle Connection Timeouts
impact: HIGH
impactDescription: Reclaim connection slots from abandoned idle sessions
tags: connections, timeout, idle, resource-management
---

## Configure Idle Connection Timeouts

Idle connections waste resources. Configure timeouts to automatically reclaim them.

**Incorrect (connections held indefinitely):**

```sql
-- No timeout configured
show idle_in_transaction_session_timeout;  -- 0 (disabled)

-- Connections stay open forever, even when idle
select pid, state, state_change, query
from pg_stat_activity
where state = 'idle in transaction';
-- Shows transactions idle for hours, holding locks
```

**Correct on Supabase (database defaults for new sessions):**

```sql
-- Terminate connections idle in transaction after 30 seconds
alter database postgres set idle_in_transaction_session_timeout = '30s';

-- Terminate completely idle connections after 10 minutes
alter database postgres set idle_session_timeout = '10min';
```

Reconnect before verifying database-level defaults. Use `set` for a single session or `alter role ... set` for a specific application role. On self-managed Postgres, choose the configuration scope appropriate to the deployment and privileges.

For pooled connections, prefer pooler-level timeouts; a server-side `idle_session_timeout` can surprise middleware that is not prepared for an asynchronous disconnect:

```ini
# pgbouncer.ini
server_idle_timeout = 60
client_idle_timeout = 300
```

Reference: [Connection Timeouts](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-IDLE-IN-TRANSACTION-SESSION-TIMEOUT)

Supabase: [Customizing Postgres configs](https://supabase.com/docs/guides/database/custom-postgres-config)
