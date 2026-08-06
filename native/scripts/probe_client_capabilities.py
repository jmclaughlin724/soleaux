#!/usr/bin/env python3
"""Produce bounded, matrix-bound capability evidence for a supported client surface."""

from __future__ import annotations

import contextlib

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "native" / "contracts" / "client-capability-matrix-v1.json"
MAX_CAPTURE = 16_384
MAX_STREAM_BYTES = 1_048_576
PROBE_TIMEOUT_SECONDS = 45
REGISTRATION_CAPABILITIES_MAX_BYTES = 2_048
VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SIGNAL_EXPECTATIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "claude_code": {
        "version": ("2.1.223", "Claude Code"),
        "help": ("Usage: claude", "Claude Code"),
        "mcp": ("Usage: claude mcp", "Configure and manage MCP servers"),
    },
    "codex": {
        "version": ("codex-cli", "0.146.1"),
        "help": ("Codex CLI", "app-server"),
        "app_server": ("Run the app server", "generate-json-schema"),
    },
    "opencode": {
        "version": ("1.18.14",),
        "help": ("Commands:", "opencode serve"),
        "serve": ("starts a headless opencode server",),
    },
}


def fail(message: str) -> None:
    raise SystemExit(message)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compact_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


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


def _capture_stream(
    stream: BinaryIO,
    process: subprocess.Popen[bytes],
    state: dict[str, Any],
) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            state["bytes"] += len(chunk)
            state["sha256"].update(chunk)
            remaining = MAX_CAPTURE - len(state["prefix"])
            if remaining > 0:
                state["prefix"].extend(chunk[:remaining])
            if state["bytes"] > MAX_STREAM_BYTES:
                state["limitExceeded"] = True
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                break
    finally:
        stream.close()


def run_signal(binary: str, argv: list[str]) -> dict[str, Any]:
    environment = os.environ.copy()
    environment.update({"CI": "1", "NO_COLOR": "1", "TERM": "dumb"})
    process = subprocess.Popen(
        [binary, *argv],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        fail("client probe could not capture stdout and stderr")

    stdout_state: dict[str, Any] = {
        "bytes": 0,
        "prefix": bytearray(),
        "sha256": hashlib.sha256(),
        "limitExceeded": False,
    }
    stderr_state: dict[str, Any] = {
        "bytes": 0,
        "prefix": bytearray(),
        "sha256": hashlib.sha256(),
        "limitExceeded": False,
    }
    threads = [
        threading.Thread(
            target=_capture_stream,
            args=(process.stdout, process, stdout_state),
            daemon=True,
        ),
        threading.Thread(
            target=_capture_stream,
            args=(process.stderr, process, stderr_state),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        process.wait(timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()

    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        process.kill()
        fail("client probe output readers did not terminate")

    output_limit_exceeded = bool(
        stdout_state["limitExceeded"] or stderr_state["limitExceeded"]
    )
    return {
        "argv": [Path(binary).name, *argv],
        "exitCode": process.returncode,
        "stdout": bytes(stdout_state["prefix"]).decode("utf-8", errors="replace"),
        "stderr": bytes(stderr_state["prefix"]).decode("utf-8", errors="replace"),
        "stdoutBytes": stdout_state["bytes"],
        "stderrBytes": stderr_state["bytes"],
        "stdoutSha256": stdout_state["sha256"].hexdigest(),
        "stderrSha256": stderr_state["sha256"].hexdigest(),
        "truncated": (
            stdout_state["bytes"] > len(stdout_state["prefix"])
            or stderr_state["bytes"] > len(stderr_state["prefix"])
        ),
        "outputLimitExceeded": output_limit_exceeded,
        "timedOut": timed_out,
        "passed": process.returncode == 0 and not timed_out and not output_limit_exceeded,
    }


def validate_signal(platform: str, signal: str, result: dict[str, Any]) -> None:
    expected = SIGNAL_EXPECTATIONS.get(platform, {}).get(signal)
    if expected is None:
        fail(f"no independent output assertion is registered for {platform}.{signal}")
    combined = ANSI_PATTERN.sub(
        "", f"{result['stdout']}\n{result['stderr']}"
    ).casefold()
    missing = [token for token in expected if token.casefold() not in combined]
    result["expectedOutputTokens"] = list(expected)
    result["missingOutputTokens"] = missing
    result["passed"] = bool(result["passed"] and not missing)


def observed_version(
    matrix_version: str,
    version_policy: str,
    evidence: dict[str, dict[str, Any]],
) -> str:
    version_signal = evidence.get("version")
    combined = ""
    if version_signal:
        combined = f"{version_signal['stdout']}\n{version_signal['stderr']}"
    match = VERSION_PATTERN.search(combined)
    observed = match.group(1) if match else ""
    if version_policy == "exact":
        if observed != matrix_version:
            fail(
                "binary version did not match exact matrix entry "
                f"{matrix_version}; observed={observed or '<missing>'}: {combined[:500]}"
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
            result = run_signal(binary, [str(item) for item in argv])
            validate_signal(str(platform["id"]), signal, result)
            command_evidence[signal] = result
        client_version = observed_version(
            str(version["version"]),
            str(platform["versionPolicy"]),
            command_evidence,
        )

    passed_signals = sorted(
        signal for signal, result in command_evidence.items() if result["passed"]
    )
    failed_signals = sorted(set(required) - set(passed_signals))
    status = "pass" if not failed_signals else "fail"
    if arguments.allow_mutation:
        fail("external client probes are evidence-only and cannot authorize mutation")

    matrix_sha256 = hashlib.sha256(raw).hexdigest()
    admission_probe = {
        "schemaVersion": "soleaux.client-capability-probe/v1",
        "platform": platform["id"],
        "clientVersion": client_version,
        "matrixSha256": matrix_sha256,
        "status": status,
        "mutationEligible": False,
        "passedSignals": passed_signals,
    }
    admission_probe["evidenceSha256"] = canonical_sha256(admission_probe)

    archival_probe = {
        **admission_probe,
        "failedSignals": failed_signals,
        "probeMode": platform["probeMode"],
        "versionPolicy": platform["versionPolicy"],
        "clientKind": platform["clientKind"],
        "documentedCapabilities": platform["capabilities"],
        "sources": platform["sources"],
        "commands": command_evidence,
        "admissionEvidenceSha256": admission_probe["evidenceSha256"],
        "productionClaimAllowed": False,
    }
    archival_probe["evidenceSha256"] = canonical_sha256(archival_probe)

    registration_capabilities = {"soleauxProbe": admission_probe}
    registration_capabilities_bytes = compact_size(registration_capabilities)
    if registration_capabilities_bytes > REGISTRATION_CAPABILITIES_MAX_BYTES:
        fail(
            "registration capability proof exceeds the admission budget: "
            f"{registration_capabilities_bytes} > {REGISTRATION_CAPABILITIES_MAX_BYTES}"
        )

    return {
        "schemaVersion": "soleaux.client-capability-probe-report/v1",
        "task": platform["task"],
        "platform": platform["id"],
        "clientVersion": client_version,
        "matrixVersion": version["version"],
        "matrixSha256": matrix_sha256,
        "probe": archival_probe,
        "admissionProbe": admission_probe,
        "registrationCapabilitiesBytes": registration_capabilities_bytes,
        "registrationCapabilitiesMaximumBytes": REGISTRATION_CAPABILITIES_MAX_BYTES,
        "registration": {
            "clientKind": platform["clientKind"],
            "clientVersion": client_version,
            "capabilities": registration_capabilities,
            "metadata": {"platform": platform["id"]},
        },
        "writeCapable": False,
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
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
