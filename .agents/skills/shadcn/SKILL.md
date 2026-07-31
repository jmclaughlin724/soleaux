---
name: shadcn
description: Install, compose, theme, and troubleshoot shadcn/ui components.
---

# shadcn/ui

## Contract

Use this skill for shadcn/ui component discovery, installation, composition, registry use, theming, and troubleshooting. Treat the owning `components.json`, package manifest, lockfile, installed CLI help, and the Codex server registered at `[mcp_servers.shadcn]` in `.codex/config.toml` as authority for this checkout. Use the server through its surfaced `mcp__shadcn__*` tools. Load only the reference needed for the active component or design decision.

## Use When

- Adding, updating, or troubleshooting shadcn/ui components or blocks.
- Changing `components.json`, registry configuration, semantic tokens, or shared UI composition.
- Building product UI that should reuse repository primitives.

## Direct Workflow

1. Identify whether the component is shared in `packages/ui` or owned by one app. Read the relevant `components.json`, package manifest, existing primitives, and owner instructions.
2. Invoke `mcp__shadcn__get_project_registries`, then use the relevant `mcp__shadcn__*` discovery or inspection tool before writing a registry-backed component from memory.
3. Use the pinned CLI owned by `packages/ui`: `pnpm --filter @anilize/ui exec shadcn`. Pass the exact target that owns `components.json` through `--cwd`; inspect with `info`, `view`, `search`, `docs`, or `add --dry-run` before any write. There is no repository wrapper or separate update command.
4. Read [skill-playbook.md](references/skill-playbook.md) only for the applicable composition or registry section. Use [design-direction.md](references/design-direction.md) for net-new product UI.
5. Keep shared primitives generic and app-owned blocks near their consumers. Compose existing primitives before adding wrappers.
6. Preserve semantic tokens, accessible behavior, form participation, and the installed component API.
7. Review generated or registry-sourced code before accepting it, then run the narrowest owning lint, type, and interaction checks.

## Detail Index

- `references/skill-playbook.md`: component and registry workflow.
- `references/design-direction.md`: visual and product design decisions.
- `references/intake-workflow.md`: optional third-party registry intake.
- `rules/*.md`: focused composition, form, icon, and styling guidance.
- `cli.md`, `customization.md`, and `mcp.md`: deeper tool references; verify all commands and server names against the live checkout before use.

## Boundaries

- Do not run `shadcn init` over an existing configured workspace.
- Do not silently substitute `.mcp.json`, another MCP server, a raw registry search, or model memory when the `[mcp_servers.shadcn]` tools required for registry-backed work are unavailable; report the missing dependency.
- Do not assume a third-party registry or MCP server is installed because an archived reference mentions it.
- Do not bypass semantic tokens with one-off theme values when an owned token exists.
- Do not move route, auth, or domain logic into shared UI primitives.
- Do not run a mutating CLI command until its target, planned paths, and overwrite behavior are known.
- Do not hand-edit generated registry output without reviewing its source and ownership.
