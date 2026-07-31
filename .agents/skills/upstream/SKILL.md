---
name: upstream
description: Audit a scoped local target against version-matched authoritative upstream sources when verifying implementation choices, failures, or dependency behavior.
---

# Upstream

## Contract

Audit one explicit local target against version-matched authoritative upstream sources. This skill is read-only: upstream evidence informs a decision but never authorizes repository edits. Repository source, owner instructions, installed versions, tests, and user constraints establish the local boundary. Cite verified upstream claims and distinguish them from local design choices and inference.

If the caller requested implementation, return decision-ready recommendations to the owning workflow and stop. Do not switch into repair mode.

## Use When

- Verify one local implementation choice, failure, or dependency behavior against official upstream guidance.
- Require the request or active handoff to name one path, glob, owner, or topic. Ask for that single target when none is discoverable instead of auditing the repository broadly.

## Source Routing

- OpenAI and Codex: invoke `openai-docs` and use current official OpenAI documentation.
- Next.js: read the installed version-matched documentation under `node_modules/next/dist/docs/` before evaluating behavior.
- Supabase: use the registered Supabase documentation search when available, then official Supabase documentation for the relevant product and version.
- PostgreSQL: use official PostgreSQL documentation matching the server version. Do not treat Supabase platform guidance as a substitute for PostgreSQL behavior.
- React, Turborepo, shadcn/ui, Zod, TypeScript, Node.js, Biome, and other libraries: use the owning skill or documentation connector when available, then the installed version and official technology documentation.
- Use a secondary source only when the canonical source is unavailable, and label the limitation.

## Direct Workflow

1. Freeze the target, audit question, local version, hard constraints, desired decision, output, and stop condition.
2. Read the complete applicable owner instruction chain and the in-scope implementation needed to understand local behavior, consumers, contracts, and validation.
3. Resolve the precise upstream version and fetch the canonical guide or API reference before making a best-practice claim.
4. Trace imports, exports, consumers, tests, and generated surfaces only as far as the audit question requires, following the root routing policy for discovery.
5. Compare local behavior with the retrieved guidance. Classify each material difference as an upstream conflict, allowed local constraint, upstream-unopinionated design choice, or unresolved evidence.
6. State the local impact and recommendations, run only safe read-only checks, and stop without editing.

## Output

Return the target and local version; canonical sources with citations; findings ordered by severity and their classification; local impact and affected consumers; recommendations; files inspected and read-only checks run; and unresolved uncertainty or remaining risk.

## Boundaries

- Never edit repository files, generated targets, configuration, or documentation.
- Never present repository prose or model memory as upstream evidence.
- Never infer a standard when the canonical source is silent or unavailable.
- Preserve explicit user values and real provider, deployment, safety, and public-contract constraints.

## Stop Conditions

Stop when the comparison is decision-complete. Stop as blocked when the exact target, applicable version, canonical source, or required safe evidence cannot be established after bounded fallbacks. Never continue into implementation.
