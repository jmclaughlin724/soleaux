# Repository Skill Validation Contract

`scripts/codex/audit-skills.mjs` is the repository validator owner. It follows Codex current-working-directory ancestor discovery by default and discovers every repository `.agents/skills` root only under `--all-workspaces`. Select one skill by exact path, never by an ambiguous name:

```bash
node scripts/codex/audit-skills.mjs --all-workspaces --skill-path .agents/skills/my-skill
```

## Portable `SKILL.md` Frontmatter

The validator implements the portable fields defined in [the Agent Skills specification](../../bridge/agent-skills-spec.md):

```yaml
---
name: my-skill
description: "Performs the named workflow. Use when the request matches its exact trigger."
license: MIT
compatibility: Requires the repository package manager.
allowed-tools: Read Bash(git:*)
metadata:
  owner: platform
---
```

- `name` and `description` are required.
- `name` must match the directory and satisfy the portable 1–64 character grammar.
- `description` is nonempty and at most 1024 characters.
- `compatibility`, when present, is nonempty and at most 500 characters.
- `license`, `compatibility`, and `allowed-tools` are strings; `allowed-tools` is not a YAML list.
- `metadata` is a string-to-string mapping.
- Unknown top-level fields are rejected. Platform-only controls belong in their platform owner rather than the shared portable declaration.

## Repository Body And Content Contract

- YAML frontmatter starts on line 1 and parses as a unique-key mapping.
- The first body node is H1 and the next heading is `## Contract`. This is a repository extension, not a portable requirement.
- Entrypoints over 8000 bytes warn; move detailed variants into focused references.
- Portable text does not embed user-specific absolute paths.
- `.rules` remains reserved for `.codex/rules/**`; skill-local rule guidance uses Markdown.
- Relative Markdown links and heading fragments resolve, and `.agents/rules/**` is not a valid owner.
- An inline-code root `pnpm <script>` reference names an existing `package.json` script.

## Optional `agents/openai.yaml`

OpenAI metadata is optional. When present, the validator accepts `interface`, `policy`, and `dependencies`:

```yaml
interface:
  display_name: "My Skill"
  short_description: "Use this skill for its named workflow"
  default_prompt: "Use $my-skill for this task."
policy:
  allow_implicit_invocation: true
dependencies:
  tools:
    - type: mcp
      value: example
      transport: streamable_http
      url: https://example.invalid/mcp
```

Interface strings are nonempty, `short_description` is 25–64 characters, and `default_prompt` invokes the exact `$skill-name`. Invocation policy is boolean when supplied. MCP dependencies require `type: mcp` and a nonempty `value`; `stdio` additionally requires `command`, and `streamable_http` requires `url`.

A dependency may be self-describing, repository-configured, or host-managed. Absence from `.codex/config.toml` is not a declaration error. Run `pnpm skills:readiness` to classify environment resolution; that command does not probe live connectivity.

## Closeout

- `pnpm skills:validate` proves declarations and static repository contracts.
- `pnpm skills:relationships` proves ownership, registrations, connections, package commands, and the CI umbrella.
- `pnpm skills:boundaries` proves complete deterministic activation fixtures without executing a model.
- `pnpm skills:audit` runs those three required checks.
- `pnpm skills:conformance` optionally compares every skill with the pinned upstream demonstration validator.

The `PostToolUse` owner `.codex/hooks/PostToolUse/skill-audit.mjs` derives exact edited skill paths and runs the focused validator. Proven findings use the native post-tool block; selection, loading, or execution failures exit `2` with corrective stderr.
