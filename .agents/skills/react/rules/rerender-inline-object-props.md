---
title: Avoid Inline Object/Array Props
impact: MEDIUM
impactDescription: prevents unnecessary child re-renders from unstable references
tags: rerender, memo, props, references
---

## Avoid Inline Object/Array Props

Inline object or array literals in JSX create a new reference on every render. When the child is wrapped in `React.memo` or uses `useEffect` dependencies, this defeats memoization.

**Incorrect — new object every render:**

```tsx
function Dashboard() {
  return (
    <Chart
      options={{ animate: true, showGrid: true }}
      colors={["#3b82f6", "#10b981"]}
    />
  );
}
```

**Correct — hoist to module scope when values are static:**

```tsx
const CHART_OPTIONS = { animate: true, showGrid: true };
const CHART_COLORS = ["#3b82f6", "#10b981"];

function Dashboard() {
  return <Chart options={CHART_OPTIONS} colors={CHART_COLORS} />;
}
```

**Correct — `useMemo` when values depend on props or state:**

```tsx
function Dashboard({ showGrid }: { showGrid: boolean }) {
  const options = useMemo(() => ({ animate: true, showGrid }), [showGrid]);
  return <Chart options={options} />;
}
```

**When this does NOT matter:** If the child is not memoized and does not use the prop in a dependency array, inline literals are fine. Do not add `useMemo` defensively — only when profiling or an established memoization boundary justifies it.
