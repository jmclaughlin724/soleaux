# Phase 3 Scoring Rubric

Each task is scored out of 100.

## Common dimensions

| Dimension | Points |
|---|---:|
| Required facts or implementation behavior correct | 40 |
| Repository evidence and provenance | 20 |
| Scope and constraints respected | 15 |
| Validation/oracle success | 15 |
| Honest gaps and no unsupported claims | 10 |

## Task-specific gates

### P3-T01

Hard fail when the response invents ownership, misses both rendered workspace components, or names no data-provider evidence.

### P3-T02

Hard fail when the response asserts complete route/boundary coverage without evidence or ignores a reported gap.

### P3-T03

Hard fail when:

- the test does not detect duplicate IDs;
- navigation/class names/runtime data behavior change;
- unrelated files change;
- authoritative validation fails.

## Aggregate gate

Treatment passes correctness when:

```text
mean treatment score >= mean baseline score
AND
no treatment hard-fail rate exceeds baseline
AND
P3-T03 treatment oracle passes
```

Context reduction is reported separately and does not compensate for lower correctness.

## Human review

Two-pass review:

1. oracle/automated scoring;
2. blind human review with arm labels hidden when practical.

Disagreements and overrides are retained with reasons.
