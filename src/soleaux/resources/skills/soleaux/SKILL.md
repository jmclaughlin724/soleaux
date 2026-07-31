---
name: soleaux
description: Use Soleaux for ranked repository search, table-batch queries, canonical record and ownership analysis, live LSP semantics, evidence review, and hash-bound editor previews.
---

# Use Soleaux

Use the task packet already supplied by a host pre-prompt integration. If the host did not supply one, call `context` exactly once with a concrete objective, optional path scopes, and only the configured resource URIs needed for the task. `context` queries the already-published SQLite generation; it does not capture files, parse source, or rebuild the catalog on the request path. Read its explicit gaps before making another discovery call.

If a host reports `host_context_limit`, its required owners, consumers, conflicts, validation routes, and coverage gaps could not fit without loss. Do not treat the missing packet as complete. Make one direct `context` request with a narrower objective and repository-relative path scopes, preserving the reported gap until a bounded packet succeeds. If the narrowed request reports the same limit, stop discovery and report the runtime-repair requirement instead of retrying.

## Follow this workflow

1. Read the typed `soleaux.context/v1` packet: ranked SQLite full-text matches, relation-expanded source, canonical owners, consumers, constraints, conflicts, validation routes, configured resources, and gaps all come from the already-published generation.
2. Begin work immediately when coverage is complete. Do not repeat `context` or call another discovery tool merely to restate packet evidence.
3. For an explicit gap, use the narrow owner: `search` for ranked candidates, `query` for exact tables, `owners` for one exact record, and `navigate` or `inspect` for live LSP semantics.
4. Run `soleaux lint` for configured workspace standards; `quality.standards` remains available through `query` when that exact table is needed.
5. Request `preview` (including `structural_rewrite` previews), review the diff and hashes, then call `edit` only with explicit user confirmation.

`describe` returns capability, schema, provider, storage, and runtime identity for discovery. Never treat zero rows as proof unless coverage is `complete`. Never treat a structural candidate as a resolved semantic edge. MCP host approval is configured independently by the client.

## Use the fixed tool catalog

Configured MCP components are additive and namespaced; they never replace the local Soleaux workflow or the tools below. The following JSON is checked against the registered FastMCP tool components in `soleaux.server`.

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

## Interpret semantic modes

- `best_available` (default): default for exploration; return partial evidence when a provider is unavailable
- `syntax_only`: skip all Language Server Protocol work
- `semantic_required`: fail when semantic coverage is incomplete

## Preserve editor safety

`preview` never writes. Before `edit`, show the user the affected paths and diff, retain the exact preview ID and digest, and obtain explicit confirmation. Do not retry a conflicted, expired, consumed, or process-mismatched preview.
