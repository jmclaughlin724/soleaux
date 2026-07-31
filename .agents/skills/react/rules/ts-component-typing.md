---
title: TypeScript Component Typing
impact: MEDIUM
impactDescription: consistent type safety without legacy wrappers
tags: typescript, React.FC, events, generics
---

## TypeScript Component Typing

**Prefer plain function declarations for application components:**

Modern `React.FC` does not add an implicit `children` prop. Plain functions are still the default here because their props and return type are direct, they support generic components naturally, and they avoid a wrapper type that adds no value. Do not reject `React.FC` by repeating the obsolete implicit-children claim; preserve it when an owning public API deliberately standardizes on that type.

```tsx
// Correct
function LoanSummary({ principal, rate }: LoanSummaryProps) {
  return <div>...</div>;
}

// Unnecessary wrapper for an ordinary application component
const LoanSummary: React.FC<LoanSummaryProps> = ({ principal, rate }) => {
  return <div>...</div>;
};
```

Infer the return type for ordinary components. Add an explicit return contract only when a public abstraction or overload needs one; a blanket `React.ReactElement` annotation unnecessarily excludes valid async Server Components and other React node results.

**Type event handlers explicitly:**

```tsx
// Correct
function handleClick(event: React.MouseEvent<HTMLButtonElement>) { ... }
function handleChange(event: React.ChangeEvent<HTMLInputElement>) { ... }
function handleSubmit(event: React.FormEvent<HTMLFormElement>) { ... }

// Incorrect — loses type narrowing
function handleClick(event: any) { ... }
function handleClick(event: React.SyntheticEvent) { ... } // too broad
```

**Use generics for reusable components:**

```tsx
interface DataListProps<T> {
  items: T[];
  renderItem: (item: T) => React.ReactNode;
  keyExtractor: (item: T) => string;
}

function DataList<T>({ items, renderItem, keyExtractor }: DataListProps<T>) {
  return (
    <ul>
      {items.map((item) => (
        <li key={keyExtractor(item)}>{renderItem(item)}</li>
      ))}
    </ul>
  );
}
```

**Use `as const` for discriminated config objects:**

```tsx
const LOAN_STATUS = {
  ACTIVE: "active",
  CLOSED: "closed",
  PENDING: "pending",
} as const;

type LoanStatus = (typeof LOAN_STATUS)[keyof typeof LOAN_STATUS];
```

This aligns with the repo's Biome rule against TypeScript enums (use `as const` objects instead).
