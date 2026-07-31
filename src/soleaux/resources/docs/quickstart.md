---
title: Quickstart
description: Start Soleaux, verify its fixed catalog, and make a bounded first repository request.
sidebar:
  label: Quickstart
  order: 0
---

## Start the reusable stdio server

Install the workspace environment, then launch the package at the repository you want to inspect:

```sh
uv sync --locked
.venv/bin/soleaux --root /path/to/repository
```

The package entry point uses stdio when no subcommand is present. A host that launches commands directly should register that executable and root as its MCP command. Host approval policy remains host-owned; Soleaux does not write or interpret approval-mode keys.

This repository's authenticated HTTP service is a separate workspace composition, not a package default. It binds to loopback, requires its scoped bearer credential, and keeps FastMCP host/origin protection enabled.

## Verify the connection

Call `describe` or read `soleaux://about`. The component-derived catalog reports ten local tools, seven local resources, zero prompts, and zero resource templates, along with configuration, storage, package, and transport identity.

You can inspect the same source-owned identity without starting MCP:

```sh
.venv/bin/soleaux --root /path/to/repository describe --json
```

## Make the first request

1. Configure the host to call `context` once before prompt processing, or make that one call manually with a concrete objective, optional repository-relative paths, and explicit resource URIs.
2. Read the typed packet's ranked SQLite full-text matches, relation-expanded source, owners, consumers, constraints, conflicts, validation routes, resources, and gaps from the already-published generation. The request does not capture files, parse source, or rebuild the catalog.
3. Begin work when coverage is complete. Use `search`, `query`, `owners`, `navigate`, or `inspect` only to close a named gap or exact semantic question.
4. Preview any edit with `preview`; apply it only after reviewing the exact preview and explicitly confirming `edit`.

Read coverage before treating zero rows as proof. `complete` is the only coverage state under which no rows means none were found.

## Understand local state

Normal analysis uses an in-memory SQLite catalog and creates no state inside the analyzed repository or user cache. Explicit `disk` mode is a content-fingerprint-keyed disposable cache outside the checkout and fails closed when disk state cannot be trusted. Legacy explicit `auto` mode may fall back to memory; it is not the default.

Language servers start lazily for selected semantic requests and close with the Soleaux lifespan. Structural lint is delivered by the `soleaux lint` CLI; its findings are also available as `quality.standards` table rows.

## Optional adoption

The `adopt` extra can detect competing language-server and MCP registrations:

```sh
.venv/bin/soleaux --root /path/to/repository adopt --dry-run
```

Review the complete plan before allowing any configuration write. See the [adopt guide](/guides/adopt-guide) for backup and revert behavior.
