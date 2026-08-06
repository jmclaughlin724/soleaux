#!/usr/bin/env python3
"""Binary-level smoke for the Phase 5 client capability matrix and write gate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def run(
    command: list[str], env: dict[str, str], *, expect_success: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if expect_success and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    if not expect_success and completed.returncode == 0:
        raise AssertionError(
            f"command unexpectedly succeeded: {' '.join(command)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def run_json(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    return json.loads(run(command, env).stdout)


def start_daemon(
    daemon: Path,
    endpoint: Path,
    state_db: Path,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            str(daemon),
            "ipc",
            "--endpoint",
            str(endpoint),
            "--state-db",
            str(state_db),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(200):
        if endpoint.exists():
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise RuntimeError(f"soleauxd exited before creating IPC endpoint: {stdout}\n{stderr}")
        time.sleep(0.025)
    process.kill()
    stdout, stderr = process.communicate(timeout=5)
    raise RuntimeError(f"soleauxd did not create IPC endpoint: {stdout}\n{stderr}")


def stop_daemon(cli: Path, process: subprocess.Popen[str], env: dict[str, str]) -> None:
    run_json([str(cli), "service", "stop"], env)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: p5_client_matrix_smoke.py SOLEAUX SOLEAUXD NATIVE_ROOT OUTPUT_JSON"
        )
    cli = Path(sys.argv[1]).resolve()
    daemon = Path(sys.argv[2]).resolve()
    native_root = Path(sys.argv[3]).resolve()
    output = Path(sys.argv[4]).resolve()
    matrix_path = native_root / "contracts" / "client-capability-matrix-v1.json"
    matrix_bytes = matrix_path.read_bytes()
    matrix = json.loads(matrix_bytes)
    matrix_sha256 = hashlib.sha256(matrix_bytes).hexdigest()
    required_signals = next(
        platform["versions"][0]["requiredBinarySignals"]
        for platform in matrix["platforms"]
        if platform["id"] == "generic_mcp_host"
    )
    probe_basis = {
        "matrixSha256": matrix_sha256,
        "passedSignals": required_signals,
        "platform": "generic_mcp_host",
        "version": "mcp-2025-11-25",
    }
    probe = {
        "schemaVersion": "soleaux.client-capability-probe/v1",
        "platform": "generic_mcp_host",
        "clientVersion": "mcp-2025-11-25",
        "matrixSha256": matrix_sha256,
        "status": "pass",
        "mutationEligible": True,
        "passedSignals": required_signals,
        "evidenceSha256": canonical_sha256(probe_basis),
    }

    with tempfile.TemporaryDirectory(prefix="soleaux-p5-client-matrix-") as temporary:
        root = Path(temporary)
        home = root / "home"
        runtime = root / "runtime"
        endpoint = runtime / "soleaux.sock"
        state_db = home / "state" / "canonical.sqlite3"
        workspace_path = root / "workspace"
        workspace_path.mkdir(parents=True)
        env = os.environ.copy()
        env["SOLEAUX_HOME"] = str(home)
        env["SOLEAUX_RUNTIME_DIR"] = str(runtime)
        env["SOLEAUX_INSTALL_BIN"] = str(root / "bin")
        env["XDG_CONFIG_HOME"] = str(root / "config")

        process = start_daemon(daemon, endpoint, state_db, env)
        workspace = run_json(
            [
                str(cli),
                "registry",
                "workspace",
                "register",
                str(workspace_path),
                "--display-name",
                "P5 capability matrix fixture",
                "--trust-state",
                "trusted",
                "--metadata",
                '{"p5ClientMatrix":true}',
            ],
            env,
        )
        workspace_id = workspace["workspace"]["id"]

        generic = run_json(
            [
                str(cli),
                "registry",
                "client",
                "register",
                "--kind",
                "adapter",
                "--instance-id",
                "generic-mcp-host-smoke",
                "--display-name",
                "Generic MCP host smoke",
                "--client-version",
                "mcp-2025-11-25",
                "--capabilities",
                json.dumps({"soleauxProbe": probe}, separators=(",", ":")),
                "--metadata",
                '{"platform":"generic_mcp_host"}',
            ],
            env,
        )
        assert generic["compatibilityState"] == "verified"
        assert generic["writeCapable"] is True
        assert generic["compatibility"]["matrixSha256"] == matrix_sha256
        assert generic["compatibility"]["platform"] == "generic_mcp_host"
        generic_id = generic["client"]["id"]
        generic_binding = run_json(
            [
                str(cli),
                "registry",
                "bind",
                generic_id,
                workspace_id,
                "--access-mode",
                "read_write",
                "--capabilities",
                '{"context":true,"matrixBound":true}',
            ],
            env,
        )
        assert generic_binding["binding"]["payload"]["accessMode"] == "read_write"

        forged_vendor_probe = {
            **probe,
            "platform": "claude_code",
            "clientVersion": "2.1.223",
            "passedSignals": ["version", "help", "mcp"],
        }
        vendor = run_json(
            [
                str(cli),
                "registry",
                "client",
                "register",
                "--kind",
                "adapter",
                "--instance-id",
                "claude-code-smoke",
                "--display-name",
                "Claude Code smoke",
                "--client-version",
                "2.1.223",
                "--capabilities",
                json.dumps({"soleauxProbe": forged_vendor_probe}, separators=(",", ":")),
                "--metadata",
                '{"platform":"claude_code"}',
            ],
            env,
        )
        assert vendor["compatibilityState"] == "unprobed"
        assert vendor["writeCapable"] is False
        assert vendor["compatibility"]["reason"] == "matrix entry is intentionally read-only"
        vendor_id = vendor["client"]["id"]
        rejected = run(
            [
                str(cli),
                "registry",
                "bind",
                vendor_id,
                workspace_id,
                "--access-mode",
                "read_write",
                "--capabilities",
                '{"context":true}',
            ],
            env,
            expect_success=False,
        )
        rejection_text = f"{rejected.stdout}\n{rejected.stderr}".lower()
        assert "read" in rejection_text and (
            "verified" in rejection_text or "write" in rejection_text
        )
        vendor_binding = run_json(
            [
                str(cli),
                "registry",
                "bind",
                vendor_id,
                workspace_id,
                "--access-mode",
                "read_only",
                "--capabilities",
                '{"context":true}',
            ],
            env,
        )
        assert vendor_binding["binding"]["payload"]["accessMode"] == "read_only"

        unknown = run_json(
            [
                str(cli),
                "registry",
                "client",
                "register",
                "--kind",
                "adapter",
                "--instance-id",
                "unknown-generic-smoke",
                "--display-name",
                "Unknown generic host smoke",
                "--client-version",
                "mcp-unknown",
                "--capabilities",
                "{}",
                "--metadata",
                '{"platform":"generic_mcp_host"}',
            ],
            env,
        )
        assert unknown["compatibilityState"] == "unprobed"
        assert unknown["writeCapable"] is False

        status = run_json([str(cli), "registry", "status"], env)
        matrix_status = status["clientCapabilityMatrix"]
        assert matrix_status["schemaVersion"] == "soleaux.client-capability-matrix/v1"
        assert matrix_status["sha256"] == matrix_sha256
        assert len(matrix_status["platforms"]) == 6
        assert matrix_status["publicToolCeiling"] == 12
        assert matrix_status["productionClaimAllowed"] is False
        assert status["productionClaimAllowed"] is False
        stop_daemon(cli, process, env)

        evidence = {
            "schemaVersion": "soleaux.p5-client-capability-matrix-smoke/v1",
            "matrixSha256": matrix_sha256,
            "platformCount": len(matrix["platforms"]),
            "genericHost": {
                "compatibilityState": generic["compatibilityState"],
                "writeCapable": generic["writeCapable"],
                "readWriteBinding": generic_binding["binding"]["payload"]["accessMode"],
                "passedSignals": required_signals,
            },
            "vendorSafeMode": {
                "platform": "claude_code",
                "compatibilityState": vendor["compatibilityState"],
                "writeCapable": vendor["writeCapable"],
                "readWriteRejected": True,
                "readOnlyBinding": vendor_binding["binding"]["payload"]["accessMode"],
            },
            "unknownVersion": {
                "compatibilityState": unknown["compatibilityState"],
                "writeCapable": unknown["writeCapable"],
            },
            "publicToolCeiling": status["publicToolCeiling"],
            "productionClaimAllowed": status["productionClaimAllowed"],
            "status": "pass",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
