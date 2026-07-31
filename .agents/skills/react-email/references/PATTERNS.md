# Template Patterns

## Props and Preview Data

- Define a narrow serializable props contract for each template.
- Keep realistic preview data on the template when the installed version supports it, but do not use preview defaults as production personalization.
- Validate untrusted values before rendering and escape provider-owned URLs through the owning integration.

## Layout

- Start with a single-column reading order and add columns only when the declared client matrix supports the rendered result.
- Prefer React Email layout components over browser-first flex or grid assumptions.
- Keep the primary action obvious without relying on images, hover, or animation.
- Include an inbox preview string that complements rather than repeats the subject.

## Shared Composition

Extract a shared email component only when multiple templates share a stable semantic contract. Keep provider calls, retries, tracking, recipient selection, and domain workflows outside render-only components.

## Asset URLs

Resolve production asset URLs from the owning application's public URL contract. Do not hard-code localhost or a task-specific host. Image format is a client-matrix decision: provide a broadly compatible fallback when SVG, WebP, or another format is not accepted by the supported clients.

## Verification

Render minimal and complete props, inspect HTML and plain text, exercise every conditional branch, and verify the result with the owner-defined client matrix.
