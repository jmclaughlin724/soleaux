# Soleaux local implementation runbook

This document is the local handoff for turning the GitHub scaffold into a tested macOS-first product. Complete the sections in order. Do not expose the daemon beyond loopback during local development.

## 1. Prerequisites

Install:

- Node.js 20 or newer
- pnpm 10.33.4
- Rust stable with Cargo
- Python 3.12 and `uv`
- Xcode command-line tools on macOS

Verify:

```bash
node --version
pnpm --version
rustc --version
cargo --version
python3 --version
uv --version
```

## 2. Install dependencies

From the repository root:

```bash
pnpm install
cargo fetch --manifest-path native/daemon/telemetry/Cargo.toml
```

## 3. Validate the current scaffold

```bash
pnpm exec turbo run typecheck test:unit --filter="@soleaux/*" --filter=soleaux-dashboard
pnpm soleaux:telemetry:daemon:check
pnpm exec ultracite check tools/soleaux/telemetry
```

`pnpm --filter soleaux-dashboard build` currently fails on this machine with a `/_global-error` prerender error that reproduces on every app in the repository; treat it as a pre-existing environment issue, not a telemetry defect, until it is resolved upstream. Fix all lint and type errors before adding features.

## 4. Run the local stack

Terminal 1:

```bash
pnpm soleaux:telemetry:daemon
```

Confirm:

```bash
curl http://127.0.0.1:43120/api/v1/health
curl http://127.0.0.1:43120/api/v1/system
```

Terminal 2:

```bash
pnpm soleaux:telemetry:dashboard
```

Open `http://127.0.0.1:43121` and confirm the SSE status is Live.

Terminal 3, launch a monitored session:

```bash
pnpm soleaux:telemetry:cli codex
# or
pnpm soleaux:telemetry:cli claude
```

Confirm the session appears in `/api/v1/sessions`, then run a CPU-producing child command and verify the descendant is attributed to the correct session.

## 5. Replace the prototype collector

The current daemon uses `sysinfo` and rebuilds a full system snapshot on each request. Implement a persistent collector service with:

- one long-lived `System` instance
- one-second CPU and memory refresh
- cached process metadata
- process start and exit diffing
- PID plus start-time identities
- parent relationship history
- bounded broadcast channels for SSE consumers
- backpressure and dropped-event counters

Recommended modules:

```text
native/daemon/telemetry/src/
  config.rs
  state.rs
  api.rs
  collector/mod.rs
  collector/macos.rs
  attribution.rs
  aggregation.rs
  alerts.rs
  storage.rs
  redaction.rs
```

## 6. Add SQLite persistence

Use WAL mode and migrations. Suggested tables:

```text
hosts
sessions
session_events
processes
process_relationships
process_attributions
process_samples
session_samples
tool_executions
alerts
settings
```

Required behavior:

- write raw one-second samples for 30 minutes
- compact into ten-second samples for 24 hours
- compact into one-minute samples for 30 days
- retain session and alert summaries
- recover active sessions and orphan candidates after daemon restart
- never persist full environment values

## 7. Implement macOS-native collection

Use `sysinfo` only as fallback. Add native collection for:

- process executable path
- parent PID and process group
- resident and virtual memory
- CPU time and thread count
- disk reads and writes
- open listening ports on demand
- process start time with sufficient precision

Measure collector overhead while idle and under 1,000+ processes. Target less than 1% idle CPU and less than 75 MB resident memory.

## 8. Harden session attribution

Implement precedence exactly:

1. explicit `ANILIZE_SESSION_ID`-equivalent Soleaux environment marker
2. registered root identity
3. persisted descendant relationship
4. tool execution registration
5. process group
6. terminal
7. working directory and timing heuristic
8. unattributed

Rename all remaining environment variables to the `SOLEAUX_` namespace. Never let a heuristic overwrite explicit attribution.

Add tests for:

- identical commands in the same repository
- two providers in one terminal application
- parent shell exit
- detached server
- daemon restart
- PID reuse
- shared language server

## 9. Add tool execution instrumentation

Create a tool-execution API and SDK hooks:

```text
POST /api/v1/tool-executions
POST /api/v1/tool-executions/:id/processes
POST /api/v1/tool-executions/:id/end
```

Support `SOLEAUX_TOOL_EXECUTION_ID`. Add Claude Code and Codex adapters only in provider-specific packages; keep the daemon provider-neutral.

## 10. Complete the dashboard

Import licensed premium Shadcn Studio source locally and preserve its license requirements. Integrate into `apps/soleaux-dashboard` and `packages/ui` rather than adding a nested template project.

Complete:

- premium collapsible shell and responsive navigation
- overview statistics and sparklines
- session-stacked resource timeline
- process table with virtualization and locked live sorting
- process tree
- tool-execution page
- alert investigation sheet
- history pages
- orphaned-process page
- settings and privacy controls
- loading, empty, disconnected, and error states

Do not commit premium license keys, downloaded archives, or credentials.

## 11. Add safe process controls

Process controls must remain disabled until these checks exist:

- require PID and exact process start time
- verify identity immediately before action
- reject daemon, dashboard, and protected system processes
- separate terminate and force-kill permissions
- require confirmation in the UI
- audit every action locally
- disable all controls when dashboard access is remote

## 12. Complete MCP integration

The MCP server must remain a client of the daemon. Add protocol version checks, timeouts, structured errors, and read-only tools. Do not expose kill or arbitrary command execution through MCP in the initial release.

## 13. Local acceptance suite

A local release candidate is ready only when:

- three simultaneous sessions remain independently filterable
- descendant totals reconcile with session totals
- attribution survives intermediate parent exit
- daemon restart recovers persisted state
- PID reuse tests pass
- dashboard reconnect creates no duplicates
- secrets are redacted
- collector overhead meets budget
- MCP and dashboard totals match
- all build, lint, type-check, unit, integration, and UI tests pass
