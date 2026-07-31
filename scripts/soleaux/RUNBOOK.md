# Soleaux workspace service runbook

A workspace deployment runs Soleaux as one long-lived macOS service owned by `scripts/soleaux/`: `service.mjs` is the service controller and `http_service.py` composes the workspace HTTP surface (stateless Streamable HTTP, proxy providers, skills). The launchd label, socket path, and served workspace come from the deployment config (`scripts/soleaux/deployment.json` for this repository's own deployment, or a consumer-supplied config via `--config <path>` / `SOLEAUX_DEPLOYMENT_CONFIG`; `deployment.example.json` is the consumer template). The service listens on a current-user-owned private Unix-domain socket (directory `0700`, socket `0600`, unsafe occupants rejected) and exposes no TCP listener; filesystem permissions are the access control, so there are no credentials to provision, rotate, or leak.

## Control the service

Use the pnpm scripts from the repository root:

```sh
pnpm service:status     # health, identity, and parity between source and runtime
pnpm service:verify     # drive the real hook path and assert one bounded context packet
pnpm service:install    # install or repair the service
pnpm service:restart    # restart the service
pnpm service:uninstall  # remove the service
```

Each is `node scripts/soleaux/service.mjs <command>`; pass `--config <path>` after the command to target another workspace's deployment.

`status` reports whether the installed composition matches the checked source, whether the socket endpoint is reachable, and a `recovery` hint when the service needs repair. `verify` exercises the host hook path end to end and asserts exit 0, one bounded `additionalContext` packet, and required-section completeness.

## Session freedom

Any AI agent in any session may call any Soleaux tool at any time. The service composes stateless HTTP: every request is independent, and the service keeps no per-host MCP session state. Product state lives only on the service side — lifecycle-published catalog generations, service-owned LSP sessions, and hash-bound edit preimages.

Restarting the service is safe for in-flight agents. A restart interrupts only an in-progress request; it never invalidates a session, because there are no sessions to invalidate. Agents do not need a fresh task, reconnect, or re-handshake after a restart.

## Correlate failures with a service boot

`pnpm service:status` reports `identity.live.processEpoch`, a per-boot identifier that changes on every service restart. The same value is published in the service's `describe` identity and `soleaux://about` resource. Use it for restart forensics: a log entry or agent-reported failure belongs to the current boot only while the epoch stays constant. The epoch carries no session or staleness semantics — with stateless HTTP there is nothing to go stale — it exists purely for operational correlation.

## Read the service logs

The controller writes launchd logs named after the deployment's service label:

- `~/Library/Logs/Soleaux/<service-label>.stdout.log`
- `~/Library/Logs/Soleaux/<service-label>.stderr.log`

Unexpected auth rejections, provider respawn lines, or tracebacks in these logs are defects to report, not normal operation.

## Canonical owners

| Concern | Canonical owner |
| --- | --- |
| Product boundary and session-freedom invariant | `AGENTS.md` |
| Operator runbook and error taxonomy (this file) | `scripts/soleaux/RUNBOOK.md` |
| Stateless transport composition and service control | `scripts/soleaux/http_service.py` and `scripts/soleaux/service.mjs` |
| Host bridge and context client | `scripts/soleaux/client.py` and `scripts/soleaux/__tests__/` |
| Product catalog, tools, and resources | `src/soleaux/server.py` |
| Derived guidance and packaged product docs | `scripts/generate_guidance.py` and `src/soleaux/resources/docs/` |
| Workspace service deployment config | The deploying workspace's `deployment.json` (schema: `deployment.example.json`) |

Secondary surfaces defer to these owners; they do not restate them.

## Error taxonomy

Every code a host or operator can observe, with its one corrective action.

**Codex pre-prompt hook** (`.codex/hooks/UserPromptSubmit/soleaux_context.py` in the consuming workspace, exits 2 with `source=` and `code=`):

| Code | Meaning | Corrective action |
| --- | --- | --- |
| `invalid_input` | Malformed hook payload | Retry from a fresh Codex task with a valid payload |
| `repository_unavailable` | `cwd` not resolvable to a Git worktree | Run the prompt from inside the repository worktree |
| `invalid_configuration` | `.codex/config.toml` Soleaux entry drifted | Repair `mcp_servers.soleaux` in `.codex/config.toml` and restart Codex |
| `dependency_unavailable` | Client runtime missing | Run `uv sync --locked` in the Soleaux checkout and retry from a fresh task |
| `context_unavailable` | Socket or transport failure on the context call | Run `pnpm service:status`, repair the service, and retry |
| `context_invalid` | Context packet exceeded the host byte limit | Report the renderer bound as a defect; retry from a fresh task |
| `unexpected_failure` | Unclassified owner failure | Inspect the focused hook tests and retry from a fresh task |

**Host client** (`scripts/soleaux/client.py`, exits 2 with `soleaux-client:` on stderr):

| Message | Meaning | Corrective action |
| --- | --- | --- |
| `deployment config could not be loaded` / `must be an object` / `unsupported schema` | The deployment config is unreadable or not `soleaux.local-deployment/v2` | Point `SOLEAUX_DEPLOYMENT_CONFIG` at a valid config, then run `pnpm service:install` |
| `{label} must be a nonempty string` | A required deployment field is empty | Repair the deployment config and run `pnpm service:install` |
| `a nonempty task objective is required on stdin` | The `context` command received an empty prompt | Pass a nonempty objective on stdin |
| `endpoint must be a credential-free http://… URL` | Endpoint drifted from the socket form | Repair the deployment config and run `pnpm service:install` |
| `socket_relative_path must stay relative…` / `exceeds the AF_UNIX limit` | Unsafe socket path in deployment config | Repair the deployment config and run `pnpm service:install` |
| `[host_context_packet_invalid]` | Service returned an invalid v1 context envelope | Report as a service defect; run `pnpm service:status` |
| `[host_context_packet_unavailable]` | Service returned no task-context packet | Run `pnpm service:status`, repair, and retry |
| `[host_context_limit]` | Required sections exceed the host byte envelope | Report the renderer bound as a defect |
| `the Soleaux request failed` | Unclassified request failure | Run `pnpm service:status` and retry |

**Service envelopes**: tool results carry `status: "ok"` or `status: "error"` with a typed code (for example `context_response_too_large` when a bounded context cannot fit the caller's `max_bytes`). An `error` status is data for the caller, not a transport failure; only a repeated typed failure after `pnpm service:verify` passes warrants a defect report.
