# Soleaux — persistent implementation task list

Owner of this list: the soleaux product repo (`~/projects/soleaux`). A new session
should work top-down, keep each task independently verifiable, and update the
checkboxes as tasks complete. Do not work around a failing gate — fix the cause
at its canonical owner.

## Ground rules (apply to every task)

- Python deps mutate only through `uv add` / `uv remove` / `uv lock`.
- Gates: `uv run --locked ruff check .`, `uv run --locked ruff format --check .`,
  `uv run --locked pyright`, `uv run --locked pytest`, `cargo check --locked
  --manifest-path telemetry/daemon/Cargo.toml`, `pnpm exec turbo run typecheck
  test:unit`, `node telemetry/scripts/verify-upstream.mjs`.
- Host-dependent tests require `SOLEAUX_HOST_ROOT=<host checkout>`; standalone,
  they skip explicitly via `tests/_host_root.py`.
- One canonical owner per contract. No duplicate owners, re-export barrels,
  compatibility aliases, or pass-through wrappers.
- The sibling repo `anilize-temp` consumes this checkout through the
  `tools/soleaux -> ../../soleaux` symlink; the host's `SOLEAUX_HOST_ROOT` is
  `/Users/johnmclaughlin/projects/anilize-temp`.
- Commits are made by the user unless they explicitly delegate delivery.

## Current state (verified 2026-07-30)

- Product tree extracted; `~/projects/soleaux` is a git repo (initial commit
  `90fe924`) with its own `uv.lock`, `.venv`, `pnpm-workspace.yaml`, lockfile,
  and self-hosted ast-grep engines (`node_modules/@ast-grep/*`).
- Standalone gates green: 801 passed/18 skipped pytest; ruff/pyright clean;
  telemetry turbo 7/7; cargo check clean; sdist/wheel build clean.
- Through the symlink, host-side gates green: 968 passed/2 skipped pytest;
  service `dev.soleaux.anilize-temp` healthy; self-dogfood deployment
  `dev.soleaux.soleaux` exists (`scripts/soleaux/deployment.json`).
- Bridge/service layer is ported in **script form** at `scripts/soleaux/`
  (client.py, service.mjs, http_service.py, deployment.json, RUNBOOK.md); a
  concurrent workstream is mid-migration on it — coordinate before editing
  those files.
- Known red gate: `uv run --locked pyright` reports ~51 errors confined to
  `scripts/soleaux/client.py` and `scripts/soleaux/__tests__/` (the concurrent
  bridge migration). Not a blocker for Stages 1–2 below but must be closed
  before any release.

---

## A. Loose ends from the extraction (small, mechanical)

### A1. Repair the `@soleaux/docs` workspace membership
`tools/soleaux/docs` (now `docs/`) was dropped from the anilize-temp pnpm
workspace and is not yet in this repo's. anilize-temp `ci.yml:81` still runs
`turbo run audit --filter=@soleaux/docs` and fails.
- [ ] Add `"docs"` to `packages` in `pnpm-workspace.yaml`.
- [ ] Add the catalog entries `docs/package.json` needs (blume, astro, and any
      other existing deps) to this repo's `catalog:` with the exact versions
      from `anilize-temp/pnpm-workspace.yaml`.
- [ ] Run `pnpm install --no-frozen-lockfile` in `~/projects/soleaux` and
      `pnpm --filter @soleaux/docs audit` (or the package's own check task).
- [ ] Move the docs-audit step out of anilize-temp CI into this repo's
      `.github/workflows/ci.yml` (or delete it there once moved).
Acceptance: `pnpm --filter @soleaux/docs <its tasks>` passes from this repo;
anilize-temp CI no longer references `@soleaux/docs`.

### A2. Create the GitHub remote and wire host CI (requires user authorization)
- [ ] User creates `github.com/jmclaughlin724/soleaux` and this repo is pushed
      (`git push --set-upstream origin main`).
- [ ] anilize-temp workflows gain a checkout of soleaux adjacent to the repo
      (`../soleaux` relative to the workspace) so the `tools/soleaux` symlink
      resolves, or soleaux-dependent lanes are gated on that checkout existing.
Acceptance: anilize-temp CI passes on a fresh checkout.

### A3. Close the pyright gate on the bridge migration
Owner: the bridge workstream. Track, don't absorb.
- [ ] Resolve the ~51 pyright errors in `scripts/soleaux/client.py` and
      `scripts/soleaux/__tests__/` (fastmcp 4.0.0b1 API surface:
      `httpx_client_factory` signature, `StreamableHttpTransport`, unknown
      member/variable types).
Acceptance: `uv run --locked pyright` reports 0 errors.

### A4. Commit both sides (user-directed)
- [ ] anilize-temp: symlink swap, `pnpm-workspace.yaml`, `package.json`,
      `eslint.config.mjs`, ast-grep rule exemptions (r080, r094), `ci.yml`,
      removal of `.github/workflows/soleaux-release.yml`, `soleaux.toml`
      `[telemetry]`, deleted `scripts/soleaux/`.
- [ ] this repo: everything since `90fe924`.

---

## B. Stage 1 — build/install identity

Goal: "which soleaux is running?" is answerable from any session.

- [ ] B1. Add a hatch build hook `scripts/build_identity_hook.py` that writes
      `src/soleaux/resources/build_identity.json`
      (`{version, git_sha, build_time_utc, source: "wheel"}`) at `uv build`;
      register it in `pyproject.toml` under `[tool.hatch.build]`.
- [ ] B2. New `src/soleaux/_identity.py`: resolve identity at runtime —
      build_identity.json when present (wheel/sdist/uvx); otherwise
      best-effort `git rev-parse HEAD` + `install_source = "editable"`; never
      raises; falls back to version-only.
- [ ] B3. Surface under `identity.build` in `describe`
      (`src/soleaux/analysis/service.py`) and in the `soleaux://about` product
      block (`src/soleaux/server.py`): `{version, git_sha, install_source,
      python}`.
- [ ] B4. Extend `scripts/soleaux/service.mjs` identity parity
      (`compareServiceIdentity`) to compare `gitSha` desired-vs-live so
      `service.mjs status` flags editable-tree drift the static version hides.
- [ ] B5. Tests: `tests/test_identity.py` (wheel contains identity; editable
      resolution; describe/about payloads), plus service.test.mjs parity case.
Acceptance: `uv build` produces a wheel containing build_identity.json with
the current git sha; `describe` returns it; `service.mjs status` shows gitSha
parity/drift. All gates from Ground rules pass.

## C. Stage 2 — productized bridge

Goal: consumer repos contain only config, never vendored bridge code.
Coordinate with the in-flight bridge workstream before editing
`scripts/soleaux/**`.

- [ ] C1. Move `scripts/soleaux/client.py` into the package as
      `src/soleaux/bridge/client.py` (+ `rendering.py` for the host-envelope
      contract — that contract's canonical owner moves with it).
- [ ] C2. Add CLI subcommands in `src/soleaux/cli.py`: `soleaux bridge
      <claude|codex|opencode>` and `soleaux context <client>`.
- [ ] C3. Deployment discovery order replaces path-relative lookup:
      (1) `SOLEAUX_DEPLOYMENT` env var, (2) `<repo>/scripts/soleaux/deployment.json`
      (v2, legacy per-repo), (3) machine-level
      `~/Library/Application Support/Soleaux/deployment.json`.
- [ ] C4. Bridge validates envelope/schema versions against
      `soleaux/contracts/` constants at startup; mismatches raise a typed
      error naming the repair command.
- [ ] C5. Migrate `scripts/soleaux/__tests__/test_client.py` into
      `tests/test_bridge.py`; add discovery-order and version-mismatch tests.
- [ ] C6. Cut anilize-temp over: `.mcp.json`, `.codex/config.toml`, and
      `.codex/hooks/UserPromptSubmit/soleaux_context.py` invoke
      `.venv/bin/soleaux bridge|context`; delete the vendored script copies;
      update `scripts/soleaux/RUNBOOK.md` owner table and the host root
      `package.json` (`soleaux:workspace:test` paths).
Acceptance: `soleaux bridge codex|claude` works from the package; host agents
connect through it end-to-end (`pnpm soleaux:service:verify` drives the real
hook path); vendored copies gone; `uv run --locked pytest` green including
migrated bridge tests.

## D. Stage 3 — `soleaux attach` onboarding

Goal: one re-runnable command owns the consumer-integration shape.

- [ ] D1. New `src/soleaux/provisioning/attach.py` sibling to `adopt.py`
      (reuse `mcp_writer.py` tomlkit/json5 writers, `backup.py`,
      detect→plan→consent→apply pattern). `soleaux attach [--repo <path>]
      [--shared] [--dry-run] [--yes]`.
- [ ] D2. Writes/repairs idempotently in a consumer repo:
      `[mcp_servers.soleaux]` in `.codex/config.toml` (command shape only —
      never approval-mode keys), `mcpServers.soleaux` in `.mcp.json`, starter
      `soleaux.toml` if absent (lift `_generate_soleaux_toml` from cli.py into
      provisioning so both callers share it), and deployment registration
      (v2 per-repo now; machine registry after Stage E).
- [ ] D3. Ends by running the `check mcp` consistency logic and printing the
      validation route.
- [ ] D4. Tests `tests/test_attach.py`: plan/apply idempotency, dry-run,
      refusal on unknown extras, backup creation.
Acceptance: dry-run against anilize, cleat-chasers, supaschema, anilize-temp
inspected; apply into a scratch repo + `soleaux check mcp --root <p>` passes;
docs regenerated (`scripts/generate_guidance.py`).

## E. Stage 4 — shared per-machine service

Goal: one launchd service serves every consumer repo; per-repo v2 mode stays
supported throughout. Largest stage — keep sub-stages independently shippable.

- [ ] E1. (4a) Machine registry `~/Library/Application Support/Soleaux/workspaces.json`
      (schema `soleaux.workspace-registry/v1`, one canonical owner in the
      package, mutated only by attach/detach). `SoleauxService.from_registry(path)`
      loads each workspace's own `soleaux.toml`. Refactor `SoleauxService` /
      `AnalysisFrameBuilder` to hold `dict[workspace_id, ResolvedConfig]`;
      config reads become per-workspace at the selection point. Registry
      changes require restart (frozen-at-launch semantics preserved).
- [ ] E2. (4b) Port `scripts/soleaux/http_service.py` composition into
      `src/soleaux/http.py` generalized over `--root` (per-repo) or
      `--registry` (shared). deployment.json v3 adds `mode` + `workspace`;
      new `src/soleaux/contracts/deployment.py` becomes the single schema
      owner (kills the client.py/service.mjs dual-parse). Bridge injects
      `workspace_id` into calls that omit one and validates its workspace
      against the service's `workspace_ids` at startup.
- [ ] E3. (4c) Port `scripts/soleaux/service.mjs` semantics
      (install/status/restart/verify, plist rendering, socket state,
      identity parity) into `src/soleaux/service/` as `soleaux service <cmd>`;
      repo constants become deployment.json parameters. Delete `service.mjs`;
      host `soleaux:service:*` scripts re-point to `uv run soleaux service ...`;
      migrate service.test.mjs to pytest equivalents.
Acceptance: per-repo v2 regression green; `soleaux service install --shared`
serves anilize-temp and soleaux workspaces over one socket with parity status;
bridge smoke from each consumer; full pytest green including a
per-repo-vs-shared envelope parity test.

## F. Stage 5 — watch-mode auto-restart

Goal: edit in this repo propagates to every consumer within seconds, no
manual restart.

- [ ] F1. The composition (`src/soleaux/http.py`) starts a watcher when
      `install_source == "editable"` (Stage B identity) or `--watch`: every
      ~2s re-fingerprint (git HEAD + bounded mtime digest of `src/soleaux/**`
      + the registry file in shared mode); on change exit code 1.
- [ ] F2. launchd `KeepAlive.SuccessfulExit=false` (already in plist
      templates) relaunches the service; `soleaux service status` reports
      live fingerprint vs tree fingerprint.
- [ ] F3. `tests/test_watch_restart.py` (fake clock / tmp tree).
Acceptance: editing a docstring in `src/soleaux/server.py` shows a new
process epoch/gitSha in `service status` without manual restart; no
in-process reload is attempted (SQLite/LSP safety — full restart only).

## G. Stage 6 — rejection record + optional dynamic versions

- [ ] G1. Document in `docs/` (and packaged guidance via
      `scripts/generate_guidance.py`): pinned-sha uvx and rolling `@latest`
      are rejected for soleaux dogfooding (bump toil / blast radius); the
      shared service + one editable install is the supported mechanism;
      uvx-from-PyPI remains the external release channel.
- [ ] G2. Optional: `hatch-vcs` derived versions for dev builds
      (`0.1.0.devN+g<sha>`); do not block on it — Stage 1 identity already
      closes the operational gap.

## H. Consumer onboarding

Blocked on Stages C–E. Order: anilize-temp (already self-hosted) → anilize →
cleat-chasers → supaschema.
- [ ] H1. `soleaux attach --repo <path>` per consumer (dry-run reviewed first).
- [ ] H2. Register each in the machine registry (Stage E) and smoke
      `soleaux context claude` through the shared socket from each repo.
- [ ] H3. supaschema gets its supaschema/telemetry surfaces validated in CI
      (its own repo gates, not this one).

## I. Small follow-ups

- [ ] I1. Wire the `.codex/hooks` vitest suite into this repo's CI (add
      vitest + a `hooks:test` script; include in `check:ci`).
- [ ] I2. anilize-temp: update `scripts/soleaux/RUNBOOK.md` and AGENTS.md
      agent-surface table after Stage C cutover.
- [ ] I3. Delete superseded host artifacts after each stage's cutover (vendored
      bridge, `service.mjs`, per-stage legacy scripts) — verify no consumer
      references remain first (`rg` the host repo).
