---
name: safe-action
description: Build typed next-safe-action actions, callers, and form workflows.
---

# next-safe-action

## Contract

Use this skill for typed next-safe-action clients, middleware, server actions, React callers, forms, optimistic flows, errors, and tests. First confirm that the scoped workspace owns the dependency and identify its actual client and action owners. Do not invent a shared package or copy paths from reference examples into a checkout where they do not exist.

## Use When

- Creating or debugging a next-safe-action client, middleware, action, caller, form integration, or test.
- The user explicitly names next-safe-action or one of its React hooks.

## Direct Workflow

1. Confirm the installed next-safe-action version, framework version, client owner, action owner, consumer, validation schema, and nearest test.
2. If the dependency or owner does not exist, stop unless the user explicitly requested introducing the capability.
3. Read [skill-playbook.md](references/skill-playbook.md) only for the relevant server, React, form, or testing section, and verify version-specific APIs against the installed package.
4. Keep authentication and authorization in the owning middleware or server boundary, input and output validation explicit, and error shapes stable for callers.
5. Preserve form submission, pending state, optimistic rollback, and framework error behavior required by the consumer.
6. Add focused tests for validation, authorization, success, expected errors, and caller-visible state.
7. Run the owning workspace's tests and typecheck.

## Detail Index

- `references/skill-playbook.md`: server, React, and testing routes.
- `references/server-*.md`: client, middleware, validation, errors, metadata, and bind arguments.
- `references/react-*.md`: hooks, forms, optimistic updates, and uploads.
- `references/testing-*.md`: validation and organization.

## Boundaries

- Do not create a shared next-safe-action workspace without an explicit owner decision.
- Do not expose raw framework or validation errors when callers depend on a stable error contract.
- Do not treat examples in references as proof of current repository paths.
- Keep database, auth, and domain behavior in their owning packages or apps.
