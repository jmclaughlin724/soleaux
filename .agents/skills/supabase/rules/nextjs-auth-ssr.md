# Next.js Supabase Auth SSR

Load this rule for Next.js App Router Supabase Auth SSR implementation or review. Current official Supabase documentation, the installed `@supabase/ssr` API, the installed Next.js documentation, and the complete applicable owner instruction chain remain authoritative.

## Clients And Secrets

- Use `@supabase/ssr`; never add or import `@supabase/auth-helpers-nextjs`.
- Create browser clients with `createBrowserClient` and only the project URL and publishable key. Keep secret and `service_role` keys out of `NEXT_PUBLIC_` variables and client code.
- Create server clients with `createServerClient`. Await `cookies()` in Next.js 16.

## Cookie Adapter

- Configure each `createServerClient` cookie adapter with `getAll` and `setAll`. Do not use the deprecated adapter keys `get`, `set`, or `remove`.
- Scope that prohibition to the Supabase adapter keys. `request.cookies.set`, `response.cookies.set`, and `cookieStore.set` remain the Next.js cookie-write APIs.
- Do not rely on cookie writes during Server Component rendering. Refresh session cookies through Proxy, a Route Handler, or a Server Action.
- In Proxy, mirror every refreshed cookie to the request and response, apply every header passed to `setAll` to the response, and return the response carrying those cookies and headers.

## Identity And Authorization

- Immediately after creating the server client in Proxy, call `supabase.auth.getClaims()` before unrelated logic. Use verified claims for identity checks.
- Call `getUser()` only when the current Auth user record or server-side session validity is required. Never authorize from the user object returned by `getSession()`.
- Treat Proxy checks as optimistic redirects and session refresh only. Authenticate and authorize again inside protected Server Components, Server Actions, and Route Handlers.

## Caching And Verification

- Never cache a response that handles authentication or can refresh a session. Preserve every cache-control header supplied to `setAll`.
- Verify the browser client, server client, and Proxy against the installed types. Exercise signed-out, signed-in, expired-token refresh, redirect, and protected-boundary authorization paths, including persistence of refreshed cookies in both the request and returned response.
