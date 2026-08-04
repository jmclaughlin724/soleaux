# Soleaux telemetry implementation status

Status of the telemetry surface as consolidated into the soleaux product tree (`tools/soleaux/telemetry/`). This document describes what actually ships; earlier revisions listed planned detectors and models that were removed before consolidation because upstream evidence did not support them.

## Wired and verified

- provider-neutral TypeScript protocol (`@soleaux/protocol`)
- loopback Rust daemon on port 43120 (`native/daemon/telemetry`)
- system and per-process CPU/memory collection
- stable process identity using PID plus process start time
- session registration and explicit root process records
- descendant attribution through the process tree
- session list, process list, health, system, usage, quota, and SSE endpoints
- session-aware CLI launcher for Claude, Codex, and custom commands (`telemetry/cli`)
- exact LLM request-usage ingest endpoint with idempotent duplicate rejection
- quota/reset snapshot ingest endpoint
- token accounting for input, output, cached input, cache writes, reasoning, and billing-style totals (one convention across all ingestion paths)
- LLM performance tracking for latency, TTFT, throughput, retries, failures, cost, and credits
- context-window capacity, usage, utilization, and alerts
- OpenAI Responses/Chat Completions and Anthropic Messages normalization (`@soleaux/telemetry-sdk`)
- OpenAI organization Usage API and Anthropic Messages Usage Report synchronization (`telemetry/sync`), with documented `group_by` fields only
- aggregate request counts taken from provider-reported counts, not bucket counts
- CLI commands for usage, quota, status, and session records
- evidence-only local scanner for JSON, JSONL, provider transcripts, daemon events, quota snapshots, and process samples (`telemetry/scan`)
- Claude and Codex transcript adapters restricted to documented event shapes, with record timestamps used when present and synthetic ordering marked
- tool categorization for model, rg, bash, web search, MCP, tests, git, file reads/writes, compaction, and other tools
- measured observations for exact normalized-signature repeats and for failed/retried calls, each carrying an explicit evidence requirement
- evidence-requirement inventories for tool-level tokens, wasted tokens, CPU/memory attribution, subscription capacity, and Soleaux savings
- Markdown and JSON scan reports plus scanner tests
- live Next.js dashboard proxy on port 43121 on `@soleaux/ui`
- live provider usage, subscription windows, reset times, recent LLM requests, context pressure, process table, and alerts
- `telemetry_*` read-only tools on the soleaux MCP server, enabled per workspace via `[telemetry]` in `soleaux.toml`
- root commands: `soleaux:telemetry:{daemon,daemon:check,dashboard,scan,sync,cli}` and `soleaux:telemetry:verify:upstream`
- security and privacy baseline, environment template, and loopback-only development infrastructure (pinned TimescaleDB, Redis, optional MinIO)
- bearer-token auth on the daemon API listener (port 43120), mirroring the `soleauxd http` pattern: `--token` flag, `SOLEAUX_DAEMON_TOKEN` env var, or a token generated on first run into `~/.soleaux/telemetry/daemon.token` (override with `--token-file` / `SOLEAUX_DAEMON_TOKEN_FILE`) with user-only permissions; minimum 32 characters, constant-time comparison, `/api/v1/health` exempt for probes
- SSE stream auth via `Authorization: Bearer` or, because `EventSource` cannot set headers, the RFC 6750 `access_token` query parameter on `/api/v1/stream` only; request spans log the path without the query string
- CORS restricted to the configured dashboard origins (default `http://{127.0.0.1,localhost,[::1]}:43121`, parameterized via `--allowed-origin` / `SOLEAUX_DAEMON_ALLOWED_ORIGINS`), plus server-side rejection of disallowed `Origin` headers; auth and CORS wrap the API listener outside `build_router` so the same-origin dashboard listener can mount the routes unwrapped
- command-argument redaction in process records: process listings and stream snapshots keep `argv[0]` and replace remaining arguments with a redaction marker

Intentionally not shipped: tool-level token allocation, arbitrary waste percentages, subscription-equivalent-day calculations, and projected Soleaux savings. Upstream data does not establish those values; the scanner reports the evidence required instead of estimating.

## Verified checks

- `turbo run typecheck test:unit` across all telemetry packages
- scanner unit and fixture tests (`node --test` in `telemetry/scan`)
- `cargo check --locked` and `cargo test --locked` for the daemon, including auth, CORS/origin, SSE token, and redaction tests
- Ultracite lint and Prettier formatting across the telemetry tree
- soleaux package suite including telemetry tool tests (`tests/test_telemetry.py`)

## Required local implementation before release

See `LOCAL_IMPLEMENTATION.md`, `USAGE_LIMITS_AND_LLM_TELEMETRY.md`, and `FREE_SCAN.md`. The highest-priority work is:

1. validate the free scanner against redacted fixtures from the installed Claude Code and Codex versions
2. replace per-request process refresh with a long-lived collector
3. add SQLite migrations, retention, usage-sync cursors, reconciliation, scan history, and restart recovery
4. implement user-authorized subscription quota capture where providers do not expose a supported API
5. add durable tool-execution correlation and process-sample history
6. propagate the daemon bearer token to the CLI, SDK, sync, dashboard, and soleaux MCP consumers, which do not yet send it
7. complete unit, integration, synthetic-load, provider-fixture, scanner, and UI tests

## Required remote implementation

See `REMOTE_SERVICE_ARCHITECTURE.md`. Remote service requires a new hosted control plane; the endpoint collector cannot simply be exposed publicly.

Required hosted components:

- identity and tenant-aware control-plane API
- device enrollment and credential rotation
- authenticated compressed telemetry ingest
- PostgreSQL/TimescaleDB metadata, process metrics, usage events, quota windows, scan reports, and model catalog storage
- provider-account credential isolation and organization-usage synchronization workers
- server-side scan aggregation without uploading prompts, source content, or raw tool output
- alert and notification workers
- hosted dashboard remote mode
- audit logging, retention, deletion, backups, monitoring, and incident response

The first remote release should be read-only. Remote terminate/kill is a separate high-risk feature.

## Not yet production-complete

- scanner fixture validation has not been executed against locally installed provider clients
- SQLite persistence and reconciliation are not implemented
- consumer Claude and Codex remaining-plan capacity cannot be fetched automatically without a supported provider API or explicit user-authorized capture
- Claude Code and Codex installed-client event schemas have not been validated locally
- model catalog and versioned context-window synchronization are not implemented
- disk and per-process network accounting are not implemented
- native macOS collector APIs are not implemented
- detached-process recovery after daemon restart is not implemented
- durable tool invocation hooks beyond transcript and process-tree attribution are not implemented
- terminate/kill endpoints are not implemented
- the telemetry CLI, SDK, sync worker, dashboard proxy, and soleaux MCP tools do not yet send the daemon bearer token, so ingest and reads against a secured daemon fail until they are updated
- premium Shadcn Studio source has not been imported
- desktop packaging and signed installers are not implemented
- hosted control-plane services do not yet exist
- Linux and Windows collectors are not implemented

This surface is a verified implementation framework, not a production release.
