---
title: Composition
description: Design compound React components around one explicit behavior contract.
type: conceptual
summary: A compact workflow for decomposing component APIs without losing semantics or accessibility.
prerequisites:
  - definitions.md
related:
  - types.md
  - state.md
  - as-child.md
  - accessibility.md
---

# Composition

Compose a component when separate descendants need to cooperate around one stable behavior contract. Do not use a compound API merely to move a boolean prop into context.

## Decision Sequence

1. Name the semantic parts and the state they share.
2. Keep domain, routing, and data-fetching behavior outside the primitive.
3. Put context at the narrowest common owner and fail clearly when a part is used outside that owner.
4. Keep native elements and their keyboard behavior unless an installed primitive already owns the more complex interaction.
5. Expose styling through stable variants, `className`, and intentional `data-*` attributes.
6. Test the public composition, not its internal context shape.

## Minimal Compound Contract

This controlled example makes the behavior it actually implements explicit. It does not claim polymorphism, roving focus, or a richer keyboard model.

```tsx
"use client";

import { createContext, useContext } from "react";

type AccordionContextValue = {
  value: string | null;
  onValueChange: (value: string | null) => void;
};

const AccordionContext = createContext<AccordionContextValue | null>(null);

function useAccordion() {
  const context = useContext(AccordionContext);
  if (!context) throw new Error("Accordion parts require <AccordionRoot>.");
  return context;
}

type AccordionRootProps = React.ComponentProps<"div"> & AccordionContextValue;

export function AccordionRoot({
  value,
  onValueChange,
  ...props
}: AccordionRootProps) {
  return (
    <AccordionContext value={{ value, onValueChange }}>
      <div {...props} />
    </AccordionContext>
  );
}

type AccordionPartProps<T extends "button" | "div"> =
  React.ComponentProps<T> & { itemId: string };

export function AccordionTrigger({
  itemId,
  onClick,
  ...props
}: AccordionPartProps<"button">) {
  const { value, onValueChange } = useAccordion();
  const open = value === itemId;

  return (
    <button
      {...props}
      type="button"
      id={`${itemId}-trigger`}
      aria-controls={`${itemId}-panel`}
      aria-expanded={open}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) onValueChange(open ? null : itemId);
      }}
    />
  );
}

export function AccordionContent({
  itemId,
  ...props
}: AccordionPartProps<"div">) {
  const { value } = useAccordion();
  return (
    <div
      {...props}
      id={`${itemId}-panel`}
      aria-labelledby={`${itemId}-trigger`}
      hidden={value !== itemId}
    />
  );
}
```

If the product requires the complete accordion keyboard pattern, animation, multiple/open modes, or slot composition, use and compose the installed accessible primitive rather than extending this teaching example ad hoc.

## Stop Conditions

- Keep a single component when parts never need independent placement or styling.
- Prefer explicit props when only one child consumes the value.
- Do not expose `asChild`, `render`, focus management, or ARIA behavior that the implementation does not actually provide.
- Split a domain-aware compound component into a generic primitive plus an app-owned adapter.
