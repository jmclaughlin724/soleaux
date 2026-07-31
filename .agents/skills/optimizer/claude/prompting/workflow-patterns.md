# Workflow Patterns

> For Claude Code's built-in `/workflows` dynamic-orchestration feature (JS scripts, `ultracode`), see [dynamic-workflows.md](dynamic-workflows.md). This file covers wave-based agent orchestration patterns.

Use these patterns when documenting multi-step agent workflows in commands or skills.

## Wave-based parallelism

- Split parallel work into waves when tasks edit disjoint files.
- Keep shared config files in their own wave.
- End with a verification wave.

## Current delegation model

- Use the `Agent` tool for explicit delegated work. `Task` is its former name.
- Agents run in the background by default; a result arrives as a completion notification. Set `background: false` only when the next step is blocked on that result.
- Issue a wave's `Agent` calls in one message so they run concurrently.
- Keep urgent blocking work local unless the user explicitly asked for delegation.
- Resume a prior agent with `SendMessage`, not a new `Agent` call.

## Anti-patterns

- Referring to the old `Task` tool name, `subagent_type`, or the OpenAI `spawn_agent`/`wait_agent` actions, which belong to the Responses API and not to Claude Code
- Polling for a result with `TaskOutput`, which no subagent can call
- Delegating work that edits the same files in parallel
- Forcing `background: false` after every spawn instead of doing local non-overlapping work
- Forcing delegation for ordinary work when the user did not ask for subagents
- Predicting or fabricating a pending agent's result instead of waiting for its notification
