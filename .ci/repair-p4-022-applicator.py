#!/usr/bin/env python3
from pathlib import Path

path = Path(".ci/apply-p4-022.py")
text = path.read_text(encoding="utf-8")
old = '''    end_index = source.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{label}: end marker not found")
'''
new = '''    if end == "":
        end_index = len(source)
    else:
        end_index = source.find(end, start_index)
        if end_index < 0:
            raise SystemExit(f"{label}: end marker not found")
'''
if text.count(old) != 1:
    raise SystemExit("P4-022 applicator repair target drifted")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
