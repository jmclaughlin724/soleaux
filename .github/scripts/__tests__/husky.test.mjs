import { spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";
import { parse } from "yaml";

const repoRoot = resolve(import.meta.dirname, "../../..");
const packageDirectory = dirname(fileURLToPath(import.meta.resolve("husky")));
const huskyCli = join(packageDirectory, "bin.js");
const executableMode = 0o755;
const hookFailureExitCode = 17;
const successExitCode = 0;
const preCommitCommand = "pnpm exec turbo run check:ci --affected";
const postCommitCommand = "pnpm exec turbo run typecheck test:unit --affected";

function run(command, arguments_, options = {}) {
  return spawnSync(command, arguments_, {
    cwd: options.cwd,
    encoding: "utf-8",
    env: { ...process.env, ...options.env },
    timeout: 30_000,
  });
}

function runOk(command, arguments_, options = {}) {
  const result = run(command, arguments_, options);
  if (result.error || result.status !== successExitCode) {
    throw new Error(
      result.error?.message ||
        result.stderr ||
        result.stdout ||
        `${command} exited ${result.status}`
    );
  }
  return result;
}

test("package scripts and tracked hooks give Husky sole Git-hook ownership", () => {
  const packageManifest = JSON.parse(
    readFileSync(join(repoRoot, "package.json"), "utf-8")
  );

  expect(packageManifest.devDependencies.husky).toBe("catalog:");
  expect(packageManifest.devDependencies.lefthook).toBeUndefined();
  expect(packageManifest.scripts.prepare).toBe("husky");
  expect(packageManifest.scripts["check:hooks"]).toBe("pnpm run husky:test");
  expect(readFileSync(join(repoRoot, ".husky", "pre-commit"), "utf-8")).toBe(
    `${preCommitCommand}\n`
  );
  expect(readFileSync(join(repoRoot, ".husky", "post-commit"), "utf-8")).toBe(
    `${postCommitCommand}\n`
  );
  expect(existsSync(join(repoRoot, ".husky", "pre-push"))).toBe(false);
});

test("CI invokes the hooks lane that verifies Husky", () => {
  const workflow = parse(
    readFileSync(join(repoRoot, ".github", "workflows", "ci.yml"), "utf-8")
  );
  const packageManifest = JSON.parse(
    readFileSync(join(repoRoot, "package.json"), "utf-8")
  );
  const steps = Object.values(workflow.jobs).flatMap((job) => job.steps);
  expect(steps.some((step) => step.run === "pnpm run check:hooks")).toBe(true);
  expect(packageManifest.scripts["check:hooks"]).toBe("pnpm run husky:test");
});

test("installed Husky wrappers execute both tracked hooks and propagate failure", (context) => {
  const fixture = mkdtempSync(join(tmpdir(), "soleaux-husky-"));
  context.onTestFinished(() =>
    rmSync(fixture, { force: true, recursive: true })
  );

  runOk("git", ["init", "--quiet"], { cwd: fixture });
  const fixtureHooks = join(fixture, ".husky");
  mkdirSync(fixtureHooks);
  copyFileSync(
    join(repoRoot, ".husky", "pre-commit"),
    join(fixtureHooks, "pre-commit")
  );
  copyFileSync(
    join(repoRoot, ".husky", "post-commit"),
    join(fixtureHooks, "post-commit")
  );

  const install = run(process.execPath, [huskyCli], { cwd: fixture });
  expect(install.status, install.stderr || install.stdout).toBe(
    successExitCode
  );
  expect(
    runOk("git", ["config", "--local", "--get", "core.hooksPath"], {
      cwd: fixture,
    }).stdout.trim()
  ).toBe(".husky/_");

  const fakeBin = join(fixture, "bin");
  const witness = join(fixture, "hook-witness.txt");
  mkdirSync(fakeBin);
  const fakePnpm = join(fakeBin, "pnpm");
  writeFileSync(
    fakePnpm,
    [
      "#!/bin/sh",
      'printf "%s\\n" "$*" >> "$HUSKY_TEST_LOG"',
      `if [ "\${HUSKY_TEST_FAILURE:-0}" = "1" ]; then exit ${hookFailureExitCode}; fi`,
      "",
    ].join("\n")
  );
  chmodSync(fakePnpm, executableMode);
  const environment = {
    HUSKY_TEST_LOG: witness,
    PATH: `${fakeBin}:${process.env.PATH ?? ""}`,
  };

  const preCommit = run("sh", [join(fixtureHooks, "_", "pre-commit")], {
    cwd: fixture,
    env: environment,
  });
  expect(preCommit.status, preCommit.stderr || preCommit.stdout).toBe(
    successExitCode
  );
  const postCommit = run("sh", [join(fixtureHooks, "_", "post-commit")], {
    cwd: fixture,
    env: environment,
  });
  expect(postCommit.status, postCommit.stderr || postCommit.stdout).toBe(
    successExitCode
  );
  expect(readFileSync(witness, "utf-8")).toBe(
    [
      "exec turbo run check:ci --affected",
      "exec turbo run typecheck test:unit --affected",
      "",
    ].join("\n")
  );

  const failure = run("sh", [join(fixtureHooks, "_", "pre-commit")], {
    cwd: fixture,
    env: { ...environment, HUSKY_TEST_FAILURE: "1" },
  });
  expect(failure.status).toBe(hookFailureExitCode);
});
