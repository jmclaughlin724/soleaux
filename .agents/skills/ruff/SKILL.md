---
name: ruff
description: "Run and configure the Astral Python toolchain: ruff linting and formatting, and uv environments, dependencies, lockfiles, and tool execution. Use when linting or formatting Python, managing a uv project, or invoking a Python tool."
---

# ruff

## Contract

Invoke ruff and uv through the task that owns the check, and report only what that task produced. An ad-hoc `uvx ruff` or bare `ruff` run is probe data, never gate evidence. Scope fixes to the files under edit, and change dependency state only through uv's own commands.

## Use When

- Linting or formatting Python, or interpreting a ruff violation or fix.
- Creating, syncing, or resolving a uv project, workspace, or lockfile.
- Adding, removing, or upgrading a Python dependency.
- Running a Python tool without installing it.

For Python engineering work — API design, testing, packaging, release, security or performance review — use [`python`](../python/SKILL.md) instead. That skill owns the workflow; this one owns the tool invocation.

## Repository Owners

Establish these before running anything in this repository.

| Concern | Owner |
| --- | --- |
| Ruff configuration | root `pyproject.toml` (`[tool.ruff]`, `required-version`, `line-length = 100`, `target-version = "py314"`) |
| Lint and format gate | `pnpm python:lint` |
| Test gate | `pnpm python:test` |
| Type checking | pyright, owned by the package it checks. This repository does not use ty and declares no `[tool.ty]`. |
| uv workspace, lockfile, and version pin | root `pyproject.toml` (`required-version = "==0.11.28"`, `[tool.uv.workspace]`) and the committed `uv.lock` |
| Editor language servers | the non-default `editor` dependency group; enable with `uv sync --group editor` |

Ruff resolves the closest configuration per file, so a workspace member that carries its own `[tool.ruff]` governs itself and the root table governs everything else.

No root `fix` script covers Python; `pnpm fix` is Ultracite and owns JavaScript and TypeScript only. Apply ruff fixes explicitly:

```bash
uv run --locked ruff check --fix .
```

## Invocation Discipline

- Prefer `uv run` when the tool is a project dependency, so the pinned version resolves. Use `uvx` only for a tool the project does not depend on, and only for well-known packages.
- Pass `--locked` when running inside a locked project. A run that silently relocks is not reproducible, and this repository's own tasks all specify it.
- `uv run` from a project root without `--no-project`, `--script`, or an equivalent flag resolves and builds that project first. In a workspace with compiled dependencies this is slow and usually unintended; reach for `--no-project` when the command has nothing to do with the project.
- Put throwaway reproduction scripts outside the checkout, for example under `/tmp`. A `.py` file inside a checkout inherits that checkout's `requires-python`, which silently changes the Python version a tool infers.
- Mutate dependencies only through `uv add`, `uv remove`, and `uv lock`. Never hand-edit `uv.lock`.
- Do not reformat a file the project does not already keep formatted. If `ruff format --diff` rewrites an entire untouched file, the project is not formatting that path, and reformatting it buries the real change.

## Detail Index

- `references/ruff-usage.md`: check and format command surface, rule selection, unsafe fixes, and migration from Black, Flake8, and isort.
- `references/uv-usage.md`: script, project, tool, and pip-interface workflows, plus migration from pyenv, pipx, pip, and pip-tools.

## Upstream Sources

Verify version-sensitive behavior against the official documentation rather than this skill.

- Ruff: https://docs.astral.sh/ruff/
- uv: https://docs.astral.sh/uv/llms.txt

The `astral-sh/ruff` repository was reviewed for this skill. Its agent-facing material governs contributing to the Rust workspace that builds ruff and ty, not using either tool, so it is deliberately excluded:

| Reviewed source | Disposition |
| --- | --- |
| [`AGENTS.md`](https://github.com/astral-sh/ruff/blob/main/AGENTS.md) | Contributor brief. Only the `uv run` project-build pitfall and the ad-hoc reproduction pitfall transfer, and both appear above. |
| `.agents/skills/adding-ty-diagnostics`, `.agents/skills/minimizing-ty-ecosystem-changes`, `.agents/skills/summarise-ecosystem-results`, `.agents/skills/wobbling-ty-constraint-order` | Excluded. Each requires cargo, mdtest, Salsa, or `mypy_primer` against the Rust workspace. |
| `fuzz/fuzz_targets`, `python/py-fuzzer`, `python/ruff-ecosystem`, `scripts/` | Excluded. Ruff development and release infrastructure, not a consumer surface. |
| `python/ruff` | Distribution wrapper. `_find_ruff.py` locates the packaged binary, which is how `uvx ruff` and `uv run ruff` resolve an executable. |

Re-check this table before importing anything further from that repository.
