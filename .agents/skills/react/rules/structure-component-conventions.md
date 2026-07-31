---
title: Component File Conventions
impact: LOW
impactDescription: consistent component structure across the codebase
tags: structure, exports, props, conventions
---

## Component File Conventions

Keep component files predictable so reviewers and tooling find what they expect.

**Named exports over default exports:**

```tsx
// Correct
export function QuoteCard({ title, amount }: QuoteCardProps) {
  return <div>...</div>;
}

// Incorrect
export default function QuoteCard({ title, amount }: QuoteCardProps) {
  return <div>...</div>;
}
```

Use named exports where the owning package or app follows that convention; they make symbol search and rename-oriented refactoring explicit. Preserve a default export where the framework or an established public contract requires one.

**Destructure props in the signature:**

```tsx
// Correct
function ContactRow({ name, email, status }: ContactRowProps) { ... }

// Incorrect — hides the shape and forces `props.` prefixes
function ContactRow(props: ContactRowProps) { ... }
```

**Colocate the props interface:**

Define the props type in the same file, directly above the component. Move it to a shared types file only when multiple components import it.

```tsx
interface QuoteCardProps {
  title: string;
  amount: number;
  children?: React.ReactNode;
}

export function QuoteCard({ title, amount, children }: QuoteCardProps) { ... }
```

**One primary component per file.** Private helpers used only by that component can stay in the same file. If a helper grows complex enough to test independently, extract it to a sibling file in the same `_components/` directory.
