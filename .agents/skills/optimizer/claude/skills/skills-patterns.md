# Skills Patterns

Best practices for creating Claude Code skills with controlled context overhead.

> Sources: https://code.claude.com/docs/en/skills and https://code.claude.com/docs/en/memory

## Choose The Right Surface First

Before creating a skill, decide whether the guidance should live somewhere with a different context-loading profile.

| Need | Use | Why |
| --- | --- | --- |
| Always-needed project instructions | `AGENTS.md` | loads at session start |
| File- or directory-specific persistent guidance | `.claude/rules/*.md` with `paths` | only applies where needed |
| Reusable expertise or workflow | skill | loads when relevant |
| Isolated execution or verbose work | subagent | separate context window |

Create a skill only when the content should not live in always-on startup context. In this repo, `CLAUDE.md` files are Claude runtime entry points that import adjacent `AGENTS.md` guidance.

Claude Code ships a set of bundled skills — `/doctor`, `/code-review`, `/batch`, `/debug`, `/loop`, `/claude-api`, and others — that demonstrate good skill design patterns. The set is open and grows between releases; check `/` rather than treating any list as complete. `/verify` and `/code-review` run only when invoked (v2.1.215+), and `/code-review` runs as a forked subagent from v2.1.218.

## How Skills Load

Claude loads skills in stages:

| Stage | When | What loads |
| --- | --- | --- |
| Metadata | startup | `name` and `description` (harness listing) |
| Body | when the skill is invoked | `SKILL.md` body only — `!`-command blocks expand; `@`-mentions do not |
| References | on demand | files under `references/`, `scripts/`, `assets/`, loaded only when the model `Read`s them |

Reference loading — lazy markdown links, the inert `@`-mention, no auto-walk, and the traversal guard — is owned by [dynamic-context-and-runtime.md §1a](../config/dynamic-context-and-runtime.md). Do not write `@references/x.md` expecting eager load; it stays literal text in a skill body.

Context implication:

- descriptions should be strong but compact because they are startup context
- `SKILL.md` should stay focused on essentials
- detailed examples and variants belong in references, reached by a markdown link + `Read`

## File Structure

```text
skill-name/
├── SKILL.md
├── references/
├── scripts/
└── assets/
```

- `SKILL.md` is the only required file
- keep references one level deep
- use scripts for generated or dynamic output instead of embedding large blobs in markdown

## Frontmatter Essentials

This repository accepts the portable Agent Skills frontmatter:

| Field | Guidance |
| --- | --- |
| `name` | required; must equal the skill directory and satisfy the portable 1–64 character grammar |
| `description` | required routing signal for Claude Code and Codex; front-load triggers, at most 1024 characters |
| `license` | optional short license name or bundled-file reference |
| `compatibility` | optional environment requirements, at most 500 characters |
| `metadata` | optional string-to-string client metadata |
| `allowed-tools` | optional experimental space-separated string; client support varies |

Claude Code also supports platform-specific invocation controls such as `disable-model-invocation`, `user-invocable`, and `context: fork` with `agent`; see [frontmatter-reference.md](frontmatter-reference.md). Those controls are not portable and the shared repository validator rejects them in `SKILL.md`. Keep OpenAI-specific interface, invocation policy, and dependency declarations in optional `agents/openai.yaml`. The exact local contract is [skill-frontmatter-schema.md](skill-frontmatter-schema.md), and [dual-surface authoring](../../bridge/agent-skills-spec.md#dual-surface-authoring) owns what a directory read by both clients may declare.

## Content Types

| Type | Body style | Best for |
| --- | --- | --- |
| Reference skill | conventions, patterns, routing | domain knowledge Claude should apply |
| Task skill | imperative steps | explicit workflows or operations |

If the body reads like reference documentation, keep it as a reference skill. If the body reads like a checklist, it is a task skill.

## Context-Aware SKILL.md Design

- keep `SKILL.md` under 500 lines
- keep the core workflow short enough to read quickly
- route to references instead of embedding long code blocks
- keep one canonical explanation per concept
- prefer tables and short bullets over repeated prose

Use `SKILL.md` for:

- quick start
- the core workflow
- high-signal constraints
- links to the right references

Use references for:

- multiple variants
- long examples
- edge cases
- large API or schema detail

## Description Guidance

Use this pattern:

```text
Use when [specific trigger or task] - [capability 1], [capability 2], [approach]
```

Good descriptions:

- state when to invoke
- name the outcome
- use words a user would naturally say

Bad descriptions:

- vague summaries such as “helps with code”
- lists of technologies without triggers
- long paragraphs that dilute the routing signal

## Anti-Patterns

- turning a skill into always-on project context
- duplicating `CLAUDE.md` or `.claude/rules` content in `SKILL.md`
- putting long examples directly in the skill body
- preloading a `context: fork` skill when a normal skill would do
- using a weak description and compensating with a verbose body

## Extension Metadata Boundaries

Skill discovery here is native description matching only—there is no keyword or file-trigger matcher runtime. Portable `metadata` values are allowed but do not become activation matchers. Put trigger vocabulary—including naming variants, goal-oriented phrasing, and the proper nouns users actually say—directly in `description`, and add an explicit boundary fixture or deterministic guard when a mistake must be prevented.

### Complete example

```yaml
---
name: debugger
description: "Use when encountering build errors, test failures, or when something is stuck, hung, or not loading - reproduces the failure, traces the owning boundary, and verifies the fix."
---
```

## Related References

- [progressive-disclosure.md](../../bridge/progressive-disclosure.md)
- [frontmatter-reference.md](frontmatter-reference.md)
- [agents-patterns.md](../agents/agents-patterns.md)
