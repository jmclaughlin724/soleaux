---
title: Reuse the Owning Client Request Cache
impact: MEDIUM-HIGH
impactDescription: avoids duplicate browser requests
tags: client, caching, deduplication, data-fetching
---

## Reuse the Owning Client Request Cache

Do not introduce browser data infrastructure for an ordinary server-owned initial read. Fetch on the server and pass the narrowest serializable result to the interactive leaf.

```tsx
function UserList({ users }: { users: UserSummary[] }) {
  return users.map((user) => <UserRow key={user.id} user={user} />);
}
```

When live client reads are a real product requirement, route every consumer through the feature's existing keyed query hook. That hook—not each component— owns deduplication, freshness, retries, invalidation, and mutation behavior.

```tsx
function UserList() {
  const { data, error, pending } = useUsersQuery();
  // Render the feature-owned loading, error, and data states.
}
```

Use whichever cache owner is already installed and established in the target. Do not add SWR, TanStack Query, or another client cache from this rule alone.
