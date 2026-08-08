#!/usr/bin/env node
// Thin Claude Agent SDK harness for the Soleaux daemon (P5-014).
//
// Speaks `soleaux.claude-host/v1`: newline-delimited JSON on stdio against
// `soleaux-adapter-claude`'s host. The harness owns no state and no policy:
// every SessionStore call, hook event, permission request, and iterator
// message is forwarded to the Rust host, which writes through canonical
// entities and answers permission decisions. Plain JS with no dependencies
// beyond the SDK it loads at runtime (`@anthropic-ai/claude-agent-sdk`, or
// the path in CLAUDE_AGENT_SDK_PATH). Never executed by build, test, or CI —
// the Rust test suite drives a scripted fake harness instead — so running
// this against the real SDK stays a local, operator-initiated step.

import { createInterface } from "node:readline";
import { createRequire } from "node:module";
import path from "node:path";

const PROTOCOL = "soleaux.claude-host/v1";
const SDK_SPECIFIER =
  process.env.CLAUDE_AGENT_SDK_PATH ?? "@anthropic-ai/claude-agent-sdk";
const FORWARDED_HOOKS = [
  "PreToolUse",
  "PostToolUse",
  "UserPromptSubmit",
  "SessionStart",
  "SessionEnd",
  "Stop",
  "SubagentStop",
  "PreCompact",
  "Notification",
];

function send(frame) {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

let nextId = 1;
const pendingStore = new Map(); // id -> {resolve, reject}
const pendingPermissions = new Map(); // id -> resolve

function storeCall(op, body) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pendingStore.set(id, { resolve, reject });
    send({ type: "store", id, op, ...body });
  });
}

// The SessionStore adapter the SDK sees: append/load plus the optional
// discovery methods. `delete` is intentionally omitted — the SDK never
// deletes on its own and the daemon owns retention.
const sessionStore = {
  append: (key, entries) => storeCall("append", { key, entries }),
  load: (key) => storeCall("load", { key }),
  listSessions: async (projectKey) => {
    const sessions = await storeCall("list_sessions", { projectKey });
    return Array.isArray(sessions) ? sessions : [];
  },
  listSubkeys: async (key) => {
    const subkeys = await storeCall("list_subkeys", {
      projectKey: key.projectKey,
      sessionId: key.sessionId,
    });
    return Array.isArray(subkeys) ? subkeys : [];
  },
};

function buildHooks() {
  const hooks = {};
  for (const name of FORWARDED_HOOKS) {
    hooks[name] = [
      {
        hooks: [
          async (payload) => {
            send({ type: "event", event: "hook", hook: name, payload });
            return {};
          },
        ],
      },
    ];
  }
  return hooks;
}

async function canUseTool(toolName, input, context) {
  const id = nextId++;
  const decision = await new Promise((resolve) => {
    pendingPermissions.set(id, resolve);
    send({
      type: "permission_request",
      id,
      request: {
        toolName,
        input,
        suggestions: context?.suggestions ?? null,
      },
    });
  });
  if (decision?.behavior === "allow") {
    return { behavior: "allow", updatedInput: decision.updatedInput ?? input };
  }
  return {
    behavior: "deny",
    message: decision?.message ?? "denied by the Soleaux Claude host",
  };
}

let sdk = null;
let sdkName = null;
let sdkVersion = null;
let activeQuery = null;

async function loadSdk() {
  const require = createRequire(path.join(process.cwd(), "noop.js"));
  const packageJson = require(`${SDK_SPECIFIER}/package.json`);
  sdkName = packageJson.name ?? null;
  sdkVersion = packageJson.version ?? null;
  sdk = await import(require.resolve(SDK_SPECIFIER));
}

async function runQuery(requestId, params, resume) {
  const options = {
    ...(params.options && typeof params.options === "object"
      ? params.options
      : {}),
    sessionStore,
    hooks: buildHooks(),
    canUseTool,
  };
  if (resume) {
    options.resume = params.sessionId;
  }
  let responded = false;
  try {
    activeQuery = sdk.query({ prompt: params.prompt, options });
    for await (const message of activeQuery) {
      if (!responded && typeof message.session_id === "string") {
        responded = true;
        send({
          type: "response",
          id: requestId,
          ok: true,
          result: { sessionId: message.session_id },
        });
      }
      send({
        type: "event",
        event: message.type === "system" ? "system" : "message",
        payload: message,
      });
    }
    if (!responded) {
      send({
        type: "response",
        id: requestId,
        ok: false,
        error: "the query ended without reporting a session id",
      });
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    send({ type: "event", event: "system", payload: { subtype: "query_error", error: detail } });
    if (!responded) {
      send({ type: "response", id: requestId, ok: false, error: detail });
    }
  } finally {
    activeQuery = null;
  }
}

async function handleRequest(frame) {
  const { id, op, params } = frame;
  switch (op) {
    case "session.start":
      runQuery(id, params ?? {}, false);
      return;
    case "session.resume":
      runQuery(id, params ?? {}, true);
      return;
    case "session.interrupt": {
      try {
        if (activeQuery && typeof activeQuery.interrupt === "function") {
          await activeQuery.interrupt();
        }
        send({ type: "response", id, ok: true, result: {} });
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        send({ type: "response", id, ok: false, error: detail });
      }
      return;
    }
    case "shutdown":
      send({ type: "response", id, ok: true, result: {} });
      process.exit(0);
      return;
    default:
      send({ type: "response", id, ok: false, error: `unsupported op ${op}` });
  }
}

function handleFrame(frame) {
  switch (frame.type) {
    case "hello_ack":
      return;
    case "store_result": {
      const pending = pendingStore.get(frame.id);
      if (!pending) {
        return;
      }
      pendingStore.delete(frame.id);
      if (frame.ok) {
        pending.resolve(frame.result ?? null);
      } else {
        pending.reject(new Error(frame.error ?? "store call failed"));
      }
      return;
    }
    case "permission_decision": {
      const resolve = pendingPermissions.get(frame.id);
      if (resolve) {
        pendingPermissions.delete(frame.id);
        resolve(frame.decision ?? null);
      }
      return;
    }
    case "request":
      handleRequest(frame);
      return;
    default:
      process.stderr.write(`ignoring unknown host frame type ${frame.type}\n`);
  }
}

async function main() {
  try {
    await loadSdk();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    process.stderr.write(`failed to load ${SDK_SPECIFIER}: ${detail}\n`);
  }
  // An unloadable SDK still says hello: the host reads the missing version
  // and locks this connection into read-only safe mode.
  send({
    type: "hello",
    protocol: PROTOCOL,
    sdkPackage: sdkName,
    sdkVersion,
    harnessVersion: "1",
  });
  const lines = createInterface({ input: process.stdin, terminal: false });
  for await (const line of lines) {
    const text = line.trim();
    if (!text) {
      continue;
    }
    let frame;
    try {
      frame = JSON.parse(text);
    } catch {
      process.stderr.write("ignoring malformed host frame\n");
      continue;
    }
    handleFrame(frame);
  }
  process.exit(0);
}

main();
