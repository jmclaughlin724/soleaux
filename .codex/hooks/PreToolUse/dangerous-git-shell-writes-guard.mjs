/* eslint-disable no-magic-numbers -- Git and shell CLI token positions are protocol mechanics. */
import { basename } from "node:path";

import {
  commandArguments,
  commandName,
  commandStart,
  environmentAssignment,
  parseGitInvocation,
} from "./bash-ast.mjs";

const ENVIRONMENT_VALUE_FILES = new Set([
  ".env",
  ".env.development",
  ".env.local",
  ".env.production",
  ".env.test",
]);
const DESTRUCTIVE_SWITCH_ARGUMENTS = new Set([
  "--discard-changes",
  "--force",
  "--force-create",
  "-C",
]);
const HOOK_EXECUTING_GIT_SUBCOMMANDS = new Set([
  "am",
  "cherry-pick",
  "commit",
  "merge",
  "rebase",
  "revert",
]);

function blocked(message, correctiveAction) {
  return [`BLOCKED: ${message}`, `Corrective action: ${correctiveAction}`].join(
    "\n"
  );
}

function containsShortFlag(argument, flag) {
  return (
    argument.startsWith("-") &&
    !argument.startsWith("--") &&
    argument.slice(1).includes(flag)
  );
}

function rmIncludesRecursiveForce(arguments_) {
  let isRecursive = false;
  let isForce = false;
  for (const argument of arguments_) {
    if (argument === "--") {
      break;
    }
    isRecursive ||= argument === "--recursive";
    isForce ||= argument === "--force";
    if (argument.startsWith("-") && !argument.startsWith("--")) {
      isRecursive ||= containsShortFlag(argument, "r");
      isRecursive ||= containsShortFlag(argument, "R");
      isForce ||= containsShortFlag(argument, "f");
    }
  }
  return isRecursive && isForce;
}

function disablesGitHooks(words) {
  return words.slice(0, commandStart(words)).some((word) => {
    const assignment = environmentAssignment(word);
    return assignment?.name === "HUSKY" && assignment.value === "0";
  });
}

function gitAliasName(value) {
  const separator = value.indexOf("=");
  const name = (
    separator === -1 ? value : value.slice(0, separator)
  ).toLowerCase();
  if (!name.startsWith("alias.")) {
    return "";
  }
  return name.slice("alias.".length).replace(/\.command$/u, "");
}

function gitConfigWritesAlias(arguments_) {
  const index = arguments_.findIndex((argument) => gitAliasName(argument));
  return index !== -1 && index < arguments_.length - 1;
}

function gitConfigWritesHooksPath(arguments_) {
  const index = arguments_.findIndex((argument) => {
    const separator = argument.indexOf("=");
    const key = separator === -1 ? argument : argument.slice(0, separator);
    return key.toLowerCase() === "core.hookspath";
  });
  if (index === -1) {
    return false;
  }
  const mutationOption = arguments_.some((argument) =>
    ["--add", "--replace-all", "--unset", "--unset-all"].includes(argument)
  );
  return mutationOption || index < arguments_.length - 1;
}

function gitGlobalOptionsBypassHooks(words) {
  const arguments_ = commandArguments(words);
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    if (!argument.startsWith("-") || argument === "-") {
      return false;
    }
    if (argument === "-c") {
      const assignment = (arguments_[index + 1] ?? "").toLowerCase();
      if (assignment.startsWith("core.hookspath=")) {
        return true;
      }
      index += 1;
      continue;
    }
    if (argument === "--config-env") {
      const assignment = (arguments_[index + 1] ?? "").toLowerCase();
      if (assignment.startsWith("core.hookspath=")) {
        return true;
      }
      index += 1;
      continue;
    }
    if (
      argument.toLowerCase().startsWith("-ccore.hookspath=") ||
      argument.toLowerCase().startsWith("--config-env=core.hookspath=")
    ) {
      return true;
    }
    if (
      [
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
        "-C",
      ].includes(argument)
    ) {
      index += 1;
    }
  }
  return false;
}

function checkGitBranch(arguments_) {
  const forceDelete =
    arguments_.includes("-D") ||
    (arguments_.includes("--delete") && arguments_.includes("--force"));
  const forceMove =
    arguments_.includes("--force") ||
    arguments_.some((argument) => containsShortFlag(argument, "f"));
  return forceDelete || forceMove
    ? blocked(
        "force-deleting or force-moving a branch can discard recovery refs.",
        "Delete only a merged branch with `git branch --delete <branch>`, or rename without force using `git branch --move <old> <new>`."
      )
    : null;
}

function checkGitCheckout(arguments_) {
  const force =
    arguments_.includes("--force") ||
    arguments_.some((argument) => containsShortFlag(argument, "f"));
  const forceCreate =
    arguments_.includes("-B") || arguments_.includes("--force-create");
  const pathCheckout =
    arguments_.includes("--") ||
    arguments_[0] === "." ||
    (arguments_[0] === "HEAD" && arguments_[1] === "--");
  return force || forceCreate || pathCheckout
    ? blocked(
        "this checkout form can discard worktree or branch state.",
        "Preserve the current work, use `git switch <existing-topic-branch>` for branch navigation, and use apply_patch, Edit, or Write for reviewed source restoration."
      )
    : null;
}

function checkGitClean(arguments_) {
  const dryRun =
    arguments_.includes("--dry-run") ||
    arguments_.includes("-n") ||
    arguments_.some((argument) => containsShortFlag(argument, "n"));
  const help = arguments_.some((argument) =>
    ["--help", "-h"].includes(argument)
  );
  return dryRun || help
    ? null
    : blocked(
        "git clean can delete untracked work.",
        "Inspect with `git clean -n`, then remove only explicitly authorized exact paths without recursive force."
      );
}

function checkGitCommit(arguments_) {
  const bypassesHooks =
    arguments_.includes("--no-verify") ||
    arguments_.some((argument) => containsShortFlag(argument, "n"));
  return bypassesHooks
    ? blocked(
        "--no-verify is prohibited because it hides repository hook failures.",
        "Run the failing hook command named by Git, fix its reported owner, then retry `git commit` without `--no-verify`."
      )
    : null;
}

function checkGitAdd(arguments_) {
  const force =
    arguments_.includes("--force") ||
    arguments_.some((argument) => containsShortFlag(argument, "f"));
  if (force) {
    return blocked(
      "force-adding ignored files can stage secrets or generated output.",
      "Remove `--force` and stage only reviewed paths with `git add -- <path>`; if the file should be tracked, update its canonical ignore owner in a reviewed edit first."
    );
  }
  const environmentFile = arguments_.find(
    (argument) =>
      !argument.startsWith("-") &&
      ENVIRONMENT_VALUE_FILES.has(basename(argument))
  );
  return environmentFile
    ? blocked(
        `do not stage environment-value file ${environmentFile}.`,
        "Commit a names-only `.env.example` or `.env.template` and keep real values in the approved secret store."
      )
    : null;
}

function checkGitSwitch(arguments_) {
  const destructive = arguments_.some(
    (argument) =>
      DESTRUCTIVE_SWITCH_ARGUMENTS.has(argument) ||
      containsShortFlag(argument, "f")
  );
  return destructive
    ? blocked(
        "this switch form can discard worktree or branch state.",
        "Preserve the current work and use `git switch <existing-topic-branch>` without force or discard options."
      )
    : null;
}

function checkGitStash(arguments_) {
  return ["clear", "drop"].includes(arguments_[0] ?? "")
    ? blocked(
        "do not permanently delete stashed work.",
        "Inspect with `git stash list` and `git stash show -p <stash>`, then restore with `git stash apply <stash>`."
      )
    : null;
}

function checkGitWorktree(arguments_) {
  if (
    arguments_.length === 3 &&
    arguments_[0] === "list" &&
    arguments_[1] === "--porcelain" &&
    arguments_[2] === "-z"
  ) {
    return null;
  }
  return blocked(
    "git worktree mutations are outside this task's repository-safety boundary.",
    "Use exactly `git worktree list --porcelain -z` for read-only inventory; obtain explicit authorization before any worktree mutation."
  );
}

function checkGitConfig(arguments_) {
  if (gitConfigWritesAlias(arguments_)) {
    return blocked(
      "Git alias configuration cannot bypass source-control policy.",
      "Invoke the canonical Git subcommand directly and leave alias configuration unchanged."
    );
  }
  return gitConfigWritesHooksPath(arguments_)
    ? blocked(
        "Git configuration cannot replace or remove core.hooksPath.",
        "Keep the repository-owned `.husky/` hooks active and run `pnpm check:hooks` to diagnose or repair their registration."
      )
    : null;
}

function checkGhPrMerge(arguments_) {
  if (arguments_[0] !== "pr" || arguments_[1] !== "merge") {
    return null;
  }
  const mergeArguments = arguments_.slice(2);
  if (mergeArguments.some((argument) => ["--help", "-h"].includes(argument))) {
    return null;
  }
  const blockedArgument = mergeArguments.find((argument) =>
    ["--admin", "--disable-auto", "--merge", "--rebase"].includes(argument)
  );
  if (blockedArgument) {
    return blocked(
      `gh pr merge ${blockedArgument} is prohibited.`,
      "Use `gh pr merge <number> --squash --delete-branch`."
    );
  }
  return mergeArguments.includes("--squash") &&
    mergeArguments.includes("--delete-branch")
    ? null
    : blocked(
        "gh pr merge requires the repository's squash-and-delete delivery shape.",
        "Use `gh pr merge <number> --squash --delete-branch`."
      );
}

function checkGitInvocation(words) {
  const invocation = parseGitInvocation(words);
  if (invocation.invokesAlias) {
    return blocked(
      "Git aliases cannot bypass source-control policy.",
      "Invoke the canonical Git subcommand directly without an alias."
    );
  }
  const [subcommand, ...arguments_] = invocation.args;
  if (
    HOOK_EXECUTING_GIT_SUBCOMMANDS.has(subcommand) &&
    gitGlobalOptionsBypassHooks(words)
  ) {
    return blocked(
      "Git configuration cannot replace core.hooksPath for repository operations.",
      "Run the operation normally with repository hooks enabled; use `pnpm check:hooks` to diagnose hook registration."
    );
  }
  if (subcommand === "add") {
    return checkGitAdd(arguments_);
  }
  if (subcommand === "branch") {
    return checkGitBranch(arguments_);
  }
  if (subcommand === "checkout") {
    return checkGitCheckout(arguments_);
  }
  if (subcommand === "clean") {
    return checkGitClean(arguments_);
  }
  if (subcommand === "commit") {
    return checkGitCommit(arguments_);
  }
  if (
    subcommand === "filter-branch" ||
    subcommand === "update-ref" ||
    (subcommand === "reflog" && arguments_[0] === "expire")
  ) {
    return blocked(
      `git ${subcommand} can discard recovery or reference state and is prohibited.`,
      "Use `git log --reflog` and `git show <ref>` for read-only recovery inspection, and obtain explicit authorization before any history or reference rewrite."
    );
  }
  if (subcommand === "reset" && arguments_.includes("--hard")) {
    return blocked(
      "git reset --hard can discard local work.",
      "Inspect with `git status --short` and `git diff`, then use apply_patch, Edit, or Write for an explicitly reviewed source correction."
    );
  }
  if (subcommand === "stash") {
    return checkGitStash(arguments_);
  }
  if (subcommand === "switch") {
    return checkGitSwitch(arguments_);
  }
  if (subcommand === "worktree") {
    return invocation.hasGlobalOptions
      ? blocked(
          "Git global options cannot wrap a worktree command.",
          "Invoke exactly `git worktree list --porcelain -z` for read-only inventory."
        )
      : checkGitWorktree(arguments_);
  }
  if (subcommand === "config") {
    return checkGitConfig(arguments_);
  }
  return null;
}

function checkGovernedInvocation(name, words) {
  switch (name) {
    case "git-worktree": {
      return checkGitWorktree(commandArguments(words));
    }
    case "git": {
      return checkGitInvocation(words);
    }
    case "gh": {
      return checkGhPrMerge(commandArguments(words));
    }
    default: {
      return null;
    }
  }
}

export function evaluateDangerousGitShellWritesGuard({ bash }) {
  for (const { words } of bash.invocations) {
    const name = commandName(words);
    if (name.startsWith("$")) {
      return blocked(
        "a dynamic executable cannot be inspected.",
        "Replace the variable executable with the literal canonical command and retry."
      );
    }
    if (disablesGitHooks(words)) {
      return blocked(
        "HUSKY=0 cannot disable repository Git hooks.",
        "Remove `HUSKY=0`, run `pnpm check:hooks` if a hook is failing, then retry the Git command normally."
      );
    }
    if (name === "rm" && rmIncludesRecursiveForce(commandArguments(words))) {
      return blocked(
        "`rm -rf` and equivalent recursive-plus-force removal are prohibited.",
        "Use apply_patch, Edit, or Write for reviewed repository deletions; for non-repository cleanup, resolve and review each exact target and use a non-recursive removal."
      );
    }
    const result = checkGovernedInvocation(name, words);
    if (result) {
      return result;
    }
  }
  return null;
}
