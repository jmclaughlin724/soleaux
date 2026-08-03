#!/usr/bin/env bash
set -euo pipefail

ROOT=$(pwd)
EVIDENCE="$ROOT/phase2-evidence"
P1="$ROOT/phase1-source"
SRC="$ROOT/phase2-source"
OVERLAY="$ROOT/phase2-overlay"
B64=/tmp/soleaux-phase2-v3.tar.xz.b64
ARCHIVE=/tmp/soleaux-phase2-v3.tar.xz
rm -rf "$EVIDENCE" "$P1" "$SRC" "$OVERLAY"
rm -f "$B64" "$ARCHIVE"
mkdir -p "$EVIDENCE" "$OVERLAY"

bash .ci/run-phase1-gate-v2.sh
cp -a "$P1" "$SRC"

cat > /tmp/phase2-parts.manifest <<'MANIFEST'
phase2-overlay-v3.part-00 4000 a80a99b0cc58f76631c8137e6ef963bf2052855f
phase2-overlay-v3.part-01 4000 8317079323cda8c76aae5106b03a371b630a1fdc
phase2-overlay-v3.part-02 4000 d454e1398ec321033867edfe04e8fef092b460ac
phase2-overlay-v3.part-03 4000 88ee38173bf930694b4b8c663bf47124a67b2f2d
phase2-overlay-v3.part-04 4000 e6074da184347676affe2331bd3b866b1bcf99c3
phase2-overlay-v3.part-05 4000 d60dd0099e970ceda8b549c0614b3700f2c3a371
phase2-overlay-v3.part-06 4000 d6a2100bece5e287cc5a2a493dfec98435e1a157
phase2-overlay-v3.part-07 4000 4213cead3fb7557e32fba374e12e4b0d819ba689
phase2-overlay-v3.part-08 4000 f665c80fd6c4413d5daae7eba2f7ea2c51e9cf59
phase2-overlay-v3.part-09 4000 01d389dd38e123fd1e40edf4454872086a85b5b7
phase2-overlay-v3.part-10 4000 63a5f00d3a750acc0712f65307e4fdfa98764a49
phase2-overlay-v3.part-11 4000 5fd94f7b9e540e55156dc5671cd1b77aaf7470b0
phase2-overlay-v3.part-12 4000 7b081f5a7815989ef02d1c26342c551e59428032
phase2-overlay-v3.part-13 4000 f9506a4c26293af7dcfd970e29141a00b834a35c
phase2-overlay-v3.part-14 3808 ad3ad9adf27e91960877ba29ca6caf27f2a0cdfa
MANIFEST

: > "$EVIDENCE/phase2-overlay-part-integrity.txt"
: > "$B64"
while read -r name size blob; do
  path=".ci/$name"
  test -f "$path"
  test "$(wc -c < "$path" | tr -d ' ')" = "$size"
  test "$(git hash-object "$path")" = "$blob"
  if LC_ALL=C grep -q $'[\r\n]' "$path"; then exit 1; fi
  printf '%s size=%s blob=%s sha256=%s\n' "$name" "$size" "$blob" "$(sha256sum "$path" | awk '{print $1}')" \
    | tee -a "$EVIDENCE/phase2-overlay-part-integrity.txt"
  cat "$path" >> "$B64"
done < /tmp/phase2-parts.manifest

test "$(wc -c < "$B64" | tr -d ' ')" = 59808
echo 'ff96b94be39e8094f11963655ff55984a2ed34b48f344b860891d37362a8066a  '"$B64" | sha256sum -c -
base64 --decode "$B64" > "$ARCHIVE"
test "$(wc -c < "$ARCHIVE" | tr -d ' ')" = 44856
echo '0b9f9e694845711fcbc739dbdb8317e0ba0e83c9a66124bc8bccc36ebbf295e3  '"$ARCHIVE" | sha256sum -c -
xz -t "$ARCHIVE"
tar -tJf "$ARCHIVE" | tee "$EVIDENCE/phase2-overlay-archive-list.txt"
tar -xJf "$ARCHIVE" -C "$OVERLAY"
cp -a "$OVERLAY/files/." "$SRC/"
if [ -s "$OVERLAY/delete-paths.txt" ]; then
  while IFS= read -r p; do [ -z "$p" ] || rm -rf -- "$SRC/$p"; done < "$OVERLAY/delete-paths.txt"
fi
[ ! -f scripts/apply_phase2_repairs.py ] || python3 scripts/apply_phase2_repairs.py "$SRC" | tee "$EVIDENCE/phase2-repairs.txt"

git rev-parse HEAD | tee "$EVIDENCE/git-head.txt"
cd "$SRC"
echo '89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc  contracts/unified-mcp-profile-v2.json' | sha256sum -c -
echo '3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f  contracts/context-packet-v2.schema.json' | sha256sum -c -
grep -F 'pub const PRODUCT_VERSION: &str = "0.4.0-dev.5";' daemon/mcp/src/profile.rs
grep -F 'pub const PRODUCTION_CLAIM_ALLOWED: bool = false;' daemon/mcp/src/profile.rs
grep -F 'pub const HARD_CEILING: usize = 12;' daemon/mcp/src/profile.rs

test "$(find . -name '*.py' -type f | sort | tr '\n' ' ')" = './scripts/phase1_mcp_smoke.py ./scripts/phase2_capability_smoke.py '

rustc --version --verbose | tee "$EVIDENCE/rustc-version.txt"
cargo --version --verbose | tee "$EVIDENCE/cargo-version.txt"
cargo generate-lockfile 2>&1 | tee "$EVIDENCE/cargo-generate-lockfile.txt"
cp Cargo.lock "$EVIDENCE/Cargo.lock"
if ! cargo fmt --all --check 2>&1 | tee "$EVIDENCE/cargo-fmt-check.txt"; then
  cargo fmt --all
  tar --exclude='./target' -cJf "$EVIDENCE/phase2-formatted-source.tar.xz" .
  sha256sum "$EVIDENCE/phase2-formatted-source.tar.xz" > "$EVIDENCE/phase2-formatted-source.sha256"
  echo "cargo fmt --check failed; exact formatted diagnostic source captured" >&2
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
python3 scripts/phase2_capability_smoke.py target/release/soleaux target/release/soleauxd . "$EVIDENCE/phase2-capability-smoke.json" 2>&1 | tee "$EVIDENCE/phase2-capability-smoke.txt"
cargo audit --deny warnings 2>&1 | tee "$EVIDENCE/cargo-audit.txt"
tar --exclude='./target' -cJf "$EVIDENCE/soleaux-phase2-source.tar.xz" .
cd "$ROOT"
find "$EVIDENCE" -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$EVIDENCE/SHA256SUMS"
