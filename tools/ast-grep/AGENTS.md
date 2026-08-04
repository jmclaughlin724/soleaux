# Soleaux ast-grep policy owner

- Root `sgconfig.yml` is the sole project configuration used by the VS Code LSP, package scripts, hooks, and `soleaux lint` through `soleaux.toml`.
- `rules/` contains only Soleaux-owned structural policy. Do not import application-specific rules from the former host repository.
- Each rule has one matching test fixture and one generated snapshot in `tests/__snapshots__/`.
- All rules use AST patterns and node kinds. Regex constraints are prohibited.
- Every relational rule declares `stopBy` explicitly. Blocking rules use `severity: error`, `metadata.heuristic: "false"`, and an actionable `note`.
- Run `pnpm ast-grep:test` for rule contracts, `pnpm ast-grep:validate` for the repository scan, and `pnpm ast-grep:update-snapshots` only when intentionally reviewing snapshot changes.
