"""Runtime resolver for build and installation identity.

Wheels carry a generated `soleaux/resources/build_identity.json` produced by the
hatch build hook in `scripts/build_identity_hook.py`. Editable installs resolve
the same fields from the source tree at runtime.
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import importlib.util
import json
import subprocess
import sys
import tomllib
import typing
from pathlib import Path
from typing import Any

_WHEEL_IDENTITY_PATH = "resources/build_identity.json"
_GIT_SHA_EXCEPTIONS: tuple[type[BaseException], ...] = (
    subprocess.CalledProcessError,
    FileNotFoundError,
    subprocess.TimeoutExpired,
)


def _package_directory() -> Path | None:
    spec = importlib.util.find_spec("soleaux")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(spec.submodule_search_locations[0])


def _version() -> str:
    try:
        return importlib.metadata.version("soleaux")
    except importlib.metadata.PackageNotFoundError:
        pass

    try:
        package_dir = _package_directory()
        if package_dir is None:
            raise RuntimeError("soleaux package directory not found")
        # In an editable install the package lives under src/soleaux.
        pyproject = package_dir.parent.parent / "pyproject.toml"
        if pyproject.is_file():
            manifest = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(manifest["project"]["version"])
    except Exception:
        pass

    return "unknown"


def _git_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except _GIT_SHA_EXCEPTIONS:
        return None
    sha = result.stdout.strip()
    return sha if sha else None


def _wheel_identity() -> dict[str, Any] | None:
    try:
        path = importlib.resources.files("soleaux").joinpath(_WHEEL_IDENTITY_PATH)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return typing.cast(dict[str, Any], payload)
    except Exception:
        return None


def _python_identity() -> dict[str, object]:
    return {
        "version_info": sys.version_info[:2],
        "version": sys.version,
    }


def resolve_build_identity() -> dict[str, Any]:
    """Return the best-effort build/install identity for the running process.

    Never raises. The returned dictionary always contains ``version``,
    ``git_sha``, ``install_source`` (``"wheel"`` or ``"editable"``), and
    ``python``.
    """
    version = _version()
    python = _python_identity()

    wheel = _wheel_identity()
    if wheel is not None:
        return {
            "version": wheel.get("version", version),
            "git_sha": wheel.get("git_sha"),
            "install_source": "wheel",
            "python": python,
        }

    root: Path | None = None
    try:
        package_dir = _package_directory()
        if package_dir is not None:
            candidate = package_dir.parent.parent
            if (candidate / ".git").is_dir() or (candidate / "pyproject.toml").is_file():
                root = candidate
    except Exception:
        pass

    git_sha = _git_sha(root) if root is not None else None
    return {
        "version": version,
        "git_sha": git_sha,
        "install_source": "editable",
        "python": python,
    }
