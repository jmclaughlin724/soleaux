/* eslint-disable no-magic-numbers -- CLI token indexes and process status values are protocol mechanics. */
import { spawnSync } from "node:child_process";
import { statSync } from "node:fs";
import {
  basename,
  extname,
  isAbsolute,
  relative,
  resolve,
  sep,
} from "node:path";

import {
  commandArguments,
  commandName,
  executableName,
  parseGitInvocation,
} from "./bash-ast.mjs";

const GOVERNED_SOURCE_EXTENSIONS = new Set([
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
const APPROVED_PNPM_MUTATION_SCRIPTS = new Set([
  "ast-grep:update-snapshots",
  "db:types",
  "fix",
  "fix:prose",
  "fix:toml",
  "supaschema:types",
]);
const INLINE_INTERPRETER_WRITE_FRAGMENTS = [
  "appendFile",
  "createWriteStream",
  "open(",
  "writeFile",
  "write_bytes",
  "write_text",
  "writeTextFile",
];
const OUTPUT_VALUE_OPTIONS = new Set([
  "--out",
  "--outfile",
  "--output",
  "--output-file",
  "--write",
  "-o",
]);
const PNPM_GLOBAL_VALUE_OPTIONS = new Set([
  "--dir",
  "--filter",
  "--global-dir",
  "--globalconfig",
  "--prefix",
  "--store-dir",
  "--virtual-store-dir",
  "-C",
]);

function repositoryRoot(cwd) {
  const result = spawnSync("git", ["-C", cwd, "rev-parse", "--show-toplevel"], {
    encoding: "utf-8",
    timeout: 5000,
  });
  if (result.error || result.status !== 0 || result.stdout.trim() === "") {
    throw new Error("cwd must be inside a Git repository");
  }
  return resolve(result.stdout.trim());
}

function withinRepository(candidatePath, root) {
  const repositoryPath = relative(root, candidatePath);
  return (
    repositoryPath === "" ||
    (repositoryPath !== ".." &&
      !repositoryPath.startsWith(`..${sep}`) &&
      !isAbsolute(repositoryPath))
  );
}

function cleanMutationTarget(value) {
  const separator = value.indexOf("=");
  return (separator === -1 ? value : value.slice(separator + 1)).trim();
}

function resolvedTarget(value, cwd) {
  const target = cleanMutationTarget(value);
  if (!target || ["$", "*", "?"].some((mark) => target.includes(mark))) {
    return "";
  }
  return isAbsolute(target) ? resolve(target) : resolve(cwd, target);
}

function governedRepositorySource(value, cwd, root) {
  const target = resolvedTarget(value, cwd);
  return (
    target !== "" &&
    withinRepository(target, root) &&
    GOVERNED_SOURCE_EXTENSIONS.has(extname(target).toLowerCase())
  );
}

function governedSourcePath(value, cwd) {
  const target = resolvedTarget(value, cwd);
  return (
    target !== "" &&
    GOVERNED_SOURCE_EXTENSIONS.has(extname(target).toLowerCase())
  );
}

function repositoryMutationPath(value, cwd, root) {
  const target = resolvedTarget(value, cwd);
  if (!target || !withinRepository(target, root)) {
    return "";
  }
  return relative(root, target) || ".";
}

function repositoryDirectoryTarget(value, cwd, root) {
  const target = resolvedTarget(value, cwd);
  if (!target || !withinRepository(target, root)) {
    return false;
  }
  return (
    value.endsWith("/") ||
    Boolean(statSync(target, { throwIfNoEntry: false })?.isDirectory())
  );
}

function hasRecursiveFlag(arguments_) {
  return arguments_.some(
    (argument) =>
      argument === "--recursive" ||
      (argument.startsWith("-") &&
        !argument.startsWith("--") &&
        ["r", "R"].some((flag) => argument.slice(1).includes(flag)))
  );
}

function blockSourceMutation(kind, target, cwd, root) {
  const repositoryPath =
    repositoryMutationPath(target, cwd, root) || cleanMutationTarget(target);
  return [
    `BLOCKED: ${kind} would hand-write governed source at ${repositoryPath} without prospective structural-policy evaluation.`,
    "Corrective action: Use apply_patch, Edit, or Write for the named repository path so the complete resulting file is checked before mutation; for generated output, edit its canonical input and run the generator declared by its owner.",
  ].join("\n");
}

function blockFirstGovernedTarget(kind, targets, cwd, root) {
  const target = targets.find((candidate) =>
    governedRepositorySource(candidate, cwd, root)
  );
  return target ? blockSourceMutation(kind, target, cwd, root) : null;
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

function valueOption(arguments_, optionNames) {
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const separator = argument.indexOf("=");
    const option = separator === -1 ? argument : argument.slice(0, separator);
    if (!optionNames.has(option)) {
      continue;
    }
    return separator === -1
      ? (arguments_[index + 1] ?? "")
      : argument.slice(separator + 1);
  }
  return "";
}

function outputTargets(arguments_) {
  const targets = [];
  for (let index = 0; index < arguments_.length; index += 1) {
    const argument = arguments_[index] ?? "";
    const separator = argument.indexOf("=");
    const option = separator === -1 ? argument : argument.slice(0, separator);
    if (!OUTPUT_VALUE_OPTIONS.has(option)) {
      continue;
    }
    targets.push(
      separator === -1
        ? (arguments_[index + 1] ?? "")
        : argument.slice(separator + 1)
    );
  }
  return targets;
}

function checkCopyOrMove(name, arguments_, cwd, root) {
  const targetDirectory = valueOption(
    arguments_,
    new Set(["--target-directory", "-t"])
  );
  const positionals = positionalArguments(arguments_);
  const target = targetDirectory || positionals.at(-1) || "";
  if (governedRepositorySource(target, cwd, root)) {
    return blockSourceMutation(`${name} destination`, target, cwd, root);
  }
  const sources = targetDirectory ? positionals : positionals.slice(0, -1);
  const directorySource = sources.find((source) => {
    const sourcePath = resolvedTarget(source, cwd);
    return (
      sourcePath !== "" &&
      Boolean(statSync(sourcePath, { throwIfNoEntry: false })?.isDirectory())
    );
  });
  const recursiveCopy = name === "cp" && hasRecursiveFlag(arguments_);
  const isDirectoryMove = name === "mv" && Boolean(directorySource);
  if (
    repositoryMutationPath(target, cwd, root) &&
    (recursiveCopy || isDirectoryMove)
  ) {
    return blockSourceMutation(`${name} directory mutation`, target, cwd, root);
  }
  const governedSource = arguments_.find(
    (argument) =>
      !argument.startsWith("-") &&
      argument !== targetDirectory &&
      governedSourcePath(argument, cwd)
  );
  if (
    !governedSource ||
    !repositoryMutationPath(target, cwd, root) ||
    !(targetDirectory || repositoryDirectoryTarget(target, cwd, root))
  ) {
    return null;
  }
  const projectedTarget = resolve(
    resolvedTarget(target, cwd),
    basename(resolvedTarget(governedSource, cwd))
  );
  return blockSourceMutation(
    `${name} of governed source into a repository directory`,
    projectedTarget,
    cwd,
    root
  );
}

function isPerlInPlaceArgument(argument) {
  return (
    argument.startsWith("-") &&
    !argument.startsWith("--") &&
    argument.includes("i")
  );
}

function hasInlineInterpreterOption(name, arguments_) {
  if (name === "node") {
    return arguments_.some((argument) =>
      ["--eval", "--print", "-e", "-p"].includes(argument)
    );
  }
  if (["python", "python3", "ruby"].includes(name)) {
    return arguments_.includes("-c");
  }
  return name === "perl" && arguments_.includes("-e");
}

function inlineInterpreterMutation(name, arguments_) {
  return (
    hasInlineInterpreterOption(name, arguments_) &&
    arguments_.some((argument) =>
      INLINE_INTERPRETER_WRITE_FRAGMENTS.some((fragment) =>
        argument.includes(fragment)
      )
    )
  );
}

function mentionsGovernedSourcePath(value) {
  const lower = value.toLowerCase();
  return [...GOVERNED_SOURCE_EXTENSIONS].some((extension) =>
    lower.includes(extension)
  );
}

function readOnlyGitMutationProbe(arguments_) {
  if (arguments_[0] === "apply") {
    return arguments_.some((argument) =>
      ["--check", "--numstat", "--stat", "--summary"].includes(argument)
    );
  }
  if (arguments_[0] !== "restore") {
    return false;
  }
  const restoreArguments = arguments_.slice(1);
  const staged = restoreArguments.some(
    (argument) =>
      argument === "--staged" ||
      (argument.startsWith("-") &&
        !argument.startsWith("--") &&
        argument.slice(1).includes("S"))
  );
  const worktree = restoreArguments.some(
    (argument) =>
      argument === "--worktree" ||
      (argument.startsWith("-") &&
        !argument.startsWith("--") &&
        argument.slice(1).includes("W"))
  );
  return staged && !worktree;
}

function pnpmCommandArguments(arguments_) {
  let index = 0;
  while (index < arguments_.length) {
    const argument = arguments_[index] ?? "";
    if (argument === "--") {
      return arguments_.slice(index + 1);
    }
    if (!argument.startsWith("-")) {
      break;
    }
    const option = argument.split("=", 1)[0] ?? "";
    index += 1;
    if (PNPM_GLOBAL_VALUE_OPTIONS.has(option) && !argument.includes("=")) {
      index += 1;
    }
  }
  if (arguments_[index] === "run") {
    index += 1;
  }
  return arguments_.slice(index);
}

function approvedDirectMutator(arguments_) {
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
  return (
    executable === "ast-grep" &&
    toolArguments[0] === "test" &&
    toolArguments.some((argument) => ["--update-all", "-U"].includes(argument))
  );
}

function approvedOwnedMutator(words) {
  const name = commandName(words);
  const arguments_ = commandArguments(words);
  if (name === "pnpm") {
    const pnpmArguments = pnpmCommandArguments(arguments_);
    if (APPROVED_PNPM_MUTATION_SCRIPTS.has(pnpmArguments[0] ?? "")) {
      return true;
    }
    if (pnpmArguments[0] === "exec") {
      return approvedDirectMutator(pnpmArguments.slice(1));
    }
  }
  if (
    name === "node" &&
    arguments_[0] === "supabase/scripts/database-types.mjs" &&
    !arguments_.includes("--check")
  ) {
    return true;
  }
  return approvedDirectMutator([name, ...arguments_]);
}

function hasForceFlag(arguments_) {
  return arguments_.some(
    (argument) =>
      argument === "--force" ||
      (argument.startsWith("-") &&
        !argument.startsWith("--") &&
        argument.slice(1).includes("f"))
  );
}

function indexOnlyUntrackShape(arguments_, cwd, root) {
  const rest = arguments_.slice(1);
  if (!rest.includes("--cached")) {
    return false;
  }
  if (hasForceFlag(rest) || hasRecursiveFlag(rest)) {
    return false;
  }
  const targets = positionalArguments(rest);
  return (
    targets.length > 0 &&
    targets.every((target) => {
      const repositoryPath = repositoryMutationPath(target, cwd, root);
      return repositoryPath !== "" && repositoryPath !== ".";
    })
  );
}

function checkGitSourceMutation(words, cwd, root) {
  const gitArguments = parseGitInvocation(words).args;
  if (
    gitArguments[0] === "rm" &&
    gitArguments.every((argument) => !["--dry-run", "-n"].includes(argument)) &&
    !indexOnlyUntrackShape(gitArguments, cwd, root)
  ) {
    return [
      "BLOCKED: git rm changes tracked repository ownership outside prospective structural-policy evaluation.",
      "Corrective action: Use apply_patch, Edit, or Write for the exact source deletion, then stage only the reviewed path; to stop tracking a file while keeping it locally, use `git rm --cached -- <exact repository path>`.",
    ].join("\n");
  }
  if (gitArguments[0] === "mv") {
    return [
      "BLOCKED: git mv changes tracked paths outside prospective structural-policy evaluation.",
      "Corrective action: Use apply_patch, Edit, or Write for the rename and update every owner and consumer in the same reviewed change.",
    ].join("\n");
  }
  if (
    ["am", "apply", "cherry-pick", "merge", "rebase", "restore"].includes(
      gitArguments[0] ?? ""
    ) &&
    !readOnlyGitMutationProbe(gitArguments)
  ) {
    return [
      "BLOCKED: this Git worktree mutation bypasses prospective structural-policy evaluation and may overwrite protected work.",
      "Corrective action: Use apply_patch, Edit, or Write for source changes; use `git diff` or `git show` for read-only inspection.",
    ].join("\n");
  }
  return null;
}

function checkInvocation(words, cwd, root) {
  if (approvedOwnedMutator(words)) {
    return null;
  }
  const name = commandName(words);
  const arguments_ = commandArguments(words);
  if (name === "tee") {
    return blockFirstGovernedTarget(
      "tee output",
      positionalArguments(arguments_),
      cwd,
      root
    );
  }
  if (["touch", "truncate"].includes(name)) {
    return blockFirstGovernedTarget(
      `${name} source mutation`,
      positionalArguments(arguments_),
      cwd,
      root
    );
  }
  if (["cp", "install", "mv"].includes(name)) {
    return checkCopyOrMove(name, arguments_, cwd, root);
  }
  if (name === "dd") {
    return blockFirstGovernedTarget(
      "dd output",
      arguments_
        .filter((argument) => argument.startsWith("of="))
        .map((argument) => argument.slice("of=".length)),
      cwd,
      root
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
      root
    );
  }
  if (name === "perl" && arguments_.some(isPerlInPlaceArgument)) {
    return blockFirstGovernedTarget(
      "in-place Perl rewrite",
      positionalArguments(arguments_),
      cwd,
      root
    );
  }
  if (name === "patch" && !arguments_.includes("--dry-run")) {
    return [
      "BLOCKED: the Bash patch command bypasses prospective structural-policy evaluation.",
      "Corrective action: Use apply_patch, Edit, or Write so the complete resulting source is checked before mutation.",
    ].join("\n");
  }
  if (name === "git") {
    return checkGitSourceMutation(words, cwd, root);
  }
  const outputResult = blockFirstGovernedTarget(
    `${name || "unknown command"} output option`,
    outputTargets(arguments_),
    cwd,
    root
  );
  if (outputResult) {
    return outputResult;
  }
  if (inlineInterpreterMutation(name, arguments_)) {
    const sourceMention = arguments_.find(mentionsGovernedSourcePath);
    if (sourceMention) {
      return [
        `BLOCKED: ${name} inline code contains a file-write primitive and a governed source path.`,
        "Corrective action: Use apply_patch, Edit, or Write for the named source path so the complete resulting source is checked before mutation.",
      ].join("\n");
    }
  }
  return null;
}

function gitInvocationMayMutateSource(words) {
  const [subcommand, ...arguments_] = parseGitInvocation(words).args;
  if (["rm", "mv"].includes(subcommand)) {
    return true;
  }
  if (
    !["am", "apply", "cherry-pick", "merge", "rebase", "restore"].includes(
      subcommand
    )
  ) {
    return false;
  }
  return !readOnlyGitMutationProbe([subcommand, ...arguments_]);
}

function invocationMayMutateSource(words, cwd) {
  if (approvedOwnedMutator(words)) {
    return false;
  }
  const name = commandName(words);
  if (
    ["cp", "dd", "install", "mv", "patch", "tee", "touch", "truncate"].includes(
      name
    )
  ) {
    return true;
  }
  const arguments_ = commandArguments(words);
  if (
    name === "sed" &&
    arguments_.some(
      (argument) =>
        argument === "-i" ||
        argument.startsWith("-i.") ||
        argument.startsWith("--in-place")
    )
  ) {
    return true;
  }
  if (name === "perl" && arguments_.some(isPerlInPlaceArgument)) {
    return true;
  }
  if (name === "git" && gitInvocationMayMutateSource(words)) {
    return true;
  }
  if (
    outputTargets(arguments_).some((target) => governedSourcePath(target, cwd))
  ) {
    return true;
  }
  return (
    inlineInterpreterMutation(name, arguments_) &&
    arguments_.some(mentionsGovernedSourcePath)
  );
}

export function evaluateSourceMutationBoundaryGuard({ bash, cwd: inputCwd }) {
  const cwd = resolve(inputCwd);
  const hasRedirectCandidate = bash.redirects.some(({ target }) =>
    governedSourcePath(target, cwd)
  );
  if (
    !hasRedirectCandidate &&
    bash.invocations.every(
      ({ words }) => !invocationMayMutateSource(words, cwd)
    )
  ) {
    return null;
  }
  const root = repositoryRoot(cwd);
  const redirectedTarget = bash.redirects
    .map(({ target }) => target)
    .find((target) => governedRepositorySource(target, cwd, root));
  if (redirectedTarget) {
    return blockSourceMutation(
      "shell output redirection",
      redirectedTarget,
      cwd,
      root
    );
  }
  for (const { words } of bash.invocations) {
    const result = checkInvocation(words, cwd, root);
    if (result) {
      return result;
    }
  }
  return null;
}
