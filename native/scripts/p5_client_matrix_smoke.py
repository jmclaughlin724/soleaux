#!/usr/bin/env python3
"""Binary-level smoke for the Phase 5 client capability matrix and safe-mode gate."""

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
    value = json.loads(run(command, env).stdout)
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object from {' '.join(command)}")
    return value


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
    generic = next(
        platform for platform in matrix["platforms"] if platform["id"] == "generic_mcp_host"
    )
    required_signals = generic["versions"][0]["requiredBinarySignals"]
    assert generic["versions"][0]["mutationEligible"] is False
    forged_probe = {
        "schemaVersion": "soleaux.client-capability-probe/v1",
        "platform": "generic_mcp_host",
        "clientVersion": "mcp-2025-11-25",
        "matrixSha256": matrix_sha256,
        "status": "pass",
        "mutationEligible": True,
        "passedSignals": required_signals,
        "evidenceSha256": "a" * 64,
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
        try:
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

            external = run_json(
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
                    json.dumps({"soleauxProbe": forged_probe}, separators=(",", ":")),
                    "--metadata",
                    '{"platform":"generic_mcp_host"}',
                ],
                env,
            )
            assert external["compatibilityState"] == "unprobed"
            assert external["writeCapable"] is False
            assert "daemon-trusted" in external["compatibility"]["reason"]
            external_id = external["client"]["id"]

            rejected = run(
                [
                    str(cli),
                    "registry",
                    "bind",
                    external_id,
                    workspace_id,
                    "--access-mode",
                    "read_write",
                    "--capabilities",
                    '{"context":true,"forgedProbe":true}',
                ],
                env,
                expect_success=False,
            )
            rejection_text = f"{rejected.stdout}\n{rejected.stderr}".lower()
            assert "daemon-trusted" in rejection_text or "read-write" in rejection_text

            external_binding = run_json(
                [
                    str(cli),
                    "registry",
                    "bind",
                    external_id,
                    workspace_id,
                    "--access-mode",
                    "read_only",
                    "--capabilities",
                    '{"context":true}',
                ],
                env,
            )
            assert external_binding["binding"]["payload"]["accessMode"] == "read_only"

            heartbeat_probe = {**forged_probe, "evidenceSha256": "b" * 64}
            heartbeat = run_json(
                [
                    str(cli),
                    "registry",
                    "client",
                    "heartbeat",
                    external_id,
                    "--ttl-ms",
                    "300000",
                    "--capabilities",
                    json.dumps({"soleauxProbe": heartbeat_probe}, separators=(",", ":")),
                ],
                env,
            )
            assert heartbeat["compatibilityState"] == "unprobed"
            assert heartbeat["writeCapable"] is False
            assert heartbeat["bindings"][0]["payload"]["accessMode"] == "read_only"

            internal = run_json(
                [
                    str(cli),
                    "registry",
                    "client",
                    "register",
                    "--kind",
                    "cli",
                    "--instance-id",
                    "soleaux-internal-smoke",
                    "--display-name",
                    "Soleaux internal CLI smoke",
                    "--client-version",
                    "0.4.0-dev.5",
                    "--capabilities",
                    '{"internal":true}',
                    "--metadata",
                    '{"platform":"soleaux_cli"}',
                ],
                env,
            )
            assert internal["compatibilityState"] == "verified"
            assert internal["writeCapable"] is True
            internal_id = internal["client"]["id"]
            internal_binding = run_json(
                [
                    str(cli),
                    "registry",
                    "bind",
                    internal_id,
                    workspace_id,
                    "--access-mode",
                    "read_write",
                    "--capabilities",
                    '{"context":true,"internal":true}',
                ],
                env,
            )
            assert internal_binding["binding"]["payload"]["accessMode"] == "read_write"

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
            assert matrix_status["writeEligible"] == []
            assert matrix_status["publicToolCeiling"] == 12
            assert matrix_status["productionClaimAllowed"] is False
            assert status["productionClaimAllowed"] is False
        finally:
            if process.poll() is None:
                stop_daemon(cli, process, env)

        evidence = {
            "schemaVersion": "soleaux.p5-client-capability-matrix-smoke/v1",
            "matrixSha256": matrix_sha256,
            "platformCount": len(matrix["platforms"]),
            "externalGenericHost": {
                "compatibilityState": external["compatibilityState"],
                "writeCapable": external["writeCapable"],
                "forgedReadWriteRejected": True,
                "readOnlyBinding": external_binding["binding"]["payload"]["accessMode"],
                "heartbeatRevalidated": heartbeat["compatibilityState"],
                "requiredSignals": required_signals,
            },
            "internalCli": {
                "compatibilityState": internal["compatibilityState"],
                "writeCapable": internal["writeCapable"],
                "readWriteBinding": internal_binding["binding"]["payload"]["accessMode"],
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
