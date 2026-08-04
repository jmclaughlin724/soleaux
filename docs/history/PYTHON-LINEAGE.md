# Python/FastMCP Lineage Mapping

## Historical role

The public Python lineage established many product semantics before native unification:

- ten-tool fixed local catalog;
- typed `soleaux.context/v1`;
- ranked SQLite search;
- LSP navigate/inspect;
- hash-bound preview/edit;
- governance/owners;
- namespaced gateway and OAuth;
- skills provider;
- adopt workflow;
- Next.js and PostgreSQL discovery;
- packaging and host bridge concepts.

## Native absorption

| Historical capability | Unified owner |
|---|---|
| `describe` | `repo_info` + about resource |
| `search` | `code.search` |
| `context` | `context.compile` |
| `query` | `registry.read` |
| `owners` | `registry.read` + context governance |
| `navigate` | native `navigate` |
| `inspect` | native `inspect` |
| `preview` | native `preview` |
| `edit` | native `edit` |
| `restart_lsp` | native `restart_lsp` |
| gateway/OAuth | native gateway + CLI |
| skills/agents/rules | native registry |
| adopt/attach | native provisioning |
| governance | native governance graph |
| framework/SQL | native providers |

## Historical task mapping

| Old task area | Current phase |
|---|---|
| extraction/build identity/bridge | completed history, Phase 0–2 |
| attach | Phase 2 |
| shared per-machine service | Phase 5 |
| watch/restart | Phase 5/6 |
| consumer onboarding | Phase 5 |
| public release | Phase 8 |
| host cleanup and docs | Phase 4/5 |

The old `TASKS.md` and `HANDOFF.md` must not be used to advance current phase status.
