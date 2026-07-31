import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { expect, test } from "vitest";

import {
  SERVICE_ENDPOINT,
  SERVICE_LABEL,
  SERVICE_STATES,
  SOCKET_PATH,
  buildDryRunPlan,
  buildDevelopmentProgramArguments,
  buildProcessEnvironment,
  buildServiceProgramArguments,
  classifyServiceState,
  createServicePaths,
  installService,
  main,
  recoveryForServiceState,
  renderLaunchAgentPlist,
  runProcess,
  statusService,
  verifyWorkflow,
} from "../service.mjs";

const repoRoot = resolve(import.meta.dirname, "../../..");
const paths = createServicePaths({
  home: "/Users/tester",
  repoRoot,
});
const TOKEN_BYTES = 32;
const DETERMINISTIC_BYTE = 171;
const SUCCESS_EXIT_CODE = 0;
const FAILURE_EXIT_CODE = 1;
const TEST_UID = 501;
const FIRST_ITEM_INDEX = 0;
const SECOND_ITEM_INDEX = 1;
const EXPECTED_BOOTOUT_COUNT = 2;
const EXPECTED_HOOK_OUTPUT_LINES = 1;
const CONTEXT_OVERAGE_BYTES = 65_536;
const DIGEST_CHARACTER_COUNT = 64;
const EPOCH_CHARACTER_COUNT = 32;
const CATALOG_DIGEST = "a".repeat(DIGEST_CHARACTER_COUNT);
const PROCESS_EPOCH = "b".repeat(EPOCH_CHARACTER_COUNT);
const CONFIGURATION_DIGEST = "b".repeat(DIGEST_CHARACTER_COUNT);
const LEGACY_ACCOUNTS = ["claude", "codex", "controller", "opencode"];
const deterministicToken = Buffer.alloc(
  TOKEN_BYTES,
  DETERMINISTIC_BYTE
).toString("base64url");

const response = ({ body = "", contentType, sessionId, status }) => ({
  headers: {
    get: (name) => {
      if (name === "content-type") {
        return contentType ?? null;
      }
      return name === "mcp-session-id" ? (sessionId ?? null) : null;
    },
  },
  status,
  text: () => Promise.resolve(body),
});

const serviceIdentity = (configurationDigest, transport = "http") => ({
  identity: {
    catalog_digest: CATALOG_DIGEST,
    configuration_digest: configurationDigest,
    deployment_transport: transport,
    process_epoch: PROCESS_EPOCH,
  },
  product: {
    version: "0.1.0",
  },
});

const healthyHttpRequest = ({ body }) => {
  const parsed = JSON.parse(body);
  if (parsed.method === "initialize") {
    return Promise.resolve(response({ body: "{}", status: 200 }));
  }
  const liveAbout = JSON.stringify({
    id: 2,
    jsonrpc: "2.0",
    result: {
      contents: [
        {
          mimeType: "application/json",
          text: JSON.stringify(serviceIdentity(CONFIGURATION_DIGEST)),
          uri: "soleaux://about",
        },
      ],
    },
  });
  return Promise.resolve(
    response({
      body: `event: message\r\ndata: ${liveAbout}\r\n\r\n`,
      contentType: "text/event-stream; charset=utf-8",
      status: 200,
    })
  );
};

const safeSocketLstat = (path) => {
  if (path === dirname(SOCKET_PATH)) {
    return Promise.resolve({
      isDirectory: () => true,
      isSocket: () => false,
      isSymbolicLink: () => false,
      mode: 0o700,
      uid: TEST_UID,
    });
  }
  return Promise.resolve({
    isDirectory: () => false,
    isSocket: () => true,
    isSymbolicLink: () => false,
    mode: 0o600,
    uid: TEST_UID,
  });
};

const createInstallRuntime = ({
  bootstrapFailureMessage = null,
  securityCalls = null,
} = {}) => {
  const calls = [];
  return {
    calls,
    runtime: {
      access: () => Promise.resolve(),
      httpRequest: healthyHttpRequest,
      mkdir: () => Promise.resolve(),
      platform: "darwin",
      rename: () => Promise.resolve(),
      runProcess: (command, commandArguments, options) => {
        calls.push({ command, commandArguments, options });
        if (command === "/usr/bin/security") {
          securityCalls?.push({ command, commandArguments, options });
          return { code: SUCCESS_EXIT_CODE, stderr: "", stdout: "" };
        }
        if (commandArguments[FIRST_ITEM_INDEX] === "print") {
          return { code: FAILURE_EXIT_CODE, stderr: "", stdout: "" };
        }
        if (
          commandArguments[FIRST_ITEM_INDEX] === "bootstrap" &&
          bootstrapFailureMessage !== null
        ) {
          throw new Error(bootstrapFailureMessage);
        }
        return { code: SUCCESS_EXIT_CODE, stderr: "", stdout: "" };
      },
      sleep: () => Promise.resolve(),
      uid: TEST_UID,
      writeFile: () => Promise.resolve(),
    },
  };
};

test("service subprocesses fail at their owned deadline", async () => {
  await expect(
    runProcess(process.execPath, ["-e", "setInterval(() => undefined, 1000)"], {
      timeoutMs: 100,
    })
  ).rejects.toThrow("timed out after 100 ms");
});

test("service subprocesses inherit only the explicit minimum environment", () => {
  const environment = buildProcessEnvironment(
    { FASTMCP_STATELESS_HTTP: "false" },
    {
      HOME: "/Users/tester",
      PATH: "/usr/bin:/bin",
      SOLEAUX_TEST_UNLISTED_SECRET: "must-not-propagate",
    }
  );

  expect(environment).toEqual({
    FASTMCP_STATELESS_HTTP: "false",
    HOME: "/Users/tester",
    PATH: "/usr/bin:/bin",
  });
});

test("service plist directly execs the prebound-socket serve entrypoint", () => {
  const programArguments = buildServiceProgramArguments(paths);
  const plist = renderLaunchAgentPlist(paths);

  expect(programArguments).toEqual([
    resolve(repoRoot, ".venv/bin/python"),
    resolve(repoRoot, "scripts/soleaux/http_service.py"),
    "serve",
  ]);
  expect(programArguments).not.toContain("pnpm");
  expect(programArguments).not.toContain("uv");
  expect(programArguments).not.toContain("sh");
  expect(programArguments).not.toContain("--port");
  expect(programArguments).not.toContain("--host");
  expect(plist).not.toContain("<key>FASTMCP_HTTP_HOST_ORIGIN_PROTECTION</key>");
  expect(plist).not.toContain("<key>FASTMCP_STATELESS_HTTP</key>");
  expect(plist).not.toContain("<key>FASTMCP_HTTP_SESSION_IDLE_TIMEOUT</key>");
  expect(plist).toContain(
    `<key>PATH</key>\n      <string>${dirname(process.execPath)}:/usr/bin:/bin:/usr/sbin:/sbin</string>`
  );
  expect(plist).toContain(
    `<key>PYTHONPATH</key>\n      <string>${repoRoot}</string>`
  );
  expect(plist).toContain(
    "<key>FASTMCP_CHECK_FOR_UPDATES</key>\n      <string>off</string>"
  );
  expect(plist).not.toContain("SOLEAUX_ANILIZE_TEMP_TOKEN");
  expect(plist).not.toContain("Authorization");
  expect(plist).not.toContain(deterministicToken);
});

test("development command is zero-backend factory reload on port 8766", () => {
  const developmentArguments = buildDevelopmentProgramArguments(paths);

  expect(developmentArguments).toContain(
    `${paths.compositionPath}:create_development_server`
  );
  expect(developmentArguments).toContain("8766");
  expect(developmentArguments).toContain("--reload");
  expect(developmentArguments).not.toContain("--no-reload");
});

test("install bootstraps the socket service without any Keychain call", async () => {
  const { calls, runtime } = createInstallRuntime();

  const result = await installService(runtime, paths);

  expect(result).toEqual({
    endpoint: SERVICE_ENDPOINT,
    label: SERVICE_LABEL,
    legacyCredentialsRemoved: 0,
    reachable: true,
    socketPath: SOCKET_PATH,
  });
  expect(calls.some(({ command }) => command === "/usr/bin/security")).toBe(
    false
  );
  const verbs = calls.map(
    ({ commandArguments }) => commandArguments[FIRST_ITEM_INDEX]
  );
  expect(verbs).toContain("bootout");
  expect(verbs).toContain("bootstrap");
  expect(verbs).not.toContain("unsetenv");
});

test("failed bootstrap boots out its exact label", async () => {
  const { calls, runtime } = createInstallRuntime({
    bootstrapFailureMessage: "simulated bootstrap failure",
  });

  await expect(installService(runtime, paths)).rejects.toThrow(
    "simulated bootstrap failure"
  );

  const bootouts = calls.filter(
    ({ commandArguments }) => commandArguments[FIRST_ITEM_INDEX] === "bootout"
  );
  expect(bootouts.length).toBeGreaterThanOrEqual(EXPECTED_BOOTOUT_COUNT);
  for (const bootout of bootouts) {
    expect(bootout.commandArguments[SECOND_ITEM_INDEX]).toBe(
      `gui/${TEST_UID}/${SERVICE_LABEL}`
    );
  }
  expect(calls.some(({ command }) => command === "/usr/bin/security")).toBe(
    false
  );
});

test("install removes legacy credentials only after socket health", async () => {
  const securityCalls = [];
  const { calls, runtime } = createInstallRuntime({ securityCalls });

  const result = await installService(runtime, paths, {
    removeLegacyCredentialsRequested: true,
  });

  expect(result.legacyCredentialsRemoved).toBe(LEGACY_ACCOUNTS.length);
  expect(securityCalls).toHaveLength(LEGACY_ACCOUNTS.length);
  for (const { commandArguments } of securityCalls) {
    expect(commandArguments[FIRST_ITEM_INDEX]).toBe("delete-generic-password");
    expect(commandArguments).toContain("-s");
    expect(commandArguments).toContain(SERVICE_LABEL);
  }
  const bootstrapIndex = calls.findIndex(
    ({ commandArguments }) => commandArguments[FIRST_ITEM_INDEX] === "bootstrap"
  );
  const firstSecurityIndex = calls.findIndex(
    ({ command }) => command === "/usr/bin/security"
  );
  expect(firstSecurityIndex).toBeGreaterThan(bootstrapIndex);
});

test("legacy credential removal is gated to install", async () => {
  await expect(
    main(["uninstall", "--remove-legacy-credentials", "--dry-run"])
  ).rejects.toThrow("--remove-legacy-credentials is valid only with install");
});

test("status compares source and live identities through the socket probe", async () => {
  const installedPlist = renderLaunchAgentPlist(paths);
  const sourceDescription = JSON.stringify({
    data: serviceIdentity(CONFIGURATION_DIGEST, "stdio"),
    status: "ok",
  });
  const runtime = {
    access: () => Promise.resolve(),
    httpRequest: healthyHttpRequest,
    lstat: safeSocketLstat,
    platform: "darwin",
    readFile: () => Promise.resolve(installedPlist),
    runProcess: (command, commandArguments) => {
      if (command === paths.soleauxPath) {
        return Promise.resolve({
          code: SUCCESS_EXIT_CODE,
          stderr: "",
          stdout: sourceDescription,
        });
      }
      expect(command).toBe("/bin/launchctl");
      expect(commandArguments[FIRST_ITEM_INDEX]).toBe("print");
      return Promise.resolve({
        code: SUCCESS_EXIT_CODE,
        stderr: "",
        stdout: "",
      });
    },
    tcpConnectable: () => Promise.resolve(false),
    uid: TEST_UID,
  };

  const result = await statusService(runtime, paths);

  expect(result.state).toBe(SERVICE_STATES.HEALTHY);
  expect(result.reachable).toBe(true);
  expect(result.identity.parity).toEqual({
    catalog: true,
    configuration: true,
    product: true,
    transport: true,
    gitSha: null,
    installSource: null,
    matches: true,
  });
  expect(result.identity.desired.transport).toBe("http");
  expect(result.identity.live.processEpoch).toBe(PROCESS_EPOCH);
  expect(result.installation.matches).toBe(true);
  expect(result.socket).toEqual({
    path: SOCKET_PATH,
    reason: null,
    state: "safe",
  });
  expect(result.tcpListenerPresent).toBe(false);
  expect(result.recovery).toBeNull();
  expect(JSON.stringify(result)).not.toContain(deterministicToken);
});

test("status marks a live TCP listener as stale drift", async () => {
  const installedPlist = renderLaunchAgentPlist(paths);
  const sourceDescription = JSON.stringify({
    data: serviceIdentity(CONFIGURATION_DIGEST, "stdio"),
    status: "ok",
  });
  const runtime = {
    access: () => Promise.resolve(),
    httpRequest: healthyHttpRequest,
    lstat: safeSocketLstat,
    platform: "darwin",
    readFile: () => Promise.resolve(installedPlist),
    runProcess: (command) => {
      if (command === paths.soleauxPath) {
        return Promise.resolve({
          code: SUCCESS_EXIT_CODE,
          stderr: "",
          stdout: sourceDescription,
        });
      }
      return Promise.resolve({
        code: SUCCESS_EXIT_CODE,
        stderr: "",
        stdout: "",
      });
    },
    tcpConnectable: () => Promise.resolve(true),
    uid: TEST_UID,
  };

  const result = await statusService(runtime, paths);

  expect(result.state).toBe(SERVICE_STATES.STALE);
  expect(result.tcpListenerPresent).toBe(true);
});

test("status reports an unsafe socket occupant", async () => {
  const installedPlist = renderLaunchAgentPlist(paths);
  const sourceDescription = JSON.stringify({
    data: serviceIdentity(CONFIGURATION_DIGEST, "stdio"),
    status: "ok",
  });
  const runtime = {
    access: () => Promise.resolve(),
    httpRequest: healthyHttpRequest,
    lstat: (path) =>
      path === dirname(SOCKET_PATH)
        ? safeSocketLstat(path)
        : Promise.resolve({
            isDirectory: () => false,
            isSocket: () => true,
            isSymbolicLink: () => false,
            mode: 0o666,
            uid: TEST_UID,
          }),
    platform: "darwin",
    readFile: () => Promise.resolve(installedPlist),
    runProcess: (command) => {
      if (command === paths.soleauxPath) {
        return Promise.resolve({
          code: SUCCESS_EXIT_CODE,
          stderr: "",
          stdout: sourceDescription,
        });
      }
      return Promise.resolve({
        code: SUCCESS_EXIT_CODE,
        stderr: "",
        stdout: "",
      });
    },
    tcpConnectable: () => Promise.resolve(false),
    uid: TEST_UID,
  };

  const result = await statusService(runtime, paths);

  expect(result.state).toBe(SERVICE_STATES.STALE);
  expect(result.socket).toEqual({
    path: SOCKET_PATH,
    reason: "socket-mode-is-not-0600",
    state: "unsafe",
  });
});

test("service states classify loaded, socket, parity, and listener drift", () => {
  const healthy = {
    endpointState: "reachable",
    installed: true,
    installationMatches: true,
    loaded: true,
    parity: true,
    socketState: "safe",
    tcpListenerPresent: false,
  };

  expect(classifyServiceState(healthy)).toBe(SERVICE_STATES.HEALTHY);
  expect(classifyServiceState({ ...healthy, loaded: false })).toBe(
    SERVICE_STATES.STOPPED
  );
  expect(
    classifyServiceState({ ...healthy, installed: false, loaded: false })
  ).toBe(SERVICE_STATES.ABSENT);
  expect(classifyServiceState({ ...healthy, endpointState: "stopped" })).toBe(
    SERVICE_STATES.STOPPED
  );
  expect(classifyServiceState({ ...healthy, parity: false })).toBe(
    SERVICE_STATES.STALE
  );
  expect(classifyServiceState({ ...healthy, installationMatches: false })).toBe(
    SERVICE_STATES.STALE
  );
  expect(classifyServiceState({ ...healthy, socketState: "missing" })).toBe(
    SERVICE_STATES.STALE
  );
  expect(classifyServiceState({ ...healthy, socketState: "unsafe" })).toBe(
    SERVICE_STATES.STALE
  );
  expect(classifyServiceState({ ...healthy, tcpListenerPresent: true })).toBe(
    SERVICE_STATES.STALE
  );
  expect(classifyServiceState({ ...healthy, endpointState: "stale" })).toBe(
    SERVICE_STATES.STALE
  );
});

test("every nonhealthy recovery remains gated behind acceptance and approval", () => {
  expect(recoveryForServiceState(SERVICE_STATES.HEALTHY)).toBeNull();
  for (const state of [SERVICE_STATES.ABSENT, SERVICE_STATES.STOPPED]) {
    expect(recoveryForServiceState(state)).toEqual({
      action: "install",
      arguments: ["install"],
      requiresCr10: true,
      requiresExplicitAuthorization: true,
    });
  }
  expect(recoveryForServiceState(SERVICE_STATES.STALE)).toEqual({
    action: "restart",
    arguments: ["restart"],
    requiresCr10: true,
    requiresExplicitAuthorization: true,
  });
  expect(
    recoveryForServiceState(SERVICE_STATES.STALE, {
      installationMatches: false,
    })
  ).toEqual({
    action: "reinstall",
    arguments: ["install"],
    requiresCr10: true,
    requiresExplicitAuthorization: true,
  });
});

const hookOutput = (additionalContext) =>
  `${JSON.stringify({
    hookSpecificOutput: {
      additionalContext,
      hookEventName: "UserPromptSubmit",
    },
  })}\n`;

const VALID_CONTEXT = [
  "# Soleaux task context",
  "",
  "## Canonical owners (1)",
  "## Consumers (0)",
  "## Conflicts (0)",
  "## Validation routes (1)",
].join("\n");

const createVerifyRuntime = ({ stdout }) => ({
  lstat: safeSocketLstat,
  platform: "darwin",
  runProcess: (command, commandArguments, options) => {
    expect(command).toBe(paths.repoPythonPath);
    expect(commandArguments).toEqual([
      resolve(repoRoot, ".codex/hooks/UserPromptSubmit/soleaux_context.py"),
    ]);
    expect(JSON.parse(options.input)).toMatchObject({
      cwd: repoRoot,
      hook_event_name: "UserPromptSubmit",
    });
    return { code: SUCCESS_EXIT_CODE, stderr: "", stdout };
  },
  uid: TEST_UID,
});

test("verify drives the real hook and validates one bounded context", async () => {
  const runtime = createVerifyRuntime({ stdout: hookOutput(VALID_CONTEXT) });

  const result = await verifyWorkflow(runtime, paths);

  expect(result.verified).toBe(true);
  expect(result.hookOutputLines).toBe(EXPECTED_HOOK_OUTPUT_LINES);
  expect(result.additionalContextBytes).toBe(
    Buffer.byteLength(VALID_CONTEXT, "utf-8")
  );
  expect(result.socketPath).toBe(SOCKET_PATH);
});

test("verify rejects an oversized hook context", async () => {
  const runtime = createVerifyRuntime({
    stdout: hookOutput(`${VALID_CONTEXT}${"x".repeat(CONTEXT_OVERAGE_BYTES)}`),
  });

  await expect(verifyWorkflow(runtime, paths)).rejects.toThrow(
    "exceeded the 65535-byte host envelope"
  );
});

test("verify rejects a hook context missing required sections", async () => {
  const runtime = createVerifyRuntime({
    stdout: hookOutput("# Soleaux task context\n"),
  });

  await expect(verifyWorkflow(runtime, paths)).rejects.toThrow(
    "missing required sections"
  );
});

test("verify refuses to run against an unsafe socket", async () => {
  const runtime = {
    ...createVerifyRuntime({ stdout: hookOutput(VALID_CONTEXT) }),
    lstat: () =>
      Promise.resolve({
        isDirectory: () => false,
        isSocket: () => false,
        isSymbolicLink: () => false,
        mode: 0o644,
        uid: TEST_UID,
      }),
  };

  await expect(verifyWorkflow(runtime, paths)).rejects.toThrow(
    "not ready for verification"
  );
});

test("every dry-run service verb remains scoped and secret-free", () => {
  for (const command of [
    "dev",
    "install",
    "restart",
    "status",
    "uninstall",
    "verify",
  ]) {
    const plan = buildDryRunPlan(command, paths, {
      removeLegacyCredentialsRequested: command === "install",
    });
    const serialized = JSON.stringify(plan);

    expect(plan.label).toBe(SERVICE_LABEL);
    expect(plan.endpoint).toBe(SERVICE_ENDPOINT);
    expect(plan.launchAgentPath).toBe(
      `/Users/tester/Library/LaunchAgents/${SERVICE_LABEL}.plist`
    );
    expect(plan.socket.path).toBe(SOCKET_PATH);
    expect(plan.legacyCredentials.removed).toBe(command === "install");
    expect(serialized).not.toContain(deterministicToken);
    expect(serialized).not.toContain("client_accounts");
  }
});

test("service source carries no dogfood tokens", async () => {
  const serviceSource = await readFile(
    resolve(repoRoot, "scripts/soleaux/service.mjs"),
    "utf-8"
  );

  expect(serviceSource).not.toContain(deterministicToken);
});
