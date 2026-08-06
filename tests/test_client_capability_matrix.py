from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "native/contracts/client-capability-matrix-v1.json"
VALIDATOR = ROOT / "native/scripts/validate_client_capability_matrix.py"
PROBE = ROOT / "native/scripts/probe_client_capabilities.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_authoritative_matrix_validates() -> None:
    result = run(str(VALIDATOR), "--matrix", str(MATRIX))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mutationEligible"] == []
    assert report["publicToolCeiling"] == 12
    assert report["productionClaimAllowed"] is False


def test_validator_rejects_task_signal_and_path_drift(tmp_path: Path) -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    matrix["platforms"][0]["task"] = "P5-003"
    path = tmp_path / "wrong-task.json"
    path.write_text(json.dumps(matrix), encoding="utf-8")
    assert run(str(VALIDATOR), "--matrix", str(path)).returncode != 0

    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    generic = next(item for item in matrix["platforms"] if item["id"] == "generic_mcp_host")
    generic["versions"][0]["requiredBinarySignals"].remove("read_only_binding")
    path = tmp_path / "wrong-signals.json"
    path.write_text(json.dumps(matrix), encoding="utf-8")
    assert run(str(VALIDATOR), "--matrix", str(path)).returncode != 0

    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    generic = next(item for item in matrix["platforms"] if item["id"] == "generic_mcp_host")
    generic["sources"][0] = {"type": "native_smoke", "path": "../../etc/passwd"}
    path = tmp_path / "escaped-path.json"
    path.write_text(json.dumps(matrix), encoding="utf-8")
    assert run(str(VALIDATOR), "--matrix", str(path)).returncode != 0


def test_probe_rejects_exit_zero_without_signal_specific_output(tmp_path: Path) -> None:
    binary = tmp_path / "fake-claude"
    binary.write_text("#!/usr/bin/env python3\nprint('2.1.223 (Claude Code)')\n", encoding="utf-8")
    binary.chmod(0o755)
    result = run(
        str(PROBE),
        "--platform",
        "claude_code",
        "--binary",
        str(binary),
        "--output",
        str(tmp_path / "probe.json"),
    )
    assert result.returncode != 0


def test_probe_terminates_output_flood(tmp_path: Path) -> None:
    binary = tmp_path / "flood-claude"
    binary.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stdout.write('x' * (2 * 1024 * 1024))\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    result = run(
        str(PROBE),
        "--platform",
        "claude_code",
        "--binary",
        str(binary),
        "--output",
        str(tmp_path / "probe.json"),
    )
    assert result.returncode != 0


def test_probe_normalizes_ansi_and_case_for_opencode(tmp_path: Path) -> None:
    binary = tmp_path / "fake-opencode"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        "    print('1.18.14')\n"
        "elif args == ['serve', '--help']:\n"
        "    print('STARTS A HEADLESS OPENCODE SERVER', file=sys.stderr)\n"
        "else:\n"
        "    print('\\x1b[36mCOMMANDS:\\x1b[0m opencode serve', file=sys.stderr)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    output = tmp_path / "opencode-probe.json"
    result = run(
        str(PROBE),
        "--platform",
        "opencode",
        "--binary",
        str(binary),
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
