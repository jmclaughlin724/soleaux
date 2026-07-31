import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateSourceMutationBoundaryGuard } from "../PreToolUse/source-mutation-boundary-guard.mjs";

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
const nestedCwd = fileURLToPath(new URL("../PreToolUse", import.meta.url));

function bash(command, cwd = repoRoot) {
  return {
    bash: parseBash(command),
    cwd,
  };
}

test.each([
  ['printf "export const bad = 1;\\n" > apps/web/bad.ts', "redirection"],
  ['echo "debugger;" >> apps/web/page.tsx', "redirection"],
  ['printf "debugger;\\n" | tee apps/web/page.tsx', "tee"],
  ["touch apps/web/empty.ts", "touch"],
  ["truncate -s 0 apps/web/page.tsx", "truncate"],
  ["cp /tmp/generated.ts apps/web/generated.ts", "cp"],
  ["cp /tmp/generated.ts apps/web", "cp"],
  ["cp -t apps/web /tmp/generated.ts", "cp"],
  ["cp -r apps/web apps/web-copy", "directory mutation"],
  ["mv /tmp/generated.ts apps/web/generated.ts", "mv"],
  ["mv /tmp/generated.py tools/", "mv"],
  ["mv apps/web apps/web-renamed", "directory mutation"],
  ["mv /tmp apps/imported-tmp", "directory mutation"],
  ["install /tmp/generated.py tools/generated.py", "install"],
  ["dd if=/tmp/generated.js of=apps/web/generated.js", "dd"],
  ["sed -i.bak 's/a/b/' apps/web/page.tsx", "sed"],
  ["perl -pi -e 's/a/b/' apps/web/page.tsx", "Perl"],
  ["unknown-codegen --output apps/web/generated.ts", "output option"],
  [
    'node -e \'writeFileSync("apps/web/generated.ts", "debugger;")\'',
    "inline code",
  ],
  [
    'python -c \'open("tools/generated.py", "w").write("print(1)")\'',
    "inline code",
  ],
  ["patch -p1 < /tmp/change.patch", "patch command"],
  ["git apply /tmp/change.patch", "Git worktree mutation"],
  ["git mv apps/web/old.ts apps/web/new.ts", "git mv"],
  ["git rm apps/web/obsolete.ts", "git rm"],
  ["git rm --cached -r tools/ast-grep", "git rm"],
  ["git rm --cached -f apps/web/obsolete.ts", "git rm"],
  ["git rm --cached --force apps/web/obsolete.ts", "git rm"],
  ["git rm --cached '*.ts'", "git rm"],
  ["git rm --cached ../outside.ts", "git rm"],
  ["git rm --cached", "git rm"],
  [
    "git restore --staged --worktree apps/web/page.tsx",
    "Git worktree mutation",
  ],
])("blocks unstructured governed-source mutation", (command, label) => {
  expect(evaluateSourceMutationBoundaryGuard(bash(command))).toContain(label);
});

test("blocks compound and nested mutations independently of prior commands", () => {
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash('pnpm fix && printf "debugger;\\n" > apps/web/page.tsx')
    )
  ).toContain("apps/web/page.tsx");
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash("bash -lc 'touch apps/web/nested.ts'")
    )
  ).toContain("apps/web/nested.ts");
});

test("resolves governed targets from nested repository directories", () => {
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash("touch ../../../apps/web/from-nested.ts", nestedCwd)
    )
  ).toContain("apps/web/from-nested.ts");
});

test("allows index-only untracking of exact repository paths", () => {
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash("git rm --cached apps/web/obsolete.ts")
    )
  ).toBeNull();
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash("git rm --cached -- apps/web/obsolete.ts")
    )
  ).toBeNull();
  expect(
    evaluateSourceMutationBoundaryGuard(
      bash("git rm --cached ../../../apps/web/obsolete.ts", nestedCwd)
    )
  ).toBeNull();
});

test.each([
  'printf "{\\"ok\\":true}\\n" > /tmp/report.json',
  'printf "notes\\n" > notes.txt',
  "cp apps/web/page.tsx /tmp/page.tsx",
  "cp -t /tmp apps/web/page.tsx",
  "cp /tmp/report.txt apps/web",
  "git apply --check /tmp/change.patch",
  "git rm --dry-run apps/web/obsolete.ts",
  "git restore --staged apps/web/page.tsx",
  "git status",
  "node -e 'console.log(process.cwd())'",
  "python -c 'print(1)'",
])("keeps outside or non-source outputs outside the gate: %s", (command) => {
  expect(evaluateSourceMutationBoundaryGuard(bash(command))).toBeNull();
});

test.each([
  "pnpm fix",
  "pnpm run fix:prose",
  "pnpm fix:toml",
  "pnpm db:types",
  "pnpm supaschema:types",
  "pnpm ast-grep:update-snapshots",
  "pnpm exec ultracite fix",
  "pnpm exec prettier --write apps/web/page.tsx",
  `pnpm --dir ${repoRoot} exec prettier --write apps/web/page.tsx`,
  "pnpm exec ruff format tools/example.py",
  "node supabase/scripts/database-types.mjs",
])("allows the repository-owned mutator: %s", (command) => {
  expect(evaluateSourceMutationBoundaryGuard(bash(command))).toBeNull();
});
