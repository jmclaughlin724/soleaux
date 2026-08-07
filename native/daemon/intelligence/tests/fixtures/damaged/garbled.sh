#!/usr/bin/env bash
set -euo pipefail

deploy() {
  rsync -az "$SRC" "$DST
}

echo "unterminated ${
