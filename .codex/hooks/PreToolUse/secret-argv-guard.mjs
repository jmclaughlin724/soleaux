/* eslint-disable no-magic-numbers -- Bash argv indexes and URL token boundaries are protocol mechanics. */
import { environmentAssignment } from "./bash-ast.mjs";

const SECRET_FLAG_NAMES = new Set([
  "--api-key",
  "--password",
  "--secret",
  "--token",
  "--value",
]);
const SECRET_NAME_FRAGMENTS = [
  "SECRET",
  "TOKEN",
  "PASSWORD",
  "KEY",
  "CREDENTIAL",
  "PASSWD",
  "PASS",
];
const MASKED_SECRET_FRAGMENTS = [
  "[redacted]",
  "<password>",
  "<secret>",
  "<token>",
  "***",
  "xxx",
];
const MINIMUM_LITERAL_SECRET_LENGTH = 16;
const MINIMUM_DATABASE_URL_PASSWORD_LENGTH = 12;

function looksMasked(value) {
  const lower = value.toLowerCase();
  return MASKED_SECRET_FRAGMENTS.some((fragment) => lower.includes(fragment));
}

function isLiteralSecretValue(value, minimumLength) {
  return (
    value.length >= minimumLength &&
    !looksMasked(value) &&
    !value.startsWith("$")
  );
}

function secretName(name) {
  const upper = name.toUpperCase();
  return SECRET_NAME_FRAGMENTS.some((fragment) => upper.includes(fragment));
}

function hasDatabaseUrlWithInlinePassword(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (!["mysql:", "postgres:", "postgresql:"].includes(url.protocol)) {
    return false;
  }
  return isLiteralSecretValue(
    decodeURIComponent(url.password),
    MINIMUM_DATABASE_URL_PASSWORD_LENGTH
  );
}

function flagSecret(words, index) {
  const word = words[index] ?? "";
  const separator = word.indexOf("=");
  if (separator > 0) {
    const name = word.slice(0, separator);
    return SECRET_FLAG_NAMES.has(name)
      ? { name, value: word.slice(separator + 1) }
      : null;
  }
  if (SECRET_FLAG_NAMES.has(word)) {
    return { name: word, value: words[index + 1] ?? "" };
  }
  return null;
}

export function evaluateSecretArgvGuard({ bash }) {
  const matches = new Set();
  for (const { words } of bash.invocations) {
    for (let index = 0; index < words.length; index += 1) {
      const word = words[index] ?? "";
      if (hasDatabaseUrlWithInlinePassword(word)) {
        matches.add("database connection URL with an inline password");
      }
      const assignment = environmentAssignment(word);
      if (
        assignment &&
        secretName(assignment.name) &&
        isLiteralSecretValue(assignment.value, MINIMUM_LITERAL_SECRET_LENGTH)
      ) {
        matches.add(`inline secret environment variable ${assignment.name}`);
      }
      const flag = flagSecret(words, index);
      if (
        flag &&
        isLiteralSecretValue(flag.value, MINIMUM_LITERAL_SECRET_LENGTH)
      ) {
        matches.add(`literal value for ${flag.name}`);
      }
    }
  }
  if (matches.size === 0) {
    return null;
  }
  return [
    "BLOCKED: Secret material detected in Bash argv.",
    `Matched patterns: ${[...matches].join(", ")}`,
    "Corrective action: Remove the literal from argv and pass it through an environment-variable reference, stdin, or an approved secret-manager handoff, then retry.",
  ].join("\n");
}
