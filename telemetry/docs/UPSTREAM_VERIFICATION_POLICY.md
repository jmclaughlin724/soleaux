# Soleaux telemetry upstream verification policy

This policy applies to the Soleaux telemetry surface under `tools/soleaux/telemetry/`: every application, package, crate, tool, script, fixture, configuration file, and document in that tree.

## Mandatory rule

Any assumption, externally derived fact, compatibility statement, API behavior, schema mapping, model capability, pricing value, subscription rule, reset interval, operating-system behavior, library behavior, security recommendation, performance target, protocol statement, or research conclusion used to design or implement a feature must be verified against an upstream canonical source before it is treated as fact.

## Evidence classes

Every material claim or value must be one of:

- `canonical-fact`: supported by an official upstream source in `tools/soleaux/telemetry/config/upstream-claims.json`.
- `measured`: observed directly from a runtime, response, export, fixture, benchmark, or authorized account surface.
- `derived`: deterministic calculation from measured or canonical inputs.
- `product-policy`: a repository-owned rule, threshold, default, or design choice.
- `estimate`: a modeled result whose formula and assumptions are disclosed.
- `unknown`: evidence is insufficient; the feature must omit the value or fail closed.

## Canonical sources

Accepted sources, in order:

1. Official provider, vendor, standards-body, or project documentation.
2. Official API schemas, specifications, source repositories, release notes, or generated references.
3. Official operating-system or runtime documentation.
4. Direct versioned runtime measurement when no canonical written source exists.

Third-party articles, search snippets, community posts, remembered behavior, generated summaries, and issue comments cannot establish a fact.

## Implementation requirements

- Every upstream-dependent implementation must reference one or more claim IDs in code comments, tests, manifests, or adjacent documentation.
- Every claim record must list affected paths, verification date, volatility, canonical URL, and verification method.
- High-volatility claims expire after 30 days; medium after 90 days; low after 365 days unless a shorter interval is specified.
- Hardcoded external limits, prices, model capacities, reset windows, API versions, units, security requirements, and schema field meanings are prohibited without claim IDs.
- Product-owned heuristics must be configurable or centralized, versioned, and labeled as `product-policy`; they must never be described as upstream facts.
- Estimated savings, waste, cost, or performance improvements must expose formulas, inputs, confidence, and methodology version.
- Fixtures derived from external systems must record upstream product version, capture date, platform, redaction status, and supporting claim IDs.
- Documentation must use canonical claim IDs instead of unsupported prose assertions.

## Fail-closed behavior

When verification is missing, stale, contradictory, or unavailable:

- do not invent a fallback;
- do not silently retain a stale value;
- return `unknown`, disable the dependent feature, or block release;
- surface the missing or expired claim ID to maintainers.

## Pull-request and release requirements

A pull request changing upstream-dependent behavior must update `tools/soleaux/telemetry/config/upstream-claims.json` and run `pnpm soleaux:telemetry:verify:upstream`.

Release is blocked when:

- a referenced claim ID is missing;
- a canonical URL is outside the registered official domains;
- affected paths do not exist or are omitted;
- product policy is presented as canonical fact;
- an estimate lacks methodology metadata;
- a fixture lacks provenance;
- documentation contains an unregistered upstream URL outside exempt files.

A stale attestation (`lastVerifiedAt` older than the claim's volatility window) is a warning, not a block: an untouched repository must not go red on a calendar schedule. Re-verify the claim against its canonical source and refresh the attestation promptly.

## Scope ownership

This policy governs the telemetry surface only. The host repository's own verification surfaces (`upstream` and `verify` skills, owning package checks) remain the authority outside `tools/soleaux/telemetry/`.
