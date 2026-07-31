---
name: blume-migrate
description: Migrate an existing documentation site (Mintlify, Docusaurus, Fumadocs, Nextra, Starlight, or any docs framework) to Blume, the markdown-first docs framework on Astro. Translate the source config to blume.config.ts, restructure content into Blume's filesystem-derived navigation, rewrite JSX callouts to directives, convert icons to Lucide, and inline snippets. Use when the user asks to migrate/convert/port a docs repo to Blume, or when the repo has a docs.json/mint.json, docusaurus.config.*, meta.json with fumadocs, _meta.* with nextra, or an astro.config.* with starlight().
---

# Migrate to Blume

## Contract

Blume is a **markdown-first** documentation framework on Astro/Vite. You drop Markdown/MDX into a folder and get navigation, search, theming, Open Graph images, and a component library with no app boilerplate — **the framework is the template**. There is no starter to clone; the only thing a project owns is its content and a `blume.config.ts`.

Your job is to convert a source docs repo into an **idiomatic** Blume project — not a 1:1 transliteration. Read this file, detect the source framework, open the matching `references/<framework>.md` for the exact mappings, and work the loop below. Report everything you drop or approximate.

## Principles

- Target idiomatic Blume, not a mechanical port. Prefer filesystem navigation, `:::` directives, and framework defaults.
- Map only behavior the source declares. Blume's page frontmatter is strict: map or remove every source-only key and report removals.
- Report unsupported chrome and behavior instead of silently faking it.
- Preserve routes intentionally. Record every old-to-new path and add a static redirect when content moves.

## Reference Routing

Read the source-specific reference before editing:

| Detection | Reference |
| --- | --- |
| `docs.json` or `mint.json` | [mintlify.md](references/mintlify.md) |
| `docusaurus.config.*` | [docusaurus.md](references/docusaurus.md) |
| `meta.json` with Fumadocs dependencies | [fumadocs.md](references/fumadocs.md) |
| `_meta.{js,ts,json}` with Nextra | [nextra.md](references/nextra.md) |
| `astro.config.*` calling `starlight()` | [starlight.md](references/starlight.md) |

Read [blume-model.md](references/blume-model.md) for Blume navigation, routes, config, frontmatter, authoring features, OpenAPI, changelogs, and redirects. Also read [monorepo.md](references/monorepo.md) whenever the target is a workspace, deploys through Vercel, pins Vite, or keeps content outside a bare `docs/` package.

## Migration Workflow

1. Inventory source config, content roots, navigation sidecars, includes, assets, API specifications, redirects, locales, custom components, and icon families. Distinguish declared behavior from framework defaults.
2. Write `blume.config.ts` with `defineConfig` from `blume`. Map declared fields only; `defineConfig({ title: "…" })` is a valid minimal result.
3. Detect the real Markdown root. Set a narrow `content.root` and `content.include`; never use `content.root: "."` to scan an entire app. Prefer numeric filename prefixes and `(group)/` folders. Convert Fumadocs `meta.json` and Nextra `_meta.*` files to `meta.ts` so declared folder order, titles, icons, and collapse behavior survive. Use explicit `navigation.sidebar` only when files cannot express the source navigation.
4. Rewrite pages to the strict Blume schema. Convert supported callouts to directives, rename `.md` to `.mdx` when a page uses directives, math, Mermaid, or package-install fences, inline imported snippets, repair assets and internal routes, convert icons to Lucide, and remove body H1 headings when frontmatter `title` supplies them. Review OpenAPI route rules and offer `github-releases` for a hand-maintained open-source changelog.
5. For Mintlify, run `node <skill>/scripts/mintlify-codemod.mjs --write <content-dir>` before manual cleanup. It deterministically maps known frontmatter and icons and reports unresolved cases.
6. Replace the old framework scripts and dependencies with `blume dev`, `blume build`, `blume preview`, and the selected Blume version. Create a manifest for config-only sources. In pnpm workspaces, resolve `minimumReleaseAge`, keep any exception exact, run `pnpm install` from the workspace root, and include the regenerated lockfile. If Ultracite's oxfmt owns formatting, follow the bundled patch procedure in `monorepo.md`.
7. Apply the host integration in `monorepo.md`: content scoping, Turbo/package wiring, Vercel config and dashboard handoff, or the pinned Astro/Vite patch only when the detected repo needs it.
8. Run `blume build --strict`, then `blume validate --strict`. Build owns config, frontmatter, and duplicate-route diagnostics; validate owns internal links, anchors, and assets. Use `--external` only when outbound HTTP checks are required. Finish with `blume dev` and a visual review.

## Handoff

Report the migrated config, page count, navigation, API docs, redirects, dependency and lockfile changes, and every host-repository edit with its reason. Separately list dropped or approximated chrome, unmappable icons or components, and manual dashboard or patch steps. Suggest `blume eject` or `blume add` only when the user needs ownership of generated framework internals.

The installed package's authoritative documentation is under `node_modules/blume/docs` (or `apps/docs/content/docs` in a Blume checkout). Use its configuration, navigation, metadata, syntax, component, and frontmatter pages when a reference does not settle a version-specific detail.
