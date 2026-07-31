import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

// Manual workspace-diagnostics owner (https://developers.openai.com/codex/hooks).
// The `--workspace` invocation runs the full scan, prints ::workspace-diagnostic:: lines,
// and exits 1 only when an owner itself reports a blocking result. Diagnostic visibility
// and owner blocking status are separate values: an owner that exits zero while reporting
// warnings stays visible without failing the task. This command does not persist diagnostics;
// the VS Code task publishes its output to the Problems panel.

const WORKSPACE_MODE_FLAG = "--workspace";
const CLI_MODE_ARGUMENT_START_INDEX = 2;
const EMPTY_COLLECTION_SIZE = 0;
const ENTRYPOINT_ARGUMENT_INDEX = 1;
const ERROR_SEVERITY = "error";
const FAILURE_EXIT_CODE = 1;
const FIRST_ITEM_INDEX = 0;
const LAST_ITEM_INDEX = -1;
const MINIMUM_POSITION = 1;
const NEXT_ITEM_OFFSET = 1;
const NOT_FOUND_INDEX = -1;
const POSITION_OFFSET = 1;
const SUCCESS_EXIT_CODE = 0;
const COMMAND_TIMEOUT_MS = 5000;
const STANDARDS_CONFIG = "sgconfig.yml";

export const DIAGNOSTIC_PREFIX = "::workspace-diagnostic::";

const WORKSPACE_COMMAND_TIMEOUT_MS = 120_000;
const MAX_BUFFER_BYTES = 16_777_216;
const MAX_EXECUTION_DETAIL_CHARACTERS = 500;
const LINE_PATTERN = /\r?\n/gu;
const DIGITS_PATTERN = /^\d+$/u;
const TOMBI_LOCATION_PREFIX = "at ";
const TOMBI_MESSAGE_SEPARATOR = ":";
const TOMBI_SEVERITY_LABELS = new Set(["Error", "Warning", "Info"]);
const TOMBI_UNFORMATTED_SUFFIX = '" is not formatted';
const WHITESPACE_PATTERN = /\s+/gu;
const SEVERITY_RANK = { [ERROR_SEVERITY]: 3, info: 1, warning: 2 };
const SEVERITIES = new Map([
  [ERROR_SEVERITY, ERROR_SEVERITY],
  ["fatal", ERROR_SEVERITY],
  ["hint", "info"],
  ["info", "info"],
  ["none", "info"],
  ["note", "info"],
  ["warn", "warning"],
  ["warning", "warning"],
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function textValue(value) {
  return typeof value === "string" ? value : "";
}

function cleanText(value) {
  return textValue(value).replaceAll(WHITESPACE_PATTERN, " ").trim();
}

function cleanCode(value, fallback) {
  return (cleanText(value) || fallback).replaceAll("]", ")");
}

function normalizeSeverity(value) {
  return SEVERITIES.get(cleanText(value).toLowerCase()) ?? ERROR_SEVERITY;
}

function positivePosition(value) {
  return Number.isSafeInteger(value) && value > EMPTY_COLLECTION_SIZE
    ? value
    : MINIMUM_POSITION;
}

function parseTombiMessageLine(sourceLine) {
  const trimmedLine = sourceLine.trim();
  const separatorIndex = trimmedLine.indexOf(TOMBI_MESSAGE_SEPARATOR);
  if (separatorIndex === NOT_FOUND_INDEX) {
    return null;
  }

  const severity = trimmedLine.slice(FIRST_ITEM_INDEX, separatorIndex);
  const message = trimmedLine
    .slice(separatorIndex + TOMBI_MESSAGE_SEPARATOR.length)
    .trim();
  if (
    !TOMBI_SEVERITY_LABELS.has(severity) ||
    message.length === EMPTY_COLLECTION_SIZE
  ) {
    return null;
  }
  return { message, severity };
}

function parseTombiUnformattedPath(message) {
  if (!message.startsWith('"') || !message.endsWith(TOMBI_UNFORMATTED_SUFFIX)) {
    return null;
  }

  const filePath = message.slice(
    NEXT_ITEM_OFFSET,
    -TOMBI_UNFORMATTED_SUFFIX.length
  );
  return filePath.length > EMPTY_COLLECTION_SIZE ? filePath : null;
}

function parseTombiLocationLine(sourceLine) {
  const trimmedLine = sourceLine.trim();
  if (!trimmedLine.startsWith(TOMBI_LOCATION_PREFIX)) {
    return null;
  }

  const location = trimmedLine.slice(TOMBI_LOCATION_PREFIX.length);
  const columnSeparatorIndex = location.lastIndexOf(TOMBI_MESSAGE_SEPARATOR);
  if (columnSeparatorIndex === NOT_FOUND_INDEX) {
    return null;
  }
  const lineSeparatorIndex = location.lastIndexOf(
    TOMBI_MESSAGE_SEPARATOR,
    columnSeparatorIndex - NEXT_ITEM_OFFSET
  );
  if (lineSeparatorIndex === NOT_FOUND_INDEX) {
    return null;
  }

  const filePath = location.slice(FIRST_ITEM_INDEX, lineSeparatorIndex);
  const line = location.slice(
    lineSeparatorIndex + NEXT_ITEM_OFFSET,
    columnSeparatorIndex
  );
  const column = location.slice(columnSeparatorIndex + NEXT_ITEM_OFFSET);
  if (
    filePath.length === EMPTY_COLLECTION_SIZE ||
    !DIGITS_PATTERN.test(line) ||
    !DIGITS_PATTERN.test(column)
  ) {
    return null;
  }
  return { column: Number(column), line: Number(line), path: filePath };
}

function isWithinDirectory(directory, candidatePath) {
  const pathFromDirectory = relative(directory, candidatePath);
  return (
    pathFromDirectory === "" ||
    (pathFromDirectory !== ".." &&
      !pathFromDirectory.startsWith(`..${sep}`) &&
      !isAbsolute(pathFromDirectory))
  );
}

function combinedOutput(result) {
  return [result?.stdout, result?.stderr]
    .filter(
      (value) =>
        typeof value === "string" && value.trim().length > EMPTY_COLLECTION_SIZE
    )
    .map((value) => value.trim())
    .join("\n");
}

function commandOutcome(result) {
  if (Error.isError(result?.error)) {
    return result.error.message;
  }
  if (result?.signal) {
    return `terminated by signal ${result.signal}`;
  }
  return result?.status === null || result?.status === undefined
    ? "completed without an exit status"
    : `exited with status ${result.status}`;
}

function findRepoRoot(cwd) {
  const result = spawnSync("git", ["rev-parse", "--show-toplevel"], {
    cwd,
    encoding: "utf-8",
    timeout: COMMAND_TIMEOUT_MS,
  });
  if (result.error) {
    throw new Error(`git rev-parse failed: ${result.error.message}`);
  }
  if (result.status !== SUCCESS_EXIT_CODE) {
    const statusDetail = result.stderr.trim() || `exit ${result.status}`;
    throw new Error(`git rev-parse failed: ${statusDetail}`);
  }

  const repoRoot = result.stdout.trim();
  if (repoRoot.length === EMPTY_COLLECTION_SIZE) {
    throw new Error("git rev-parse returned an empty repository root");
  }
  return realpathSync(repoRoot);
}

// Exit status an owner uses to mean "I completed and found problems". Any other
// nonzero status means the owner failed its own contract. Most CLIs use 1, but
// Stylelint reserves 1 for a fatal error and reports lint problems as 2, so this is
// declared per owner rather than inferred from the status value.
const FINDINGS_STATUS_DEFAULT = 1;

function workspaceCommand({
  args,
  executable,
  fallbackPath,
  findingsStatus = FINDINGS_STATUS_DEFAULT,
  id,
  label,
  parser,
  pathBase = "",
}) {
  return {
    args,
    executable,
    fallbackPath,
    findingsStatus,
    id,
    label,
    parser,
    pathBase,
    timeoutMs: WORKSPACE_COMMAND_TIMEOUT_MS,
  };
}

export function createWorkspaceDiagnosticCommands() {
  return [
    workspaceCommand({
      args: ["exec", "tombi", "lint", "--error-on-warnings", "--offline"],
      executable: "pnpm",
      fallbackPath: "tombi.toml",
      id: "tombi",
      label: "Tombi",
      parser: parseTombiCommand,
    }),
    workspaceCommand({
      args: ["exec", "tombi", "format", "--check", "--offline"],
      executable: "pnpm",
      fallbackPath: "tombi.toml",
      id: "tombi-format",
      label: "Tombi format",
      parser: parseTombiCommand,
    }),
    workspaceCommand({
      args: [
        "exec",
        "prettier",
        "--list-different",
        "--no-color",
        "**/*.{md,mdx,yml,yaml}",
      ],
      executable: "pnpm",
      fallbackPath: ".prettierignore",
      id: "prettier",
      label: "Prettier",
      parser: parsePrettierCommand,
    }),
    workspaceCommand({
      args: [
        "exec",
        "eslint",
        ".",
        "--no-error-on-unmatched-pattern",
        "--format",
        "json",
      ],
      executable: "pnpm",
      fallbackPath: "eslint.config.mjs",
      id: "eslint",
      label: "ESLint",
      parser: parseEslintCommand,
    }),
    workspaceCommand({
      args: [
        "exec",
        "stylelint",
        "**/*.{css,scss,sass,less}",
        "--allow-empty-input",
        "--formatter",
        "json",
      ],
      executable: "pnpm",
      fallbackPath: "stylelint.config.mjs",
      // Stylelint reserves exit 1 for a fatal error and reports lint problems as 2.
      findingsStatus: 2,
      id: "stylelint",
      label: "Stylelint",
      parser: parseStylelintCommand,
    }),
    workspaceCommand({
      args: [
        "ast-grep",
        "scan",
        "--config",
        STANDARDS_CONFIG,
        "--error=unused-suppression",
        "--error=no-suppress-all",
        "--json=compact",
        "--color",
        "never",
        "--globs",
        "!plans/**",
        ".",
      ],
      executable: "pnpm",
      fallbackPath: STANDARDS_CONFIG,
      id: "ast-grep",
      label: "ast-grep",
      parser: parseAstGrepJsonCommand,
    }),
  ];
}

function pathFromUri(value) {
  if (!value.startsWith("file:")) {
    return value;
  }
  try {
    return fileURLToPath(value);
  } catch {
    return value;
  }
}

export function normalizeDiagnosticPath(value, repoRoot, pathBase = "") {
  const cleaned = cleanText(value);
  const unquoted =
    cleaned.startsWith('"') && cleaned.endsWith('"')
      ? cleaned.slice(NEXT_ITEM_OFFSET, LAST_ITEM_INDEX)
      : cleaned;
  const rawPath = pathFromUri(unquoted);
  if (rawPath.length === EMPTY_COLLECTION_SIZE) {
    return "";
  }

  const absolutePath = isAbsolute(rawPath)
    ? resolve(rawPath)
    : resolve(repoRoot, pathBase, rawPath);
  return isWithinDirectory(repoRoot, absolutePath)
    ? relative(repoRoot, absolutePath) || "."
    : absolutePath;
}

function diagnostic({
  code,
  column,
  help,
  line,
  message,
  path,
  severity,
  source,
}) {
  const item = {
    code: cleanCode(code, `${source}/diagnostic`),
    column: positivePosition(column),
    line: positivePosition(line),
    message: cleanText(message) || `${source} reported a diagnostic.`,
    path,
    severity: normalizeSeverity(severity),
  };
  const helpUri = cleanText(help);
  if (helpUri.length > EMPTY_COLLECTION_SIZE) {
    item.help = helpUri;
  }
  return item;
}

// ast-grep JSON positions are zero-based; diagnostics are one-based.
function astGrepPosition(value) {
  return Number.isSafeInteger(value)
    ? value + POSITION_OFFSET
    : MINIMUM_POSITION;
}

export function parseAstGrepJsonDiagnostics(output, commandSpec, repoRoot) {
  const matches = JSON.parse(output.trim() || "[]");
  if (!Array.isArray(matches)) {
    throw new TypeError(
      `${commandSpec.label} did not return a JSON match array`
    );
  }

  const diagnostics = [];
  for (const match of matches) {
    if (!isRecord(match)) {
      continue;
    }
    const range = isRecord(match.range) ? match.range : null;
    const start = isRecord(range?.start) ? range.start : null;
    diagnostics.push(
      diagnostic({
        code: `${commandSpec.id}/${cleanText(match.ruleId) || "diagnostic"}`,
        column: astGrepPosition(start?.column),
        line: astGrepPosition(start?.line),
        message: cleanText(match.message),
        path: normalizeDiagnosticPath(
          textValue(match.file) || commandSpec.fallbackPath,
          repoRoot,
          commandSpec.pathBase
        ),
        severity: match.severity,
        source: commandSpec.id,
      })
    );
  }
  return diagnostics;
}

// ESLint reports severity as 2 for an error and 1 for a warning.
const ESLINT_ERROR_SEVERITY = 2;

function createEslintDiagnostic(result, message, commandSpec, repoRoot) {
  if (!isRecord(message)) {
    return null;
  }

  // A fatal message is a parse failure in one file, not an owner failure.
  const rule =
    cleanText(message.ruleId) || (message.fatal ? "parse" : "diagnostic");
  return diagnostic({
    code: `${commandSpec.id}/${rule}`,
    column: message.column,
    line: message.line,
    message: cleanText(message.message),
    path: normalizeDiagnosticPath(
      textValue(result.filePath) || commandSpec.fallbackPath,
      repoRoot,
      commandSpec.pathBase
    ),
    severity:
      message.severity === ESLINT_ERROR_SEVERITY ? ERROR_SEVERITY : "warning",
    source: commandSpec.id,
  });
}

export function parseEslintJsonDiagnostics(output, commandSpec, repoRoot) {
  const results = JSON.parse(output.trim() || "[]");
  if (!Array.isArray(results)) {
    throw new TypeError(
      `${commandSpec.label} did not return a JSON result array`
    );
  }

  const diagnostics = [];
  for (const result of results) {
    if (isRecord(result) && Array.isArray(result.messages)) {
      for (const message of result.messages) {
        const item = createEslintDiagnostic(
          result,
          message,
          commandSpec,
          repoRoot
        );
        if (item !== null) {
          diagnostics.push(item);
        }
      }
    }
  }
  return diagnostics;
}

function createStylelintDiagnostic(result, warning, commandSpec, repoRoot) {
  if (!isRecord(warning)) {
    return null;
  }

  return diagnostic({
    code: `${commandSpec.id}/${cleanText(warning.rule) || "diagnostic"}`,
    column: warning.column,
    line: warning.line,
    message: cleanText(warning.text),
    path: normalizeDiagnosticPath(
      textValue(result.source) || commandSpec.fallbackPath,
      repoRoot,
      commandSpec.pathBase
    ),
    severity: warning.severity,
    source: commandSpec.id,
  });
}

export function parseStylelintJsonDiagnostics(output, commandSpec, repoRoot) {
  const results = JSON.parse(output.trim() || "[]");
  if (!Array.isArray(results)) {
    throw new TypeError(
      `${commandSpec.label} did not return a JSON result array`
    );
  }

  const diagnostics = [];
  for (const result of results) {
    if (isRecord(result) && Array.isArray(result.warnings)) {
      for (const warning of result.warnings) {
        const item = createStylelintDiagnostic(
          result,
          warning,
          commandSpec,
          repoRoot
        );
        if (item !== null) {
          diagnostics.push(item);
        }
      }
    }
  }
  return diagnostics;
}

export function parseTombiDiagnostics(output, commandSpec, repoRoot) {
  const diagnostics = [];
  let pending = null;

  for (const sourceLine of output.split(LINE_PATTERN)) {
    const message = parseTombiMessageLine(sourceLine);
    if (message) {
      const unformattedPath = parseTombiUnformattedPath(message.message);
      if (unformattedPath) {
        diagnostics.push(
          diagnostic({
            code: `${commandSpec.id}/format`,
            column: MINIMUM_POSITION,
            line: MINIMUM_POSITION,
            message:
              "File does not match Tombi formatting. Fix: pnpm exec tombi format.",
            path: normalizeDiagnosticPath(
              unformattedPath,
              repoRoot,
              commandSpec.pathBase
            ),
            severity: message.severity,
            source: commandSpec.id,
          })
        );
        pending = null;
      } else {
        pending = message;
      }
    } else {
      const location = parseTombiLocationLine(sourceLine);
      if (location !== null && pending !== null) {
        diagnostics.push(
          diagnostic({
            code: `${commandSpec.id}/diagnostic`,
            column: location.column,
            line: location.line,
            message: pending.message,
            path: normalizeDiagnosticPath(
              location.path,
              repoRoot,
              commandSpec.pathBase
            ),
            severity: pending.severity,
            source: commandSpec.id,
          })
        );
        pending = null;
      }
    }
  }
  return diagnostics;
}

export function parsePrettierDiagnostics(output, commandSpec, repoRoot) {
  return output
    .split(LINE_PATTERN)
    .map((line) => line.trim())
    .filter(
      (line) =>
        line.length > EMPTY_COLLECTION_SIZE &&
        !line.startsWith("[") &&
        !line.startsWith("Checking formatting") &&
        !line.startsWith("All matched files")
    )
    .map((filePath) =>
      diagnostic({
        code: `${commandSpec.id}/format`,
        column: 1,
        line: 1,
        message:
          "File does not match Prettier formatting. Fix: pnpm fix:prose.",
        path: normalizeDiagnosticPath(filePath, repoRoot, commandSpec.pathBase),
        severity: "warning",
        source: commandSpec.id,
      })
    );
}

function parsePrettierCommand(result, commandSpec, repoRoot) {
  return parsePrettierDiagnostics(
    combinedOutput(result),
    commandSpec,
    repoRoot
  );
}

function parseAstGrepJsonCommand(result, commandSpec, repoRoot) {
  return parseAstGrepJsonDiagnostics(
    textValue(result.stdout),
    commandSpec,
    repoRoot
  );
}

function parseEslintCommand(result, commandSpec, repoRoot) {
  return parseEslintJsonDiagnostics(
    textValue(result.stdout),
    commandSpec,
    repoRoot
  );
}

function parseStylelintCommand(result, commandSpec, repoRoot) {
  return parseStylelintJsonDiagnostics(
    textValue(result.stdout),
    commandSpec,
    repoRoot
  );
}

function parseTombiCommand(result, commandSpec, repoRoot) {
  return parseTombiDiagnostics(combinedOutput(result), commandSpec, repoRoot);
}

function executionDiagnostic(commandSpec, repoRoot, outcome, output = "") {
  const detail = cleanText(output).slice(
    FIRST_ITEM_INDEX,
    MAX_EXECUTION_DETAIL_CHARACTERS
  );
  const detailSuffix =
    detail.length > EMPTY_COLLECTION_SIZE ? ` ${detail}` : "";
  return diagnostic({
    code: `${commandSpec.id}/execution`,
    column: 1,
    line: 1,
    message: `${commandSpec.label} ${outcome}.${detailSuffix}`,
    path: normalizeDiagnosticPath(commandSpec.fallbackPath, repoRoot),
    severity: ERROR_SEVERITY,
    source: commandSpec.id,
  });
}

function deduplicateDiagnostics(diagnostics) {
  const unique = new Map();
  for (const item of diagnostics) {
    const key = JSON.stringify([
      item.path,
      item.line,
      item.column,
      item.code,
      item.message,
    ]);
    const current = unique.get(key);
    if (
      current === undefined ||
      SEVERITY_RANK[item.severity] > SEVERITY_RANK[current.severity]
    ) {
      unique.set(key, item);
    }
  }
  return unique
    .values()
    .toArray()
    .toSorted(
      (left, right) =>
        left.path.localeCompare(right.path) ||
        left.line - right.line ||
        left.column - right.column ||
        left.code.localeCompare(right.code)
    );
}

function ownerResult(commandSpec, { blocking, diagnostics, status, valid }) {
  return {
    blocking,
    diagnostics,
    id: commandSpec.id,
    label: commandSpec.label,
    status,
    valid,
  };
}

// An invalid owner publishes exactly one diagnostic about itself. Source findings from a
// run that failed its own contract are not trustworthy, so they are deliberately dropped.
function invalidOwner(commandSpec, repoRoot, outcome, output, status) {
  return ownerResult(commandSpec, {
    blocking: true,
    diagnostics: [executionDiagnostic(commandSpec, repoRoot, outcome, output)],
    status,
    valid: false,
  });
}

function runWorkspaceOwner(commandSpec, repoRoot, runner) {
  let result;
  try {
    result = runner(commandSpec.executable, commandSpec.args, {
      cwd: repoRoot,
      encoding: "utf-8",
      maxBuffer: MAX_BUFFER_BYTES,
      timeout: commandSpec.timeoutMs,
    });
  } catch (error) {
    return invalidOwner(
      commandSpec,
      repoRoot,
      "could not start",
      Error.isError(error) ? error.message : String(error),
      null
    );
  }

  if (
    result?.error ||
    result?.signal ||
    result?.status === null ||
    result?.status === undefined
  ) {
    return invalidOwner(
      commandSpec,
      repoRoot,
      commandOutcome(result),
      combinedOutput(result),
      null
    );
  }

  let parsed;
  try {
    parsed = commandSpec.parser(result, commandSpec, repoRoot);
  } catch (error) {
    return invalidOwner(
      commandSpec,
      repoRoot,
      "returned invalid diagnostic output",
      `${Error.isError(error) ? error.message : String(error)} ${combinedOutput(result)}`,
      result.status
    );
  }

  // A nonzero status the owner does not use for findings, or a findings status the
  // parser could not explain, means the owner did not complete its findings contract.
  const isReportedFindings = result.status === commandSpec.findingsStatus;
  if (
    (result.status !== SUCCESS_EXIT_CODE && !isReportedFindings) ||
    (isReportedFindings && parsed.length === EMPTY_COLLECTION_SIZE)
  ) {
    return invalidOwner(
      commandSpec,
      repoRoot,
      commandOutcome(result),
      combinedOutput(result),
      result.status
    );
  }

  // The owner's own exit status decides blocking. ast-grep exits zero for warning-only
  // findings, so those stay visible without failing the workspace task.
  return ownerResult(commandSpec, {
    blocking: result.status !== SUCCESS_EXIT_CODE,
    diagnostics: parsed,
    status: result.status,
    valid: true,
  });
}

export function runWorkspaceDiagnostics(
  repoRoot,
  runner = spawnSync,
  commands = createWorkspaceDiagnosticCommands()
) {
  const owners = commands.map((commandSpec) =>
    runWorkspaceOwner(commandSpec, repoRoot, runner)
  );
  return {
    diagnostics: deduplicateDiagnostics(
      owners.flatMap((owner) => owner.diagnostics)
    ),
    owners,
  };
}

export function formatWorkspaceDiagnostic(item) {
  return `${DIAGNOSTIC_PREFIX + item.path}:${item.line}:${item.column}: ${
    item.severity
  } [${item.code}] ${cleanText(item.message)}`;
}

function executeWorkspaceCli(repoRoot) {
  const { diagnostics, owners } = runWorkspaceDiagnostics(repoRoot);
  if (diagnostics.length > EMPTY_COLLECTION_SIZE) {
    process.stdout.write(
      `${diagnostics.map(formatWorkspaceDiagnostic).join("\n")}\n`
    );
  }
  if (owners.some((owner) => owner.blocking)) {
    process.exitCode = FAILURE_EXIT_CODE;
  }
}

function runWorkspaceCli() {
  try {
    const repoRoot = findRepoRoot(process.cwd());
    executeWorkspaceCli(repoRoot);
  } catch (error) {
    process.stderr.write(
      `Workspace diagnostics failed: ${Error.isError(error) ? error.message : String(error)}\n`
    );
    process.exitCode = FAILURE_EXIT_CODE;
  }
}

function main() {
  const modes = process.argv.slice(CLI_MODE_ARGUMENT_START_INDEX);
  if (
    modes.length !== NEXT_ITEM_OFFSET ||
    modes[FIRST_ITEM_INDEX] !== WORKSPACE_MODE_FLAG
  ) {
    process.stderr.write(
      `Workspace diagnostics requires exactly one mode: ${WORKSPACE_MODE_FLAG}.\n`
    );
    process.exitCode = FAILURE_EXIT_CODE;
    return;
  }
  runWorkspaceCli();
}

const entrypoint = process.argv.at(ENTRYPOINT_ARGUMENT_INDEX);
if (
  entrypoint &&
  realpathSync(entrypoint) === realpathSync(import.meta.filename)
) {
  main();
}
