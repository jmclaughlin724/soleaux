# Cold-start handoff — execute ~/projects/soleaux/TASKS.md

## Objective and mode

Continue the accepted Soleaux extraction and dogfooding program from its
verified state. Mode: **Implement** — make the in-scope changes itemized in
`/Users/johnmclaughlin/projects/soleaux/TASKS.md` and run the named
non-destructive validation. Do not expand scope beyond that list without
asking.

## Instruction sources (read first, they outrank this document)

- `/Users/johnmclaughlin/projects/soleaux/TASKS.md` — the itemized task list
  (A1–A4 loose ends, B–G dogfooding stages, H consumers, I follow-ups).
- `/Users/johnmclaughlin/projects/soleaux/AGENTS.md` — soleaux product
  boundary and contracts.
- `/Users/johnmclaughlin/projects/anilize-temp/AGENTS.md` — host repo rules
  (uv workspace ownership, one canonical owner per contract, no duplicate
  owners/aliases, validation through owning tasks only).
- `telemetry/docs/UPSTREAM_VERIFICATION_POLICY.md` — claim-registry rules for
  the telemetry surface.

## Verified baseline (observed, not inferred, 2026-07-31)

- `github.com/jmclaughlin724/soleaux` exists; `main` is published (PRs #1/#2
  plus follow-up commits). The concurrent MCP-gateway program (OAuth
  backends, credentials, health tracking, metrics, policy rendering, `soleaux
  mcp login/logout/status/doctor`, daemon + dashboard registry) has landed
  and is committed on both sides.
- Gates green at `8b18c34` plus the Stage C working tree: `uv run --locked
  pytest` 1141 passed/2 skipped (with `SOLEAUX_HOST_ROOT`); ruff and pyright
  clean; `pnpm exec turbo run typecheck test:unit` 11/11; `cargo check
  --locked` clean; `node telemetry/scripts/verify-upstream.mjs` clean; `uv
  build` sdist/wheel clean (deterministic `build_identity.json`).
- Stages A1, A3, B, and C1–C5 are complete (see TASKS.md for per-item
  state). The vendored bridge at `scripts/soleaux/` is now a shim over
  `src/soleaux/bridge/`; deletion happens at C6.
- Process-teardown hardening landed (health-probe reap, SIGTERM→SIGINT
  graceful shutdown, load-margin bounds in the process tests). Two upstream
  fastmcp 4.0.0b1 issues remain worth filing: proxy `_disconnect` re-raises
  the session task's `MCPError` through connection cleanup; `fastmcp run`
  ignores stdin EOF.

## Scope and exclusions

- In scope: TASKS.md items A2 (host CI wiring), C6 (host cutover,
  user-directed), D–G, H, I, in order unless a dependency forces otherwise.
- Excluded: anilize-temp app code outside the soleaux surface; the 13 copied
  environment dirs (`.agents`, `.claude`, `.codex`, `.github`, `.husky`,
  `.kimi-code`, caches) beyond what TASKS.md names.
- Commits are the user's unless they explicitly delegate delivery.

## Assumptions and blockers

- anilize-temp CI dangles on the committed `tools/soleaux -> ../../soleaux`
  symlink in fresh checkouts; the fix (adjacent checkout vs deleting
  duplicated product lanes) rides on the user-gated consumption-model
  decision (anilize-temp EX-1), which also shapes Stage H.
- C6 and Stage H touch the host repo and require user direction.
- `SOLEAUX_HOST_ROOT=/Users/johnmclaughlin/projects/anilize-temp` is required
  for host-dependent tests; standalone they skip by design.

## Next executable action

Stage D (`soleaux attach`): new `src/soleaux/provisioning/attach.py` sibling
to `adopt.py` — see TASKS.md D1–D4. Before that, the Stage C working tree
needs its user-directed commit.

## Validation (run after each task, through these owners)

- `uv run --locked ruff check . && uv run --locked ruff format --check .`
- `uv run --locked pyright`
- `uv run --locked pytest` (add `SOLEAUX_HOST_ROOT=/Users/johnmclaughlin/projects/anilize-temp` for host coverage)
- `cargo check --locked --manifest-path telemetry/daemon/Cargo.toml`
- `pnpm exec turbo run typecheck test:unit`
- `node telemetry/scripts/verify-upstream.mjs`
- Per-stage acceptance criteria are written into each TASKS.md section —
  meet those exactly and report commands + outcomes.

## Failure and stop behavior

- Investigate any unexpected failure until explained, reproduced, or shown
  unrelated; do not bypass a gate to get green.
- If a task collides with another workstream's uncommitted files, stop and
  surface it rather than racing the edit.
- Ask the user before: external writes (remotes, pushes, PRs), deleting
  another session's in-flight work, or materially expanding scope.
- Stop after the requested stage completes and its validation is reported;
  do not roll into the next stage silently on a long run — report stage
  boundaries.

## Output and done criteria

Per task: changed files, exact validation commands and observed results,
skipped/unavailable checks named, remaining blockers. The program is complete
when TASKS.md items A–I are checked off with their acceptance criteria met,
gates green on both sides of the symlink, and anilize, cleat-chasers, and
supaschema are attached through the shared service.
