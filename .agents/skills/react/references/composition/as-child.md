---
title: Child Composition
description: Preserve semantics when a component delegates rendering to its child.
type: reference
summary: Decision rules for installed slot or render APIs without nested interactive elements.
prerequisites:
  - composition.md
  - accessibility.md
related:
  - polymorphism.md
  - types.md
---

# Child Composition

Use child composition only when one semantic element must receive behavior and styles from another component. Prefer a fixed native element or a style-variant function when that is sufficient.

## Select the Installed Contract

- Read the generated component and primitive types before choosing an API.
- Base UI slots use the generated `render` contract; Radix-backed slots use `asChild` only when the generated component exposes it. See the [repository-specific primitive decision](../../../shadcn/rules/base-vs-radix.md).
- Do not install a slot package from this reference or implement a second slot abstraction beside the component library.

## Required Invariants

The selected primitive must define and test:

- exactly one rendered semantic element;
- merged `className`, style, data, and ARIA props;
- composed event handlers with documented cancellation order;
- a ref that reaches the rendered element; and
- a supported child shape.

Never produce nested controls such as `<button><a /></button>` or `<button><button /></button>`.

## Links and Destructive Actions

Navigation remains a link. If only button styling is needed, apply the existing variant function directly to the link instead of changing its semantics.

```tsx
<Link href="/settings" className={buttonVariants({ variant: "outline" })}>
  Settings
</Link>
```

A destructive confirmation remains an action button and runs the mutation from the dialog action. Do not model deletion as a navigation link.

## Verification

Inspect the rendered DOM, then test pointer activation, keyboard activation, focus return, disabled behavior, accessible name, ref delivery, and handler composition. If any of those contracts are unclear, use the primitive's default element.
