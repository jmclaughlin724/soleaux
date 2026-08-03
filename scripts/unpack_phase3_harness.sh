#!/usr/bin/env bash
set -euo pipefail

ROOT=${1:-.}
OUT=${2:-.phase3-tools}
mkdir -p "$OUT"

unpack_one() {
  local name=$1
  local b64_size=$2
  local b64_sha=$3
  local xz_size=$4
  local xz_sha=$5
  local py_size=$6
  local py_sha=$7
  local source="$ROOT/phase3/carriers/$name.xz.b64"
  local archive="$OUT/$name.xz"
  local target="$OUT/$name"

  test -f "$source"
  test "$(wc -c < "$source" | tr -d ' ')" = "$b64_size"
  printf '%s  %s\n' "$b64_sha" "$source" | sha256sum -c -
  test "$(LC_ALL=C tr -cd '\r\n' < "$source" | wc -c | tr -d ' ')" = 0

  base64 --decode "$source" > "$archive"
  test "$(wc -c < "$archive" | tr -d ' ')" = "$xz_size"
  printf '%s  %s\n' "$xz_sha" "$archive" | sha256sum -c -
  xz -t "$archive"
  xz --decompress --stdout "$archive" > "$target"
  test "$(wc -c < "$target" | tr -d ' ')" = "$py_size"
  printf '%s  %s\n' "$py_sha" "$target" | sha256sum -c -
  chmod 0755 "$target"
  python -m py_compile "$target"
}

# Primary harness (compressed carrier — required)
unpack_one \
  phase3_live_wedge.py \
  18792 \
  d3fe7021fcf80fd7cbb1cac02f77802fcffde4bcaffa97c8243b270f842f27b2 \
  14092 \
  500b805a2eef6cc266a1756e37c5d88e302427b7378020f0dc46664369f795f4 \
  62001 \
  bf7a158811aa4e9efdc3d8c675fa7c3b1d3d8601ba104781f012f9c9eecd03bb

# Verifier: prefer plain source if present (avoids long-base64 transmission issues),
# otherwise fall back to compressed carrier.
VERIFY_PLAIN="$ROOT/phase3/carriers/verify_phase3_artifact.py"
VERIFY_TARGET="$OUT/verify_phase3_artifact.py"
if [[ -f "$VERIFY_PLAIN" ]]; then
  test "$(wc -c < "$VERIFY_PLAIN" | tr -d ' ')" = "10865"
  printf '%s  %s\n' "560b5cfcae0f281326de5015ce031d4b75e6a492509d0885ab6a49d9a6bc4faf" "$VERIFY_PLAIN" | sha256sum -c -
  cp "$VERIFY_PLAIN" "$VERIFY_TARGET"
  chmod 0755 "$VERIFY_TARGET"
  python -m py_compile "$VERIFY_TARGET"
  echo "verifier installed from plain source"
else
  unpack_one \
    verify_phase3_artifact.py \
    4640 \
    d6c863d1270842dd810688eef6dd26f5f5101adab401f98f508d3f6cf43968d0 \
    3480 \
    c1c803ac048cef5021e92a9aef72e196f87cdd4ad6a07533112ac1e3c86a391b \
    10865 \
    560b5cfcae0f281326de5015ce031d4b75e6a492509d0885ab6a49d9a6bc4faf
fi

printf 'Phase 3 harness unpacked and verified in %s\n' "$OUT"
