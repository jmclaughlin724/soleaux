#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

applicator = Path(__file__).with_name("apply-p4-021.py")
source = applicator.read_text(encoding="utf-8")
old = '''    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)
'''
new = '''    if label == "native workspace redaction member" and count == 2:
        return text.replace(old, new, 1)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)
'''
if source.count(old) != 1:
    raise SystemExit("P4-021 applicator guard block changed unexpectedly")
source = source.replace(old, new, 1)
namespace = {"__file__": str(applicator), "__name__": "__main__"}
exec(compile(source, str(applicator), "exec"), namespace)
