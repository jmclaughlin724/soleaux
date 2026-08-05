from pathlib import Path

path = Path('.github/workflows/verify-phase4-unsigned-alpha.yml')
text = path.read_text(encoding='utf-8')
replacements = [
    (
        '          "$BIN/soleaux" doctor | tee "$LOGS/doctor.json"\n',
        '          "$BIN/soleaux" doctor "$GITHUB_WORKSPACE" --json | tee "$LOGS/doctor.json"\n',
        'explicit doctor repository',
    ),
    (
        "          uninstall = json.loads((root / 'uninstall.json').read_text())\n",
        "          uninstall = json.loads((root / 'uninstall.json').read_text())\n          uninstall_report = uninstall['uninstall']\n",
        'uninstall response owner',
    ),
    (
        "          assert uninstall['preservedState'] is True\n          assert uninstall['removedManifest'] is True\n          assert uninstall['removedCli'] is True\n          assert uninstall['removedDaemon'] is True\n",
        "          assert uninstall_report['preservedState'] is True\n          assert uninstall_report['removedManifest'] is True\n          assert uninstall_report['removedCli'] is True\n          assert uninstall_report['removedDaemon'] is True\n",
        'uninstall nested assertions',
    ),
    (
        "      - uses: actions/upload-artifact@v4\n        with:\n          name: soleaux-phase4-alpha-${{ env.SOURCE_COMMIT }}\n",
        "      - uses: actions/upload-artifact@v4\n        if: always()\n        with:\n          name: soleaux-phase4-alpha-${{ env.SOURCE_COMMIT }}\n",
        'always upload evidence',
    ),
]
for old, new, label in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'alpha verifier target drifted for {label}: {count}')
    text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8')
