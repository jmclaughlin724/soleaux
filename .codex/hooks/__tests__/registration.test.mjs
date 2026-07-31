import { spawn, spawnSync } from "node:child_process";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, expect, test } from "vitest";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const hookRegistrationPath = join(repoRoot, ".codex", "hooks.json");
const editMatcher = "^(apply_patch|Edit|Write)$";
const bashMatcher = "^Bash$";
const exactRegistrations = [
  {
    command:
      'node "$(git rev-parse --show-toplevel)/.codex/hooks/PreToolUse/bash-policy.mjs"',
    event: "PreToolUse",
    matcher: bashMatcher,
    owner: "PreToolUse/bash-policy.mjs",
  },
  {
    command:
      '"$(git rev-parse --show-toplevel)/.venv/bin/python" "$(git rev-parse --show-toplevel)/.codex/hooks/UserPromptSubmit/soleaux_context.py"',
    event: "UserPromptSubmit",
    owner: "UserPromptSubmit/soleaux_context.py",
  },
];
const removedForwarders = [
  "PostToolUse/supaschema-schema-write.mjs",
  "PreToolUse/general-guard.mjs",
  "PreToolUse/pre-tool-use-runtime.mjs",
  "PreToolUse/supaschema-generated-migration-edit.mjs",
];
const temporaryRoots = [];

afterEach(() => {
  const roots = [...temporaryRoots];
  temporaryRoots.length = 0;
  for (const root of roots) {
    rmSync(root, { force: true, recursive: true });
  }
});

function requireRegularFile(path, label) {
  const stats = lstatSync(path);
  if (!stats.isFile()) {
    throw new TypeError(`${label} must be a regular, non-symlink file`);
  }
}

function eventEntrypoint(event, command, root = repoRoot) {
  const prefixes = [
    `node "$(git rev-parse --show-toplevel)/.codex/hooks/${event}/`,
    `"$(git rev-parse --show-toplevel)/.venv/bin/python" "$(git rev-parse --show-toplevel)/.codex/hooks/${event}/`,
  ];
  const prefix = prefixes.find((candidate) => command.startsWith(candidate));
  if (prefix === undefined || !command.endsWith('"')) {
    throw new TypeError(
      `${event} hook command must directly invoke one event-owned file`
    );
  }
  const entrypoint = command.slice(prefix.length, -1);
  if (
    !/^[A-Za-z0-9_.+-]+$/u.test(entrypoint) ||
    basename(entrypoint) !== entrypoint
  ) {
    throw new TypeError(
      `${event} hook entrypoint must be one safe direct filename`
    );
  }
  const eventDirectory = join(root, ".codex", "hooks", event);
  if (!lstatSync(eventDirectory).isDirectory()) {
    throw new TypeError(
      `${event} hook directory must be a regular, non-symlink directory`
    );
  }
  const path = join(eventDirectory, entrypoint);
  requireRegularFile(path, `${event}/${entrypoint}`);
  return path;
}

function registrationRows(registration) {
  return Object.entries(registration.hooks).flatMap(([event, groups]) =>
    groups.flatMap((group) => {
      if (group.hooks.length !== 1) {
        throw new TypeError(
          `${event} matcher ${group.matcher} must register one direct command`
        );
      }
      return group.hooks.map((handler) => ({
        command: handler.command,
        event,
        matcher: group.matcher,
      }));
    })
  );
}

function resolveDirectOwner(row) {
  const expected = exactRegistrations.find(
    ({ command, event }) => command === row.command && event === row.event
  );
  if (!expected) {
    throw new TypeError(
      `${row.event} has an unrecognized or indirect hook command`
    );
  }
  eventEntrypoint(row.event, row.command);
  return expected.owner;
}

function readRegistration() {
  return JSON.parse(readFileSync(hookRegistrationPath, "utf-8"));
}

function runHookConcurrently(path, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path], {
      cwd: repoRoot,
      stdio: ["pipe", "pipe", "pipe"],
      timeout: 10_000,
    });
    let stderr = "";
    let stdout = "";
    child.stderr.setEncoding("utf-8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdout.setEncoding("utf-8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.once("error", reject);
    child.once("close", (status, signal) => {
      resolve({ signal, status, stderr, stdout });
    });
    child.stdin.end(input);
  });
}

test("registers the exact direct owners and matcher scopes", () => {
  const rows = registrationRows(readRegistration());
  const actual = rows
    .map((row) => ({
      ...row,
      owner: resolveDirectOwner(row),
    }))
    .toSorted((left, right) => left.owner.localeCompare(right.owner));
  const expected = exactRegistrations
    .map(({ command, event, matcher, owner }) => ({
      command,
      event,
      matcher,
      owner,
    }))
    .toSorted((left, right) => left.owner.localeCompare(right.owner));
  expect(actual).toEqual(expected);
});

test("registers exactly one Bash event owner", () => {
  const commandOwners = registrationRows(readRegistration())
    .filter(
      ({ event, matcher }) => event === "PreToolUse" && matcher === bashMatcher
    )
    .map(resolveDirectOwner)
    .toSorted((left, right) => left.localeCompare(right));
  expect(commandOwners).toEqual(["PreToolUse/bash-policy.mjs"]);
});

test("parallel safe Bash events stay silent through the single owner", async () => {
  const row = registrationRows(readRegistration()).find(
    ({ event, matcher }) => event === "PreToolUse" && matcher === bashMatcher
  );
  const path = eventEntrypoint(row.event, row.command);
  const input = JSON.stringify({
    cwd: repoRoot,
    hook_event_name: "PreToolUse",
    tool_input: { command: "git status --short" },
    tool_name: "Bash",
  });
  const results = await Promise.all(
    Array.from({ length: 6 }, () => runHookConcurrently(path, input))
  );

  expect(results).toEqual(
    Array.from({ length: 6 }, () => ({
      signal: null,
      status: 0,
      stderr: "",
      stdout: "",
    }))
  );
});

test.each(exactRegistrations)(
  "$owner exits 2 with corrective stderr for malformed native input",
  ({ command }) => {
    const result = spawnSync(command, {
      cwd: repoRoot,
      encoding: "utf-8",
      input: "{",
      shell: true,
      timeout: 30_000,
    });
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(2);
    expect(result.stdout).toBe("");
    expect(result.stderr).toContain("source=");
    expect(result.stderr).toContain("code=");
    expect(result.stderr).toContain("cause=");
    expect(result.stderr).toContain("Corrective action:");
  }
);

test("does not register Supaschema lifecycle hooks", () => {
  expect(JSON.stringify(readRegistration())).not.toContain("supaschema hook");
});

test("removes retired forwarding shims", () => {
  for (const path of removedForwarders) {
    expect(existsSync(join(repoRoot, ".codex", "hooks", path))).toBe(false);
  }
  expect(JSON.stringify(readRegistration())).not.toContain("general-guard.mjs");
});

test("structural policy retains its hard timeout", () => {
  const group = readRegistration().hooks.PreToolUse.find(({ hooks }) =>
    hooks.some(({ command }) =>
      command.endsWith('/PreToolUse/structural-policy.mjs"')
    )
  );
  expect(group.hooks[0].timeout).toBe(2);
  expect(group.matcher).toBe(editMatcher);
});

test("Bash owner timeout exceeds the internal parser deadline", () => {
  const group = readRegistration().hooks.PreToolUse.find(
    ({ matcher }) => matcher === bashMatcher
  );
  expect(group.hooks[0].timeout).toBeGreaterThan(5);
  expect(group.hooks[0].command).toContain("/PreToolUse/bash-policy.mjs");
});

test("pre-prompt context has no ignored matcher and outlives the MCP tool deadline", () => {
  const [group] = readRegistration().hooks.UserPromptSubmit;
  expect(group).not.toHaveProperty("matcher");
  expect(group.hooks[0].timeout).toBeGreaterThan(60);
  expect(group.hooks[0].command).toContain(
    "/UserPromptSubmit/soleaux_context.py"
  );
});

test.each([
  'node "$(git rev-parse --show-toplevel)/scripts/codex/stop-hook.mjs"',
  'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/nested/stop-hook.mjs"',
  'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/missing.mjs"',
  'sh -c node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/stop-hook.mjs"',
  'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/../PostToolUse/auto-fix.mjs"',
  'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/stop-hook.mjs" --safe',
])("rejects a misplaced, wrapped, or missing local entrypoint", (command) => {
  expect(() => eventEntrypoint("Stop", command)).toThrow();
});

test("rejects directory and symlink local entrypoints", () => {
  const root = mkdtempSync(join(tmpdir(), "hook-registration-"));
  temporaryRoots.push(root);
  const eventDirectory = join(root, ".codex", "hooks", "Stop");
  mkdirSync(eventDirectory, { recursive: true });
  mkdirSync(join(eventDirectory, "directory.mjs"));
  const target = join(root, "outside.mjs");
  writeFileSync(target, "export {};\n");
  symlinkSync(target, join(eventDirectory, "symlink.mjs"));

  expect(() =>
    eventEntrypoint(
      "Stop",
      'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/directory.mjs"',
      root
    )
  ).toThrow("regular, non-symlink file");
  expect(() =>
    eventEntrypoint(
      "Stop",
      'node "$(git rev-parse --show-toplevel)/.codex/hooks/Stop/symlink.mjs"',
      root
    )
  ).toThrow("regular, non-symlink file");
});
