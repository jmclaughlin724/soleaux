"""Explicit provisioning for the Rust ast-grep worker binary.

The Rust engine is never built at startup. `soleaux install ast-grep-rust`
builds the pinned cargo workspace with `--locked` and copies the release
binary into the managed cache path that `StructuralEngines` resolves.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import soleaux.postgresql.runtime
from soleaux.structural.engines import RUST_WORKER_NAME, managed_rust_binary_path
from soleaux.structural.fragments import AST_GREP_VERSION

_BUILD_TIMEOUT_SECONDS = 600.0


class RustWorkerError(Exception):
    """The Rust worker could not be provisioned."""


@dataclass(frozen=True, slots=True)
class RustWorkerInstallation:
    """One provisioned Rust worker binary at the exact pinned version."""

    version: str
    binary_path: Path


def rust_workspace_manifest() -> Path | None:
    """Locate the Cargo workspace shipped by an artifact or source checkout."""
    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[1] / "resources" / "structural" / "rust" / "Cargo.toml",
        module_path.parents[3] / "rust" / "Cargo.toml",
    )
    return next((manifest for manifest in candidates if manifest.is_file()), None)


def provision_rust_worker(
    *,
    cargo_executable: str = "cargo",
    timeout_seconds: float = _BUILD_TIMEOUT_SECONDS,
) -> RustWorkerInstallation:
    """Build the pinned worker with ``cargo build --release --locked`` and install it."""
    manifest = rust_workspace_manifest()
    if manifest is None:
        raise RustWorkerError(
            "this Soleaux installation does not contain the packaged Rust worker "
            "workspace; reinstall Soleaux and retry."
        )
    if shutil.which(cargo_executable) is None:
        raise RustWorkerError(
            f"{cargo_executable!r} is not on PATH; install a Rust toolchain "
            "(https://rustup.rs) and retry."
        )
    destination = managed_rust_binary_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    build_directory = destination.parent / "build"
    try:
        completed = subprocess.run(
            [
                cargo_executable,
                "build",
                "--release",
                "--locked",
                "--manifest-path",
                str(manifest),
                "--target-dir",
                str(build_directory),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=soleaux.postgresql.runtime.build_safe_environment(
                {},
                environment_names=(),
            ),
        )
    except OSError:
        raise RustWorkerError(f"{cargo_executable!r} could not start") from None
    except subprocess.TimeoutExpired as exc:
        raise RustWorkerError(
            f"cargo build exceeded {timeout_seconds:.0f}s for {manifest}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RustWorkerError(f"cargo build failed for {manifest}: {detail}")

    built = build_directory / "release" / RUST_WORKER_NAME
    if not built.is_file():
        raise RustWorkerError(f"cargo build produced no binary at {built}")
    shutil.copy2(built, destination)
    destination.chmod(0o755)
    return RustWorkerInstallation(version=AST_GREP_VERSION, binary_path=destination)
