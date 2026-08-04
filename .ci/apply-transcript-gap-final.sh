#!/usr/bin/env bash
set -euo pipefail

encoded=/tmp/soleaux-transcript-gap-final.tar.xz.b64
archive=/tmp/soleaux-transcript-gap-final.tar.xz

cat .ci/transcript-gap-final.part-* | tr -d '\r\n' > "$encoded"
test "$(wc -c < "$encoded")" -eq 32124
echo "0f7c1ec53a5650d6e9a574163d309c25d02842b3f2b0631301cc0f6bd2c1dc23  $encoded" | sha256sum -c -
base64 --decode "$encoded" > "$archive"
test "$(wc -c < "$archive")" -eq 24092
echo "d018a074e9e6eb8a1683fe1efe8349b8116ff41995de9c4c9489f6dc3a7a9143  $archive" | sha256sum -c -
xz -t "$archive"

python3 - "$archive" <<'PY'
from pathlib import Path, PurePosixPath
import sys, tarfile
archive = Path(sys.argv[1])
with tarfile.open(archive, "r:xz") as tf:
    members = tf.getmembers()
    if not members:
        raise SystemExit("empty transcript gap package")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise SystemExit(f"unsafe archive member: {member.name}")
        if not member.isfile():
            raise SystemExit(f"unexpected non-file member: {member.name}")
    tf.extractall(path=".", filter="data")
PY

chmod +x scripts/check_documentation_consistency.py
python3 scripts/check_documentation_consistency.py
python3 - <<'PY'
import json
from pathlib import Path
json.loads(Path('PROJECT-STATUS.json').read_text(encoding='utf-8'))
for path in Path('docs').rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
PY
git diff --check
