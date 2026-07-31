"""Hermetic subprocess helpers for Soleaux acceptance tests."""

from __future__ import annotations

import collections.abc
import os
import pathlib
import shlex
import shutil
import subprocess
import typing

from soleaux.postgresql.runtime import (
    SAFE_BASELINE_ENVIRONMENT_NAMES,
    capture_inherited_environment,
    redact_text,
)

_TEST_INHERITED_ENVIRONMENT_NAMES: typing.Final = (
    *SAFE_BASELINE_ENVIRONMENT_NAMES,
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
)
_MAX_COMMAND_CHARACTERS: typing.Final = 2_048
_MAX_STREAM_CHARACTERS: typing.Final = 8_192
_MINIMUM_REDACTION_LENGTH: typing.Final = 8


def minimum_environment(
    additions: collections.abc.Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the portable process baseline plus explicitly supplied values."""
    environment = capture_inherited_environment(_TEST_INHERITED_ENVIRONMENT_NAMES)
    environment.update(
        {
            "FASTMCP_MCP_CAMELCASE_COMPAT": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if additions is not None:
        environment.update(additions)
    return environment


def required_executable(name: str) -> pathlib.Path:
    """Resolve one declared test executable to a stable absolute identity."""
    path = shutil.which(name, path=minimum_environment().get("PATH"))
    if path is None:
        raise AssertionError(f"required test executable is unavailable: {name}")
    return pathlib.Path(path).resolve(strict=True)


def run_checked(
    command: collections.abc.Sequence[str | os.PathLike[str]],
    *,
    cwd: pathlib.Path,
    environment: collections.abc.Mapping[str, str],
    timeout: float = 120,
    expected_returncode: int = 0,
    secret_names: collections.abc.Collection[str] = (),
    secret_values: collections.abc.Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Run one child and raise a bounded, environment-redacted diagnostic."""
    argv = tuple(os.fspath(token) for token in command)
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(environment),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostic = _failure_diagnostic(
            argv,
            status="timeout",
            stdout=_stream_text(exc.stdout),
            stderr=_stream_text(exc.stderr),
            environment=environment,
            secret_names=secret_names,
            secret_values=secret_values,
        )
        raise AssertionError(diagnostic) from None
    except OSError as exc:
        diagnostic = _failure_diagnostic(
            argv,
            status="spawn_error",
            stdout="",
            stderr=str(exc),
            environment=environment,
            secret_names=secret_names,
            secret_values=secret_values,
        )
        raise AssertionError(diagnostic) from None
    if result.returncode != expected_returncode:
        diagnostic = _failure_diagnostic(
            argv,
            status=str(result.returncode),
            stdout=result.stdout,
            stderr=result.stderr,
            environment=environment,
            secret_names=secret_names,
            secret_values=secret_values,
        )
        raise AssertionError(diagnostic)
    return result


def _failure_diagnostic(
    argv: tuple[str, ...],
    *,
    status: str,
    stdout: str,
    stderr: str,
    environment: collections.abc.Mapping[str, str],
    secret_names: collections.abc.Collection[str],
    secret_values: collections.abc.Sequence[str],
) -> str:
    explicit_values = {value for name in secret_names if (value := environment.get(name))}
    explicit_values.update(value for value in secret_values if value)
    heuristic_values = {
        value for value in environment.values() if len(value) >= _MINIMUM_REDACTION_LENGTH
    }
    redactions = tuple(
        sorted(
            explicit_values | heuristic_values,
            key=lambda value: (-len(value), value),
        )
    )
    command = redact_text(shlex.join(argv), redactions)
    safe_stdout = redact_text(stdout, redactions)
    safe_stderr = redact_text(stderr, redactions)
    return "\n".join(
        (
            "subprocess result did not match the expected status",
            f"command: {_bounded(command, _MAX_COMMAND_CHARACTERS)}",
            f"status: {status}",
            f"stdout: {_bounded(safe_stdout, _MAX_STREAM_CHARACTERS)}",
            f"stderr: {_bounded(safe_stderr, _MAX_STREAM_CHARACTERS)}",
        )
    )


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    retained = (limit - len("\n...[truncated]...\n")) // 2
    return f"{value[:retained]}\n...[truncated]...\n{value[-retained:]}"


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
