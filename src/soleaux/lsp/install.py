"""Gated language-server installer.

Default OFF. Requires SOLEAUX_AUTO_INSTALL env var AND explicit CLI invocation.
Never invoked from MCP tool calls — installation is a setup-time action.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from soleaux.lsp.providers import (
    BUILTIN_PROVIDERS,
    BuiltinProvider,
    resolve_provider_executable,
)
from soleaux.postgresql.runtime import build_safe_environment


@dataclass(frozen=True)
class InstallResult:
    """Outcome of one install attempt."""

    name: str
    success: bool
    message: str
    command: str | None = None


def is_install_allowed() -> bool:
    """Check whether the SOLEAUX_AUTO_INSTALL gate is open."""
    return bool(os.environ.get("SOLEAUX_AUTO_INSTALL"))


def install_provider(name: str, root: Path) -> InstallResult:
    """Install one built-in provider. Refuses unless the gate is open."""
    if not is_install_allowed():
        return InstallResult(
            name=name,
            success=False,
            message="SOLEAUX_AUTO_INSTALL is not set. Set it to install providers.",
        )

    builtin = next((p for p in BUILTIN_PROVIDERS if p.name == name), None)
    if builtin is None:
        return InstallResult(
            name=name,
            success=False,
            message=f"{name!r} is not a known built-in provider.",
        )

    if builtin.install_command is None:
        return InstallResult(
            name=name,
            success=False,
            message=(
                f"{name!r} has no automated install path. Install manually: {builtin.install_hint}"
            ),
        )

    if _is_installed(builtin, root):
        return InstallResult(
            name=name,
            success=True,
            message=f"{name!r} is already installed.",
        )

    try:
        result = subprocess.run(
            list(builtin.install_command),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=build_safe_environment({}, environment_names=()),
        )
        if result.returncode == 0:
            return InstallResult(
                name=name,
                success=True,
                message=f"Installed {name!r} successfully.",
                command=" ".join(builtin.install_command),
            )
        return InstallResult(
            name=name,
            success=False,
            message=f"Install failed (exit {result.returncode}): {result.stderr[:200]}",
            command=" ".join(builtin.install_command),
        )
    except subprocess.TimeoutExpired:
        return InstallResult(
            name=name,
            success=False,
            message="Install timed out after 120 seconds.",
            command=" ".join(builtin.install_command),
        )


def _is_installed(
    builtin: BuiltinProvider,
    root: Path,
) -> bool:
    """Check if the provider executable is already available."""
    return resolve_provider_executable(builtin.argv, root) is not None
