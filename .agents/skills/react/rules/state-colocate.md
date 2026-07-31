---
title: Colocate State
impact: MEDIUM
impactDescription: reduces unnecessary re-renders and simplifies component trees
tags: state, colocation, derived, context
---

## Colocate State

Keep state as close as possible to where it is read and written. Lifting state higher than necessary causes parent re-renders that cascade to unrelated siblings.

**Incorrect — state lifted too high:**

```tsx
function LoanWorkspace() {
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTab, setSelectedTab] = useState("overview");

  return (
    <>
      <SearchBar query={searchQuery} onQueryChange={setSearchQuery} />
      <TabNav selected={selectedTab} onSelect={setSelectedTab} />
      <LoanDetails />
    </>
  );
}
```

Every keystroke in `SearchBar` re-renders `TabNav` and `LoanDetails`.

**Correct — state owned by the consumer:**

```tsx
function LoanWorkspace() {
  return (
    <>
      <SearchBar />
      <TabNav />
      <LoanDetails />
    </>
  );
}

function SearchBar() {
  const [query, setQuery] = useState("");
  return <Input value={query} onChange={(e) => setQuery(e.target.value)} />;
}
```

Lift state only when siblings genuinely need to share it.

**Derive, don't sync:**

Compute values from existing state instead of mirroring with `useEffect`.

```tsx
// Incorrect — effect syncs derived value
const [items, setItems] = useState<Item[]>([]);
const [total, setTotal] = useState(0);
useEffect(() => {
  setTotal(items.reduce((sum, i) => sum + i.amount, 0));
}, [items]);

// Correct — derive during render
const [items, setItems] = useState<Item[]>([]);
const total = items.reduce((sum, i) => sum + i.amount, 0);
```

See also: `rerender-derived-state-no-effect.md` and `rerender-derived-state.md` for deeper patterns.
