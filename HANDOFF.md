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

## Verified baseline (observed, not inferred)

- `~/projects/soleaux` is a git repo (initial commit `90fe924`) with its own
  `uv.lock`, `.venv`, `pnpm-workspace.yaml`, lockfile, and self-hosted
  `@ast-grep` engines. `~/projects/anilize-temp/tools/soleaux` is a symlink
  to it.
- Standalone gates green (2026-07-30): `uv run --locked pytest` 801
  passed/18 skipped; ruff and pyright clean; `pnpm exec turbo run typecheck
  test:unit` 7/7; `cargo check --locked` clean; `uv build` sdist/wheel clean.
- Host side through the symlink: `SOLEAUX_HOST_ROOT=$PWD uv --directory
  tools/soleaux run --locked --package soleaux pytest` → 968 passed/2 skipped;
  service `dev.soleaux.anilize-temp` healthy after restart+verify.
- Known red: `uv run --locked pyright` → ~51 errors, all in
  `scripts/soleaux/client.py` and `scripts/soleaux/__tests__/` (a concurrent
  bridge-migration workstream's files — tracked as TASKS A3, do not absorb).
- Bridge/service exists in script form at `scripts/soleaux/` (client.py,
  service.mjs, http_service.py, deployment.json, RUNBOOK.md); self-dogfood
  deployment `dev.soleaux.soleaux` is registered.

## Scope and exclusions

- In scope: TASKS.md items A1–A4, B–G, H, I, in order unless a dependency
  forces otherwise.
- Excluded: `scripts/soleaux/client.py` and `scripts/soleaux/__tests__/` while
  the concurrent bridge workstream is mid-migration (A3 is theirs); anilize-temp
  app code outside the soleaux surface; the 13 copied environment dirs
  (`.agents`, `.claude`, `.codex`, `.github`, `.husky`, `.kimi-code`, caches)
  beyond what TASKS.md names.
- Commits are the user's unless they explicitly delegate delivery.

## Assumptions and blockers

- A GitHub remote for `~/projects/soleaux` does NOT exist yet — A2 requires
  the user to create `github.com/jmclaughlin724/soleaux` before host CI can be
  wired. Do not push anywhere without explicit authorization.
- anilize-temp CI currently dangles on the `tools/soleaux` symlink in fresh
  checkouts until A2 lands.
- `SOLEAUX_HOST_ROOT=/Users/johnmclaughlin/projects/anilize-temp` is required
  for host-dependent tests; standalone they skip by design.

## Next executable action

Start with **A1**: add `"docs"` to `packages` in
`/Users/johnmclaughlin/projects/soleaux/pnpm-workspace.yaml`, add the catalog
entries `docs/package.json` needs (exact versions from
`anilize-temp/pnpm-workspace.yaml`), run `pnpm install --no-frozen-lockfile`
in `~/projects/soleaux`, then `pnpm --filter @soleaux/docs audit`. Then
proceed to Stage B (build/install identity) per TASKS.md.

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
- If a task collides with the concurrent bridge workstream's files, stop and
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
