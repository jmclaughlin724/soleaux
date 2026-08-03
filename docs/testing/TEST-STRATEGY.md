# Test Strategy

## Test layers

| Layer | Purpose | Evidence |
|---|---|---|
| Contract | Prevent schema/tool/version drift | schema tests and digest checks |
| Unit | Validate parser, registry, policy, editor, storage behavior | `cargo test` |
| Native integration | Verify crates together | workspace check/test |
| MCP smoke | Initialize, tools/list, calls, clean exit | JSON smoke artifacts |
| Transport | stdio and Streamable HTTP security/session behavior | transport artifacts |
| Capability | Gateway/catalog/adopt/attach/governance | Phase capability smoke |
| Artifact | Verify ZIP/TAR/checksums/source/binaries | independent receipt |
| Live product proof | Same-model/same-task correctness and context economy | Phase 3 package |
| Compatibility | Real clients, LSPs, frameworks, OSes | matrices |
| Assurance | Security, privacy, accessibility, install/rollback | external and E2E reports |

## Native gate

Every native implementation phase runs:

```bash
cargo fmt --all --check
cargo check --workspace --all-targets --all-features
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo build --release --workspace --all-features
cargo audit --deny warnings
./target/release/soleaux --help
./target/release/soleaux --version
./target/release/soleauxd --help
./target/release/soleauxd --version
```

## Required MCP smoke

Canonical:

```text
initialize
→ tools/list == exact canonical 12
→ context.compile
→ schema validation
→ clean shutdown
```

Substituted:

```text
initialize with one explicit optional substitution
→ tools/list == 12
→ replaced slot absent
→ optional tool in the same slot
→ call optional tool
→ clean shutdown
```

## Fail-closed testing

Tests must intentionally prove failure for:

- 13th tool;
- unknown/duplicate substitution;
- contract-digest drift;
- path escape or symlink escape;
- non-native selected provider;
- expired or changed preview;
- secret leakage;
- false complete coverage;
- unknown context fields;
- malformed host resources;
- gateway credential in worktree.

## Evidence rules

- Logs are retained even on failure.
- Receipt source commit must match the artifact source.
- Independent verification may not reuse the producing workflow environment.
- All checksums and archive paths are validated.
- A failed later run does not invalidate an earlier exact receipt, but it cannot close a new phase.
