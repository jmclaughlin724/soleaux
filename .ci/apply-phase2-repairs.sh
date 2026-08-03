#!/usr/bin/env bash
set -euo pipefail

SRC=${1:?usage: apply-phase2-repairs.sh <phase2-source>}

after_cleanup() {
  rm -f "${tmp:-}"
}
trap after_cleanup EXIT

replace_exact_line() {
  local relative=$1
  local old=$2
  local new=$3
  local file="$SRC/$relative"
  local count

  test -f "$file"
  count=$(grep -Fxc -- "$old" "$file" || true)
  if [ "$count" -ne 1 ]; then
    printf 'phase2 repair target mismatch: %s expected=1 observed=%s\n' "$relative" "$count" >&2
    exit 1
  fi

  tmp=$(mktemp)
  awk -v old="$old" -v new="$new" '
    BEGIN { changed = 0 }
    $0 == old { print new; changed += 1; next }
    { print }
    END { if (changed != 1) exit 42 }
  ' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
  tmp=

  grep -Fqx -- "$new" "$file"
  if grep -Fqx -- "$old" "$file"; then
    printf 'phase2 repair did not remove target: %s\n' "$relative" >&2
    exit 1
  fi

  printf 'repaired %s\n' "$relative"
}

replace_exact_line \
  'daemon/intelligence/src/governance.rs' \
  'use std::{collections::BTreeSet, fs, path::Path};' \
  'use std::{fs, path::Path};'

replace_exact_line \
  'daemon/mcp/src/gateway.rs' \
  '    ffi::OsString,' \
  ''

replace_exact_line \
  'daemon/mcp/src/provisioning.rs' \
  'use serde_json::{Value, json};' \
  'use serde_json::json;'

replace_exact_line \
  'daemon/intelligence/src/governance.rs' \
  '        trust: "verified_repository_governance".to_string(),' \
  '        trust: "verified_repository_metadata".to_string(),'

printf 'Phase 2 deterministic source repairs applied\n'
