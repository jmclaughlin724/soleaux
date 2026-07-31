# Codex hook surface

## Ownership and sources

| Surface | Direct owner |
| --- | --- |
| Active lifecycle registration | `.codex/hooks.json` |
| Registration shape and required-owner enforcement | `.codex/hooks/__tests__/registration.test.mjs` |
| Pre-prompt Soleaux context and Codex-native output | `.codex/hooks/UserPromptSubmit/soleaux_context.py` |
| Bash event decision and native output | `.codex/hooks/PreToolUse/bash-policy.mjs` |
| Secret argv policy module | `.codex/hooks/PreToolUse/secret-argv-guard.mjs` |
| Secret environment-file read policy module | `.codex/hooks/PreToolUse/secret-env-file-read-guard.mjs` |
| Raw SQL DDL policy module | `.codex/hooks/PreToolUse/raw-sql-ddl-guard.mjs` |
| Dangerous Git and shell policy module | `.codex/hooks/PreToolUse/dangerous-git-shell-writes-guard.mjs` |
| Source-mutation boundary policy module | `.codex/hooks/PreToolUse/source-mutation-boundary-guard.mjs` |
| Git delivery-binding policy module | `.codex/hooks/PreToolUse/git-delivery-binding-guard.mjs` |
| Manual workspace diagnostics | `.codex/hooks/PostToolUse/workspace-diagnostics.mjs --workspace`; not lifecycle-registered |
| Inert event placeholders | `.codex/hooks/*/.gitkeep` |
| Upstream registration, matcher, input, output, and trust behavior | `.agents/skills/optimizer/codex/hooks/hooks.md` and its cited current Codex documentation |

## Runtime contract

- A registered command must invoke its direct event owner. A local handler may import narrow parsing, path, structural-analysis, or policy modules, but the registered owner validates native input and shapes the single native output. A policy module consumes validated structured context, returns only its own reason or neutral result, and never reads stdin or emits a platform decision. Do not add a compatibility layer, handler-order dependency, or file whose only purpose is forwarding stdin to another repository executable.
- A package-owned CLI may be registered directly only when the user explicitly chooses that lifecycle integration. Repository owners outside `.codex/hooks/<Event>/` must be directly executable and explicitly covered by the registration suite.
- Codex runs every matching same-event command concurrently, and its matcher scopes only the tool name. Register exactly one `PreToolUse` command for `^Bash$`; it parses the command once and evaluates the Bash policy modules sequentially. Keep separate same-event registrations only when their matcher, owner, and failure domain are genuinely independent.
- This is a Mac-only repository. Register only command-handler fields supported here: `type`, `command`, positive `timeout`, and `statusMessage`. Do not add `commandWindows`, `async`, `prompt`, or `agent` handlers.
- Resolve repository-local executables from `git rev-parse --show-toplevel`; a task may start in a repository subdirectory.
- Read one native JSON payload from standard input, validate `hook_event_name`, `tool_name`, `tool_input`, `cwd`, and every consumed field, and preserve documented nullability. Do not parse transcripts.
- `soleaux_context.py` is the sole `UserPromptSubmit` owner. It validates the native event and consumed prompt fields, resolves the repository root, parses `.codex/config.toml` with `tomllib`, and calls the configured Soleaux `context` tool exactly once through the installed FastMCP client over the private Unix-domain socket. It must never log credentials or raw transport errors. A successful call emits one `hookSpecificOutput.additionalContext` packet; malformed input, invalid configuration, socket or transport failure, or an invalid text result exits `2` with bounded corrective stderr.
- Select one enforcement boundary from the requested effect. Use execpolicy for static command gates, `PreToolUse` to deny a supported tool call before execution regardless of approval flow, `PermissionRequest` when the requested behavior is to prevent an action from being allowed at approval time, and `PostToolUse` only for completed-tool handling. If a request names `PostToolUse` but asks for prevention, route it to the applicable preventive boundary instead of implementing the contradictory event. Do not duplicate one policy across those owners.
- `PermissionRequest` matchers filter tool names and aliases: use `^Bash$` for shell or unified exec, `^(apply_patch|Edit|Write)$` for file edits, or an anchored canonical MCP name. A denial must return `hookSpecificOutput.hookEventName = "PermissionRequest"`, `decision.behavior = "deny"`, and a nonempty `decision.message` that tells the agent which approved action to take instead. It runs only when Codex is about to request approval.
- `bash-policy.mjs` is the sole registered `PreToolUse`/`Bash` owner. It validates the payload once, parses Bash once, runs the six policy modules in a fixed order, and lazily permits repository, filesystem, SQL, or Git inspection only inside a module whose parsed candidate shape requires it. A valid non-match remains silent; one or more proven policy matches produce one native deny decision.
- Operational failures must never be hidden, converted into policy decisions, or returned under exit `0`. Malformed protocol input, parser failure, timeout, missing dependency, invalid parser output, or unavailable Git/filesystem state exits `2` with empty stdout and bounded stderr containing the failed `source`, stable `code`, safe `cause`, and literal `Corrective action:`. Independent applicable Bash modules continue so one failure does not hide another finding, then the owner fails execution and includes any additional proven policy findings. Neither raw commands nor secret values appear in diagnostics.
- Parse Bash structure through the installed ast-grep CLI and candidate PostgreSQL statements through `@libpg-query/parser`. Do not replace either parser with handwritten shell grammar or SQL keyword classification.
- `secret-argv-guard.mjs` owns only literal secrets in argv, inline environment assignments, and database URLs. Its denial output identifies the category or option name without including the matched value.
- `secret-env-file-read-guard.mjs` owns only read/search commands targeting secret-bearing `.env*` or `.envrc` files; approved example, default, sample, and template files remain readable.
- `raw-sql-ddl-guard.mjs` owns only parser-proven DDL passed through supported Bash database CLI forms. Non-DDL SQL remains outside this decision.
- `dangerous-git-shell-writes-guard.mjs` owns intrinsically destructive shell and local Git shapes, source-control bypasses, and hosted PR merge shape. It does not decide source mutation or Git delivery. It must inspect compound and nested commands without depending on another hook.
- `source-mutation-boundary-guard.mjs` owns unstructured Bash writes and Git content application to governed repository source. Known repository formatters and generators remain allowed; redirection, in-place rewriting, patching, tracked removal or rename, copying, moving, interpreter writes, and explicit output paths route the caller to `apply_patch`, `Edit`, or `Write`. The sole tracked-removal exception is the upstream untrack-and-keep operation (gitignore(5) NOTES): `git rm --cached` with exact repository-contained paths and no force or recursive flag stays allowed; it removes only index entries and never touches the worktree.
- `git-delivery-binding-guard.mjs` owns every push, exact origin fetch-refspec and upstream repairs, and pull-request creation state. It rejects force-push forms and diagnostic push pipelines, reads Git state without changing `.git/config`, requires the wildcard origin fetch mapping and same-branch upstream, enforces explicit first and later push forms, and requires the remote-tracking ref to equal `HEAD` before `gh pr create`. Its denial provides exact repair commands.
- Do not register Supaschema lifecycle hooks. Supaschema remains an explicit CLI workflow invoked by the agent or user when schema work requires it.
- A neutral success exits `0` without standard output. Use the upstream-documented event-specific decision shape only for a proven denial or completed validation finding. Do not assume `PostToolUse` can undo a completed side effect.
- `workspace-diagnostics.mjs` remains a manual VS Code diagnostics transport, not a lifecycle hook. It publishes bounded `::workspace-diagnostic::` lines and writes no diagnostic file.
- Only the `UserPromptSubmit` context owner calls an MCP tool. It consumes the Soleaux connection owned by `.codex/config.toml`; it does not duplicate connection settings or change host approval configuration. Other hooks do not call, configure, describe, or match MCP tools unless a later implemented event explicitly owns that behavior.
- `Stop` and `SubagentStop` handlers must honor `stop_hook_active` so a continuation decision cannot loop indefinitely.

## Verification

- `.codex/hooks/__tests__/registration.test.mjs` enforces exact matcher scopes, one command per registration, direct executable ownership, exactly one Bash event owner, the one pre-prompt context owner, unregistered Bash policy modules, and absence of forwarding shims.
- Adding a lifecycle owner requires the direct handler or true executable, one `.codex/hooks.json` registration, an ownership row above, an exact registration-test entry, focused neutral/decision/malformed-input tests, and the corresponding Claude owner when the contract is cross-platform.
- Focused handler suites cover malformed and wrong-event payloads, compound and nested Bash, redaction, parsed SQL DDL, repository containment, Git publication state, and native runtime output. Malformed input and operational dependency failures must exit `2` with empty stdout and corrective stderr containing `source`, `code`, `cause`, and `Corrective action:`.
- Exercise changed handlers from the repository root and a nested working directory.
- The focused `UserPromptSubmit` suite proves one context call, the event-native `additionalContext` shape, repository-root and nested-cwd resolution, bounded objectives, malformed input handling, and credential redaction. Claude uses its native `mcp_tool` hook against the already-connected `soleaux` server and relies on the context tool's human-readable non-JSON content.
- Run targeted Vitest suites and `pnpm hooks:test`; add `pnpm execpolicy:check`, `pnpm skills:audit`, or `pnpm check:structural-policy` only when the corresponding rule, skill, or structural surface changed.
- After changing a hook definition, start a fresh trusted Codex task, inspect `/hooks`, review the changed definition hashes, and exercise a real event.
