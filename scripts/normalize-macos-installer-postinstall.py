#!/usr/bin/env python3
"""Replace the macOS package postinstall with deterministic per-user setup."""

from pathlib import Path

path = Path("scripts/build-macos-gui-installer.sh")
text = path.read_text(encoding="utf-8")
start_marker = 'cat > "${SCRIPTS}/postinstall" <<\'POSTINSTALL\'\n'
end_marker = 'POSTINSTALL\nchmod 0755 "${SCRIPTS}/postinstall"\n'
start = text.find(start_marker)
if start < 0:
    raise SystemExit("postinstall start marker not found")
end = text.find(end_marker, start)
if end < 0:
    raise SystemExit("postinstall end marker not found")
end += len(end_marker)

replacement = r'''cat > "${SCRIPTS}/postinstall" <<'POSTINSTALL'
#!/usr/bin/env bash
# The package payload is already installed at this point. Per-user daemon setup
# is intentionally best-effort so a headless/macOS setup session cannot corrupt
# or roll back the otherwise valid application installation.
set -u

LOG_FILE="/var/tmp/soleaux-postinstall.log"
exec >>"${LOG_FILE}" 2>&1
printf '%s\n' "Soleaux postinstall started at $(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"

CONSOLE_USER="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
case "${CONSOLE_USER}" in
  ""|root|loginwindow|_mbsetupuser)
    printf '%s\n' "No signed-in desktop user; deferring LaunchAgent activation."
    exit 0
    ;;
esac

USER_HOME="$(/usr/bin/dscl . -read "/Users/${CONSOLE_USER}" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"
USER_ID="$(/usr/bin/id -u "${CONSOLE_USER}" 2>/dev/null || true)"
USER_GROUP="$(/usr/bin/id -g "${CONSOLE_USER}" 2>/dev/null || true)"
if [[ -z "${USER_HOME}" || -z "${USER_ID}" || -z "${USER_GROUP}" ]]; then
  printf '%s\n' "Could not resolve the signed-in user; deferring LaunchAgent activation."
  exit 0
fi

LAUNCH_AGENTS="${USER_HOME}/Library/LaunchAgents"
SUPPORT_DIR="${USER_HOME}/Library/Application Support/Soleaux"
STATE_DIR="${SUPPORT_DIR}/state"
LOG_DIR="${USER_HOME}/Library/Logs/Soleaux"
PLIST="${LAUNCH_AGENTS}/com.soleaux.daemon.plist"
PLIST_TMP="${PLIST}.tmp.$$"

/bin/mkdir -p "${LAUNCH_AGENTS}" "${STATE_DIR}" "${LOG_DIR}" || {
  printf '%s\n' "Could not create per-user Soleaux directories; daemon activation deferred."
  exit 0
}

xml_escape() {
  /usr/bin/sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}
ESCAPED_SUPPORT="$(printf '%s' "${SUPPORT_DIR}" | xml_escape)"
ESCAPED_LOG="$(printf '%s' "${LOG_DIR}" | xml_escape)"

cat > "${PLIST_TMP}" <<PLIST
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
    <string>${ESCAPED_SUPPORT}/soleaux.sock</string>
    <string>--state-db</string>
    <string>${ESCAPED_SUPPORT}/state/canonical.sqlite3</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>${ESCAPED_LOG}/soleauxd.log</string>
  <key>StandardErrorPath</key><string>${ESCAPED_LOG}/soleauxd-error.log</string>
</dict>
</plist>
PLIST

if ! /usr/bin/plutil -lint "${PLIST_TMP}" >/dev/null 2>&1; then
  printf '%s\n' "Generated LaunchAgent failed validation; daemon activation deferred."
  /bin/rm -f "${PLIST_TMP}"
  exit 0
fi

/bin/mv -f "${PLIST_TMP}" "${PLIST}" || exit 0
/bin/chown -R "${USER_ID}:${USER_GROUP}" "${SUPPORT_DIR}" "${LOG_DIR}" || true
/bin/chown "${USER_ID}:${USER_GROUP}" "${PLIST}" || true
/bin/chmod 0600 "${PLIST}" || true

# A real desktop installation has a gui/$UID launchd domain. Headless build
# machines may not, so activation failures are logged and never invalidate the
# package installation itself.
/bin/launchctl bootout "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
/bin/launchctl bootout "gui/${USER_ID}" "${PLIST}" >/dev/null 2>&1 || true
if /bin/launchctl bootstrap "gui/${USER_ID}" "${PLIST}" >/dev/null 2>&1; then
  /bin/launchctl enable "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
  /bin/launchctl kickstart -k "gui/${USER_ID}/com.soleaux.daemon" >/dev/null 2>&1 || true
  printf '%s\n' "Soleaux LaunchAgent installed and started for ${CONSOLE_USER}."
else
  printf '%s\n' "LaunchAgent written for ${CONSOLE_USER}; activation will occur at the next login."
fi
exit 0
POSTINSTALL
chmod 0755 "${SCRIPTS}/postinstall"
'''

path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
