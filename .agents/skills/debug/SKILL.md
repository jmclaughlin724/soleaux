---
name: debug
description: Diagnose reproducible failures, regressions, runtime errors, flaky tests, integration faults, unexpected tool or command failures, and other surprising behavior by tracing evidence to the owning boundary. Use when asked to debug, investigate, explain a failure, or implement a verified fix, and whenever an in-scope tool, build, test, command, or validation fails unexpectedly during another task.
---

# Debug

## Contract

Use an evidence-led workflow that separates the observed symptom from its cause, tests competing explanations efficiently, and changes code only when the request authorizes a fix.

## Direct Workflow

### 1. Establish the contract

- Record the exact expected behavior, actual behavior, reproduction path, environment, and earliest known failure.
- Treat an unexpected failure produced by an in-scope command as the workflow's active diagnostic input until evidence shows it is unrelated. Apply the root `AGENTS.md` scope boundary while tracing its owner.
- Determine whether the request authorizes diagnosis only or also implementation. Read-only investigation does not authorize a fix.
- Inspect the worktree before changing anything. Preserve unrelated and overlapping user changes.

### 2. Route to the owner

- Start at the user-named file, symbol, command, failure, or location.
- Read every applicable instruction file on the root-to-target chain.
- For unfamiliar, cross-subtree, or ownership-ambiguous failures, load repository task context before broad exploration.
- Identify the authoritative owner, its configured consumers, and the narrowest command that exercises the failing path.
- When a defect class reached the user unflagged, identify the delivery surface that should have caught it and whether that surface covers the affected file family.

### 3. Reproduce and preserve evidence

- Reproduce through the same manifest, configuration, working directory, and environment used by the real consumer.
- Treat editor and IDE diagnostics as probe-class observations: reproduce them through the repository's configured checker invocation (locked version, configured environment) before classifying them as product defects. A diagnostic that does not reproduce under the configured check is environment or probe behavior.
- Capture the narrowest sufficient error, stack, input, output, timestamp, and version information. Redact credentials, prompts, personal data, and environment values.
- If the reported failure does not reproduce, compare the user's path with the probe before drawing conclusions.
- Classify each observation as product code, test or fixture, dependency or version, configuration or integration, environment or permission, or diagnostic-probe behavior.

### 4. Test hypotheses

- Form two to four ranked, falsifiable hypotheses from the evidence.
- Treat mass "unknown type", unresolved-import, or missing-symbol diagnostics as an environment-resolution hypothesis first: verify the checker's execution environment (interpreter, activated virtualenv, configured venv path) resolves dependencies before believing type-system output.
- Select the probe that best distinguishes the leading hypotheses; change one variable at a time.
- Use authoritative upstream documentation only when behavior depends on an external version, API, platform, or tool contract.
- Treat warnings, timing correlations, and nearby changes as leads, not proof. Trace the failing value or control path to the first incorrect state.

### 5. Fix only the confirmed owner

When a fix is authorized and the root cause is supported:

- Patch the narrowest complete ownership boundary; do not add compatibility layers or adjacent cleanup.
- Add or update a regression test that fails for the confirmed cause, not merely the visible symptom.
- Preserve public contracts unless the request explicitly changes them.

If a fix is not authorized, stop after producing an evidence-backed diagnosis and a concrete repair recommendation.

### 6. Verify the claim

- Re-run the original reproduction first.
- Exercise the owner-provided test and at least one risk-selected negative or adjacent case.
- Review the final in-scope diff and search for stale names, paths, or duplicated owners.
- Distinguish executed evidence from suggested checks. If uncertainty remains, state the leading explanation and the next discriminating probe.

## Codex and agent-runtime failures

For Codex, MCP, hook, skill, plugin, or agent-harness problems, also inspect the effective configuration and runtime advertisement boundary:

- use `codex doctor --json`, `codex features list`, and `codex mcp list/get` where applicable;
- compare system, user, and project configuration precedence;
- confirm configured, enabled, initialized, advertised, authenticated, and callable as separate states; and
- inspect only bounded diagnostic log excerpts and metadata.

Never dump complete transcripts, prompts, daemon rosters, environment blocks, tokens, or credential stores into tool output or the final report.

## Report

Lead with the outcome, then provide:

1. the reproduced symptom and evidence;
2. the confirmed root cause or ranked remaining hypotheses;
3. the narrow fix, if authorized;
4. verification executed; and
5. residual risk or the next decisive probe.
