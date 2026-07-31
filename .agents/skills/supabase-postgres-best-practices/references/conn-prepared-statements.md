---
title: Use Prepared Statements Correctly with Pooling
impact: HIGH
impactDescription: Avoid prepared statement conflicts in pooled environments
tags: prepared-statements, connection-pooling, transaction-mode
---

## Use Prepared Statements Correctly with Pooling

Prepared statements are tied to individual database connections. In transaction-mode pooling, connections are shared, causing conflicts.

**Incorrect (named prepared statements with transaction pooling):**

```sql
-- Named prepared statement
prepare get_user as select * from users where id = $1;

-- In transaction mode pooling, next request may get different connection
execute get_user(123);
-- ERROR: prepared statement "get_user" does not exist
```

**Correct (use unnamed statements or session mode):**

```text
- In transaction mode, use unnamed statements or disable the driver's statement cache.
- Use session mode when the application requires named prepared statements.
- node-postgres (`pg`): omit the query config `name` property to avoid a named prepared statement.
- Postgres.js: use `prepare: false` when required by transaction mode.
- JDBC: use the driver's current `prepareThreshold` guidance.
```

Reference: [Prepared Statements with Pooling](https://supabase.com/docs/guides/database/connecting-to-postgres#connection-pool-modes)

Driver reference: [node-postgres prepared statements](https://node-postgres.com/features/queries#prepared-statements)
