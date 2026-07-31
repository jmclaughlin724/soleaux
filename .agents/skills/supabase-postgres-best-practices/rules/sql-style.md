# Postgres SQL Style

Load this rule when writing or reviewing PostgreSQL. It owns reusable SQL style decisions, not Codex command execpolicy. The target repository's schema ownership, key strategy, migration workflow, public contracts, and version-specific PostgreSQL behavior override the defaults below when they deliberately differ.

## General

- Write SQL reserved words in lowercase.
- Use descriptive identifiers, consistent indentation, blank lines between logical blocks, spaces around operators, and spaces after commas.
- Write temporal literals in ISO 8601 form: `yyyy-mm-dd` for dates and `yyyy-mm-ddThh:mm:ss.sssss` for timestamps. Add an offset when the value represents an instant.
- Comment complex or non-obvious logic with `--` line comments or `/* ... */` block comments.

## Naming

- Use descriptive, unquoted lowercase `snake_case` identifiers. Keep acronyms as readable lowercase tokens, such as `api_client_id`, unless an existing quoted identifier contract must be preserved.
- Avoid reserved words and keep identifiers unique within PostgreSQL's 63-byte default limit.
- Prefer plural table names and singular column names.
- Do not prefix tables with `tbl_`, and do not give a column the same name as its table.

## Tables And Columns

- Schema-qualify database objects in DDL and queries. Use `public` when no owner specifies another schema.
- Give each new base table an `id bigint generated always as identity primary key` unless the owner specifies another primary-key contract. The primary key is the allowed generic `id`; otherwise avoid generic column names.
- Name foreign-key columns from the singular referenced domain term plus `_id`, such as `user_id` for `users`, unless the relationship needs a more specific role name.
- Add `comment on table <schema>.<table> is '<description>';` for every new table and update stale comments when behavior changes. Keep each table description at or below 1024 characters.

```sql
create table public.books (
  id bigint generated always as identity primary key,
  title text not null,
  author_id bigint references public.authors (id)
);

comment on table public.books is 'Books available in the library.';
```

## Queries

- Keep short queries compact. As a query grows, put projected columns and major clauses on separate lines.
- Align joins and subqueries with the clauses they support. Prefer schema-qualified full table names when that is clearer than an alias.
- When an alias materially improves readability, choose a meaningful name and write `as` explicitly for table and column aliases.
- For highly complex queries, prefer clearly named, linear CTEs. Comment each CTE block with its purpose, prefer readability unless measured requirements demand optimization, and keep the optimized form as clear as the workload permits.

## Authority

Lowercase keywords, naming preferences, the default schema and primary key, formatting, and the 1024-character table-description limit are style decisions. Do not present them as PostgreSQL requirements. PostgreSQL syntax and limits remain authoritative:

- [Lexical structure and identifiers](https://www.postgresql.org/docs/current/sql-syntax-lexical.html)
- [`create table` and identity columns](https://www.postgresql.org/docs/current/sql-createtable.html)
- [`comment`](https://www.postgresql.org/docs/current/sql-comment.html)
