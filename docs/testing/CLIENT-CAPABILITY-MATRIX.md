# Client capability and version matrix

Status: Phase 5 implementation contract for P5-002 through P5-006.  This document describes supported client mechanisms and evidence. No external client receives a read-write Soleaux workspace binding until a daemon-trusted admission receipt verifier is implemented.

The machine-readable authority is [`native/contracts/client-capability-matrix-v1.json`](../../native/contracts/client-capability-matrix-v1.json).  The daemon embeds that file and publishes its SHA-256 digest in registry status. Probe reports are archival evidence, not runtime authorization. Unknown versions, documentation-only surfaces, stale or malformed evidence, and all external clients remain read-only.

## Safety classification

| Platform | Matrix version | Phase task | Probe coverage | Soleaux write mode |
| --- | --- | --- | --- | --- |
| Claude Code | `2.1.223` | P5-002 | Pinned CLI version/help/MCP commands plus official memory, rules, skills, subagent, hook, and MCP documentation | Denied pending authenticated runtime probe |
| Claude Desktop | supported current surface | P5-003 | Official remote-connector and account-data export/import documentation | Denied; documentation-contract surface only |
| Codex CLI / app-server | `0.146.1` | P5-004 | Pinned CLI version/help/app-server commands plus official app-server protocol documentation | Denied pending authenticated app-server lifecycle probe |
| OpenCode | `1.18.14` | P5-005 | SHA-256-pinned Linux release binary plus HTTP/OpenAPI/SSE/plugin/rule/agent documentation | Denied pending authenticated HTTP/SSE lifecycle probe |
| Cursor CLI / editor | supported current surface | P5-006 | Official rules, MCP, hooks, CLI, and session documentation; no moving installer is executed in evidence CI | Denied; documentation-contract surface only |
| Generic MCP host fixture | `mcp-2025-11-25` | P5-006 | Native initialize, tools/list, context.compile, registry registration, binding rejection, and twelve-tool-ceiling smokes | Denied; conformance evidence is archival until trusted receipt admission exists |

The table is not a production-readiness claim. `productionClaimAllowed` remains `false`, and the public MCP ceiling remains twelve.

## Mechanism map

### Claude Code

- Persistent project guidance: `CLAUDE.md` and `.claude/rules`.
- User and project memory: `CLAUDE.md` plus auto-memory.
- Reusable skills: `.claude/skills` and plugin skills.
- Agent delegation: isolated subagents from `.claude/agents` and supported plugin scopes.
- Lifecycle interception: settings and plugin hooks.
- Repository tooling: project, user, and managed MCP configuration.
- Native continuity: Claude Code resume and fork remain platform-native; Soleaux does not claim unsupported hosted session CRUD.

The pinned binary is probed for its version, top-level command surface, and MCP command.  These signals prove the expected executable surface, not authenticated mutation behavior.

### Claude Desktop

- Remote custom connectors and prebuilt connectors are supported user-facing integration surfaces.
- Account data export includes user and chat-history export workflows.
- Desktop extensions and supported local connectors are treated as configuration/materialization targets.
- No direct hosted session or hosted memory CRUD API is assumed.

Because the supported surface is documentation- and UI-driven, this matrix entry is permanently read-only until a separately approved executable or supported API probe is available.

### Codex CLI and desktop app-server

- Project instructions: `AGENTS.md` and Codex configuration.
- Canonical native thread operations: start, resume, fork, list, read, and archive.
- App-server transport: JSONL over stdio, with separately documented experimental WebSocket and Unix-socket modes.
- Approvals: command and file-change approval flows.
- Catalog surfaces: skills and apps.

The exact `0.146.1` package is probed for version, help, and `app-server --help`.  Write access remains disabled until an authenticated app-server run proves approvals, steering, compaction, archive, cursors, reconnect, and safe-mode behavior.

### OpenCode

- Configuration: project and global OpenCode configuration.
- Agents: primary agents, subagents, and parallel general-agent execution.
- Rules: `AGENTS.md` and rules files.
- Extensions: TypeScript and JavaScript plugins.
- Transport: HTTP server, OpenAPI surface, and server-sent events.
- Session lifecycle: fork, abort, summarize, revert, and event reconciliation.

The Linux x64 `1.18.14` release archive is pinned by SHA-256 before its version/help/server commands are executed.  This does not grant write mode without authenticated HTTP/SSE lifecycle evidence.

### Cursor and generic MCP hosts

Cursor is represented by a documentation-only supported-surface contract for rules, MCP configuration, hooks, CLI behavior, and native session history.  The evidence workflow does not execute Cursor's moving remote installer, and the entry remains read-only until a checksum-pinned version and authenticated lifecycle oracle are approved.

No external matrix entry is mutation eligible. The generic MCP fixture proves the
locked conformance surface against compiled Soleaux binaries, including initialization,
the exact twelve-tool profile, Context Packet V2, registry registration, and rejection
of caller-self-attested read-write admission. Its compact registration proof stays below
the IPC field limit, while full command output remains in the archival report.

The only current write-capable client path is the exact internal Soleaux CLI identity.
External write admission requires a future daemon-trusted, cryptographically bound receipt
verifier and a separately reviewed lifecycle oracle; a caller-computed hash is never
accepted as authorization.

## Probe and evidence files

- `native/scripts/validate_client_capability_matrix.py` validates task coverage, versions, official sources, pinned assets, client kinds, and locked invariants.
- `native/scripts/probe_client_capabilities.py` streams and hashes bounded argv-only output, applies signal-specific oracles, and separates compact registration evidence from archival output.
- `native/scripts/p5_client_matrix_smoke.py` proves external read-write rejection, internal CLI write access, heartbeat revalidation, and the locked registry/matrix invariants.
- `.github/workflows/client-capability-matrix.yml` executes the client tracks independently and aggregates their evidence.

## Updating a client version

A version change requires all of the following in one reviewed change:

1. Update the machine-readable matrix and official source evidence.
2. Run the pinned binary probe for that exact version.
3. Recompute and publish the matrix digest through the daemon.
4. Run Rust tests, the binary registry smoke, and the full repository CI.
5. Keep `mutationEligible=false`; enabling external writes additionally requires a daemon-trusted cryptographic receipt verifier and an authenticated task-specific lifecycle oracle.

A version number alone never authorizes mutation.
