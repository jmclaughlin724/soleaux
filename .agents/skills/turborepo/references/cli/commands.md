# Turbo CLI Routing

The installed `turbo --help` and subcommand help are authoritative. Run the repository-owned binary through pnpm; do not use `npx`, `pnpm dlx`, or a copied version-specific command catalog.

## Common Read-Only Inspection

```bash
pnpm exec turbo --version
pnpm exec turbo run <task> --filter <workspace> --dry=json
pnpm exec turbo run <task> --affected --dry=json
```

Use only flags exposed by the installed help. A filter is a graph selector, so inspect its dry-run result before an expensive or mutating task.

## Execution

Committed package and root scripts use the stable `turbo run <task>` form. Interactive diagnosis may invoke `pnpm exec turbo run` directly when the exact filter and task are known.

Prefer:

- one package for a focused check;
- `--affected` only with a verified base comparison;
- explicit dependency/dependent selectors when testing graph impact; and
- JSON dry-run output when the result must be reviewed or processed.

Do not introduce `turbo-ignore` from this reference. If the repository later owns that capability, verify its installed version and make it a deliberate CI or deployment dependency.
