---
title: Use the server instructions
description: Read the exact workflow instructions Soleaux advertises to MCP clients for fixed local tool selection, evidence coverage, and safe editor changes.
sidebar:
  label: Server instructions
  order: 8
---

The FastMCP server advertises the following exact instruction string from `soleaux.server.SERVER_INSTRUCTIONS`:

<!-- soleaux-server-instructions:start -->

```text
Soleaux repository intelligence. Tool names are server-local; hosts may qualify them once with the configured server identity. Start repository research with context and state the task objective; it queries the already-published SQLite generation and returns one typed, bounded packet of source, canonical owners, consumers, constraints, conflicts, validation routes, requested resources, and explicit gaps. It does not build or scan the repository on the context request path. Context, search, query, and owners are pure reads of the currently published generation: they never wait, capture, parse, build, enrich, or publish. When that packet is complete, begin work without another discovery call. Use describe only for capability or schema discovery, search and query for an explicit gap, owners for one exact canonical record, and navigate/inspect for semantics. Edits go through preview followed by edit. restart_lsp restarts selected provider sessions. The soleaux://about resource lists the full catalog. Zero rows means none found only under complete coverage; every result names its evidence. Soleaux is also the MCP gateway: every configured MCP server reaches this host through soleaux, its tools namespaced as <backend>_<tool>. The soleaux://about resource lists backends with their lifecycle, auth mode, and live health. Backend registration and tool policy are owned by soleaux.toml; never edit host MCP configs or propose per-host registrations. If a backend call fails because it is not authenticated, do not retry it: tell the user to run `soleaux mcp login <backend>` in their shell, then retry only after they confirm.
```

<!-- soleaux-server-instructions:end -->

These frozen instructions cover the ten local Soleaux tools. Configured MCP tool names are discovered additively from their providers and are intentionally not interpolated into the package-owned instruction string.
