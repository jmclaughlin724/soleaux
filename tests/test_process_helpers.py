"""Subprocess acceptance diagnostics are hermetic, bounded, and secret-safe."""

from __future__ import annotations

import pathlib
import subprocess

import _processes
import pytest


def test_minimum_environment_excludes_unlisted_host_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "host-secret-must-not-propagate")

    environment = _processes.minimum_environment()

    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment


def test_failure_diagnostic_redacts_environment_secret_and_bounds_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    secret = "sentinel-secret-must-never-appear"
    environment = _processes.minimum_environment({"SOLEAUX_TEST_SECRET": secret})
    stdout = f"stdout {secret} " + ("x" * 20_000)
    stderr = f"stderr {secret} " + ("y" * 20_000)
    completed = subprocess.CompletedProcess(("fixture",), 17, stdout, stderr)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AssertionError) as caught:
        _processes.run_checked(
            ("fixture", "--fail"),
            cwd=tmp_path,
            environment=environment,
        )

    diagnostic = str(caught.value)
    assert secret not in diagnostic
    assert "SOLEAUX_TEST_SECRET" not in diagnostic
    assert "status: 17" in diagnostic
    assert "stdout:" in diagnostic
    assert "stderr:" in diagnostic
    assert diagnostic.count("...[truncated]...") == 2
    assert len(diagnostic) < 18_000


def test_failure_diagnostic_redacts_explicit_short_secrets_from_every_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    named_secret = "n7"
    direct_secret = "v4"
    environment = _processes.minimum_environment({"SOLEAUX_TEST_SHORT_SECRET": named_secret})
    completed = subprocess.CompletedProcess(
        ("fixture",),
        19,
        f"stdout {named_secret} {direct_secret}",
        f"stderr {named_secret} {direct_secret}",
    )

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AssertionError) as caught:
        _processes.run_checked(
            ("fixture", "--named", named_secret, "--direct", direct_secret),
            cwd=tmp_path,
            environment=environment,
            secret_names=("SOLEAUX_TEST_SHORT_SECRET",),
            secret_values=(direct_secret,),
        )

    diagnostic = str(caught.value)
    assert named_secret not in diagnostic
    assert direct_secret not in diagnostic
    assert "status: 19" in diagnostic


@pytest.mark.parametrize("failure", ("timeout", "spawn_error"))
def test_failure_diagnostic_redacts_explicit_short_secrets_from_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    failure: str,
) -> None:
    named_secret = "q2"
    direct_secret = "w3"
    command = ("fixture", "--named", named_secret, "--direct", direct_secret)
    environment = _processes.minimum_environment({"SOLEAUX_TEST_SHORT_SECRET": named_secret})

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        detail = f"{named_secret} {direct_secret}"
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                command,
                1,
                output=f"stdout {detail}",
                stderr=f"stderr {detail}",
            )
        raise OSError(f"spawn failed with {detail}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AssertionError) as caught:
        _processes.run_checked(
            command,
            cwd=tmp_path,
            environment=environment,
            secret_names=("SOLEAUX_TEST_SHORT_SECRET",),
            secret_values=(direct_secret,),
        )

    diagnostic = str(caught.value)
    assert named_secret not in diagnostic
    assert direct_secret not in diagnostic
    assert f"status: {failure}" in diagnostic
