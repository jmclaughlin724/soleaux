# Remote infrastructure handoff

This directory only provisions development dependencies for the future hosted control plane. It does not make the current local daemon safe to expose publicly.

## Start dependencies

```bash
cd infra/remote
export POSTGRES_PASSWORD='replace-with-a-local-development-secret'
docker compose up -d postgres redis
```

Optional local object storage for diagnostic bundle development:

```bash
export MINIO_ROOT_USER='soleaux-local'
export MINIO_ROOT_PASSWORD='replace-with-a-long-local-secret'
docker compose --profile diagnostics up -d
```

## Services still to implement

Add these as separate deployable services before remote beta:

```text
services/control-plane-api
services/telemetry-ingest
services/alert-worker
services/notification-worker
apps/soleaux-dashboard remote mode
```

The control-plane API owns identity, organizations, hosts, enrollment, settings, queries, and audit records. Telemetry ingest owns device connections, batch validation, acknowledgement, and time-series writes. Do not combine endpoint process collection with the hosted API.

## Required database work

- migrations and migration runner
- organizations, users, memberships, roles
- hosts and device credentials
- sessions and process metadata
- time-series hypertables
- alerts and alert state
- audit events
- retention and deletion jobs
- tenant-scoped indexes
- authorization tests

## Required deployment work

- container images for each hosted service
- non-root runtime users
- health and readiness endpoints
- TLS termination
- secret-manager integration
- database backups and restoration tests
- metrics, logs, and distributed traces
- autoscaling and resource limits
- rate limiting and abuse controls
- regional environment separation
- CI deployment promotion

## Production warning

Do not publish PostgreSQL, Redis, MinIO, or the endpoint daemon directly to the internet. The compose ports bind to loopback intentionally. Production deployments should use private networks and managed identity between services.
