# Prompting And Workflows Playbook

Sources verified 2026-07-28:

- https://learn.chatgpt.com/guides/best-practices
- https://learn.chatgpt.com/docs/prompting
- https://learn.chatgpt.com/docs/long-running-work
- https://learn.chatgpt.com/docs/developer-commands?surface=cli
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://developers.openai.com/cookbook/articles/codex_exec_plans

Supplement with `prompt-cache-and-surface-audit.md` for 2026-05-27 OpenAI prompt-cache and surface-audit findings.

## Intent

Use prompts and workflows to define the actual job Codex must deliver. A good Codex prompt narrows the objective, gives the relevant context, states constraints, and defines how completion will be proven.

## Prompt Template

Use this structure for nontrivial work:

```text
Goal: <concrete outcome>
Context: <repo paths, failing behavior, user constraints>
Constraints: <must/must-not, ownership, safety, style>
Done when: <checks, files changed, observed behavior>
```

Add repro steps, error output, screenshots, or failing tests when they are relevant. Do not ask for broad "improvements" without an acceptance boundary.

## Parallel Research Template

Use this structure when the task is exploration-heavy and benefits from bounded parallel subagents:

```text
Use parallel subagents for research only.

Spawn:
1. Map: identify relevant files, sources, symbols, flows, or document sections.
2. Context: verify upstream docs, external references, background concepts, and version-specific behavior.
3. Details: trace APIs, data shapes, edge cases, equations, figures, or call paths.
4. Skeptic: identify contradictions, weak evidence, missing checks, and unverified assumptions.

Each subagent must return:
- scope covered
- key findings
- file references, source links, or command evidence
- uncertainties and contradictions
- blockers, if any
- recommended next step

Wait for all subagents, compare results, resolve contradictions, and synthesize one final answer. Do not edit files unless I explicitly approve an implementation phase.
```

## Workflow Selection

- Explain codebase: ask for map, owners, and evidence. Do not edit.
- Research or exploration: use parallel subagents only when the slices are independent and read-heavy; keep final synthesis in the parent.
- Fix bug: give repro, expected behavior, affected paths, and validation command.
- Write tests: provide behavior contract and where tests should live.
- UI iteration: provide target viewport, acceptance details, and screenshot verification expectation.
- Review: ask for findings first, ordered by severity, with file/line evidence.
- Docs update: name the source of truth and require link or command validation.

## Goal Mode

- Use a goal for work that needs a persistent objective and automatic continuation. Handle small, direct tasks without one.
- Treat goal text as both the first prompt and the completion criteria. Include the outcome, material constraints, and verification that proves completion.
- Use `/plan` first when the outcome or constraints are unclear, then start the refined goal.
- Steer a running goal with new context. Edit the goal and any task control document when the accepted completion contract changes; do not silently drift.
- A goal does not broaden task authority, sandboxing, approvals, or scope.
- Do not use a goal as a task-list substitute. Goal text is the starting prompt plus completion criteria; it does not hold task UUIDs, dependency records, change inventories, or arbitrary metadata.

## Task And Persistence Surfaces

- Use `/plan` to shape execution before implementation. It is planning mode, not a persistent task-list data model.
- Use `/goal` only when the user wants a persistent larger objective or completion contract.
- Use `$exec-plans` for an explicitly selected two-file, multi-session execution bundle. When paired with a goal, point the goal to `PLAN.md`; keep `TASKS.md` as the sole live tracker.
- Use `codex cloud` tasks only for actual Codex Cloud work submitted or inspected through `codex cloud exec`, `codex cloud list`, `codex cloud status`, `codex cloud diff`, or `codex cloud apply`.
- Treat `history.persistence` and `sqlite_home` as transcript and resumable-runtime persistence. They do not create a local structured task-list object.
- When a workflow needs task records, dependency edges, ownership metadata, change inventories, or verification metadata and no native task-list tool is exposed, require a clearly labeled fallback execution record in the transcript or plan history and mirror live status through the current session tracker when one is available. Do not describe that fallback as a Codex-native task list.

## Thread Hygiene

- Use one thread per task.
- Avoid two active threads editing the same files.
- Keep critical instructions in the active prompt or durable repo context because long threads may compact.
- Keep the static instruction prefix stable. Put volatile facts such as current repro data, tenant/user context, timestamps, retrieval results, or session findings later in the user prompt or owner-specific artifact.

## Repo Delivery Pattern

- User constraints are invariants.
- Repo instructions come from live files.
- Closeout reports should state what changed, what was synced or verified, and any real blocker.
