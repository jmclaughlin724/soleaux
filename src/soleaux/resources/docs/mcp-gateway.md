---
title: Use the MCP gateway
description: Register, authenticate, and troubleshoot MCP server backends through the Soleaux gateway — one registry in soleaux.toml, namespaced tools, and CLI-mediated OAuth.
sidebar:
  label: MCP gateway
  order: 9
---

Soleaux is the MCP gateway between your host and every MCP server you use. Each enabled `[mcp.<name>]` entry in `soleaux.toml` becomes one namespaced proxy provider on the Soleaux server, so a host connects once to Soleaux and reaches every configured backend through it.

## The registry model

`soleaux.toml` is the single registry. Host MCP configurations (`.mcp.json`, `.codex/config.toml`, and equivalents) hold only the Soleaux bridge registration; they never list backend servers directly. Backend tools arrive through the gateway namespaced as `<backend>_<tool>` — a `github` backend's `create_issue` tool appears as `github_create_issue`.

The `soleaux://mcp/v1` resource lists registered backends with their lifecycle, auth mode, and live health for agent-facing consumption.

Agents never edit host MCP configurations and never propose per-host registrations. When a backend is missing, propose the `[mcp.<name>]` block for `soleaux.toml` and let a human apply it.

## Choose a lifecycle

Each backend declares one lifecycle mode:

- `on_demand` (default) creates a fresh backend client per provider operation. Best for rarely used backends, command or URL.
- `session` retains one backend client per connected downstream session and closes it with that session. Requires a command backend; best for interactive stdio servers used throughout a session.
- `shared` retains one backend client across all sessions for the server lifetime. Requires a URL backend explicitly declared with `stateless = true`; best for stateless HTTP servers where connection reuse eliminates per-call initialization overhead.

The validator rejects invalid combinations: `session` with a URL, or `shared` without `stateless = true`. `cache_ttl_seconds` (default 300, maximum 300) bounds tool-catalog reuse so protocol-mandated `tools/list` calls do not repeatedly start command-backed providers.

## Understand the auth model

Each URL backend declares one auth mode:

| Mode | Fields | Behavior |
| --- | --- | --- |
| `none` (default) | — | No credentials; optional `headers_from_env` for non-secret headers. |
| `bearer_env` | `auth_token_env` | Sends the named environment variable's value as the bearer token. The config holds the variable name, never the token. |
| `oauth` | `oauth_scopes`, `oauth_client_name`, `oauth_client_metadata_url`, `client_id_env`, `client_secret_env`, `token_store` | Full OAuth flow with persistent tokens. URL backends only. |

OAuth client registration follows the MCP authorization priority of CIMD over pre-registered credentials over dynamic client registration:

1. `oauth_client_metadata_url` (an HTTPS URL with a non-root path) selects client ID metadata document registration.
2. `client_id_env` plus optional `client_secret_env` selects pre-registered client credentials held in named environment variables.
3. With neither, the gateway uses dynamic client registration against the server.

### Token storage

OAuth tokens persist in a per-backend store so one backend's tokens are never visible to another. The default `token_store = "disk"` writes one directory per backend under the platformdirs user data directory (`mcp-tokens/<backend>`), created mode 0700 as the access guard. `token_store = "keyring"` opts into the operating-system keyring under the `soleaux` service name. Tokens never live under the worktree and never appear in logs.

### CLI-mediated login

The daemon never launches a browser. When an OAuth backend needs interactive authorization, the gateway fails the call with an error naming the exact command instead of opening a redirect. `soleaux mcp login <name>` runs the OAuth flow in your foreground shell against the same token store the daemon reads, so one login serves every later gateway call.

### What an agent does on an auth failure

When a backend call fails because the backend is not authenticated, do not retry it and do not loop. Tell the user to run `soleaux mcp login <backend>` in their shell, then retry the call only after they confirm the login completed.

## Add a backend

Humans edit `soleaux.toml`. An agent's role is to propose the block and, after it lands, verify it.

A plain URL backend with a bearer token:

```toml
[mcp.remote]
url = "https://mcp.example.com/mcp"
auth = "bearer_env"
auth_token_env = "EXAMPLE_MCP_TOKEN"
lifecycle = "on_demand"
```

An OAuth-protected URL backend with pre-registered client credentials and keyring storage:

```toml
[mcp.work]
url = "https://mcp.work.example.com/mcp"
auth = "oauth"
oauth_scopes = ["read", "write"]
client_id_env = "WORK_MCP_CLIENT_ID"
client_secret_env = "WORK_MCP_CLIENT_SECRET"
token_store = "keyring"
lifecycle = "shared"
stateless = true
```

A command backend:

```toml
[mcp.local]
command = ["backend-server", "--stdio"]
lifecycle = "session"
```

A backend must set exactly one of `command` or `url`. Command backends may set literal `env` values and a workspace-contained relative `cwd`. URL backends require HTTPS except for loopback HTTP, and read bearer tokens, custom headers, and custom CA paths only from named environment variables.

After adding a backend, verify it:

```sh
soleaux --root /path/to/repository mcp status
soleaux --root /path/to/repository mcp doctor
```

`mcp status` shows each backend's transport, lifecycle, and auth state, including whether an OAuth backend holds stored tokens. `mcp doctor` probes liveness by connecting and listing tools; it never triggers interactive auth, and reports `run soleaux mcp login <name>` for an unauthenticated OAuth backend. `soleaux check mcp --probe` runs the same probe alongside host-config consistency checks.

## Set tool policy

The `[policy]` section owns per-backend tool approval effects. Each policy backend must be declared under `[mcp]`; tool keys are the unprefixed tool names the backend exposes. Effects are `allow`, `ask`, and `deny`, with `ask` as the default when a backend or tool has no entry. Wildcards are not supported — `default` is the only per-backend fallback.

```toml
[policy.backends.github]
default = "ask"

[policy.backends.github.tools]
search_repositories = "allow"
create_issue = "ask"
delete_repository = "deny"
```

`soleaux.toml` owns policy effects. Host approval surfaces — Codex approval modes, host permission rules, and equivalents — are rendered output derived from this section, not independent sources of truth. Live backend membership is unknowable at config-load time, so only the shape of tool entries is validated; a stale tool key takes effect again if the backend re-exposes that name.

## Troubleshoot backends

**A backend call reports it is not authenticated.** Run `soleaux mcp status` to confirm, then `soleaux mcp login <name>` for OAuth backends. For `bearer_env` backends, confirm the named environment variable is set and nonempty in the daemon's environment.

**A command backend fails its probe.** `soleaux mcp doctor` reports the startup error. Confirm the command resolves on `PATH`, that declared `env` values are literal and NUL-free, and that `cwd` is a relative path contained in the workspace.

**TLS failures on a URL backend.** Verification is on by default (`tls_verify = true`) and may be disabled only for loopback URLs. For a private CA, set `tls_ca_file_env` to the name of an environment variable holding the CA file's absolute path; the file must resolve to a regular file.

**Where tokens live.** Disk tokens sit under the platformdirs user data directory in `mcp-tokens/<backend>/`, one mode-0700 directory per backend; keyring tokens sit in the OS keyring under the `soleaux` service. Nothing is stored in the repository.

**Revoke access.** `soleaux mcp logout <name>` clears one backend's stored tokens from the configured store. The next gateway call to that backend fails unauthenticated until a fresh `soleaux mcp login <name>`.
