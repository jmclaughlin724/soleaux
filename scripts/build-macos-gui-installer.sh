#!/usr/bin/env bash
set -euo pipefail

VERSION="0.4.0-dev.5"
PACKAGE_VERSION="0.4.0.5"
SOURCE_COMMIT="${SOURCE_COMMIT:-unknown}"
REPOSITORY_ROOT="${1:-$(pwd)}"
BINARIES_DIR="${2:-${REPOSITORY_ROOT}/native/target/release}"
OUTPUT_DIR="${3:-${REPOSITORY_ROOT}/dist/macos-installer}"
ARCH="$(uname -m)"

if [[ "${ARCH}" != "arm64" ]]; then
  echo "This installer build must run on an Apple Silicon macOS host; found ${ARCH}." >&2
  exit 1
fi

SOLEAUX_BIN="${BINARIES_DIR}/soleaux"
SOLEAUXD_BIN="${BINARIES_DIR}/soleauxd"
for binary in "${SOLEAUX_BIN}" "${SOLEAUXD_BIN}"; do
  [[ -x "${binary}" ]] || { echo "Missing executable: ${binary}" >&2; exit 1; }
done

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
PKGROOT="${WORK}/pkgroot"
SCRIPTS="${WORK}/scripts"
RESOURCES="${WORK}/resources"
PACKAGES="${WORK}/packages"
DMGROOT="${WORK}/dmgroot"
mkdir -p \
  "${PKGROOT}/usr/local/bin" \
  "${PKGROOT}/usr/local/share/soleaux" \
  "${PKGROOT}/Applications/Uninstall Soleaux.app/Contents/MacOS" \
  "${SCRIPTS}" "${RESOURCES}" "${PACKAGES}" "${DMGROOT}" "${OUTPUT_DIR}"

install -m 0755 "${SOLEAUX_BIN}" "${PKGROOT}/usr/local/bin/soleaux"
install -m 0755 "${SOLEAUXD_BIN}" "${PKGROOT}/usr/local/bin/soleauxd"
install -m 0644 "${REPOSITORY_ROOT}/LICENSE" "${PKGROOT}/usr/local/share/soleaux/LICENSE"
install -m 0644 "${REPOSITORY_ROOT}/README.md" "${PKGROOT}/usr/local/share/soleaux/README.md"
install -m 0644 "${REPOSITORY_ROOT}/UNIFIED-MCP-PROFILE.md" "${PKGROOT}/usr/local/share/soleaux/UNIFIED-MCP-PROFILE.md"
install -m 0644 "${REPOSITORY_ROOT}/CONTEXT-PACKET-V2.md" "${PKGROOT}/usr/local/share/soleaux/CONTEXT-PACKET-V2.md"

cat > "${PKGROOT}/Applications/Uninstall Soleaux.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key><string>Uninstall Soleaux</string>
  <key>CFBundleExecutable</key><string>uninstall-soleaux</string>
  <key>CFBundleIdentifier</key><string>com.soleaux.uninstaller</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Uninstall Soleaux</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.4.0-dev.5</string>
  <key>CFBundleVersion</key><string>0.4.0.5</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict>
</plist>
PLIST

cat > "${PKGROOT}/Applications/Uninstall Soleaux.app/Contents/MacOS/uninstall-soleaux" <<'UNINSTALL'
#!/usr/bin/env bash
set -euo pipefail

CHOICE="$(/usr/bin/osascript <<'APPLESCRIPT'
button returned of (display dialog "Remove Soleaux from this Mac?\n\nYour indexed state and configuration can be preserved or removed." with title "Uninstall Soleaux" buttons {"Cancel", "Remove and Delete Data", "Remove and Preserve Data"} default button "Remove and Preserve Data" cancel button "Cancel" with icon caution)
APPLESCRIPT
)" || exit 0

CONSOLE_USER="$(/usr/bin/stat -f '%Su' /dev/console)"
USER_HOME="$(/usr/bin/dscl . -read "/Users/${CONSOLE_USER}" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"
USER_ID="$(/usr/bin/id -u "${CONSOLE_USER}")"
PLIST="${USER_HOME}/Library/LaunchAgents/com.soleaux.daemon.plist"

/bin/launchctl bootout "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/${USER_ID}" "${PLIST}" >/dev/null 2>&1 || true
/bin/rm -f "${PLIST}"

if [[ "${CHOICE}" == "Remove and Delete Data" ]]; then
  /bin/rm -rf \
    "${USER_HOME}/.local/share/soleaux" \
    "${USER_HOME}/.config/soleaux" \
    "${USER_HOME}/Library/Application Support/Soleaux" \
    "${USER_HOME}/Library/Caches/Soleaux" \
    "${USER_HOME}/Library/Logs/Soleaux"
fi

ADMIN_COMMAND='/bin/rm -f /usr/local/bin/soleaux /usr/local/bin/soleauxd; /bin/rm -rf "/usr/local/share/soleaux" "/Applications/Uninstall Soleaux.app"; /usr/sbin/pkgutil --forget com.soleaux.runtime >/dev/null 2>&1 || true'
/usr/bin/osascript - "${ADMIN_COMMAND}" <<'APPLESCRIPT'
on run argv
  do shell script (item 1 of argv) with administrator privileges
end run
APPLESCRIPT

/usr/bin/osascript <<'APPLESCRIPT'
display dialog "Soleaux has been removed." with title "Uninstall Soleaux" buttons {"OK"} default button "OK"
APPLESCRIPT
UNINSTALL
chmod 0755 "${PKGROOT}/Applications/Uninstall Soleaux.app/Contents/MacOS/uninstall-soleaux"

cat > "${SCRIPTS}/postinstall" <<'POSTINSTALL'
#!/usr/bin/env bash
set -euo pipefail

CONSOLE_USER="$(/usr/bin/stat -f '%Su' /dev/console)"
if [[ -z "${CONSOLE_USER}" || "${CONSOLE_USER}" == "root" || "${CONSOLE_USER}" == "loginwindow" || "${CONSOLE_USER}" == "_mbsetupuser" ]]; then
  exit 0
fi

USER_HOME="$(/usr/bin/dscl . -read "/Users/${CONSOLE_USER}" NFSHomeDirectory | /usr/bin/awk '{print $2}')"
USER_ID="$(/usr/bin/id -u "${CONSOLE_USER}")"
USER_GROUP="$(/usr/bin/id -g "${CONSOLE_USER}")"
PLIST="${USER_HOME}/Library/LaunchAgents/com.soleaux.daemon.plist"

/bin/mkdir -p "${USER_HOME}/Library/LaunchAgents" "${USER_HOME}/Library/Application Support/Soleaux"
/bin/chown -R "${USER_ID}:${USER_GROUP}" \
  "${USER_HOME}/Library/LaunchAgents" \
  "${USER_HOME}/Library/Application Support/Soleaux"

if ! /bin/launchctl asuser "${USER_ID}" /usr/bin/sudo -u "${CONSOLE_USER}" \
  /usr/bin/env \
  HOME="${USER_HOME}" \
  USER="${CONSOLE_USER}" \
  LOGNAME="${CONSOLE_USER}" \
  PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  /usr/local/bin/soleaux install \
    --cli /usr/local/bin/soleaux \
    --daemon /usr/local/bin/soleauxd \
    --no-start >/tmp/soleaux-installer-setup.log 2>&1; then
  cat > "${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.soleaux.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/soleauxd</string>
    <string>ipc</string>
    <string>--endpoint</string>
    <string>${USER_HOME}/Library/Application Support/Soleaux/soleaux.sock</string>
    <string>--state-db</string>
    <string>${USER_HOME}/Library/Application Support/Soleaux/state/canonical.sqlite3</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${USER_HOME}/Library/Logs/Soleaux/soleauxd.log</string>
  <key>StandardErrorPath</key><string>${USER_HOME}/Library/Logs/Soleaux/soleauxd-error.log</string>
</dict>
</plist>
PLIST
fi

/bin/chown "${USER_ID}:${USER_GROUP}" "${PLIST}"
/bin/chmod 0600 "${PLIST}"
/bin/mkdir -p "${USER_HOME}/Library/Logs/Soleaux"
/bin/chown -R "${USER_ID}:${USER_GROUP}" "${USER_HOME}/Library/Logs/Soleaux"

/bin/launchctl bootout "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/${USER_ID}" "${PLIST}" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/${USER_ID}" "${PLIST}" >/dev/null 2>&1 || true
/bin/launchctl enable "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
/bin/launchctl kickstart -k "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
exit 0
POSTINSTALL
chmod 0755 "${SCRIPTS}/postinstall"

cat > "${RESOURCES}/welcome.html" <<EOF
<!doctype html><html><body style="font-family:-apple-system;margin:24px">
<h1>Install Soleaux ${VERSION}</h1>
<p>This installer adds the Soleaux command-line service and background daemon to your Mac and configures the per-user LaunchAgent automatically.</p>
<p><strong>No Terminal commands are required.</strong></p>
<p>This is an unsigned development build. <code>productionClaimAllowed</code> remains <strong>false</strong>.</p>
</body></html>
EOF

cat > "${RESOURCES}/readme.html" <<EOF
<!doctype html><html><body style="font-family:-apple-system;margin:24px">
<h2>What will be installed</h2>
<ul>
<li><code>/usr/local/bin/soleaux</code></li>
<li><code>/usr/local/bin/soleauxd</code></li>
<li>A per-user Soleaux LaunchAgent</li>
<li><code>/Applications/Uninstall Soleaux.app</code></li>
</ul>
<p>The installer starts the Soleaux background service automatically for the signed-in user.</p>
<p>Source commit: <code>${SOURCE_COMMIT}</code></p>
</body></html>
EOF
cp "${REPOSITORY_ROOT}/LICENSE" "${RESOURCES}/LICENSE"

pkgbuild \
  --root "${PKGROOT}" \
  --scripts "${SCRIPTS}" \
  --identifier com.soleaux.runtime \
  --version "${PACKAGE_VERSION}" \
  --install-location / \
  "${PACKAGES}/SoleauxRuntime.pkg"

cat > "${WORK}/Distribution.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Soleaux ${VERSION}</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <license file="LICENSE"/>
  <readme file="readme.html" mime-type="text/html"/>
  <options customize="never" require-scripts="false" hostArchitectures="arm64"/>
  <domains enable_localSystem="true" enable_currentUserHome="false" enable_anywhere="false"/>
  <choices-outline><line choice="default"><line choice="runtime"/></line></choices-outline>
  <choice id="default"/>
  <choice id="runtime" visible="false"><pkg-ref id="com.soleaux.runtime"/></choice>
  <pkg-ref id="com.soleaux.runtime" version="${PACKAGE_VERSION}" onConclusion="none">SoleauxRuntime.pkg</pkg-ref>
</installer-gui-script>
EOF

INSTALLER_PKG="${OUTPUT_DIR}/Soleaux-${VERSION}-macOS-Apple-Silicon.pkg"
INSTALLER_DMG="${OUTPUT_DIR}/Soleaux-${VERSION}-macOS-Apple-Silicon-Installer.dmg"
productbuild \
  --distribution "${WORK}/Distribution.xml" \
  --package-path "${PACKAGES}" \
  --resources "${RESOURCES}" \
  "${INSTALLER_PKG}"

cat > "${DMGROOT}/READ ME.html" <<EOF
<!doctype html><html><body style="font-family:-apple-system;margin:32px;max-width:720px">
<h1>Soleaux ${VERSION}</h1>
<ol>
<li>Double-click <strong>Install Soleaux.pkg</strong>.</li>
<li>Follow the macOS Installer prompts.</li>
<li>The Soleaux daemon is configured and started automatically.</li>
</ol>
<p>To remove Soleaux later, open <strong>Applications → Uninstall Soleaux</strong>.</p>
<p><strong>Unsigned development build:</strong> if macOS blocks the package, Control-click it in Finder and choose <em>Open</em>. No Terminal commands are required.</p>
</body></html>
EOF
cp "${INSTALLER_PKG}" "${DMGROOT}/Install Soleaux.pkg"

hdiutil create \
  -volname "Soleaux ${VERSION} Installer" \
  -srcfolder "${DMGROOT}" \
  -ov \
  -format UDZO \
  "${INSTALLER_DMG}"

pkgutil --payload-files "${INSTALLER_PKG}" > "${OUTPUT_DIR}/PACKAGE-CONTENTS.txt"
shasum -a 256 "${INSTALLER_PKG}" "${INSTALLER_DMG}" > "${OUTPUT_DIR}/SHA256SUMS"

python3 - "${OUTPUT_DIR}" "${SOURCE_COMMIT}" "${VERSION}" <<'PY'
import hashlib
import json
import pathlib
import platform
import sys

output = pathlib.Path(sys.argv[1])
source_commit = sys.argv[2]
version = sys.argv[3]
files = []
for path in sorted(output.iterdir()):
    if not path.is_file() or path.name == "INSTALLER-MANIFEST.json":
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
manifest = {
    "schemaVersion": "soleaux.macos-installer/v1",
    "product": "Soleaux",
    "version": version,
    "sourceCommit": source_commit,
    "architecture": platform.machine(),
    "installerType": "double-clickable-pkg-in-dmg",
    "signed": False,
    "notarized": False,
    "productionClaimAllowed": False,
    "files": files,
}
(output / "INSTALLER-MANIFEST.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

echo "Built ${INSTALLER_PKG}"
echo "Built ${INSTALLER_DMG}"
