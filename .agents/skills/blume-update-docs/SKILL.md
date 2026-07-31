---
name: blume-update-docs
description: Audit Blume docs for factual drift or explicitly refresh stale pages. Use for docs drift checks, scheduled audits, shipped documentation updates, or an authorized maintenance pull request. Audit requests are read-only; fixes and publication require separate authority.
---

# Update Blume Docs

## Contract

Operate in one mode:

- **Audit:** Default for checks, reviews, and scheduled scans. Inspect and report without changing files or Git state.
- **Fix:** Requires an explicit request to update documentation. Edit and verify the accepted local scope without creating or switching branches, committing, pushing, or opening or updating a pull request.
- **Publish:** Requires an explicit delivery request. Resolve the remote, base branch, repository policy, and allowed delivery actions before changing Git or external state.

Fix authority does not authorize publication.

## Ground rules

- **Only document what shipped.** Never invent features, timelines, pricing, APIs, or compatibility claims. Work behind a feature flag is not ready for docs unless the flag is enabled for the documented audience or the repo explicitly documents unreleased behavior.
- **Facts over polish.** Edit when a command, option, default, route, prop, or workflow is wrong or missing. Skip subjective rewording, marketing polish, restructuring, and formatting-only churn.
- **Smallest correct diff.** Touch the fewest pages that remove the drift. Preserve the site's voice, frontmatter style, component usage, and `meta.ts` navigation patterns.
- **Exact source-of-truth wording.** Copy commands, flags, config keys, environment variables, routes, and version numbers from their owners instead of paraphrasing from memory.
- **Respect the repo.** Follow `AGENTS.md`/`CLAUDE.md` conventions, don't touch generated output (`.blume/`, `dist/`), and never overwrite unrelated local changes.

## Workflow

1. **Select the authorized contract mode.**
2. **Establish context.**
   - Read the repo's agent/contributor instructions (`AGENTS.md`, `CLAUDE.md`, contribution docs) and honor them.
   - Locate the docs app and content root: `blume.config.ts` (`content.root`), the directory of `.md`/`.mdx` pages, `meta.ts` files, and the package manager + docs build command.
   - If this run was configured with a trigger, lookback window, docs path, target branch, or PR policy, honor those. Use the defaults below only where the prompt is silent.
3. **Find drift.** Read `references/audit-checklist.md` for the full source list and change criteria, then:
   - Review PRs merged into the default branch within the lookback window (default: the last 7 days) and extract the user-facing changes.
   - Compare those changes, plus changelogs, release notes, config schemas, exported APIs, CLI help, and examples, against the docs content.
   - Check external links only when a checked page depends on them; prefer official docs and release notes over secondary sources.
   - Keep notes: what you checked, what changed upstream, and why each edit is (or isn't) needed.
4. **Stop after the report in audit mode.**
   - Report each factual drift finding with its source and affected page, or report the checked sources and verified no-op.
   - Stop without changing files or Git state.
5. **Update the docs in fix or publication mode.**
   - In publication mode, create or switch to a branch before editing only when the authorized delivery workflow and live repository policy require it.
   - Fix the stale pages. Add, rename, or remove `meta.ts` entries when pages are added, renamed, or deleted.
   - Match the surrounding pages: frontmatter shape, Blume components already in use, code-fence style, root-relative internal links.
6. **Verify fix or publication mode.**
   - Run the docs build (`blume build` or the repo's documented docs QA) — it validates frontmatter and duplicate routes.
   - Run `blume validate` to check internal links and anchors.
   - Run lint/format/typecheck when the repo's conventions call for them on docs changes.
   - Fix failures your edits caused; report pre-existing failures separately instead of fixing them in this PR.
7. **Deliver only in publication mode.**
   - If no edit is needed, report the no-op and do not create a branch, commit, push, or pull request.
   - Follow the repository's branch and pull-request policy. Reuse an existing branch or pull request only when the user selected that target or the live repository policy explicitly permits reuse.
   - Commit only the accepted maintenance edits, push only the authorized branch, and open or update only the authorized pull request. Include sources checked, docs changed, verification commands and results, skipped checks, and residual risk.

## Resources

- `references/audit-checklist.md` — the source checklist, edit/skip criteria, and Blume-specific editing guidance. Read it before making docs changes.
