---
title: Use Soleaux in an agent workflow
description: Follow Soleaux's request-scoped analysis loop, choose the right repository tools, and interpret evidence and coverage before drawing conclusions.
sidebar:
  label: Agent workflow
  order: 1
---

Soleaux answers repository questions from one lifecycle-published SQLite generation. The default catalog is in memory, language servers start only when a request needs semantics, and workers and providers close with the server lifespan.

## Follow the analysis loop

1. Consume the host-supplied task packet, or call `context` exactly once with the task objective, optional repository-relative path scopes, and explicit configured resource URIs. It queries the already-published SQLite generation—without capturing, parsing, or rebuilding—and returns ranked source, canonical owners, consumers, constraints, conflicts, validation routes, resources, and gaps.
2. Begin work when packet coverage is complete; another discovery call is not part of the default workflow.
3. Address an explicit gap with the narrow owner: `search` for ranked candidates, `query` for exact tables, `owners` for one exact record, and `navigate` or `inspect` for live LSP semantics.
4. Run `soleaux lint` on the CLI for configured workspace standards; findings also surface as `quality.standards` rows through `query`.
5. Call `preview` to obtain a no-write patch — including `structural_rewrite` previews from typed matchers. Call `edit` only after reviewing the preview and explicitly confirming the exact ID and digest.

`describe` returns capability, schema, provider, storage, and runtime identity for discovery. The `soleaux://about` resource lists the complete component catalog; `restart_lsp` restarts explicitly selected provider sessions when a language server misbehaves.

## Read coverage before conclusions

Zero rows means no matching facts only when coverage is `complete`. For `partial`, `truncated`, `unsupported`, `failed`, or `changed_during_analysis` coverage, absence is not evidence.

- `best_available` (default): default for exploration; return partial evidence when a provider is unavailable
- `syntax_only`: skip all Language Server Protocol work
- `semantic_required`: fail when semantic coverage is incomplete

Every fact row carries one evidence record with its provider, provider version, source hash, workspace-relative path, exact range, resolution status, and authority.

## Use the fixed tool catalog

Configured MCP components are additive and namespaced; they never replace the local Soleaux workflow or the tools below. These are server-local names; a host may qualify each one once with its configured `soleaux` server identity. The following JSON is checked against the registered FastMCP tool components in `soleaux.server`.

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
