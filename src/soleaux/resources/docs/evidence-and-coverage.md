---
title: Interpret evidence and coverage
description: Interpret Soleaux evidence records, resolution status, source authority, and frame coverage before treating results or missing rows as authoritative.
sidebar:
  label: Evidence and coverage
  order: 3
---

Every fact row includes one `soleaux.evidence/v1` record. Read the evidence and the frame coverage before treating a result or an absence as authoritative.

## Check each evidence record

An evidence record names:

- `evidence_kind`: `structural`, `semantic`, `metadata`, or `heuristic`
- `resolution_status`: `resolved`, `partial`, `unresolved`, `unavailable`, or `candidate`
- `authority`: `source`, `manifest`, `governance`, `generated`, `inferred`, or `unresolved`
- the producer and producer version
- a workspace-relative path and one-based source range
- the exact captured source SHA-256 and source fingerprint
- the request snapshot and a bounded confidence value

Structural imports and calls can remain candidates. Do not promote them to resolved semantic edges. Derived dependency, consumer, impact, cycle, and dead-code views use only the prerequisites declared by the fixed table catalog.

## Check frame coverage

Coverage status is one of:

- `complete`: the eligible request scope was examined within its declared limits
- `partial`: some eligible work was omitted
- `truncated`: a row, file, byte, depth, or time limit stopped the request
- `unsupported`: the requested producer or capability is unavailable
- `failed`: the producer attempted the work but failed
- `changed_during_analysis`: captured source changed before the result completed

Coverage also reports eligible and examined files, parse failures, candidate and resolution counts, unsupported and failed counts, omitted reasons, the deadline, enforced limits, and elapsed time.

Zero rows means no matching facts only under `complete` coverage. Under every other status, report the absence as inconclusive.
