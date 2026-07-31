# Ruff Usage

Ruff is a Python linter and formatter that replaces Flake8, isort, Black, pyupgrade, autoflake, and their plugin ecosystems.

Treat a project as ruff-governed when it declares `[tool.ruff]` in `pyproject.toml`, or carries a `ruff.toml` or `.ruff.toml`.

## Linting

```bash
ruff check .                       # Check the current directory
ruff check path/to/file.py         # Check one file
ruff check --fix .                 # Apply safe fixes
ruff check --diff .                # Show fixes without applying them
ruff check --watch .               # Re-lint on change
ruff check --select E,F .          # Restrict to specific rules
ruff check --ignore E501 .         # Suppress specific rules
ruff rule E501                     # Explain one rule
ruff linter                        # List the available linters
```

Use `ruff check --diff` to see only the fixes that touch code under edit. Apply fixes to files you are already changing unless the user asks for a broader sweep.

## Formatting

```bash
ruff format .                      # Format the current directory
ruff format path/to/file.py        # Format one file
ruff format --check .              # Report formatting drift, change nothing
ruff format --diff .               # Show the formatting diff
```

## Fix Before Format

Run `ruff check --fix` before `ruff format`. Lint fixes restructure code — reordering imports, rewriting comprehensions — and formatting then normalizes the result. The reverse order leaves the file needing a second pass.

```bash
ruff check --fix .
ruff format .
```

## Unsafe Fixes

Ruff marks a fix unsafe when applying it can change behavior rather than only style. Removing an unused import is the common case: the import may exist for its side effect.

```bash
ruff check --fix --unsafe-fixes --diff .   # Preview
ruff check --fix --unsafe-fixes .          # Apply
```

Before applying, run `ruff rule <CODE>` to read why the fix is unsafe, then confirm the code does not depend on the assumption the fix breaks.

## Configuration

Configure ruff in `pyproject.toml` under `[tool.ruff]`, or in a standalone `ruff.toml`. This repository's soleaux package is the working example:

```toml
[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.format]
docstring-code-format = true

[tool.ruff.lint]
select = [
  "B", "E", "ERA", "F", "G", "I", "ICN", "LOG",
  "PGH", "PTH", "RET", "RSE", "RUF", "SIM", "UP", "W", "YTT",
]
# W191 is on the Ruff formatter's documented conflicting-rules list.
ignore = ["W191"]
```

Scale the selected rule set to the project's maturity. Adding a large rule family to an established codebase produces a violation backlog that obscures new defects.

Import sorting is a lint rule, not a formatter setting:

```toml
[tool.ruff.lint.isort]
known-first-party = ["myproject"]
```

## Migrating From Other Tools

### Black

```bash
black .                → ruff format .
black --check .        → ruff format --check .
black --diff .         → ruff format --diff .
```

### Flake8

```bash
flake8 .               → ruff check .
flake8 --select E,F .  → ruff check --select E,F .
flake8 --ignore E501 . → ruff check --ignore E501 .
```

### isort

```bash
isort .                → ruff check --select I --fix .
isort --check .        → ruff check --select I .
isort --diff .         → ruff check --select I --diff .
```

## Documentation

- https://docs.astral.sh/ruff/
- Configuration reference: https://docs.astral.sh/ruff/configuration/
