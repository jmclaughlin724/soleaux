# Anilize MCP greenfield foundation task list

This file is the authoritative, repository-local execution record for rebuilding the local `anilize-mcp` foundation. Codex `update_plan` is only the session-visible status mirror; it is not the persistent record.

- Execution lens: elegant
- Elegant end state: one installed, release-pinned FastMCP 3.4.4 application with one registration-only composition root, one explicit stdio transport, one shared root uv environment, an empty feature catalog, deterministic security defaults, and tests that prove both source and built-artifact launch paths.
- Status: ready for execution; implementation has not started.
- Active implementation task: none. At execution start, set `W1-T10` to `in_progress` and mirror it in `update_plan`.
- Execution model: one primary agent, serialized writes, maximum concurrent agents `1`; subagents are not authorized by this task list.
- External-contract boundaries: preserve `https://anilize.fastmcp.app/mcp` as a separate remote OAuth service; preserve `fastmcp.json` as the local declarative stdio profile; do not register a local Codex/Claude host, add HTTP, or add a feature provider.
- Open assumptions: none (all validated).
- Open questions: none.
- Placeholders / TODOs: none.
- Deferral budget: zero. Every task in this record is part of this workstream; excluded surfaces are not tasks.

## Research Record

Research is complete and is recorded as `W0-T00`.

- Live runtime: Python `3.14.6`, FastMCP `3.4.4`, MCP SDK `1.28.1`, installed from the repository root `.venv`.
- Package declaration: `tools/anilize-mcp/pyproject.toml` requires Python `>=3.14`, pins `fastmcp==3.4.4`, uses Hatchling, and exposes `anilize-mcp = "anilize_mcp.server:main"`.
- Workspace declaration: root `pyproject.toml` is a non-package uv workspace with `tools/anilize-mcp` as its only Python member; root `.python-version` is `3.14.6`; root `uv.lock` resolves the environment.
- Installed API evidence: `FastMCP` supports `version`, `on_duplicate`, `mask_error_details`, `strict_input_validation`, and `run(transport=..., show_banner=...)`; `StdioTransport` supports explicit command, args, environment, cwd, teardown, and stderr routing.
- Current catalog: `fastmcp inspect fastmcp.json --skip-env` reports one `anilize-mcp` server and zero tools, prompts, resources, and templates.
- Current policy defaults: the live root reports application version `3.4.4` instead of package version `0.1.0`, duplicate behavior `warn`, error masking `False`, flexible validation `False` as a strictness flag, and `main()` leaves transport selection to ambient settings.
- Current transport defect: console and declarative profile both serve stdio normally, but setting `FASTMCP_TRANSPORT=http` makes the console bind `127.0.0.1:8000` and causes a stdio client timeout.
- Proven policy behavior: `on_duplicate="error"` raises `ValueError`; `mask_error_details=True` returns a generic `ToolError` without the synthetic exception sentinel.
- Current quality baseline: Ruff passed, Ruff format check passed, strict mypy passed for four source files, pytest passed one test, and `uv lock --check` resolved 83 packages without changing the lock.
- Artifact baseline: `uv build --package anilize-mcp` produced an sdist and wheel in an isolated temporary directory; the wheel installed in a fresh uv environment and its `anilize-mcp` console script served a zero-tool stdio catalog.
- Consumer evidence: root `package.json` is the only verified local launcher. `.codex/config.toml` points to the separate remote OAuth URL. No local Claude server registration or tracked `.mcp.json` was found.
- Code-map evidence: `pnpm code-map:query` is unavailable because this repository has no `code-map:query` script and no `tools/code-map` owner. The failed command created no cache. Exact owner instructions, package graph, launch consumers, installed runtime probes, and direct file reads replace that unavailable lane.
- Documentation audit: the initial pass found no live repository or global `fastmcp` skill, so it removed a broken skill route while correcting the `ProxyProvider` contradiction and clarifying that the live catalog is empty. During final validation, `.agents/skills/fastmcp/` reappeared as protected concurrent work and validation stopped. On 2026-07-21 the user authorized a re-audit and rebase. The live skill now owns reusable FastMCP workflow, its `SKILL.md` is SHA-256 `9db62b9b3f3d3823326594d84b3015d0c1672b6a1a29d4a5f9f2ddafde9a286a`, and `node scripts/codex/audit-skills.mjs --skill fastmcp` passed one skill with zero warnings. The validator explicitly forbids `.agents/rules/**` as a skill-policy owner; root `AGENTS.md` delegates the package boundary to `tools/anilize-mcp/AGENTS.md`, which now routes material FastMCP work through the repository skill. Historical `plans/**` references remain protected evidence rather than active context consumers. No unimplemented constructor policy or `mcp:inspect` command was documented.
- Repository-state evidence: `tools/anilize-mcp` has 683 ambient status rows, including 46 staged deletions, 630 working-tree deletions, and two untracked live files. These changes are protected ambient work. The implementation allowlist below is exhaustive and forbids broad staging or restoration.
- Plan-state rebase: after artifact creation, the live `package.json` SHA-256 changed from `1e81ead84dc59e4ce01ec9fd62f7229f162263b170d5d7012b02ddb01947c77f` to `1e748a9a002f5ab1a5a8da4f8ec4a6f45cc59393ae57bd5027c567828461d143`. Preimage validation stopped, the user explicitly authorized rebasing the persistent plan, and a second inspection confirmed the four MCP command values were unchanged. On 2026-07-21 it changed again to `bd4d4a7c443046dab937c8e24c41b1dcea3b43e2282aeab6c31ad0fa550caeff` through concurrent TypeScript tooling work outside the MCP scripts. Validation stopped again, the user authorized this second rebase, and the four MCP command values were still unchanged. These rebases change only recorded preimages; they do not claim or modify the concurrent `package.json` work.
- Research tooling notes: five combined evidence or instruction-read batches truncated and were reread in bounded sections through EOF; one combined temporary artifact command was rejected by a pre-tool hook before execution and was rerun as explicit commands; one import probe briefly yielded and then completed successfully. During the first authorized rebase, one interactive patch process was terminated while awaiting input and two shell-wrapped patch attempts were rejected before execution; unchanged hashes proved that none mutated a file before the structured patch succeeded. A `/dev/fd`-dependent ambient comparison failed on environment permissions and its in-memory equivalent proved an exact 683-row match. One summary checker expected the wrong table labels; the corrected checker covered all seven actionable paths and five count rows. No conclusion relies on omitted or failed output.
- Subagents: not used for this task-list creation; local evidence plus the completed preceding FastMCP architecture review covered the required owner, consumer, upstream, runtime, security, and verification slices.

## Inventory Artifacts

- [`fastmcp-foundation-manifest.json`](./fastmcp-foundation-manifest.json) is the machine-readable change inventory and preimage gate.
- [`ambient-worktree-manifest.json`](./ambient-worktree-manifest.json) is the complete 683-row protected status ledger under `tools/anilize-mcp`.
- [`inventory-summary.md`](./inventory-summary.md) gives counts, owners, and the execution allowlist.
- No CSV is needed for the 15-row inventory.
- Generation method: exact live paths came from `find`, `git status --porcelain=v1`, `git ls-files --stage`, SHA-256 probes, direct owner reads, installed FastMCP inspection, and the package/workspace manifests. The ambient ledger was generated deterministically from the complete target-scoped porcelain output. Cache/build directories and all paths not listed as `add` or `update` are excluded.

## Resolved assumptions

1. **Workstream scope:** this record builds only the local empty stdio foundation. Evidence: `tools/anilize-mcp/AGENTS.md` defines that identity, the live catalog is empty, and no local consumer requires a feature component.
2. **Application version:** advertise the installed `anilize-mcp` distribution version (`0.1.0`), not FastMCP's library version. Evidence: current inspection reports `3.4.4` because the constructor omits `version`.
3. **Transport:** enforce `stdio` in `main()` and `fastmcp.json`; ambient `FASTMCP_TRANSPORT` must not change the console transport. Evidence: the failure-oriented runtime probe reproduced an HTTP bind.
4. **Duplicate and error policy:** set `on_duplicate="error"` and `mask_error_details=True`. Evidence: installed 3.4.4 defaults are `warn` and `False`; the installed policy probe confirmed the intended behavior.
5. **Input validation:** set `strict_input_validation=False` explicitly for LLM-client compatibility. Flexible coercion is deliberate and is not an authorization boundary; any later capability must use typed constraints and may require a separately approved strict-mode contract.
6. **Feature catalog:** keep all four catalogs empty. A first provider, provider namespace, data source, and mutation policy are absent because no consumer contract proves them.
7. **Environment:** retain one root uv `.venv` and one root `uv.lock`; do not create a package-local environment or another lockfile.
8. **Import behavior:** remove pytest's `pythonpath = ["src"]` so tests exercise the installed src-layout package. Keep mypy's `mypy_path` because it is a static-analysis setting, not runtime import injection.
9. **Launch ownership:** keep the console script as the canonical local launcher and `fastmcp.json` as the declarative profile. Add a locked inspection wrapper; do not add another launch profile.
10. **Dependency graph:** no dependency or extra changes are required. `uv.lock` must remain byte-identical.
11. **Execution safety:** a future execution may write only the manifest rows marked `add` or `update`, and only if every planned update path matches its recorded SHA-256 preimage immediately before the first write. Drift blocks execution; it is not permission to rebase, restore, or absorb concurrent work.
12. **Documentation:** the current update audit removed the `ProxyProvider` contradiction, clarified the empty catalog, and routes reusable implementation workflow through the validated repository `$fastmcp` skill without publishing future state. After implementation and adversarial verification, `W5-T50` updates only `tools/anilize-mcp/AGENTS.md` with the verified constructor and command contract.

## Instruction ledger

| Instruction | Acceptance mapping |
| --- | --- |
| Rebuild the foundation in `anilize-temp` using FastMCP best practices. | `W1-T10` through `W5-T50`, using installed 3.4.4 APIs and the repository FastMCP boundary. |
| Preserve the root uv workspace and shared environment model. | `W2-T20`; root `pyproject.toml`, `.python-version`, and `uv.lock` remain unchanged. |
| Make runtime and packaging behavior deterministic. | `W1-T10`, `W2-T20`, and `W3-T30`. |
| Prove real stdio behavior and package artifacts. | `W2-T21`, `W3-T30`, and `W4-T40`. |
| Keep the composition root provider-ready but empty. | `W1-T10`; no provider file or registration is added. |
| Keep security boundaries explicit. | Error masking, duplicate rejection, transport pinning, payload-free stdout, remote-service exclusion, and failure-oriented probes in `W1-T10`, `W2-T21`, and `W4-T40`. |
| Create a persistent task list for another session. | This folder is the authoritative file-backed fallback because no native persistent task-list tool is available. |

## Scope ledger

### Plan-owned implementation scope

- `tools/anilize-mcp/src/anilize_mcp/server.py`
- `tools/anilize-mcp/tests/test_server.py`
- `tools/anilize-mcp/tests/test_stdio.py`
- `tools/anilize-mcp/pyproject.toml`
- `package.json`
- `.github/workflows/ci.yml`
- `tools/anilize-mcp/AGENTS.md`

### Explicitly preserved or excluded

- Root Python owners: `pyproject.toml`, `.python-version`, and `uv.lock` remain unchanged.
- MCP profile: `tools/anilize-mcp/fastmcp.json` remains unchanged and is exercised as a real consumer.
- Empty package markers: `src/anilize_mcp/__init__.py` and `providers/__init__.py` remain unchanged.
- Remote and host configuration: `.codex/config.toml`, `.claude/settings.json`, remote deployment, OAuth, and host registration remain unchanged.
- All other `tools/anilize-mcp/**` paths, including predecessor deletions and index-only files, are excluded. Do not restore, stage, remove, or reclassify them.
- Routes, database objects, migrations, generated source, browser surfaces, external deployments, secrets, and runtime credentials: none are in scope.
- Temporary wheel/sdist and virtual-environment output may exist only under an explicit temporary directory or CI `$RUNNER_TEMP`; no build artifact is written into the repository.

## Change inventory

- `add`: 1 path.
- `update`: 6 paths.
- `remove`: 0 paths.
- `unchanged/excluded`: 8 paths.
- Complete rows, owners, current hashes, and task mappings are in `fastmcp-foundation-manifest.json`.
- Elegant effect: preserve the seven-file empty kernel, add one transport test, consolidate launch/check behavior around existing owners, and remove only contradictory guidance. Do not recreate predecessor modules or compatibility paths.

## Enforcement-surface ledger

| Standard | Canonical runtime owner | Enforcement surfaces |
| --- | --- | --- |
| Stdio-only console | `server.py` | `test_stdio.py`, `fastmcp.json`, `package.json`, CI subprocess tests |
| Application identity/version | `server.py` | `test_server.py`, FastMCP inspect gate |
| Duplicate rejection | `server.py` | Synthetic duplicate registration test |
| Client error masking | `server.py` | Synthetic failing-tool Client test |
| Installed src-layout imports | `tools/anilize-mcp/pyproject.toml` | pytest without `pythonpath`, wheel install smoke |
| Locked root commands | `package.json` | `uv lock --check`, CI wrappers |
| Provider-ready empty catalog | `server.py` and `AGENTS.md` | Client catalog assertions and inspect gate |
| Provider/no-proxy ownership boundary | `AGENTS.md` | Final exact stale-term check; no generated mirror applies |

No repository hook, generated projection, migration, route, package dependency, or external rollout surface is added.

## Execution protocol

1. Read this file and the manifest before every wave. Mirror the active task in `update_plan` with exactly one `in_progress` task while execution is underway.
2. Confirm the seven `add`/`update` paths still match the manifest's expected state. If any path drifted, mark the active task blocked and request direction; do not infer ownership from Git status.
3. Execute with one writer and no subagents. Do not run repository-wide formatters, generators, package managers, or staging commands.
4. Apply source edits with `apply_patch`. Preserve overlapping user changes exactly.
5. Run only the task-owned verification before changing task status.
6. Update this file's task status and evidence before advancing the `update_plan` mirror.
7. Never use `git add -A`, `git add tools/anilize-mcp`, reset, checkout, restore, clean, amend, or another broad Git operation. Staging or committing requires a separate explicit user instruction and an exact path allowlist.

## Wave 0 — completed evidence

### W0-T00 — Freeze the verified greenfield target

- status: `completed`
- subject: Freeze the verified greenfield target
- activeForm: Freezing the verified greenfield target
- wave: `0`
- blockedBy: `[]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: preserve only the local stdio profile and separate remote OAuth boundary
- requiredSkills: `task-creator`, `lightweight-explorer`, `code-map` (command unavailable and recorded), `elegant`, `fastmcp`, `python`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: resolve owners, consumers, installed APIs, package behavior, security defaults, dirty-worktree safety, and exact verification commands before implementation tasks exist.
- write scope: none.
- files: creates `[]`; modifies `[]`; removes `[]`; tests `[]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[]`; modifies `[]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp` inspected read-only.
- legacyDisposition: predecessor surfaces are evidence only and remain protected/excluded.
- completion evidence: Research Record, resolved-assumption ledger, live hashes, runtime probes, current baseline checks, and artifact smoke above.

Elegant effect: choose the seven-file empty kernel as the owner and reject the predecessor platform as an implementation template.

## Wave 1 — composition-root contract

### W1-T10 — Harden the registration-only stdio composition root

- status: `pending`
- subject: Harden the registration-only stdio composition root
- activeForm: Hardening the registration-only stdio composition root
- wave: `1`
- blockedBy: `[W0-T00]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: preserve the exported global `mcp`, package console script, empty catalog, and stdio profile; no feature compatibility layer
- requiredSkills: `elegant`, `fastmcp`, `python`, `test`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: make application identity, error policy, duplicate policy, validation posture, and transport deterministic in the sole composition owner.
- write scope: only `tools/anilize-mcp/src/anilize_mcp/server.py` and `tools/anilize-mcp/tests/test_server.py`.
- files: creates `[]`; modifies `[server.py, test_server.py]`; removes `[]`; tests `[test_server.py]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[]`; modifies `[console script behavior, fastmcp.json entrypoint behavior]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp`.
- legacyDisposition: keep no predecessor registrations, compatibility aliases, launch profiles, client APIs, or business logic.

Procedure:

1. Add `create_server() -> FastMCP`; use it once to export global `mcp`.
2. Set name `anilize-mcp`, package version from `importlib.metadata.version("anilize-mcp")`, the existing concise empty-catalog instructions, `on_duplicate="error"`, `mask_error_details=True`, and `strict_input_validation=False`.
3. Keep `server.py` registration-only. Do not add decorators, providers, middleware, lifespan, auth, tasks, storage, or HTTP code.
4. Make `main()` call `mcp.run(transport="stdio", show_banner=False)` under the existing direct-execution guard.
5. Expand `test_server.py` through `fastmcp.Client` to prove ping; all four catalogs empty; application version `0.1.0`; duplicate tool registration raises `ValueError`; and a synthetic normal exception containing a sentinel reaches the client only as a generic `fastmcp.exceptions.ToolError` without that sentinel.

Required verification:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_NO_CACHE=1 uv --directory tools/anilize-mcp run --no-sync --package anilize-mcp pytest -p no:cacheprovider tests/test_server.py
PYTHONDONTWRITEBYTECODE=1 UV_NO_CACHE=1 uv --directory tools/anilize-mcp run --no-sync --package anilize-mcp fastmcp inspect fastmcp.json --skip-env
```

Acceptance evidence:

- inspect reports server version `0.1.0` and zero tools, prompts, resources, and templates;
- the targeted pytest command exits successfully;
- only the two task-owned files changed.

Elegant effect: one fresh server factory owns every root invariant; no policy wrapper or compatibility path is introduced.

## Wave 2 — installed-package and subprocess contracts

### W2-T20 — Enforce installed imports and locked operator commands

- status: `pending`
- subject: Enforce installed imports and locked operator commands
- activeForm: Enforcing installed imports and locked operator commands
- wave: `2`
- blockedBy: `[W1-T10]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: preserve Hatchling, Python 3.14, the current direct dependencies, and existing command names
- requiredSkills: `elegant`, `python`, `fastmcp`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: ensure tests import the installed src-layout package and every canonical local MCP command refuses implicit lock changes.
- write scope: only `tools/anilize-mcp/pyproject.toml` and `package.json`.
- files: creates `[]`; modifies `[tools/anilize-mcp/pyproject.toml, package.json]`; removes `[]`; tests `[existing package tests]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[pnpm mcp:inspect]`; modifies `[pnpm mcp:dev]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[]`; modifies `[]`; removes `[]`.
- packages: root operator scripts and `anilize-mcp`.
- legacyDisposition: remove only pytest's source-path bypass; preserve all existing tool choices and command names.

Procedure:

1. Remove only `pythonpath = ["src"]` from pytest configuration. Keep `mypy_path = ["src"]`.
2. Add `--locked` to `mcp:dev` without changing its console-script target.
3. Add `mcp:inspect` as the sole new root wrapper: run the installed FastMCP CLI through the package workspace, inspect `fastmcp.json` with `--skip-env`, and do not write an output file.
4. Do not change dependencies, build backend, FastMCP version, Python version, `uv.lock`, pnpm lock, or any non-MCP script.

Required verification:

```bash
UV_NO_CACHE=1 uv lock --check
pnpm mcp:lint
pnpm mcp:typecheck
pnpm mcp:test
pnpm mcp:inspect
```

Acceptance evidence:

- all commands exit successfully;
- `uv.lock` remains SHA-256 `151379b57564a14cbf510397bb1dcd1d7b09df0071f53cfd046b8c86c8209d4d`;
- removing pytest's path injection does not break imports;
- only the two task-owned files changed.

Elegant effect: the installed package is the only runtime import path, and existing root scripts remain the single operator surface.

### W2-T21 — Add real stdio launch contracts

- status: `pending`
- subject: Add real stdio launch contracts
- activeForm: Adding real stdio launch contracts
- wave: `2`
- blockedBy: `[W1-T10]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: preserve both existing launch consumers while enforcing one stdio behavior
- requiredSkills: `fastmcp`, `python`, `test`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: prove the installed console script and declarative profile both speak protocol-clean stdio, shut down, and cannot be redirected to HTTP by ambient settings.
- write scope: create only `tools/anilize-mcp/tests/test_stdio.py`.
- files: creates `[test_stdio.py]`; modifies `[]`; removes `[]`; tests `[test_stdio.py]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[ANILIZE_MCP_EXECUTABLE test override]`; modifies `[console and fastmcp.json test coverage]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp`.
- legacyDisposition: do not add shell launchers, profiles, HTTP tests, or host-specific adapters.

Procedure:

1. Resolve the console executable from `ANILIZE_MCP_EXECUTABLE` when set; otherwise require `shutil.which("anilize-mcp")` to succeed.
2. Use `fastmcp.Client` with installed `StdioTransport`, `keep_alive=False`, the package directory as cwd, and a bounded `asyncio.timeout`.
3. Test the console script under its normal environment and with a copied environment containing `FASTMCP_TRANSPORT=http`; both must ping and expose four empty catalogs over stdio.
4. Test `fastmcp run fastmcp.json --skip-env --no-banner` from the package directory with the same ping, empty-catalog, timeout, and teardown contract.
5. Treat successful MCP framing as the stdout-cleanliness assertion. Server diagnostics may use stderr but must not appear as protocol data.

Required verification:

```bash
PYTHONDONTWRITEBYTECODE=1 UV_NO_CACHE=1 uv --directory tools/anilize-mcp run --no-sync --package anilize-mcp pytest -p no:cacheprovider tests/test_stdio.py
```

Acceptance evidence:

- three subprocess cases pass without timeout or orphaned process;
- the HTTP environment override still serves stdio;
- only `test_stdio.py` was added.

Elegant effect: one concise transport test proves every existing launch consumer without adding another runtime layer.

## Wave 3 — CI artifact gate

### W3-T30 — Gate the real profile and built wheel in CI

- status: `pending`
- subject: Gate the real profile and built wheel in CI
- activeForm: Gating the real profile and built wheel in CI
- wave: `3`
- blockedBy: `[W2-T20, W2-T21]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: preserve the existing `python-quality` job, permissions, pnpm setup, uv pin, and affected job
- requiredSkills: `elegant`, `python`, `fastmcp`, `test`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: make lock correctness, assembled profile, package artifacts, and artifact-installed console behavior required CI outcomes.
- write scope: only `.github/workflows/ci.yml`.
- files: creates `[]`; modifies `[.github/workflows/ci.yml]`; removes `[]`; tests `[python-quality job]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[wheel-installed stdio smoke]`; modifies `[python-quality CI]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[temporary CI sdist, wheel, virtual environment]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp`.
- legacyDisposition: preserve unrelated CI and do not add publishing, deployment, action-pin, or dependency-audit scope.

Procedure:

1. Add `uv lock --check` before the existing locked sync.
2. Run `pnpm mcp:inspect` after lint, typecheck, and tests.
3. In one named Bash step, create artifact and venv directories only under `$RUNNER_TEMP`.
4. Run `uv build --package anilize-mcp --out-dir "$RUNNER_TEMP/anilize-mcp-dist" --no-create-gitignore`; require exactly one wheel and one sdist for version `0.1.0`.
5. Create a Python 3.14 uv environment under `$RUNNER_TEMP`, install the built wheel with `uv pip install --python`, and rerun only `tests/test_stdio.py` with `ANILIZE_MCP_EXECUTABLE` pointing to that environment's console script.
6. Do not write `dist/`, a venv, a cache, or generated metadata into the repository.

Required verification:

```bash
pnpm mcp:lint
pnpm mcp:typecheck
pnpm mcp:test
pnpm mcp:inspect
git diff --check -- .github/workflows/ci.yml
```

Acceptance evidence:

- the focused local gates exit successfully;
- YAML remains structurally valid through the repository's existing CI/check tooling;
- only `.github/workflows/ci.yml` changed;
- CI commands use temporary artifact paths and the built console script.

Elegant effect: extend the one existing Python job instead of adding a parallel workflow or packaging script.

## Wave 4 — adversarial verification

### W4-T40 — Adversarially verify the full foundation

- status: `pending`
- subject: Adversarially verify the full foundation
- activeForm: Adversarially verifying the full foundation
- wave: `4`
- blockedBy: `[W3-T30]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: verify only the manifest-owned implementation; unrelated dirty files cannot become findings
- requiredSkills: `adversarial-verification`, `fastmcp`, `python`, `test`, `lint`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: independently prove the exact runtime, transport, error, packaging, and lock contracts and attempt to break the environment-controlled transport boundary.
- write scope: none. If a manifest-owned defect is found, mark this task blocked and reactivate the exact owner task before any repair.
- files: creates `[]`; modifies `[]`; removes `[]`; tests `[test_server.py, test_stdio.py, CI command sequence]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[]`; modifies `[]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[temporary verification artifacts only]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp`.
- legacyDisposition: predecessor paths remain excluded even if repository-wide status is noisy.

Required verification:

```bash
UV_NO_CACHE=1 uv lock --check
pnpm mcp:lint
pnpm mcp:typecheck
pnpm mcp:test
pnpm mcp:inspect
git diff --check
```

Additional failure-oriented probes:

1. Run the console subprocess test with `FASTMCP_TRANSPORT=http` and require stdio success within the bounded timeout.
2. Raise a synthetic normal exception containing a sentinel and require a generic client error without the sentinel.
3. Inspect the complete MCP JSON catalog and require zero tools, prompts, resources, and templates plus application version `0.1.0`.
4. Build wheel and sdist into a fresh temporary directory, install the wheel into a fresh Python 3.14 environment, and run its console-script stdio test from outside the source package.
5. Hash `uv.lock` before and after all gates and require exact equality with the recorded baseline hash.
6. Review the final diff against the manifest allowlist; any additional path is a blocker.

Acceptance evidence:

- every required command and adversarial probe exits successfully;
- temporary artifacts stay outside the repository;
- no task follows with implementation changes; only the final context update remains.

Elegant effect: prove the small end state directly instead of approving it from code reading or a single unit test.

## Wave 5 — final context update

### W5-T50 — Update the canonical MCP boundary and close the record

- status: `pending`
- subject: Update the canonical MCP boundary and close the record
- activeForm: Updating the canonical MCP boundary and closing the record
- wave: `5`
- blockedBy: `[W4-T40]`
- agentType: `primary`
- executionLens: `elegant`
- compatibilityConstraint: update only repository-local MCP guidance; no generated projection or remote documentation owner applies
- requiredSkills: `update`, `fastmcp`, `python`
- parallelSafe: `false`
- maxConcurrentAgents: `1`
- requiresVerificationAgent: `false`
- purpose: make the verified post-implementation constructor and command contract durable while preserving the already-corrected empty no-proxy boundary.
- write scope: only `tools/anilize-mcp/AGENTS.md` plus task-status/evidence updates inside this plan folder.
- files: creates `[]`; modifies `[tools/anilize-mcp/AGENTS.md, task-list.md]`; removes `[]`; tests `[focused documentation checks]`.
- routes: creates `[]`; modifies `[]`; removes `[]`.
- consumers: creates `[]`; modifies `[coding-agent guidance]`; removes `[]`.
- dependencies: creates `[]`; modifies `[]`; removes `[]`.
- generated: creates `[]`; modifies `[]`; removes `[]`.
- packages: `anilize-mcp` guidance only.
- legacyDisposition: preserve the reviewed namespaced-provider contract, empty-server identity, removal of the proxy recommendation, and validated repository skill owner.

Procedure:

1. Document the required root constructor policy: package application version, `on_duplicate="error"`, `mask_error_details=True`, deliberate flexible validation, and explicit stdio in code.
2. State that every child FastMCP server must use the same duplicate/error policy when a separately approved capability is added.
3. Confirm `ProxyProvider` remains absent and the repository `$fastmcp` skill still passes its focused audit. Do not add replacement proxy guidance or bypass the version-routed skill owner.
4. Add `pnpm mcp:inspect` to the command list and clarify that root wrappers run locked.
5. Record final command evidence and mark every task `completed`. No task may remain pending, active, cancelled, or carried forward.

Required verification:

```bash
rg -n 'on_duplicate|mask_error_details|strict_input_validation|mcp:inspect' tools/anilize-mcp/AGENTS.md
! rg -n 'ProxyProvider' tools/anilize-mcp/AGENTS.md
node scripts/codex/audit-skills.mjs --skill fastmcp
git diff --check -- tools/anilize-mcp/AGENTS.md .claude/plans/2026-07-20-anilize-mcp-greenfield-foundation
```

Acceptance evidence:

- the canonical boundary matches verified runtime behavior;
- the contradictory proxy recommendation is absent and the referenced repository skill passes its focused audit;
- the task list records all tasks as completed and includes final verification evidence;
- no documentation, implementation, verification, or cleanup task follows this one.

Elegant effect: one repository-local owner documents the final contract, with contradictory legacy guidance removed rather than qualified through another layer.

## Completion state

- completed: `1`
- pending: `6`
- in progress: `0`
- blocked: `0`
- next task: `W1-T10`

The implementation workstream is complete only when all seven tasks are `completed`, the verification evidence is recorded here, and no manifest-owned or unowned path remains silently unresolved.
