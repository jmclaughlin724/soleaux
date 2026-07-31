# FastMCP ecosystem deep-research + repo gap analysis

## Context

The user invoked `/deep-research` asking to investigate the entire FastMCP ecosystem and identify gaps in this repo's implementation — which features we're not fully using, which we use that could be used at a higher level — with the intent of simplifying and optimizing the repo.

Exploration (completed) established the ground truth to research against:

- **What we run:** `tools/anilize-mcp/` — Python `fastmcp[anthropic,apps,code-mode,openai,tasks]==3.4.4` (gofastmcp / jlowin) on Python ≥3.14, over `mcp==1.28.1`. No TS MCP server (the `@modelcontextprotocol/sdk` in `pnpm-lock.yaml` is only a shadcn transitive dep).
- **Coverage today:** the server already exercises almost the entire 3.4.4 surface — tools/resources/templates/prompts with versioning, annotations, structured output, sampling (client + Anthropic/OpenAI server fallback), elicitation (all shapes incl. URL-mode), progress, ctx logging/state, all built-in middleware, StaticToken/JWT/Remote auth + component `AuthCheck`s, providers/mount/proxy, `from_openapi`, transforms (ToolTransform, VersionFilter, ResourcesAsTools, PromptsAsTools, CodeMode, BM25/Regex search), custom routes, composed lifespans, tag filtering, DI, SEP-1686 tasks on a custom Supabase runtime, MCP Apps (prefab-ui), fastmcp Client (all transports), CLI + 7 `*.fastmcp.json` profiles, in-memory test transport.
- **Deployment:** stdio local, self-hosted uvicorn ASGI, and managed **Horizon** (FastMCP Cloud, `anilize.fastmcp.app`) which owns OAuth in `managed` auth mode (`server.py::create_auth_provider` returns `None`).
- **Complexity hotspots (candidate simplifications):**
  1. `repository.py` (~1,870 lines) + `scripts/anilize-mcp/repository-index.mjs` (~707 lines): hand-built BM25 code-intelligence engine with Python→Node subprocess bridge + ast-grep subprocess; conceptually overlaps FastMCP's own BM25/Regex tool-search transforms.
  2. `task_runtime.py` (~1,542 lines): custom Docket-on-Supabase task backend (`SupabaseFastMCP`, monkeypatches `fastmcp.server.tasks.routing/elicitation`) replacing stock memory/Redis task storage.
  3. Env-config parsing duplicated in spirit across `server.py`, `deployment.py`, `middleware.py`, `remote_security.py` (same boolean/int/JSON-array/URL validators re-implemented 3–4×).
  4. Config sprawl: 8 launch profiles / 4 catalog modes, several of which are demonstration paths (`sse` legacy, `full-catalog`, `code-mode`).

## Approach

Run the `deep-research` workflow (already user-opted-in via `/deep-research`) with a refined, repo-grounded question, then synthesize its verified findings against the exploration facts above into a single gap/optimization report. **No code edits in this pass** — the deliverable is the report with a prioritized recommendation list; applying simplifications is a follow-up the user can request per-item.

### Step 1 — Launch the deep-research workflow

`Workflow({ name: "deep-research", args: <refined question> })` where the refined question directs the five search angles at:

1. **Version delta:** FastMCP releases/changelog after 3.4.4 (current latest as of July 2026), new features, deprecations, breaking changes, and whether `mcp` (official SDK) 1.28.1 is behind; MCP spec revisions / SEPs adopted since (beyond SEP-1686 tasks and SEP-1330 titled elicitation already in use).
2. **Horizon / FastMCP Cloud:** platform capabilities beyond OAuth termination (env/secret management, scaling, telemetry, task workers, event replay, custom domains) we may be under-using or redundantly self-implementing.
3. **Higher-level usage of features we already touch:** recommended production patterns and maturity for progressive tool discovery / search transforms, CodeMode, background tasks + Docket backends, MCP Apps / prefab-ui, sampling & elicitation — what "using it well" looks like vs. demo-level usage.
4. **Native replacements for hand-rolled subsystems:** official/community task-store backends for Docket (Postgres?), pydantic-settings-style config for FastMCP servers, built-in repo/code-search or indexing offerings vs. our bespoke `repository.py` pipeline, py-key-value-aio ecosystem status.
5. **Ecosystem & integrations:** state of fastmcp extras (`anthropic`, `openai`, `apps`, `code-mode`, `tasks`), pydantic-ai MCP integration, OpenAI Deep Research MCP contract (our `search`/`fetch` pair), testing/CI tooling, and any features we don't use at all (worth confirming: anything genuinely absent from the inventory above).

### Step 2 — Cross-check load-bearing claims

Before trusting version/feature claims in the synthesis, spot-verify the highest-impact ones against primary sources (gofastmcp.com docs / GitHub releases via WebFetch, Context7 for API docs) and against the repo's pins in `tools/anilize-mcp/pyproject.toml` and `uv.lock`.

### Step 3 — Synthesize the final report

Merge workflow output + repo ground truth into one cited report with these sections:

- **Features not (fully) used** — genuinely absent or demo-level surfaces, each with what adopting it would buy us.
- **Features used that could be used at a higher level** — e.g. replacing bespoke repository search with native search transforms if parity exists; letting Horizon own more (event replay, telemetry); consolidating catalog modes.
- **Simplification opportunities (repo-grounded)** — prioritized: (a) env-config consolidation into one settings layer (lowest risk), (b) repository indexer pipeline (highest surface), (c) Supabase task runtime (justified only if Postgres-authoritative tasks are a hard requirement — flag for user decision), (d) pruning demonstration launch profiles.
- **Version/upgrade considerations** — if >3.4.4 exists: what an upgrade unlocks/breaks; note `tools/anilize-mcp/AGENTS.md` names `pyproject.toml`/`uv.lock` as API authority and `.agents/skills/fastmcp/**` (18 reference docs) would need updating in any follow-up.

Deliver the report in the final chat message and save a copy to the session scratchpad (not the repo).

## Files involved (read-only this pass)

- `tools/anilize-mcp/pyproject.toml`, `uv.lock` — version authority
- `tools/anilize-mcp/src/anilize_mcp/{server,repository,task_runtime,deployment,middleware,providers,transforms}.py` — cited in findings
- `tools/anilize-mcp/README.md`, `tools/anilize-mcp/AGENTS.md`, `.agents/skills/fastmcp/**` — doc surfaces any follow-up must keep in sync

## Verification

- The workflow's own adversarial-verify phase (3-vote refutation per claim) gates web claims.
- Version and API-surface claims re-checked against gofastmcp.com / GitHub releases and the repo's `uv.lock` pins before appearing in the report.
- Every repo-side gap claim must cite a concrete file path from the exploration inventory (no "we don't use X" without having confirmed X is absent from the inventory).
