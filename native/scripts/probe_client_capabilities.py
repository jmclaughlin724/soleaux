#!/usr/bin/env python3
"""Produce bounded, matrix-bound capability evidence for a supported client surface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "native" / "contracts" / "client-capability-matrix-v1.json"
MAX_CAPTURE = 16_384
VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")


def fail(message: str) -> None:
    raise SystemExit(message)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_entry(path: Path, platform_id: str) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    matrix = json.loads(raw)
    platform = next((item for item in matrix["platforms"] if item["id"] == platform_id), None)
    if platform is None:
        fail(f"platform is not in the matrix: {platform_id}")
    versions = platform.get("versions", [])
    if len(versions) != 1:
        fail(f"platform must contain one matrix entry: {platform_id}")
    return raw, platform, versions[0]


def resolve_binary(binary: str) -> str:
    candidate = Path(binary)
    if candidate.is_file():
        return str(candidate.resolve())
    resolved = shutil.which(binary)
    if resolved is None:
        fail(f"client binary was not found: {binary}")
    return resolved


def run_signal(binary: str, argv: list[str]) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update({"CI": "1", "NO_COLOR": "1", "TERM": "dumb"})
    completed = subprocess.run(
        [binary, *argv],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    stdout = completed.stdout[:MAX_CAPTURE]
    stderr = completed.stderr[:MAX_CAPTURE]
    return {
        "argv": [Path(binary).name, *argv],
        "exitCode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdoutSha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderrSha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "truncated": len(completed.stdout) > MAX_CAPTURE or len(completed.stderr) > MAX_CAPTURE,
        "passed": completed.returncode == 0,
    }


def observed_version(
    matrix_version: str, version_policy: str, evidence: dict[str, dict[str, Any]]
) -> str:
    version_signal = evidence.get("version")
    combined = ""
    if version_signal:
        combined = f"{version_signal['stdout']}\n{version_signal['stderr']}"
    match = VERSION_PATTERN.search(combined)
    observed = match.group(1) if match else ""
    if version_policy == "exact":
        if matrix_version not in combined:
            fail(
                "binary version did not match exact matrix entry "
                f"{matrix_version}: {combined[:500]}"
            )
        return matrix_version
    if version_policy == "runtime_observed_read_only":
        if not observed:
            fail("runtime-observed client did not report a semantic version")
        return observed
    return matrix_version


def build_report(arguments: argparse.Namespace) -> dict[str, Any]:
    raw, platform, version = load_entry(arguments.matrix, arguments.platform)
    required = list(version.get("requiredBinarySignals", []))
    commands = dict(version.get("binaryCommands", {}))
    command_evidence: dict[str, dict[str, Any]] = {}

    if arguments.documentation_only:
        if required:
            fail("documentation-only probes cannot skip required binary signals")
        client_version = str(version["version"])
    else:
        if not arguments.binary:
            fail("--binary is required for a binary probe")
        binary = resolve_binary(arguments.binary)
        for signal in required:
            argv = commands.get(signal)
            if not isinstance(argv, list) or not argv:
                fail(f"matrix does not define argv for required signal {signal}")
            command_evidence[signal] = run_signal(binary, [str(item) for item in argv])
        client_version = observed_version(
            str(version["version"]), str(platform["versionPolicy"]), command_evidence
        )

    passed_signals = sorted(
        signal for signal, result in command_evidence.items() if result["passed"]
    )
    failed_signals = sorted(set(required) - set(passed_signals))
    status = "pass" if not failed_signals else "fail"
    mutation_eligible = bool(version.get("mutationEligible")) and arguments.allow_mutation
    if arguments.allow_mutation and not version.get("mutationEligible"):
        fail("the selected matrix entry is not mutation eligible")

    probe = {
        "schemaVersion": "soleaux.client-capability-probe/v1",
        "platform": platform["id"],
        "clientVersion": client_version,
        "matrixVersion": version["version"],
        "matrixSha256": hashlib.sha256(raw).hexdigest(),
        "status": status,
        "mutationEligible": mutation_eligible,
        "passedSignals": passed_signals,
        "failedSignals": failed_signals,
        "probeMode": platform["probeMode"],
        "versionPolicy": platform["versionPolicy"],
        "clientKind": platform["clientKind"],
        "documentedCapabilities": platform["capabilities"],
        "sources": platform["sources"],
        "commands": command_evidence,
        "productionClaimAllowed": False,
    }
    probe["evidenceSha256"] = canonical_sha256(probe)
    return {
        "schemaVersion": "soleaux.client-capability-probe-report/v1",
        "task": platform["task"],
        "platform": platform["id"],
        "clientVersion": client_version,
        "matrixVersion": version["version"],
        "matrixSha256": probe["matrixSha256"],
        "probe": probe,
        "registration": {
            "clientKind": platform["clientKind"],
            "clientVersion": client_version,
            "capabilities": {"soleauxProbe": probe},
            "metadata": {"platform": platform["id"]},
        },
        "writeCapable": mutation_eligible and status == "pass",
        "productionClaimAllowed": False,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    parser.add_argument("--binary")
    parser.add_argument("--documentation-only", action="store_true")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.matrix = arguments.matrix.resolve()
    report = build_report(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
