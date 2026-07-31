---
name: react-email
description: Author and verify React Email templates when the dependency is installed.
---

# React Email

## Contract

Use this skill for React Email templates, preview and export behavior, and template-level rendering constraints. First verify that the active workspace owns a React Email dependency and template surface. If it does not, do not invent an owner or prescribe a path without an explicit implementation request.

## Use When

- The user explicitly asks for React Email work.
- The scoped workspace already contains React Email templates or dependencies.
- A template render, preview, export, compatibility, or props contract is failing.

## Direct Workflow

1. Identify the actual template owner, installed React Email version, preview or export command, send-path consumer, and nearest template example.
2. If no owner or dependency exists, stop and report the missing prerequisite unless the user explicitly asked to introduce the capability.
3. Read only the applicable reference: `references/COMPONENTS.md`, `PATTERNS.md`, `STYLING.md`, `I18N.md`, or `SENDING.md`.
4. Keep props typed and provide realistic preview data. Default non-critical preview-only values when the renderer may invoke the template without them.
5. Keep template markup and styles compatible with the supported email clients and preserve the repository-owned send path.
6. Run the owning preview or export command and a focused render test.

## Detail Index

- Component APIs: [COMPONENTS.md](references/COMPONENTS.md).
- Composition patterns: [PATTERNS.md](references/PATTERNS.md).
- Styling and Tailwind: [STYLING.md](references/STYLING.md).
- Localization: [I18N.md](references/I18N.md).
- Rendering and provider handoff: [SENDING.md](references/SENDING.md).

## Boundaries

- Do not create production email ownership outside the live canonical package.
- Do not add or migrate providers as part of a template-only task.
- Do not assume package names, import paths, CLI commands, or version-specific APIs without checking the installed workspace.
- Keep provider credentials and external sends outside template validation.
