#!/usr/bin/env python3
from pathlib import Path
import subprocess

path = Path("native/daemon/ipc/src/tests.rs")
text = path.read_text(encoding="utf-8")
old = '    assert!(format!("{error:#}").contains("verified client compatibility matrix"));\n'
new = (
    '    assert!(\n'
    '        format!("{error:#}").contains(\n'
    '            "verified daemon-trusted client compatibility decision"\n'
    '        )\n'
    '    );\n'
)
if text.count(old) != 1:
    raise SystemExit("read-write denial assertion target drifted")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
subprocess.run(
    [
        "git",
        "add",
        "native/daemon/ipc/src/compatibility.rs",
        "native/daemon/ipc/src/tests.rs",
    ],
    check=True,
)
