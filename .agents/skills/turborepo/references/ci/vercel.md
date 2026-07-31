# Vercel Monorepo Builds

The live Vercel project configuration and current deployment documentation own root-directory, build, install, and unaffected-deployment behavior. Inspect each configured project independently; one monorepo does not imply one shared deployment contract.

## Project Mapping

For every affected Vercel project, confirm:

- its workspace and root directory;
- install and build commands;
- framework detection and output owner;
- environment variables and deployment protection;
- internal packages included in its Turbo graph; and
- the current mechanism for skipping unaffected deployments.

Use repository scripts or the installed `turbo run` contract. Do not add or run `turbo-ignore` unless the repository explicitly owns and versions it.

## Verification

Inspect a dry-run graph for the project workspace, then verify one change that should deploy and one change that should not. Confirm internal dependency changes reach every consuming project and that production does not rely on a working-directory or environment override absent from Vercel configuration.
