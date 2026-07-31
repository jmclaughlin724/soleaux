# Vercel Deployment Playbook

## State and scope

Start with read-only evidence from the exact app directory:

```bash
git remote get-url origin
vercel --version
vercel whoami
vercel link --help
vercel deploy --help
vercel inspect --help
```

Read `.vercel/project.json` or `.vercel/repo.json` directly when present. A linked configuration owns the project and account identity; do not override its `orgId` from the current CLI context. If the app is unlinked and more than one team or personal scope is available, ask which account owns the deployment before creating or linking anything.

Use only flags supported by the installed CLI help. Do not assume `--format json`, `--repo`, or any other version-sensitive option exists merely because an upstream example used it.

## Deployment paths

### Linked project preview

The user's explicit deployment request authorizes the preview itself when the target project and account are already resolved:

```bash
vercel deploy <app-directory> --yes --no-wait
```

Add the resolved account scope only when the installed CLI supports the flag and it matches the linked owner. Capture the returned URL, then inspect it with the installed CLI's supported status command.

When supported, use Vercel's dry-run deployment output before the real upload to inspect which files will leave the machine. Treat the dry run as evidence; do not silently broaden ignore rules merely to reduce the manifest.

### Production

Production is a separate external action. Run the installed CLI's documented production form only after the user explicitly asks for production and the exact project/account is verified. State that the action changes the live production deployment before running it.

## Git-integrated projects

Git push can trigger Vercel, but a request to deploy does not automatically authorize a commit or push. Use Git only when the user selects that path.

Before committing:

1. Show the exact files intended for the deployment commit.
2. Preserve unrelated staged and unstaged work.
3. Stage only the selected paths; never run `git add .`.
4. Confirm the destination branch and whether it maps to preview or production.
5. Commit and push only with explicit authority.

After the push, obtain the deployment URL from the provider's status checks, dashboard, or an authenticated CLI command supported by the installed version.

## Setup and failures

### CLI missing or unauthenticated

Installing a global CLI and logging in are separate setup actions. Ask before installation. For login, let the user complete the provider-owned browser flow and never request or echo a token. There is no unauthenticated proxy fallback in this repository skill.

### Unlinked project

Linking may select or create remote state. Explain the intended account, project, and local file that will be created, then obtain confirmation. Prefer the ordinary single-project link flow. Reserve repository-wide or multi-project linking options for an intentional multi-project request and only when the installed CLI documents them.

### Build or network failure

Return the deployment URL when one exists, the provider status, and the narrowest relevant error. Retry only when the failure is transient and the retry stays within the authorized target. A sandbox network denial may require a scoped escalation, but it does not authorize broader file or credential access.

### Completion

Report:

- preview or production environment;
- project and account/team scope;
- deployment URL;
- inspected status;
- commit/branch only when Git was used;
- unresolved build, domain, environment-variable, or runtime blockers.
