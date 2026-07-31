---
title: Create Supabase RLS Policies
impact: CRITICAL
impactDescription: Prevents cross-operation access and unauthorized ownership changes
tags: rls, row-level-security, supabase-auth, authorization, security
---

# Database: Create RLS Policies

## Contract

Generate valid Supabase Postgres row-level security policy SQL from the user's constraints and the retrieved schema. Inspect the target schema, tables, columns, relationships, roles, grants, existing policies, and application access path before authoring. Start schema discovery with `public` when no narrower target is known, but never invent or assume an identifier or authorization relationship that the available schema does not establish.

If a request is not about creating or altering RLS policies, explain that this rule handles policy authoring only and route the task to the applicable Postgres or Supabase guidance.

## Output

- Return valid Markdown with SQL inside a fenced `sql` block.
- Emit only valid `CREATE POLICY` or `ALTER POLICY` statements in the SQL block. Put required RLS enablement, privileges, indexes, functions, policy replacement, and other non-policy prerequisites in prose instead of emitting additional SQL.
- Keep explanations short and outside the SQL block. Never add inline SQL comments.
- Use short, descriptive policy names enclosed in double quotes.
- Escape an apostrophe inside a SQL string literal by doubling it, for example `'Night''s watch'`.
- Use schema-qualified table names when the schema is known.

## Operation Contracts

Create one policy per required operation. Never use `FOR ALL`, and never put multiple operations in one `FOR` clause.

| Operation | `USING` | `WITH CHECK` |
| --- | --- | --- |
| `SELECT` | Required | Forbidden |
| `INSERT` | Forbidden | Required |
| `UPDATE` | Normally required for the existing row | Required for the resulting row |
| `DELETE` | Required | Forbidden |

For ownership- or tenancy-preserving updates, use the same authorization boundary in both `USING` and `WITH CHECK` unless the retrieved access model explicitly requires different predicates. Confirm that a compatible `SELECT` policy exists because an update cannot operate as intended when its target row is not selectable.

## Supabase Roles And Identity

- Put `FOR <operation>` after the table and `TO <roles>` after `FOR`.
- Always specify the applicable role set. Use `authenticated` for signed-in requests, `anon` for unsigned requests, or the narrow custom role established by the schema and access model.
- A `TO` role controls who evaluates a policy; it does not authorize rows by itself. Pair it with the required ownership, tenancy, or public-access predicate.
- Use `(select auth.uid())` for Supabase user identity, never `current_user` or `auth.role()`. Add an explicit non-null check when the policy can run for unauthenticated requests or when it makes the intended denial clearer.
- Use `auth.jwt()` authorization claims only from trusted app metadata. Never authorize from user-editable metadata, and account for JWT claims remaining stale until token refresh.

## Policy Combination

Prefer `AS PERMISSIVE`, which is PostgreSQL's default and combines applicable permissive policies with `OR`. Discourage `AS RESTRICTIVE` unless the user needs a deliberate defense-in-depth constraint. When restrictive policy is requested, explain that restrictive policies combine with `AND` and require at least one applicable permissive policy to grant the base access.

## Performance

- Wrap row-independent helpers such as `auth.uid()` and `auth.jwt()` in `select` so PostgreSQL can evaluate them once per statement when their result does not depend on row data.
- Check whether ownership, tenancy, or membership predicate columns are already covered by suitable indexes. When the output is policy-only, recommend missing indexes in prose rather than emitting `CREATE INDEX`.
- Avoid row-dependent joins when an equivalent set lookup can compute the caller's authorized identifiers independently of the target row.

## ALTER POLICY Limitation

`ALTER POLICY` can change the role list, `USING`, `WITH CHECK`, or the policy name. It cannot change the policy command or switch between permissive and restrictive. If the requested change requires dropping and recreating a policy, explain that limitation instead of fabricating unsupported `ALTER POLICY` syntax or emitting prohibited statements.

## Example

```sql
CREATE POLICY "Authenticated users can create their own books."
  ON public.books
  AS PERMISSIVE
  FOR INSERT
  TO authenticated
  WITH CHECK ((select auth.uid()) = author_id);
```

## Verification

Before returning the result, verify that every referenced identifier exists in the retrieved schema, every policy uses exactly one operation, the `TO` clause follows `FOR`, the operation uses only its permitted predicate clauses, and the SQL block contains no statement other than `CREATE POLICY` or `ALTER POLICY`.

References:

- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [PostgreSQL CREATE POLICY](https://www.postgresql.org/docs/current/sql-createpolicy.html)
- [PostgreSQL ALTER POLICY](https://www.postgresql.org/docs/current/sql-alterpolicy.html)
