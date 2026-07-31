---
title: Base UI and Radix Composition
impact: HIGH
impactDescription: preserve the generated shadcn public API instead of leaking primitive assumptions
tags: shadcn, base-ui, radix, composition
---

## Authority

Read the target's live `components.json`, generated component, and current official page for that component. The shadcn wrapper is the public contract; differences in the underlying Base UI and Radix primitives matter only when the generated wrapper exposes them.

The shared owner currently uses the `base-vega` style. Do not apply Radix `asChild` examples to it by default.

## Composition Slots

Base UI-backed trigger and slot components generally use `render`:

```tsx
<DialogTrigger render={<Button />}>Open</DialogTrigger>
```

Radix-backed equivalents generally use `asChild`:

```tsx
<DialogTrigger asChild>
  <Button>Open</Button>
</DialogTrigger>
```

Confirm the generated component before applying either pattern. Some structural shadcn components expose neither contract.

## Links Styled as Buttons

For the Base UI Button, render a real link and apply `buttonVariants`. Do not render an anchor through `Button`; the primitive retains button semantics.

```tsx
<Link className={buttonVariants({ variant: "default" })} href="/docs">
  Read the docs
</Link>
```

Radix targets may expose a component-specific `asChild` contract, but the live generated component remains authoritative.

## Normalized shadcn APIs

Do not expose raw primitive differences when shadcn normalizes them:

- Base Select uses an `items` collection and may still render `<SelectValue placeholder="..." />`.
- The public ToggleGroup API uses `type="single"` or `type="multiple"` as shown by the current shadcn component documentation.
- The public Slider API represents one or more thumb values with arrays.

```tsx
const items = [
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
];

<Select items={items}>
  <SelectTrigger>
    <SelectValue placeholder="Theme" />
  </SelectTrigger>
  <SelectContent>
    <SelectGroup>
      {items.map((item) => (
        <SelectItem key={item.value} value={item.value}>
          {item.label}
        </SelectItem>
      ))}
    </SelectGroup>
  </SelectContent>
</Select>;

<ToggleGroup type="single">
  <ToggleGroupItem value="left">Left</ToggleGroupItem>
  <ToggleGroupItem value="right">Right</ToggleGroupItem>
</ToggleGroup>;

<Slider defaultValue={[33]} max={100} step={1} />;
```

When the target's installed component or official version-matched page differs, follow that evidence and update this reference in the same change.
