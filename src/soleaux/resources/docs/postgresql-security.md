# PostgreSQL provider security

Bounded PostgreSQL source extraction, statement-aware repository resolution, PL/pgSQL provenance, diagnostics, and promotion into generic catalog symbols, calls, references, engines, chunks, semantic tables, and quality tables are integrated. The pinned language-server launch, offline configuration, process cleanup, and secret boundary are also implemented. Offline repository intelligence is the supported default; optional connected-database enrichment remains experimental and is not part of the default release claim.

Soleaux starts PostgreSQL Language Server in offline mode by default. Version 0.25.4's `lsp-proxy` does not accept `--disable-db`; its database connection defaults off and is enabled only when connection settings are present. The offline runtime carries no database environment variables, generated configuration contains neither `db.connectionString` nor `db.host`, and `db.allowStatementExecutionsAgainst` remains empty. Soleaux uses `--disable-db` only for the separate `check` validation command. No database credential is stored in the repository or generated configuration.

## Environment-only local connections

The experimental connected-mode boundary is request-local. Supply credentials from a short-lived process environment or secret manager only. Soleaux accepts an explicit endpoint only when it resolves to loopback (`localhost`, `127.0.0.0/8`, or `::1`) or a local Unix-domain socket. Production-like hosts are rejected.

The supported connection names are exactly:

- `DATABASE_URL`; or
- `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, and `PGDATABASE`.

Other `PG*` names are not inherited. Never place a password, connection URL, or service credential in `soleaux.toml`, SQL source, a checked-in environment file, a provider command, or the generated `postgres-language-server.jsonc`.

## Least-privilege role

Create a dedicated login outside the repository. Grant only what the selected schemas require:

```sql
CREATE ROLE soleaux_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION;
GRANT CONNECT ON DATABASE local_development TO soleaux_reader;
GRANT USAGE ON SCHEMA app TO soleaux_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA app TO soleaux_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
  GRANT SELECT ON TABLES TO soleaux_reader;
```

Set the password through the local database administrator or secret manager; do not put it in the SQL above. Do not grant `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER`, ownership, superuser, replication, or broad cross-database roles. Add schema `USAGE` and table or view `SELECT` grants one schema at a time.

## Logs, configuration, and disclosure

Each provider process uses session-scoped configuration and log directories under the host temporary directory, outside the analyzed repository. Generated configuration is removed at shutdown. Retained logs follow Soleaux's configured log-retention window and carried provider values are redacted before errors, diagnostics, metadata, or retained logs cross the runtime boundary.

Connected results disclose that external database state was consulted through an opaque SHA-256 fingerprint and observation time. Repository source remains the authority; the fingerprint contains no endpoint, username, password, or connection string.
