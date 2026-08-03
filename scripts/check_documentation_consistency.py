#!/usr/bin/env python3
"""Fail-closed consistency validation for the Soleaux documentation system."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PROFILE_SHA = "89a2b783c4bd9c0ae834a5894dceb2c4abcaa8050dd0f57ed967a9c57e3a60fc"
EXPECTED_CONTEXT_SHA = "3bbb53e84b0624f2a1de26bad7f2031b6a7cf0f7e892262d584347bd54b6003f"
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
FORBIDDEN_PUBLIC = [
    r"\bSoleaux is an agent operating system\b",
    r"\bSoleaux is production[- ]ready\b",
    r"\bSoleaux is generally available\b",
    r"\b1\.0\.0-rc\.1\b",
    r"\bfixed local catalog is ten tools\b",
    r"\bVersion `0\.1\.0`\b",
    r"\bSoleaux is a local FastMCP server\b",
]


class Failure(Exception):
    pass


def load_json(path: str) -> dict:
    target = ROOT / path
    if not target.is_file():
        raise Failure(f"missing {path}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"invalid JSON {path}: {exc}") from exc


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def main() -> int:
    manifest = load_json("docs/DOCUMENTATION-MANIFEST.json")
    package_mode = os.environ.get("SOLEAUX_DOCS_PACKAGE_MODE") == "1"
    inherited_locked_docs = {"UNIFIED-MCP-PROFILE.md", "CONTEXT-PACKET-V2.md"}
    for path in manifest["required"]:
        if package_mode and path in inherited_locked_docs:
            continue
        require((ROOT / path).is_file(), f"required documentation missing: {path}")

    status = load_json("PROJECT-STATUS.json")
    require(status["version"] == EXPECTED_VERSION, "status version drift")
    require(status["productionClaimAllowed"] is False, "production claim must remain false")
    require(status["currentPhase"]["number"] == 3, "current phase must be 3")
    require(status["currentPhase"]["status"] == "unblocked_not_started", "Phase 3 status drift")
    require(status["publicMcp"]["hardCeiling"] == 12, "public tool ceiling drift")
    require(status["publicMcp"]["canonicalTools"] == EXPECTED_TOOLS, "canonical tool drift")
    require(
        status["lockedContracts"]["unifiedMcpProfile"]["sha256"] == EXPECTED_PROFILE_SHA,
        "profile digest declaration drift",
    )
    require(
        status["lockedContracts"]["contextPacketV2"]["sha256"] == EXPECTED_CONTEXT_SHA,
        "context digest declaration drift",
    )

    if (ROOT / "contracts/unified-mcp-profile-v2.json").is_file():
        require(
            sha256("contracts/unified-mcp-profile-v2.json") == EXPECTED_PROFILE_SHA,
            "profile contract bytes drift",
        )
    if (ROOT / "contracts/context-packet-v2.schema.json").is_file():
        require(
            sha256("contracts/context-packet-v2.schema.json") == EXPECTED_CONTEXT_SHA,
            "context contract bytes drift",
        )

    phase3 = load_json("docs/experiments/phase3/STATUS.json")
    require(phase3["status"] == "draft_blocked", "Phase 3 must remain draft_blocked before model lock")
    require(phase3["phase3Started"] is False, "Phase 3 must not be marked started")
    require(phase3["productionClaimAllowed"] is False, "Phase 3 cannot change production claim")
    require(phase3["runAuthorization"]["allowed"] is False, "live runs cannot be authorized yet")

    tasks = load_json("docs/experiments/phase3/TASKS.json")
    require(tasks["frozen"] is True, "Phase 3 task registry must be frozen")
    require([item["id"] for item in tasks["tasks"]] == ["P3-T01", "P3-T02", "P3-T03"], "task IDs drift")
    require(len({item["id"] for item in tasks["tasks"]}) == 3, "duplicate Phase 3 task ID")

    results = (ROOT / "docs/experiments/phase3/RESULTS.md").read_text(encoding="utf-8")
    require("Status: NOT RUN" in results, "Phase 3 results must remain NOT RUN")

    public_files = [
        "README.md",
        "docs/marketing/MESSAGING.md",
        "docs/marketing/WEBSITE-COPY.md",
        "docs/marketing/FAQ.md",
    ]
    for path in public_files:
        content = (ROOT / path).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PUBLIC:
            require(
                re.search(pattern, content, flags=re.IGNORECASE) is None,
                f"prohibited public claim in {path}: {pattern}",
            )

    status_md = (ROOT / "PROJECT-STATUS.md").read_text(encoding="utf-8")
    require("Phase 2:                     CLOSED" in status_md, "human status Phase 2 drift")
    require("Phase 3:                     UNBLOCKED, NOT STARTED" in status_md, "human status Phase 3 drift")
    require("productionClaimAllowed:      false" in status_md, "human production claim drift")

    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    tasks_md = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    handoff = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
    for phase in range(3, 9):
        require(f"Phase {phase}" in roadmap, f"roadmap missing Phase {phase}")
        require(f"Phase {phase}" in tasks_md, f"tasks missing Phase {phase}")
    require("P3-005" in handoff, "handoff must identify the exact next task")

    print(json.dumps({
        "status": "pass",
        "version": EXPECTED_VERSION,
        "currentPhase": 3,
        "productionClaimAllowed": False,
        "publicToolCount": len(EXPECTED_TOOLS),
        "requiredDocuments": len(manifest["required"]),
        "phase3Status": phase3["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as exc:
        print(f"documentation consistency failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
