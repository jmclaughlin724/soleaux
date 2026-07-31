---
title: Give Global Browser Events One Owner
impact: LOW
impactDescription: avoids duplicated global work
tags: client, events, subscriptions, ownership
---

## Give Global Browser Events One Owner

A local listener is simplest when only one component needs an event. Centralize the listener only when multiple mounted consumers duplicate meaningful work or must coordinate priority.

For a shared keyboard, resize, connectivity, or visibility concern:

1. choose one client-only provider, external store, or existing query/event owner;
2. attach the browser listener once in that owner and remove it during cleanup;
3. expose a stable subscription contract to consumers;
4. keep the latest callback without resubscribing on every render; and
5. test mounting, unmounting, duplicate shortcuts, focus context, and server rendering.

Do not install a data-fetching library merely to deduplicate DOM listeners, and do not move route-specific shortcuts into a global singleton without a real cross-route contract.
