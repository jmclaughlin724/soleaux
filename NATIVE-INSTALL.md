# Native Soleaux install (0.4.0-dev.5)

**Status:** pre-production. `productionClaimAllowed` is **false**.

## Install (linux-x86_64)

```bash
curl -fsSL https://raw.githubusercontent.com/jmclaughlin724/soleaux/native/0.4.0-dev.5/scripts/install-native.sh | bash
```

Defaults:

- Install prefix: `~/.local` (`soleaux` and `soleauxd` under `~/.local/bin`)
- Release tag: `native-v0.4.0-dev.5`

Overrides:

```bash
SOLEAUX_PREFIX=/usr/local SOLEAUX_NATIVE_TAG=native-v0.4.0-dev.5 bash scripts/install-native.sh
```

## Publish / refresh the release

From GitHub Actions on branch `native/0.4.0-dev.5`:

1. Run workflow **Soleaux native install release**
2. Input Phase 2 run ID (default `30818963313` — the closed green gate)
3. Tag default `native-v0.4.0-dev.5`

The workflow packages the **already verified** Phase 2 `soleaux` / `soleauxd` binaries into a GitHub **pre-release**. It does not lift the production claim.

## Host wiring

After install, ensure `~/.local/bin` is on `PATH`, then point Codex / Claude / OpenCode MCP config at:

```text
soleaux
```

(stdio). Confirm with:

```bash
soleaux --version   # 0.4.0-dev.5
soleauxd --version  # 0.4.0-dev.5
```

## Platforms

This release path currently publishes **linux-x86_64** from the Phase 2 ubuntu-24.04 artifact. Other targets need matrix builds added to the release workflow.
