# Kimi MCP Playbook

Sources verified 2026-07-30:

- https://www.kimi.com/code/docs/en/kimi-code-cli/customization/mcp.html
- Kimi Code CLI 0.30.0 binary, for the merge order marked below

## Intent

`.kimi-code/mcp.json` is this repository's Kimi MCP registry, the sibling of `mcp_servers` in `.codex/config.toml`. It is tracked, so a change here reaches every clone. Read this before adding a server, changing a timeout, or moving a declaration between platforms.

## Scopes and Merge Order

Two files, both named `mcp.json`:

- User: `$KIMI_CODE_HOME/mcp.json` (default `~/.kimi-code/mcp.json`).
- Project: `<cwd>/.kimi-code/mcp.json`.

Binary-verified: declarations load from the user file first and then the project file, where "entries in later files override earlier files." A project entry therefore wins under the same server name. That is the intended lever for pinning a repository server without touching a contributor's personal registry, and it is also how a personal entry silently stops applying.

## Transports

| Shape | Selected by                                                       |
| ----- | ----------------------------------------------------------------- |
| stdio | a `command` field; the CLI launches the server as a child process |
| HTTP  | a `url` field with no `transport`; preferred for new servers      |
| SSE   | `transport: "sse"`; legacy HTTP + Server-Sent Events              |

```json
{
  "mcpServers": {
    "soleaux": {
      "args": ["scripts/soleaux/client.py", "bridge", "claude"],
      "command": ".venv/bin/python",
      "type": "stdio"
    },
    "remote": {
      "url": "https://example.invalid/mcp",
      "bearerTokenEnvVar": "EXAMPLE_TOKEN"
    }
  }
}
```

## Fields

| Field | Applies to | Purpose |
| --- | --- | --- |
| `env` | stdio | environment for the child process |
| `cwd` | stdio | working directory for the child process |
| `headers` | HTTP, SSE | static request headers |
| `bearerTokenEnvVar` | HTTP, SSE | **name** of the env var holding the token |
| `enabled` | all | default `true` |
| `startupTimeoutMs` | all | connection and discovery, 1–2147483647, default 30000 |
| `toolTimeoutMs` | all | single tool call |
| `enabledTools` | all | allowlist |
| `disabledTools` | all | denylist |

`bearerTokenEnvVar` takes a variable name, not a value — unlike the declared-provider credential fields in [`providers-and-interop.md`](../config/providers-and-interop.md#credentials), which store literals. It is the only indirection available inside a config file, so it is the only correct way to authenticate a tracked server entry. Never inline a token in `mcp.json`.

## Timeouts

Global defaults live in `config.toml`:

```toml
[mcp]
startup_timeout_ms = 30000
tool_timeout_ms = 300000
```

or in `KIMI_MCP_STARTUP_TIMEOUT_MS` and `KIMI_MCP_TOOL_TIMEOUT_MS`. Precedence is per-server field, then environment variable, then `config.toml`, then built-in default.

## Tool Naming and Permissions

Tools surface as `mcp__<server>__<tool>`, for example `mcp__soleaux__context`. Permission patterns accept wildcards — `mcp__soleaux__*` for one server, `mcp__*` for all MCP tools. MCP and custom tools match **by name only**; argument patterns are not available, so a rule cannot distinguish a read call from a mutating call on the same server.

That limitation matters here: `soleaux` exposes read projections and the `preview`/`edit` mutation pair under one namespace. A blanket `mcp__soleaux__*` allow grants the mutation path too. Scope rules to the exact tool when the intent is read-only.

## Commands

- `/mcp` — connection status for every server.
- `/mcp-config` — add, edit, or delete servers interactively.
- `/mcp-config login <server-name>` — complete an OAuth authorization.

MCP OAuth credentials land in `$KIMI_CODE_HOME/credentials/mcp/` and are **not** cleared by `/logout`; remove that directory to revoke them.

## Repository Delivery

1. Change `.kimi-code/mcp.json` and nothing else — do not hand-copy entries between it and `.codex/config.toml`. The two registries are separate owners with separate formats.
2. Keep tokens out of the file; use `bearerTokenEnvVar`.
3. Verify with `/mcp` in a fresh session, not by re-reading the file.
4. Onboard an unfamiliar server the same way the Codex lane requires — see the staging procedure in [`../../codex/tools/mcp.md`](../../codex/tools/mcp.md). Read-only tools and non-sensitive fixtures first; mutation only after the permission model is understood.
