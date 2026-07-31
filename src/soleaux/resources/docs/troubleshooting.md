---
title: Troubleshoot Soleaux
description: Diagnose Soleaux runtime setup, unavailable semantic providers, incomplete coverage, preview conflicts, and unexpected repository state.
sidebar:
  label: Troubleshooting
  order: 7
---

## Confirm the runtime

Run:

```sh
soleaux --version
soleaux --root /path/to/repository doctor --json
```

`doctor` reports the selected root, config source, Python and package versions, provider availability, and whether a bounded probe ran. Its default mode opens no source files and starts no analyzer.

## Validate MCP configuration

Cross-check that `.mcp.json` and `.codex/config.toml` are consistent:

```sh
soleaux --root /path/to/repository check mcp --json
```

Reports servers present in one file but not the other, and disabled servers that should be removed.

## Check workspace health

Scan workspace `.tmp/` entries against `[health]` thresholds:

```sh
soleaux --root /path/to/repository check health --json
```

The `soleaux://health/v1` resource returns the same thresholds for agent-facing consumption.

## Handle unsupported semantics

An `unsupported` semantic result usually means the matching provider is not installed where the package-owned registry expects it. Review [provider configuration](/guides/provider-configuration), then rerun `doctor --json`. Use `syntax_only` when structural evidence is sufficient, or install the selected provider before using `semantic_required`.

## Handle incomplete or empty results

Inspect `coverage.status`, `omitted_reasons`, counters, and enforced limits. Empty rows are conclusive only under `complete` coverage. Narrow the workspace, query fewer tables, reduce the search scope, or follow a suggested request without changing the original table prohibitions.

The server lifespan publishes a bounded base SQLite generation before admitting wire clients. `catalog_not_ready` therefore indicates an in-process caller used the service before `start()` completed, or base publication failed. The request intentionally does not capture files, build a frame, enrich, or publish a replacement generation.

## Handle host context limits

`host_context_limit` means the required owners, consumers, conflicts, validation routes, and coverage gaps could not fit inside the host's context envelope without losing required semantics. Do not treat the missing packet as complete and do not replace it with silently truncated output. Make one direct `context` request with a narrower objective and repository-relative `paths`; retain the gap until a bounded packet succeeds. If the narrowed request reports the same limit, stop discovery and report the runtime-repair requirement instead of retrying.

## Handle preview conflicts

Do not reuse an expired or consumed preview. Request a new preview when the process epoch, provider generation, file hash, preview digest, or selected workspace changed.

## Check for unexpected repository state

Normal analysis uses an in-memory SQLite catalog and creates no `.soleaux` directory, database, index, or cache in the target repository or user cache. Explicit `disk` mode may create a content-fingerprint-keyed database under the platform user-cache directory, never inside the checkout. If repository-local state appears, treat it as external state and identify its owner before removal.
