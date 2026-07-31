---
title: Dependency-Based Parallelization
impact: CRITICAL
impactDescription: 2-10× improvement
tags: async, parallelization, dependencies, promises
---

## Dependency-Based Parallelization

Represent the dependency graph with native promises. Start each independent root immediately and derive dependent work from the promise it actually needs.

**Incorrect (profile waits for config unnecessarily):**

```typescript
const [user, config] = await Promise.all([fetchUser(), fetchConfig()]);
const profile = await fetchProfile(user.id);
```

**Correct (config and profile overlap without another dependency):**

```typescript
const userPromise = fetchUser();
const configPromise = fetchConfig();
const profilePromise = userPromise.then((user) => fetchProfile(user.id));

const [user, config, profile] = await Promise.all([
  userPromise,
  configPromise,
  profilePromise,
]);
```

Keep the graph explicit. If it becomes hard to read, split it by use case or move orchestration into the owning service rather than introducing a repository-wide promise DSL.
