# Session Prompts And Handoffs

## Contract

Use this reference for prompts consumed by a future AI agent or coding session. The prompt must be executable from a cold start, inherit the real instruction hierarchy, preserve current verified state, and authorize only the named mode and scope.

## Select One Primary Mode

| Mode | Authorized outcome |
| --- | --- |
| Answer or explain | Inspect relevant evidence and answer; no implementation |
| Review | Produce prioritized, evidence-backed findings; no edits unless separately authorized |
| Diagnose | Reproduce and isolate the cause; do not implement a fix unless requested |
| Research | Retrieve and synthesize evidence with source and recency requirements |
| Plan | Produce an implementation-ready plan; do not perform it |
| Implement | Make the named in-scope changes and run relevant non-destructive validation |
| Handoff | Continue the actual accepted work from its verified state and stop at its completion bar |

Use a hybrid only when the requested outcome genuinely crosses modes, and name the transition and authority for each phase. Never let a generic workflow turn a read-only mode into implementation.

## Build The Evidence Ledger

For repository-bound prompts, inspect and separate:

- user requirements and explicit non-goals;
- applicable root and scoped instructions;
- verified owners, consumers, paths, symbols, contracts, and commands;
- work completed and why each decision was made;
- fresh commands or probes and their observed results;
- in-scope worktree changes the next session must preserve;
- assumptions and facts the next session must verify;
- external credentials, approvals, private documentation, or activation gates;
- unrelated dirty or concurrent work that is explicitly excluded.

This is prompt-grounding work, not authority to execute the future session's substantive task. Stop discovery once the control document has a real owner, verified starting context, and an executable first action. Do not turn investigation history into the baseline. Carry the latest corrected state. Do not present an old test result or inferred capability as current proof.

## Compose A Cold-Start Control Document

Include only the sections needed for the mode:

1. **Objective and mode** — one user-visible outcome and the allowed action class.
2. **Instruction sources** — the live root/scoped owners the next session must read; do not copy or reorder higher-level authority.
3. **Verified baseline** — facts needed to start, with paths and observed results.
4. **Scope and exclusions** — named files, owners, plan items, or behaviors; exclude unrelated drift.
5. **Assumptions and blockers** — facts to verify and external gates.
6. **Next executable action** — the first useful read, command, or edit a fresh session can perform.
7. **Required outcome and workflow** — task-specific requirements; prescribe order only when it affects correctness.
8. **Validation** — the narrowest checks supported by live owner instructions or explicit risk.
9. **Failure and stop behavior** — when to retry, ask, report, or stop.
10. **Output and done criteria** — what the next session returns and what proves completion.

Reference existing repository autonomy, approval, safety, and worktree policy instead of duplicating it. Add only task-specific constraints or exceptions.

For long-running tool work, add a host-appropriate collaboration contract only when higher-priority instructions do not already own it: one short preamble naming the first useful step, then updates only at major phase changes or when a finding changes the plan. Do not require fixed update intervals or routine tool narration.

## Durable Handoffs

A handoff must say:

- what has been completed;
- why the key choices were made;
- what remains in the accepted task or plan;
- the exact next executable action;
- which checks already ran and what they observed;
- which failures remain in scope;
- what is blocked externally;
- what concurrent or dirty work is excluded;
- when the continuation must stop.

Do not imply that a narrowed closeout completed a broader plan. Do not carry unrelated cleanup, generated drift, or another session's work merely because it is visible in the worktree.

Persist a handoff only when the user requests a durable artifact or cross-session continuation. Use an existing plan, task, issue, or owner-defined handoff location. Otherwise return it directly.

## Choose The Portability Boundary

Classify the artifact before resolving paths:

- **Portable, public, or shared:** name the repository root or working directory without embedding a user-specific machine path. Use repository-relative paths, or keep a neutral path placeholder in a clearly labeled reusable template.
- **Private same-machine handoff:** use a verified absolute path only when the next session runs in the same environment and needs that path to locate the source. Do not carry the path into a shared artifact.

If the destination or reuse boundary is unclear, default to the portable form. Path precision does not justify exposing usernames, home directories, private mount names, or other machine-specific context.

## Commands, Placeholders, And Data

- Include an exact command only after verifying that it exists and applies. Otherwise name the check category and require owner discovery first.
- Remove unresolved placeholders from a final prompt. Placeholders belong only in a clearly labeled template such as [session-agent-prompt-template.md](../assets/session-agent-prompt-template.md).
- Never include secrets, raw credentials, full environment files, private keys, unnecessary personal data, or customer records. Direct the future session to approved secure access without copying values into chat.

## Cold-Read Pass

Read the prompt without the current conversation and verify that a new session can answer:

1. What outcome am I authorized to complete?
2. What evidence is verified, assumed, stale, blocked, or excluded?
3. What should I do first?
4. Which live instructions and owners govern the work?
5. Which validation is required and why?
6. What must I not change or claim?
7. Exactly when am I done?
