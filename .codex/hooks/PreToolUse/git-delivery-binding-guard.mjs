/* eslint-disable no-magic-numbers -- Git CLI token positions and process limits are protocol mechanics. */
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

import {
  commandArguments,
  commandName,
  parseGitInvocation,
} from "./bash-ast.mjs";

const REQUIRED_ORIGIN_FETCH = "+refs/heads/*:refs/remotes/origin/*";
const PROTECTED_BRANCHES = new Set(["main", "master"]);
const PROHIBITED_PUSH_ARGUMENTS = new Set(["--force", "--force-if-includes"]);
const DIAGNOSTIC_PIPE_COMMANDS = new Set([
  "awk",
  "grep",
  "head",
  "sed",
  "tail",
  "wc",
]);

function runGit(cwd, arguments_, { optional = false } = {}) {
  const result = spawnSync("git", arguments_, {
    cwd,
    encoding: "utf-8",
    maxBuffer: 1_048_576,
    timeout: 5000,
  });
  if (result.error || result.status !== 0) {
    if (optional) {
      return "";
    }
    throw new Error("Git repository state could not be resolved");
  }
  return result.stdout.trim();
}

function repositoryRoot(cwd) {
  return resolve(runGit(cwd, ["rev-parse", "--show-toplevel"]));
}

function currentBranch(root) {
  return runGit(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], {
    optional: true,
  });
}

function currentUpstream(root) {
  return runGit(
    root,
    ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
    { optional: true }
  );
}

function hasRequiredOriginFetch(root) {
  const refspecs = runGit(
    root,
    ["config", "--get-all", "remote.origin.fetch"],
    { optional: true }
  )
    .split("\n")
    .filter(Boolean);
  return refspecs.includes(REQUIRED_ORIGIN_FETCH);
}

function revision(root, name) {
  const commitRevision = [name, "^{commit}"].join("");
  return runGit(root, ["rev-parse", "--verify", commitRevision], {
    optional: true,
  });
}

function fetchRepairCommands() {
  return ["git remote set-branches origin '*'", "git fetch origin --prune"];
}

function firstPublicationCommand(branch) {
  return `git push --set-upstream origin ${branch}`;
}

function laterPublicationCommand(branch) {
  return `git push origin ${branch}`;
}

function upstreamRepairCommand(branch) {
  return `git branch --set-upstream-to origin/${branch} ${branch}`;
}

function bindingRepairCommands(root, branch) {
  if (!hasRequiredOriginFetch(root)) {
    return fetchRepairCommands();
  }
  const remoteRevision = revision(root, `refs/remotes/origin/${branch}`);
  if (!remoteRevision) {
    return [firstPublicationCommand(branch)];
  }
  if (currentUpstream(root) !== `origin/${branch}`) {
    return [upstreamRepairCommand(branch)];
  }
  return [laterPublicationCommand(branch)];
}

function denial(message, correctiveActions) {
  if (!Array.isArray(correctiveActions) || correctiveActions.length === 0) {
    throw new TypeError("Git delivery denial requires a corrective action");
  }
  return [
    `BLOCKED: ${message}`,
    "Corrective action:",
    ...[...new Set(correctiveActions)].map((action) => `  ${action}`),
  ].join("\n");
}

function containsShortFlag(argument, flag) {
  return (
    argument.startsWith("-") &&
    !argument.startsWith("--") &&
    argument.slice(1).includes(flag)
  );
}

function prohibitedPushArgument(argument) {
  return (
    PROHIBITED_PUSH_ARGUMENTS.has(argument) ||
    argument.startsWith("--force-with-lease") ||
    containsShortFlag(argument, "f")
  );
}

function isDiagnosticPipe(nextInvocation) {
  return (
    nextInvocation?.separatorBefore.includes("|") &&
    DIAGNOSTIC_PIPE_COMMANDS.has(commandName(nextInvocation.words))
  );
}

function requireTopicBranch(root) {
  const branch = currentBranch(root);
  if (!branch) {
    return {
      branch: "",
      reason: denial(
        "Git delivery requires an attached topic branch; HEAD is detached.",
        [
          "Switch to an existing non-protected topic branch with `git switch <branch>`, then retry the delivery command.",
        ]
      ),
    };
  }
  if (PROTECTED_BRANCHES.has(branch)) {
    return {
      branch,
      reason: denial(
        `direct delivery from protected branch ${branch} is prohibited.`,
        [
          "Switch to an existing non-protected topic branch with `git switch <branch>`, then retry the delivery command.",
        ]
      ),
    };
  }
  return { branch, reason: null };
}

function branchRepair(arguments_, branch) {
  if (arguments_.length === 3) {
    const [option, upstream, target] = arguments_;
    return (
      ["--set-upstream-to", "-u"].includes(option) &&
      upstream === `origin/${branch}` &&
      target === branch
    );
  }
  return false;
}

function evaluateBranchRepair(root, branch, arguments_) {
  if (!hasRequiredOriginFetch(root)) {
    return denial(
      `origin must fetch every branch through ${REQUIRED_ORIGIN_FETCH}.`,
      fetchRepairCommands()
    );
  }
  if (!revision(root, `refs/remotes/origin/${branch}`)) {
    return denial(
      `origin/${branch} is missing; publish the current branch first.`,
      [firstPublicationCommand(branch)]
    );
  }
  if (!branchRepair(arguments_, branch)) {
    return denial(
      "upstream repair must bind the current branch to the same origin branch.",
      [upstreamRepairCommand(branch)]
    );
  }
  return null;
}

function evaluateOriginFetchRepair(invocation, arguments_) {
  if (invocation.hasGlobalOptions) {
    return denial(
      "origin fetch repair does not permit Git global options.",
      fetchRepairCommands()
    );
  }
  if (arguments_.some((argument) => ["--help", "-h"].includes(argument))) {
    return null;
  }
  return JSON.stringify(arguments_) ===
    JSON.stringify(["set-branches", "origin", "*"])
    ? null
    : denial(
        `origin must fetch every branch through ${REQUIRED_ORIGIN_FETCH}.`,
        fetchRepairCommands()
      );
}

function evaluatePush(root, branch, invocation, nextInvocation) {
  if (invocation.hasGlobalOptions) {
    return denial(
      "Git delivery does not permit global Git options on push.",
      bindingRepairCommands(root, branch)
    );
  }
  const arguments_ = invocation.args.slice(1);
  if (
    arguments_.some(prohibitedPushArgument) ||
    arguments_.some((argument) => argument.startsWith("+"))
  ) {
    return denial(
      "force pushes are prohibited; publish the current topic branch without rewriting remote history.",
      bindingRepairCommands(root, branch)
    );
  }
  const dryRun = arguments_.includes("--dry-run") || arguments_.includes("-n");
  if (!dryRun && isDiagnosticPipe(nextInvocation)) {
    return denial(
      "do not use git push as a diagnostic probe; use the explicit dry-run publication command.",
      [`git push --dry-run origin ${branch}`]
    );
  }
  if (!hasRequiredOriginFetch(root)) {
    return denial(
      `origin must fetch every branch through ${REQUIRED_ORIGIN_FETCH}.`,
      fetchRepairCommands()
    );
  }
  if (dryRun) {
    return JSON.stringify(arguments_) ===
      JSON.stringify(["--dry-run", "origin", branch]) ||
      JSON.stringify(arguments_) === JSON.stringify(["-n", "origin", branch])
      ? null
      : denial(
          `dry-run publication must target origin/${branch} explicitly.`,
          bindingRepairCommands(root, branch)
        );
  }
  const remoteRevision = revision(root, `refs/remotes/origin/${branch}`);
  const upstream = currentUpstream(root);
  if (!upstream) {
    if (remoteRevision) {
      return denial(
        `origin/${branch} already exists and must be bound as the current upstream before publication.`,
        [upstreamRepairCommand(branch)]
      );
    }
    const expected = ["--set-upstream", "origin", branch];
    return JSON.stringify(arguments_) === JSON.stringify(expected)
      ? null
      : denial(
          `first publication must be \`git push --set-upstream origin ${branch}\`.`,
          [firstPublicationCommand(branch)]
        );
  }
  if (upstream !== `origin/${branch}`) {
    return denial(
      `the current upstream is ${upstream}, not origin/${branch}.`,
      bindingRepairCommands(root, branch)
    );
  }
  return JSON.stringify(arguments_) === JSON.stringify(["origin", branch])
    ? null
    : denial(`later publication must be \`git push origin ${branch}\`.`, [
        laterPublicationCommand(branch),
      ]);
}

function optionValue(arguments_, names) {
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const separator = argument.indexOf("=");
    const option = separator === -1 ? argument : argument.slice(0, separator);
    if (!names.has(option)) {
      continue;
    }
    return separator === -1
      ? (arguments_[index + 1] ?? "")
      : argument.slice(separator + 1);
  }
  return "";
}

function isPrCreate(arguments_) {
  const prIndex = arguments_.indexOf("pr");
  return (
    prIndex !== -1 &&
    arguments_[prIndex + 1] === "create" &&
    arguments_.every((argument) => !["--help", "-h"].includes(argument))
  );
}

function overridesRepositoryTarget(argument) {
  if (argument === "--repo" || argument === "-R") {
    return true;
  }
  return (
    argument.startsWith("--repo=") ||
    (argument.startsWith("-R") && argument.length > "-R".length)
  );
}

function evaluatePrCreate(root, branch, arguments_) {
  if (arguments_.some(overridesRepositoryTarget)) {
    return denial("gh pr create cannot override the repository target.", [
      "Remove `--repo`/`-R` and run `gh pr create` from the current checkout.",
    ]);
  }
  if (!hasRequiredOriginFetch(root)) {
    return denial(
      `origin must fetch every branch through ${REQUIRED_ORIGIN_FETCH}.`,
      fetchRepairCommands()
    );
  }
  const localRevision = revision(root, "HEAD");
  const remoteRevision = revision(root, `refs/remotes/origin/${branch}`);
  if (!remoteRevision) {
    return denial(
      `origin/${branch} is missing; publish the current branch first.`,
      [firstPublicationCommand(branch)]
    );
  }
  const upstream = currentUpstream(root);
  if (upstream !== `origin/${branch}`) {
    return denial(`gh pr create requires upstream origin/${branch}.`, [
      upstreamRepairCommand(branch),
    ]);
  }
  const head = optionValue(arguments_, new Set(["--head", "-H"]));
  if (head && head !== branch) {
    return denial(
      `gh pr create --head must name the current branch ${branch}.`,
      [
        `Omit --head or use \`--head ${branch}\`, then retry from the current checkout.`,
      ]
    );
  }
  if (remoteRevision !== localRevision) {
    return denial(
      `origin/${branch} is stale and must equal HEAD before pull-request creation.`,
      [laterPublicationCommand(branch)]
    );
  }
  return null;
}

function evaluateGitDeliveryInvocation(root, words, nextInvocation) {
  const invocation = parseGitInvocation(words);
  const [subcommand, ...arguments_] = invocation.args;
  if (
    subcommand === "push" &&
    arguments_.some((argument) => ["--help", "-h"].includes(argument))
  ) {
    return null;
  }
  const isOriginFetchRepair =
    subcommand === "remote" && arguments_[0] === "set-branches";
  if (isOriginFetchRepair) {
    return evaluateOriginFetchRepair(invocation, arguments_);
  }
  if (subcommand !== "push" && subcommand !== "branch") {
    return null;
  }
  const isUpstreamRepair =
    subcommand === "branch" &&
    arguments_.some(
      (argument) =>
        argument === "-u" ||
        argument === "--set-upstream-to" ||
        argument.startsWith("--set-upstream-to=")
    );
  if (subcommand === "branch" && !isUpstreamRepair) {
    return null;
  }
  const topic = requireTopicBranch(root);
  if (topic.reason) {
    return topic.reason;
  }
  if (subcommand === "push") {
    return evaluatePush(root, topic.branch, invocation, nextInvocation);
  }
  return evaluateBranchRepair(root, topic.branch, arguments_);
}

function evaluateGhDeliveryInvocation(root, words) {
  const arguments_ = commandArguments(words);
  if (!isPrCreate(arguments_)) {
    return null;
  }
  const topic = requireTopicBranch(root);
  return topic.reason || evaluatePrCreate(root, topic.branch, arguments_);
}

function requiresDeliveryState(words) {
  const name = commandName(words);
  if (name === "gh") {
    return isPrCreate(commandArguments(words));
  }
  if (name !== "git") {
    return false;
  }
  const { args } = parseGitInvocation(words);
  const [subcommand, ...arguments_] = args;
  if (
    subcommand === "push" &&
    arguments_.every((argument) => !["--help", "-h"].includes(argument))
  ) {
    return true;
  }
  if (subcommand === "remote" && arguments_[0] === "set-branches") {
    return true;
  }
  return (
    subcommand === "branch" &&
    arguments_.some(
      (argument) =>
        argument === "-u" ||
        argument === "--set-upstream-to" ||
        argument.startsWith("--set-upstream-to=")
    )
  );
}

export function evaluateGitDeliveryBindingGuard({ bash, cwd }) {
  if (bash.invocations.every(({ words }) => !requiresDeliveryState(words))) {
    return null;
  }
  const root = repositoryRoot(cwd);
  for (let index = 0; index < bash.invocations.length; index += 1) {
    const invocation = bash.invocations[index];
    const { words } = invocation;
    const name = commandName(words);
    let result = null;
    if (name === "git") {
      result = evaluateGitDeliveryInvocation(
        root,
        words,
        bash.invocations[index + 1]
      );
    } else if (name === "gh") {
      result = evaluateGhDeliveryInvocation(root, words);
    }
    if (result) {
      return result;
    }
  }
  return null;
}
