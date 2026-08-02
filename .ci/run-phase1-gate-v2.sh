#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
EVIDENCE="$ROOT/phase1-evidence"
BASE="$ROOT/phase1-base"
OVERLAY="$ROOT/phase1-overlay"
SOURCE="$ROOT/phase1-source"
CONTRACTS="$ROOT/phase0-materialized"

rm -rf "$EVIDENCE" "$BASE" "$OVERLAY" "$SOURCE" "$CONTRACTS"
mkdir -p "$EVIDENCE" "$BASE" "$OVERLAY" "$SOURCE" "$CONTRACTS"

# Immutable Phase 0 contracts.
cat .ci/phase0-contracts.part-* | tr -d '\r\n' > /tmp/soleaux-phase0-contracts.tar.xz.b64
test "$(wc -c < /tmp/soleaux-phase0-contracts.tar.xz.b64)" -eq 26360
echo "ee4cfde3ce76b01737fa08498950af8288c9f3a585b72f452d2917d739e1e73a  /tmp/soleaux-phase0-contracts.tar.xz.b64" | sha256sum -c -
base64 --decode /tmp/soleaux-phase0-contracts.tar.xz.b64 > /tmp/soleaux-phase0-contracts.tar.xz
echo "6826368b15370b50f08e697e06ba9afb6b955455f2bb84cbd81088dc3bb2f564  /tmp/soleaux-phase0-contracts.tar.xz" | sha256sum -c -
tar -xJf /tmp/soleaux-phase0-contracts.tar.xz -C "$CONTRACTS"
cmp contracts/phase0-identity.json "$CONTRACTS/contracts/phase0-identity.json"
python3 scripts/validate_phase0_contracts.py 2>&1 | tee "$EVIDENCE/phase0-contract-test.json"

# Exact Phase 0 native source.
cat .ci/native-wedge-source.part-* | tr -d '\r\n' > /tmp/soleaux-native-base.tar.xz.b64
test "$(wc -c < /tmp/soleaux-native-base.tar.xz.b64)" -eq 49336
echo "c35307fd88284d43e991e4d98ae9ae569411e41afa17f29dc13a882204b1dbca  /tmp/soleaux-native-base.tar.xz.b64" | sha256sum -c -
base64 --decode /tmp/soleaux-native-base.tar.xz.b64 > /tmp/soleaux-native-base.tar.xz
echo "520b32d1223136b8a0dcaedd7b1438d3bb897c171b95884973e200ddf294dbfd  /tmp/soleaux-native-base.tar.xz" | sha256sum -c -
tar -xJf /tmp/soleaux-native-base.tar.xz -C "$BASE"
if [ -d .ci/native-overrides/files ]; then
  cp -a .ci/native-overrides/files/. "$BASE/"
fi
for patch_file in .ci/native-overrides/patches/*.patch; do
  patch -d "$BASE" -p1 < "$patch_file"
done
cp -a .ci/phase0-native-overrides/files/. "$BASE/"
python3 scripts/apply_phase0_native_overlay.py "$BASE" 2>&1 | tee "$EVIDENCE/phase0-native-overlay.json"
cp UNIFIED-MCP-PROFILE.md CONTEXT-PACKET-V2.md "$BASE/"
mkdir -p "$BASE/contracts"
cp contracts/phase0-identity.json "$BASE/contracts/"
cp "$CONTRACTS/contracts/unified-mcp-profile-v2.json" "$BASE/contracts/"
cp "$CONTRACTS/contracts/context-packet-v2.schema.json" "$BASE/contracts/"
cp -a "$BASE/." "$SOURCE/"

# Hash-bound repaired Phase 1 overlay.
cat .ci/phase1-overlay.part-* | tr -d '\r\n' > /tmp/soleaux-phase1-overlay.tar.xz.b64
{
  printf 'encoded_size='
  wc -c < /tmp/soleaux-phase1-overlay.tar.xz.b64
  printf 'encoded_sha256='
  sha256sum /tmp/soleaux-phase1-overlay.tar.xz.b64 | awk '{print $1}'
} | tee "$EVIDENCE/phase1-overlay-encoded-integrity.txt"
test "$(wc -c < /tmp/soleaux-phase1-overlay.tar.xz.b64)" -eq 73728
echo "014e7cba3b044950df95e06723ab92e9d3a0c08b9188211ba7a1538947827fff  /tmp/soleaux-phase1-overlay.tar.xz.b64" | sha256sum -c -
base64 --decode /tmp/soleaux-phase1-overlay.tar.xz.b64 > /tmp/soleaux-phase1-overlay.tar.xz
{
  printf 'decoded_size='
  wc -c < /tmp/soleaux-phase1-overlay.tar.xz
  printf 'decoded_sha256='
  sha256sum /tmp/soleaux-phase1-overlay.tar.xz | awk '{print $1}'
} | tee "$EVIDENCE/phase1-overlay-decoded-integrity.txt"
test "$(wc -c < /tmp/soleaux-phase1-overlay.tar.xz)" -eq 55296
echo "1ead562a7e8da9c26d097bbe3b30dd520df596cf22db8f0f9c30640dcb9c6c73  /tmp/soleaux-phase1-overlay.tar.xz" | sha256sum -c -
tar -xJf /tmp/soleaux-phase1-overlay.tar.xz -C "$OVERLAY"
cp -a "$OVERLAY/files/." "$SOURCE/"
if [ -s "$OVERLAY/delete-paths.txt" ]; then
  while IFS= read -r relative_path; do
    test -n "$relative_path" || continue
    rm -rf -- "$SOURCE/$relative_path"
  done < "$OVERLAY/delete-paths.txt"
fi
git rev-parse HEAD | tee "$EVIDENCE/git-head.txt"
find "$SOURCE" -type f | sort > "$EVIDENCE/materialized-files.txt"
cp "$OVERLAY/changed-files.txt" "$EVIDENCE/phase1-changed-files.txt"

cd "$SOURCE"
grep -q 'version = "0.4.0-dev.5"' Cargo.toml
echo "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc  contracts/unified-mcp-profile-v2.json" | sha256sum -c -
echo "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f  contracts/context-packet-v2.schema.json" | sha256sum -c -
python3 - <<'PY' | tee "$EVIDENCE/contract-source-check.json"
import json
from pathlib import Path
expected = [
    'context.compile', 'code.search', 'memory.search', 'get_symbols',
    'registry.list', 'registry.read', 'repo_info', 'navigate',
    'inspect', 'preview', 'edit', 'restart_lsp',
]
profile = json.loads(Path('contracts/unified-mcp-profile-v2.json').read_text())
assert profile['productVersion'] == '0.4.0-dev.5'
assert profile['productionClaimAllowed'] is False
assert profile['hardCeiling'] == 12
assert profile['defaultProfile'] == expected
assert profile['optionalTools'] == [
    'parse_and_validate_postgres_sql',
    'turborepo.packages',
    'next.get_routes',
]
source = Path('daemon/mcp/src/lib.rs').read_text()
assert 'pub const PUBLIC_ROOT_TOOL_COUNT: usize = profile::HARD_CEILING;' in source
assert 'enable_optional_tool' not in source
for name in expected:
    assert name in source or name in Path('daemon/mcp/src/profile.rs').read_text()
print(json.dumps({
    'status': 'pass',
    'canonical_tools': expected,
    'hard_ceiling': 12,
    'production_claim_allowed': False,
}, sort_keys=True))
PY

rustc --version --verbose | tee "$EVIDENCE/rustc-version.txt"
cargo --version --verbose | tee "$EVIDENCE/cargo-version.txt"
rustfmt --version | tee "$EVIDENCE/rustfmt-version.txt"
cargo clippy --version | tee "$EVIDENCE/clippy-version.txt"
python3 -c 'import jsonschema; print(jsonschema.__version__)' | tee "$EVIDENCE/jsonschema-version.txt"

cargo generate-lockfile 2>&1 | tee "$EVIDENCE/cargo-generate-lockfile.txt"
cp Cargo.lock "$EVIDENCE/Cargo.lock"
cargo fmt --all 2>&1 | tee "$EVIDENCE/cargo-fmt-apply.txt"
cargo fmt --all --check 2>&1 | tee "$EVIDENCE/cargo-fmt-check.txt"
cargo check --workspace --all-targets --all-features 2>&1 | tee "$EVIDENCE/cargo-check.txt"
cargo clippy --workspace --all-targets --all-features -- -D warnings 2>&1 | tee "$EVIDENCE/cargo-clippy.txt"
cargo test --workspace --all-features 2>&1 | tee "$EVIDENCE/cargo-test.txt"
cargo build --release --workspace --all-features 2>&1 | tee "$EVIDENCE/cargo-build-release.txt"
cp target/release/soleaux target/release/soleauxd "$EVIDENCE/"
./target/release/soleaux --help 2>&1 | tee "$EVIDENCE/soleaux-cli-help.txt"
./target/release/soleauxd --help 2>&1 | tee "$EVIDENCE/soleauxd-help.txt"
./target/release/soleaux --version 2>&1 | tee "$EVIDENCE/soleaux-version.txt"
./target/release/soleauxd --version 2>&1 | tee "$EVIDENCE/soleauxd-version.txt"
python3 scripts/phase1_mcp_smoke.py target/release/soleaux . "$EVIDENCE/phase1-mcp-smoke.json" 2>&1 | tee "$EVIDENCE/phase1-mcp-smoke.txt"
cargo audit --deny warnings 2>&1 | tee "$EVIDENCE/cargo-audit.txt"
