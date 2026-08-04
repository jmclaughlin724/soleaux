#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PROFILE = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
EXPECTED_CONTEXT = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"
EXPECTED_TOOLS = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
]
REQUIRED_DOCS = [
    "PROJECT-STATUS.json",
    "PROJECT-STATUS.md",
    "ROADMAP.md",
    "TASKS.md",
    "HANDOFF.md",
    "CHANGELOG.md",
    "docs/DOCUMENTATION-MANIFEST.json",
    "docs/audits/TRANSCRIPT-TO-REPOSITORY-GAP-AUDIT-2026-08-03.md",
    "docs/audits/TRANSCRIPT-GAP-REGISTRY.json",
    "docs/architecture/CAPABILITY-ABSORPTION-MAP.md",
]

errors: list[str] = []


def read(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        errors.append(f"missing required documentation: {path}")
        return ""
    return target.read_text(encoding="utf-8")


def load(path: str):
    text = read(path)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        errors.append(f"invalid JSON in {path}: {error}")
        return {}


def digest(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        errors.append(f"missing locked contract: {path}")
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()


for path in REQUIRED_DOCS:
    read(path)

status = load("PROJECT-STATUS.json")
manifest = load("docs/DOCUMENTATION-MANIFEST.json")
gaps = load("docs/audits/TRANSCRIPT-GAP-REGISTRY.json")
tasks = read("TASKS.md")
roadmap = read("ROADMAP.md")
handoff = read("HANDOFF.md")
status_md = read("PROJECT-STATUS.md")

if status.get("version") != EXPECTED_VERSION:
    errors.append("PROJECT-STATUS.json version drift")
if status.get("productionClaimAllowed") is not False:
    errors.append("productionClaimAllowed must remain false")
if status.get("canonicalBranch") != "main":
    errors.append("main must remain the canonical branch")
if status.get("publicMcp", {}).get("hardCeiling") != 12:
    errors.append("public MCP hard ceiling drift")
if status.get("publicMcp", {}).get("canonicalOrder") != EXPECTED_TOOLS:
    errors.append("canonical public tool order drift")
if status.get("phases", {}).get("4", {}).get("status") != "in_progress":
    errors.append("Phase 4 must be the current implementation phase")
phase3 = status.get("phases", {}).get("3", {})
if phase3.get("status") != "deferred_reconciliation_required":
    errors.append("Phase 3 deferred reconciliation status drift")
if phase3.get("requiredArms") != [
    "control_no_soleaux",
    "historical_python",
    "native_treatment",
]:
    errors.append("Phase 3 three-arm design drift")

profile_path = (
    "native/contracts/unified-mcp-profile-v2.json"
    if (ROOT / "native/contracts/unified-mcp-profile-v2.json").is_file()
    else "contracts/unified-mcp-profile-v2.json"
)
context_path = (
    "native/contracts/context-packet-v2.schema.json"
    if (ROOT / "native/contracts/context-packet-v2.schema.json").is_file()
    else "contracts/context-packet-v2.schema.json"
)
if digest(profile_path) != EXPECTED_PROFILE:
    errors.append("unified MCP profile digest drift")
if digest(context_path) != EXPECTED_CONTEXT:
    errors.append("Context Packet V2 digest drift")

if manifest.get("version") != EXPECTED_VERSION:
    errors.append("documentation manifest version drift")
manifest_docs = set(manifest.get("authoritative", []))
for required in REQUIRED_DOCS:
    if required not in manifest_docs and required not in {
        "CHANGELOG.md",
        "docs/DOCUMENTATION-MANIFEST.json",
    }:
        errors.append(f"documentation manifest omitted {required}")

ids = re.findall(r"\*\*([A-Z]+-\d+[A-Z]?)\*\*", tasks)
duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
if duplicates:
    errors.append(f"duplicate task IDs: {', '.join(duplicates)}")
task_ids = set(ids)
for required in [
    "P4-010",
    "P4-011",
    "P4-012",
    "P4-013",
    "P4-014",
    "P4-015",
    "P4-017",
    "P4-018",
    "P4-019",
    "P4-020",
    "P4-021",
    "P4-022",
    "P4-023",
    "P4-024",
    "P4-025",
    "P4-026",
    "P5-014",
    "P5-029",
    "P6-016",
    "P7-013",
    "P8-008",
]:
    if required not in task_ids:
        errors.append(f"executable task registry omitted {required}")

for item in gaps.get("gaps", []):
    for task_id in item.get("tasks", []):
        if task_id and task_id not in task_ids:
            errors.append(
                f"gap registry {item.get('id', '<unknown>')} references missing task {task_id}"
            )

for path, text in {
    "PROJECT-STATUS.md": status_md,
    "ROADMAP.md": roadmap,
    "HANDOFF.md": handoff,
}.items():
    if "0.4.0-dev.5" not in text:
        errors.append(f"{path} omitted locked version")
    if "productionClaimAllowed" not in text or "false" not in text:
        errors.append(f"{path} omitted false production claim")
    if "Phase 4" not in text:
        errors.append(f"{path} omitted current Phase 4")

for path in ["PROJECT-STATUS.md", "ROADMAP.md", "HANDOFF.md"]:
    text = read(path).lower()
    if "agent operating system" in text and "not an agent operating system" not in text:
        errors.append(f"{path} reintroduced agent-operating-system positioning")

if errors:
    print("Soleaux documentation consistency: FAIL", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    json.dumps(
        {
            "status": "pass",
            "version": EXPECTED_VERSION,
            "phase": 4,
            "productionClaimAllowed": False,
            "publicToolCeiling": 12,
            "taskCount": len(task_ids),
            "gapCount": len(gaps.get("gaps", [])),
        },
        sort_keys=True,
    )
)
