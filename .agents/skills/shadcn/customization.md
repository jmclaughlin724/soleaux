# Customization and Theming

The live owners are `packages/ui/components.json` and `packages/ui/src/styles/globals.css`. Do not copy their current token values, font stacks, radii, shadows, or registry inventory into this reference.

## Theme Structure

Preserve the pattern already used by the live stylesheet:

1. light and dark semantic variables define runtime values;
2. `@theme inline` maps those variables to Tailwind tokens; and
3. components consume semantic utilities rather than palette-specific values.

Add a token only when it represents reusable product meaning. Define its light and dark values, map it through the existing Tailwind layer, and exercise at least one real consumer. Match the live radius and shadow formulas instead of introducing a parallel scale.

## Applying a Registry Theme or Preset

The shared `components.json` configures the `@ss-themes` registry. Preview the resolved write set through the pinned CLI before applying it:

```bash
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> @ss-themes/anilize --dry-run
pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> @ss-themes/anilize
```

For another accepted preset, inspect the installed `apply --help` contract and use the same pinned target:

```bash
pnpm --filter @anilize/ui exec shadcn apply --cwd <absolute-target> <preset>
```

Do not run `init` over an existing configuration. Review all token, font, dependency, and component changes before accepting either command.

## Component Customization Order

1. Use an existing variant.
2. Add focused `className` composition for a one-off layout need.
3. Add a shared `cva` variant when multiple production consumers need the same semantic option.
4. Compose a higher-level component only when it owns a stable behavior or product contract.

For Base UI slots use the generated `render` contract; for Radix slots use the generated `asChild` contract. Read [base-vs-radix.md](rules/base-vs-radix.md) before changing composition.

## Update and Verification

Use the preview workflow in [cli.md](cli.md#preview-and-add). After a write, review every changed file, verify semantic tokens and accessible behavior, run `pnpm --filter @anilize/ui typecheck`, and render the affected component in its real consumer.
