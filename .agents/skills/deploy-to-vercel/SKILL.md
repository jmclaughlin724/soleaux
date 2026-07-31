---
name: deploy-to-vercel
description: Deploy a project to Vercel through an authorized preview or production flow.
---

# Deploy to Vercel

## Contract

Deploy only the project and environment the user placed in scope. Prefer an official authenticated Vercel CLI flow, default to a preview deployment, and treat installation, login, linking, project creation, Git publication, and production promotion as distinct external actions. Return the final deployment URL and verified status or the precise blocker.

## Use When

- The user asks to deploy, publish, preview, or promote a project on Vercel.
- An existing Vercel deployment needs a new preview or production release.
- A project must be linked to the intended Vercel account before deployment.

## Direct Workflow

1. Identify the exact app directory, framework, current branch, Git remote, `.vercel/project.json` or `.vercel/repo.json`, and intended Vercel account or team.
2. Inspect the installed CLI with `vercel --version` and focused subcommand `--help` output before relying on flags. Use `vercel whoami` only as a read-only authentication check.
3. Follow [deployment-playbook.md](references/deployment-playbook.md) for the matching linked, unlinked, authenticated, or Git-integrated path.
4. Confirm any materially separate action not already explicit in the request, including global installation, interactive login, creating or linking a project, pushing Git commits, or a production deployment.
5. Deploy a preview unless production was explicitly requested. Keep account scope consistent on every command and never infer a team from an unrelated current CLI context.
6. Inspect the returned deployment with the supported CLI status command, then report the URL, environment, project/account scope, and any remaining build or runtime blocker.

## Detail Index

- State inspection and account selection: [deployment-playbook.md](references/deployment-playbook.md#state-and-scope)
- Preview and production CLI paths: [deployment-playbook.md](references/deployment-playbook.md#deployment-paths)
- Git-integrated deployments: [deployment-playbook.md](references/deployment-playbook.md#git-integrated-projects)
- Linking, authentication, and failure handling: [deployment-playbook.md](references/deployment-playbook.md#setup-and-failures)

## Boundaries

- Never stage the whole worktree. Stage only the files explicitly selected by the user.
- Never commit or push merely because a project has Git integration.
- Never add `--prod`, promote a deployment, or push the production branch without explicit production authority.
- Never upload project source to an unauthenticated proxy or third-party deployment endpoint.
- Never expose tokens in commands, logs, patches, or chat output.
- Do not install global tooling, create projects, or change account linkage without authorization.
