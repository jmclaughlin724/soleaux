---
title: Controlled and Uncontrolled State
description: Choose and preserve one clear state ownership contract.
type: reference
summary: Prefer one state mode; support both only when a real public API requires it.
prerequisites:
  - composition.md
related:
  - types.md
  - accessibility.md
---

# Controlled and Uncontrolled State

State ownership should be obvious from the component API.

- Use a controlled contract when the parent coordinates state, persists it, or derives other behavior from it.
- Use an uncontrolled contract when the state is local and only an initial value matters.
- Support both only for a reusable public primitive with real consumers in both modes.

## Dual-Mode Contract

Make the modes mutually exclusive in the public type and never switch between them during one mount.

```tsx
type ControlledStepperProps = {
  value: number;
  defaultValue?: never;
  onValueChange: (value: number) => void;
};

type UncontrolledStepperProps = {
  value?: never;
  defaultValue?: number;
  onValueChange?: (value: number) => void;
};

type StepperProps = ControlledStepperProps | UncontrolledStepperProps;

function Stepper(props: StepperProps) {
  const [internalValue, setInternalValue] = useState(props.defaultValue ?? 0);
  const controlled = props.value !== undefined;
  const value = controlled ? props.value : internalValue;

  function setValue(next: number) {
    if (!controlled) setInternalValue(next);
    props.onValueChange?.(next);
  }

  return (
    <button type="button" onClick={() => setValue(value + 1)}>
      {value}
    </button>
  );
}
```

For an installed primitive, use its existing state helper and contract. Do not add a state utility dependency from this reference. Test controlled updates, uncontrolled initialization, callback order, form reset behavior when applicable, and the no-mode-switch invariant.
