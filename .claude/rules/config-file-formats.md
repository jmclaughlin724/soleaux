---
paths:
  - "**/*.{yml,yaml}"
  - "**/*.{json,jsonc,json5}"
  - "**/*.toml"
  - "**/*.rules"
  - "**/.env*"
  - ".husky/pre-commit"
  - ".husky/post-commit"
---

# Config file format selection (tracker row 98)

One format per config concern. Never introduce a second format variant of an existing config file (`renovate.yaml` beside `renovate.json5` is a defect, not an option).

## Decision rule — apply in order, first match wins

1. **Tool-mandated format wins.** See the registry below. No debate, no migration.
2. **Machine-generated and machine-consumed → JSON.** Generated types, `openapi.json`, lockfiles, build artifacts. No comments needed by anyone.
3. **Human-authored config needing comments:**
   - **YAML** when nested, list-of-records, or prose-bearing (CI workflows, ast-grep rules, alert rules, fixtures, standards records)
   - **TOML** when flat key-value (`pyproject.toml`-style settings)
   - **JSONC/JSON5** only where JS tooling expects JSON-shaped config (`turbo.jsonc`, `renovate.json5`)
4. **Environment values → `.env`**, names-only committed via `.env.example` (row 21). Never in any other format.

## Format registry (mandated — amend row 98 before changing)

| Concern | File | Format |
| --- | --- | --- |
| Package manifests | `package.json`, `components.json` | JSON |
| Task graph / linting | `turbo.jsonc`, `tsconfig.json` | JSONC |
| Workflows | `.github/workflows/*.yml` | YAML |
| ast-grep rules + tests | `sgconfig.yml`, `rules/*.yml` | YAML |
| Git hooks | `.husky/pre-commit`, `.husky/post-commit` | POSIX shell |
| Workspace / catalogs | `pnpm-workspace.yaml` | YAML |
| Alerts-as-code | `infra/alerts/*.yml` | YAML |
| Standards records | `standards/*.yml`, `verification.yml` | YAML |
| Dependency automation | `renovate.json5` | JSON5 |
| Supabase / Codex / Python config | `supabase/config.toml`, `.codex/config.toml`, `pyproject.toml` | TOML |
| Codex command gates | `.codex/rules/*.rules` | Starlark |
| Generated API spec | `openapi.json` | JSON |

## YAML discipline

- Every YAML file ships schema validation (JSON Schema or yamllint in `check-standards`).
- Quote values YAML 1.1 coerces: `no`, `on`, `off`, country codes, version numbers (`"1.10"`).
- Long prose goes in block scalars (`|`), never quote-escaped flow strings.
- Load YAML with safe loaders only.
