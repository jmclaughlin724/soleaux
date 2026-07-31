# Upstream Sources

## Ownership

`.agents/skills/react/` is the sole repository skill owner for the consolidated React workflow. Imported material remains lazy guidance under `rules/` and `references/`; local repository instructions, installed framework documentation, and live runtime evidence take precedence.

## Consolidated Inputs

- The prior repository `react` skill remains the owner of project-specific rendering, typing, state, and performance guidance.
- The former composition skill contributes component API, state, accessibility, styling, and TypeScript references plus focused composition rules. Registry and marketplace operations route to the shadcn owner instead of being duplicated here.
- Vercel React Best Practices contributes the performance rule catalog. Existing project-adapted rules were retained; only missing upstream rules were added.
- Vercel React Composition Patterns contributes the composition rules already represented by the project-adapted rule files. Its compiled and duplicate source surfaces are not retained.
- [Vercel React View Transitions](https://github.com/vercel-labs/react-view-transitions-skill) contributes the implementation, pattern, CSS, and Next.js references. For this repository, `node_modules/next/dist/docs/01-app/02-guides/view-transitions.md` and the installed config/API references are authoritative.
- [Vercel Web Interface Guidelines](https://vercel.com/design/guidelines) remains a living source; reviews fetch the current [command](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md) rather than treating a local snapshot as current.

The imported Vercel skill packages declared the MIT license in their original skill metadata.

## Updating

Compare upstream rule names and content against this owner, preserve intentional project-specific adaptations, add only missing or materially improved guidance, repair direct consumers, and rerun the skill audit and focused optimizer tests. Do not restore duplicate skill entrypoints or compiled `AGENTS.md` mirrors.
