# Soleaux remote service architecture

Soleaux cannot become a remote service by moving the current daemon to a server. Operating-system collection must remain on each monitored endpoint. Remote service requires a split architecture.

## Target topology

```text
Developer machine
  Soleaux endpoint agent
    native collector
    local session attribution
    redaction and aggregation
    encrypted outbound connection
          |
          v
Hosted Soleaux control plane
  API gateway and identity
  device enrollment
  telemetry ingest
  event stream
  durable metrics storage
  tenant metadata database
  alert workers
  web application
```

The endpoint agent initiates every connection. No inbound port should be required on the developer machine.

## Product modes

### Local-only

- daemon and dashboard run on one machine
- SQLite storage
- no account required
- process controls may be enabled locally

### Remote personal

- one user, multiple enrolled machines
- hosted dashboard and history
- endpoint data encrypted in transit
- process controls disabled remotely by default

### Team/enterprise

- organizations, roles, audit logs, SSO, retention policies, regional data placement, and fleet management

## Required new services

### Endpoint agent

Evolve the daemon into a signed endpoint agent that owns collection and local attribution. Add:

- persistent device identity
- enrollment flow
- outbound WebSocket or bidirectional gRPC connection
- offline queue with bounded disk usage
- sequence numbers and acknowledgements
- retry with exponential backoff and jitter
- server policy synchronization
- local redaction before transmission
- remote-control capability disabled by default
- signed automatic updates

### Control-plane API

Create a new service, separate from the collector:

```text
apps/soleaux-api or services/control-plane
```

Responsibilities:

- user and organization identity
- device enrollment and key rotation
- tenant authorization
- host/session/process metadata APIs
- dashboard query APIs
- alert and notification configuration
- audit logging
- subscription and usage metering if commercialized

A practical initial stack is TypeScript or Go for the API, PostgreSQL for metadata, and Redis for short-lived coordination. Rust is also appropriate if one-language backend consistency is preferred.

### Telemetry ingest

Do not send every raw process object through ordinary REST requests. Provide a dedicated ingest protocol with:

- compressed batches
- host ID, tenant ID, protocol version, sequence range, and timestamp
- idempotency keys
- explicit acknowledgements
- maximum batch sizes
- schema compatibility checks
- backpressure responses
- clock-skew reporting

Start with HTTPS batch upload or WebSocket. Adopt gRPC streaming when scale or binary efficiency justifies it.

### Storage

Use separate stores for separate workloads:

- PostgreSQL: users, organizations, hosts, sessions, settings, alerts, audit records
- time-series store: process and session samples
- object storage: optional diagnostic bundles and exports
- Redis or equivalent: enrollment challenges, presence, rate limits, and transient fan-out

For an MVP, TimescaleDB on PostgreSQL can reduce operational complexity. At larger scale, evaluate ClickHouse for high-cardinality telemetry.

### Hosted dashboard

The existing Next.js dashboard can operate against either local or remote APIs through configuration. Add:

```text
SOLEAUX_MODE=local|remote
SOLEAUX_API_URL=https://api.example.com
SOLEAUX_DAEMON_URL=http://127.0.0.1:43120
```

Remote mode requires account authentication, organization/host selection, scoped queries, server-sent or WebSocket updates, and strict prevention of direct local process actions.

## Identity and enrollment

Recommended enrollment flow:

1. User signs into the hosted dashboard.
2. Dashboard creates a short-lived, single-use enrollment code.
3. User runs `soleaux enroll <code>` locally.
4. Endpoint generates a device key pair locally.
5. Control plane exchanges the code for a device certificate or signed device token.
6. Only the public identity leaves the endpoint.
7. Endpoint stores credentials in the operating-system credential store.
8. Device appears as pending until the user approves it.

Use short-lived access tokens and renewable device credentials. Do not use one shared API key for all installations.

## Tenant isolation

Every remotely stored record must include a tenant boundary:

```text
organization_id
user_id where applicable
host_id
session_id
```

Authorization must be enforced in the query layer, not only in the UI. Consider PostgreSQL row-level security as defense in depth, but do not make it the only authorization control.

## Data minimization

Transmit aggregates by default:

- session totals
- process executable category
- CPU and memory samples
- alert evidence
- hashed or user-approved repository identifiers

Make these opt-in or redacted:

- full command lines
- absolute working-directory paths
- network destinations
- usernames in paths
- provider conversation identifiers

Never transmit environment values, file contents, terminal output, prompts, or source code as part of routine telemetry.

## Remote process actions

Remote terminate/kill is a separate high-risk product feature. The first hosted version should be read-only.

A future implementation requires:

- explicit per-device enablement
- privileged role requirement
- step-up authentication
- signed, expiring commands
- endpoint-side allow/deny policy
- exact process identity verification
- command acknowledgement and audit trail
- visible local notification
- emergency disable switch

## Scale phases

### Phase 1: remote personal beta

- hosted auth
- device enrollment
- one organization per account
- outbound HTTPS telemetry batches
- PostgreSQL/TimescaleDB
- read-only hosted dashboard
- 24-hour retention

### Phase 2: multi-user teams

- organizations and RBAC
- invitation flows
- audit logs
- configurable retention
- alert notifications
- regional deployment options

### Phase 3: enterprise

- SAML/OIDC SSO
- SCIM
- customer-managed keys
- private networking or relay
- data residency
- endpoint policy management
- signed update channels
- compliance program

## Remote acceptance criteria

- endpoint requires no inbound firewall rule
- every batch is authenticated, encrypted, ordered, and idempotent
- tenant isolation tests cover every API
- offline telemetry resumes without duplication
- endpoint survives expired credentials and rotates them safely
- hosted service never receives secrets or source content by default
- remote mode is read-only until separately approved
- deletion removes tenant data according to documented retention policy
- audit events exist for enrollment, credential changes, exports, and administrative actions
