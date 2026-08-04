# Native Soleaux install (0.4.0-dev.5)

**Status:** pre-production. `productionClaimAllowed` is **false**.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/jmclaughlin724/soleaux/main/scripts/install-native.sh | bash
```

Supported targets:

- `linux-x86_64`
- `darwin-arm64` (Apple Silicon)

Defaults: prefix `~/.local`, tag `native-v0.4.0-dev.5`.

## Host wiring

Ensure `~/.local/bin` is on `PATH`, then point Codex / Claude / OpenCode at `soleaux`.

```bash
soleaux --version   # 0.4.0-dev.5
```
