import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateSecretArgvGuard } from "../PreToolUse/secret-argv-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const secret = "super-secret-value-1234567890";
const databasePasswordName = ["DATABASE", "PASSWORD"].join("_");

function bash(command) {
  return {
    bash: parseBash(command),
    cwd: repoRoot,
  };
}

test.each([
  `curl --api-key ${secret} https://api.example.com`,
  `${databasePasswordName}=${secret} node server.js`,
  `psql postgres://user:${secret}@host/db`,
  `bash -lc 'curl --token ${secret} https://api.example.com'`,
])("blocks literal secret material in structured Bash argv", (command) => {
  const reason = evaluateSecretArgvGuard(bash(command));
  expect(reason).toContain("Secret material");
  expect(reason).not.toContain(secret);
});

test.each([
  "curl --token $API_TOKEN https://api.example.com",
  "curl --token '[redacted]' https://api.example.com",
  `${databasePasswordName}=$${databasePasswordName} node server.js`,
  "psql postgres://user:%3Credacted%3E@host/db",
  "echo safe",
])("remains silent for non-literal or non-secret argv: %s", (command) => {
  expect(evaluateSecretArgvGuard(bash(command))).toBeNull();
});
