# shadcn/ui Playbook

Read only the section needed for the active component, block, update, or registry task. The target's `components.json` and the pinned CLI owned by `packages/ui` are canonical.

## Choose the Owner

- Reusable primitives and render-only shared composition: `packages/ui`.
- Route, auth, dashboard, form, or domain-specific blocks: the consuming app.
- Shared semantic tokens: `packages/ui/src/styles/globals.css`.

Every configured target has its own `components.json`. Pass that directory as an absolute `--cwd` value; do not infer output ownership from the registry item.

## Discover and Preview

1. Read the target's `components.json`, manifest, existing primitives, and complete applicable owner instruction chain.
2. Use the registered shadcn MCP to inspect the exact item, dependencies, examples, and audit checklist.
3. Confirm the item registry is configured in that target. App targets do not inherit the custom registries in `packages/ui/components.json`.
4. Preview resolved paths and diffs through the pinned CLI:

   ```bash
   pnpm --filter @anilize/ui exec shadcn info --cwd <absolute-target>
   pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...> --dry-run
   pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item> --diff
   ```

## Apply

For a new item, rerun the accepted preview without `--dry-run`. For an existing item, inspect the diff and use `add --overwrite` only when replacing local edits is explicitly in scope.

```bash
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...>
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...> --overwrite
```

A registry block uses the same `add` command and the app directory that owns its `components.json`.

## Review Registry Output

- Read every created or overwritten file and compare it with the preview.
- Verify aliases, direct dependencies, semantic tokens, accessible behavior, form participation, and client or server boundaries.
- Keep shared primitives generic and route-owned behavior in the consuming app.
- Remove demo-only files only after proving they have no consumers.
- Add a package export only for a real cross-workspace consumer.

For net-new interfaces, read [design-direction.md](design-direction.md). For existing surfaces, preserve the established product language and component contract.

## Verification

- Shared component: `pnpm --filter @anilize/ui typecheck` plus a focused render or interaction check when behavior changed.
- App-owned block: the target app's focused typecheck or test.
- Configuration change: rerun `shadcn info --cwd <absolute-target>`, inspect the generated diff, and validate the target owner.

Never run `init` over an existing configured target or accept generated code without reviewing ownership, dependencies, accessibility, and imports.
