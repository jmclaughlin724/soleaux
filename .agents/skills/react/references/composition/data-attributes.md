---
title: Data Attributes
description: Expose stable visual state and component slots without prop explosion.
type: reference
summary: Use intentional data-state and data-slot contracts while preserving native semantics.
prerequisites:
  - composition.md
  - styling.md
related:
  - design-tokens.md
  - accessibility.md
---

# Data Attributes

Use `data-*` attributes when consumers need a stable CSS or test hook for state or structure that the DOM does not already express.

## State

Emit a small documented vocabulary from the component owner.

```tsx
<button
  type="button"
  aria-expanded={open}
  data-state={open ? "open" : "closed"}
  disabled={disabled}
  className="data-[state=open]:bg-accent"
>
  Filters
</button>
```

Native behavior remains authoritative: use `disabled`, `checked`, `selected`, and semantic elements where available. A `data-disabled` attribute alone does not disable interaction or communicate the state to assistive technology.

## Slots

Use `data-slot` as a stable component-part identifier when parent composition or consumer styling genuinely targets that part.

```tsx
function FieldSet(props: React.ComponentProps<"fieldset">) {
  return <fieldset data-slot="field-set" {...props} />;
}

function FieldDescription(props: React.ComponentProps<"p">) {
  return <p data-slot="field-description" {...props} />;
}
```

Choose slot names from the public component vocabulary. Do not expose internal wrapper structure that consumers should not depend on.

## Boundaries

- Do not put secrets, personal data, or large serialized objects in attributes.
- Do not create multiple aliases for the same state.
- Do not use data attributes as an event bus or application state store.
- Preserve the attributes emitted by an installed primitive and verify their current contract before styling against them.
