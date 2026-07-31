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

## Current state (verified 2026-07-31)

- `github.com/jmclaughlin724/soleaux` exists; `main` is published (PRs #1/#2
  plus follow-up CI fixes). History was squashed on merge; the local baseline
  is no longer `90fe924`.
- The concurrent MCP-gateway program (GW-1–30: OAuth backends, credentials,
  health tracking, metrics, canonical policy rendering, `soleaux mcp
  login/logout/status/doctor`, daemon + dashboard registry) has landed and is
  committed on both sides.
- Gates green at `8b18c34`: 1136 passed/2 skipped pytest (with
  `SOLEAUX_HOST_ROOT`), ruff/pyright clean, turbo 11/11, cargo clean,
  verify-upstream clean, sdist/wheel build clean (deterministic
  `build_identity.json` via committer-date stamping).
- Process-teardown hardening landed: health-probe cancelled-mid-connect reap
  (`src/soleaux/mcp_health.py`), SIGTERM→SIGINT graceful shutdown in the
  server lifespan (`src/soleaux/server.py`), and load-margin bounds in the
  process-inventory/topology tests.
- Known upstream (fastmcp 4.0.0b1, latest available): proxy `_disconnect`
  re-raises the session task's `MCPError` through connection cleanup, and
  `fastmcp run` ignores stdin EOF. Worth filing; no newer release exists.
- Stage 1 (B) and Stage 2 repo-internal work (C1–C5) are complete; the
  vendored bridge remains as a shim until the host cutover (C6).

---

## A. Loose ends from the extraction (small, mechanical)

### A1. Repair the `@soleaux/docs` workspace membership ✅ (2026-07-31)
- [x] `pnpm-workspace.yaml` lists `"docs"` with catalog entries; lockfile
      reconciled (`pnpm install --frozen-lockfile` passes).
- [x] `pnpm --filter @soleaux/docs check:ci` and `audit` pass; docs audit
      step runs in this repo's `.github/workflows/ci.yml`.
- [x] anilize-temp CI no longer references `@soleaux/docs`.
- [x] Regenerated `tests/fixtures/contracts/d019-zero-mcp.json` after
      extraction drift.
Note: anilize-temp CI still runs duplicated product lanes
(`soleaux:lint/typecheck/test`, telemetry verify-upstream) through the
dangling symlink — that is A2/EX-9 territory, not A1.

### A2. Create the GitHub remote and wire host CI (partially done)
- [x] Remote created and `main` published by the user.
- [ ] anilize-temp workflows gain a checkout of soleaux adjacent to the repo
      (`../soleaux` relative to the workspace) so the committed
      `tools/soleaux -> ../../soleaux` symlink resolves, or the duplicated
      product lanes are deleted in favour of this repo's own CI (the
      consumption-model decision, anilize-temp EX-1, is user-gated).
Acceptance: anilize-temp CI passes on a fresh checkout.

### A3. Close the pyright gate on the bridge migration ✅ (moot)
- [x] `uv run --locked pyright` reports 0 errors; `scripts/soleaux/client.py`
      and `__tests__/` are committed and clean (the ~51-error state was
      resolved by the bridge workstream; C5 later migrated those tests into
      `tests/test_bridge.py`).

### A4. Commit both sides (mostly done)
- [x] anilize-temp: extraction + gateway host-side commits landed.
- [x] this repo: PRs #1/#2 + gateway + CI fix commits landed.
- [ ] this repo: Stage C working tree (bridge package, CLI subcommand,
      migrated tests, skill-copy repair, TASKS/HANDOFF refresh) — pending
      user-directed commit.

---

## B. Stage 1 — build/install identity ✅ (2026-07-31)

Goal: "which soleaux is running?" is answerable from any session.

- [x] B1. `scripts/build_identity_hook.py` (hatch `custom` hook) stamps
      `build_identity.json` (`{version, git_sha, build_time_utc, source:
      "wheel"}`); registered under `[tool.hatch.build.hooks.custom]`; sdist
      `force-include`s the hook; deterministic timestamp (SOURCE_DATE_EPOCH →
      existing artifact → committer date of HEAD) keeps direct and
      sdist-rebuilt wheels byte-identical.
- [x] B2. `src/soleaux/_identity.py`: wheel artifact → runtime git fallback;
      never raises; version chain metadata → pyproject → "unknown".
- [x] B3. `identity.build` in `describe` and `soleaux://about`.
- [x] B4. `service.mjs` `compareServiceIdentity` has `gitSha`/`installSource`
      parity bits (null-tolerant for pre-B payloads).
- [x] B5. `tests/test_identity.py` covers wheel contents, editable
      resolution, describe/about payloads; service.test.mjs parity shape
      updated.

## C. Stage 2 — productized bridge (C1–C5 ✅ 2026-07-31; C6 open)

Goal: consumer repos contain only config, never vendored bridge code.

- [x] C1. Bridge moved into the package: `src/soleaux/bridge/client.py`,
      `src/soleaux/bridge/rendering.py` (host-envelope contract owner),
      `src/soleaux/bridge/deployment.py` (discovery + validation),
      `src/soleaux/contracts/deployment.py` (schema constants).
      `scripts/soleaux/client.py` is now a back-compat shim;
      `scripts/soleaux/http_service.py` imports from the package.
- [x] C2. `soleaux bridge <claude|codex|opencode>` serves stdio;
      `soleaux bridge --context <client>` emits the host context payload.
      Deviation from the plan: `soleaux context <client>` was not used
      because `soleaux context <objective>` already exists; the mode lives
      under the bridge namespace instead.
- [x] C3. Discovery order: `SOLEAUX_DEPLOYMENT` (legacy alias
      `SOLEAUX_DEPLOYMENT_CONFIG`) → repo walk-up
      (`scripts/soleaux/deployment.json`, then `soleaux.deployment.json`) →
      `~/Library/Application Support/Soleaux/deployment.json`.
- [x] C4. Schema validation against `contracts/deployment.py`; unsupported
      schemas raise a typed error naming `soleaux attach --repo <path>`.
- [x] C5. `tests/test_bridge.py` (migrated, pyright-strict) plus
      discovery-order and schema-mismatch tests; old
      `scripts/soleaux/__tests__/test_client.py` deleted.
- [ ] C6. Cut anilize-temp over (user-directed, host repo): `.mcp.json`,
      `.codex/config.toml`, `opencode.json`, and
      `.codex/hooks/UserPromptSubmit/soleaux_context.py` invoke the installed
      CLI; delete the vendored script copies; update
      `scripts/soleaux/RUNBOOK.md` owner table and host `package.json`.
Acceptance so far: `soleaux bridge --context codex` works from the package
against the live anilize-temp deployment (env-override and repo-walk-up
discovery both verified); all gates green (1141 passed/2 skipped).


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
