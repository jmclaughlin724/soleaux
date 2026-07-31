import { spawn } from "node:child_process";
import { once } from "node:events";
import { constants as fsConstants, readFileSync, realpathSync } from "node:fs";
import {
  access,
  lstat,
  mkdir,
  readFile,
  rename,
  unlink,
  writeFile,
} from "node:fs/promises";
import { request as httpRequest } from "node:http";
import { createConnection } from "node:net";
import { homedir, platform } from "node:os";
import { dirname, delimiter as pathDelimiter, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const resolveDeploymentConfigPath = () => {
  const configFlagIndex = process.argv.indexOf("--config");
  if (configFlagIndex !== -1 && process.argv[configFlagIndex + 1] !== undefined) {
    return resolve(process.argv[configFlagIndex + 1]);
  }
  if (process.env.SOLEAUX_DEPLOYMENT_CONFIG !== undefined) {
    return resolve(process.env.SOLEAUX_DEPLOYMENT_CONFIG);
  }
  return resolve(import.meta.dirname, "deployment.json");
};

const DEPLOYMENT_CONFIG_PATH = resolveDeploymentConfigPath();
const SOCKET_HOSTNAME = "soleaux.local";
const MAX_SOCKET_PATH_CHARACTERS = 100;
const EMPTY_COLLECTION_SIZE = 0;

const requireString = (value, label) => {
  if (typeof value !== "string" || value.length === EMPTY_COLLECTION_SIZE) {
    throw new TypeError(`${label} must be a nonempty string`);
  }
  return value;
};

const loadDeploymentConfig = () => {
  const value = JSON.parse(readFileSync(DEPLOYMENT_CONFIG_PATH, "utf-8"));
  const isRecord =
    value !== null && typeof value === "object" && !Array.isArray(value);
  if (!isRecord || value.schema_version !== "soleaux.local-deployment/v2") {
    throw new TypeError("deployment.json has an unsupported schema");
  }
  const endpoint = new URL(requireString(value.endpoint, "endpoint"));
  if (
    endpoint.protocol !== "http:" ||
    endpoint.hostname !== SOCKET_HOSTNAME ||
    endpoint.username ||
    endpoint.password
  ) {
    throw new TypeError(
      `deployment endpoint must be a credential-free http://${SOCKET_HOSTNAME} URL`
    );
  }
  const socketRelativePath = requireString(
    value.socket_relative_path,
    "socket_relative_path"
  );
  if (
    socketRelativePath.startsWith("/") ||
    socketRelativePath.split("/").includes("..")
  ) {
    throw new TypeError(
      "socket_relative_path must stay relative to the home directory"
    );
  }
  const socketPath = resolve(homedir(), socketRelativePath);
  if (socketPath.length > MAX_SOCKET_PATH_CHARACTERS) {
    throw new TypeError(
      "the resolved Soleaux socket path exceeds the AF_UNIX limit"
    );
  }
  let workspaceRoot = null;
  if (value.workspace_root !== undefined && value.workspace_root !== null) {
    const candidate = requireString(value.workspace_root, "workspace_root");
    workspaceRoot = candidate.startsWith("/")
      ? candidate
      : resolve(dirname(DEPLOYMENT_CONFIG_PATH), candidate);
  }
  return Object.freeze({
    endpoint,
    legacyTokenEnvironment:
      typeof value.legacy_token_environment === "string" &&
      value.legacy_token_environment.length > EMPTY_COLLECTION_SIZE
        ? value.legacy_token_environment
        : null,
    serviceLabel: requireString(value.service_label, "service_label"),
    socketPath,
    workspaceRoot,
  });
};

const DEPLOYMENT = loadDeploymentConfig();
export const SERVICE_LABEL = DEPLOYMENT.serviceLabel;
export const SERVICE_ENDPOINT = DEPLOYMENT.endpoint.href;
export const SOCKET_PATH = DEPLOYMENT.socketPath;

const SERVICE_PATH = DEPLOYMENT.endpoint.pathname;
const SERVICE_TRANSPORT = DEPLOYMENT.endpoint.protocol.replace(":", "");
const DEVELOPMENT_HOST = "127.0.0.1";
const DEVELOPMENT_PORT = 8766;
const LEGACY_TCP_PORT = 8765;
const LEGACY_CLIENT_ACCOUNTS = Object.freeze([
  "claude",
  "codex",
  "controller",
  "opencode",
]);
const PLIST_MODE = 0o600;
const SOCKET_MODE = 0o600;
const SOCKET_DIRECTORY_MODE = 0o700;
const MODE_BASE = 0o1000;
const HEALTH_ATTEMPTS = 60;
const HEALTH_RETRY_DELAY_MS = 250;
const HTTP_TIMEOUT_MS = 5000;
const PROCESS_TIMEOUT_MS = 15_000;
const HTTP_OK = 200;
const EVENT_STREAM_MEDIA_TYPE = "text/event-stream";
const EVENT_STREAM_DATA_PREFIX = "data:";
const SUCCESS_EXIT_CODE = 0;
const FAILURE_EXIT_CODE = 1;
const FINAL_HEALTH_ATTEMPT = 1;
const HEALTH_ATTEMPT_DECREMENT = 1;
const FIRST_MEDIA_TYPE_COUNT = 1;
const FIRST_ITEM_INDEX = 0;
const ENTRYPOINT_ARGUMENT_INDEX = 1;
const CLI_ARGUMENT_OFFSET = 2;
const JSON_INDENT_SPACES = 2;
const KEYCHAIN_ITEM_NOT_FOUND = 44;
const SECURITY_COMMAND = "/usr/bin/security";
const LAUNCHCTL_COMMAND = "/bin/launchctl";
const PRODUCT_ROOT = resolve(import.meta.dirname, "../..");
const WORKSPACE_ROOT = DEPLOYMENT.workspaceRoot ?? PRODUCT_ROOT;
const SERVICE_EXECUTABLE_PATH = [
  dirname(process.execPath),
  "/usr/bin",
  "/bin",
  "/usr/sbin",
  "/sbin",
].join(pathDelimiter);
const MCP_PROTOCOL_VERSION = "2025-11-25";
const ABOUT_RESOURCE_URI = "soleaux://about";
const LEGACY_DOMAIN_TOKEN_ENVIRONMENT = DEPLOYMENT.legacyTokenEnvironment;
const HOOK_SCRIPT_PATH = ".codex/hooks/UserPromptSubmit/soleaux_context.py";
const HOOK_EVENT_NAME = "UserPromptSubmit";
const HOOK_TIMEOUT_MS = 90_000;
const EXPECTED_HOOK_OUTPUT_LINES = 1;
const MAX_CONTEXT_PAYLOAD_BYTES = 65_535;
const VERIFY_OBJECTIVE =
  "Audit the Soleaux context delivery workflow and identify its canonical owners and validation path.";
const REQUIRED_CONTEXT_MARKERS = Object.freeze([
  "# Soleaux task context",
  "Canonical owners",
  "Consumers",
  "Conflicts",
  "Validation routes",
]);
const PROCESS_ENVIRONMENT_NAMES = Object.freeze([
  "HOME",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "PATH",
  "PATHEXT",
  "SYSTEMROOT",
  "TEMP",
  "TMP",
  "TMPDIR",
  "USERPROFILE",
  "WINDIR",
]);
/* eslint-disable unicorn/prefer-https -- Apple publishes this exact plist DTD identifier. */
const APPLE_PLIST_DOCTYPE =
  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">';
/* eslint-enable unicorn/prefer-https */

class ServiceControllerError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "ServiceControllerError";
  }
}

export const SERVICE_STATES = Object.freeze({
  ABSENT: "absent",
  HEALTHY: "healthy",
  STALE: "stale",
  STOPPED: "stopped",
});

const ENDPOINT_STATES = Object.freeze({
  REACHABLE: "reachable",
  STALE: "stale",
  STOPPED: "stopped",
});

const SOCKET_STATES = Object.freeze({
  MISSING: "missing",
  SAFE: "safe",
  UNSAFE: "unsafe",
});

export const buildProcessEnvironment = (
  additions = {},
  inherited = process.env
) => ({
  ...Object.fromEntries(
    PROCESS_ENVIRONMENT_NAMES.flatMap((name) =>
      inherited[name] === undefined ? [] : [[name, inherited[name]]]
    )
  ),
  ...additions,
});

export const runProcess = async (
  command,
  commandArguments,
  {
    allowFailure = false,
    environment = {},
    input,
    timeoutMs = PROCESS_TIMEOUT_MS,
  } = {}
) => {
  const child = spawn(command, commandArguments, {
    env: buildProcessEnvironment(environment),
    stdio: ["pipe", "pipe", "pipe"],
  });
  let didTimeout = false;
  const timeout = setTimeout(() => {
    didTimeout = true;
    child.kill("SIGKILL");
  }, timeoutMs);
  timeout.unref();
  let stdout = "";
  let stderr = "";

  child.stdout.setEncoding("utf-8");
  child.stderr.setEncoding("utf-8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  child.stdin.on("error", () => {
    // The close status owns the outcome if a child exits before reading stdin.
  });
  if (input === undefined) {
    child.stdin.end();
  } else {
    child.stdin.end(`${input}\n`);
  }

  let closeResult;
  try {
    closeResult = await once(child, "close");
  } catch {
    throw new ServiceControllerError(`${command} could not be started`);
  } finally {
    clearTimeout(timeout);
  }
  if (didTimeout) {
    throw new ServiceControllerError(
      `${command} timed out after ${timeoutMs} ms`
    );
  }
  const [code] = closeResult;
  const exitCode = code ?? FAILURE_EXIT_CODE;
  if (!allowFailure && exitCode !== SUCCESS_EXIT_CODE) {
    throw new ServiceControllerError(`${command} exited with code ${exitCode}`);
  }
  return { code: exitCode, stderr, stdout };
};

const runForeground = async (command, commandArguments, environment = {}) => {
  const executableDirectory = dirname(command);
  const childEnvironment = buildProcessEnvironment(environment);
  const inheritedPath = childEnvironment.PATH;
  const childPath =
    inheritedPath === undefined
      ? executableDirectory
      : `${executableDirectory}${pathDelimiter}${inheritedPath}`;
  const child = spawn(command, commandArguments, {
    env: {
      ...childEnvironment,
      PATH: childPath,
    },
    stdio: "inherit",
  });
  let closeResult;
  try {
    closeResult = await once(child, "close");
  } catch {
    throw new ServiceControllerError(`${command} could not be started`);
  }
  const [code, signal] = closeResult;
  if (code === SUCCESS_EXIT_CODE) {
    return { code, signal };
  }
  const exitDescription = signal ?? `code ${code ?? FAILURE_EXIT_CODE}`;
  throw new ServiceControllerError(
    `${command} stopped with ${exitDescription}`
  );
};

const collectResponse = (response, _resolve) => {
  const chunks = [];
  response.on("data", (chunk) => {
    chunks.push(chunk);
  });
  response.on("end", () => {
    _resolve({
      headers: new Headers(response.headers),
      status: response.statusCode,
      text: () => Promise.resolve(Buffer.concat(chunks).toString("utf-8")),
    });
  });
};

const nodeHttpRequest = ({
  body,
  headers,
  method,
  path,
  socketPath,
  timeoutMs = HTTP_TIMEOUT_MS,
}) =>
  new Promise((_resolve, _reject) => {
    const request = httpRequest(
      {
        headers: { host: SOCKET_HOSTNAME, ...headers },
        method,
        path,
        socketPath,
      },
      (response) => collectResponse(response, _resolve)
    );
    request.on("error", _reject);
    request.setTimeout(timeoutMs, () => {
      request.destroy(new ServiceControllerError("socket request timed out"));
    });
    if (body !== undefined) {
      request.write(body);
    }
    request.end();
  });

const nodeTcpConnectable = ({ host, port, timeoutMs = HTTP_TIMEOUT_MS }) =>
  new Promise((_resolve) => {
    const socket = createConnection({ host, port });
    const finish = (connected) => {
      socket.destroy();
      _resolve(connected);
    };
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(timeoutMs, () => finish(false));
  });

export const createServicePaths = ({
  home = homedir(),
  repoRoot = WORKSPACE_ROOT,
  productRoot = PRODUCT_ROOT,
} = {}) => {
  const launchAgentsDirectory = resolve(home, "Library/LaunchAgents");
  const logsDirectory = resolve(home, "Library/Logs/Soleaux");
  return {
    compositionPath: resolve(productRoot, "scripts/soleaux/http_service.py"),
    fastmcpPath: resolve(productRoot, ".venv/bin/fastmcp"),
    launchAgentsDirectory,
    logsDirectory,
    plistPath: resolve(launchAgentsDirectory, `${SERVICE_LABEL}.plist`),
    productRoot,
    repoPythonPath: resolve(productRoot, ".venv/bin/python"),
    repoRoot,
    socketDirectory: dirname(SOCKET_PATH),
    socketPath: SOCKET_PATH,
    soleauxPath: resolve(productRoot, ".venv/bin/soleaux"),
    standardErrorPath: resolve(logsDirectory, `${SERVICE_LABEL}.stderr.log`),
    standardOutputPath: resolve(logsDirectory, `${SERVICE_LABEL}.stdout.log`),
  };
};

export const buildServiceProgramArguments = (paths) => [
  paths.repoPythonPath,
  paths.compositionPath,
  "serve",
];

export const DEVELOPMENT_FASTMCP_ENVIRONMENT = Object.freeze({
  FASTMCP_CHECK_FOR_UPDATES: "off",
  FASTMCP_HTTP_HOST_ORIGIN_PROTECTION: "auto",
  FASTMCP_STATELESS_HTTP: "false",
  PYTHONPATH: PRODUCT_ROOT,
});

export const buildDevelopmentProgramArguments = (paths) => [
  paths.fastmcpPath,
  "run",
  `${paths.compositionPath}:create_development_server`,
  "--transport",
  "http",
  "--host",
  DEVELOPMENT_HOST,
  "--port",
  String(DEVELOPMENT_PORT),
  "--path",
  SERVICE_PATH,
  "--no-banner",
  "--skip-source",
  "--skip-env",
  "--reload",
  "--reload-dir",
  paths.productRoot,
];

const escapeXml = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");

export const renderLaunchAgentPlist = (paths) => {
  const argumentsXml = buildServiceProgramArguments(paths)
    .map((argument) => `      <string>${escapeXml(argument)}</string>`)
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
${APPLE_PLIST_DOCTYPE}
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
${argumentsXml}
    </array>
    <key>WorkingDirectory</key>
    <string>${escapeXml(paths.repoRoot)}</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>FASTMCP_CHECK_FOR_UPDATES</key>
      <string>off</string>
      <key>PATH</key>
      <string>${escapeXml(SERVICE_EXECUTABLE_PATH)}</string>
      <key>PYTHONPATH</key>
      <string>${escapeXml(paths.productRoot)}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
      <key>SuccessfulExit</key>
      <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>${escapeXml(paths.standardOutputPath)}</string>
    <key>StandardErrorPath</key>
    <string>${escapeXml(paths.standardErrorPath)}</string>
  </dict>
</plist>
`;
};

const createRuntime = () => ({
  access,
  home: homedir(),
  httpRequest: nodeHttpRequest,
  lstat,
  mkdir,
  platform: platform(),
  readFile,
  rename,
  runForeground,
  runProcess,
  sleep: delay,
  tcpConnectable: nodeTcpConnectable,
  uid: process.getuid?.(),
  unlink,
  writeFile,
});

const requireDarwinRuntime = (runtime) => {
  if (runtime.platform !== "darwin" || runtime.uid === undefined) {
    throw new ServiceControllerError(
      "the Soleaux LaunchAgent controller requires macOS"
    );
  }
};

const serviceTarget = (runtime) => `gui/${runtime.uid}/${SERVICE_LABEL}`;
const userDomain = (runtime) => `gui/${runtime.uid}`;

export const removeLegacyCredentials = async (runtime) => {
  const results = await Promise.allSettled(
    LEGACY_CLIENT_ACCOUNTS.map(async (account) => {
      const result = await runtime.runProcess(
        SECURITY_COMMAND,
        ["delete-generic-password", "-a", account, "-s", SERVICE_LABEL],
        { allowFailure: true }
      );
      if (
        result.code !== SUCCESS_EXIT_CODE &&
        result.code !== KEYCHAIN_ITEM_NOT_FOUND
      ) {
        throw new ServiceControllerError(
          `the legacy Soleaux Keychain item for ${account} could not be removed`
        );
      }
    })
  );
  const failure = results.find((result) => result.status === "rejected");
  if (failure !== undefined) {
    throw failure.reason;
  }
  return { legacyCredentialsRemoved: LEGACY_CLIENT_ACCOUNTS.length };
};

const createInitializeBody = () =>
  JSON.stringify({
    id: 1,
    jsonrpc: "2.0",
    method: "initialize",
    params: {
      capabilities: {},
      clientInfo: {
        name: "soleaux-service-status",
        version: "1",
      },
      protocolVersion: MCP_PROTOCOL_VERSION,
    },
  });

const createAboutReadBody = () =>
  JSON.stringify({
    id: 2,
    jsonrpc: "2.0",
    method: "resources/read",
    params: {
      uri: ABOUT_RESOURCE_URI,
    },
  });

const postMcpRequest = async (runtime, body) =>
  await runtime.httpRequest({
    body,
    headers: {
      accept: "application/json, text/event-stream",
      "content-type": "application/json",
      "mcp-protocol-version": MCP_PROTOCOL_VERSION,
    },
    method: "POST",
    path: SERVICE_PATH,
    socketPath: SOCKET_PATH,
  });

const parseJson = (value, label) => {
  try {
    return JSON.parse(value);
  } catch {
    throw new ServiceControllerError(`${label} was not valid JSON`);
  }
};

const parseEventStreamJson = (value, label) => {
  const events = value
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n\n");
  for (const event of events) {
    const data = event
      .split("\n")
      .filter((line) => line.startsWith(EVENT_STREAM_DATA_PREFIX))
      .map((line) => {
        const field = line.slice(EVENT_STREAM_DATA_PREFIX.length);
        return field.startsWith(" ") ? field.slice(" ".length) : field;
      })
      .join("\n");
    if (data) {
      return parseJson(data, label);
    }
  }
  throw new ServiceControllerError(`${label} had no event-stream data`);
};

const parseMcpResponseJson = async (response, label) => {
  const body = await response.text();
  const mediaType = response.headers
    .get("content-type")
    ?.split(";", FIRST_MEDIA_TYPE_COUNT)
    .at(FIRST_ITEM_INDEX)
    ?.trim()
    .toLowerCase();
  return mediaType === EVENT_STREAM_MEDIA_TYPE
    ? parseEventStreamJson(body, label)
    : parseJson(body, label);
};

const requireRecord = (value, label) => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ServiceControllerError(`${label} was not an object`);
  }
  return value;
};

const extractServiceIdentity = (value, label) => {
  const payload = requireRecord(value, label);
  const identity = requireRecord(payload.identity, `${label} identity`);
  const product = requireRecord(payload.product, `${label} product`);
  const build = identity.build ?? null;
  return {
    catalogDigest: requireString(
      identity.catalog_digest,
      `${label} catalog digest`
    ),
    configurationDigest: requireString(
      identity.configuration_digest,
      `${label} configuration digest`
    ),
    productVersion: requireString(product.version, `${label} product version`),
    transport: requireString(
      identity.deployment_transport,
      `${label} deployment transport`
    ),
    gitSha:
      build !== null && typeof build.git_sha === "string" && build.git_sha
        ? build.git_sha
        : null,
    installSource:
      build !== null &&
      typeof build.install_source === "string" &&
      build.install_source
        ? build.install_source
        : null,
  };
};

const readDesiredIdentity = async (runtime, paths) => {
  const result = await runtime.runProcess(paths.soleauxPath, [
    "--root",
    paths.repoRoot,
    "describe",
    "--json",
  ]);
  const envelope = requireRecord(
    parseJson(result.stdout, "the source describe response"),
    "the source describe response"
  );
  const sourceIdentity = extractServiceIdentity(
    requireRecord(envelope.data, "the source describe data"),
    "the source"
  );
  return {
    ...sourceIdentity,
    transport: SERVICE_TRANSPORT,
  };
};

const readLiveIdentity = async (response) => {
  const rpcResponse = requireRecord(
    await parseMcpResponseJson(response, "the live about response"),
    "the live about response"
  );
  if (rpcResponse.error !== undefined) {
    throw new ServiceControllerError(
      "the live about resource returned a protocol error"
    );
  }
  const result = requireRecord(
    rpcResponse.result,
    "the live about response result"
  );
  if (
    !Array.isArray(result.contents) ||
    result.contents.length === SUCCESS_EXIT_CODE
  ) {
    throw new ServiceControllerError(
      "the live about response had no resource content"
    );
  }
  const content = requireRecord(
    result.contents[FIRST_ITEM_INDEX],
    "the live about resource content"
  );
  const about = parseJson(
    requireString(content.text, "the live about resource text"),
    "the live about resource"
  );
  const aboutIdentity = requireRecord(
    about.identity,
    "the live service identity"
  );
  return {
    ...extractServiceIdentity(about, "the live service"),
    processEpoch: requireString(
      aboutIdentity.process_epoch,
      "the live service process epoch"
    ),
  };
};

const inspectSocketEndpoint = async (runtime) => {
  let initializeResponse;
  try {
    initializeResponse = await postMcpRequest(runtime, createInitializeBody());
  } catch {
    return {
      identity: null,
      reason: "endpoint-unreachable",
      state: ENDPOINT_STATES.STOPPED,
    };
  }
  await initializeResponse.text();
  if (initializeResponse.status !== HTTP_OK) {
    return {
      identity: null,
      reason: "initialize-request-failed",
      state: ENDPOINT_STATES.STALE,
    };
  }

  try {
    const aboutResponse = await postMcpRequest(runtime, createAboutReadBody());
    if (aboutResponse.status !== HTTP_OK) {
      await aboutResponse.text();
      return {
        identity: null,
        reason: "about-resource-read-failed",
        state: ENDPOINT_STATES.STALE,
      };
    }
    return {
      identity: await readLiveIdentity(aboutResponse),
      reason: null,
      state: ENDPOINT_STATES.REACHABLE,
    };
  } catch {
    return {
      identity: null,
      reason: "live-identity-invalid",
      state: ENDPOINT_STATES.STALE,
    };
  }
};

const verifySocketEndpoint = async (runtime) => {
  const probe = await inspectSocketEndpoint(runtime);
  if (probe.state !== ENDPOINT_STATES.REACHABLE) {
    throw new ServiceControllerError(
      `the Soleaux endpoint failed its socket probe: ${probe.reason}`
    );
  }
  return probe.identity;
};

const waitForSocketEndpoint = async (
  runtime,
  attemptsRemaining = HEALTH_ATTEMPTS
) => {
  try {
    await verifySocketEndpoint(runtime);
  } catch (error) {
    if (attemptsRemaining === FINAL_HEALTH_ATTEMPT) {
      throw new ServiceControllerError(
        `the Soleaux endpoint did not become healthy: ${
          Error.isError(error) ? error.message : "unknown failure"
        }`
      );
    }
    await runtime.sleep(HEALTH_RETRY_DELAY_MS);
    await waitForSocketEndpoint(
      runtime,
      attemptsRemaining - HEALTH_ATTEMPT_DECREMENT
    );
  }
};

const waitForServiceUnloaded = async (
  runtime,
  attemptsRemaining = HEALTH_ATTEMPTS
) => {
  const result = await runtime.runProcess(
    LAUNCHCTL_COMMAND,
    ["print", serviceTarget(runtime)],
    { allowFailure: true }
  );
  if (result.code !== SUCCESS_EXIT_CODE) {
    return;
  }
  if (attemptsRemaining === FINAL_HEALTH_ATTEMPT) {
    throw new ServiceControllerError(
      "the prior Soleaux LaunchAgent did not unload"
    );
  }
  await runtime.sleep(HEALTH_RETRY_DELAY_MS);
  await waitForServiceUnloaded(
    runtime,
    attemptsRemaining - HEALTH_ATTEMPT_DECREMENT
  );
};

const writeLaunchAgent = async (runtime, paths) => {
  await runtime.mkdir(paths.launchAgentsDirectory, { recursive: true });
  await runtime.mkdir(paths.logsDirectory, { recursive: true });
  const temporaryPath = `${paths.plistPath}.tmp-${process.pid}`;
  await runtime.writeFile(temporaryPath, renderLaunchAgentPlist(paths), {
    encoding: "utf-8",
    mode: PLIST_MODE,
  });
  await runtime.rename(temporaryPath, paths.plistPath);
};

export const installService = async (
  runtime,
  paths,
  { removeLegacyCredentialsRequested = false } = {}
) => {
  requireDarwinRuntime(runtime);
  await runtime.access(paths.repoPythonPath, fsConstants.X_OK);
  await runtime.access(paths.compositionPath, fsConstants.R_OK);
  await writeLaunchAgent(runtime, paths);
  await runtime.runProcess(
    LAUNCHCTL_COMMAND,
    ["bootout", serviceTarget(runtime)],
    { allowFailure: true }
  );
  await waitForServiceUnloaded(runtime);
  try {
    if (LEGACY_DOMAIN_TOKEN_ENVIRONMENT !== null) {
      await runtime.runProcess(LAUNCHCTL_COMMAND, [
        "unsetenv",
        LEGACY_DOMAIN_TOKEN_ENVIRONMENT,
      ]);
    }
    await runtime.runProcess(LAUNCHCTL_COMMAND, [
      "bootstrap",
      userDomain(runtime),
      paths.plistPath,
    ]);
    await waitForSocketEndpoint(runtime);
  } catch (error) {
    try {
      await runtime.runProcess(
        LAUNCHCTL_COMMAND,
        ["bootout", serviceTarget(runtime)],
        { allowFailure: true }
      );
      await waitForServiceUnloaded(runtime);
    } catch (cleanupError) {
      throw new ServiceControllerError(
        "Soleaux install failed and the replacement service could not be confirmed stopped",
        {
          cause: new AggregateError(
            [error, cleanupError],
            "service install and replacement cleanup both failed"
          ),
        }
      );
    }
    throw error;
  }
  const legacy = removeLegacyCredentialsRequested
    ? await removeLegacyCredentials(runtime)
    : { legacyCredentialsRemoved: 0 };
  return {
    ...legacy,
    endpoint: SERVICE_ENDPOINT,
    label: SERVICE_LABEL,
    reachable: true,
    socketPath: paths.socketPath,
  };
};

const pathIsAccessible = async (runtime, path) => {
  try {
    await runtime.access(path, fsConstants.R_OK);
    return true;
  } catch (error) {
    if (Error.isError(error) && Reflect.get(error, "code") === "ENOENT") {
      return false;
    }
    throw error;
  }
};

const inspectSocketState = async (runtime, socketPath) => {
  let info;
  try {
    info = await runtime.lstat(socketPath);
  } catch (error) {
    if (Error.isError(error) && Reflect.get(error, "code") === "ENOENT") {
      return {
        path: socketPath,
        reason: "socket-missing",
        state: SOCKET_STATES.MISSING,
      };
    }
    throw error;
  }
  if (info.isSymbolicLink()) {
    return {
      path: socketPath,
      reason: "socket-path-is-a-symlink",
      state: SOCKET_STATES.UNSAFE,
    };
  }
  if (!info.isSocket()) {
    return {
      path: socketPath,
      reason: "socket-occupant-not-a-socket",
      state: SOCKET_STATES.UNSAFE,
    };
  }
  if (info.uid !== runtime.uid) {
    return {
      path: socketPath,
      reason: "socket-owned-by-another-user",
      state: SOCKET_STATES.UNSAFE,
    };
  }
  if (info.mode % MODE_BASE !== SOCKET_MODE) {
    return {
      path: socketPath,
      reason: "socket-mode-is-not-0600",
      state: SOCKET_STATES.UNSAFE,
    };
  }
  const directory = await runtime.lstat(dirname(socketPath));
  if (
    !directory.isDirectory() ||
    directory.uid !== runtime.uid ||
    directory.mode % MODE_BASE !== SOCKET_DIRECTORY_MODE
  ) {
    return {
      path: socketPath,
      reason: "socket-directory-is-not-user-private",
      state: SOCKET_STATES.UNSAFE,
    };
  }
  return {
    path: socketPath,
    reason: null,
    state: SOCKET_STATES.SAFE,
  };
};

export const classifyServiceState = ({
  endpointState,
  installed,
  installationMatches,
  loaded,
  parity,
  socketState,
  tcpListenerPresent,
}) => {
  if (!loaded) {
    return installed ? SERVICE_STATES.STOPPED : SERVICE_STATES.ABSENT;
  }
  if (endpointState === ENDPOINT_STATES.STOPPED) {
    return SERVICE_STATES.STOPPED;
  }
  const isDrift = [
    endpointState !== ENDPOINT_STATES.REACHABLE,
    parity !== true,
    installationMatches === false,
    socketState !== SOCKET_STATES.SAFE,
    tcpListenerPresent === true,
  ].some(Boolean);
  if (isDrift) {
    return SERVICE_STATES.STALE;
  }
  return SERVICE_STATES.HEALTHY;
};

export const recoveryForServiceState = (
  state,
  { installationMatches = true } = {}
) => {
  switch (state) {
    case SERVICE_STATES.ABSENT:
    case SERVICE_STATES.STOPPED: {
      return {
        action: "install",
        arguments: ["install"],
        requiresCr10: true,
        requiresExplicitAuthorization: true,
      };
    }
    case SERVICE_STATES.STALE: {
      if (!installationMatches) {
        return {
          action: "reinstall",
          arguments: ["install"],
          requiresCr10: true,
          requiresExplicitAuthorization: true,
        };
      }
      return {
        action: "restart",
        arguments: ["restart"],
        requiresCr10: true,
        requiresExplicitAuthorization: true,
      };
    }
    case SERVICE_STATES.HEALTHY: {
      return null;
    }
    default: {
      throw new ServiceControllerError(`unknown service state: ${state}`);
    }
  }
};

const compareServiceIdentity = (desired, live) => {
  if (live === null) {
    return {
      catalog: null,
      configuration: null,
      product: null,
      transport: null,
      gitSha: null,
      installSource: null,
      matches: null,
    };
  }
  const isCatalogCurrent = desired.catalogDigest === live.catalogDigest;
  const isConfigurationCurrent =
    desired.configurationDigest === live.configurationDigest;
  const isProductCurrent = desired.productVersion === live.productVersion;
  const isTransportCurrent = desired.transport === live.transport;
  const isGitShaCurrent =
    desired.gitSha !== null && live.gitSha !== null
      ? desired.gitSha === live.gitSha
      : null;
  const isInstallSourceCurrent =
    desired.installSource !== null && live.installSource !== null
      ? desired.installSource === live.installSource
      : null;
  const matches =
    isCatalogCurrent &&
    isConfigurationCurrent &&
    isProductCurrent &&
    isTransportCurrent &&
    (isGitShaCurrent !== false) &&
    (isInstallSourceCurrent !== false);
  return {
    catalog: isCatalogCurrent,
    configuration: isConfigurationCurrent,
    product: isProductCurrent,
    transport: isTransportCurrent,
    gitSha: isGitShaCurrent,
    installSource: isInstallSourceCurrent,
    matches,
  };
};

export const statusService = async (runtime, paths) => {
  requireDarwinRuntime(runtime);
  const desiredIdentity = await readDesiredIdentity(runtime, paths);
  const launchdResult = await runtime.runProcess(
    LAUNCHCTL_COMMAND,
    ["print", serviceTarget(runtime)],
    { allowFailure: true }
  );
  const isInstalled = await pathIsAccessible(runtime, paths.plistPath);
  const installationMatches = isInstalled
    ? (await runtime.readFile(paths.plistPath, "utf-8")) ===
      renderLaunchAgentPlist(paths)
    : null;
  const isLoaded = launchdResult.code === SUCCESS_EXIT_CODE;
  const tcpListenerPresent = await runtime.tcpConnectable({
    host: DEVELOPMENT_HOST,
    port: LEGACY_TCP_PORT,
  });
  let endpoint = {
    identity: null,
    reason: null,
    state: null,
  };
  let socket = {
    path: paths.socketPath,
    reason: "service-not-loaded",
    state: null,
  };
  if (isLoaded) {
    endpoint = await inspectSocketEndpoint(runtime);
    socket = await inspectSocketState(runtime, paths.socketPath);
  }
  const parity = compareServiceIdentity(desiredIdentity, endpoint.identity);
  const state = classifyServiceState({
    endpointState: endpoint.state,
    installed: isInstalled,
    installationMatches,
    loaded: isLoaded,
    parity: parity.matches,
    socketState: socket.state,
    tcpListenerPresent,
  });
  return {
    endpoint: SERVICE_ENDPOINT,
    identity: {
      desired: desiredIdentity,
      live: endpoint.identity,
      parity,
    },
    installed: isInstalled,
    installation: {
      matches: installationMatches,
    },
    label: SERVICE_LABEL,
    loaded: isLoaded,
    reachable: endpoint.state === ENDPOINT_STATES.REACHABLE,
    reason: endpoint.reason,
    recovery: recoveryForServiceState(state, { installationMatches }),
    socket,
    state,
    tcpListenerPresent,
  };
};

const restartService = async (runtime) => {
  requireDarwinRuntime(runtime);
  await runtime.runProcess(LAUNCHCTL_COMMAND, [
    "kickstart",
    "-k",
    serviceTarget(runtime),
  ]);
  await waitForSocketEndpoint(runtime);
  return {
    endpoint: SERVICE_ENDPOINT,
    label: SERVICE_LABEL,
    reachable: true,
    restarted: true,
  };
};

const unlinkIfPresent = async (runtime, path) => {
  try {
    await runtime.unlink(path);
  } catch (error) {
    if (!Error.isError(error) || Reflect.get(error, "code") !== "ENOENT") {
      throw error;
    }
  }
};

const uninstallService = async (runtime, paths) => {
  requireDarwinRuntime(runtime);
  await runtime.runProcess(
    LAUNCHCTL_COMMAND,
    ["bootout", serviceTarget(runtime)],
    { allowFailure: true }
  );
  await unlinkIfPresent(runtime, paths.plistPath);
  await unlinkIfPresent(runtime, paths.socketPath);
  return {
    label: SERVICE_LABEL,
    uninstalled: true,
  };
};

export const buildDryRunPlan = (
  command,
  paths,
  { removeLegacyCredentialsRequested = false } = {}
) => ({
  command,
  developmentProgram:
    command === "dev" ? buildDevelopmentProgramArguments(paths) : undefined,
  endpoint: SERVICE_ENDPOINT,
  label: SERVICE_LABEL,
  launchAgentPath: paths.plistPath,
  legacyCredentials: {
    removed: command === "install" && removeLegacyCredentialsRequested,
    service: SERVICE_LABEL,
  },
  serviceProgram: buildServiceProgramArguments(paths),
  socket: {
    directory: paths.socketDirectory,
    path: paths.socketPath,
  },
});

const parseArguments = (commandArguments) => {
  const [command, ...rest] = commandArguments;
  if (
    !["dev", "install", "restart", "status", "uninstall", "verify"].includes(
      command
    )
  ) {
    throw new ServiceControllerError(
      "usage: service.mjs <dev|install|restart|status|uninstall|verify> [--config <path>] [--dry-run] [--remove-legacy-credentials]"
    );
  }
  const flags = rest.filter(
    (flag, index) => flag !== "--config" && rest[index - 1] !== "--config"
  );
  const unknownFlags = flags.filter(
    (flag) => !["--dry-run", "--remove-legacy-credentials"].includes(flag)
  );
  if (unknownFlags.length > SUCCESS_EXIT_CODE) {
    throw new ServiceControllerError(
      `unknown option: ${unknownFlags[FIRST_ITEM_INDEX]}`
    );
  }
  const removeLegacyCredentialsRequested = flags.includes(
    "--remove-legacy-credentials"
  );
  if (removeLegacyCredentialsRequested && command !== "install") {
    throw new ServiceControllerError(
      "--remove-legacy-credentials is valid only with install"
    );
  }
  return {
    command,
    dryRun: flags.includes("--dry-run"),
    removeLegacyCredentialsRequested,
  };
};

export const verifyWorkflow = async (runtime, paths) => {
  requireDarwinRuntime(runtime);
  const socket = await inspectSocketState(runtime, paths.socketPath);
  if (socket.state !== SOCKET_STATES.SAFE) {
    throw new ServiceControllerError(
      `the Soleaux socket is not ready for verification: ${socket.reason}`
    );
  }
  const hookPayload = JSON.stringify({
    cwd: paths.repoRoot,
    hook_event_name: HOOK_EVENT_NAME,
    prompt: VERIFY_OBJECTIVE,
    turn_id: `soleaux-service-verify-${process.pid}`,
  });
  const result = await runtime.runProcess(
    paths.repoPythonPath,
    [resolve(paths.repoRoot, HOOK_SCRIPT_PATH)],
    { input: hookPayload, timeoutMs: HOOK_TIMEOUT_MS }
  );
  const lines = result.stdout.trim().split("\n");
  if (lines.length !== EXPECTED_HOOK_OUTPUT_LINES) {
    throw new ServiceControllerError(
      "the Soleaux hook did not emit exactly one JSON output line"
    );
  }
  const output = parseJson(lines[FIRST_ITEM_INDEX], "the hook output");
  const hookSpecificOutput = requireRecord(
    output.hookSpecificOutput,
    "the hook output"
  );
  if (hookSpecificOutput.hookEventName !== HOOK_EVENT_NAME) {
    throw new ServiceControllerError(
      "the Soleaux hook output named the wrong event"
    );
  }
  const additionalContext = requireString(
    hookSpecificOutput.additionalContext,
    "the hook additionalContext"
  );
  const contextBytes = Buffer.byteLength(additionalContext, "utf-8");
  if (contextBytes > MAX_CONTEXT_PAYLOAD_BYTES) {
    throw new ServiceControllerError(
      `the verified context exceeded the ${MAX_CONTEXT_PAYLOAD_BYTES}-byte host envelope`
    );
  }
  const missingMarkers = REQUIRED_CONTEXT_MARKERS.filter(
    (marker) => !additionalContext.includes(marker)
  );
  if (missingMarkers.length > EMPTY_COLLECTION_SIZE) {
    throw new ServiceControllerError(
      `the verified context is missing required sections: ${missingMarkers.join(", ")}`
    );
  }
  return {
    additionalContextBytes: contextBytes,
    endpoint: SERVICE_ENDPOINT,
    hookOutputLines: lines.length,
    label: SERVICE_LABEL,
    objective: VERIFY_OBJECTIVE,
    socketPath: paths.socketPath,
    verified: true,
  };
};

const executeCommand = async (
  runtime,
  paths,
  { command, removeLegacyCredentialsRequested }
) => {
  switch (command) {
    case "dev": {
      await runtime.runForeground(
        paths.fastmcpPath,
        buildDevelopmentProgramArguments(paths).slice(
          ENTRYPOINT_ARGUMENT_INDEX
        ),
        DEVELOPMENT_FASTMCP_ENVIRONMENT
      );
      return {
        endpoint: `http://${DEVELOPMENT_HOST}:${DEVELOPMENT_PORT}${SERVICE_PATH}`,
      };
    }
    case "install": {
      return await installService(runtime, paths, {
        removeLegacyCredentialsRequested,
      });
    }
    case "restart": {
      return await restartService(runtime);
    }
    case "status": {
      return await statusService(runtime, paths);
    }
    case "uninstall": {
      return await uninstallService(runtime, paths);
    }
    case "verify": {
      return await verifyWorkflow(runtime, paths);
    }
    default: {
      throw new ServiceControllerError(`unsupported command: ${command}`);
    }
  }
};

export const main = async (
  commandArguments = process.argv.slice(CLI_ARGUMENT_OFFSET)
) => {
  const options = parseArguments(commandArguments);
  const runtime = createRuntime();
  const paths = createServicePaths({
    home: runtime.home,
    repoRoot: WORKSPACE_ROOT,
  });
  const result = options.dryRun
    ? buildDryRunPlan(options.command, paths, options)
    : await executeCommand(runtime, paths, options);
  process.stdout.write(`${JSON.stringify(result, null, JSON_INDENT_SPACES)}\n`);
};

const runCli = async () => {
  try {
    await main();
  } catch (error) {
    const message = Error.isError(error)
      ? error.message
      : "unknown service-controller failure";
    process.stderr.write(`${message}\n`);
    process.exitCode = FAILURE_EXIT_CODE;
  }
};

if (
  process.argv[ENTRYPOINT_ARGUMENT_INDEX] !== undefined &&
  realpathSync(process.argv[ENTRYPOINT_ARGUMENT_INDEX]) ===
    realpathSync(fileURLToPath(import.meta.url))
) {
  void runCli();
}
