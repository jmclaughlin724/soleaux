# Product Definition

## Mission

Soleaux gives existing AI coding agents a shared, trustworthy understanding of a repository through one bounded MCP server and one governed catalog.

## Primary user problem

Serious repositories accumulate:

- overlapping MCP servers;
- client-specific rules and skills;
- repeated whole-file reads;
- inconsistent ownership and validation knowledge;
- framework-specific discovery tools;
- fragmented memory and handoffs.

The model receives more tools but not necessarily better ground truth.

## Product answer

```text
Repository
    ↓
Native index + parser/LSP/framework intelligence
    ↓
context.compile
    ↓
Bounded Context Packet V2
    ↓
Claude / Codex / OpenCode / Cursor / other MCP clients
```

Alongside the context path, a central registry owns skills, agents, rules, ownership, governance, and namespaced MCP backends.

## Primary wedge

The primary demonstration is:

1. attach only Soleaux;
2. list 12 root tools;
3. compile task context;
4. show provenance, ownership, constraints, validation routes, and gaps;
5. complete the same task with equal-or-better correctness and less waste context than the baseline.

## Target users

Initial target:

- teams on large repositories;
- TypeScript/Turborepo/Next.js-heavy environments;
- users of Claude and/or Codex;
- teams with multiple rules, skills, agents, and MCP backends;
- teams that need auditable repository ground truth.

## North-star metric

> Percentage of waste context replaced by Soleaux compilation at equal or better task success.

The deterministic proxy is a regression signal. The live same-model comparison is the product gate.

## Non-goals

- replacing agent clients;
- becoming a general model runtime;
- exposing the entire control plane to the model;
- native session-database mutation;
- running parsers on mobile;
- universal hosted-memory synchronization.
