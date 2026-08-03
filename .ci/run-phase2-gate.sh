#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
EVIDENCE="$ROOT/phase2-evidence"
PHASE1_SOURCE="$ROOT/phase1-source"
SOURCE="$ROOT/phase2-source"
OVERLAY="$ROOT/phase2-overlay"
ARCHIVE_B64="/tmp/soleaux-phase2-overlay.tar.xz.b64"
ARCHIVE="/tmp/soleaux-phase2-overlay.tar.xz"

rm -rf "$EVIDENCE" "$PHASE1_SOURCE" "$SOURCE" "$OVERLAY"
mkdir -p "$EVIDENCE" "$OVERLAY"

# Re-prove and materialize the immutable Phase 1 source before applying Phase 2.
bash .ci/run-phase1-gate-v2.sh
cp -a "$PHASE1_SOURCE" "$SOURCE"

# Bind the existing Phase 2 implementation overlay to the exact Git blob SHAs.
declare -A EXPECTED_BLOBS=(
  [phase2-overlay.part-00]="5269a48acbdbb03b5269aca0f97c09518c473317"
  [phase2-overlay.part-01]="42cf5997a3546cd3d60facde2a0f559a54a5d49c"
  [phase2-overlay.part-02]="1dce1886ed34db120e99a337fb770c2365c4e9ee"
  [phase2-overlay.part-03]="de6cfd8a3a939b70b80697ec18b5fbca12aae31d"
  [phase2-overlay.part-04]="bef5ceeb6260ce60aa72bec609e7e59cde7d0e70"
)
: > "$EVIDENCE/phase2-overlay-part-integrity.txt"
for name in phase2-overlay.part-00 phase2-overlay.part-01 phase2-overlay.part-02 phase2-overlay.part-03 phase2-overlay.part-04; do
  path=".ci/$name"
  actual="$(git hash-object "$path")"
  expected="${EXPECTED_BLOBS[$name]}"
  test "$actual" = "$expected"
  compact="$(tr -d '\r\n' < "$path")"
  printf '%s blob=%s compact_size=%s compact_sha256=%s\n' \
    "$name" "$actual" "${#compact}" \
    "$(printf '%s' "$compact" | sha256sum | awk '{print $1}')" \
    | tee -a "$EVIDENCE/phase2-overlay-part-integrity.txt"
done

cat .ci/phase2-overlay.part-* | tr -d '\r\n' > "$ARCHIVE_B64"
{
  printf 'encoded_size='
  wc -c < "$ARCHIVE_B64"
  printf 'encoded_sha256='
  sha256sum "$ARCHIVE_B64" | awk '{print $1}'
} | tee "$EVIDENCE/phase2-overlay-encoded-integrity.txt"
base64 --decode "$ARCHIVE_B64" > "$ARCHIVE"
xz -t "$ARCHIVE"
{
  printf 'decoded_size='
  wc -c < "$ARCHIVE"
  printf 'decoded_sha256='
  sha256sum "$ARCHIVE" | awk '{print $1}'
} | tee "$EVIDENCE/phase2-overlay-decoded-integrity.txt"
tar -tJf "$ARCHIVE" | tee "$EVIDENCE/phase2-overlay-archive-list.txt"
tar -xJf "$ARCHIVE" -C "$OVERLAY"

if [ -d "$OVERLAY/files" ]; then
  cp -a "$OVERLAY/files/." "$SOURCE/"
elif [ -f "$OVERLAY/Cargo.toml" ]; then
  cp -a "$OVERLAY/." "$SOURCE/"
else
  echo "Phase 2 overlay has no recognized source payload" >&2
  exit 1
fi
if [ -s "$OVERLAY/delete-paths.txt" ]; then
  while IFS= read -r relative_path; do
    test -n "$relative_path" || continue
    rm -rf -- "$SOURCE/$relative_path"
  done < "$OVERLAY/delete-paths.txt"
fi
if [ -f scripts/apply_phase2_repairs.py ]; then
  python3 scripts/apply_phase2_repairs.py "$SOURCE" \
    2>&1 | tee "$EVIDENCE/phase2-repairs.txt"
fi

git rev-parse HEAD | tee "$EVIDENCE/git-head.txt"
find "$SOURCE" -type f | sort > "$EVIDENCE/materialized-files.txt"

cd "$SOURCE"
PROFILE_SHA="$(sha256sum contracts/unified-mcp-profile-v2.json | awk '{print $1}')"
CONTEXT_SHA="$(sha256sum contracts/context-packet-v2.schema.json | awk '{print $1}')"
test "$PROFILE_SHA" = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
test "$CONTEXT_SHA" = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"
python3 - <<'PY' | tee "$EVIDENCE/phase2-contract-check.json"
import json
from pathlib import Path
expected = [
    "context.compile", "code.search", "memory.search", "get_symbols",
    "registry.list", "registry.read", "repo_info", "navigate", "inspect",
    "preview", "edit", "restart_lsp",
]
profile = json.loads(Path("contracts/unified-mcp-profile-v2.json").read_text())
identity = json.loads(Path("contracts/phase0-identity.json").read_text())
assert profile["productVersion"] == "0.4.0-dev.5"
assert profile["productionClaimAllowed"] is False
assert profile["hardCeiling"] == 12
assert profile["defaultProfile"] == expected
assert profile["optionalTools"] == [
    "parse_and_validate_postgres_sql", "turborepo.packages", "next.get_routes"
]
assert identity["productionClaimAllowed"] is False
profile_rs = Path("daemon/mcp/src/profile.rs").read_text()
assert 'pub const PRODUCT_VERSION: &str = "0.4.0-dev.5";' in profile_rs
assert 'pub const PRODUCTION_CLAIM_ALLOWED: bool = false;' in profile_rs
assert 'pub const HARD_CEILING: usize = 12;' in profile_rs
mcp = Path("daemon/mcp/src/lib.rs").read_text()
intel = Path("daemon/intelligence/src/lib.rs").read_text()
assert "pub mod gateway;" in mcp
assert "pub mod provisioning;" in mcp
assert "pub mod governance;" in intel
print(json.dumps({
    "status": "pass",
    "canonical_tools": expected,
    "hard_ceiling": 12,
    "profile_digest": "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc",
    "context_digest": "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f",
    "version": "0.4.0-dev.5",
    "production_claim_allowed": False,
}, sort_keys=True))
PY

# Python remains conformance-only in the materialized native source.
python3 - <<'PY' | tee "$EVIDENCE/python-surface-check.json"
import json
from pathlib import Path
files = sorted(str(path).replace('\\', '/') for path in Path('.').rglob('*.py'))
allowed = {"scripts/phase1_mcp_smoke.py", "scripts/phase2_capability_smoke.py"}
assert set(files) == allowed, files
print(json.dumps({"status":"pass","python_files":files,"production_python_surface":False}, sort_keys=True))
PY

rustc --version --verbose | tee "$EVIDENCE/rustc-version.txt"
cargo --version --verbose | tee "$EVIDENCE/cargo-version.txt"
rustfmt --version | tee "$EVIDENCE/rustfmt-version.txt"
cargo clippy --version | tee "$EVIDENCE/clippy-version.txt"
python3 -c 'import importlib.metadata; print(importlib.metadata.version("jsonschema"))' | tee "$EVIDENCE/jsonschema-version.txt"

cargo generate-lockfile 2>&1 | tee "$EVIDENCE/cargo-generate-lockfile.txt"
cp Cargo.lock "$EVIDENCE/Cargo.lock"
if ! cargo fmt --all --check 2>&1 | tee "$EVIDENCE/cargo-fmt-check.txt"; then
  cargo fmt --all
  tar --exclude='./target' -cJf "$EVIDENCE/phase2-formatted-source.tar.xz" .
  echo "cargo fmt --check failed; formatted diagnostic source captured" >&2
  exit 1
fi
cargo check --workspace --all-targets --all-features 2>&1 | tee "$EVIDENCE/cargo-check.txt"
cargo clippy --workspace --all-targets --all-features -- -D warnings 2>&1 | tee "$EVIDENCE/cargo-clippy.txt"
cargo test --workspace --all-features 2>&1 | tee "$EVIDENCE/cargo-test.txt"
cargo build --release --workspace --all-features 2>&1 | tee "$EVIDENCE/cargo-build-release.txt"
cp target/release/soleaux target/release/soleauxd "$EVIDENCE/"
./target/release/soleaux --help 2>&1 | tee "$EVIDENCE/soleaux-help.txt"
./target/release/soleaux --version 2>&1 | tee "$EVIDENCE/soleaux-version.txt"
./target/release/soleauxd --help 2>&1 | tee "$EVIDENCE/soleauxd-help.txt"
./target/release/soleauxd --version 2>&1 | tee "$EVIDENCE/soleauxd-version.txt"
python3 scripts/phase1_mcp_smoke.py target/release/soleaux . "$EVIDENCE/phase1-regression-smoke.json" 2>&1 | tee "$EVIDENCE/phase1-regression-smoke.txt"
python3 scripts/phase2_capability_smoke.py \
  target/release/soleaux target/release/soleauxd . \
  "$EVIDENCE/phase2-capability-smoke.json" \
  2>&1 | tee "$EVIDENCE/phase2-capability-smoke.txt"
cargo audit --deny warnings 2>&1 | tee "$EVIDENCE/cargo-audit.txt"

tar --exclude='./target' -cJf "$EVIDENCE/soleaux-phase2-source.tar.xz" .
cd "$ROOT"
find "$EVIDENCE" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$EVIDENCE/SHA256SUMS"
