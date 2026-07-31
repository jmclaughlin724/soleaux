# Remote Cache

Remote caching changes external account state and moves task artifacts outside the local machine. Confirm the provider, team/project, data policy, and user authorization before linking or logging in.

## Preconditions

- Local cache behavior is already correct for the selected task.
- The remote provider and account owner are known.
- Secret storage for the CI token and team identifier is established.
- Cached outputs contain no secrets, personal data, or non-portable machine state.

When the user authorizes account changes, use the installed CLI rather than an ephemeral package:

```bash
pnpm exec turbo login
pnpm exec turbo link
```

For CI, inject the provider's token and team values from the CI secret store. Do not commit credentials or print them during diagnostics.

## Verification

Run one bounded task twice from separate clean local cache contexts, inspect the cache source and task summary, and confirm the restored outputs match a local miss. Treat authentication, upload, download, and cache-correctness failures as separate diagnoses.
