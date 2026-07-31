# Soleaux security and privacy requirements

Soleaux observes process metadata and resource activity on developer machines. Treat it as endpoint security-sensitive software even when it is not an endpoint protection product.

## Trust boundaries

1. Operating system to endpoint agent
2. Local agent API to local dashboard and MCP client
3. Endpoint agent to hosted ingest
4. Hosted API to dashboard
5. Tenant data to administrators and support personnel
6. Update service to installed endpoint binaries

## Local security baseline

- Bind collector APIs to `127.0.0.1` by default.
- Remove permissive CORS before release. Allow only the configured local dashboard origin.
- Generate a local bearer token on first run and store it with user-only permissions.
- Require the token for mutations and sensitive metadata endpoints.
- Keep process actions disabled unless explicitly enabled.
- Reject arbitrary proxy destinations.
- Validate request bodies and cap response sizes.
- Redact command arguments before persistence or display.
- Never log authorization values or full environment maps.

## Remote security baseline

- TLS 1.2 or newer; prefer TLS 1.3.
- Per-device credentials, not shared installation keys.
- Short-lived user sessions and renewable device credentials.
- Device key material stored in Keychain, Credential Manager, or Secret Service.
- Signed enrollment challenges with short expiry and one-time use.
- Tenant authorization on every read and write.
- Rate limiting by user, tenant, device, and IP.
- Immutable audit records for security-sensitive operations.
- Encryption at rest for credentials and sensitive tenant metadata.
- Secrets managed through a dedicated secret manager.

## Threat model checklist

Address at minimum:

- malicious website reaching a loopback daemon
- DNS rebinding
- CSRF against local mutation endpoints
- another local user reading the daemon socket or database
- stale PID used to terminate a different process
- process command containing secrets
- compromised endpoint sending fabricated telemetry
- stolen enrollment code
- replayed telemetry batches
- cross-tenant object reference
- support personnel over-access
- malicious or compromised update channel
- denial of service through high process counts or telemetry flood

## Privacy model

Default collection should include only what is required to attribute resource usage:

- process identity
- executable name
- parent relationship
- CPU and memory
- coarse disk/network counters when available
- session and tool correlation IDs
- timestamps and lifecycle state

Default exclusion:

- prompts
- source code
- file contents
- terminal output
- environment values
- clipboard contents
- browser content
- authentication headers

Command lines and paths require configurable redaction. Provide an on-device preview showing exactly what will be transmitted in remote mode.

## Data controls

Users and organizations need:

- configurable retention
- host pause and disconnect
- session deletion
- host deletion
- organization export
- organization deletion
- remote collection policy
- per-field redaction policy
- audit history

## Secure development requirements

- dependency and license scanning
- secret scanning
- Rust and npm advisory scanning
- static analysis
- fuzzing for ingest decoders and redaction
- property tests for tenant authorization
- penetration testing before remote process control
- reproducible signed releases where practical
- documented vulnerability disclosure process

## Incident readiness

Before hosted launch, define:

- security contact
- severity classification
- credential revocation procedure
- endpoint certificate rotation
- forced client update mechanism
- tenant notification process
- forensic log retention
- backup restoration tests
- data breach response procedure
