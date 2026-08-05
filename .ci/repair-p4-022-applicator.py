#!/usr/bin/env python3
from pathlib import Path

path = Path(".ci/apply-p4-022.py")
text = path.read_text(encoding="utf-8")

terminal_old = '''    end_index = source.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{label}: end marker not found")
'''
terminal_new = '''    if end == "":
        end_index = len(source)
    else:
        end_index = source.find(end, start_index)
        if end_index < 0:
            raise SystemExit(f"{label}: end marker not found")
'''
if text.count(terminal_old) != 1:
    raise SystemExit("P4-022 terminal-block repair target drifted")
text = text.replace(terminal_old, terminal_new, 1)

admit_old = '''    "fn admit(root: &Path, relative: &str) -> Result<PathBuf> {",
'''
admit_new = '''    "fn admit(root: &Path, relative: impl AsRef<Path>) -> Result<PathBuf> {",
'''
if text.count(admit_old) != 1:
    raise SystemExit("P4-022 path-admission marker repair target drifted")
text = text.replace(admit_old, admit_new, 1)

path.write_text(text, encoding="utf-8")
