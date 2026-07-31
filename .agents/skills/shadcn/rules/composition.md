# Component Composition

## Contents

- Items always inside their Group component
- Callouts use Alert
- Empty states use Empty component
- Toast notifications use sonner
- Choosing between overlay components
- Dialog, Sheet, and Drawer always need a Title
- Icon-only buttons must have an accessible name
- Card structure
- Test harnesses mirror production composition
- Button has no isPending or isLoading prop
- TabsTrigger must be inside TabsList
- Avatar always needs AvatarFallback
- Use Separator instead of raw hr or border divs
- Use Skeleton for loading placeholders
- Use Badge instead of custom styled spans

---

## Items always inside their Group component

Never render items directly inside the content container.

**Incorrect:**

```tsx
<SelectContent>
  <SelectItem value="apple">Apple</SelectItem>
  <SelectItem value="banana">Banana</SelectItem>
</SelectContent>
```

**Correct:**

```tsx
<SelectContent>
  <SelectGroup>
    <SelectItem value="apple">Apple</SelectItem>
    <SelectItem value="banana">Banana</SelectItem>
  </SelectGroup>
</SelectContent>
```

This applies to all group-based components:

| Item | Group |
| --- | --- |
| `SelectItem`, `SelectLabel` | `SelectGroup` |
| `DropdownMenuItem`, `DropdownMenuLabel`, `DropdownMenuSub` | `DropdownMenuGroup` |
| `MenubarItem` | `MenubarGroup` |
| `ContextMenuItem` | `ContextMenuGroup` |
| `CommandItem` | `CommandGroup` |

---

## Callouts use Alert

```tsx
<Alert>
  <AlertTitle>Warning</AlertTitle>
  <AlertDescription>Something needs attention.</AlertDescription>
</Alert>
```

---

## Empty states use Empty component

```tsx
<Empty>
  <EmptyHeader>
    <EmptyMedia variant="icon">
      <FolderIcon />
    </EmptyMedia>
    <EmptyTitle>No projects yet</EmptyTitle>
    <EmptyDescription>Get started by creating a new project.</EmptyDescription>
  </EmptyHeader>
  <EmptyContent>
    <Button>Create Project</Button>
  </EmptyContent>
</Empty>
```

---

## Toast notifications use sonner

```tsx
import { toast } from "sonner";

toast.success("Changes saved.");
toast.error("Something went wrong.");
toast("File deleted.", {
  action: { label: "Undo", onClick: () => undoDelete() },
});
```

---

## Choosing between overlay components

| Use case                           | Component     |
| ---------------------------------- | ------------- |
| Focused task that requires input   | `Dialog`      |
| Destructive action confirmation    | `AlertDialog` |
| Side panel with details or filters | `Sheet`       |
| Mobile-first bottom panel          | `Drawer`      |
| Quick info on hover                | `HoverCard`   |
| Small contextual content on click  | `Popover`     |

---

## Dialog, Sheet, and Drawer always need a Title

`DialogTitle`, `SheetTitle`, `DrawerTitle` are required for accessibility. Use `className="sr-only"` if visually hidden.

```tsx
<DialogContent>
  <DialogHeader>
    <DialogTitle>Edit Profile</DialogTitle>
    <DialogDescription>Update your profile.</DialogDescription>
  </DialogHeader>
  ...
</DialogContent>
```

---

## Icon-only buttons must have an accessible name

Every `<button>` element must have discernible text (axe-core `button-name` rule). Icon-only buttons need `aria-label` or `<span className="sr-only">` text.

This applies to `Button`, `CollapsibleTrigger`, `SelectTrigger`, `DropdownMenuTrigger`, and any raw `<button>` that renders only an icon.

```tsx
// ✅ Correct — aria-label
<Button aria-label="Delete item" size="icon" variant="ghost">
  <TrashIcon />
</Button>

// ✅ Correct — sr-only text
<Button size="icon" variant="ghost">
  <TrashIcon />
  <span className="sr-only">Delete item</span>
</Button>

// ❌ Wrong — title is not reliably exposed as accessible name
<Button size="icon" title="Delete item" variant="ghost">
  <TrashIcon />
</Button>

// ❌ Wrong — icon only, no accessible name
<button onClick={onRemove} type="button">
  <XIcon className="size-3.5" />
</button>
```

**`SelectTrigger` without a `<Label>` association** needs `aria-label` to describe the control's purpose. The selected value text alone ("10", "Days") is not a descriptive name.

```tsx
// ✅ Correct — aria-label describes what the select controls
<SelectTrigger aria-label="Rows per page">
  <SelectValue />
</SelectTrigger>

// ✅ Correct — Label programmatically associated via id
<Label htmlFor="operator-select">Operator</Label>
<SelectTrigger id="operator-select">
  <SelectValue />
</SelectTrigger>

// ❌ Wrong — renders as button with only "10" as accessible name
<SelectTrigger>
  <SelectValue />
</SelectTrigger>
```

**`CollapsibleTrigger` with conditional text** needs a permanent `aria-label` for the collapsed state.

**A tooltip trigger wrapping an icon** — the tooltip content does not provide an accessible name to the trigger. The rendered control needs its own `aria-label` or visible text. Base UI presets compose with `render`; Radix presets compose with `asChild`.

---

## Card structure

Use full composition — don't dump everything into `CardContent`:

```tsx
<Card>
  <CardHeader>
    <CardTitle>Team Members</CardTitle>
    <CardDescription>Manage your team.</CardDescription>
  </CardHeader>
  <CardContent>...</CardContent>
  <CardFooter>
    <Button>Invite</Button>
  </CardFooter>
</Card>
```

For a whole-card link with no nested controls, make the link the interactive root. Do not assume the structural `Card` component implements `asChild` or `render`:

```tsx
<Link
  className="block rounded-xl focus-visible:ring-2 focus-visible:outline-none"
  href="/resources"
>
  <Card>
    <CardHeader>
      <CardTitle>Resources</CardTitle>
    </CardHeader>
  </Card>
</Link>
```

---

## Test harnesses mirror production composition

UI tests should model the same shadcn primitive composition as production code. When a child action lives inside a clickable card, use shared `Card` and sibling action slots or positioned action surfaces; do not invent a raw wrapper just to catch bubbled events.

**Incorrect:**

```tsx
render(
  <div onClick={parentClick} onKeyDown={parentKeyDown}>
    <AlertDialogTrigger
      render={<Button size="icon" type="button" variant="ghost" />}
    >
      <TrashIcon />
    </AlertDialogTrigger>
  </div>
);
```

**Correct:**

```tsx
render(
  <div className="group relative">
    <Card
      onClick={parentClick}
      onKeyDown={parentKeyDown}
      role="button"
      tabIndex={0}
    >
      <CardHeader>
        <CardTitle>Template</CardTitle>
      </CardHeader>
    </Card>
    <AlertDialogTrigger
      render={
        <Button
          aria-label="Delete template"
          className="absolute top-2 right-2 opacity-0 group-hover:opacity-100"
          size="icon"
          type="button"
          variant="ghost"
        />
      }
    >
      <TrashIcon />
    </AlertDialogTrigger>
  </div>
);
```

The current `base-vega` owner uses Base UI `render`. Radix targets use `asChild`. For link-shaped actions in Base UI, render the link directly with `buttonVariants`; do not force anchor semantics through `Button`. Never nest one native interactive element inside another.

---

## Button has no isPending or isLoading prop

Compose with `Spinner` + `disabled`; do not add manual icon sizing or spacing:

```tsx
<Button disabled>
  <Spinner />
  Saving...
</Button>
```

---

## TabsTrigger must be inside TabsList

Never render `TabsTrigger` directly inside `Tabs` — always wrap in `TabsList`:

```tsx
<Tabs defaultValue="account">
  <TabsList>
    <TabsTrigger value="account">Account</TabsTrigger>
    <TabsTrigger value="password">Password</TabsTrigger>
  </TabsList>
  <TabsContent value="account">...</TabsContent>
</Tabs>
```

---

## Avatar always needs AvatarFallback

Always include `AvatarFallback` for when the image fails to load:

```tsx
<Avatar>
  <AvatarImage src="/avatar.png" alt="User" />
  <AvatarFallback>JD</AvatarFallback>
</Avatar>
```

---

## Use existing components instead of custom markup

| Instead of | Use |
| --- | --- |
| `<hr>` or `<div className="border-t">` | `<Separator />` |
| `<div className="animate-pulse">` with styled divs | `<Skeleton className="h-4 w-3/4" />` |
| `<span className="rounded-full bg-green-100 ...">` | `<Badge variant="secondary">` |
| Repeated `<div className="flex justify-between">` rows for label + value pairs | `<Table>` with `<TableBody>`, `<TableRow>`, `<TableCell>` |
| `<div>` with manual column headers + separator + data rows | `<Table>` with `<TableHeader>`, `<TableHead>`, `<TableBody>` |
