# Anilize MCP foundation inventory summary

## Counts

| Disposition | Count |
| --- | ---: |
| Add | 1 |
| Update | 6 |
| Remove | 0 |
| Unchanged/excluded | 8 |
| Total | 15 |

## Execution allowlist

| Disposition | Path | Task |
| --- | --- | --- |
| Update | `tools/anilize-mcp/src/anilize_mcp/server.py` | `W1-T10` |
| Update | `tools/anilize-mcp/tests/test_server.py` | `W1-T10` |
| Add | `tools/anilize-mcp/tests/test_stdio.py` | `W2-T21` |
| Update | `tools/anilize-mcp/pyproject.toml` | `W2-T20` |
| Update | `package.json` | `W2-T20` |
| Update | `.github/workflows/ci.yml` | `W3-T30` |
| Update | `tools/anilize-mcp/AGENTS.md` | `W5-T50` |

Every other repository path is excluded. In particular, the 683 rows recorded in [`ambient-worktree-manifest.json`](./ambient-worktree-manifest.json) are protected user work and must not be staged, restored, removed, reformatted, or used for change attribution.

## Preserved boundaries

- One root uv workspace, root `.venv`, root `.python-version`, and root `uv.lock`.
- One `anilize-mcp` package pinned to FastMCP `3.4.4`.
- One exported composition root and one stdio console entrypoint.
- One existing declarative `fastmcp.json` profile.
- Zero feature components and zero feature providers.
- Separate remote OAuth server configuration remains unchanged.
- No route, database, migration, dependency, generated repository output, deployment, or host-registration change.

## Wave order

1. `W0-T00` — completed research and scope freeze.
2. `W1-T10` — server identity, security defaults, and explicit stdio.
3. `W2-T20` — installed-package import and locked commands.
4. `W2-T21` — real console/profile subprocess contracts.
5. `W3-T30` — lock, inspect, artifact, and wheel smoke CI gate.
6. `W4-T40` — adversarial verification, including failure-oriented probes.
7. `W5-T50` — final canonical boundary update; no task follows it.

Execution is serialized with one primary agent. The full metadata, preimage hashes, procedures, acceptance evidence, and protected ambient ledger are in [`task-list.md`](./task-list.md), [`fastmcp-foundation-manifest.json`](./fastmcp-foundation-manifest.json), and [`ambient-worktree-manifest.json`](./ambient-worktree-manifest.json).
