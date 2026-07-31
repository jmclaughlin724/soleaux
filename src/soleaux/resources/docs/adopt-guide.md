---
title: Adopt existing language servers
description: Detect running language servers and migrate them under soleaux with an interactive consent flow.
sidebar:
  label: Adopt
  order: 11
---

# Adopt existing language servers

When you install soleaux into a workspace that already runs its own language servers (Pylance in VS Code, typescript-language-server, rust-analyzer, gopls, python-lsp-server, and others), you end up with two analysis graphs and two memory footprints for the same workspace. The `soleaux adopt` workflow detects that duplication and offers to consolidate it under soleaux with your explicit consent.

## Why adoption matters

Without adoption, an editor-launched language server and soleaux's own broker both index the same workspace. That was the original failure mode that motivated this command: a Pylance process and a nested soleaux process each discovered roughly 130 source files, doubling analysis memory to roughly 700 MiB RSS and contending for the same workspace files. `soleaux adopt` collapses the duplication.

## Install

`adopt` depends on three optional libraries that keep the base install lean:

- `psutil` enumerates running language-server processes by command line.
- `tomlkit` round-trips `.codex/config.toml` without losing comments.
- `json5` parses VS Code's `.vscode/settings.json`, which permits comments and trailing commas.

```sh
pip install "soleaux[adopt]"
```

`soleaux adopt` errors with a clear install hint if the extra is missing.

## Detect

The dry run reports three classes of detections without writing anything:

```sh
soleaux --root /path/to/repository adopt --dry-run
```

1. **Running host processes** — every LSP process whose current working directory is inside or below the workspace root. Pylance, pyright-langserver, typescript-language-server, rust-analyzer, gopls, python-lsp-server, bash-language-server, deno, astro-ls, and others are recognised by command line. Processes you do not own raise `psutil.AccessDenied` and are surfaced as "detection incomplete" warnings.
2. **Editor configuration** — `.vscode/settings.json` keys that select a language server (`python.languageServer`, `python.analysis.indexing`, `typescript.tsdk`, and others) and `pyrightconfig.json` if present.
3. **Competing MCP launch registrations** — entries in `.mcp.json`, `.codex/config.toml`, and `opencode.json` that launch a competing repository-intelligence MCP or a sibling language-server command.

The report names each detection, the file or process it came from, and the planned write.

## Apply

Run without `--dry-run` to apply the plan interactively:

```sh
soleaux --root /path/to/repository adopt
```

Soleaux prints the numbered plan, prompts for `y/n/exclude` per item, and writes only confirmed actions. For non-interactive environments (CI, containers), pass `--yes`:

```sh
soleaux --root /path/to/repository adopt --yes
```

Scope the writes with `--target`:

```sh
soleaux --root /path/to/repository adopt --target editor,mcp,providers
```

- `editor` writes `.vscode/settings.json` and `pyrightconfig.json` to disable the existing language server (`python.languageServer: "None"`, `python.analysis.indexing: false`, and so on).
- `mcp` adds a portable `soleaux` registration to `.mcp.json`, `.codex/config.toml`, and `opencode.json` using `uvx soleaux` rather than any repo-coupled launch form.
- `providers` emits a commented `[providers.<language>]` block to `soleaux.toml` so you can review and enable it without surprise.

Restrict the detection to a subset of languages with `--language`:

```sh
soleaux --root /path/to/repository adopt --language python,typescript
```

## Backups and revert

Every file soleaux modifies is copied to `.soleaux-backups/<relative-path>.<iso-timestamp>` before any write, and a manifest at `.soleaux-backups/manifest.json` records each backup. Restore the previous state with:

```sh
soleaux --root /path/to/repository adopt --revert
```

`--revert` reads the manifest, restores each file in reverse order, and leaves the manifest in place for audit. Drop the `.soleaux-backups/` directory once you no longer need it.

## What soleaux never writes

- **User-level editor config** (`~/.config/Code/...`, `~/.codex/...`, `~/.claude/...`). Adoption only writes files inside the workspace root.
- **Editor extensions.** Soleaux does not install or uninstall VS Code extensions; it only disables the language-server selection in workspace settings.
- **Anything outside the three named targets.** Source files, `.git/`, lock files, and other configuration are untouched.

## Idempotency

`soleaux adopt` is safe to re-run. A healthy existing soleaux registration is left in place unless you pass `--force`. Disabled editor keys stay disabled. Backups accumulate by timestamp so you can roll back to any prior state.

## What adoption does not replace

Soleaux's broker speaks a subset of LSP useful for repository intelligence: definition, references, implementation, hover, call hierarchy, diagnostics, completion, signature help, code actions. Richer IDE features (inlay hints, debug adapter integration, semantic tokens for colourisation) continue to come from the editor's own experience. If you depend on those features, keep the editor's language server and let soleaux coexist by setting `python.languageServer: "pylance"` (or equivalent) back after adoption; the overlap cost is then a deliberate trade rather than accidental duplication.
