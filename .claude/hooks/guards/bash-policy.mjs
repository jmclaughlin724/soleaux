import { existsSync } from "node:fs";
import {
    dirname,
    extname,
    isAbsolute,
    join,
    relative,
    resolve,
    sep,
} from "node:path";

// Deterministic Claude Bash policy. Codex composes its corresponding policy
// modules through one PreToolUse event owner because Codex matchers see only
// the tool name.
const readCommands = new Set([
  "awk",
  "bat",
  "cat",
  "egrep",
  "fgrep",
  "grep",
  "head",
  "less",
  "more",
  "nl",
  "rg",
  "sed",
  "tail",
]);
const secretFlagNames = new Set([
  "--api-key",
  "--password",
  "--secret",
  "--token",
  "--value",
]);
const safeEnvironmentTemplates = new Set([
  ".env.default",
  ".env.defaults",
  ".env.example",
  ".env.sample",
  ".env.template",
]);
const shellQuoteCharacters = new Set(["'", '"', "`"]);
const shellOperatorCharacters = new Set([";", "|", ">", "&", "(", ")"]);
const secretNameFragments = [
  "SECRET",
  "TOKEN",
  "PASSWORD",
  "KEY",
  "CREDENTIAL",
  "PASSWD",
  "PASS",
];
const prohibitedPushArguments = new Set(["--force", "--force-if-includes"]);
const destructiveCheckoutArguments = new Set(["--", "--force", "-B"]);
const destructiveSwitchArguments = new Set([
  "--discard-changes",
  "--force",
  "--force-create",
  "-C",
]);
const implicitPushExemptions = new Set(["--dry-run", "-n", "--help", "-h"]);
const maskedSecretFragments = [
  "[redacted]",
  "<password>",
  "<secret>",
  "<token>",
  "***",
  "xxx",
];
const whitespaceCharacters = new Set([" ", "\n", "\r", "\t"]);
const singleTokenDdlKeywords = new Set(["GRANT", "REVOKE", "TRUNCATE"]);
const heredocUnquotedTerminators = new Set([";", "|", "&"]);
const governedSourceExtensions = new Set([
  ".cjs",
  ".css",
  ".cts",
  ".js",
  ".json",
  ".jsonc",
  ".jsx",
  ".mjs",
  ".mts",
  ".py",
  ".ts",
  ".tsx",
]);
const approvedPnpmMutationScripts = new Set([
  "ast-grep:update-snapshots",
  "db:types",
  "fix",
  "fix:prose",
  "fix:toml",
  "supaschema:types",
]);
const inlineInterpreterWriteFragments = [
  "appendFile",
  "createWriteStream",
  "open(",
  "writeFile",
  "write_bytes",
  "write_text",
  "writeTextFile",
];
const genericOutputValueOptions = new Set([
  "--out",
  "--outfile",
  "--output",
  "--output-file",
  "--write",
  "-o",
]);
const gitWorktreeListArgumentCount = 3;
const maximumNestedCommandDepth = 3;
const minimumLiteralSecretLength = 16;
const maximumSecretPreviewLength = 44;
const secretPreviewPrefixLength = 16;
const secretPreviewSuffixLength = 6;
const minimumDatabaseUrlPasswordLength = 12;
const fourthSqlTokenOffset = 3;
const readOnlyCommandNames = new Set([
  "echo",
  "find",
  "git",
  "ls",
  "pwd",
  "which",
]);
const readOnlyGitSubcommands = new Set([
  "branch",
  "diff",
  "log",
  "rev-parse",
  "show",
  "status",
]);
const workspaceMutationCommandNames = new Set([
  "cp",
  "dd",
  "install",
  "mv",
  "patch",
  "perl",
  "rm",
  "sed",
  "tee",
  "touch",
  "truncate",
]);
const processMutationCommandNames = new Set([
  "kill",
  "killall",
  "launchctl",
  "pkill",
]);

export function isReadCommandName(name) {
  return readCommands.has(name);
}

function discoverRepositoryRoot(input, environment) {
  const configuredRoot =
    environment.SOLEAUX_REPOSITORY_ROOT ?? environment.CLAUDE_PROJECT_DIR;
  if (
    typeof configuredRoot === "string" &&
    configuredRoot.length > 0 &&
    isAbsolute(configuredRoot)
  ) {
    return resolve(configuredRoot);
  }
  const payloadCwd = input?.cwd;
  const start = resolve(
    typeof payloadCwd === "string" &&
      payloadCwd.length > 0 &&
      isAbsolute(payloadCwd)
      ? payloadCwd
      : process.cwd()
  );
  let candidate = start;
  while (true) {
    if (
      existsSync(join(candidate, ".git")) ||
      existsSync(join(candidate, "soleaux.toml"))
    ) {
      return candidate;
    }
    const parent = dirname(candidate);
    if (parent === candidate) {
      return start;
    }
    candidate = parent;
  }
}

export function evaluateBashPolicy(input, environment = process.env) {
  if (!isBashPayload(input)) {
    return allowResult();
  }

  const command = commandFromPayload(input);
  if (!command.trim()) {
    return allowResult();
  }

  for (const check of [
    checkSecretArgv,
    checkSecretEnvironmentFileRead,
    checkRawSqlDdlCommand,
    checkDangerousGitAndShellWrites,
  ]) {
    const result = check(command, environment);
    if (result.action !== "allow") {
      return result;
    }
  }

  const repositoryRoot = discoverRepositoryRoot(input, environment);
  const mutationResult = checkSourceMutationBoundary(
    command,
    input,
    repositoryRoot
  );
  if (mutationResult.action !== "allow") {
    return mutationResult;
  }

  return allowResult();
}

function isBashPayload(input) {
  return input?.tool_name === "Bash";
}

function commandFromPayload(input) {
  const toolInput = input?.tool_input ?? {};
  if (typeof toolInput.command === "string") {
    return toolInput.command;
  }
  return "";
}

function allowResult() {
  return { action: "allow" };
}

function block(message) {
  return { action: "block", message };
}

function checkSecretArgv(command) {
  const scanned = stripHeredocs(command);
  const hits = [];

  for (const tokens of commandSegments(scanned)) {
    for (let index = 0; index < tokens.length; index += 1) {
      const token = tokens[index] ?? "";
      if (hasDatabaseUrlWithInlinePassword(token)) {
        hits.push(describeHit("DB connection URL with inline password", token));
      }
      const assignment = environmentAssignment(token);
      if (
        assignment &&
        secretName(assignment.name) &&
        isLiteralSecretValue(assignment.value)
      ) {
        hits.push(
          describeHit(`inline secret env ${assignment.name}=<literal>`, token)
        );
      } else {
        const flagHit = secretFlagValue(tokens, index);
        if (flagHit && isLiteralSecretValue(flagHit.value)) {
          hits.push(
            describeHit(`${flagHit.name} literal in argv`, flagHit.preview)
          );
        }
      }
    }
  }

  if (hits.length === 0) {
    return allowResult();
  }

  return block(
    "BLOCKED: Secret material detected in Bash argv.\n\n" +
      `Matched patterns:\n  - ${hits.join("\n  - ")}\n\n` +
      "Use env-var references, stdin, or a secure file/secret-manager handoff. Do not put secrets in command argv."
  );
}

function checkSecretEnvironmentFileRead(command) {
  const matches = [];
  const segments = commandSegments(stripHeredocs(command));
  for (const tokens of segments) {
    const start = commandStart(tokens);
    if (!isReadCommandName(tokens[start] ?? "")) {
      continue;
    }
    const readArguments = tokens.slice(start + 1);
    for (const token of readArguments) {
      const fileName = environmentFileName(token);
      if (fileName && !safeEnvironmentTemplates.has(fileName)) {
        matches.push(fileName);
      }
    }
  }

  if (matches.length === 0) {
    return allowResult();
  }

  return block(
    "BLOCKED: Bash read/search of secret-bearing env files is prohibited.\n\n" +
      `Matched env file(s): ${[...new Set(matches)].join(", ")}\n\n` +
      "Use env-var references, a targeted non-secret example file, or an approved secret manager command."
  );
}

function checkRawSqlDdlCommand(command) {
  const segments = commandSegments(stripHeredocs(command));
  for (const tokens of segments) {
    if (!isRawSqlCliSegment(tokens)) {
      continue;
    }
    const keyword = sqlDdlKeyword(stripSqlComments(rawSqlPayload(tokens)));
    if (keyword) {
      return block(
        `BLOCKED: raw SQL DDL through Bash detected: ${keyword}.\n\n` +
          "Structural database changes must go through the declarative schema and generated migration workflow. Use `supaschema diff` and `supaschema check` for durable schema changes."
      );
    }
  }
  return allowResult();
}

const scopedGitSubcommandChecks = new Map([
  ["branch", checkGitBranch],
  ["config", checkGitConfig],
  ["worktree", checkGitWorktree],
]);

function checkGitWriteSubcommand(gitArguments, ast, tokens) {
  const subcommand = gitArguments[0] ?? "";
  const scopedCheck = scopedGitSubcommandChecks.get(subcommand);
  if (scopedCheck) {
    return scopedCheck(gitArguments.slice(1));
  }
  const arguments_ = gitArguments.slice(1);
  if (subcommand === "clean") {
    return checkGitClean(arguments_);
  }
  if (subcommand === "checkout") {
    return checkGitCheckout(arguments_);
  }
  if (subcommand === "reset" && arguments_.includes("--hard")) {
    return block(
      "BLOCKED: git reset --hard can discard local work. Use a non-destructive reset or an explicitly reviewed source edit."
    );
  }
  if (
    subcommand === "stash" &&
    ["clear", "drop"].includes(arguments_[0] ?? "")
  ) {
    return block(
      "BLOCKED: do not permanently delete stashed work. Inspect and preserve the stash."
    );
  }
  if (subcommand === "switch") {
    return checkGitSwitch(arguments_);
  }
  if (subcommand === "merge" && gitArguments.includes("--squash")) {
    return block(
      "BLOCKED: local `git merge --squash` is prohibited for PR merges. Use the hosted PR squash flow instead."
    );
  }
  if (
    subcommand === "commit" &&
    (arguments_.includes("--no-verify") ||
      arguments_.some((argument) => hasShortFlag(argument, "n")))
  ) {
    return block(
      "BLOCKED: --no-verify is prohibited. Fix the hook failure instead."
    );
  }
  if (subcommand === "push") {
    return checkGitPush(gitArguments, ast, tokens);
  }
  if (
    subcommand === "restore" &&
    gitArguments.some(
      (argument) => argument === "-s" || argument.startsWith("--source")
    )
  ) {
    return block(
      "BLOCKED: git restore --source is prohibited. It overwrites local files with content from another branch. Use git diff or git show for read-only comparisons."
    );
  }
  return allowResult();
}

function hasShortFlag(argument, flag) {
  return (
    argument.startsWith("-") &&
    !argument.startsWith("--") &&
    argument.slice(1).includes(flag)
  );
}

function checkGitClean(arguments_) {
  const dryRun =
    arguments_.includes("--dry-run") ||
    arguments_.some((argument) => hasShortFlag(argument, "n"));
  return dryRun
    ? allowResult()
    : block(
        "BLOCKED: git clean can delete untracked work. Inspect with `git clean -n` and remove only explicitly authorized paths."
      );
}

function checkGitCheckout(arguments_) {
  const destructive =
    arguments_[0] === "." ||
    arguments_.some(
      (argument) =>
        destructiveCheckoutArguments.has(argument) ||
        hasShortFlag(argument, "f")
    );
  return destructive
    ? block(
        "BLOCKED: this checkout form can discard worktree or branch state. Use a non-force branch checkout or an explicitly reviewed source edit."
      )
    : allowResult();
}

function checkGitPush(gitArguments, ast, tokens) {
  if (isProhibitedPush(gitArguments)) {
    return block(
      "BLOCKED: force pushes are prohibited. Publish a new topic commit instead."
    );
  }
  if (isPushToMain(gitArguments)) {
    return block(
      "BLOCKED: direct pushes to main are prohibited. Push a topic branch and merge its protected pull request."
    );
  }
  if (gitArguments.includes("HEAD")) {
    return block(
      "BLOCKED: symbolic HEAD pushes are ambiguous. Push an explicit topic branch or explicit HEAD:<topic> refspec."
    );
  }
  if (isImplicitPush(gitArguments)) {
    return block(
      "BLOCKED: implicit pushes are ambiguous. Push an explicit topic branch or use --dry-run for remote negotiation."
    );
  }
  if (isDiagnosticPush(ast, tokens, gitArguments)) {
    return block(
      "BLOCKED: Do not use `git push` as a diagnostic or inventory probe. Use `git push --dry-run` only when remote negotiation must be tested."
    );
  }
  return allowResult();
}

function checkGitConfig(arguments_) {
  if (!configWritesAlias(arguments_)) {
    return allowResult();
  }
  return block(
    "BLOCKED: Git alias configuration cannot bypass source-control policy. Invoke the canonical Git subcommand directly."
  );
}

function checkGitBranch(arguments_) {
  const force =
    arguments_.includes("-D") ||
    arguments_.includes("--force") ||
    arguments_.some((argument) => hasShortFlag(argument, "f"));
  return force
    ? block(
        "BLOCKED: force-deleting or force-moving a branch can discard recovery refs. Use a non-force branch operation."
      )
    : allowResult();
}

function checkGitWorktree(arguments_) {
  if (
    arguments_.length === gitWorktreeListArgumentCount &&
    arguments_[0] === "list" &&
    arguments_[1] === "--porcelain" &&
    arguments_[2] === "-z"
  ) {
    return allowResult();
  }
  return blockGitWorktree();
}

function blockGitWorktree() {
  return block(
    "BLOCKED: git worktree is limited to `git worktree list --porcelain -z` for stable read-only inventory. Use host-managed isolation for worktree creation or mutation."
  );
}

function checkGitSwitch(arguments_) {
  const destructive = arguments_.some(
    (argument) =>
      destructiveSwitchArguments.has(argument) || hasShortFlag(argument, "f")
  );
  return destructive
    ? block(
        "BLOCKED: this switch form can discard worktree or branch state. Preserve the current work and use a non-force switch."
      )
    : allowResult();
}

function checkDangerousGitAndShellWrites(command) {
  const ast = { segments: commandSegmentObjects(stripHeredocs(command)) };
  if (ast.segments.some((segment) => segment.nestedCommandLimitReached)) {
    return block(
      "BLOCKED: nested shell command depth exceeded the safety parser limit. Run the governed command directly."
    );
  }
  if (
    ast.segments.some(
      (segment) =>
        commandName(segment.words) === "rm" &&
        rmArgumentsIncludeRecursiveForce(commandArguments(segment.words))
    )
  ) {
    return block(
      "BLOCKED: `rm -rf` and equivalent recursive+force rm invocations are prohibited. Use explicit, reviewed file operations instead."
    );
  }

  for (let index = 0; index < ast.segments.length; index += 1) {
    const result = checkDangerousCommandSegment(ast, index);
    if (result.action !== "allow") {
      return result;
    }
  }

  return allowResult();
}

function checkDangerousCommandSegment(ast, index) {
  const segment = ast.segments[index];
  const tokens = segment.words;
  const name = commandName(tokens);
  if (disablesGitHooks(tokens)) {
    return block(
      "BLOCKED: HUSKY=0 cannot disable repository Git hooks. Run the command normally."
    );
  }
  if (
    isDynamicWorktreeInvocation(ast.segments, index) ||
    name === "git-worktree"
  ) {
    return blockGitWorktree();
  }
  if (name === "gh") {
    return checkGhPrMerge(commandArguments(tokens));
  }
  if (name !== "git") {
    return allowResult();
  }
  const invocation = parseGitInvocation(tokens);
  if (invocation.invokesAlias) {
    return block(
      "BLOCKED: Git aliases cannot bypass source-control policy. Invoke the canonical Git subcommand directly."
    );
  }
  if (invocation.hasGlobalOptions && invocation.args[0] === "worktree") {
    return blockGitWorktree();
  }
  return checkGitWriteSubcommand(invocation.args, ast, tokens);
}

function disablesGitHooks(tokens) {
  return tokens.slice(0, commandStart(tokens)).some((token) => {
    const assignment = environmentAssignment(token);
    return assignment?.name === "HUSKY" && assignment.value === "0";
  });
}

function checkSourceMutationBoundary(command, input, repositoryRoot) {
  const cwd = sourceMutationWorkingDirectory(input, repositoryRoot);
  const segments = commandSegmentObjects(stripHeredocs(command));
  for (const segment of segments) {
    if (segment.operatorBefore === ">") {
      const target = segment.words[0] ?? "";
      if (isGovernedRepositorySource(target, cwd, repositoryRoot)) {
        return blockSourceMutation(
          "shell output redirection",
          target,
          cwd,
          repositoryRoot
        );
      }
    }

    const result = checkSourceMutationSegment(
      segment.words,
      cwd,
      repositoryRoot
    );
    if (result.action !== "allow") {
      return result;
    }
  }
  return allowResult();
}

function checkSourceMutationSegment(tokens, cwd, repositoryRoot) {
  if (isApprovedOwnedMutator(tokens)) {
    return allowResult();
  }

  const name = commandName(tokens);
  const arguments_ = commandArguments(tokens);
  if (name === "tee") {
    return blockFirstGovernedTarget(
      "tee output",
      positionalArguments(arguments_),
      cwd,
      repositoryRoot
    );
  }
  if (["touch", "truncate"].includes(name)) {
    return blockFirstGovernedTarget(
      `${name} source mutation`,
      positionalArguments(arguments_),
      cwd,
      repositoryRoot
    );
  }
  if (["cp", "install", "mv"].includes(name)) {
    return checkCopyOrMoveMutation(name, arguments_, cwd, repositoryRoot);
  }
  if (name === "dd") {
    return blockFirstGovernedTarget(
      "dd output",
      arguments_
        .filter((argument) => argument.startsWith("of="))
        .map((argument) => argument.slice("of=".length)),
      cwd,
      repositoryRoot
    );
  }
  if (
    name === "sed" &&
    arguments_.some(
      (argument) =>
        argument === "-i" ||
        argument.startsWith("-i.") ||
        argument.startsWith("--in-place")
    )
  ) {
    return blockFirstGovernedTarget(
      "in-place sed rewrite",
      positionalArguments(arguments_),
      cwd,
      repositoryRoot
    );
  }
  if (name === "perl" && arguments_.some(isPerlInPlaceArgument)) {
    return blockFirstGovernedTarget(
      "in-place Perl rewrite",
      positionalArguments(arguments_),
      cwd,
      repositoryRoot
    );
  }
  if (name === "patch" && !arguments_.includes("--dry-run")) {
    return block(
      "BLOCKED: the Bash `patch` command bypasses prospective structural-policy evaluation. Use apply_patch, Edit, or Write so the complete resulting source is checked before mutation."
    );
  }
  if (name === "git") {
    const gitArguments = parseGitInvocation(tokens).args;
    if (
      ["am", "apply", "cherry-pick", "rebase", "restore"].includes(
        gitArguments[0] ?? ""
      ) &&
      !isReadOnlyGitMutationProbe(gitArguments)
    ) {
      return block(
        "BLOCKED: this Git worktree mutation bypasses prospective structural-policy evaluation and may overwrite protected work. Use apply_patch, Edit, or Write for source changes; use git diff or git show for read-only inspection."
      );
    }
  }

  const outputTargets = genericOutputTargets(arguments_);
  const outputResult = blockFirstGovernedTarget(
    `${name || "unknown command"} output option`,
    outputTargets,
    cwd,
    repositoryRoot
  );
  if (outputResult.action !== "allow") {
    return outputResult;
  }

  if (isInlineInterpreterMutation(name, arguments_)) {
    const sourceMention = arguments_.find((argument) =>
      mentionsGovernedSourcePath(argument)
    );
    if (sourceMention) {
      return block(
        `BLOCKED: ${name} inline code contains a file-write primitive and a governed source path, so it would bypass prospective structural-policy evaluation.\n\n` +
          "Use apply_patch, Edit, or Write so the complete resulting file is checked before mutation."
      );
    }
  }
  return allowResult();
}

function sourceMutationWorkingDirectory(input, repositoryRoot) {
  const cwd = input?.cwd;
  return typeof cwd === "string" && cwd.length > 0
    ? resolve(cwd)
    : repositoryRoot;
}

function isWithinRepository(candidatePath, repositoryRoot) {
  const repositoryPath = relative(repositoryRoot, candidatePath);
  return (
    repositoryPath === "" ||
    (repositoryPath !== ".." &&
      !repositoryPath.startsWith(`..${sep}`) &&
      !isAbsolute(repositoryPath))
  );
}

function cleanMutationTarget(value) {
  return trimShellPunctuation(value.split("=", 2).at(-1) ?? "");
}

function isGovernedRepositorySource(value, cwd, repositoryRoot) {
  const target = cleanMutationTarget(value);
  if (!target || containsAny(target, ["$", "*", "?"])) {
    return false;
  }
  const candidate = isAbsolute(target) ? resolve(target) : resolve(cwd, target);
  return (
    isWithinRepository(candidate, repositoryRoot) &&
    governedSourceExtensions.has(extname(candidate).toLowerCase())
  );
}

function repositoryMutationPath(value, cwd, repositoryRoot) {
  const target = cleanMutationTarget(value);
  if (!target || containsAny(target, ["$", "*", "?"])) {
    return "";
  }
  const candidate = isAbsolute(target) ? resolve(target) : resolve(cwd, target);
  if (!isWithinRepository(candidate, repositoryRoot)) {
    return "";
  }
  return relative(repositoryRoot, candidate) || ".";
}

function blockSourceMutation(kind, target, cwd, repositoryRoot) {
  const repositoryPath =
    repositoryMutationPath(target, cwd, repositoryRoot) ||
    cleanMutationTarget(target);
  return block(
    `BLOCKED: ${kind} would hand-write governed source at ${repositoryPath} without prospective structural-policy evaluation.\n\n` +
      "Use apply_patch, Edit, or Write so the complete resulting file is checked before mutation. For owned output, run the repository formatter or generator directly: `pnpm fix`, `pnpm fix:prose`, `pnpm fix:toml`, `pnpm db:types`, `pnpm supaschema:types`, or `pnpm ast-grep:update-snapshots`."
  );
}

function blockFirstGovernedTarget(kind, targets, cwd, repositoryRoot) {
  const target = targets.find((candidate) =>
    isGovernedRepositorySource(candidate, cwd, repositoryRoot)
  );
  return target
    ? blockSourceMutation(kind, target, cwd, repositoryRoot)
    : allowResult();
}

function positionalArguments(arguments_) {
  const values = [];
  let isAfterOptions = false;
  for (const argument of arguments_) {
    if (argument === "--") {
      isAfterOptions = true;
    } else if (isAfterOptions || !argument.startsWith("-")) {
      values.push(argument);
    }
  }
  return values;
}

function checkCopyOrMoveMutation(name, arguments_, cwd, repositoryRoot) {
  const targetDirectory = valueOption(
    arguments_,
    new Set(["--target-directory", "-t"])
  );
  const positionals = positionalArguments(arguments_);
  const target = targetDirectory || positionals.at(-1) || "";
  if (isGovernedRepositorySource(target, cwd, repositoryRoot)) {
    return blockSourceMutation(
      `${name} destination`,
      target,
      cwd,
      repositoryRoot
    );
  }
  if (
    repositoryMutationPath(target, cwd, repositoryRoot) &&
    positionals
      .slice(0, -1)
      .some((source) => isGovernedRepositorySource(source, cwd, repositoryRoot))
  ) {
    return blockSourceMutation(
      `${name} of governed source into a repository directory`,
      positionals.find((source) =>
        isGovernedRepositorySource(source, cwd, repositoryRoot)
      ) ?? target,
      cwd,
      repositoryRoot
    );
  }
  return allowResult();
}

function valueOption(arguments_, optionNames) {
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const [optionName, inlineValue] = argument.split("=", 2);
    if (!optionNames.has(optionName)) {
      continue;
    }
    return inlineValue ?? arguments_[index + 1] ?? "";
  }
  return "";
}

function genericOutputTargets(arguments_) {
  const targets = [];
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const [optionName, inlineValue] = argument.split("=", 2);
    if (!genericOutputValueOptions.has(optionName)) {
      continue;
    }
    targets.push(inlineValue ?? arguments_[index + 1] ?? "");
  }
  return targets;
}

function isPerlInPlaceArgument(argument) {
  if (!argument.startsWith("-") || argument.startsWith("--")) {
    return false;
  }
  return argument.includes("i");
}

function hasInlineInterpreterOption(name, arguments_) {
  if (name === "node") {
    return arguments_.some((argument) =>
      ["-e", "--eval", "-p", "--print"].includes(argument)
    );
  }
  if (["python", "python3", "ruby"].includes(name)) {
    return arguments_.includes("-c");
  }
  return name === "perl" && arguments_.includes("-e");
}

function isInlineInterpreterMutation(name, arguments_) {
  return (
    hasInlineInterpreterOption(name, arguments_) &&
    arguments_.some((argument) =>
      inlineInterpreterWriteFragments.some((fragment) =>
        argument.includes(fragment)
      )
    )
  );
}

function mentionsGovernedSourcePath(value) {
  const lower = value.toLowerCase();
  return [...governedSourceExtensions].some((extension) =>
    lower.includes(extension)
  );
}

function isReadOnlyGitMutationProbe(arguments_) {
  const subcommand = arguments_[0] ?? "";
  if (subcommand === "apply") {
    return arguments_.some((argument) =>
      ["--check", "--numstat", "--stat", "--summary"].includes(argument)
    );
  }
  return subcommand === "restore" && arguments_.includes("--staged");
}

function pnpmCommandArguments(arguments_) {
  let startIndex = 0;
  while (
    arguments_[startIndex]?.startsWith("-") &&
    !["--", "-C", "--dir"].includes(arguments_[startIndex])
  ) {
    startIndex += 1;
  }
  if (arguments_[startIndex] === "run") {
    startIndex += 1;
  }
  return arguments_.slice(startIndex);
}

function isApprovedOwnedMutator(tokens) {
  const name = commandName(tokens);
  const arguments_ = commandArguments(tokens);
  if (name === "pnpm") {
    const pnpmArguments = pnpmCommandArguments(arguments_);
    if (approvedPnpmMutationScripts.has(pnpmArguments[0] ?? "")) {
      return true;
    }
    if (pnpmArguments[0] === "exec") {
      return isApprovedDirectMutator(pnpmArguments.slice(1));
    }
  }
  if (
    name === "node" &&
    arguments_[0] === "supabase/scripts/database-types.mjs" &&
    !arguments_.includes("--check")
  ) {
    return true;
  }
  return isApprovedDirectMutator([name, ...arguments_]);
}

function isApprovedDirectMutator(arguments_) {
  const executable = executableName(arguments_[0] ?? "");
  const toolArguments = arguments_.slice(1);
  if (executable === "ultracite") {
    return toolArguments[0] === "fix";
  }
  if (executable === "prettier") {
    return toolArguments.includes("--write");
  }
  if (executable === "tombi") {
    return toolArguments[0] === "format" && !toolArguments.includes("--check");
  }
  if (executable === "ruff") {
    return toolArguments[0] === "format" && !toolArguments.includes("--check");
  }
  if (executable === "supaschema") {
    return toolArguments[0] === "types";
  }
  if (executable === "ast-grep") {
    return (
      toolArguments[0] === "test" &&
      toolArguments.some((argument) =>
        ["-U", "--update-all"].includes(argument)
      )
    );
  }
  return false;
}

function checkGhPrMerge(arguments_) {
  if (arguments_[0] !== "pr" || arguments_[1] !== "merge") {
    return allowResult();
  }
  const mergeArguments = arguments_.slice(2);
  if (
    mergeArguments.some(
      (argument) => argument === "--help" || argument === "-h"
    )
  ) {
    return allowResult();
  }
  const blocked = mergeArguments.find((argument) =>
    ["--merge", "--rebase", "--admin", "--disable-auto"].includes(argument)
  );
  if (blocked) {
    return block(
      `BLOCKED: gh pr merge ${blocked} is prohibited. Use \`gh pr merge <number> --squash --delete-branch\`.`
    );
  }
  if (!(
    mergeArguments.includes("--squash") &&
    mergeArguments.includes("--delete-branch")
  )) {
    return block(
      "BLOCKED: gh pr merge must use the repo policy method: `gh pr merge <number> --squash --delete-branch`."
    );
  }
  return allowResult();
}

function commandMutationClass(segment) {
  if (segment.operatorBefore === ">") {
    return "workspace_mutating";
  }
  const tokens = segment.words;
  if (isApprovedOwnedMutator(tokens)) {
    return "workspace_mutating";
  }
  const name = commandName(tokens);
  if (workspaceMutationCommandNames.has(name)) {
    return "workspace_mutating";
  }
  if (processMutationCommandNames.has(name)) {
    return "process_mutating";
  }
  if (name === "git") {
    const [subcommand] = parseGitInvocation(tokens).args;
    if (subcommand === "push") {
      return "externally_mutating";
    }
    return readOnlyGitSubcommands.has(subcommand)
      ? "read_only"
      : "workspace_mutating";
  }
  if (name === "gh") {
    const arguments_ = commandArguments(tokens);
    return arguments_.includes("--help") ? "read_only" : "externally_mutating";
  }
  if (isReadCommandName(name) || readOnlyCommandNames.has(name)) {
    return "read_only";
  }
  return "unknown";
}

function aggregateMutationClass(effects) {
  const unique = new Set(effects);
  if (unique.size === 1) {
    return effects[0];
  }
  if (unique.has("unknown")) {
    return "unknown";
  }
  const mutations = effects.filter((effect) => effect !== "read_only");
  const mutationClasses = new Set(mutations);
  return mutationClasses.size === 1 ? mutations[0] : "unknown";
}

export function describeBashCommand(input) {
  if (!isBashPayload(input)) {
    throw new TypeError("command descriptor requires a Bash tool payload");
  }
  const command = commandFromPayload(input);
  const segments = command.trim()
    ? commandSegmentObjects(stripHeredocs(command))
    : [];
  const lineage = Object.freeze(
    segments.map((segment, index) =>
      Object.freeze({
        commandName: commandName(segment.words),
        effect: commandMutationClass(segment),
        index,
        nestedCommandLimitReached: segment.nestedCommandLimitReached,
        operatorBefore: segment.operatorBefore,
      })
    )
  );
  const effects = lineage.map((segment) => segment.effect);
  const effect =
    effects.length === 0 ? "read_only" : aggregateMutationClass(effects);
  return Object.freeze({
    effect,
    external: effects.includes("externally_mutating"),
    lineage,
    lineageId:
      input.tool_use_id ??
      input.tool_input?.command_id ??
      input.session_id ??
      null,
    previewable: false,
    schemaVersion: "soleaux.command-descriptor/v1",
    selfValidating:
      segments.length > 0 &&
      segments.every((segment) => isApprovedOwnedMutator(segment.words)),
  });
}

function commandSegments(command) {
  return commandSegmentObjects(command).map((segment) => segment.words);
}

export function commandSegmentObjects(command) {
  return expandShellSegments(parseShellAst(command));
}

function expandShellSegments(ast, depth = 0) {
  const segments = [];
  for (const segment of ast.segments) {
    const nestedCommands = [
      nestedShellCommand(segment.words),
      nestedEnvironmentSplitCommand(segment.words),
    ].filter((command) => typeof command === "string" && command.length > 0);
    segments.push({
      ...segment,
      nestedCommandLimitReached:
        depth >= maximumNestedCommandDepth && nestedCommands.length > 0,
    });
    if (depth < maximumNestedCommandDepth) {
      for (const nestedCommand of nestedCommands) {
        segments.push(
          ...expandShellSegments(parseShellAst(nestedCommand), depth + 1)
        );
      }
    }
  }
  return segments;
}

function nestedShellCommand(tokens) {
  if (!["bash", "sh", "zsh"].includes(commandName(tokens))) {
    return "";
  }
  const arguments_ = commandArguments(tokens);
  for (let index = 0; index < arguments_.length - 1; index += 1) {
    if (shellCommandOption(arguments_[index] ?? "")) {
      const commandIndex = nestedShellCommandIndex(arguments_, index + 1);
      return arguments_[commandIndex] ?? "";
    }
  }
  return "";
}

function nestedEnvironmentSplitCommand(tokens) {
  let index = 0;
  while (index < tokens.length && environmentAssignment(tokens[index])) {
    index += 1;
  }
  if (executableName(tokens[index] ?? "") !== "env") {
    return "";
  }
  const arguments_ = tokens.slice(index + 1);
  for (
    let argumentIndex = 0;
    argumentIndex < arguments_.length;
    argumentIndex += 1
  ) {
    const argument = arguments_[argumentIndex] ?? "";
    if (argument === "-S" || argument === "--split-string") {
      return [
        arguments_[argumentIndex + 1],
        ...arguments_.slice(argumentIndex + 2),
      ]
        .filter(Boolean)
        .join(" ");
    }
    for (const prefix of ["-S", "--split-string="]) {
      if (argument.startsWith(prefix) && argument.length > prefix.length) {
        return [
          argument.slice(prefix.length),
          ...arguments_.slice(argumentIndex + 1),
        ].join(" ");
      }
    }
  }
  return "";
}

function shellCommandOption(value) {
  return value.startsWith("-") && value.slice(1).includes("c");
}

function nestedShellCommandIndex(arguments_, start) {
  let index = start;
  while (arguments_[index] === "--") {
    index += 1;
  }
  return index;
}

function parseShellAst(command) {
  const segments = [];
  let current = [];
  let nextOperator = "";
  for (const token of shellTokens(command)) {
    if (token.kind === "operator") {
      if (current.length > 0) {
        segments.push({ operatorBefore: nextOperator, words: current });
        current = [];
      }
      nextOperator = token.value;
      continue;
    }
    current.push(token.value);
  }
  if (current.length > 0) {
    segments.push({ operatorBefore: nextOperator, words: current });
  }
  return { segments };
}

function shellTokens(command) {
  const tokens = [];
  let token = "";
  let quote = "";
  let isEscaped = false;

  const pushToken = () => {
    if (token.length === 0) {
      return;
    }

    tokens.push({ kind: "word", value: token });
    token = "";
  };

  for (const char of command) {
    if (isEscaped) {
      token += char;
      isEscaped = false;
    } else if (char === "\\") {
      isEscaped = true;
    } else if (quote) {
      if (char === quote) {
        quote = "";
      } else {
        token += char;
      }
    } else if (shellQuoteCharacters.has(char)) {
      quote = char;
    } else if (isWhitespace(char)) {
      pushToken();
    } else if (shellOperatorCharacters.has(char)) {
      pushToken();
      tokens.push({ kind: "operator", value: char });
    } else {
      token += char;
    }
  }
  pushToken();
  return tokens;
}

const commandWrappers = new Map([
  ["env", new Set(["-C", "-S", "-u", "-P"])],
  ["exec", new Set(["-a"])],
  ["command", new Set()],
  [
    "sudo",
    new Set([
      "--chdir",
      "--close-from",
      "--group",
      "--host",
      "--prompt",
      "--role",
      "--type",
      "--user",
      "-C",
      "-D",
      "-g",
      "-h",
      "-p",
      "-R",
      "-r",
      "-T",
      "-t",
      "-u",
    ]),
  ],
]);

function commandStart(tokens) {
  let index = 0;
  while (index < tokens.length) {
    while (index < tokens.length && environmentAssignment(tokens[index])) {
      index += 1;
    }
    if (!commandWrappers.has(executableName(tokens[index] ?? ""))) {
      break;
    }
    index = skipCommandWrapper(tokens, index);
  }
  return index;
}

function skipCommandWrapper(tokens, start) {
  const valueOptions = commandWrappers.get(executableName(tokens[start] ?? ""));
  let index = start + 1;
  let isComplete = false;
  while (index < tokens.length && !isComplete) {
    const token = tokens[index];
    if (token === "--") {
      index += 1;
      isComplete = true;
    } else if (environmentAssignment(token)) {
      index += 1;
    } else if (
      typeof token === "string" &&
      token.startsWith("-") &&
      token !== "-"
    ) {
      index += 1;
      if (valueOptions.has(token) && index < tokens.length) {
        index += 1;
      }
    } else {
      isComplete = true;
    }
  }
  return index;
}

const gitGlobalValueOptions = new Set([
  "--config-env",
  "--exec-path",
  "--git-dir",
  "--namespace",
  "--super-prefix",
  "--work-tree",
  "-C",
  "-c",
]);

function parseGitInvocation(tokens) {
  const start = commandStart(tokens);
  const aliases = configuredGitAliases(tokens.slice(0, start));
  const arguments_ = tokens.slice(start + 1);
  let index = 0;
  let hasGlobalOptions = false;
  while (index < arguments_.length) {
    const argument = arguments_[index];
    if (
      typeof argument !== "string" ||
      !argument.startsWith("-") ||
      argument === "-"
    ) {
      break;
    }
    hasGlobalOptions = true;
    index += 1;
    if (
      (argument === "-c" || argument === "--config-env") &&
      index < arguments_.length
    ) {
      addGitAlias(aliases, arguments_[index] ?? "");
    } else if (argument.startsWith("--config-env=")) {
      addGitAlias(aliases, argument.slice("--config-env=".length));
    } else if (argument.startsWith("-c") && argument.length > 2) {
      addGitAlias(aliases, argument.slice(2));
    }
    if (gitGlobalValueOptions.has(argument) && index < arguments_.length) {
      index += 1;
    }
  }
  const gitCommandArguments = arguments_.slice(index);
  return {
    args: gitCommandArguments,
    hasGlobalOptions,
    invokesAlias: aliases.has((gitCommandArguments[0] ?? "").toLowerCase()),
  };
}

function configuredGitAliases(tokens) {
  const aliases = new Set();
  for (const token of tokens) {
    const assignment = environmentAssignment(token);
    if (assignment?.name.startsWith("GIT_CONFIG_KEY_")) {
      addGitAlias(aliases, assignment.value);
    }
  }
  return aliases;
}

function addGitAlias(aliases, config) {
  const alias = gitAliasName(config);
  if (alias) {
    aliases.add(alias);
  }
}

function gitAliasName(config) {
  const equals = config.indexOf("=");
  const name = (equals === -1 ? config : config.slice(0, equals)).toLowerCase();
  if (!name.startsWith("alias.")) {
    return "";
  }
  const alias = name.slice("alias.".length);
  return alias.endsWith(".command")
    ? alias.slice(0, -".command".length)
    : alias;
}

function configWritesAlias(arguments_) {
  const aliasIndex = arguments_.findIndex((argument) => gitAliasName(argument));
  return aliasIndex !== -1 && aliasIndex < arguments_.length - 1;
}

function isDynamicWorktreeInvocation(segments, index) {
  const segment = segments[index];
  const tokens = segment.words;
  const start = commandStart(tokens);
  const rawName = tokens[start] ?? "";
  const dynamicInvocation = parseGitInvocation([
    "git",
    ...tokens.slice(start + 1),
  ]);
  if (
    isDynamicExecutable(rawName) &&
    dynamicInvocation.args[0] === "worktree"
  ) {
    return true;
  }
  const substitutionTail = parseGitInvocation(["git", ...tokens]);
  if (
    segment.operatorBefore !== ")" ||
    substitutionTail.args[0] !== "worktree"
  ) {
    return false;
  }
  for (let openIndex = index - 1; openIndex > 0; openIndex -= 1) {
    if (segments[openIndex].operatorBefore !== "(") {
      continue;
    }
    const owner = segments[openIndex - 1].words;
    const ownerStart = commandStart(owner);
    return owner[ownerStart] === "$" && owner.length === ownerStart + 1;
  }
  return false;
}

function isDynamicExecutable(value) {
  return value.startsWith("$") || [...value].some(isWhitespace);
}

function executableName(value) {
  const separator = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"));
  const basename = value.slice(separator + 1);
  return basename.endsWith(".exe")
    ? basename.slice(0, -".exe".length)
    : basename;
}

export function commandName(tokens) {
  return executableName(tokens[commandStart(tokens)] ?? "");
}

export function commandArguments(tokens) {
  return tokens.slice(commandStart(tokens) + 1);
}

function environmentAssignment(token) {
  const equals = token.indexOf("=");
  let assignment;
  if (equals > 0) {
    const name = token.slice(0, equals);
    if (isIdentifierName(name)) {
      assignment = { name, value: token.slice(equals + 1) };
    }
  }
  return assignment;
}

function secretName(name) {
  const upper = name.toUpperCase();
  return secretNameFragments.some((fragment) => upper.includes(fragment));
}

function isLiteralSecretValue(value) {
  return (
    value.length >= minimumLiteralSecretLength &&
    !looksMasked(value) &&
    value[0] !== "$"
  );
}

function secretFlagValue(tokens, index) {
  const token = tokens[index] ?? "";
  const equals = token.indexOf("=");
  let hit;
  if (equals > 0) {
    const name = token.slice(0, equals);
    if (secretFlagNames.has(name)) {
      hit = { name, preview: token, value: token.slice(equals + 1) };
    }
  } else if (
    secretFlagNames.has(token) &&
    typeof tokens[index + 1] === "string"
  ) {
    hit = {
      name: token,
      preview: `${token} ${tokens[index + 1]}`,
      value: tokens[index + 1],
    };
  }
  return hit;
}

function environmentFileName(token) {
  const cleaned = trimShellPunctuation(token);
  const fileName = cleaned.split("/").pop() ?? "";
  if (
    fileName === ".envrc" ||
    fileName === ".env" ||
    fileName.startsWith(".env.")
  ) {
    return fileName;
  }
  return "";
}

function isRawSqlCliSegment(tokens) {
  const start = commandStart(tokens);
  const command = tokens[start] ?? "";
  if (command === "psql") {
    return true;
  }
  return (
    command === "supabase" &&
    tokens[start + 1] === "db" &&
    ["execute", "query"].includes(tokens[start + 2] ?? "")
  );
}

function rawSqlPayload(tokens) {
  const start = commandStart(tokens);
  const command = tokens[start] ?? "";
  for (let index = start + 1; index < tokens.length; index += 1) {
    const token = tokens[index] ?? "";
    if (
      (command === "psql" && ["-c", "--command"].includes(token)) ||
      token === "--sql"
    ) {
      return tokens[index + 1] ?? "";
    }
    const sqlFlag = "--sql=";
    if (token.startsWith(sqlFlag)) {
      return token.slice(sqlFlag.length);
    }
  }
  return tokens.slice(start + 1).join(" ");
}

function rmArgumentsIncludeRecursiveForce(arguments_) {
  let isRecursive = false;
  let isForce = false;
  for (const argument of arguments_) {
    if (argument === "--") {
      break;
    }
    if (argument.startsWith("-") && argument !== "-") {
      if (argument === "--recursive") {
        isRecursive = true;
      } else if (argument === "--force") {
        isForce = true;
      } else if (!argument.startsWith("--")) {
        isRecursive ||= containsAny(argument, ["r", "R"]);
        isForce ||= argument.includes("f");
      }
      if (isRecursive && isForce) {
        return true;
      }
    }
  }
  return false;
}

function isPushToMain(arguments_) {
  return arguments_.some((argument) => {
    const refspec = argument.startsWith("+") ? argument.slice(1) : argument;
    return (
      refspec === "main" ||
      refspec === "refs/heads/main" ||
      refspec.endsWith(":main") ||
      refspec.endsWith(":refs/heads/main")
    );
  });
}

function isProhibitedPush(arguments_) {
  return (
    arguments_.some(isProhibitedPushArgument) ||
    pushRefspecs(arguments_).some((refspec) => refspec.startsWith("+"))
  );
}

function isProhibitedPushArgument(argument) {
  return (
    prohibitedPushArguments.has(argument) ||
    hasShortForceFlag(argument) ||
    argument.startsWith("--force-with-lease")
  );
}

function hasShortForceFlag(argument) {
  if (!argument.startsWith("-") || argument.startsWith("--")) {
    return false;
  }
  const [flags] = argument.slice(1).split("o", 1);
  return flags.includes("f");
}

function isImplicitPush(arguments_) {
  if (arguments_.some((argument) => implicitPushExemptions.has(argument))) {
    return false;
  }
  return pushRefspecs(arguments_).length === 0;
}

function pushRefspecs(arguments_) {
  const valueOptions = new Set([
    "--exec",
    "--push-option",
    "--receive-pack",
    "--repo",
    "-o",
  ]);
  const positionals = [];
  let isRepoProvidedByOption = false;
  for (let index = 1; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const [optionName] = argument.split("=", 1);
    if (optionName === "--recurse-submodules") {
      if (
        !argument.includes("=") &&
        ["check", "on-demand", "no"].includes(arguments_[index + 1] ?? "")
      ) {
        index += 1;
      }
    } else if (valueOptions.has(optionName)) {
      isRepoProvidedByOption ||= optionName === "--repo";
      if (!argument.includes("=")) {
        index += 1;
      }
    } else if (!argument.startsWith("-")) {
      positionals.push(argument);
    }
  }
  return isRepoProvidedByOption ? positionals : positionals.slice(1);
}

function isDiagnosticPush(ast, gitPushTokens, arguments_) {
  if (
    arguments_.some((argument) => argument === "--dry-run" || argument === "-n")
  ) {
    return false;
  }
  const index = ast.segments.findIndex(
    (segment) => segment.words === gitPushTokens
  );
  if (index === -1) {
    return false;
  }
  const next = ast.segments[index + 1];
  return (
    next?.operatorBefore === "|" &&
    ["awk", "grep", "head", "sed", "tail", "wc"].includes(
      commandName(next.words)
    )
  );
}

function stripHeredocs(command) {
  const lines = command.split("\n");
  const out = [];
  let marker = "";
  for (const line of lines) {
    if (marker) {
      if (line.trim() === marker) {
        out.push("HEREDOC");
        marker = "";
      }
    } else {
      const heredoc = heredocMarker(line);
      if (heredoc) {
        marker = heredoc;
        out.push("<<HEREDOC", "[heredoc body stripped]");
      } else {
        out.push(line);
      }
    }
  }
  return out.join("\n");
}

function looksMasked(value) {
  const lower = value.toLowerCase();
  return maskedSecretFragments.some((fragment) => lower.includes(fragment));
}

function describeHit(kind, match) {
  const preview =
    match.length > maximumSecretPreviewLength
      ? `${match.slice(0, secretPreviewPrefixLength)}...${match.slice(
          -secretPreviewSuffixLength
        )}`
      : match;
  return `${kind}: ${preview}`;
}

function isWhitespace(char) {
  return whitespaceCharacters.has(char);
}

function hasDatabaseUrlWithInlinePassword(value) {
  const schemeSeparator = "://";
  const schemeEnd = value.indexOf(schemeSeparator);
  if (schemeEnd <= 0) {
    return false;
  }
  const scheme = value.slice(0, schemeEnd).toLowerCase();
  if (!["mysql", "postgres", "postgresql"].includes(scheme)) {
    return false;
  }
  const authorityStart = schemeEnd + schemeSeparator.length;
  const authorityEnd = firstIndexOfAny(value, ["/", "?", "#"], authorityStart);
  const authority = value.slice(
    authorityStart,
    authorityEnd === -1 ? value.length : authorityEnd
  );
  const at = authority.lastIndexOf("@");
  if (at <= 0) {
    return false;
  }
  const userinfo = authority.slice(0, at);
  const colon = userinfo.indexOf(":");
  if (colon <= 0) {
    return false;
  }
  const password = userinfo.slice(colon + 1);
  return (
    password.length >= minimumDatabaseUrlPasswordLength &&
    !looksMasked(password)
  );
}

function sqlDdlKeyword(sql) {
  const tokens = sqlTokens(sql);
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (singleTokenDdlKeywords.has(token)) {
      return token;
    }
    const next = tokens[index + 1] ?? "";
    const third = tokens[index + 2] ?? "";
    const fourth = tokens[index + fourthSqlTokenOffset] ?? "";
    if (
      token === "ENABLE" &&
      next === "ROW" &&
      third === "LEVEL" &&
      fourth === "SECURITY"
    ) {
      return "ENABLE ROW LEVEL SECURITY";
    }
    if (token === "CREATE" && createOrDropKinds(tokens, index + 1)) {
      return `CREATE ${createOrDropKinds(tokens, index + 1)}`;
    }
    if (token === "ALTER" && alterKinds(tokens, index + 1)) {
      return `ALTER ${alterKinds(tokens, index + 1)}`;
    }
    if (token === "DROP" && createOrDropKinds(tokens, index + 1)) {
      return `DROP ${createOrDropKinds(tokens, index + 1)}`;
    }
  }
  return "";
}

function createOrDropKinds(tokens, index) {
  const first = tokens[index] ?? "";
  const second = tokens[index + 1] ?? "";
  if (first === "MATERIALIZED" && second === "VIEW") {
    return "MATERIALIZED VIEW";
  }
  return [
    "EXTENSION",
    "FUNCTION",
    "INDEX",
    "POLICY",
    "ROLE",
    "SCHEMA",
    "SEQUENCE",
    "TABLE",
    "TRIGGER",
    "TYPE",
    "VIEW",
  ].includes(first)
    ? first
    : "";
}

function alterKinds(tokens, index) {
  const first = tokens[index] ?? "";
  return [
    "FUNCTION",
    "POLICY",
    "ROLE",
    "SCHEMA",
    "SEQUENCE",
    "TABLE",
    "TYPE",
  ].includes(first)
    ? first
    : "";
}

function sqlTokens(sql) {
  const tokens = [];
  let token = "";
  let quote = "";
  const push = () => {
    if (!token) {
      return;
    }

    tokens.push(token.toUpperCase());
    token = "";
  };

  for (const char of sql) {
    if (quote) {
      if (char === quote) {
        quote = "";
      }
    } else if (char === "'" || char === '"') {
      push();
      quote = char;
    } else if (isIdentifierChar(char)) {
      token += char;
    } else {
      push();
    }
  }
  push();
  return tokens;
}

function skipSqlLineComment(sql, start) {
  let index = start + 2;
  while (index < sql.length && !["\n", "\r"].includes(sql[index] ?? "")) {
    index += 1;
  }
  return index;
}

function skipSqlBlockComment(sql, start) {
  let index = start + 2;
  while (index < sql.length && !isSqlBlockCommentEnd(sql, index)) {
    index += 1;
  }
  return index + 2;
}

function isSqlBlockCommentEnd(sql, index) {
  return (sql[index] ?? "") === "*" && (sql[index + 1] ?? "") === "/";
}

function stripSqlComments(sql) {
  let out = "";
  let index = 0;
  while (index < sql.length) {
    const char = sql[index] ?? "";
    const next = sql[index + 1] ?? "";
    if (char === "-" && next === "-") {
      index = skipSqlLineComment(sql, index);
      out += " ";
    } else if (char === "/" && next === "*") {
      index = skipSqlBlockComment(sql, index);
      out += " ";
    } else {
      out += char;
      index += 1;
    }
  }
  return out;
}

function heredocMarker(line) {
  const markerStart = line.indexOf("<<");
  if (markerStart === -1) {
    return "";
  }
  let index = markerStart + 2;
  if (line[index] === "-") {
    index += 1;
  }
  while (index < line.length && isWhitespace(line[index] ?? "")) {
    index += 1;
  }
  const quote = line[index] === "'" || line[index] === '"' ? line[index] : "";
  if (quote) {
    index += 1;
  }
  let marker = "";
  while (index < line.length) {
    const char = line[index] ?? "";
    if (isHeredocMarkerEnd(char, quote)) {
      break;
    }
    marker += char;
    index += 1;
  }
  return marker;
}

function isHeredocMarkerEnd(char, quote) {
  if (quote) {
    return char === quote;
  }
  return isWhitespace(char) || heredocUnquotedTerminators.has(char);
}

function trimShellPunctuation(value) {
  let start = 0;
  let end = value.length;
  const leading = new Set(["<", ">", '"', "'"]);
  const trailing = new Set(["<", ">", '"', "'", ",", ":", ";", "|", ")"]);
  while (start < end && leading.has(value[start] ?? "")) {
    start += 1;
  }
  while (end > start && trailing.has(value[end - 1] ?? "")) {
    end -= 1;
  }
  return value.slice(start, end);
}

function isIdentifierName(value) {
  if (!value) {
    return false;
  }
  if (!(isAsciiLetter(value[0] ?? "") || value[0] === "_")) {
    return false;
  }
  for (const char of value.slice(1)) {
    if (!(isAsciiLetter(char) || isDigit(char) || char === "_")) {
      return false;
    }
  }
  return true;
}

function isIdentifierChar(char) {
  return isAsciiLetter(char) || isDigit(char) || char === "_";
}

function isAsciiLetter(char) {
  return (char >= "A" && char <= "Z") || (char >= "a" && char <= "z");
}

function isDigit(char) {
  return char >= "0" && char <= "9";
}

function containsAny(value, candidates) {
  return candidates.some((candidate) => value.includes(candidate));
}

function firstIndexOfAny(value, chars, start) {
  let found = -1;
  for (const char of chars) {
    const index = value.indexOf(char, start);
    if (index !== -1 && (found === -1 || index < found)) {
      found = index;
    }
  }
  return found;
}

// The Claude runtime entrypoint imports this Claude-owned policy; this module is CLI-free.
