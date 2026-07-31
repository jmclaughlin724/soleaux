# Rendering and Provider Handoff

Use the installed React Email render contract. The current React Email 6 API is documented at <https://react.email/docs/utilities/render>:

```tsx
import { render, toPlainText } from "react-email";

const html = await render(<WelcomeEmail {...props} />);
const text = toPlainText(html);
```

An older installed major may expose rendering from `@react-email/render`; do not migrate that owner during a template-only task.

## Boundary

Return or pass the rendered HTML and plain text to the repository-owned provider integration. Provider SDK setup, recipient selection, domains, batching, templates, retries, tracking, and external sends belong to that integration and its current official documentation. React Email's provider index is <https://react.email/docs/integrations/overview>.

Rendering a template does not authorize an external send. Keep credentials out of templates and tests, validate provider inputs at their boundary, and use the owner's verified sender-domain contract.

## Verification

Render realistic minimal and complete props, inspect HTML and plain text, verify all links and unsubscribe requirements owned by the product, and exercise the provider adapter only when that integration is explicitly in scope.
