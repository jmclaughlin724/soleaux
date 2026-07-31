#!/usr/bin/env node
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import { pathToFileURL } from "node:url";
import { resolveDaemonOrigin } from "@soleaux/protocol/env";

const EXIT_SUCCESS = 0;
const EXIT_FAILURE = 1;
const JSON_INDENT = 2;
const CLI_ARGUMENT_OFFSET = 2;
const ENTRYPOINT_ARGUMENT_INDEX = 1;
const HTTP_NO_CONTENT = 204;

const DAEMON = `${resolveDaemonOrigin(process.env.SOLEAUX_DAEMON_URL)}/api/v1`;

async function request(path, options = {}) {
  const response = await fetch(`${DAEMON}${path}`, {
    ...options,
    headers: { "content-type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  if (response.status === HTTP_NO_CONTENT) {
    return null;
  }
  return response.json();
}

async function readPayload(value) {
  if (!value) {
    throw new Error("A JSON file, JSON string, or '-' for stdin is required");
  }
  if (value === "-") {
    const chunks = await Array.fromAsync(process.stdin);
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  }
  if (value.trim().startsWith("{")) {
    return JSON.parse(value);
  }
  return JSON.parse(await readFile(value, "utf-8"));
}

function providerFor(command) {
  if (command === "claude") {
    return "anthropic";
  }
  if (command === "codex") {
    return "openai";
  }
  return "custom";
}

function launchChild(command, arguments_) {
  const providerId = providerFor(command);
  const sessionId = randomUUID();
  const startedAtUnixMs = Date.now();
  const workingDirectory = process.cwd();
  const child = spawn(command, arguments_, {
    cwd: workingDirectory,
    env: {
      ...process.env,
      SOLEAUX_SESSION_ID: sessionId,
      SOLEAUX_PROVIDER_ID: providerId,
      SOLEAUX_SESSION_STARTED_AT: String(startedAtUnixMs),
    },
    stdio: "inherit",
  });

  child.once("spawn", async () => {
    try {
      await request("/sessions", {
        method: "POST",
        body: JSON.stringify({
          id: sessionId,
          providerId,
          displayName: `${providerId} · ${basename(workingDirectory)}`,
          rootPid: child.pid,
          rootStartedAtUnixMs: startedAtUnixMs,
          workingDirectory,
          repositoryRoot: workingDirectory,
          modelId: process.env.SOLEAUX_MODEL_ID,
          contextWindowTokens: process.env.SOLEAUX_CONTEXT_WINDOW_TOKENS
            ? Number(process.env.SOLEAUX_CONTEXT_WINDOW_TOKENS)
            : undefined,
        }),
      });
    } catch (error) {
      console.error(
        "Soleaux daemon registration failed:",
        Error.isError(error) ? error.message : error
      );
    }
  });

  child.once("exit", async (code, signal) => {
    try {
      await request(`/sessions/${sessionId}/end`, { method: "POST" });
    } catch {
      // Session end is best-effort; the daemon may already be gone.
    }
    if (signal) {
      process.kill(process.pid, signal);
    }
    process.exit(code ?? EXIT_FAILURE);
  });

  child.once("error", (error) => {
    console.error(`Unable to launch ${command}:`, error.message);
    process.exit(EXIT_FAILURE);
  });
}

async function main() {
  const [command, ...arguments_] = process.argv.slice(CLI_ARGUMENT_OFFSET);

  if (!command || command === "help" || command === "--help") {
    console.log(`Usage:
  soleaux <claude|codex|command> [args...]
  soleaux usage <json-file|json|->
  soleaux quota <json-file|json|->
  soleaux status
  soleaux sessions`);
    process.exit(command ? EXIT_SUCCESS : EXIT_FAILURE);
  }

  if (command === "usage" || command === "quota") {
    const [payloadArgument] = arguments_;
    const payload = await readPayload(payloadArgument);
    const endpoint = command === "usage" ? "/usage/events" : "/quotas";
    const result = await request(endpoint, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    console.log(JSON.stringify(result, null, JSON_INDENT));
    return;
  }

  if (command === "status") {
    const [health, system, usage, quotas] = await Promise.all([
      request("/health"),
      request("/system"),
      request("/usage/summary"),
      request("/quotas"),
    ]);
    console.log(
      JSON.stringify({ health, system, usage, quotas }, null, JSON_INDENT)
    );
    return;
  }

  if (command === "sessions") {
    console.log(JSON.stringify(await request("/sessions"), null, JSON_INDENT));
    return;
  }

  launchChild(command, arguments_);
}

const runCli = async () => {
  try {
    await main();
  } catch (error) {
    const message = Error.isError(error)
      ? error.message
      : "unknown telemetry-cli failure";
    process.stderr.write(`${message}\n`);
    process.exitCode = EXIT_FAILURE;
  }
};

if (
  process.argv[ENTRYPOINT_ARGUMENT_INDEX] !== undefined &&
  import.meta.url ===
    pathToFileURL(process.argv[ENTRYPOINT_ARGUMENT_INDEX]).href
) {
  void runCli();
}
