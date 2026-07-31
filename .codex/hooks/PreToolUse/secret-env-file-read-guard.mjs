import { basename } from "node:path";

import { commandArguments, commandName } from "./bash-ast.mjs";

const READ_COMMANDS = new Set([
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
const SAFE_ENVIRONMENT_TEMPLATES = new Set([
  ".env.default",
  ".env.defaults",
  ".env.example",
  ".env.sample",
  ".env.template",
]);
const EMPTY_LENGTH = 0;

function environmentFileName(value) {
  const fileName = basename(value);
  return fileName === ".env" ||
    fileName === ".envrc" ||
    fileName.startsWith(".env.")
    ? fileName
    : "";
}

export function evaluateSecretEnvironmentFileReadGuard({ bash }) {
  const matches = new Set();
  for (const { words } of bash.invocations) {
    if (!READ_COMMANDS.has(commandName(words))) {
      continue;
    }
    for (const argument of commandArguments(words)) {
      const fileName = environmentFileName(argument);
      if (fileName && !SAFE_ENVIRONMENT_TEMPLATES.has(fileName)) {
        matches.add(fileName);
      }
    }
  }
  if (matches.size === EMPTY_LENGTH) {
    return null;
  }
  return [
    "BLOCKED: Bash read or search of secret-bearing environment files is prohibited.",
    `Matched environment files: ${[...matches].join(", ")}`,
    "Corrective action: Use an environment-variable reference, inspect a non-secret `.env.example`/`.env.template`, or retrieve the value through the approved secret manager.",
  ].join("\n");
}
