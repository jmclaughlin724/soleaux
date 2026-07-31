import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateDangerousGitShellWritesGuard } from "../PreToolUse/dangerous-git-shell-writes-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

function bash(command) {
  return {
    bash: parseBash(command),
    cwd: repoRoot,
  };
}

test.each([
  "rm -rf node_modules",
  "rm -r -f /tmp/test",
  "rm --recursive --force .cache",
  "bash -lc 'echo safe; rm -Rf build'",
])("blocks recursive-plus-force removal: %s", (command) => {
  expect(evaluateDangerousGitShellWritesGuard(bash(command))).toContain(
    "recursive-plus-force"
  );
});

test.each([
  "git clean -fd",
  "git checkout -- apps/web/page.tsx",
  "git checkout -f main",
  "git checkout -B codex/topic",
  "git reset --hard HEAD~1",
  "git branch -D codex/topic",
  "git branch --delete --force codex/topic",
  "git switch -C codex/topic",
  "git switch --discard-changes main",
  "git stash clear",
  "git stash drop stash@{1}",
  "git commit --no-verify -m test",
  "git commit -n -m test",
  "git add --force generated.txt",
  "git add .env.local",
  "git reflog expire --expire=now --all",
  "git update-ref refs/heads/x HEAD",
  "git filter-branch --tree-filter true HEAD",
  "git -c core.hooksPath=/tmp/hooks commit -m test",
  "git config --local core.hooksPath /tmp/hooks",
  "git config --unset-all core.hooksPath",
  "HUSKY=0 git commit -m test",
  "env HUSKY=0 git commit -m test",
  "env -- HUSKY=0 git commit -m test",
  "env -i HUSKY=0 git commit -m test",
  "bash -lc 'HUSKY=0 git commit -m test'",
  "git worktree add /tmp/worktree",
  "git -C . worktree add /tmp/worktree",
])("blocks destructive Git operations: %s", (command) => {
  expect(evaluateDangerousGitShellWritesGuard(bash(command))).toMatch(
    /^BLOCKED:/u
  );
});

test("blocks Git aliases that conceal a governed command", () => {
  const command = [
    "GIT_CONFIG_COUNT=1",
    "GIT_CONFIG_KEY_0=alias.wt",
    "GIT_CONFIG_VALUE_0=worktree",
    "git wt add /tmp/worktree",
  ].join(" ");
  expect(evaluateDangerousGitShellWritesGuard(bash(command))).toContain(
    "aliases"
  );
});

test.each([
  "gh pr merge 123 --merge",
  "gh pr merge 123 --rebase",
  "gh pr merge 123 --squash",
])("blocks non-policy pull-request merge forms: %s", (command) => {
  expect(evaluateDangerousGitShellWritesGuard(bash(command))).toMatch(
    /^BLOCKED:/u
  );
});

test.each([
  "git status",
  "git diff --stat",
  "git branch --show-current",
  "git branch -d codex/topic",
  "git clean -n",
  "git checkout main",
  "git reset --soft HEAD~1",
  "git stash",
  "git stash list",
  "git push --dry-run origin feature",
  "git push origin feature",
  "git push --force origin feature",
  "git switch -c feature-branch origin/main",
  "git worktree list --porcelain -z",
  "git branch --set-upstream-to origin/feature feature",
  "git branch -u origin/feature feature",
  "git merge --squash feature",
  "git restore --source origin/main app.ts",
  "git mv old.ts new.ts",
  "git rm old.ts",
  "git add .env.example",
  "git config --get core.hooksPath",
  "git config core.hooksPath",
  "git -c core.hooksPath=/tmp/hooks status",
  "env HUSKY=1 git commit -m test",
  "gh pr merge 123 --squash --delete-branch",
])("remains silent for permitted Git and shell forms: %s", (command) => {
  expect(evaluateDangerousGitShellWritesGuard(bash(command))).toBeNull();
});
