# View Transitions in Next.js

## Setup

`<ViewTransition>` works out of the box for `startTransition`/`Suspense` updates. To also animate `<Link>` navigations:

```js
// next.config.js
const nextConfig = {
  experimental: { viewTransition: true },
};
module.exports = nextConfig;
```

This wraps every `<Link>` navigation in `document.startViewTransition`. Any VT with `default="auto"` fires on **every** link click — use `default="none"` to prevent competing animations.

Use the repository's installed React and Next.js versions. Do not install a canary package to obtain this API. Before implementation, read the installed `view-transitions` guide, `next/link` reference, `useRouter` reference, and matching type declarations.

---

## Next.js Implementation Additions

When following `implementation.md`, apply these additions:

**After Step 2:** Enable the experimental flag above.

**Step 4:** Use `transitionTypes` on `<Link>` — see "The `transitionTypes` Prop" section below for usage and availability.

**After Step 6:** For same-route dynamic segments (e.g., `/collection/[slug]`), use the `key` + `name` + `share` pattern — see Same-Route Dynamic Segment Transitions below.

---

## Layout-Level ViewTransition

Do not add a persistent layout wrapper by default when page boundaries already own the route effect. Layouts persist across navigation, so their boundary usually observes an update rather than the page mount/unmount lifecycle, and a default layout effect can compete visually with child transitions.

Nested transitions can be intentional. When using them, give the parent and child distinct responsibilities and verify the actual link, history, and Suspense paths in the installed React and Next.js versions. Keep type-keyed directional enter/exit behavior at the boundary that actually mounts and unmounts for that navigation.

---

## The `transitionTypes` Prop on `next/link`

No wrapper component needed, works in Server Components:

```tsx
<Link href="/products/1" transitionTypes={["transition-to-detail"]}>
  View Product
</Link>
```

Replaces the manual pattern of `onNavigate` + `startTransition` + `addTransitionType` + `router.push()`. Reserve manual `startTransition` for non-link interactions (buttons, forms).

**Availability:** Treat the installed Next.js documentation and `LinkProps` declaration as authority. When they expose `transitionTypes`, enable `experimental.viewTransition` and use the installed contract. If they do not, do not copy this example or install a canary dependency; use the version-matched navigation API documented by that checkout.

---

## Programmatic Navigation

```tsx
"use client";

import { useRouter } from "next/navigation";

function ForwardNavigationButton({ href }: { href: string }) {
  const router = useRouter();

  return (
    <button
      type="button"
      onClick={() => router.push(href, { transitionTypes: ["nav-forward"] })}
    >
      Continue
    </button>
  );
}
```

---

## Server-Side Filtering with `router.replace`

For search, sort, or filter navigation that re-renders on the server through URL parameters, use the installed `router.replace` transition option:

```tsx
"use client";

import { useRouter } from "next/navigation";

function SortControl() {
  const router = useRouter();

  return (
    <button
      type="button"
      onClick={() =>
        router.replace("?sort=recent", {
          transitionTypes: ["filter-change"],
        })
      }
    >
      Recent
    </button>
  );
}
```

List items wrapped in `<ViewTransition key={item.id}>` will animate reorder. This is the server-component alternative to the client-side `useDeferredValue` pattern in `patterns.md`.

---

## Two-Layer Pattern (Directional + Suspense)

Directional slides + Suspense reveals coexist because they fire at different moments. Place the directional VT in the **page component** (not layout):

```tsx
<ViewTransition
  enter={{ "nav-forward": "slide-from-right", default: "none" }}
  exit={{ "nav-forward": "slide-to-left", default: "none" }}
  default="none"
>
  <div>
    <Suspense
      fallback={
        <ViewTransition exit="slide-down">
          <Skeleton />
        </ViewTransition>
      }
    >
      <ViewTransition enter="slide-up" default="none">
        <Content />
      </ViewTransition>
    </Suspense>
  </div>
</ViewTransition>
```

---

## `loading.tsx` as Suspense Boundary

Next.js `loading.tsx` is an implicit `<Suspense>` boundary. Wrap the skeleton in `<ViewTransition exit="...">` in `loading.tsx`, and the content in `<ViewTransition enter="..." default="none">` in the page:

```tsx
// loading.tsx
<ViewTransition exit="slide-down"><PhotoGridSkeleton /></ViewTransition>

// page.tsx
<ViewTransition enter="slide-up" default="none"><PhotoGrid photos={photos} /></ViewTransition>
```

Same rules as explicit `<Suspense>`: use simple string props (not type maps) since Suspense reveals fire without transition types.

---

## Shared Elements Across Routes

```tsx
// List page
{
  products.map((product) => (
    <Link
      key={product.id}
      href={`/products/${product.id}`}
      transitionTypes={["nav-forward"]}
    >
      <ViewTransition name={`product-${product.id}`}>
        <Image
          src={product.image}
          alt={product.name}
          width={400}
          height={300}
        />
      </ViewTransition>
    </Link>
  ));
}

// Detail page — same name
<ViewTransition name={`product-${product.id}`}>
  <Image src={product.image} alt={product.name} width={800} height={600} />
</ViewTransition>;
```

---

## Same-Route Dynamic Segment Transitions

When navigation between dynamic segments reuses the same page boundary, an enter/exit pair may not describe the update. Use a stable identity decision such as `key` plus a matched `name`/`share` only after confirming the installed route lifecycle:

```tsx
<Suspense fallback={<Skeleton />}>
  <ViewTransition
    key={slug}
    name={`collection-${slug}`}
    share="auto"
    default="none"
  >
    <Content slug={slug} />
  </ViewTransition>
</Suspense>
```

- `key={slug}` forces unmount/remount on change
- `name` + `share="auto"` creates a shared element crossfade
- VT inside `<Suspense>` (without keying Suspense) keeps old content visible during loading

---

## Server Components

- `<ViewTransition>` works in both Server and Client Components
- `<Link transitionTypes>` works in Server Components — no `'use client'` needed
- `addTransitionType` and `startTransition` for programmatic nav require Client Components
