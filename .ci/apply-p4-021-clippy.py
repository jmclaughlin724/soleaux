#!/usr/bin/env python3
from pathlib import Path

path = Path("telemetry/daemon/src/main.rs")
text = path.read_text(encoding="utf-8")
old = '''            query
                .backend
                .as_deref()
                .map_or(true, |backend| event.backend == backend)
'''
new = '''            query
                .backend
                .as_deref()
                .is_none_or(|backend| event.backend == backend)
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one P4-021 Clippy target, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
