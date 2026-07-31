"""Tests for the gated LSP provider installer."""

from __future__ import annotations

import os
import pathlib
import subprocess
import unittest.mock

import soleaux.lsp.install


def test_postgres_install_gate_refuses_without_env(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()
    with (
        unittest.mock.patch.dict(os.environ, {}, clear=True),
        unittest.mock.patch("soleaux.lsp.install.subprocess.run") as run,
    ):
        result = soleaux.lsp.install.install_provider("postgres-language-server", tmp_path)
    assert not result.success
    assert "SOLEAUX_AUTO_INSTALL" in result.message
    run.assert_not_called()


def test_gate_refuses_unknown_provider(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()
    with unittest.mock.patch.dict(os.environ, {"SOLEAUX_AUTO_INSTALL": "1"}):
        result = soleaux.lsp.install.install_provider("nonexistent-provider", tmp_path)
    assert not result.success
    assert "not a known built-in" in result.message


def test_is_install_allowed_returns_false_by_default() -> None:
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        assert soleaux.lsp.install.is_install_allowed() is False


def test_is_install_allowed_returns_true_when_set() -> None:
    with unittest.mock.patch.dict(os.environ, {"SOLEAUX_AUTO_INSTALL": "1"}):
        assert soleaux.lsp.install.is_install_allowed() is True


def test_postgres_install_uses_exact_gated_command(tmp_path: pathlib.Path) -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="",
        stderr="",
    )
    with (
        unittest.mock.patch.dict(os.environ, {"SOLEAUX_AUTO_INSTALL": "1"}, clear=True),
        unittest.mock.patch("soleaux.lsp.install.resolve_provider_executable", return_value=None),
        unittest.mock.patch("soleaux.lsp.install.subprocess.run", return_value=completed) as run,
    ):
        result = soleaux.lsp.install.install_provider("postgres-language-server", tmp_path)

    assert result.success
    assert result.command == "npm install -g @postgres-language-server/cli"
    run.assert_called_once_with(
        ["npm", "install", "-g", "@postgres-language-server/cli"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={},
    )


def test_postgres_install_detection_reuses_runtime_resolution(tmp_path: pathlib.Path) -> None:
    executable = tmp_path / "node_modules" / ".bin" / "postgres-language-server"
    with (
        unittest.mock.patch.dict(os.environ, {"SOLEAUX_AUTO_INSTALL": "1"}, clear=True),
        unittest.mock.patch(
            "soleaux.lsp.install.resolve_provider_executable",
            return_value=str(executable),
        ) as resolve,
        unittest.mock.patch("soleaux.lsp.install.subprocess.run") as run,
    ):
        result = soleaux.lsp.install.install_provider("postgres-language-server", tmp_path)

    assert result.success
    assert result.message == "'postgres-language-server' is already installed."
    resolve.assert_called_once_with(
        ("postgres-language-server", "lsp-proxy"),
        tmp_path,
    )
    run.assert_not_called()
