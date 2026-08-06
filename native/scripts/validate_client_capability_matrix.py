#!/usr/bin/env python3
"""Fail-closed validation for the Phase 5 client capability matrix."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
EXPECTED_TASK_BY_PLATFORM = {
    "claude_code": "P5-002",
    "claude_desktop": "P5-003",
    "codex": "P5-004",
    "opencode": "P5-005",
    "cursor": "P5-006",
    "generic_mcp_host": "P5-006",
}
GENERIC_REQUIRED_SIGNALS = [
    "initialize",
    "tools_list",
    "context_compile",
    "registry_registration",
    "read_only_binding",
    "tool_ceiling",
]
EXPECTED_PACKAGE_ARTIFACTS = {
    "claude_code": {
        "package": "@anthropic-ai/claude-code",
        "version": "2.1.223",
        "tarball": "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-2.1.223.tgz",
        "shasum": "a05d73e01e71d5a68708d30421b9945b41a15641",
        "integrity": (
            "sha512-t6la5i6TP8p/zf6QlZjwzpMKM3kCty6aGRv6opeOnMVnJRcVuchPWSPgcdZZ"
            "dvE82Alk4xbJ83XQXGOj6F4mlA=="
        ),
    },
    "codex": {
        "package": "@openai/codex",
        "version": "0.146.1",
        "tarball": "https://registry.npmjs.org/@openai/codex/-/codex-0.146.1.tgz",
        "shasum": "016c49faa4bfa60801f3a5949ae42c4d4e095411",
        "integrity": (
            "sha512-f51R56E/G15soLhf5l5pWUiM+mGHK0NdLozOtzjRoAa+bA20hgWrkyxE/fpw"
            "CnuGQM6XNdktHYtK9xQ7bPIbTA=="
        ),
    },
}
EXPECTED_OPENCODE_ASSET = {
    "url": (
        "https://github.com/anomalyco/opencode/releases/download/v1.18.14/"
        "opencode-linux-x64.tar.gz"
    ),
    "sha256": "f23980ba2aebfbfa53948e55e213d3f2a53740fd7326553828e89ad27e970572",
}
ALLOWED_OFFICIAL_HOSTS = {
    "code.claude.com",
    "support.claude.com",
    "registry.npmjs.org",
    "github.com",
    "opencode.ai",
    "cursor.com",
}
EXPECTED_EXTERNAL_ADMISSION = "disabled_until_daemon_trusted_receipt_verifier"


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
    if data.get("externalRuntimeWriteAdmission") != EXPECTED_EXTERNAL_ADMISSION:
        fail("external runtime write admission policy drifted")
    if data.get("publicToolCeiling") != 12:
        fail("public MCP ceiling drifted")
    if data.get("productionClaimAllowed") is not False:
        fail("client matrix cannot enable a production claim")

    platforms = require_list(data.get("platforms"), "platforms")
    if len(platforms) != 6:
        fail("client matrix must contain exactly six platform records")
    ids: set[str] = set()
    validated: list[str] = []
    for raw_platform in platforms:
        platform = require_object(raw_platform, "platform")
        platform_id = str(platform.get("id", ""))
        if platform_id not in EXPECTED_VERSIONS:
            fail(f"unexpected client platform: {platform_id}")
        if platform_id in ids:
            fail(f"duplicate client platform: {platform_id}")
        ids.add(platform_id)
        if platform.get("task") != EXPECTED_TASK_BY_PLATFORM[platform_id]:
            fail(f"{platform_id} phase task drifted")
        if platform.get("clientKind") != EXPECTED_KINDS[platform_id]:
            fail(f"{platform_id} client kind drifted")
        if not require_object(platform.get("capabilities"), f"{platform_id}.capabilities"):
            fail(f"{platform_id} capabilities are empty")
        sources = require_list(platform.get("sources"), f"{platform_id}.sources")
        if not sources:
            fail(f"{platform_id} has no sources")
        for source in sources:
            source_object = require_object(source, f"{platform_id}.source")
            validate_source(source_object, platform_id)
            if platform.get("versionPolicy") == "exact" and str(
                source_object.get("url", "")
            ).endswith("/latest"):
                fail(f"{platform_id} exact-version evidence cannot use a mutable latest URL")

        versions = require_list(platform.get("versions"), f"{platform_id}.versions")
        if len(versions) != 1:
            fail(f"{platform_id} must have one exact observed matrix entry")
        version = require_object(versions[0], f"{platform_id}.version")
        if version.get("version") != EXPECTED_VERSIONS[platform_id]:
            fail(f"{platform_id} version drifted")
        required = [
            str(value)
            for value in require_list(
                version.get("requiredBinarySignals"),
                f"{platform_id}.requiredBinarySignals",
            )
        ]
        if len(required) != len(set(required)):
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
        if version.get("mutationEligible") is not False:
            fail(
                f"{platform_id} cannot be mutation eligible without a "
                "daemon-trusted admission receipt verifier"
            )
        if platform_id == "generic_mcp_host" and required != GENERIC_REQUIRED_SIGNALS:
            fail("generic MCP host required signal set drifted")
        if platform_id in EXPECTED_PACKAGE_ARTIFACTS:
            artifact = require_object(
                version.get("packageArtifact"), f"{platform_id}.packageArtifact"
            )
            if artifact != EXPECTED_PACKAGE_ARTIFACTS[platform_id]:
                fail(f"{platform_id} package artifact provenance drifted")
            source_urls = {str(source.get("url", "")) for source in sources}
            metadata_url = (
                "https://registry.npmjs.org/"
                f"{artifact['package']}/{artifact['version']}"
            )
            if metadata_url not in source_urls or artifact["tarball"] not in source_urls:
                fail(f"{platform_id} exact package sources are incomplete")
        if platform_id == "opencode":
            asset = require_object(version.get("linuxX64Asset"), "opencode.linuxX64Asset")
            if asset != EXPECTED_OPENCODE_ASSET:
                fail("OpenCode asset is not pinned to the exact approved release")
        if selected_platform is None or selected_platform == platform_id:
            validated.append(platform_id)

    if ids != set(EXPECTED_VERSIONS):
        fail("client matrix platform set is incomplete")
    if selected_platform is not None and selected_platform not in ids:
        fail(f"selected platform is not in the matrix: {selected_platform}")

    return {
        "schemaVersion": "soleaux.client-capability-matrix-validation/v1",
        "matrixSchemaVersion": data["schemaVersion"],
        "matrixSha256": hashlib.sha256(raw).hexdigest(),
        "asOfDate": data["asOfDate"],
        "platforms": validated,
        "platformCount": len(platforms),
        "tasks": sorted(set(EXPECTED_TASK_BY_PLATFORM.values())),
        "mutationEligible": [],
        "externalRuntimeWriteAdmission": data["externalRuntimeWriteAdmission"],
        "publicToolCeiling": data["publicToolCeiling"],
        "productionClaimAllowed": data["productionClaimAllowed"],
        "status": "pass",
    }


def expect_failure(data: dict[str, Any], label: str) -> None:
    with tempfile.TemporaryDirectory(prefix="soleaux-matrix-validator-") as temporary:
        path = Path(temporary) / "matrix.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        try:
            validate_matrix(path, None)
        except SystemExit:
            return
    fail(f"validator self-test unexpectedly accepted {label}")


def run_self_tests(path: Path) -> dict[str, Any]:
    data = require_object(json.loads(path.read_bytes()), "matrix")

    traversal = copy.deepcopy(data)
    traversal["platforms"][-1]["sources"][0] = {"path": "../../etc/passwd"}
    expect_failure(traversal, "repository path traversal")

    task_swap = copy.deepcopy(data)
    task_swap["platforms"][0]["task"] = "P5-003"
    expect_failure(task_swap, "platform task reassignment")

    weakened_signals = copy.deepcopy(data)
    weakened_signals["platforms"][-1]["versions"][0]["requiredBinarySignals"].pop()
    expect_failure(weakened_signals, "weakened generic signal set")

    mutation = copy.deepcopy(data)
    mutation["platforms"][-1]["versions"][0]["mutationEligible"] = True
    expect_failure(mutation, "external mutation eligibility")

    mutable_source = copy.deepcopy(data)
    mutable_source["platforms"][0]["sources"][-1]["url"] = (
        "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"
    )
    expect_failure(mutable_source, "mutable latest source")

    return {
        "schemaVersion": "soleaux.client-capability-matrix-validator-self-test/v1",
        "pathTraversalRejected": True,
        "taskReassignmentRejected": True,
        "weakenedSignalSetRejected": True,
        "externalMutationRejected": True,
        "mutableSourceRejected": True,
        "status": "pass",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--platform", choices=sorted(EXPECTED_VERSIONS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    matrix = arguments.matrix.resolve()
    result = run_self_tests(matrix) if arguments.self_test else validate_matrix(matrix, arguments.platform)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
