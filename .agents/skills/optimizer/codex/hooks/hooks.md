# Hooks Playbook

Sources verified 2026-07-27:

- https://learn.chatgpt.com/docs/hooks
- https://learn.chatgpt.com/docs/config-file/config-advanced#hooks
- https://learn.chatgpt.com/docs/config-file/config-reference

## Intent

Use native Codex hooks only when a Codex runtime event needs deterministic intervention that instructions or rules cannot provide. Hooks are for event-time behavior: tool gating, approval decisions, extra context, result blocking, and stop continuation.

## Decide Whether a Hook Is Warranted

Use a hook when:

- The behavior must run at a specific event such as `SessionStart`, `SessionEnd`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, or `Stop`.
- The behavior needs tool payload data, approval payload data, or transcript context.
- A shell rule cannot express the policy.

Do not use a hook when:

- A short instruction would solve the issue.
- A fail-only Codex rule can statically forbid the command and provide the permitted corrective action.
- The goal is duplicating a policy that already has a registered owner on the other platform; update both hand-authored registrations instead.

## Choose the Enforcement Boundary

Choose from the requested effect before writing a handler:

| Requested effect | Owner | Matcher contract |
| --- | --- | --- |
| Statically reject a shell-command prefix that always violates a repository invariant | `.codex/rules/*.rules` | A fail-only `forbidden` execpolicy rule with corrective guidance, not a lifecycle matcher |
| Deny or rewrite a supported tool call before it runs, including calls that need no approval | `PreToolUse` | Canonical tool name or supported alias |
| Prevent an action from being allowed at an approval boundary and return corrective guidance | `PermissionRequest` | Canonical tool name or supported alias; the event fires only when Codex is about to request approval |
| Inspect or alter result handling after a supported tool has run | `PostToolUse` | Canonical tool name or supported alias; side effects have already happened |

If a request names `PostToolUse` but asks to “prevent allowing” a command or action, do not implement the contradictory event literally. Use `PermissionRequest` when the policy is about what may be approved, and deny with a corrective `decision.message`. Use `PreToolUse` instead only when the policy must also cover supported calls that do not request approval. Keep one decision owner; do not register the same prohibition at both events.

For `PreToolUse`, `PermissionRequest`, and `PostToolUse`, match the tool that performs the targeted action:

- Shell or unified exec: `^Bash$`, never `exec_command`.
- File edits: `^(apply_patch|Edit|Write)$`; hook input still reports canonical `tool_name: "apply_patch"`.
- One MCP tool: its anchored canonical name, such as `^mcp__filesystem__read_file$`.
- A deliberately scoped MCP family: an anchored namespace expression such as `^mcp__filesystem__.*$`.

## Authoring Steps

1. Pick the event that owns the requested effect. Use `PermissionRequest` for approval-boundary prevention and `PreToolUse` for unconditional supported-tool prevention; never use `PostToolUse` to claim a side effect was prevented. For `apply_patch` remediation flows, virtually apply the patch or otherwise inspect the complete resulting file before execution; pre-deny source-policy and contract-ownership findings when the result would introduce or preserve violations. Do not duplicate that source-policy gate in `PostToolUse`; keep completed-edit maintenance there and full diagnostics at repository boundaries.
2. Use native `matcher` strings to scope each registration to the tools its owner handles. Upstream accepts a shell command string; this repo requires each command to invoke its direct event handler or true executable owner. When one matcher covers several policies, keep them as narrow modules under that event owner instead of registering overlapping commands. Do not add a pure forwarding adapter (see `.codex/hooks/AGENTS.md`). `UserPromptSubmit` and `Stop` ignore matchers. This is a Mac-only repository; `commandWindows` overrides are out of scope and must not be added.
3. Use `type = "command"` for executable hooks. Other hook handler types may parse but are skipped today.
4. Set timeouts deliberately. The default command timeout is 600 seconds.
5. Make output contracts explicit: deny, allow, add context, rewrite supported input, block result handling, or continue.
6. Keep repo-local command paths rooted at the git root. Do not read `CODEX_PROJECT_DIR` — it is **not documented** in the upstream Codex Hooks reference and is not guaranteed to be set. Derive the project root from `git rev-parse --show-toplevel` (at registration time) or from the hook entrypoint's own path (`import.meta.dirname` ancestor walk) at runtime.
7. Keep only one hook representation per config layer: prefer `.codex/hooks.json` for project hooks, not mixed inline `[hooks]` tables plus `hooks.json`.
8. Multiple matching command hooks launch concurrently with no ordering guarantee. Separate policy owners must not depend on execution order. If one policy genuinely requires ordered internal stages, keep those stages inside that one direct owner.

## Runtime Ownership

The registered event handler or true executable owns the runtime decision. It validates the native payload and emits the Codex contract. It may import narrow parsing, path, formatting, staging, structural-analysis, or policy modules through exact module paths. When the matcher cannot distinguish several applicable policies, the event owner may parse once and evaluate those modules deterministically; modules consume validated context and return only their own result. They must not read hook stdin, emit Codex decisions, or become a second runtime owner, and no repository file may exist only to forward stdin to another executable. Do not add a `shared`, `common`, `lib`, `utils`, or barrel namespace.

## Runtime And Trust

- Hooks are enabled by default through the canonical `features.hooks` key. Do not use the deprecated `features.codex_hooks` alias.
- Codex loads matching hooks from every active hook source. Higher-precedence config layers do not replace lower-precedence hooks.
- Multiple matching command hooks for the same event launch concurrently. Do not rely on hook ordering, or on one hook preventing another matching hook from starting.
- Non-managed command hooks must be reviewed and trusted with `/hooks` before they run. New or changed hooks are skipped until trusted. Use `--dangerously-bypass-hook-trust` only for automation that vets hook sources outside Codex.
- Project-local hooks load only when the project `.codex/` layer is trusted.
- `PreToolUse` and `PostToolUse` intercept supported Bash calls, file edits through `apply_patch`, MCP calls, and most other local function tools. They do not intercept hosted tools such as `WebSearch`, and specialized paths can opt out; treat hooks as guardrails, not complete enforcement boundaries.
- Codex reports its unified `exec_command` surface to hook matchers as `Bash`. Match `Bash`, not `exec_command`, only when a handler intentionally owns shell calls; file-edit handlers that require an exact edited-file inventory must match neither.
- Before hard-blocking on cross-event state, enumerate every valid producer path. A missing `PostToolUse` record is unknown when Codex can satisfy the requirement through an unobserved tool path; keep absence-based checks advisory while retaining hard gates for supported calls that reach `PreToolUse`.
- Use `turn_id` to scope observed events to a real turn, not to infer that the surrounding hook stream is complete.

## Event Guidance

- `SessionStart`: add startup context for `startup`, `resume`, `clear`, or `compact` sources.
- `SessionEnd`: run advisory cleanup or note capture for the main thread. Its matcher filters the currently fixed reason `other`, output cannot steer Codex, and its timeout may not exceed three seconds.
- `PreToolUse`: deny, add context, or rewrite supported tool inputs before execution.
- `PermissionRequest`: approve, deny with a corrective message, or decline to decide before an approval prompt reaches the user or reviewer. It does not fire for calls that need no approval.
- `PostToolUse`: add context, replace result handling, or block result handling after a supported tool produces output. It cannot undo side effects.
- `PreCompact` and `PostCompact`: run around `manual` or `auto` compaction.
- `UserPromptSubmit`: inspect the prompt before processing; matcher is ignored.
- `SubagentStart`: add context for a subagent. It cannot block subagent creation.
- `Stop` and `SubagentStop`: return control to Codex when unresolved work remains. They expect JSON on stdout for exit `0`; plain text stdout is invalid.
- In this repo, `PreToolUse` owns structural error prevention for structured edits and command mutation policy for Bash. `PostToolUse` owns completed-edit maintenance: one handler formats and stages the exact current-invocation paths sequentially, while separate handlers audit only their owned edited surfaces (see `.codex/hooks.json`). Full diagnostics remain manual and at repository boundaries.

For file mutation tools, be precise about enforcement timing. A `PreToolUse` deny prevents the tool call. A `PostToolUse` block interrupts Codex result handling after a successful tool call, but any file edit already happened. Policies that must prevent new source violations require PreToolUse inspection of the resulting content; PostToolUse alone is not prevention.

## Output Contracts

- `PreToolUse` deny: return `hookSpecificOutput.hookEventName = "PreToolUse"`, `permissionDecision = "deny"`, and `permissionDecisionReason`. The older top-level `decision = "block"` shape and exit code `2` with stderr also block. `permissionDecisionReason` is fed to the model; add a top-level sibling `systemMessage` field when the block needs a user-visible recovery instruction the user must read and act on (e.g., a literal phrase to type back).
- `PreToolUse` rewrite: return `permissionDecision = "allow"` with `updatedInput`. Bash and `apply_patch` rewrites require `updatedInput.command` as a string.
- `PermissionRequest` deny:

  ```json
  {
    "hookSpecificOutput": {
      "hookEventName": "PermissionRequest",
      "decision": {
        "behavior": "deny",
        "message": "This action is prohibited. Use the approved read-only command instead."
      }
    }
  }
  ```

  Make `decision.message` nonempty, bounded, and actionable: state the policy reason and tell the agent what approved action to take instead without echoing secrets or untrusted command text. Any deny wins. An `allow` decision bypasses the approval prompt; silence declines to decide and preserves the normal approval flow. Do not return `updatedInput`, `updatedPermissions`, or `interrupt`; Codex rejects those unsupported fields instead of applying the requested permission decision.

- `PostToolUse`: `decision = "block"` replaces Codex result handling with hook feedback; it does not undo the completed tool. `continue: false` also replaces normal result processing after the tool has already run.
- `Stop` and `SubagentStop`: `decision = "block"` means continue the turn or subagent, not reject the result. `continue: false` takes precedence over continuation decisions.
- Exit `0` with no output is success. When `Stop` or `SubagentStop` emits output, it must be JSON; use `systemMessage` for a nonblocking warning and never put successful maintenance output in a block reason that becomes a continuation prompt.
- Operational failures are not proven policy matches and must never be serialized as successful native decisions. Exit `2` with empty stdout and bounded stderr identifying the failed owner, stable code, safe cause, and literal `Corrective action:`. Only a successfully evaluated policy match or completed validation finding may use the event-native deny or block shape under exit `0`. Never echo raw commands, parser input, or secrets.
- Plain stdout is ignored by `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, and `PostCompact`; it is context for `SessionStart`, `SubagentStart`, and `UserPromptSubmit`.

## Repo Delivery Pattern

- `.codex/hooks.json` is the hand-authored registration owner. The directly registered handler or true executable is the runtime enforcement owner. `.codex/hooks/AGENTS.md` owns the detailed contract, and `.codex/hooks/__tests__/registration.test.mjs` enforces exact matchers, one command per registration, and direct ownership.
- The parallel Claude hook surface is registered in `.claude/settings.json`. When a policy must hold on both platforms, update both hand-authored registrations in the same change.
- Although upstream permits an arbitrary shell command string, each command in this repo invokes exactly one direct owner. Local repository paths resolve from `git rev-parse --show-toplevel` because Codex may run from a repository subdirectory. A package-owned CLI may be registered directly when it is the true enforcement owner.
- Register `PostToolUse/auto-fix.mjs` only for the `apply_patch`, `Edit`, and `Write` matcher aliases; the received payload remains canonical `apply_patch`. Extract the exact invocation paths, reject repository escapes and symlinks, skip deleted paths during formatting, run targeted Ultracite, Tombi, and Prettier commands in that order, and stage the invocation paths only after every formatter succeeds. Staging classifies each path by Git index and ignore state: indexed paths stage through an update-only operation, new non-ignored paths stage as additions, and ignored untracked paths are intentional local-only no-ops; recovery guidance never stages or force-adds an intentionally ignored path. Do not match `Bash`: unified `exec_command` calls arrive under that name but do not provide a trustworthy edited-file inventory.
- Register skill maintenance only on `PostToolUse` for the `apply_patch`, `Edit`, and `Write` matcher aliases; hook input remains canonical `apply_patch`. Derive the edited skill directories from `tool_input.command`, audit only those skills, stay silent outside `.agents/skills/**`, and return bounded block feedback only when the audit fails. Never write or inject a replacement catalog. The repository's apply-patch maintenance subset is documented by the [input schema](../../references/post-tool-use.apply-patch.input.schema.json) and [output schema](../../references/post-tool-use.apply-patch.output.schema.json).
- For an approval-boundary prohibition, register `PermissionRequest` with the exact targeted matcher, validate the [input schema](../../references/permission-request.command.input.schema.json), and validate every emitted decision against the [output schema](../../references/permission-request.command.output.schema.json). A valid non-match is silent; a match denies with the local required corrective message.
- Native Codex event handlers own their final decision and output. Classification of shell syntax, code syntax, imports, file edits, SQL, or mutation intent must use ASTs, structured parsers, or narrow structured primitives. An event owner may evaluate narrow policy modules against that structured context; do not hand-roll source parsers or delegate native decision output to another runtime file.
- For `apply_patch` and file-edit checks, parse the patch or resulting file into a structured representation before deciding ownership, imports, deletes, or mutation intent.
- Separate upstream-verified guidance from repo policy in hook messages. Do not label repo hardening rules or local workflow choices as upstream best practice unless the upstream source directly says so.
- Catch top-level operational errors, write bounded corrective diagnostics to stderr, and exit `2`; never return them as exit-`0` JSON. Include the owner, stable error code, safe cause, and literal `Corrective action:` without echoing untrusted commands, secret values, or raw parser input. Reserve the documented event-native blocking shape for a proven policy match or completed validation finding.

## New Owner Checklist

- Name the single requested effect and select its event boundary before choosing a directory.
- Identify the exact canonical tool name and matcher aliases; anchor the narrowest regex that covers the action.
- Add one direct handler or true executable owner and one `.codex/hooks.json` registration.
- Add its ownership row to `.codex/hooks/AGENTS.md` and exact registration expectation to `.codex/hooks/__tests__/registration.test.mjs`.
- Test a valid non-match, every decision branch, malformed and wrong-event input, redaction, repository-root and nested-directory execution, and the event-specific output schema.
- Add the corresponding Claude-native owner only when the contract must hold on both platforms.
- Run the focused handler and registration suites, the owner audit, and trust/exercise the changed definition through `/hooks` in a fresh Codex task.

## Consolidation Checklist

- Keep separate handlers when they represent distinct event lifecycles, matcher scopes, or true executable owners. Multiple commands matching the same event and tool launch concurrently and do not create ordering guarantees.
- When one event/tool matcher covers several policy checks that share parsing or require deterministic lazy evaluation, register one direct event owner. It validates native input once, parses once, evaluates narrow modules in a fixed order, and emits one platform decision. Keep each module independently testable and free of stdin and native-output behavior.
- Reject second runtime decision owners and handlers whose only behavior is forwarding stdin to another repository executable.
- `Stop` and `SubagentStop` registrations must return valid JSON and honor `stop_hook_active` so a continuation decision cannot loop indefinitely.
- Validation for Codex hook changes: run `pnpm hooks:test`, JSON-parse `.codex/hooks.json`, execute the changed owner with representative root and nested-directory payloads, and re-trust the definition via `/hooks` in a fresh Codex task.
