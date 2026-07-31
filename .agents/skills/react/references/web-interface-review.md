# Web Interface Review

## Contract

Use this reference only for a scoped React interface review. Inspect the named files and rendered surface when available, apply the current source guidance, and return findings without editing unless the user explicitly requests fixes.

## Living Source

Fetch the current command before every review:

<https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md>

Use the official rendered guidelines to resolve malformed raw rendering or missing context:

<https://vercel.com/design/guidelines>

If neither source is available, disclose that the live guidance could not be verified. Use the fallback categories below only as bounded review coverage; do not claim current Vercel compliance.

## Review Workflow

1. Confirm the exact files, routes, or components in scope. A review request is read-only.
2. Fetch and read the living source before inspecting findings.
3. Read every in-scope file and the directly owned styles, tests, and component contracts needed to evaluate it. Render the affected UI when the runtime is available.
4. Check every applicable source rule, including interaction, accessibility, focus, forms, motion, layout, content resilience, hydration, localization, images, performance, theming, and responsive behavior.
5. Separate framework-agnostic interface guidance from Vercel-specific copy and brand preferences. Apply the latter only when the product or user asks for Vercel style.
6. Report correctness and accessibility risks before polish. Group findings by file and use exact `file:line` locations. State the defect and narrowest useful remediation; omit background unless the fix is non-obvious.
7. Mark an in-scope file as passing when it has no findings, and end with any runtime or device coverage that could not be verified.

## Output Shape

```text
## path/to/Button.tsx
path/to/Button.tsx:42 - icon-only button lacks an accessible name
path/to/Button.tsx:67 - transition animates all properties; list transform and opacity explicitly

## path/to/Card.tsx
pass
```

Do not add a preamble. Do not hide material issues behind a score.

## Fallback Coverage

- Native semantics, keyboard operation, visible focus, focus movement and restoration, accessible names, live announcements, labels, autocomplete, error focus, paste, zoom, and generous targets.
- URL-addressable state, real links for navigation, resilient loading, empty, error, sparse, dense, long-content, Back/Forward, and unsaved-change behavior.
- Reduced motion, compositor-friendly and interruptible animation, explicit transition properties, correct transform origins, and no decorative motion without purpose.
- Responsive layout, safe areas, overflow, contrast, dark mode, locale-aware formatting, hydration safety, image dimensions, lazy loading, preconnects, fonts, large-list virtualization, and bounded keystroke or layout work.
