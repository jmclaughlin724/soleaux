# Soleaux provenance and verification policy

Soleaux must not present an upstream provider, operating-system, protocol, pricing, subscription, model, context-window, quota, reset, token-accounting, or API behavior as fact unless it is verified against an upstream canonical source.

## Evidence classes

Every externally derived statement must be classified as one of:

1. `canonical-fact` — verified against an official provider, standards body, operating-system vendor, or library maintainer source.
2. `measured` — directly observed from a provider response, provider export, local process collector, or user-authorized account surface.
3. `derived` — deterministic calculation from measured inputs.
4. `soleaux-heuristic` — a configurable Soleaux policy or detection threshold.
5. `estimate` — a modeled projection with assumptions and uncertainty.
6. `unknown` — insufficient evidence; the feature must not claim a result.

## Non-negotiable rules

- Canonical facts require a source ID from `config/canonical-sources.json`.
- Provider totals always outrank reconstructed totals.
- Subscription remaining capacity and reset times must come from a supported provider API or a user-authorized provider surface.
- Tool-level token attribution must state whether it is provider-measured, directly instrumented, or allocated.
- Soleaux heuristics must be configurable, versioned, and described as policy choices rather than provider facts.
- Estimates must expose their formula, inputs, confidence, and methodology version.
- Unsupported claims must fail closed: omit the value or return `unknown`; never fabricate a fallback.
- Sources must be reviewed for freshness before each release and whenever a provider changes pricing, plans, limits, models, or APIs.

## Canonical-source hierarchy

Use sources in this order:

1. Official API or product documentation from the upstream provider.
2. Official help-center or pricing documentation from the upstream provider.
3. Official source repository or release notes from the upstream maintainer.
4. Formal standard or specification.
5. Direct runtime measurement.

Third-party articles, search snippets, community posts, issue comments, and remembered behavior cannot establish product facts.

## Current heuristic parameters

The following values are Soleaux defaults, not upstream facts:

- duplicate-call window
- oversized-output byte threshold
- tool-result token budget
- broad-search avoidable fraction
- compaction avoidable fraction
- CPU hotspot threshold
- memory hotspot threshold
- context warning thresholds
- latency anomaly multiplier and minimum delta
- recovery factor used in projected savings
- bytes-per-token approximation when an exact tokenizer is unavailable

These must move to a versioned configuration object before release. Reports must include the active values and label them `soleaux-heuristic`.

## Required provenance on report fields

Each material report value must include or inherit:

```json
{
  "evidenceClass": "measured | derived | soleaux-heuristic | estimate | unknown",
  "sourceIds": ["openai-codex-rate-card"],
  "methodologyVersion": "scan-v1",
  "confidence": 0.0,
  "observedAt": 0,
  "notes": ""
}
```

## Release gate

A release is blocked when:

- a provider claim lacks a canonical source ID;
- a source URL is no longer official or no longer supports the claim;
- an estimate is rendered without an estimate label;
- a heuristic is described as a provider rule;
- overlapping findings can inflate a total without deduplication disclosure;
- provider totals do not reconcile with imported or synchronized records;
- model context limits or pricing are hardcoded without a dated canonical source;
- a consumer subscription value is inferred from API usage without provider evidence.

## Review cadence

- Verify volatile provider and pricing sources at build/release time.
- Review model catalogs, rate cards, quota behavior, and plan documentation at least monthly.
- Store `lastVerifiedAt` and `contentFingerprint` for each source.
- Mark dependent features degraded when a canonical source becomes unavailable or materially changes.
