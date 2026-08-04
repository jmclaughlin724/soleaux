# Unified MCP Profile

**Status:** Binding Phase 0 contract  
**Product version:** `0.4.0-dev.5`  
**Profile schema:** `soleaux.mcp.profile/v2`  
**Response envelope:** `soleaux.mcp/v2`  
**Context packet:** `soleaux.context/v2`  
**Hard ceiling:** `12` active tools  
**Production claim:** `false`

This is the sole public MCP catalog contract. Names are server-local and exact; hosts may qualify them once with the configured server identity. Soleaux must not register aliases or a second public catalog. The complete closed JSON Schemas are normative in [`contracts/unified-mcp-profile-v2.json`](contracts/unified-mcp-profile-v2.json).

```text
sha256 89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc  contracts/unified-mcp-profile-v2.json
sha256 3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f  contracts/context-packet-v2.schema.json
```

## Canonical default profile — exactly 12

| Slot | Tool | Mutation | Purpose |
|---:|---|---|---|
| 1 | `context.compile` | read | Single intelligence entry point returning a bounded relation-complete packet with native provenance and trust. |
| 2 | `code.search` | read | Ranked structural plus textual search with kind/path filters and honest coverage semantics. |
| 3 | `memory.search` | read | Search prior compiled context, session memory, and team memory surfaces. |
| 4 | `get_symbols` | read | Return symbols and compact structural outlines for files or scopes; bounded source ranges absorb the former skeleton and node-source tools. |
| 5 | `registry.list` | read | List registry domains and compact entries for tables, ownership, skills, agents, rules, and MCP backends. |
| 6 | `registry.read` | read | Read registry entries or a typed table batch; table and ownership reads absorb former query and owners tools. |
| 7 | `repo_info` | read | Return repository identity, shape, frameworks, storage mode, active profile, and catalog digest. |
| 8 | `navigate` | read | Native LSP-backed definition, references, implementation, hover, and call hierarchy with an 800 ms soft deadline. |
| 9 | `inspect` | read | Native LSP-backed diagnostics, completion, signature help, and code actions with an 800 ms soft deadline. |
| 10 | `preview` | read | Produce a hash-bound, non-overlapping, no-write patch preview for rename, format, code action, or structural rewrite. |
| 11 | `edit` | write | Apply exactly one confirmed, unexpired, preimage-validated preview and return an audit receipt. |
| 12 | `restart_lsp` | process | Restart explicitly selected native language-server sessions and return a process-mutation receipt. |

No gateway backend, skill, agent, rule, registry domain, resource, or remote operation may add a root tool. Those surfaces remain namespaced behind the gateway/registry or CLI.

## Exact schema resolution

The JSON document is the schema registry. For canonical tool slot `N` (zero-based array index):

- input schema: `/tools/N/inputSchema`;
- output schema: `/responseEnvelopeSchema` with its `data` member narrowed by `/tools/N/outputDataSchema`;
- mutation flags: `/tools/N/mutating` and `/tools/N/processMutating`;
- native-selection requirement: `/tools/N/nativeSelectionRequired`.

For optional candidate index `N`, use `/optionalDefinitions/N/inputSchema` and `/optionalDefinitions/N/outputDataSchema`. These JSON Pointers are normative schema references, not prose summaries. Every object schema is closed with `additionalProperties: false`.

## Shared response envelope

Every tool returns the closed `soleaux.mcp/v2` envelope. Its exact JSON Schema is `/responseEnvelopeSchema`. Required members are:

```text
schema_version product_version request_id workspace_id snapshot_id workspace
status data rows evidence coverage warnings next_cursor suggested_next_requests error
source engine engine_version trust provenance cache_status truncated continuation_cursor
sensitivity duration_us
```

Envelope invariants are fail-closed: `status="ok"` requires schema-valid non-null `data` and null `error`; `status="error"` requires null `data` and a typed non-null `error`. Unknown fields, unknown trust labels, and invalid provenance are rejected.

## Tool schema index

| Slot | Tool | Required input | Optional input | Required output `data` |
|---:|---|---|---|---|
| 1 | `context.compile` | `objective` | `limit`, `max_bytes`, `paths`, `references`, `relation_depth`, `resource_uris`, `semantic_mode`, `terms`, `token_budget`, `workspace_id` | — |
| 2 | `code.search` | `query` | `context_lines`, `cursor`, `kinds`, `limit`, `paths`, `semantic_mode`, `workspace_id` | `query`, `matches`, `coverage_complete`, `gaps` |
| 3 | `memory.search` | `query` | `cursor`, `limit`, `scopes`, `workspace_id` | `query`, `attached`, `items`, `coverage_complete`, `gaps` |
| 4 | `get_symbols` | — | `cursor`, `include_source`, `kinds`, `limit`, `max_source_bytes_per_symbol`, `path`, `paths`, `semantic_mode`, `workspace_id` | `scope`, `files`, `symbols`, `coverage_complete`, `gaps` |
| 5 | `registry.list` | — | `cursor`, `domain`, `limit`, `workspace_id` | `domains`, `entries`, `catalog_digest` |
| 6 | `registry.read` | — | `cursor`, `domain`, `exclude_tables`, `ids`, `include_ownership`, `limit`, `seed_keys`, `tables`, `workspace_id` | `domain`, `entries`, `tables`, `ownership`, `coverage_complete`, `gaps` |
| 7 | `repo_info` | — | — | `product`, `version`, `production_claim_allowed`, `workspace_id`, `root`, `shape`, `frameworks`, `storage`, `transport`, `active_tools`, `hard_ceiling`, `catalog_digest`, `native_selections` |
| 8 | `navigate` | `operation` | `column`, `limit`, `line`, `path`, `semantic_mode`, `symbol_kind`, `symbol_name`, `workspace_id` | `operation`, `pending`, `request_id`, `cached`, `locations`, `hover`, `call_hierarchy`, `server_id`, `document_version`, `soft_deadline_ms` |
| 9 | `inspect` | `operation`, `path`, `line`, `column` | `limit`, `semantic_mode`, `workspace_id` | `operation`, `pending`, `request_id`, `cached`, `items`, `server_id`, `document_version`, `soft_deadline_ms` |
| 10 | `preview` | `operation` | `action_index`, `column`, `end_column`, `end_line`, `line`, `new_name`, `path`, `paths`, `semantic_mode`, `strict`, `structural`, `symbol_kind`, `symbol_name`, `target`, `ttl_seconds`, `workspace_id` | `preview_id`, `digest`, `created_at_unix_ms`, `expires_at_unix_ms`, `operation`, `patches`, `non_overlapping`, `writes_performed`, `validation_plan`, `warnings` |
| 11 | `edit` | `preview_id`, `digest`, `confirm` | `workspace_id` | `receipt_id`, `preview_id`, `applied`, `files`, `formatter`, `diagnostics`, `reindexed`, `audit_event_hash` |
| 12 | `restart_lsp` | — | `language`, `path`, `provider`, `reason`, `workspace_id` | `receipt_id`, `restarted`, `skipped`, `failures`, `process_mutated` |

### Semantic and safety constraints

- `context.compile` accepts path scopes and explicit resource URIs; it returns `soleaux.context/v2` and is the sole public context tool.
- `navigate` and `inspect` use the native LSP broker. The interactive soft deadline is at most 800 ms; the response is live, cached, or typed pending with a stable request ID.
- `preview` performs no writes. Patches are hash-bound, bounded, and non-overlapping.
- `edit` applies exactly one confirmed, unexpired preview only after all preimage hashes are revalidated.
- `restart_lsp` is process-mutating and requires an explicit provider, language, or path selector.

## Optional substitution

Optional candidates are exactly:

- `parse_and_validate_postgres_sql`
- `turborepo.packages`
- `next.get_routes`

They never append. Workspace configuration declares explicit one-for-one substitutions. The active order remains the canonical slot order. A canonical slot and optional candidate may each be used at most once. The selected provider, parser, and LSP implementation must be native. Unknown names, duplicates, disabled providers, non-native selections, or an active count above 12 abort startup before transport acceptance.

Example:

```toml
[public_profile]
substitutions = [
  { replace = "restart_lsp", with = "turborepo.packages" },
]
```

The optional capability displaced from the root catalog remains available through its required CLI, registry, or gateway surface; substitution does not delete or demote product capability.

## Drift enforcement

Phase 0 tests fail when any of the following changes without a reviewed contract update:

1. ordered canonical names or the 12-tool hard ceiling;
2. optional candidate names or explicit substitution semantics;
3. a missing/unknown/ open input or output schema;
4. product version, schema versions, or `productionClaimAllowed`;
5. document/schema SHA-256 digests;
6. native-selection guarantees.

During Phase 0, the pre-unification binary may still expose its smaller transitional catalog, but its absolute runtime ceiling is tightened to 12. Phase 1 is incomplete until `tools/list` equals the canonical list or an explicitly substituted 12-slot profile.
