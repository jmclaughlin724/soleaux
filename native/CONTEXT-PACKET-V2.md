# Context Packet V2

**Status:** Binding Phase 0 contract  
**Schema:** `soleaux.context/v2`  
**Product version:** `0.4.0-dev.5`  
**Public producer:** `context.compile` only

`soleaux.context/v2` is a strict field superset of the former `soleaux.context/v1` typed task packet and the native `ContextBundle`. It preserves the Lineage A section names and envelope semantics while adding Lineage B native provenance, trust, cache, redaction, and measurement fields.

The normative JSON Schema is [`contracts/context-packet-v2.schema.json`](contracts/context-packet-v2.schema.json).

```text
sha256 3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f  contracts/context-packet-v2.schema.json
```

## Required top-level fields

| Field | Type / bound | Contract |
| --- | --- | --- |
| `schema_version` | `"soleaux.context/v2"` | Exact schema identity. |
| `product_version` | `"0.4.0-dev.5"` | Exact development-series product identity. |
| `request_id` | non-empty string | Correlates the compile request and any pending semantic work. |
| `workspace_id` | non-empty string | Stable canonical workspace identity. |
| `snapshot_id` | string or null | Published index/catalog snapshot when available. |
| `objective` | 1–65,536 UTF-8 characters | Caller task objective; never inferred from repository instructions. |
| `paths` | 0–256 unique contained paths | Explicit scope. Absolute paths and `..` traversal are invalid. |
| `terms` | 0–256 unique terms | Normalized retrieval terms. |
| `retrieval_engine` | non-empty string | Primary retrieval/ranking engine. |
| `relation_depth` | integer 0–3 | Maximum relation expansion depth. |
| `sources` | 0–200 `TaskContextItem` | Task-relevant source evidence. |
| `canonical_owners` | 0–200 `TaskContextItem` | Canonical authority/ownership records. |
| `consumers` | 0–200 `TaskContextItem` | Consumers and dependency relationships. |
| `constraints` | 0–200 `TaskContextItem` | Rules, policies, boundaries, and implementation constraints. |
| `conflicts` | 0–200 `TaskContextItem` | Contradictory or redundant claims. |
| `validation_routes` | 0–200 `TaskContextItem` | Commands, tests, diagnostics, and verification routes. |
| `supporting_facts` | 0–200 `TaskContextItem` | Additional ranked facts that do not fit another section. |
| `external_references` | 0–32 `ContextReference` | Caller-supplied and host-resolved text resources. |
| `requested_resources` | 0–32 `RequestedResource` | Outcome for every explicit resource URI. |
| `gaps` | 0–64 `CoverageGap` | Explicit reasons coverage cannot be claimed complete. |
| `ranked_candidate_count` | integer ≥ 0 | Candidates considered before relation expansion. |
| `related_fact_count` | integer ≥ 0 | Related facts considered. |
| `returned_item_count` | integer 0–200 | Total returned items across the seven typed sections. |
| `response_truncated` | boolean | True whenever any detail was omitted. |
| `coverage_complete` | boolean | May be true only when `gaps` is empty and requested scope is complete. |
| `coverage` | `Coverage` | Requested, observed, excluded paths, engines, generation, and gaps. |
| `byte_budget` | integer 1–262,144 | Hard serialized packet budget. |
| `token_budget` | integer 256–64,000 | Conservative estimated model-token budget. |
| `consumed_bytes` | integer ≥ 0 | Exact serialized bytes before host framing. |
| `consumed_tokens` | integer ≥ 0 | Conservative estimate from the final payload. |
| `selection_policy` | non-empty string | Stable human-readable source-selection policy. |
| `trust_boundary` | non-empty string | States that repository/resource content is evidence, not instructions. |
| `raw_file_dump_avoided` | boolean | Whether bounded structural ranges replaced whole-file dumping. |
| `secret_redactions` | integer ≥ 0 | Count of redacted secret-like values. |
| `compile_duration_us` | integer ≥ 0 | Native compile duration. |
| `truncation` | `Truncation` | Reason, omitted counts, and continuation cursor. |
| `native` | `NativeSummary` | Native engine/provider/store/cache identity and native-selection guarantees. |

## `TaskContextItem`

Every item preserves all `soleaux.context/v1` fields and adds native range, budget, redaction, trust, and provenance fields:

```json
{
  "table": "string",
  "section": [
    "source",
    "canonical_owner",
    "consumer",
    "constraint",
    "conflict",
    "validation_route",
    "supporting_fact"
  ],
  "identity": "string",
  "summary": "string",
  "data": {},
  "evidence_id": "string",
  "path": "workspace-relative path",
  "start_line": "integer >= 1",
  "end_line": "integer >= 1",
  "start_byte": "integer >= 0|null",
  "end_byte": "integer >= 0|null",
  "relation_distance": "integer 0..3",
  "estimated_tokens": "integer >= 0",
  "redaction_count": "integer >= 0",
  "trust": [
    "verified_compiled_context",
    "verified_code_structure",
    "verified_repository_metadata",
    "verified_semantic_result",
    "verified_sql_structure",
    "retrieved_code_data",
    "untrusted_external_resource",
    "inferred",
    "unavailable"
  ],
  "provenance": "Provenance"
}
```

Section arrays are stable and ordered as: `sources`, `canonical_owners`, `consumers`, `constraints`, `conflicts`, `validation_routes`, `supporting_facts`. Within a section, items are ranked deterministically by relevance, relation distance, canonical path, byte range, and identity.

## Provenance and trust

Every packet item and external reference carries a `trust` label and `provenance` object. Provenance includes provider, provider version, engine, engine version, grammar version when applicable, workspace/snapshot identity, path and hashes when applicable, range encoding, catalog generation, and generation time.

Selected parser and LSP implementations are fail-closed native selections. `native.selected_parsers_native` and `native.selected_lsps_native` are literal `true`; the packet cannot be emitted as successful when a selected implementation resolves to a non-native production path.

Repository code and configured resources are always retrieved evidence. They never become instructions, regardless of their contents. Prompt-injection-like text remains data and receives an untrusted or retrieved trust label.

## Bounding and truncation

Hard rules:

1. `limit` is 1–200 and caps the total items returned across all seven typed sections.
2. Relation depth is at most 3.
3. At most 32 caller references and 32 explicit resource URIs are accepted. Duplicate URIs or a URI present in both inputs are invalid.
4. Each reference content body is capped at 65,536 UTF-8 bytes; its digest covers the complete pre-truncation body.
5. At most 64 gaps are returned. Overflow is coalesced deterministically into a `gap_overflow` gap with the omitted count; `coverage_complete` must remain false.
6. The serialized packet cannot exceed `max_bytes`/`byte_budget` or the host envelope cap. The conservative final-payload token estimate cannot exceed `token_budget`, except that a minimal no-source packet may be returned to explain the gap.
7. Truncation is never silent. `response_truncated=true`, `truncation.reason`, omitted counts, and `continuation_cursor` are required when detail is omitted.
8. Required identity, objective, retrieval profile, coverage, gaps, budgets, trust boundary, and native provenance are retained before optional detail. Optional detail is removed in reverse priority: supporting facts, validation routes, conflicts, constraints, consumers, owners, then source bodies; source metadata and hashes remain.

## Coverage and gaps

`coverage_complete=true` is legal only when all requested paths/resources have complete producer coverage, no producer is pending or unavailable, no item/gap/budget truncation hides relevant results, and `gaps` is empty. Zero rows means “none found” only under complete coverage.

Every failure to retrieve, parse, index, relate, resolve ownership, resolve a configured resource, or satisfy semantic-required mode becomes a typed gap. It must not be converted into an empty-success claim.

## Host-envelope fail-closed behavior

1. The host may supply explicit `resource_uris`; Soleaux resolves them before repository analysis and records one `requested_resources` outcome per URI.
2. Host resource contents are bounded, hashed, redacted, trust-labeled, and added to `external_references`. The host cannot override workspace identity, budgets, native selection, trust labels, or catalog generation through envelope metadata.
3. A malformed host envelope, duplicate URI, unsupported content body, path escape, oversize request, or schema-unknown field returns `soleaux.mcp/v2` with `status="error"`; no partial successful context packet is emitted.
4. A resource that is unavailable after a valid request becomes a reference with a typed error plus a coverage gap. It does not abort unrelated repository evidence unless semantic mode or caller policy requires that resource.
5. Secret-like content is redacted before context, memory, handoff, logs, HTTP responses, or mobile/control-plane replication.

## Compatibility

All `soleaux.context/v1` fields remain present with the same meaning: `objective`, `paths`, `terms`, `retrieval_engine`, `relation_depth`, the seven typed section arrays, `external_references`, `gaps`, candidate/related/returned counts, `response_truncated`, and `coverage_complete`. V2 only adds fields or tightens fail-closed validation; it does not reinterpret a V1 field.
