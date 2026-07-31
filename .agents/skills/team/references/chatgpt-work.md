# ChatGPT Work Subagents

Source verified 2026-07-16:

- https://learn.chatgpt.com/docs/agent-configuration/subagents

## Use This Reference

Use this reference for hosted ChatGPT Work tasks. Do not apply local Codex `.codex/agents/**`, `config.toml`, sandbox modes, or approval settings to this runtime.

## Workflow

1. Choose a task whose workstreams are independent and bounded.
2. At most intelligence levels, request delegation explicitly. Eligible Ultra sessions can delegate proactively when parallel agents would materially improve speed or quality.
3. Name the split, require ChatGPT to wait for every requested result, and define the consolidated output.
4. Keep requirements, decisions, reconciliation, and final validation in the main task.
5. Inspect the task's subagent activity and returned results when the interface exposes them.

Prompt shape:

```text
Use parallel subagents for this task. Assign one bounded workstream per agent: <workstreams>.
Wait for all requested results, compare their evidence, resolve conflicts, and return <output>.
Do not perform <excluded side effects>.
```

ChatGPT Work subagents run in the hosted environment with the tools available to the parent task. Website and connector permissions remain tool-specific. Delegation does not add authority or bypass approval requirements.

## Verification

- Confirm every requested workstream appears in the consolidated result.
- Check that agent summaries contain evidence rather than raw exploration dumps.
- Verify that the main task reconciles disagreements and owns the final answer.
- Compare the workflow's quality, latency, and token cost with a single-agent task when adopting it for repeat use.
