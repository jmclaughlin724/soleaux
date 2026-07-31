import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import {
  evaluateBashPolicy,
  runBashPolicyHook,
} from "../PreToolUse/bash-policy.mjs";
import { evaluateGitDeliveryBindingGuard } from "../PreToolUse/git-delivery-binding-guard.mjs";
import { evaluateSourceMutationBoundaryGuard } from "../PreToolUse/source-mutation-boundary-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const handlerPath = fileURLToPath(
  new URL("../PreToolUse/bash-policy.mjs", import.meta.url)
);
const emptyBash = Object.freeze({
  heredocs: Object.freeze([]),
  invocations: Object.freeze([]),
  redirects: Object.freeze([]),
});

function payload(command = "git status --short") {
  return {
    cwd: repoRoot,
    hook_event_name: "PreToolUse",
    tool_input: { command },
    tool_name: "Bash",
  };
}

function runHandler(input) {
  return spawnSync(process.execPath, [handlerPath], {
    cwd: repoRoot,
    encoding: "utf-8",
    input,
    timeout: 10_000,
  });
}

async function expectExecutionFailure(operation, code) {
  let failure;
  try {
    await operation();
  } catch (error) {
    failure = error;
  }
  expect(failure).toBeInstanceOf(Error);
  expect(failure.message).toContain(`code=${code}`);
  expect(failure.message).toContain("source=");
  expect(failure.message).toContain("cause=");
  expect(failure.message).toContain("Corrective action:");
  return failure.message;
}

test("malformed JSON exits 2 with redacted corrective guidance", async () => {
  const message = await expectExecutionFailure(
    () => runBashPolicyHook("{malformed"),
    "HOOK_INPUT_INVALID"
  );
  expect(message).not.toContain("{malformed");

  const result = runHandler("{malformed");
  expect(result.status).toBe(2);
  expect(result.stdout).toBe("");
  expect(result.stderr).toContain("Corrective action:");
});

test("wrong native hook input fails with protocol repair guidance", async () => {
  await expectExecutionFailure(
    () =>
      evaluateBashPolicy({
        ...payload(),
        hook_event_name: "PostToolUse",
      }),
    "HOOK_INPUT_INVALID"
  );
});

test("the executable owner remains silent for a valid safe non-match", () => {
  const result = runHandler(JSON.stringify(payload()));
  expect({
    status: result.status,
    stderr: result.stderr,
    stdout: result.stdout,
  }).toEqual({ status: 0, stderr: "", stdout: "" });
});

test("a proven policy match emits a native denial without exposing the secret", () => {
  const secret = "super-secret-value-1234567890";
  const result = runHandler(
    JSON.stringify(payload(`curl --token ${secret} https://example.com`))
  );
  const output = JSON.parse(result.stdout);
  expect(output.hookSpecificOutput).toMatchObject({
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
  });
  expect(output.hookSpecificOutput.permissionDecisionReason).toContain(
    "Secret material"
  );
  expect(output.hookSpecificOutput.permissionDecisionReason).toContain(
    "Corrective action:"
  );
  expect(JSON.stringify(output)).not.toContain(secret);
});

test("parses once, continues after one policy failure, and reports a later denial", async () => {
  let parseCount = 0;
  const evaluated = [];
  const message = await expectExecutionFailure(
    () =>
      evaluateBashPolicy(payload(), {
        checks: [
          {
            code: "SQL_PARSE_FAILED",
            evaluate() {
              evaluated.push("sql");
              throw new Error("private parser detail");
            },
            source: "sql-policy.mjs",
          },
          {
            code: "UNCLASSIFIED_HOOK_FAILURE",
            evaluate() {
              evaluated.push("later");
              return [
                "BLOCKED: later proven policy match.",
                "Corrective action: Use the approved safe form.",
              ].join("\n");
            },
            source: "later-policy.mjs",
          },
        ],
        parse() {
          parseCount += 1;
          return emptyBash;
        },
      }),
    "SQL_PARSE_FAILED"
  );

  expect(parseCount).toBe(1);
  expect(evaluated).toEqual(["sql", "later"]);
  expect(message).toContain("later proven policy match");
  expect(message).not.toContain("private parser detail");
});

test("parser dependency failures fail execution with corrective guidance", async () => {
  const parserError = new Error("untrusted parser detail");
  parserError.code = "AST_GREP_TIMEOUT";
  parserError.safeCause = "ast-grep exceeded its 5000ms parser deadline";
  const message = await expectExecutionFailure(
    () =>
      evaluateBashPolicy(payload(), {
        parse() {
          throw parserError;
        },
      }),
    "AST_GREP_TIMEOUT"
  );
  expect(message).not.toContain("untrusted parser detail");
});

test("unrelated commands do not inspect Git or filesystem state", () => {
  const context = {
    bash: Object.freeze({
      ...emptyBash,
      invocations: Object.freeze([
        Object.freeze({
          separatorBefore: "",
          words: Object.freeze(["git", "status", "--short"]),
        }),
      ]),
    }),
    cwd: "/path/that/does/not/exist",
  };
  expect(evaluateGitDeliveryBindingGuard(context)).toBeNull();
  expect(evaluateSourceMutationBoundaryGuard(context)).toBeNull();
});
