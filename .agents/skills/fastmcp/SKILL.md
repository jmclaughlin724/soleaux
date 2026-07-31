---
name: fastmcp
description: Build and verify FastMCP servers, clients, integrations, auth, and deployments.
---

# FastMCP

## Contract

Build against the FastMCP version that the owning project actually installs. Read project instructions and local dependency evidence before choosing APIs. Installed source and the matching release tag are the exact API authorities. Live FastMCP documentation is current guidance; the upstream main branch is future-facing unless the task explicitly targets an upgrade.

**This repository pins a FastMCP 4 pre-release (beta).** Pre-releases move real API surface, and `gofastmcp.com/python-sdk/*` carries no version or stability marker, so presence there proves nothing about the pin. Introspect installed source before relying on any v4 API. [Version and source routing](references/version-and-source-routing.md) is the only reference naming a release; the rest defer to it.

## Use When

Use this skill for FastMCP servers, components, providers, transforms, Code Mode, clients, and transports. It also covers authentication, authorization, MCP Apps, integrations, testing, configuration, migrations, and deployment. It may be invoked implicitly when FastMCP is clearly the requested framework. Do not invoke it for an ordinary FastAPI task that does not use FastMCP or MCP.

## Direct Workflow

1. Read the complete applicable repository instruction chain for every target.
2. Resolve the Python interpreter, installed FastMCP version, selected extras, dependency declaration, and lockfile. Do not infer capabilities from a floating documentation page.
3. Inspect installed source and signatures, then use the matching release tag and versioned SDK reference for exact APIs.
4. Use live documentation for design guidance. Read upstream main only for explicit upgrade or future-feature work.
5. Prefer FastMCP components, providers, transforms, middleware, auth providers, transports, and test clients before writing protocol or framework plumbing.
6. Keep the implementation inside the narrowest owning boundary. Preserve authentication, authorization, component identity, transport, and lifecycle contracts.
7. Add focused tests for success, failure, capability negotiation, and lifecycle behavior. Run owner-specific validation and inspect the final diff.

Choose the preflight depth by task shape:

- **Routine invocation.** The task only exercises repository-owned FastMCP consumers pinned to the locked version through already-verified APIs (for example, invoking or extending `scripts/soleaux/**`, which pins its client mode and transport). Proceed with the Direct Workflow and cite the lockfile as version evidence; no reference read is required.
- **Escalation.** The task selects or relies on new API surface, upgrades the pin, lacks installed-version evidence, or touches pre-release-boundary behavior. Start with [Version and source routing](references/version-and-source-routing.md) and inspect installed source and signatures before writing code.

## Detail Index

Read only the references needed for the active task:

| Need | Reference |
| --- | --- |
| Server construction and running, transports, tools, resources, prompts | [Server components](references/server-components.md) |
| Providers, composition, namespaces, precedence | [Providers and composition](references/providers-and-composition.md) |
| Transforms, tool reshaping, search, Code Mode | [Providers and transforms](references/providers-and-transforms.md) |
| Protocol eras, `Client(mode=)`, guard tools, `UserSession`/`SessionId`, extensions | [Protocol eras and sessions](references/protocol-eras-and-sessions.md) |
| `Settings`, environment variables, extras, packaging | [Settings and packaging](references/settings-and-packaging.md) |
| Context, session state, elicitation, sampling removal, progress, telemetry | [Interactivity and observability](references/interactivity-and-observability.md) |
| Middleware pipeline, hooks, built-ins | [Middleware](references/middleware.md) |
| Dependency injection, request values | [Dependency injection](references/dependency-injection.md) |
| Lifespan startup, teardown, composition | [Lifespan](references/lifespan.md) |
| Storage backends, caches, session stores | [Storage backends](references/storage-backends.md) |
| Background tasks, the tasks extension, `fastmcp-tasks` | [Background tasks](references/tasks.md) |
| Component versions, selection, migration | [Versioning](references/versioning.md) |
| Clients, transports, operations, callbacks, auth | [Clients and transports](references/clients-and-transports.md) |
| Authentication, deployment, scaling, proxies | [Auth, security, and deployment](references/auth-security-and-deployment.md) |
| Authorization checks, component rules | [Authorization](references/authorization.md) |
| MCP Apps, generative UI, app providers | [Apps and integrations](references/apps-and-integrations.md) |
| OpenAPI, host integrations, `fastmcp install` | [Integration hosts and SDKs](references/integration-hosts-and-sdks.md) |
| CLI, `fastmcp.json`, testing, migrations | [CLI, testing, and migrations](references/cli-testing-and-migrations.md) |

## Verification

- Prove the selected API exists in the installed version.
- Exercise components through FastMCP Client or the owner's integration harness instead of calling only the underlying Python function.
- **Assert no deprecation warning is raised — both classes.** `FastMCPDeprecationWarning` subclasses `DeprecationWarning`; the SDK's `MCPDeprecationWarning` subclasses `UserWarning`, so a filter catching one misses the other. Since `mcp_camelcase_compat` defaults on and warns only once per name, also exercise under `FASTMCP_MCP_CAMELCASE_COMPAT=false`, where a stale read raises `AttributeError` instead of passing silently.
- **State the negotiated protocol era.** `Client(mode=)` defaults to `"auto"`, which against a FastMCP server negotiates a sessionless era with no `initialize` handshake; initialization, middleware, and cross-request state all differ from `mode="legacy"`. A client result is not interpretable without knowing which era produced it.
- Test rejected auth, invalid input, duplicate identity, callback failure, cancellation, or transport loss when relevant.
- Inspect the exposed component catalog and transport behavior after composition or transforms.
- Run the repository's lint, type, and test commands at the narrowest useful scope.
- Report version evidence, references loaded, validation performed, and any unverified host-specific behavior.

## Boundaries

- Do not copy the full upstream documentation corpus into a repository.
- Do not hand-roll MCP framing, JSON-RPC dispatch, OAuth discovery, session handling, or component registries when the installed FastMCP release owns them.
- Do not use upstream main APIs in a release-pinned project without an explicit upgrade.
- Do not treat `fastmcp.experimental.*` as stable; it carries no compatibility guarantee and mixes forward-looking work with back-compat re-exports.
- Do not treat the camelCase compatibility shim as a contract. It warns once and keeps working, so stale field names survive review; write snake_case and verify with the shim disabled.
- Do not assume an optional surface is importable. A surface behind an undeclared extra cannot be introspected, and enabling it is a manifest change, not a code change.
- Do not confuse authentication with application authorization.
- Do not enable side effects, network exposure, browser code, or arbitrary code execution without the owning security contract.
- Keep FastMCP release management, upstream contribution, and issue triage out of scope; this skill covers implementing and executing FastMCP only.
