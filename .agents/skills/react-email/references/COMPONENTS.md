# Component Decisions

Use the component exports and props from the target workspace's installed `react-email` version. The living component index is <https://react.email/docs/components>.

## Structure

- Use `Html`, `Head`, `Preview`, and `Body` to establish the document and inbox preview contract.
- Use `Container`, `Section`, `Row`, and `Column` when their table-backed output fits the supported client matrix.
- Use semantic content components such as `Heading`, `Text`, `Link`, `Button`, and `Img` instead of recreating their email-specific output.
- Keep every URL absolute in rendered production output. Give informative images useful alternative text and decorative images an empty alternative.
- Preserve a readable plain-text order even when the visual layout uses columns.

## Selection Rule

Inspect the installed export and official page before using a component or prop. Do not infer current APIs from this skill, an older template, or the deprecated `@react-email/components` package. React Email 6 consolidates components and rendering utilities under `react-email`; an older installed major remains the authority for an existing owner until migration is explicitly in scope.

## Verification

Render the template with realistic props, inspect HTML and plain text, and use the target's declared client-compatibility checks. A successful TypeScript build does not prove email-client rendering.
