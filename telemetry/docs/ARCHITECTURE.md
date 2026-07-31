# Soleaux telemetry architecture

Soleaux telemetry is the observability surface of the Soleaux product: local-first usage, quota, context, and process observability for AI coding agents. It ships as part of the soleaux package tree and must not depend on host-application product concepts or data.

## Product boundaries

All surfaces live under `tools/soleaux/telemetry/`:

- `daemon/`: loopback-only Rust collector, attribution engine, and HTTP/SSE API on `127.0.0.1:43120`. State is in-memory; persistence is pending.
- `dashboard/`: Next.js browser client on port 43121; proxies the daemon through a rewrite and renders the live snapshot stream.
- `protocol/`: provider-neutral, versioned TypeScript domain and stream contracts (`@soleaux/protocol`).
- `sdk/`: request-level OpenAI/Anthropic usage normalization and ingest client (`@soleaux/telemetry-sdk`).
- `cli/`: session-aware launcher for Claude, Codex, and custom commands.
- `sync/`: OpenAI organization Usage API and Anthropic Messages Usage Report synchronization.
- `scan/`: evidence-only local scanner for JSON/JSONL events, provider transcripts, daemon state, quota snapshots, and process samples.
- `ui/`: the telemetry design system (`@soleaux/ui`), owned by this product.
- Read-only diagnostics are the `telemetry_*` tools on the soleaux MCP server (`src/soleaux/telemetry.py`), enabled per workspace via `[telemetry]` in `soleaux.toml`. There is no separate MCP server or distribution.

## Runtime topology

```text
Agent or launcher
  -> provider adapter
  -> Soleaux session registration
  -> local process collector
  -> attribution and aggregation
  -> HTTP/SSE on 127.0.0.1:43120/api/v1
  -> dashboard proxy on 43121
  -> telemetry_* tools on the soleaux MCP server
```

## Base-URL convention

`SOLEAUX_DAEMON_URL` is always the bare origin (`http://127.0.0.1:43120`). Every consumer — dashboard rewrite, CLI, SDK, scanner, sync, MCP tools — appends `/api/v1` itself. No consumer accepts a path-bearing URL.

## Identity rules

A process is identified by PID plus process start time. A session uses a Soleaux-generated stable ID and an extensible provider identity. Attribution decisions must retain their method, confidence, and evidence.

Attribution precedence:

1. Explicit inherited session metadata
2. Registered session root
3. Attributed ancestor
4. Tool registration
5. Process group or terminal
6. Repository/time correlation
7. Heuristic
8. Unattributed

Lower-confidence evidence may not overwrite stronger evidence.

## Security defaults

- Daemon binds to loopback only.
- Browser access is routed through the dashboard proxy.
- Process-control operations are out of scope for the first framework slice.
- Environment values and unredacted credentials are never persisted.
- Remote proxy exposure must default to read-only.

## UI framework

The dashboard renders on `@soleaux/ui`. Premium Shadcn Studio assets may be integrated later under their license; reusable primitives belong in `telemetry/ui`, Soleaux-specific compositions in `telemetry/dashboard`.

## Next implementation slices

1. Long-lived process collector replacing per-request refresh.
2. SQLite history, retention, and deterministic drain rules.
3. Durable tool-execution correlation and process-sample history.
4. Subscription quota capture where providers expose no supported API.
5. Premium UI shell, charts, tables, sheets, and settings.
