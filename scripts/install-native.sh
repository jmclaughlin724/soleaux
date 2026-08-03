#!/usr/bin/env bash
# Install Soleaux native binaries (pre-production 0.4.0-dev.5).
# productionClaimAllowed remains false until explicitly lifted.
set -euo pipefail

REPO="${SOLEAUX_REPO:-jmclaughlin724/soleaux}"
TAG="${SOLEAUX_NATIVE_TAG:-native-v0.4.0-dev.5}"
PREFIX="${SOLEAUX_PREFIX:-$HOME/.local}"
BIN_DIR="${PREFIX}/bin"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "${OS}-${ARCH}" in
  linux-x86_64|linux-amd64) TARGET="linux-x86_64" ;;
  linux-aarch64|linux-arm64) TARGET="linux-aarch64" ;;
  darwin-arm64) TARGET="darwin-arm64" ;;
  darwin-x86_64) TARGET="darwin-x86_64" ;;
  *)
    echo "unsupported platform: ${OS}-${ARCH}" >&2
    exit 1
    ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

API="https://api.github.com/repos/${REPO}/releases/tags/${TAG}"
echo "fetching release ${TAG} from ${REPO}"
if ! curl -fsSL "$API" > "$TMP/release.json"; then
  echo "failed to fetch release ${TAG}. Create it via workflow native-install-release first." >&2
  exit 1
fi

download_asset() {
  local name="$1"
  local url
  url="$(python3 - "$TMP/release.json" "$name" <<'PY'
import json, sys
rel = json.load(open(sys.argv[1]))
name = sys.argv[2]
for a in rel.get("assets", []):
    if a.get("name") == name:
        print(a["browser_download_url"])
        raise SystemExit(0)
raise SystemExit(f"asset not found: {name}")
PY
)"
  curl -fsSL "$url" -o "$TMP/$name"
}

download_asset "soleaux-${TARGET}"
download_asset "soleauxd-${TARGET}"
download_asset "SHA256SUMS"

(
  cd "$TMP"
  grep -E "soleaux(-d)?-${TARGET}$" SHA256SUMS | sha256sum -c -
)

mkdir -p "$BIN_DIR"
install -m 0755 "$TMP/soleaux-${TARGET}" "$BIN_DIR/soleaux"
install -m 0755 "$TMP/soleauxd-${TARGET}" "$BIN_DIR/soleauxd"

echo "installed:"
echo "  $BIN_DIR/soleaux"
echo "  $BIN_DIR/soleauxd"
"$BIN_DIR/soleaux" --version
"$BIN_DIR/soleauxd" --version
echo
echo "NOTE: productionClaimAllowed=false (0.4.0-dev.5 pre-production)."
echo "Ensure $BIN_DIR is on PATH, then point MCP hosts at: $BIN_DIR/soleaux"
