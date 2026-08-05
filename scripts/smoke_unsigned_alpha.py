#!/usr/bin/env python3
"""Exercise the extracted unsigned alpha through its complete operational lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import Any


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_json(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: pathlib.Path,
    logs: pathlib.Path,
    name: str,
    timeout: int = 180,
) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    write(logs / f"{name}.stdout", result.stdout)
    write(logs / f"{name}.stderr", result.stderr)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{name} failed with {result.returncode}: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{name} did not return JSON: {result.stdout!r}") from error
    write(logs / f"{name}.json", json.dumps(value, indent=2, sort_keys=True) + "\n")
    return value


def wait_for_endpoint(endpoint: pathlib.Path, process: subprocess.Popen[str]) -> None:
    for _ in range(1000):
        if endpoint.exists():
            return
        if process.poll() is not None:
            raise RuntimeError(f"daemon exited before creating endpoint: {process.returncode}")
        time.sleep(0.01)
    raise RuntimeError("daemon endpoint did not appear")


def launch_daemon(
    daemon: pathlib.Path,
    endpoint: pathlib.Path,
    state_db: pathlib.Path,
    *,
    env: dict[str, str],
    cwd: pathlib.Path,
    logs: pathlib.Path,
    suffix: str,
) -> subprocess.Popen[str]:
    stdout = (logs / f"daemon-{suffix}.stdout").open("w", encoding="utf-8")
    stderr = (logs / f"daemon-{suffix}.stderr").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(daemon), "ipc", "--endpoint", str(endpoint), "--state-db", str(state_db)],
        cwd=cwd,
        env=env,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )
    wait_for_endpoint(endpoint, process)
    return process


def stop_daemon(
    cli: pathlib.Path,
    process: subprocess.Popen[str],
    endpoint: pathlib.Path,
    *,
    env: dict[str, str],
    cwd: pathlib.Path,
    logs: pathlib.Path,
    suffix: str,
) -> dict[str, Any]:
    value = run_json(
        [str(cli), "service", "stop"],
        env=env,
        cwd=cwd,
        logs=logs,
        name=f"service-stop-{suffix}",
    )
    try:
        code = process.wait(timeout=20)
    except subprocess.TimeoutExpired as error:
        process.kill()
        code = process.wait(timeout=10)
        raise RuntimeError(
            f"daemon did not stop after graceful IPC shutdown; killed with {code}"
        ) from error
    if code != 0:
        raise RuntimeError(f"daemon exited with {code} after stop")
    if endpoint.exists():
        raise RuntimeError("daemon endpoint remained after stop")
    return value


def execute(args: argparse.Namespace) -> dict[str, Any]:
    package = args.package_root.resolve()
    workspace = args.workspace.resolve()
    root = args.root.resolve()
    logs = args.logs.resolve()
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(logs, ignore_errors=True)
    for path in (
        root / "home",
        root / "state",
        root / "runtime",
        root / "config",
        root / "bin",
        logs,
    ):
        path.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_RUNTIME_DIR": str(root / "runtime"),
            "SOLEAUX_HOME": str(root / "state"),
            "SOLEAUX_RUNTIME_DIR": str(root / "runtime" / "soleaux"),
            "SOLEAUX_INSTALL_BIN": str(root / "bin"),
        }
    )
    cli = root / "bin" / "soleaux"
    daemon = root / "bin" / "soleauxd"
    endpoint = root / "runtime" / "soleaux" / "soleaux.sock"
    state_db = root / "state" / "state" / "canonical.sqlite3"
    manifest = root / "config" / "systemd" / "user" / "soleaux.service"
    process: subprocess.Popen[str] | None = None
    stage = "install"
    try:
        install = run_json(
            [str(package / "install.sh")],
            env=env,
            cwd=workspace,
            logs=logs,
            name="install",
        )
        for required in (cli, daemon, manifest):
            if not required.is_file():
                raise RuntimeError(f"installation omitted {required}")

        stage = "first daemon launch"
        process = launch_daemon(
            daemon,
            endpoint,
            state_db,
            env=env,
            cwd=workspace,
            logs=logs,
            suffix="first",
        )
        status_before = run_json(
            [str(cli), "service", "status"],
            env=env,
            cwd=workspace,
            logs=logs,
            name="service-status-before",
        )
        stage = "doctor"
        doctor = run_json(
            [str(cli), "doctor", str(workspace), "--json"],
            env=env,
            cwd=workspace,
            logs=logs,
            name="doctor",
            timeout=300,
        )
        stage = "backup/export/repair"
        backup_path = logs / "canonical.backup.sqlite3"
        export_path = logs / "canonical.export.json"
        backup = run_json(
            [str(cli), "backup", str(backup_path)],
            env=env,
            cwd=workspace,
            logs=logs,
            name="backup",
        )
        export = run_json(
            [str(cli), "export", str(export_path)],
            env=env,
            cwd=workspace,
            logs=logs,
            name="export",
        )
        repair = run_json(
            [str(cli), "repair"],
            env=env,
            cwd=workspace,
            logs=logs,
            name="repair",
        )
        stage = "first daemon stop"
        stop_first = stop_daemon(
            cli,
            process,
            endpoint,
            env=env,
            cwd=workspace,
            logs=logs,
            suffix="first",
        )
        process = None

        stage = "daemon restart"
        process = launch_daemon(
            daemon,
            endpoint,
            state_db,
            env=env,
            cwd=workspace,
            logs=logs,
            suffix="second",
        )
        status_after = run_json(
            [str(cli), "service", "status"],
            env=env,
            cwd=workspace,
            logs=logs,
            name="service-status-after-restart",
        )
        stop_second = stop_daemon(
            cli,
            process,
            endpoint,
            env=env,
            cwd=workspace,
            logs=logs,
            suffix="second",
        )
        process = None

        stage = "offline restore"
        restore = run_json(
            [str(cli), "restore", str(backup_path)],
            env=env,
            cwd=workspace,
            logs=logs,
            name="restore",
        )
        stage = "uninstall"
        uninstall = run_json(
            [str(package / "uninstall.sh")],
            env=env,
            cwd=workspace,
            logs=logs,
            name="uninstall",
        )
        report = uninstall.get("uninstall")
        if not isinstance(report, dict):
            raise RuntimeError("uninstall response omitted the typed uninstall report")
        if manifest.exists() or cli.exists() or daemon.exists():
            raise RuntimeError("uninstall left installed service or binary files")
        if not pathlib.Path(env["SOLEAUX_HOME"]).is_dir():
            raise RuntimeError("uninstall removed state despite preserve-state=true")
        if status_before.get("running") is not True or status_after.get("running") is not True:
            raise RuntimeError("daemon did not report running before and after restart")
        if repair.get("integrity") != "ok" or repair.get("foreignKeyViolations") != 0:
            raise RuntimeError("repair did not prove canonical database integrity")
        if repair.get("auditChainValid") is not True:
            raise RuntimeError("repair did not prove the canonical audit chain")
        for key in ("preservedState", "removedManifest", "removedCli", "removedDaemon"):
            if report.get(key) is not True:
                raise RuntimeError(f"uninstall report did not prove {key}")

        result = {
            "schemaVersion": "soleaux.phase4-alpha-operations/v1",
            "installedFromExtractedArchive": True,
            "daemonRestarted": True,
            "doctorPassed": bool(doctor),
            "backupPassed": bool(backup),
            "exportPassed": bool(export),
            "repairPassed": True,
            "restorePassed": bool(restore),
            "uninstallPassed": True,
            "statePreserved": True,
            "firstStop": stop_first,
            "secondStop": stop_second,
            "install": install,
            "productionClaimAllowed": False,
            "status": "pass",
        }
        write(logs / "summary.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as error:
        failure = {
            "schemaVersion": "soleaux.phase4-alpha-operations/v1",
            "stage": stage,
            "error": str(error),
            "productionClaimAllowed": False,
            "status": "failure",
        }
        write(logs / "failure.json", json.dumps(failure, indent=2, sort_keys=True) + "\n")
        raise
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=pathlib.Path, required=True)
    parser.add_argument("--workspace", type=pathlib.Path, required=True)
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--logs", type=pathlib.Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    execute(parse_args())