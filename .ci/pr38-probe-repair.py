#!/usr/bin/env python3
"""Repair deterministic transcription defects after generating PR #38 probe sources."""

from __future__ import annotations

import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


probe_path = Path("native/scripts/probe_client_capabilities.py")
probe = probe_path.read_text(encoding="utf-8")
probe = replace_once(
    probe,
    "import argparse\n",
    "import argparse\nimport contextlib\n",
    "contextlib import",
)
probe = replace_once(
    probe,
    "SIGNAL_MARKERS: dict[str, tuple[st, ...]] = {",
    "SIGNAL_MARKERS: dict[str, tuple[str, ...]] = {",
    "signal marker annotation",
)
old_block = (
    "                try:\n"
    "                    process." + "kill()\n"
    "                except ProcessLookupError:\n"
    "                    pass\n"
)
new_block = (
    "                with contextlib.suppress(ProcessLookupError):\n"
    "                    process." + "kill()\n"
)
probe = replace_once(probe, old_block, new_block, "bounded process termination")
probe_path.write_text(probe, encoding="utf-8")

validator_path = Path("native/scripts/validate_client_capability_matrix.py")
validator = validator_path.read_text(encoding="utf-8")
validator = validator.replace(" is NOT False", " is not False")
validator = replace_once(
    validator,
    'parsed = urlparse(str(asset.get("url", ""))\n',
    'parsed = urlparse(str(asset.get("url", "")))\n',
    "OpenCode URL parser",
)
validator_path.write_text(validator, encoding="utf-8")

matrix_path = Path("native/contracts/client-capability-matrix-v1.json")
matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
for platform in matrix["platforms"]:
    if platform["id"] != "codex":
        continue
    for source in platform["sources"]:
        if source.get("url") == "https://registry.npmjs.org/@openai/codex/latest":
            source["url"] = "https://registry.npmjs.org/@openai/codex/0.146.1"
matrix_path.write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

print("generated PR38 probe sources repaired")
