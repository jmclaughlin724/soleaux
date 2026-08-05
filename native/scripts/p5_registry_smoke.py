#!/usr/bin/env python3
"""Binary-level smoke for the Phase 5 per-user workspace/client registry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def run_json(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


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
        raise SystemExit("usage: p5_registry_smoke.py SOLEAUX SOLEAUXD REPOSITORY OUTPUT_JSON")
    cli = Path(sys.argv[1]).resolve()
    daemon = Path(sys.argv[2]).resolve()
    repository = Path(sys.argv[3]).resolve()
    output = Path(sys.argv[4]).resolve()

    with tempfile.TemporaryDirectory(prefix="soleaux-p5-registry-") as temporary:
        root = Path(temporary)
        home = root / "home"
        runtime = root / "runtime"
        endpoint = runtime / "soleaux.sock"
        state_db = home / "state" / "canonical.sqlite3"
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
                str(repository),
                "--display-name",
                "Soleaux source",
                "--trust-state",
                "trusted",
                "--metadata",
                '{"smoke":true}',
            ],
            env,
        )
        workspace_id = workspace["workspace"]["id"]

        client_ids: list[str] = []
        for kind in ("cli", "desktop", "editor", "adapter"):
            client = run_json(
                [
                    str(cli),
                    "registry",
                    "client",
                    "register",
                    "--kind",
                    kind,
                    "--instance-id",
                    f"smoke-{kind}",
                    "--display-name",
                    f"Smoke {kind}",
                    "--client-version",
                    "1.0.0-smoke",
                    "--ttl-ms",
                    "300000",
                    "--capabilities",
                    '{"registry":true}',
                ],
                env,
            )
            client_id = client["client"]["id"]
            client_ids.append(client_id)
            run_json(
                [
                    str(cli),
                    "registry",
                    "bind",
                    client_id,
                    workspace_id,
                    "--access-mode",
                    "read_write",
                    "--capabilities",
                    '{"context":true}',
                ],
                env,
            )

        first_status = run_json([str(cli), "registry", "status"], env)
        assert first_status["schemaVersion"] == "soleaux.workspace-registry/v1"
        assert len(first_status["workspaces"]) == 1
        assert len(first_status["clients"]) == 4
        assert len(first_status["bindings"]) == 4
        assert first_status["publicToolCeiling"] == 12
        assert first_status["productionClaimAllowed"] is False

        stop_daemon(cli, process, env)
        process = start_daemon(daemon, endpoint, state_db, env)
        persisted_status = run_json([str(cli), "registry", "status"], env)
        assert len(persisted_status["workspaces"]) == 1
        assert len(persisted_status["clients"]) == 4
        assert len(persisted_status["bindings"]) == 4

        heartbeat = run_json(
            [
                str(cli),
                "registry",
                "client",
                "heartbeat",
                client_ids[0],
                "--ttl-ms",
                "300000",
                "--capabilities",
                '{"registry":true,"heartbeat":true}',
            ],
            env,
        )
        assert heartbeat["client"]["revision"] >= 2
        stop_daemon(cli, process, env)

        evidence = {
            "schemaVersion": "soleaux.p5-registry-smoke/v1",
            "workspaceId": workspace_id,
            "clientIds": client_ids,
            "clientKinds": ["cli", "desktop", "editor", "adapter"],
            "initial": {
                "workspaces": len(first_status["workspaces"]),
                "clients": len(first_status["clients"]),
                "bindings": len(first_status["bindings"]),
            },
            "afterRestart": {
                "workspaces": len(persisted_status["workspaces"]),
                "clients": len(persisted_status["clients"]),
                "bindings": len(persisted_status["bindings"]),
            },
            "heartbeatRevision": heartbeat["client"]["revision"],
            "publicToolCeiling": first_status["publicToolCeiling"],
            "productionClaimAllowed": first_status["productionClaimAllowed"],
            "status": "pass",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
