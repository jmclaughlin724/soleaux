import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";

function processResult(overrides = {}) {
  return {
    signal: null,
    status: 0,
    stdout: "[]",
    ...overrides,
  };
}

function nestedShellResult(_path, _arguments, options) {
  return processResult({
    stdout: JSON.stringify([
      {
        metaVariables: {
          multi: {
            ARGUMENTS: [{ text: "-lc" }, { text: "'nested'" }],
          },
          single: {
            COMMAND: { text: "bash" },
          },
        },
        range: {
          byteOffset: {
            end: Buffer.byteLength(options.input),
            start: 0,
          },
        },
        ruleId: "bash-command",
        text: options.input,
      },
    ]),
  });
}

function expectCode(runAstGrep, code) {
  try {
    parseBash("echo safe", { runAstGrep });
  } catch (error) {
    expect(error).toMatchObject({ code });
    expect(error.safeCause).toBeTypeOf("string");
    return;
  }
  throw new Error(`expected ${code}`);
}

test.each([
  ["ETIMEDOUT", "AST_GREP_TIMEOUT"],
  ["ENOENT", "AST_GREP_NOT_FOUND"],
  ["ENOBUFS", "AST_GREP_OUTPUT_LIMIT"],
])("classifies ast-grep process error %s", (processCode, policyCode) => {
  expectCode(
    () =>
      processResult({
        error: Object.assign(new Error("private process detail"), {
          code: processCode,
        }),
        status: null,
      }),
    policyCode
  );
});

test("classifies unsuccessful ast-grep exits without exposing stderr", () => {
  expectCode(
    () =>
      processResult({
        signal: "SIGTERM",
        status: 9,
        stderr: "private command contents",
      }),
    "AST_GREP_EXIT_NONZERO"
  );
});

test.each(["not json", JSON.stringify({ unexpected: true })])(
  "classifies invalid ast-grep output",
  (stdout) => {
    expectCode(
      () =>
        processResult({
          stdout,
        }),
      "AST_GREP_INVALID_OUTPUT"
    );
  }
);

test("classifies parser-reported unsupported Bash syntax", () => {
  expectCode(
    () =>
      processResult({
        stdout: JSON.stringify([{ ruleId: "bash-parse-error" }]),
      }),
    "BASH_SYNTAX_UNSUPPORTED"
  );
});

test("classifies excessive nested shell depth", () => {
  expect(() => parseBash("bash", { runAstGrep: nestedShellResult })).toThrow(
    expect.objectContaining({ code: "BASH_NESTING_LIMIT" })
  );
});
