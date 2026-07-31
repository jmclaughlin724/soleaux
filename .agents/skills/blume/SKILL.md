---
name: blume
description: Use for any Blume dependency or documentation-site work, including installation checks, scaffolding, configuration, Markdown/MDX content, navigation, search, theming, SEO, AI features, or Blume CLI commands. Blume is the markdown-first docs framework on Astro and Vite.
---

# Blume

## Contract

Build, configure, and maintain documentation sites with Blume, the markdown-first docs framework on Astro and Vite, against the installed `blume` version. Content is Markdown/MDX under a content root (default `docs/`); navigation derives from the file tree plus optional `meta.ts` files; `blume.config.ts` (`defineConfig`) owns configuration and every field has a default, so `{}` is a valid config. Treat the bundled docs in `node_modules/blume/docs` as the authoritative reference for configuration fields, CLI flags, component APIs, and behavior; do not assert any of them from memory.

## Use When

- Working in a project that depends on `blume`, or scaffolding a new docs site with `blume init`.
- Writing or editing Markdown/MDX content, frontmatter, navigation `meta.ts` files, or `blume.config.ts`.
- Tuning navigation, search, theming, SEO, OpenAPI references, or AI features (`llms.txt`, Ask AI, the MCP server endpoint).
- Running or debugging the `blume` CLI: `init`, `dev`, `build`, `preview`, `validate`, `add`, `eject`.

Route adjacent jobs to their owners: `$blume-migrate` converts another docs framework to Blume, and `$blume-update-docs` audits existing Blume docs against shipped behavior.

## Direct Workflow

1. Confirm the installed `blume` dependency, the content root (`blume.config.ts` `content.root`), the package manager, and the docs scripts. Blume requires Node.js 22.12 or newer.
2. Read the matching bundled docs before writing config or content: start at `node_modules/blume/docs/index.mdx`, then the relevant `configuration/`, `content/`, `reference/`, or `advanced/` page.
3. Make the narrowest change and rely on defaults instead of restating them. Page frontmatter is strict (unknown keys are build errors), icons are bare kebab-case Lucide names, and directives, mermaid, math, and `package-install` fences are MDX-only.
4. Verify with `blume build --strict` (frontmatter schema, duplicate routes, config) and `blume validate --strict` (internal links, heading anchors, assets); without `--strict` a build can exit 0 while silently dropping invalid pages. Review the rendered result with `blume dev` when navigation, theming, or layout changed.

## Detail Index

- Configuration fields and `defineConfig`: `node_modules/blume/docs/configuration/`
- Navigation, `meta.ts`, authoring syntax, and components: `node_modules/blume/docs/content/`
- Frontmatter schema and CLI reference: `node_modules/blume/docs/reference/`
- OpenAPI, AI features, theming, and deployment: `node_modules/blume/docs/advanced/`

## Boundaries

- Do not edit generated output under `.blume/` or `dist/`.
- Do not set `deployment.site`; Blume auto-detects the deployment URL, and hardcoding it pins wrong absolute URLs everywhere else.
- Do not claim a config field, component, frontmatter key, or CLI flag exists without confirming it in the installed docs.
- `blume eject` is a one-way ownership change; run it only on explicit request.
