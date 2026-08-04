import { strict as assert } from "node:assert";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { describe, test } from "vitest";
import { parse } from "yaml";

const repositoryRoot = resolve(import.meta.dirname, "..", "..", "..");
const packageManifest = JSON.parse(
  readFileSync(resolve(repositoryRoot, "package.json"), "utf8")
);
const workspaceManifest = parse(
  readFileSync(resolve(repositoryRoot, "pnpm-workspace.yaml"), "utf8")
);
const projectConfig = parse(
  readFileSync(resolve(repositoryRoot, "sgconfig.yml"), "utf8")
);
const soleauxConfig = readFileSync(
  resolve(repositoryRoot, "soleaux.toml"),
  "utf8"
);
const configuredBinary = JSON.parse(
  readFileSync(resolve(repositoryRoot, ".vscode", "settings.json"), "utf8")
)["astGrep.serverPath"];
const executablePath = resolve(
  repositoryRoot,
  process.platform === "win32" ? `${configuredBinary}.cmd` : configuredBinary
);

function yamlIds(directory) {
  return yamlRecords(directory)
    .map(({ id }) => id)
    .sort();
}

function yamlRecords(directory) {
  return readdirSync(directory)
    .filter((name) => name.endsWith(".yml"))
    .map((name) => parse(readFileSync(resolve(directory, name), "utf8")));
}

describe("repository-owned ast-grep integration", () => {
  test("pins and authorizes the native CLI used by every consumer", () => {
    assert.equal(packageManifest.devDependencies["@ast-grep/cli"], "catalog:");
    assert.equal(workspaceManifest.catalog["@ast-grep/cli"], "0.45.0");
    assert.equal(workspaceManifest.allowBuilds["@ast-grep/cli"], true);
    assert.equal(workspaceManifest.preferSymlinkedExecutables, true);
    assert.equal(configuredBinary, "node_modules/.bin/ast-grep");
    assert.equal(existsSync(executablePath), true);

    const help = spawnSync(executablePath, ["-h"], {
      cwd: repositoryRoot,
      encoding: "utf8",
    });
    assert.equal(help.status, 0, help.stderr);

    const version = spawnSync(executablePath, ["--version"], {
      cwd: repositoryRoot,
      encoding: "utf8",
    });
    assert.equal(version.status, 0, version.stderr);
    assert.equal(version.stdout.trim(), "ast-grep 0.45.0");

    const lsp = spawnSync(executablePath, ["lsp", "--help"], {
      cwd: repositoryRoot,
      encoding: "utf8",
    });
    assert.equal(lsp.status, 0, lsp.stderr);
    assert.equal(lsp.stdout.includes("Start language server"), true);
  });

  test("keeps config, rules, fixtures, snapshots, and scripts complete", () => {
    assert.deepEqual(projectConfig.ruleDirs, ["tools/ast-grep/rules"]);
    assert.deepEqual(projectConfig.testConfigs, [
      { testDir: "tools/ast-grep/tests" },
    ]);
    assert.equal(
      soleauxConfig.includes(
        '[structural]\nproject_config = "sgconfig.yml"'
      ),
      true
    );

    const ruleDirectory = resolve(repositoryRoot, "tools/ast-grep/rules");
    const fixtureDirectory = resolve(repositoryRoot, "tools/ast-grep/tests");
    const ruleIds = yamlIds(ruleDirectory);
    const testIds = yamlIds(fixtureDirectory);
    const snapshotIds = yamlIds(
      resolve(repositoryRoot, "tools/ast-grep/tests/__snapshots__")
    );
    assert.deepEqual(ruleIds, ["r074-python", "r074-untyped-mcp-tool"]);
    assert.deepEqual(testIds, ruleIds);
    assert.deepEqual(snapshotIds, ruleIds);

    for (const rule of yamlRecords(ruleDirectory)) {
      assert.equal(rule.severity, "error");
      assert.equal(rule.metadata?.heuristic, "false");
      assert.equal(typeof rule.note, "string");
      assert.equal(rule.note.length > 0, true);
    }
    for (const fixture of yamlRecords(fixtureDirectory)) {
      assert.equal(Array.isArray(fixture.valid), true);
      assert.equal(fixture.valid.length > 0, true);
      assert.equal(Array.isArray(fixture.invalid), true);
      assert.equal(fixture.invalid.length > 0, true);
    }

    for (const script of [
      "ast-grep:rules:test",
      "ast-grep:test",
      "ast-grep:update-snapshots",
      "ast-grep:validate",
      "check:structural-policy",
    ]) {
      assert.equal(typeof packageManifest.scripts[script], "string");
    }
  });
});
