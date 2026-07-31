---
description: Keep topic-branch publication and pull requests bound to the matching origin branch.
---

# Git remote and delivery bindings

## Contract

- `remote.origin.fetch` must include `+refs/heads/*:refs/remotes/origin/*`; a branch upstream setting does not replace the required remote-tracking refspec.
- First publication is `git push --set-upstream origin <current-branch>`. Later publication is `git push origin <current-branch>`.
- Never use an implicit push, `HEAD`, another remote, another branch, a detached HEAD, or a protected branch for delivery.
- Repair existing same-branch metadata only with `git branch --set-upstream-to origin/<current-branch> <current-branch>`. Do not mutate `.git/config` from a hook.
- Create a pull request only when the upstream is `origin/<current-branch>` and `refs/remotes/origin/<current-branch>` exists and equals `HEAD`.
- If the binding is invalid, report the exact `git remote set-branches`, `git fetch`, upstream-repair, and explicit push commands needed to repair it.

Claude command enforcement lives in `.claude/settings.json` and `.claude/hooks/**`. Codex command decisions live in `.codex/rules/*.rules`, and Codex runtime delivery enforcement is the directly registered `git-delivery-binding-guard.mjs`.
