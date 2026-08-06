#!/usr/bin/env python3
"""Fail-closed validation for the Phase 5 client capability matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "native" / "contracts" / "client-capability-matrix-v1.json"
EXPECTED_VERSIONS = {
    "claude_code": "2.1.223",
    "claude_desktop": "supported-current",
    "codex": "0.146.1",
    "opencode": "1.18.14",
    "cursor": "supported-current",
    "generic_mcp_host": "mcp-2025-11-25",
}
EXPECTED_KINDS = {
    "claude_code": "adapter",
    "claude_desktop": "desktop",
    "codex": "adapter",
    "opencode": "adapter",
    "cursor": "editor",
    "generic_mcp_host": "adapter",
}
EXPECTED_TASKS = {"P5-002", "P5-003", "P5-004", "P5-005", "P5-006"}
ALLOWED_OFFICIAL_HOSTS = {
    "code.claude.com",
    "support.claude.com",
    "registry.npmjs.org",
    "github.com",
    "opencode.ai",
    "cursor.com",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    return value


def validate_source(source: dict[str, Any], platform: str) -> None:
    if "url" in source:
        from urllib.parse import urlparse

        parsed = urlparse(str(source["url"]))
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_OFFICIAL_HOSTS:
            fail(f"{platform} has a non-official source URL: {source['url']}")
    elif "path" in source:
        relative = Path(str(source["path"]))
        if relative.is_absolute():
            fail(f"{platform} source path must be repository relative: {source['path']}")
        path = (ROOT / relative).resolve()
        if path != ROOT and ROOT not in path.parents:
            fail(f"{platform} source path escapes the repository: {source['path']}")
        if not path.is_file():
            fail(f"{platform} source path does not exist: {source['path']}")
    else:
        fail(f"{platform} source must contain url or path")


def validate_matrix(path: Path, selected_platform: str | None) -> dict[str, Any]:
    raw = path.read_bytes()
    data = require_object(json.loads(raw), "matrix")
    if data.get("schemaVersion") != "soleaux.client-capability-matrix/v1":
        fail("unsupported client capability matrix schema")
    if data.get("probeSchemaVersion") != "soleaux.client-capability-probe/v1":
        fail("unsupported client capability probe schema")
    if data.get("clientProtocolVersion") != "soleaux.client/v1":
        fail("client protocol version drifted")
    if data.get("publicToolCeiling") != 12:
        fail("public MCP ceiling drifted")
    if data.get("productionClaimAllowed") is not False:
        fail("client matrix cannot enable a production claim")

    platforms = require_list(data.get("platforms"), "platforms")
    if len(platforms) != 6:
        fail("client matrix must contain exactly six platform records")
    ids: set[str] = set()
    tasks: set[str] = set()
    mutation_eligible: list[tuple[str, str]] = []
    validated: list[str] = []
    for raw_platform in platforms:
        platform = require_object(raw_platform, "platform")
        platform_id = str(platform.get("id", ""))
        if platform_id not in EXPECTED_VERSIONS:
            fail(f"unexpected client platform: {platform_id}")
        if platform_id in ids:
            fail(f"duplicate client platform: {platform_id}")
        ids.add(platform_id)
        tasks.add(str(platform.get("task", "")))
        if platform.get("clientKind") != EXPECTED_KINDS[platform_id]:
            fail(f"{platform_id} client kind drifted")
        if not require_object(platform.get("capabilities"), f"{platform_id}.capabilities"):
            fail(f"{platform_id} capabilities are empty")
        sources = require_list(platform.get("sources"), f"{platform_id}.sources")
        if not sources:
            fail(f"{platform_id} has no sources")
        for source in sources:
            validate_source(require_object(source, f"{platform_id}.source"), platform_id)

        versions = require_list(platform.get("versions"), f"{platform_id}.versions")
        if len(versions) != 1:
            fail(f"{platform_id} must have one exact observed matrix entry")
        version = require_object(versions[0], f"{platform_id}.version")
        if version.get("version") != EXPECTED_VERSIONS[platform_id]:
            fail(f"{platform_id} version drifted")
        required = require_list(
            version.get("requiredBinarySignals"), f"{platform_id}.requiredBinarySignals"
        )
        if len(required) != len(set(map(str, required))):
            fail(f"{platform_id} has duplicate required binary signals")
        commands = require_object(version.get("binaryCommands"), f"{platform_id}.binaryCommands")
        if platform.get("probeMode") != "native_binary_conformance":
            for signal in required:
                argv = commands.get(signal)
                if (
                    not isinstance(argv, list)
                    or not argv
                    or not all(isinstance(item, str) and item for item in argv)
                ):
                    fail(f"{platform_id} lacks argv for required signal {signal}")
        if version.get("mutationEligible") is True:
            mutation_eligible.append((platform_id, str(version["version"])))
        if platform_id == "opencode":
            asset = require_object(version.get("linuxX64Asset"), "opencode.linuxX64Asset")
            digest = str(asset.get("sha256", ""))
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                fail("OpenCode asset digest must be lowercase SHA-256")
        if selected_platform is None or selected_platform == platform_id:
            validated.append(platform_id)

    if ids != set(EXPECTED_VERSIONS):
        fail("client matrix platform set is incomplete")
    if tasks != EXPECTED_TASKS:
        fail("client matrix task coverage is incomplete")
    if mutation_eligible != [("generic_mcp_host", "mcp-2025-11-25")]:
        fail("only the exact generic MCP host may be mutation eligible")
    if selected_platform is not None and selected_platform not in ids:
        fail(f"selected platform is not in the matrix: {selected_platform}")

    return {
        "schemaVersion": "soleaux.client-capability-matrix-validation/v1",
        "matrixSchemaVersion": data["schemaVersion"],
        "matrixSha256": hashlib.sha256(raw).hexdigest(),
        "asOfDate": data["asOfDate"],
        "platforms": validated,
        "platformCount": len(platforms),
        "tasks": sorted(tasks),
        "mutationEligible": [
            {"platform": platform, "version": version} for platform, version in mutation_eligible
        ],
        "publicToolCeiling": data["publicToolCeiling"],
        "productionClaimAllowed": data["productionClaimAllowed"],
        "status": "pass",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--platform", choices=sorted(EXPECTED_VERSIONS))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = validate_matrix(arguments.matrix.resolve(), arguments.platform)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
