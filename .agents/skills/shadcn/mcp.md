# shadcn MCP

The Codex host owns the server registered at `[mcp_servers.shadcn]` in `.codex/config.toml` and surfaces its tools as `mcp__shadcn__*`. The skill does not duplicate that transport in `agents/openai.yaml`.

## Workflow

1. Call `mcp__shadcn__get_project_registries` first.
2. Use the surfaced search, item-detail, example, add-command, and audit tools needed for the requested item. Do not invent tool names.
3. Treat the target's live `components.json` as authority for style, aliases, registries, and output paths.
4. Preview through the pinned CLI dependency before writing:

   ```bash
   pnpm --filter @anilize/ui exec shadcn add --cwd <absolute-target> <item...> --dry-run
   ```

5. If the preview is in scope, run the same command without `--dry-run`, review every generated file, and run the target owner's checks. Updating an existing item uses `add --overwrite`; there is no separate update command.

`packages/ui/components.json` currently configures `@ss-components`, `@ss-themes`, and `@ss-blocks`. Their credentials come from environment variables; never print or commit their values. An app-local `components.json` does not inherit those registries.

## Boundaries

- MCP discovery is evidence, not authorization to write.
- Do not substitute `.mcp.json`, a different server, or model memory when the registered Codex server is required and unavailable.
- Do not use an ephemeral `npx`, `pnpm dlx`, Yarn, or Bun CLI when the pinned `@anilize/ui` dependency is available.
- Do not run `init` over an existing configured target.
