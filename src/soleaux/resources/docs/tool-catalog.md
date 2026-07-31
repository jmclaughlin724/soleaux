---
title: Choose a Soleaux tool
description: Choose the right Soleaux tool for repository discovery, source context, typed queries, semantic inspection, previews, and safe edit application.
sidebar:
  label: Tool catalog
  order: 2
---

Read `soleaux://about` for the component-derived catalog: product identity, schema versions, and every registered tool and resource.

The catalog below is Soleaux's fixed local catalog. Its tool identities are bare actions because MCP hosts already own server qualification. Enabled `[mcp.<name>]` backends can add namespaced MCP components, but they never replace or rename these ten local tools.

`context`, `search`, `query`, and `owners` read the lifecycle-published SQLite generation. In particular, `context` never captures files, parses source, or builds a new analysis frame on its request path.

The following JSON is checked against the registered FastMCP tool components in `soleaux.server`.

<!-- soleaux-tool-catalog:start -->

```json
[
  {
    "name": "describe",
    "summary": "Product, catalog, provider, storage, and transport identity",
    "description": "Inspect the fixed tool and resource catalog, schema versions, semantic modes, table-catalog summary, configured providers, storage mode, and runtime identity. Use for capability or schema discovery only."
  },
  {
    "name": "search",
    "summary": "Ranked, hydrated repository facts with excerpts and relations",
    "description": "Ranked repository facts from the currently published SQLite generation: text, symbols, files, routes, rules, tasks, dependencies, and policies. Filter with kinds and paths. The request never waits for enrichment or launches structural or language-server work; use query for published quality.standards and navigate or inspect for live language intelligence."
  },
  {
    "name": "context",
    "summary": "Typed task context from the published SQLite generation",
    "description": "Start repository research here with an objective and optional path scopes. Queries the already-published SQLite generation without building or scanning on the request path, then returns one typed, bounded task packet containing source, canonical owners, direct consumers, constraints, conflicts, validation routes, configured resources, and explicit coverage gaps."
  },
  {
    "name": "query",
    "summary": "Explicit table batch over the fixed catalog with coverage",
    "description": "Batch table reads over the fixed catalog; include_tables selects and exclude_tables is a hard prohibition. Use for exact table control when a context coverage gap requires it."
  },
  {
    "name": "owners",
    "summary": "Paginated canonical identities, decisions, evidence, and conflicts",
    "description": "Explain one canonical consumer record, its consumer-authored field relationships, neutral repository evidence, and conflicting or redundant declarations. Selects only exact record IDs, referenced paths, or normalized authored identities and aliases; ambiguous matches are returned without guessing. The default decisions view returns page-bounded relationship metadata; use view=identities and follow its cursor to enumerate compact policy identities for a configured source."
  },
  {
    "name": "navigate",
    "summary": "LSP-backed semantic navigation with typed coverage",
    "description": "Semantic navigation through installed language servers: definition, references, implementation, hover, call hierarchy, incoming calls, and outgoing calls. Returns explicit partial/unsupported coverage when a provider is unavailable."
  },
  {
    "name": "inspect",
    "summary": "LSP-backed diagnostics, completion, signature help, and code actions",
    "description": "Semantic inspection through installed language servers: diagnostics, completion, signature help, and code actions. Returns explicit partial/unsupported coverage when a provider is unavailable."
  },
  {
    "name": "preview",
    "summary": "Hash-bound, no-write editor patch preview",
    "description": "Normalize rename, format, selected code-action, and structural-rewrite edits into sorted, non-overlapping repository-relative patches. Never writes. Follow up with edit using the issued preview id and digest."
  },
  {
    "name": "edit",
    "summary": "Apply exactly one unexpired preview",
    "description": "Mutating. Revalidates preview id, digest, and every preimage hash before any write; conflicts abort safely. Requires explicit confirmation in the request."
  },
  {
    "name": "restart_lsp",
    "summary": "Restart selected language-server sessions",
    "description": "Process-mutating. Restarts explicitly selected provider, language, or path sessions without rescanning."
  }
]
```

<!-- soleaux-tool-catalog:end -->

## Keep mutations explicit

The first eight local tools are read-only. `edit` can write only a confirmed, hash-bound preview, and `restart_lsp` can restart only explicitly selected provider processes. Host authorization remains independent of Soleaux and does not change these runtime contracts.
