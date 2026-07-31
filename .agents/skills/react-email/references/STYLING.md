# Styling and Compatibility

Treat styling as an explicit client-support contract, not a universal list of browser CSS bans. Start from the target workspace's installed React Email and Tailwind versions and the living Tailwind component guide: <https://react.email/docs/components/tailwind>.

## Tailwind

For React Email 6, import `Tailwind` and `pixelBasedPreset` from `react-email`. The preset converts rem-based utilities to pixels for clients that do not support rem reliably:

```tsx
import { Tailwind, pixelBasedPreset } from "react-email";

<Tailwind config={{ presets: [pixelBasedPreset] }}>{children}</Tailwind>;
```

Follow an older installed major's exports until a migration is requested. Keep `Head` inside `Tailwind` when the installed guide requires generated styles to be collected there.

## Compatibility Rules

- Prefer inlineable, mobile-first styles with a useful unstyled fallback.
- Use `Row` and `Column` when broad table-based layout compatibility matters.
- Media queries, dark-mode selectors, flexbox, grid, SVG, and WebP have uneven client support; use them only when the declared client matrix and rendered tests accept the fallback behavior.
- Use absolute asset URLs, preserve text equivalents, and do not depend on remote images for the primary message or action.
- Use explicit border styles and box sizing when the rendered client output needs them; do not cargo-cult classes that the installed renderer already supplies.
- Keep brand values in an owned theme or shared template contract instead of duplicating them across templates.

## Verification

Render HTML and plain text with realistic data. Inspect the generated markup and run the owner-defined compatibility or screenshot matrix for the clients actually supported by the product.
