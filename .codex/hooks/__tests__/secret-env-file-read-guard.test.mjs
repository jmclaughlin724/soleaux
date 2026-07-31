import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateSecretEnvironmentFileReadGuard } from "../PreToolUse/secret-env-file-read-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

function bash(command) {
  return {
    bash: parseBash(command),
    cwd: repoRoot,
  };
}

test.each([
  ["cat .env", ".env"],
  ["cat config/.envrc", ".envrc"],
  ["grep KEY .env.production", ".env.production"],
  ["bash -lc 'rg TOKEN services/api/.env.local'", ".env.local"],
])("blocks secret environment-file reads", (command, fileName) => {
  const reason = evaluateSecretEnvironmentFileReadGuard(bash(command));
  expect(reason).toContain(fileName);
});

test.each([
  "cat .env.example",
  "cat .env.template",
  "cat .env.defaults",
  "printf .env.production",
  "git status",
])("remains silent for safe environment-file use: %s", (command) => {
  expect(evaluateSecretEnvironmentFileReadGuard(bash(command))).toBeNull();
});
