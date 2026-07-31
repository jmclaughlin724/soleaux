import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import {
  createWorkspaceDiagnosticCommands,
  DIAGNOSTIC_PREFIX,
  formatWorkspaceDiagnostic,
  normalizeDiagnosticPath,
  parseAstGrepJsonDiagnostics,
  parseEslintJsonDiagnostics,
  parsePrettierDiagnostics,
  parseStylelintJsonDiagnostics,
  parseTombiDiagnostics,
  runWorkspaceDiagnostics,
} from "../PostToolUse/workspace-diagnostics.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const hookPath = join(
  repoRoot,
  ".codex",
  "hooks",
  "PostToolUse",
  "workspace-diagnostics.mjs"
);

function runHook(cliArguments = []) {
  return spawnSync(process.execPath, [hookPath, ...cliArguments], {
    cwd: repoRoot,
    encoding: "utf-8",
    input: "",
  });
}

function commandById(id) {
  const result = createWorkspaceDiagnosticCommands().find(
    (candidate) => candidate.id === id
  );
  if (result === undefined) {
    throw new Error(`Missing command fixture: ${id}`);
  }
  return result;
}

function astGrepJson(matches) {
  return JSON.stringify(matches);
}

function astGrepMatch({
  column = 0,
  file = "",
  line = 0,
  message = "match",
  ruleId = "",
  severity = "warning",
}) {
  return {
    file,
    message,
    range: {
      byteOffset: { end: 1, start: 0 },
      end: { column: column + 1, line },
      start: { column, line },
    },
    ruleId,
    severity,
    text: "match",
  };
}

test("accepts only the explicit manual workspace mode", () => {
  const bare = runHook();
  expect(bare.status).toBe(1);
  expect(bare.stderr).toContain("requires exactly one mode");

  const unknown = runHook(["--unknown"]);
  expect(unknown.status).toBe(1);
  expect(unknown.stderr).toContain("--workspace");

  const multiple = runHook(["--workspace", "--unknown"]);
  expect(multiple.status).toBe(1);
  expect(multiple.stderr).toContain("requires exactly one mode");
});

test("workspace settings preserve supported editor diagnostic owners", () => {
  const settings = JSON.parse(
    readFileSync(join(repoRoot, ".vscode", "settings.json"), "utf-8")
  );
  const extensions = JSON.parse(
    readFileSync(join(repoRoot, ".vscode", "extensions.json"), "utf-8")
  );
  expect(settings["python.analysis.diagnosticMode"]).toBe("openFilesOnly");
  expect(settings["python.analysis.exclude"]).toEqual([
    "**/.venv/**",
    "**/.tmp/**",
    "**/dist/**",
    "**/node_modules/**",
  ]);
  expect(settings["python.analysis.extraPaths"]).toEqual([
    "./tools/soleaux/src",
  ]);
  expect(settings["python.analysis.typeCheckingMode"]).toBe("strict");
  expect(settings["python.analysis.useNearestConfiguration"]).toBe(false);
  expect(settings["python.analysis.userFileIndexFollowSymlinkedFolders"]).toBe(
    false
  );
  const workspaceFolderToken = "{workspaceFolder}";
  expect(settings["python.defaultInterpreterPath"]).toBe(
    `$${workspaceFolderToken}/.venv/bin/python`
  );
  expect(settings["ruff.path"]).toBeUndefined();
  expect(settings["astGrep.serverPath"]).toBe("node_modules/.bin/ast-grep");
  expect(settings["js/ts.experimental.useTsgo"]).toBe(true);
  expect(
    settings["typescript.tsserver.experimental.enableProjectDiagnostics"]
  ).toBeUndefined();
  expect(extensions.recommendations).toEqual(
    expect.arrayContaining([
      "ast-grep.ast-grep-vscode",
      "esbenp.prettier-vscode",
      "ms-python.vscode-pylance",
      "tombi-toml.tombi",
      "typescriptteam.native-preview",
    ])
  );
  expect(extensions.recommendations).not.toContain(
    "ms-python.mypy-type-checker"
  );
});

test("defines every uncapped workspace checker through repository-owned CLIs", () => {
  const commands = createWorkspaceDiagnosticCommands();

  expect(commands.map(({ id }) => id)).toEqual([
    "tombi",
    "tombi-format",
    "prettier",
    "eslint",
    "stylelint",
    "ast-grep",
  ]);
  // Stylelint reserves exit 1 for a fatal error and reports lint problems as 2.
  expect(commandById("stylelint").findingsStatus).toBe(2);
  expect(commandById("eslint").findingsStatus).toBe(1);
  expect(commandById("eslint").args).toContain("json");
  expect(commandById("stylelint").args).toContain("--allow-empty-input");
  expect(commands.every(({ timeoutMs }) => timeoutMs === 120_000)).toBe(true);
  expect(commandById("prettier").args).toContain("**/*.{md,mdx,yml,yaml}");
  expect(commandById("tombi").args).toContain("--offline");
  expect(commandById("tombi-format").args).toContain("--offline");
  expect(commandById("ast-grep").args).toContain("--json=compact");
  expect(commandById("ast-grep").args.at(-1)).toBe(".");
  expect(commandById("ast-grep").args).not.toContain("--max-results");
});

test("normalizes ast-grep JSON diagnostics and checker-level fallbacks", () => {
  const diagnostics = parseAstGrepJsonDiagnostics(
    astGrepJson([
      astGrepMatch({
        column: 6,
        file: join(repoRoot, "apps", "corporate-web", "app", "page.tsx"),
        line: 2,
        message: "Unused value.",
        ruleId: "lint/correctness/noUnusedVariables",
        severity: "error",
      }),
      astGrepMatch({
        message: "Configuration warning.",
        severity: "warning",
      }),
    ]),
    commandById("ast-grep"),
    repoRoot
  );

  expect(diagnostics).toEqual([
    {
      code: "ast-grep/lint/correctness/noUnusedVariables",
      column: 7,
      line: 3,
      message: "Unused value.",
      path: "apps/corporate-web/app/page.tsx",
      severity: "error",
    },
    {
      code: "ast-grep/diagnostic",
      column: 1,
      line: 1,
      message: "Configuration warning.",
      path: "sgconfig.yml",
      severity: "warning",
    },
  ]);
});

test("parses ESLint JSON results including fatal parse failures", () => {
  const diagnostics = parseEslintJsonDiagnostics(
    JSON.stringify([
      {
        filePath: join(repoRoot, "apps", "web", "page.tsx"),
        messages: [
          {
            column: 3,
            line: 12,
            message: "Unexpected any.",
            ruleId: "@typescript-eslint/no-explicit-any",
            severity: 2,
          },
          {
            column: 1,
            fatal: true,
            line: 1,
            message: "Parsing error: Unexpected token",
            ruleId: null,
            severity: 2,
          },
          {
            column: 9,
            line: 4,
            message: "Prefer const.",
            ruleId: "prefer-const",
            severity: 1,
          },
        ],
      },
    ]),
    commandById("eslint"),
    repoRoot
  );

  expect(diagnostics).toEqual([
    {
      code: "eslint/@typescript-eslint/no-explicit-any",
      column: 3,
      line: 12,
      message: "Unexpected any.",
      path: "apps/web/page.tsx",
      severity: "error",
    },
    {
      code: "eslint/parse",
      column: 1,
      line: 1,
      message: "Parsing error: Unexpected token",
      path: "apps/web/page.tsx",
      severity: "error",
    },
    {
      code: "eslint/prefer-const",
      column: 9,
      line: 4,
      message: "Prefer const.",
      path: "apps/web/page.tsx",
      severity: "warning",
    },
  ]);
});

test("parses Stylelint JSON warnings", () => {
  expect(
    parseStylelintJsonDiagnostics(
      JSON.stringify([
        {
          source: join(repoRoot, "packages", "ui", "src", "app.css"),
          warnings: [
            {
              column: 1,
              line: 5,
              rule: "at-rule-no-unknown",
              severity: "error",
              text: 'Unknown at-rule "@theme" (at-rule-no-unknown)',
            },
          ],
        },
        { source: "clean.css", warnings: [] },
      ]),
      commandById("stylelint"),
      repoRoot
    )
  ).toEqual([
    {
      code: "stylelint/at-rule-no-unknown",
      column: 1,
      line: 5,
      message: 'Unknown at-rule "@theme" (at-rule-no-unknown)',
      path: "packages/ui/src/app.css",
      severity: "error",
    },
  ]);
});

test("treats a Stylelint fatal exit as invalid and status two as findings", () => {
  const stylelint = commandById("stylelint");
  const findingsPayload = JSON.stringify([
    {
      source: join(repoRoot, "packages", "ui", "src", "app.css"),
      warnings: [
        {
          column: 1,
          line: 5,
          rule: "at-rule-no-unknown",
          severity: "error",
          text: "Unknown at-rule.",
        },
      ],
    },
  ]);

  // Exit 2 is Stylelint's findings contract, so the findings must survive.
  const found = runSingleOwner(stylelint, {
    status: 2,
    stderr: "",
    stdout: findingsPayload,
  });
  expect(found.diagnostics).toHaveLength(1);
  expect(found.owners[0]).toMatchObject({
    blocking: true,
    status: 2,
    valid: true,
  });

  // Exit 1 is Stylelint's fatal error, not findings, so the owner is invalid.
  const fatal = runSingleOwner(stylelint, {
    status: 1,
    stderr: "config error",
    stdout: findingsPayload,
  });
  expect(fatal.diagnostics).toHaveLength(1);
  expect(fatal.diagnostics[0].code).toBe("stylelint/execution");
  expect(fatal.owners[0]).toMatchObject({
    blocking: true,
    status: 1,
    valid: false,
  });
});

test("parses formatter and TOML diagnostics", () => {
  expect(
    parseTombiDiagnostics(
      [
        "  Error: expected value",
        "    at .vscode/settings.toml:4:12",
        '  Error: "tombi.toml" is not formatted',
      ].join("\n"),
      commandById("tombi"),
      repoRoot
    )
  ).toEqual([
    {
      code: "tombi/diagnostic",
      column: 12,
      line: 4,
      message: "expected value",
      path: ".vscode/settings.toml",
      severity: "error",
    },
    {
      code: "tombi/format",
      column: 1,
      line: 1,
      message:
        "File does not match Tombi formatting. Fix: pnpm exec tombi format.",
      path: "tombi.toml",
      severity: "error",
    },
  ]);

  expect(
    parsePrettierDiagnostics(
      "docs/one.md\n.github/workflows/ci.yml\n",
      commandById("prettier"),
      repoRoot
    ).map(({ path }) => path)
  ).toEqual(["docs/one.md", ".github/workflows/ci.yml"]);
});

test("runs later checkers after diagnostics and execution failures", () => {
  const invocations = [];
  const { diagnostics, owners } = runWorkspaceDiagnostics(
    repoRoot,
    (executable, commandArguments) => {
      invocations.push([executable, ...commandArguments]);

      if (
        commandArguments.includes("tombi") &&
        commandArguments.includes("lint")
      ) {
        return {
          status: 1,
          stderr: "",
          stdout: "  Error: expected value\n    at tombi.toml:2:5\n",
        };
      }
      if (
        commandArguments.includes("tombi") &&
        commandArguments.includes("format")
      ) {
        return { status: 0, stderr: "", stdout: "" };
      }
      if (commandArguments.includes("prettier")) {
        return { status: 1, stderr: "", stdout: "docs/example.md\n" };
      }
      if (commandArguments.includes("eslint")) {
        return { status: 0, stderr: "", stdout: "[]" };
      }
      if (commandArguments.includes("stylelint")) {
        return { status: 0, stderr: "", stdout: "[]" };
      }
      if (commandArguments.includes("ast-grep")) {
        return { status: 2, stderr: "invalid config", stdout: "" };
      }
      return { status: 0, stderr: "", stdout: "" };
    }
  );

  expect(invocations).toHaveLength(6);
  expect(diagnostics.map(({ code }) => code)).toEqual(
    expect.arrayContaining([
      "tombi/diagnostic",
      "prettier/format",
      "ast-grep/execution",
    ])
  );
  expect(
    owners.map(({ blocking, id, status, valid }) => [
      id,
      status,
      blocking,
      valid,
    ])
  ).toEqual([
    ["tombi", 1, true, true],
    ["tombi-format", 0, false, true],
    ["prettier", 1, true, true],
    ["eslint", 0, false, true],
    ["stylelint", 0, false, true],
    ["ast-grep", 2, true, false],
  ]);
});

function runSingleOwner(commandSpec, result) {
  return runWorkspaceDiagnostics(repoRoot, () => result, [commandSpec]);
}

test("keeps warning-only owner output visible without blocking the task", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("ast-grep"), {
    status: 0,
    stderr: "",
    stdout: astGrepJson([
      astGrepMatch({
        message: "Prefer a top level regex.",
        ruleId: "r013-as-any",
        severity: "warning",
      }),
    ]),
  });

  expect(diagnostics).toHaveLength(1);
  expect(diagnostics[0].severity).toBe("warning");
  expect(owners[0]).toMatchObject({ blocking: false, status: 0, valid: true });
  // The owner record must carry its own parsed findings, not just a count.
  expect(owners[0].diagnostics).toEqual(diagnostics);
});

test("deduplicates across owners, keeps the highest severity, and orders deterministically", () => {
  const duplicated = astGrepMatch({
    column: 4,
    file: "apps/web/page.tsx",
    line: 2,
    message: "Duplicate finding.",
    ruleId: "r001-example",
    severity: "warning",
  });
  const { diagnostics, owners } = runWorkspaceDiagnostics(
    repoRoot,
    () => ({
      status: 0,
      stderr: "",
      stdout: astGrepJson([
        astGrepMatch({
          column: 0,
          file: "zzz/last.tsx",
          line: 9,
          message: "Later path.",
          ruleId: "r002-example",
          severity: "warning",
        }),
        duplicated,
        { ...duplicated, severity: "error" },
      ]),
    }),
    [commandById("ast-grep"), commandById("ast-grep")]
  );

  // Two owners emit the same three matches; dedup collapses them to two entries.
  expect(owners).toHaveLength(2);
  expect(owners[0].diagnostics).toHaveLength(3);
  expect(diagnostics).toHaveLength(2);
  expect(diagnostics.map(({ path }) => path)).toEqual([
    "apps/web/page.tsx",
    "zzz/last.tsx",
  ]);
  // The duplicate pair collapses to its highest severity.
  expect(diagnostics[0].severity).toBe("error");
});

test("blocks when an owner exits nonzero for effective error severity", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("ast-grep"), {
    status: 1,
    stderr: "",
    stdout: astGrepJson([
      astGrepMatch({
        message: "Unresolved contract projection.",
        ruleId: "r006-no-contract-projection",
        severity: "error",
      }),
    ]),
  });

  expect(diagnostics).toHaveLength(1);
  expect(owners[0]).toMatchObject({ blocking: true, status: 1, valid: true });
});

test("replaces source findings with one owner diagnostic when a validator fails", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("ast-grep"), {
    status: 2,
    stderr: "sgconfig.yml: unknown field",
    stdout: astGrepJson([
      astGrepMatch({ message: "Stale finding.", ruleId: "r001-example" }),
    ]),
  });

  expect(diagnostics).toHaveLength(1);
  expect(diagnostics[0].code).toBe("ast-grep/execution");
  expect(diagnostics[0].message).toContain("sgconfig.yml: unknown field");
  expect(owners[0]).toMatchObject({ blocking: true, status: 2, valid: false });
});

test("treats an unparsable owner result as an invalid owner", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("ast-grep"), {
    status: 0,
    stderr: "",
    stdout: "not json",
  });

  expect(diagnostics).toHaveLength(1);
  expect(diagnostics[0].code).toBe("ast-grep/execution");
  expect(owners[0]).toMatchObject({ blocking: true, valid: false });
});

test("treats a signalled or statusless owner as an invalid owner", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("prettier"), {
    signal: "SIGTERM",
    status: null,
    stderr: "",
    stdout: "",
  });

  expect(diagnostics).toHaveLength(1);
  expect(diagnostics[0].code).toBe("prettier/execution");
  expect(owners[0]).toMatchObject({
    blocking: true,
    status: null,
    valid: false,
  });
});

test("reports a clean owner as nonblocking with no diagnostics", () => {
  const { diagnostics, owners } = runSingleOwner(commandById("prettier"), {
    status: 0,
    stderr: "",
    stdout: "",
  });

  expect(diagnostics).toEqual([]);
  expect(owners[0]).toMatchObject({ blocking: false, status: 0, valid: true });
});

test("emits the exact single-line terminal diagnostic contract", () => {
  const output = formatWorkspaceDiagnostic({
    code: "ast-grep/lint/example",
    column: 9,
    line: 7,
    message: "First line\nsecond line",
    path: "apps/example.ts",
    severity: "error",
  });

  expect(output).toBe(
    `${DIAGNOSTIC_PREFIX}apps/example.ts:7:9: error [ast-grep/lint/example] First line second line`
  );
});

test("publishes manual VS Code workspace diagnostics to the Problems panel", () => {
  const tasks = JSON.parse(
    readFileSync(join(repoRoot, ".vscode", "tasks.json"), "utf-8")
  );
  const settings = JSON.parse(
    readFileSync(join(repoRoot, ".vscode", "settings.json"), "utf-8")
  );

  const workspaceTask = tasks.tasks.find(
    ({ label }) => label === "Diagnostics: Workspace Linters"
  );
  expect(workspaceTask).toMatchObject({
    args: [".codex/hooks/PostToolUse/workspace-diagnostics.mjs", "--workspace"],
    command: "node",
    presentation: { revealProblems: "onProblem" },
    type: "process",
  });
  expect(workspaceTask.problemMatcher).toEqual({
    applyTo: "allDocuments",
    fileLocation: ["relative", ["$", "{workspaceFolder}"].join("")],
    owner: "workspace-diagnostics",
    pattern: {
      code: 5,
      column: 3,
      file: 1,
      line: 2,
      message: 6,
      regexp:
        "^::workspace-diagnostic::(.+):(\\d+):(\\d+):\\s+(error|warning|info)\\s+\\[([^\\]]+)\\]\\s+(.+)$",
      severity: 4,
    },
    source: "Workspace diagnostics",
  });

  const problemPattern = new RegExp(
    workspaceTask.problemMatcher.pattern.regexp,
    "u"
  );
  const problem = problemPattern.exec(
    `${DIAGNOSTIC_PREFIX}apps/example.ts:7:9: warning [ultracite/lint/example] First line second line`
  );
  expect(problem.slice(1)).toEqual([
    "apps/example.ts",
    "7",
    "9",
    "warning",
    "ultracite/lint/example",
    "First line second line",
  ]);

  const refreshTask = tasks.tasks.find(
    ({ label }) => label === "Diagnostics: Refresh Workspace"
  );
  expect(refreshTask).toMatchObject({
    dependsOn: ["Root: Typecheck", "Diagnostics: Workspace Linters"],
    runOptions: { instanceLimit: 1 },
  });
  expect(refreshTask.runOptions.runOn).toBeUndefined();
  expect(settings["task.allowAutomaticTasks"]).toBeUndefined();
  expect(normalizeDiagnosticPath("./apps/example.ts", repoRoot)).toBe(
    "apps/example.ts"
  );
});
