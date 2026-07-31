"""The module CLI exposes version output and a default stdio profile."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import _processes
import pytest

import soleaux.server
from soleaux.analysis.service import product_version
from soleaux.cli import main


def _environment() -> dict[str, str]:
    return _processes.minimum_environment()


def test_module_cli_reports_the_package_version(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "soleaux", "--version"],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"soleaux {product_version()}"
    assert result.stderr == ""


def test_default_stdio_profile_exits_cleanly_on_eof(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "soleaux"],
        cwd=tmp_path,
        env=_environment(),
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0


def test_bare_invocation_delegates_once_to_the_factory_with_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = Mock()
    factory = Mock(return_value=server)
    monkeypatch.setattr(soleaux.server, "create_server", factory)

    main(["--root", str(tmp_path)])

    factory.assert_called_once_with(tmp_path)
    server.run.assert_called_once_with()
