# shadcn CLI

This checkout owns the CLI dependency in `packages/ui`. Use one canonical runner and pass the directory that owns `components.json` explicitly:

```bash
pnpm --filter @anilize/ui exec shadcn <command> --cwd <absolute-target>
```

Do not substitute `npx`, `pnpm dlx`, Yarn, Bun, or a globally installed CLI. Before relying on an option, inspect the installed command's `--help` output.

## Inspect

```bash
pnpm --filter @anilize/ui exec shadcn info --cwd <absolute-target>
pnpm --filter @anilize/ui exec shadcn search --cwd <absolute-target> <registry...>
pnpm --filter @anilize/ui exec shadcn view --cwd <absolute-target> <item...>
pnpm --filter @anilize/ui exec shadcn docs --cwd <absolute-target> <component...>
```

Use the registered shadcn MCP for ordinary discovery. The CLI is useful when the result must be resolved against one target's aliases, registries, and paths.

## Preview and Add

`add` owns both new installation and replacement. Preview the exact target before writing:

```bash
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...> --dry-run
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item> --diff
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item> --view
```

Apply only the reviewed command:

```bash
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...>
```

Replacing an existing item is an explicit overwrite:

```bash
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...> --overwrite
```

There is no separate update or block command. Registry blocks are added through `add` with the consuming app as `--cwd`.

## Presets and Themes

For an already configured target, inspect the installed `apply --help` contract and apply only the requested preset or part:

```bash
pnpm --filter @anilize/ui exec shadcn apply --cwd <absolute-target> <preset>
pnpm --filter @anilize/ui exec shadcn apply --cwd <absolute-target> <preset> --only theme
```

Use `init` or `create` only for an explicitly authorized new target. Never run either over an existing configured workspace.

## Migrations and Registry Builds

`migrate`, `eject`, and `build` can rewrite broad surfaces. Inspect installed help, freeze their scope, and review the complete diff before accepting them. Do not use `eject` unless removing the shadcn dependency is the stated outcome.

## Completion

After any write:

1. compare actual paths with the preview;
2. read every created or overwritten file;
3. verify aliases and direct dependencies;
4. preserve semantic tokens and accessible behavior; and
5. run the target owner's focused typecheck and interaction tests.
