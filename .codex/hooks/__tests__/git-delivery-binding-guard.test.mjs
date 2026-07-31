import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, expect, test } from "vitest";

import { parseBash } from "../PreToolUse/bash-ast.mjs";
import { evaluateGitDeliveryBindingGuard } from "../PreToolUse/git-delivery-binding-guard.mjs";

const wildcardFetch = "+refs/heads/*:refs/remotes/origin/*";
const mainOnlyFetch = "+refs/heads/main:refs/remotes/origin/main";
const temporaryRoots = [];

afterEach(() => {
  const roots = [...temporaryRoots];
  temporaryRoots.length = 0;
  for (const root of roots) {
    rmSync(root, { force: true, recursive: true });
  }
});

function git(cwd, ...arguments_) {
  return execFileSync("git", arguments_, {
    cwd,
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function createRepository({
  branch = "codex/topic",
  fetch = wildcardFetch,
} = {}) {
  const root = mkdtempSync(join(tmpdir(), "git-delivery-"));
  temporaryRoots.push(root);
  const remote = join(root, "remote.git");
  git(root, "init", "--initial-branch", branch);
  git(root, "config", "user.email", "agent@example.com");
  git(root, "config", "user.name", "Agent");
  writeFileSync(join(root, "README.md"), "test\n");
  git(root, "add", "README.md");
  git(root, "commit", "-m", "initial");
  git(root, "init", "--bare", remote);
  git(root, "remote", "add", "origin", remote);
  git(root, "config", "--replace-all", "remote.origin.fetch", fetch);
  return { branch, root };
}

function bash(repository, command) {
  return {
    bash: parseBash(command),
    cwd: repository.root,
  };
}

function establishUpstream(repository) {
  git(
    repository.root,
    "update-ref",
    `refs/remotes/origin/${repository.branch}`,
    "HEAD"
  );
  git(
    repository.root,
    "config",
    `branch.${repository.branch}.remote`,
    "origin"
  );
  git(
    repository.root,
    "config",
    `branch.${repository.branch}.merge`,
    `refs/heads/${repository.branch}`
  );
}

test("requires the wildcard origin fetch mapping", () => {
  const repository = createRepository({ fetch: mainOnlyFetch });
  const reason = evaluateGitDeliveryBindingGuard(
    bash(repository, `git push --set-upstream origin ${repository.branch}`)
  );
  expect(reason).toContain(wildcardFetch);
  expect(reason).toContain("git remote set-branches origin '*'");
  expect(reason).toContain("git fetch origin --prune");
  expect(reason).not.toContain("git branch --set-upstream-to");
});

test("permits only the exact origin fetch-refspec repair", () => {
  const repository = createRepository({ fetch: mainOnlyFetch });
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, "git remote set-branches origin '*'")
    )
  ).toBeNull();
  for (const command of [
    "git remote set-branches upstream '*'",
    "git remote set-branches origin main",
    "git -C . remote set-branches origin '*'",
  ]) {
    expect(
      evaluateGitDeliveryBindingGuard(bash(repository, command))
    ).toContain("git remote set-branches origin '*'");
  }
});

test("permits only exact first-publication binding", () => {
  const repository = createRepository();
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, `git push --set-upstream origin ${repository.branch}`)
    )
  ).toBeNull();
  for (const command of [
    `git push origin ${repository.branch}`,
    `git push --set-upstream upstream ${repository.branch}`,
    "git push --set-upstream origin HEAD",
    "git push --set-upstream origin codex/other",
    "git push",
  ]) {
    expect(evaluateGitDeliveryBindingGuard(bash(repository, command))).toMatch(
      /^BLOCKED:/u
    );
  }
});

test("rejects force pushes and diagnostic publication probes", () => {
  const repository = createRepository();
  establishUpstream(repository);
  for (const command of [
    `git push --force origin ${repository.branch}`,
    `git push --force-with-lease origin ${repository.branch}`,
    `git push --force-if-includes origin ${repository.branch}`,
    `git push -f origin ${repository.branch}`,
    `git push origin +${repository.branch}`,
    `git push origin ${repository.branch} | head -1`,
  ]) {
    expect(evaluateGitDeliveryBindingGuard(bash(repository, command))).toMatch(
      /^BLOCKED:/u
    );
  }
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(
        repository,
        `git push --dry-run origin ${repository.branch} | head -1`
      )
    )
  ).toBeNull();
});

test("does not treat branch metadata alone as an established upstream", () => {
  const repository = createRepository();
  git(
    repository.root,
    "config",
    `branch.${repository.branch}.remote`,
    "origin"
  );
  git(
    repository.root,
    "config",
    `branch.${repository.branch}.merge`,
    `refs/heads/${repository.branch}`
  );
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, `git push --set-upstream origin ${repository.branch}`)
    )
  ).toBeNull();
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, `git push origin ${repository.branch}`)
    )
  ).toContain(`git push --set-upstream origin ${repository.branch}`);
});

test("requires upstream repair when the matching remote ref already exists", () => {
  const repository = createRepository();
  git(
    repository.root,
    "update-ref",
    `refs/remotes/origin/${repository.branch}`,
    "HEAD"
  );
  const reason = evaluateGitDeliveryBindingGuard(
    bash(repository, `git push --set-upstream origin ${repository.branch}`)
  );
  expect(reason).toContain(
    `git branch --set-upstream-to origin/${repository.branch} ${repository.branch}`
  );
  expect(reason).not.toContain(`git push --set-upstream origin`);
});

test("permits only exact later publication to the established upstream", () => {
  const repository = createRepository();
  establishUpstream(repository);
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, `git push origin ${repository.branch}`)
    )
  ).toBeNull();
  for (const command of [
    `git push --set-upstream origin ${repository.branch}`,
    `git push upstream ${repository.branch}`,
    "git push origin codex/other",
    "git push origin HEAD",
    "git push",
  ]) {
    expect(evaluateGitDeliveryBindingGuard(bash(repository, command))).toMatch(
      /^BLOCKED:/u
    );
  }
});

test("allows only exact same-branch upstream repair with a known remote ref", () => {
  const repository = createRepository();
  git(
    repository.root,
    "update-ref",
    `refs/remotes/origin/${repository.branch}`,
    "HEAD"
  );
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(
        repository,
        `git branch --set-upstream-to origin/${repository.branch} ${repository.branch}`
      )
    )
  ).toBeNull();
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, `git branch -u origin/${repository.branch} codex/other`)
    )
  ).toMatch(/^BLOCKED:/u);
  git(
    repository.root,
    "update-ref",
    "-d",
    `refs/remotes/origin/${repository.branch}`
  );
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(
        repository,
        `git branch --set-upstream-to origin/${repository.branch} ${repository.branch}`
      )
    )
  ).toContain("is missing");
});

test("requires an equal origin tracking ref before pull-request creation", () => {
  const repository = createRepository();
  establishUpstream(repository);
  expect(
    evaluateGitDeliveryBindingGuard(bash(repository, "gh pr create"))
  ).toBeNull();
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, "gh pr create --head codex/other")
    )
  ).toContain("--head");
  expect(
    evaluateGitDeliveryBindingGuard(
      bash(repository, "gh pr create --repo other/repository")
    )
  ).toContain("repository target");

  writeFileSync(join(repository.root, "changed.txt"), "next\n");
  git(repository.root, "add", "changed.txt");
  git(repository.root, "commit", "-m", "next");
  expect(
    evaluateGitDeliveryBindingGuard(bash(repository, "gh pr create"))
  ).toContain("stale");

  git(
    repository.root,
    "update-ref",
    "-d",
    `refs/remotes/origin/${repository.branch}`
  );
  expect(
    evaluateGitDeliveryBindingGuard(bash(repository, "gh pr create"))
  ).toContain("is missing");
});

test("denies delivery from detached and protected branches", () => {
  const detached = createRepository();
  git(detached.root, "checkout", "--detach");
  const detachedReason = evaluateGitDeliveryBindingGuard(
    bash(detached, `git push --set-upstream origin ${detached.branch}`)
  );
  expect(detachedReason).toContain("detached");
  expect(detachedReason).not.toContain("Repair with:");

  const protectedRepository = createRepository({ branch: "main" });
  const protectedReason = evaluateGitDeliveryBindingGuard(
    bash(protectedRepository, "git push --set-upstream origin main")
  );
  expect(protectedReason).toContain("protected branch");
  expect(protectedReason).not.toContain("Repair with:");
});
