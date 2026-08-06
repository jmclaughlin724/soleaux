#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/check_documentation_consistency.py"
text = PATH.read_text(encoding="utf-8")

before = '''if "- [ ] **P5-001**" not in tasks_text:
    fail("P5-001 must be the first open implementation task")
'''
after = '''if "- [x] **P5-001**" not in tasks_text:
    fail("P5-001 must be closed")
if "- [ ] **P5-002**" not in tasks_text:
    fail("P5-002 must be the first open implementation task")
'''
if text.count(before) != 1:
    raise SystemExit("P5-001 checker transition target drifted")
text = text.replace(before, after, 1)

required = (
    'p5_001 = load_json("P5-001-CLOSURE-RECEIPT.json")',
    'fail("P5-001 receipt is not closed")',
    '"nextTask": "P5-002"',
)
for marker in required:
    if marker not in text:
        raise SystemExit(f"P5-001 checker convergence omitted: {marker}")

PATH.write_text(text, encoding="utf-8")
print("P5-001 checker now requires P5-001 closed and P5-002 open")
