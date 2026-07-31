const EXPECTED_EVENT = "SubagentStop";

async function readInput() {
  process.stdin.setEncoding("utf-8");
  let source = "";
  for await (const chunk of process.stdin) {
    source += chunk;
  }
  const input = JSON.parse(source);
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    throw new TypeError("stdin must contain one JSON object");
  }
  return input;
}
try {
  const input = await readInput();
  if (input.hook_event_name !== EXPECTED_EVENT) {
    throw new TypeError(`expected hook_event_name ${EXPECTED_EVENT}`);
  }
  // Exit 0 without stdout allows the subagent to stop.
} catch (error) {
  const message = Error.isError(error) ? error.message : String(error);
  process.stderr.write(`${EXPECTED_EVENT} hook input error: ${message}\n`);
  process.exitCode = 1;
}
