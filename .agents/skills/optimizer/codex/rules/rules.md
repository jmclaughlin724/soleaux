# Rules Playbook

Sources verified 2026-05-27:

- https://developers.openai.com/codex/rules
- https://developers.openai.com/codex/config-reference

## Intent

Use Codex rules only for executable shell prefixes that must fail before they run. Although upstream execpolicy supports multiple decisions, this repository's rule surface is deny-only: every `prefix_rule` uses `decision = "forbidden"`. Valid or context-dependent commands remain unmatched and are governed by instructions, runtime hooks, task authorization, or the relevant operator workflow. Do not use rules as long-form instructions.

## When To Add A Rule

- Add a rule only when the matched command always violates a repository invariant and must be rejected even if the agent tries it.
- Leave safe commands and commands that can be valid for an authorized task unmatched.
- Use the owning runtime hook when the decision depends on parsed command structure, paths, repository state, event payload, or another runtime fact.
- Do not add a rule for a vague preference. Convert the preference into a command pattern first.

## Authoring Steps

1. Identify the exact command prefix. Prefer the shortest stable prefix that captures intent without catching unrelated commands.
2. Set `decision = "forbidden"`. `allow`, `prompt`, and an omitted decision are invalid on this repository surface.
3. Write a human-readable justification that states the violated invariant and tells the agent which permitted command, workflow, or response to use instead.
4. Add `match` and a nearby `not_match` for new or changed rules whenever feasible. Treat them as inline unit tests that Codex validates when loading the rule file, not as prose examples.
5. Account for common shell wrappers. Codex can split simple `bash -lc`, `sh`, and `zsh` wrappers, but complex shell syntax is treated conservatively.

## Testing A Rule

Use upstream policy checks for rule behavior:

```bash
codex execpolicy check --pretty --rules path/to/rules -- command args
```

Test both the command that should match and at least one nearby command that should not match.

## Repo Delivery Pattern

- Durable policy prose lives in `AGENTS.md` and `.claude/rules/**`.
- `.codex/rules/**` is hand-authored Codex-native executable shell policy (execpolicy) with short pointers to prose owners — not a generated mirror. Keep tracker rows aligned across parallel owners (row 98 ↔ `# r98`).
- Edit the owning rule file directly, then run `pnpm execpolicy:check`.
- Rule edits still follow the repo's current rule-check closeout. Skill edits do not inherit rule validation.
