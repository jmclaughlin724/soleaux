---
title: Require an Owner for Cross-Request Caching
impact: HIGH
impactDescription: prevents unsafe process-local cache semantics
tags: server, cache, invalidation, tenancy
---

## Require an Owner for Cross-Request Caching

Request memoization and cross-request caching are different contracts. Before adding a cache that outlives one render or request, identify:

- the framework or service that owns storage;
- cache scope across users, tenants, regions, and processes;
- the stable key and authorization boundary;
- freshness, invalidation, failure, and capacity behavior; and
- evidence that repeated work is material.

Do not use a module-level `Map` or an ad hoc LRU as a universal server cache. Warm process reuse is not durable, region-consistent, or tenant-safe by itself.

For Next.js, read the installed caching documentation and use the version- matched framework contract selected by the owning route. Use an external cache only when the application already owns one and the consistency requirement needs cross-process storage. Use `React.cache` only for its request/render deduplication contract, not as a cross-request substitute.
