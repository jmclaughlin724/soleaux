# Skill Catalog and Activation

## Contract

Use this reference when implementing or reviewing a client-side Agent Skills lifecycle: discovery, startup catalog exposure, activation, resource listing, or context retention. The portable bundle format remains owned by [agent-skills-spec.md](agent-skills-spec.md); this file owns how a client exposes and activates those bundles.

Sources:

- https://agentskills.io/client-implementation/adding-skills-support
- https://agentskills.io/skill-creation/optimizing-descriptions
- https://agentskills.io/specification

## Progressive Disclosure

Keep the three tiers separate:

| Tier         | Content                         | Load time                  |
| ------------ | ------------------------------- | -------------------------- |
| Catalog      | name, description, and location | session start              |
| Instructions | the selected `SKILL.md` body    | activation                 |
| Resources    | paths to bundled support files  | on demand after activation |

Do not commit a generated catalog. Discover the live skill roots at session startup and render the catalog into the client request. Keep descriptions concise and intent-centered because they carry the discovery decision for every session; resources do not belong in the startup catalog.

## Discovery and Parsing

1. Scan the trusted scopes the client supports. For local agents, check project and user scopes, including `.agents/skills/`; client-native roots may be additional inputs.
2. Within a root, inspect immediate child directories containing a file named exactly `SKILL.md`. Bound broader directory searches and skip `.git` and `node_modules`.
3. Parse frontmatter with a YAML parser. Require a non-empty `name` and `description`; skip an unparseable skill and surface a diagnostic instead of silently inventing metadata.
4. Resolve collisions deterministically. Project scope overrides user scope; within one scope, use a stable directory order and warn about the shadowed skill.
5. Store the absolute `SKILL.md` location. Derive the skill directory from it when resolving relative paths or activating the skill.

Only expose skills that are enabled, permitted, and from a trusted source. Hide filtered skills from the catalog rather than advertising an activation that will fail.

## Catalog Placement

Use one catalog owner per client:

- Put `<available_skills>` in the system prompt when activation uses the client's ordinary file-read capability.
- Put the catalog in a dedicated activation tool's description when that tool is the only supported activation route.
- Omit the catalog and its behavioral instructions when discovery returns no valid skills.

Each catalog entry contains only `name`, `description`, and `location`. Escape metadata for the selected serialization and keep output order stable. Tell the model to load the listed `SKILL.md` before acting and to resolve relative paths from its parent directory.

## Activation and Structured Wrapping

File-read activation may return the whole file. A dedicated activation tool should normally return the Markdown body without frontmatter, wrapped so the client can recognize and preserve it:

```xml
<skill_content name="example">
# Example

[SKILL.md body]

Skill directory: /absolute/path/to/example
Relative paths in this skill are relative to the skill directory.
<skill_resources>
  <file>references/guide.md</file>
  <file>scripts/check.mjs</file>
</skill_resources>
</skill_content>
```

List resource paths without reading their contents. Keep paths relative to the skill directory, sort them, cap large inventories, and say when entries were omitted. The model can then load only the resource needed for the current step.

Allow trusted skill directories through any file-read permission gate. Track activated skill names so repeated activation does not duplicate instructions, and protect `<skill_content>` tool results from pruning or summarization during context compaction.

## Boundaries

- The client owns trust, permissions, root eligibility, and scope precedence; do not run project discovery before the workspace is trusted.
- The catalog is not an end-user marketplace. Expose only reviewed skills mapped to the client's allowed workflows.
- Codex owns its native skill discovery, catalog budgeting, and refresh. Repository hooks must not inject a replacement catalog.
- Do not move platform-specific invocation controls here; keep those deltas in the Claude or Codex lane.
