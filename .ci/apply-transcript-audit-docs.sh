#!/usr/bin/env bash
# One-shot, hash-bound documentation reconciliation applicator.
set -euo pipefail

carrier=.ci/transcript-audit-docs.tar.xz.b64
encoded=/tmp/soleaux-transcript-audit-docs.tar.xz.b64
archive=/tmp/soleaux-transcript-audit-docs.tar.xz

tr -d '\r\n' < "$carrier" > "$encoded"
test "$(wc -c < "$encoded")" -eq 36152
echo "7d61ebd520945b734de3ca782a61d378d531870d6a7db91ef6240cb35b0a68b8  $encoded" | sha256sum -c -
base64 --decode "$encoded" > "$archive"
test "$(wc -c < "$archive")" -eq 27112
echo "700f613f33ef51ad9537a49500d71bfdde908c3b7dc1a8a5982fd629dbdb723d  $archive" | sha256sum -c -
xz -t "$archive"

python3 - "$archive" <<'PY'
from pathlib import Path, PurePosixPath
import sys, tarfile
archive = Path(sys.argv[1])
with tarfile.open(archive, "r:xz") as tf:
    members = tf.getmembers()
    if not members:
        raise SystemExit("empty documentation carrier")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise SystemExit(f"unsafe carrier member: {member.name}")
        if not member.isfile():
            raise SystemExit(f"unexpected non-file member: {member.name}")
    tf.extractall(path=".", filter="data")
PY

chmod +x scripts/check_documentation_consistency.py
python3 scripts/check_documentation_consistency.py
python3 - <<'PY'
import json
from pathlib import Path
for path in Path('.').glob('PROJECT-STATUS.json'):
    json.loads(path.read_text(encoding='utf-8'))
for path in Path('docs').rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
PY
git diff --check
