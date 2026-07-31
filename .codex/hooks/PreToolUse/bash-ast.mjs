/* eslint-disable no-magic-numbers -- Bash AST byte offsets and CLI token positions are protocol mechanics. */
import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { join, resolve } from "node:path";

const MAXIMUM_NESTED_SHELL_DEPTH = 3;
const AST_GREP_MAXIMUM_OUTPUT_BYTES = 2_097_152;
const AST_GREP_TIMEOUT_MILLISECONDS = 5000;
const repositoryRoot = realpathSync(
  resolve(import.meta.dirname, "..", "..", "..")
);
const astGrepPath = join(repositoryRoot, "node_modules", ".bin", "ast-grep");
const shellRuleSource = [
  "id: bash-command",
  "language: Bash",
  "rule:",
  "  pattern: $COMMAND $$$ARGUMENTS",
  "---",
  "id: bash-command-name",
  "language: Bash",
  "rule:",
  "  kind: command",
  "---",
  "id: bash-output-redirect",
  "language: Bash",
  "rule:",
  "  any:",
  "    - pattern: $SOURCE > $TARGET",
  "    - pattern: $SOURCE >> $TARGET",
  "---",
  "id: bash-heredoc",
  "language: Bash",
  "rule:",
  "  kind: heredoc_redirect",
  "---",
  "id: bash-parse-error",
  "language: Bash",
  "rule:",
  "  kind: ERROR",
].join("\n");
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

export class BashAstError extends Error {
  constructor(safeCause, options) {
    super(safeCause, options);
    this.code = options.code;
    this.name = "BashAstError";
    this.safeCause = safeCause;
  }
}

function astGrepProcessFailure(result) {
  const errorCode =
    result.error && typeof result.error.code === "string"
      ? result.error.code
      : "";
  if (errorCode === "ETIMEDOUT") {
    return new BashAstError(
      `ast-grep exceeded its ${AST_GREP_TIMEOUT_MILLISECONDS}ms parser deadline`,
      { code: "AST_GREP_TIMEOUT" }
    );
  }
  if (errorCode === "ENOENT") {
    return new BashAstError(
      "the repository-local ast-grep executable was not found",
      { code: "AST_GREP_NOT_FOUND" }
    );
  }
  if (errorCode === "ENOBUFS") {
    return new BashAstError(
      `ast-grep exceeded the ${AST_GREP_MAXIMUM_OUTPUT_BYTES}-byte output limit`,
      { code: "AST_GREP_OUTPUT_LIMIT" }
    );
  }
  const status = Number.isSafeInteger(result.status)
    ? String(result.status)
    : "unknown";
  const signal =
    typeof result.signal === "string" && result.signal.length > 0
      ? ` signal=${result.signal}`
      : "";
  return new BashAstError(
    `ast-grep exited unsuccessfully with status=${status}${signal}`,
    { code: "AST_GREP_EXIT_NONZERO" }
  );
}

function requireAstGrepResult(command, runAstGrep) {
  const result = runAstGrep(
    astGrepPath,
    ["scan", "--inline-rules", shellRuleSource, "--stdin", "--json=compact"],
    {
      cwd: repositoryRoot,
      encoding: "utf-8",
      input: command,
      maxBuffer: AST_GREP_MAXIMUM_OUTPUT_BYTES,
      timeout: AST_GREP_TIMEOUT_MILLISECONDS,
    }
  );
  if (result.error || result.status !== 0) {
    throw astGrepProcessFailure(result);
  }
  let parsed;
  try {
    parsed = result.stdout.trim() === "" ? [] : JSON.parse(result.stdout);
  } catch {
    throw new BashAstError("ast-grep returned output that was not valid JSON", {
      code: "AST_GREP_INVALID_OUTPUT",
    });
  }
  if (!Array.isArray(parsed)) {
    throw new BashAstError(
      "ast-grep returned JSON with an unexpected top-level shape",
      { code: "AST_GREP_INVALID_OUTPUT" }
    );
  }
  if (parsed.some(({ ruleId }) => ruleId === "bash-parse-error")) {
    throw new BashAstError(
      "the Bash parser reported unsupported or invalid syntax",
      { code: "BASH_SYNTAX_UNSUPPORTED" }
    );
  }
  return parsed;
}

function captureText(match, collection, name) {
  const capture = match?.metaVariables?.[collection]?.[name];
  return typeof capture?.text === "string" ? capture.text : "";
}

function captureTexts(match, name) {
  const captures = match?.metaVariables?.multi?.[name];
  return Array.isArray(captures)
    ? captures
        .map(({ text }) => (typeof text === "string" ? text : ""))
        .filter(Boolean)
    : [];
}

function byteOffset(match, edge) {
  const value = match?.range?.byteOffset?.[edge];
  return Number.isSafeInteger(value) ? value : 0;
}

function decodeDoubleQuotedWord(value) {
  let decoded = "";
  let isEscaped = false;
  const characters = value.slice(1, -1);
  for (const character of characters) {
    if (isEscaped) {
      decoded += character;
      isEscaped = false;
    } else if (character === "\\") {
      isEscaped = true;
    } else {
      decoded += character;
    }
  }
  if (isEscaped) {
    decoded += "\\";
  }
  return decoded;
}

export function decodeShellWord(value) {
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  if (trimmed.length >= 2 && trimmed.startsWith("'") && trimmed.endsWith("'")) {
    return trimmed.slice(1, -1);
  }
  if (trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')) {
    return decodeDoubleQuotedWord(trimmed);
  }
  return trimmed.replaceAll(/\\(.)/gu, "$1");
}

function nestedShellSource(words) {
  const start = commandStart(words);
  const executable = executableName(words[start] ?? "");
  if (!["bash", "sh", "zsh"].includes(executable)) {
    return "";
  }
  const arguments_ = words.slice(start + 1);
  const optionIndex = arguments_.findIndex((argument) =>
    ["-c", "-lc"].includes(argument)
  );
  if (optionIndex === -1) {
    return "";
  }
  return arguments_[optionIndex + 1] ?? "";
}

function nestedEnvironmentSource(words) {
  let index = 0;
  while (index < words.length && environmentAssignment(words[index] ?? "")) {
    index += 1;
  }
  if (executableName(words[index] ?? "") !== "env") {
    return "";
  }
  const arguments_ = words.slice(index + 1);
  for (
    let argumentIndex = 0;
    argumentIndex < arguments_.length;
    argumentIndex += 1
  ) {
    const argument = arguments_[argumentIndex] ?? "";
    if (argument === "-S" || argument === "--split-string") {
      return arguments_.slice(argumentIndex + 1).join(" ");
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

function extractHeredocBody(value) {
  const lines = value.split("\n");
  return lines.length >= 3 ? lines.slice(1, -1).join("\n") : "";
}

function parseAtDepth(command, depth, runAstGrep) {
  if (depth > MAXIMUM_NESTED_SHELL_DEPTH) {
    throw new BashAstError(
      `nested Bash command depth exceeded the limit of ${MAXIMUM_NESTED_SHELL_DEPTH}`,
      { code: "BASH_NESTING_LIMIT" }
    );
  }
  const matches = requireAstGrepResult(command, runAstGrep);
  const matchedInvocations = matches
    .filter(({ ruleId }) => ruleId === "bash-command")
    .map((match) => ({
      end: byteOffset(match, "end"),
      match,
      start: byteOffset(match, "start"),
      words: [
        captureText(match, "single", "COMMAND"),
        ...captureTexts(match, "ARGUMENTS"),
      ]
        .filter(Boolean)
        .map(decodeShellWord),
    }));
  const bareInvocations = matches
    .filter(({ ruleId }) => ruleId === "bash-command-name")
    .filter((match) => {
      const start = byteOffset(match, "start");
      const end = byteOffset(match, "end");
      return matchedInvocations.every(
        (invocation) => invocation.start > start || invocation.end < end
      );
    })
    .map((match) => ({
      end: byteOffset(match, "end"),
      match,
      start: byteOffset(match, "start"),
      words: [decodeShellWord(match.text ?? "")],
    }));
  const commandMatches = [...matchedInvocations, ...bareInvocations].toSorted(
    (left, right) => left.start - right.start
  );
  const invocations = [];
  const nestedHeredocs = [];
  const nestedRedirects = [];
  let previousEnd = 0;
  const commandBuffer = Buffer.from(command);

  for (const { end, match, start, words } of commandMatches) {
    const separatorBefore = commandBuffer
      .subarray(previousEnd, start)
      .toString("utf-8");
    const invocation = Object.freeze({
      depth,
      end,
      separatorBefore,
      start,
      text: typeof match.text === "string" ? match.text : "",
      words: Object.freeze(words),
    });
    invocations.push(invocation);
    previousEnd = Math.max(previousEnd, end);

    const nestedSources = [
      nestedShellSource(words),
      nestedEnvironmentSource(words),
    ].filter(Boolean);
    for (const nested of nestedSources) {
      const nestedResult = parseAtDepth(nested, depth + 1, runAstGrep);
      invocations.push(...nestedResult.invocations);
      nestedHeredocs.push(...nestedResult.heredocs);
      nestedRedirects.push(...nestedResult.redirects);
    }
  }

  const redirects = matches
    .filter(({ ruleId }) => ruleId === "bash-output-redirect")
    .map((match) => {
      const sourceEnd =
        match?.metaVariables?.single?.SOURCE?.range?.byteOffset?.end ?? 0;
      const targetStart =
        match?.metaVariables?.single?.TARGET?.range?.byteOffset?.start ?? 0;
      const operator = commandBuffer
        .subarray(sourceEnd, targetStart)
        .toString("utf-8")
        .trim();
      return Object.freeze({
        operator,
        target: decodeShellWord(captureText(match, "single", "TARGET")),
      });
    });
  const heredocs = matches
    .filter(({ ruleId }) => ruleId === "bash-heredoc")
    .map(({ text }) => extractHeredocBody(typeof text === "string" ? text : ""))
    .filter(Boolean);

  return {
    heredocs: Object.freeze([...heredocs, ...nestedHeredocs]),
    invocations: Object.freeze(invocations),
    redirects: Object.freeze([...redirects, ...nestedRedirects]),
  };
}

export function parseBash(command, { runAstGrep = spawnSync } = {}) {
  return Object.freeze(parseAtDepth(command, 0, runAstGrep));
}

export function executableName(value) {
  const separator = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"));
  const basename = value.slice(separator + 1);
  return basename.endsWith(".exe")
    ? basename.slice(0, -".exe".length)
    : basename;
}

export function environmentAssignment(token) {
  const separator = token.indexOf("=");
  if (separator <= 0) {
    return null;
  }
  const name = token.slice(0, separator);
  return /^[A-Za-z_]\w*$/u.test(name)
    ? { name, value: token.slice(separator + 1) }
    : null;
}

function skipCommandWrapper(words, start) {
  const valueOptions = commandWrappers.get(executableName(words[start] ?? ""));
  let index = start + 1;
  while (index < words.length) {
    const word = words[index] ?? "";
    if (word === "--") {
      return index + 1;
    }
    if (environmentAssignment(word)) {
      index += 1;
    } else if (word.startsWith("-") && word !== "-") {
      index += valueOptions.has(word) ? 2 : 1;
    } else {
      return index;
    }
  }
  return index;
}

export function commandStart(words) {
  let index = 0;
  while (index < words.length) {
    while (index < words.length && environmentAssignment(words[index] ?? "")) {
      index += 1;
    }
    if (!commandWrappers.has(executableName(words[index] ?? ""))) {
      break;
    }
    index = skipCommandWrapper(words, index);
  }
  return index;
}

export function commandName(words) {
  return executableName(words[commandStart(words)] ?? "");
}

export function commandArguments(words) {
  return words.slice(commandStart(words) + 1);
}

function gitAliasName(value) {
  const separator = value.indexOf("=");
  const name = (
    separator === -1 ? value : value.slice(0, separator)
  ).toLowerCase();
  if (!name.startsWith("alias.")) {
    return "";
  }
  const alias = name.slice("alias.".length);
  return alias.endsWith(".command")
    ? alias.slice(0, -".command".length)
    : alias;
}

function addGitAlias(aliases, value) {
  const name = gitAliasName(value);
  if (name) {
    aliases.add(name);
  }
}

export function parseGitInvocation(words) {
  const start = commandStart(words);
  const aliases = new Set();
  for (const word of words.slice(0, start)) {
    const assignment = environmentAssignment(word);
    if (assignment?.name.startsWith("GIT_CONFIG_KEY_")) {
      addGitAlias(aliases, assignment.value);
    }
  }

  const arguments_ = words.slice(start + 1);
  let index = 0;
  let hasGlobalOptions = false;
  while (index < arguments_.length) {
    const argument = arguments_[index] ?? "";
    if (!argument.startsWith("-") || argument === "-") {
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
  const commandArguments_ = arguments_.slice(index);
  return Object.freeze({
    args: Object.freeze(commandArguments_),
    hasGlobalOptions,
    invokesAlias: aliases.has((commandArguments_[0] ?? "").toLowerCase()),
  });
}
