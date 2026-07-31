---
title: Defer Non-Critical Third-Party Code
impact: MEDIUM
impactDescription: keeps non-critical code off the initial path
tags: bundle, third-party, analytics, defer
---

## Defer Non-Critical Third-Party Code

Load analytics, support widgets, and similar integrations according to their real consent and product requirements. Prefer the framework's documented script or integration API and the provider already owned by the target.

In Next.js, a layout is a Server Component by default. If an installed integration truly requires a browser-only dynamic import, put that import in a dedicated Client Component rather than calling `dynamic(..., { ssr: false })` from the layout itself.

```tsx
// client-telemetry.tsx
"use client";

import dynamic from "next/dynamic";

const BrowserTelemetry = dynamic(() => import("./browser-telemetry"), {
  ssr: false,
});

export function ClientTelemetry() {
  return <BrowserTelemetry />;
}
```

Render that leaf from the layout only after verifying the installed Next.js documentation, provider contract, consent timing, Content Security Policy, and failure behavior. Deferral is not permission to delay code required for privacy, security, or core interaction.
