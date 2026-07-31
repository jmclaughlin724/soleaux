// Claude PreToolUse entrypoint for its platform-owned Bash policy.
import { readFileSync } from "node:fs";

import { evaluateBashPolicy } from "./bash-policy.mjs";

function operationalNotice(error) {
  const errorName = Error.isError(error) ? error.name : "Error";
  return [
    "BLOCKED: Claude Bash policy could not complete; the command was not executed.",
    "source=.claude/hooks/guards/bash-policy-checks.mjs",
    "code=CLAUDE_BASH_POLICY_FAILED",
    `cause=${errorName} while evaluating the Bash policy`,
    "Corrective action: Run `pnpm exec vitest run .claude/hooks/__tests__/bash-policy.test.mjs`, repair `.claude/hooks/guards/bash-policy.mjs` or its installed parser dependency, then retry the Bash request.",
  ].join("\n");
}

function main() {
  try {
    const raw = readFileSync(0, "utf-8");
    const payload = raw.trim() ? JSON.parse(raw) : {};
    const result = evaluateBashPolicy(payload);
    if (result.action === "block") {
      process.stderr.write(`${result.message}\n`);
      process.exit(2);
    }
  } catch (error) {
    const notice = operationalNotice(error);
    process.stderr.write(`${notice}\n`);
    process.exitCode = 2;
  }
}

main();
