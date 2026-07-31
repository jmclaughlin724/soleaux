# Third-Party Registry Intake

Use this reference only when the user names or authorizes a non-default shadcn registry. `packages/ui/components.json` currently configures `@ss-components`, `@ss-themes`, and `@ss-blocks`; app-local configurations do not inherit them.

## Workflow

1. Select the directory that owns the output and read its `components.json`.
2. Verify the registry URL, item, license, source, dependencies, and credential requirements. Never expose registry credentials.
3. Add registry configuration only when the active task authorizes that target.
4. Inspect the item through the registered shadcn MCP, then preview it:

   ```bash
   pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <registry-item> --dry-run
   ```

5. Apply the same command without `--dry-run` only after the resolved paths are accepted. Use `--overwrite` only for an explicitly authorized replacement.
6. Review every resulting file and verify aliases, dependencies, semantic tokens, accessibility, form behavior, rendering boundaries, and exports.
7. Keep only production-consumed output and run the target owner's checks.

Stop instead of recreating an unavailable item, crossing the selected owner, bypassing required credentials, or substituting an unpinned CLI.
