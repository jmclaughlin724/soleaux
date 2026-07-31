import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";

import { parseBash } from "./bash-ast.mjs";
import { evaluateDangerousGitShellWritesGuard } from "./dangerous-git-shell-writes-guard.mjs";
import { evaluateGitDeliveryBindingGuard } from "./git-delivery-binding-guard.mjs";
import { evaluateRawSqlDdlGuard } from "./raw-sql-ddl-guard.mjs";
import { evaluateSecretArgvGuard } from "./secret-argv-guard.mjs";
import { evaluateSecretEnvironmentFileReadGuard } from "./secret-env-file-read-guard.mjs";
import { evaluateSourceMutationBoundaryGuard } from "./source-mutation-boundary-guard.mjs";

const EXPECTED_EVENT = "PreToolUse";
const EXPECTED_TOOL = "Bash";
const EMPTY_COLLECTION_LENGTH = 0;
const STANDARD_INPUT_FILE_DESCRIPTOR = 0;
const KNOWN_DIAGNOSTIC_CODES = new Set([
  "AST_GREP_EXIT_NONZERO",
  "AST_GREP_INVALID_OUTPUT",
  "AST_GREP_NOT_FOUND",
  "AST_GREP_OUTPUT_LIMIT",
  "AST_GREP_TIMEOUT",
  "BASH_NESTING_LIMIT",
  "BASH_SYNTAX_UNSUPPORTED",
  "FILESYSTEM_INSPECTION_FAILED",
  "GIT_STATE_UNAVAILABLE",
  "HOOK_INPUT_INVALID",
  "POLICY_GUIDANCE_INVALID",
  "SQL_PARSE_FAILED",
  "UNCLASSIFIED_HOOK_FAILURE",
]);
const DIAGNOSTIC_ACTIONS = new Map([
  [
    "AST_GREP_EXIT_NONZERO",
    "Run the repository ast-grep validation and repair the local parser installation before retrying.",
  ],
  [
    "AST_GREP_INVALID_OUTPUT",
    "Verify the pinned ast-grep version and Bash inline-rule compatibility before retrying.",
  ],
  [
    "AST_GREP_NOT_FOUND",
    "Restore dependencies with pnpm so node_modules/.bin/ast-grep exists, then retry.",
  ],
  [
    "AST_GREP_OUTPUT_LIMIT",
    "Split the Bash request into smaller commands and retry.",
  ],
  [
    "AST_GREP_TIMEOUT",
    "Retry with a smaller Bash command; if it recurs, run the Bash policy tests and inspect ast-grep health.",
  ],
  [
    "BASH_NESTING_LIMIT",
    "Remove nested shell wrappers and invoke the intended command directly.",
  ],
  [
    "BASH_SYNTAX_UNSUPPORTED",
    "Use a simpler valid Bash form whose structure ast-grep can parse.",
  ],
  [
    "FILESYSTEM_INSPECTION_FAILED",
    "Confirm the working directory and referenced paths are accessible, then retry the structured operation.",
  ],
  [
    "GIT_STATE_UNAVAILABLE",
    "Confirm the working directory is in a readable Git checkout, then retry the delivery command.",
  ],
  [
    "HOOK_INPUT_INVALID",
    "Retry through the native Bash tool with a complete PreToolUse payload.",
  ],
  [
    "POLICY_GUIDANCE_INVALID",
    "Repair the named policy module so every denial includes `Corrective action:`, run the focused Bash policy tests, then retry.",
  ],
  [
    "SQL_PARSE_FAILED",
    "Repair the candidate SQL syntax or use the declarative Supaschema workflow, then retry.",
  ],
  [
    "UNCLASSIFIED_HOOK_FAILURE",
    "Run the focused Bash policy tests and repair the named policy owner before retrying.",
  ],
]);
const DIAGNOSTIC_CAUSES = new Map([
  [
    "FILESYSTEM_INSPECTION_FAILED",
    "repository path or filesystem state could not be inspected",
  ],
  ["GIT_STATE_UNAVAILABLE", "required Git repository state could not be read"],
  [
    "HOOK_INPUT_INVALID",
    "the hook payload did not satisfy the native PreToolUse Bash protocol",
  ],
  [
    "POLICY_GUIDANCE_INVALID",
    "the named policy module returned a denial without an explicit corrective course of action",
  ],
  [
    "SQL_PARSE_FAILED",
    "the PostgreSQL parser could not analyze a candidate database CLI statement",
  ],
  [
    "UNCLASSIFIED_HOOK_FAILURE",
    "the named policy check raised an unexpected internal exception",
  ],
]);
const DEFAULT_CHECKS = Object.freeze([
  Object.freeze({
    code: "UNCLASSIFIED_HOOK_FAILURE",
    evaluate: evaluateSecretArgvGuard,
    source: ".codex/hooks/PreToolUse/secret-argv-guard.mjs",
  }),
  Object.freeze({
    code: "UNCLASSIFIED_HOOK_FAILURE",
    evaluate: evaluateSecretEnvironmentFileReadGuard,
    source: ".codex/hooks/PreToolUse/secret-env-file-read-guard.mjs",
  }),
  Object.freeze({
    code: "SQL_PARSE_FAILED",
    evaluate: evaluateRawSqlDdlGuard,
    source: ".codex/hooks/PreToolUse/raw-sql-ddl-guard.mjs",
  }),
  Object.freeze({
    code: "UNCLASSIFIED_HOOK_FAILURE",
    evaluate: evaluateDangerousGitShellWritesGuard,
    source: ".codex/hooks/PreToolUse/dangerous-git-shell-writes-guard.mjs",
  }),
  Object.freeze({
    code: "FILESYSTEM_INSPECTION_FAILED",
    evaluate: evaluateSourceMutationBoundaryGuard,
    source: ".codex/hooks/PreToolUse/source-mutation-boundary-guard.mjs",
  }),
  Object.freeze({
    code: "GIT_STATE_UNAVAILABLE",
    evaluate: evaluateGitDeliveryBindingGuard,
    source: ".codex/hooks/PreToolUse/git-delivery-binding-guard.mjs",
  }),
]);

class HookInputError extends Error {
  constructor() {
    super("invalid native hook input");
    this.code = "HOOK_INPUT_INVALID";
    this.name = "HookInputError";
  }
}

class HookExecutionError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "HookExecutionError";
  }
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonemptyString(value) {
  return typeof value === "string" && value !== "";
}

function requireBashPayload(payload) {
  if (!isRecord(payload)) {
    throw new HookInputError();
  }
  if (
    payload.hook_event_name !== EXPECTED_EVENT ||
    payload.tool_name !== EXPECTED_TOOL
  ) {
    throw new HookInputError();
  }
  if (!isNonemptyString(payload.cwd) || !isAbsolute(payload.cwd)) {
    throw new HookInputError();
  }
  if (!isRecord(payload.tool_input)) {
    throw new HookInputError();
  }
  if (
    !isNonemptyString(payload.tool_input.command) ||
    payload.tool_input.command.trim() === ""
  ) {
    throw new HookInputError();
  }
  return payload;
}

function errorCode(error) {
  if (!isRecord(error) || typeof error.code !== "string") {
    return "";
  }
  return error.code;
}

function safeCause(error, code) {
  if (
    isRecord(error) &&
    typeof error.safeCause === "string" &&
    error.safeCause !== ""
  ) {
    return error.safeCause;
  }
  return (
    DIAGNOSTIC_CAUSES.get(code) ??
    DIAGNOSTIC_CAUSES.get("UNCLASSIFIED_HOOK_FAILURE")
  );
}

function diagnostic(source, fallbackCode, error) {
  const reportedCode = errorCode(error);
  const code = KNOWN_DIAGNOSTIC_CODES.has(reportedCode)
    ? reportedCode
    : fallbackCode;
  return Object.freeze({
    action: DIAGNOSTIC_ACTIONS.get(code),
    cause: safeCause(error, code),
    code,
    source,
  });
}

function operationalFailureMessage(diagnostics, reasons = []) {
  const lines = [
    "BLOCKED: Codex Bash policy could not complete; the command was not executed.",
    ...diagnostics.flatMap(({ action, cause, code, source }) => [
      `source=${source}`,
      `code=${code}`,
      `cause=${cause}`,
      `Corrective action: ${action}`,
    ]),
  ];
  const policyFindings = [...new Set(reasons)];
  if (policyFindings.length > EMPTY_COLLECTION_LENGTH) {
    lines.push(
      "Additional policy findings discovered before execution stopped:",
      ...policyFindings
    );
  }
  return lines.join("\n");
}

function failExecution(diagnostics, reasons = []) {
  throw new HookExecutionError(operationalFailureMessage(diagnostics, reasons));
}

function nativeOutput(reasons) {
  if (reasons.length === EMPTY_COLLECTION_LENGTH) {
    return null;
  }
  const denialReasons = [...new Set(reasons)];
  return {
    hookSpecificOutput: {
      hookEventName: EXPECTED_EVENT,
      permissionDecision: "deny",
      permissionDecisionReason: denialReasons.join("\n\n"),
    },
  };
}

export async function evaluateBashPolicy(
  rawPayload,
  { checks = DEFAULT_CHECKS, parse = parseBash } = {}
) {
  let payload;
  try {
    payload = requireBashPayload(rawPayload);
  } catch (error) {
    failExecution([
      diagnostic(
        ".codex/hooks/PreToolUse/bash-policy.mjs",
        "HOOK_INPUT_INVALID",
        error
      ),
    ]);
  }

  let bash;
  try {
    bash = parse(payload.tool_input.command);
  } catch (error) {
    failExecution([
      diagnostic(
        ".codex/hooks/PreToolUse/bash-ast.mjs",
        "UNCLASSIFIED_HOOK_FAILURE",
        error
      ),
    ]);
  }

  const context = Object.freeze({
    bash,
    cwd: resolve(payload.cwd),
  });
  const diagnostics = [];
  const reasons = [];
  for (const check of checks) {
    try {
      // Policies intentionally run sequentially so applicable state checks are lazy and deterministic.
      // eslint-disable-next-line no-await-in-loop
      const reason = await check.evaluate(context);
      if (typeof reason === "string" && reason !== "") {
        if (!reason.includes("Corrective action:")) {
          const error = new TypeError(
            "policy denial omitted required corrective guidance"
          );
          error.code = "POLICY_GUIDANCE_INVALID";
          throw error;
        }
        reasons.push(reason);
      } else if (reason !== null) {
        throw new TypeError("policy check returned an invalid result");
      }
    } catch (error) {
      diagnostics.push(diagnostic(check.source, check.code, error));
    }
  }
  if (diagnostics.length > EMPTY_COLLECTION_LENGTH) {
    failExecution(diagnostics, reasons);
  }
  return nativeOutput(reasons);
}

export function runBashPolicyHook(source, dependencies = {}) {
  let payload;
  try {
    payload = source.trim() === "" ? {} : JSON.parse(source);
  } catch (error) {
    failExecution([
      diagnostic(
        ".codex/hooks/PreToolUse/bash-policy.mjs",
        "HOOK_INPUT_INVALID",
        error
      ),
    ]);
  }
  return evaluateBashPolicy(payload, dependencies);
}

async function main() {
  try {
    const source = readFileSync(STANDARD_INPUT_FILE_DESCRIPTOR, "utf-8");
    const output = await runBashPolicyHook(source);
    if (output !== null) {
      process.stdout.write(`${JSON.stringify(output)}\n`);
    }
  } catch (error) {
    const message =
      error instanceof HookExecutionError
        ? error.message
        : operationalFailureMessage([
            diagnostic(
              ".codex/hooks/PreToolUse/bash-policy.mjs",
              "UNCLASSIFIED_HOOK_FAILURE",
              error
            ),
          ]);
    process.stderr.write(`${message}\n`);
    process.exitCode = 2;
  }
}

const [, entrypoint] = process.argv;
if (entrypoint) {
  try {
    if (realpathSync(entrypoint) === realpathSync(import.meta.filename)) {
      await main();
    }
  } catch {
    if (entrypoint === import.meta.filename) {
      await main();
    }
  }
}
