"""Detect running language-server processes whose CWD matches the workspace."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil

from soleaux.provisioning.contracts import DetectedLspProcess

# (argv substring, language, provider). The substring is the minimum unique
# signal that distinguishes the language server from a generic interpreter.
KNOWN_LSP_ARGV: tuple[tuple[str, str, str], ...] = (
    ("pylance", "python", "pylance"),
    ("pyright-langserver", "python", "pyright"),
    ("python-lsp-server", "python", "python-lsp-server"),
    ("pylsp", "python", "python-lsp-server"),
    ("typescript-language-server", "typescript", "typescript-language-server"),
    ("rust-analyzer", "rust", "rust-analyzer"),
    ("gopls", "go", "gopls"),
    ("bash-language-server", "shell", "bash-language-server"),
    ("deno lsp", "typescript", "deno"),
    ("astro-ls", "astro", "astro-ls"),
    ("prisma-language-server", "prisma", "prisma-language-server"),
    ("yaml-language-server", "yaml", "yaml-language-server"),
    ("postgres-language-server", "sql", "postgres-language-server"),
)


def _match(cmdline: list[str]) -> tuple[str, str] | None:
    if not cmdline:
        return None
    joined = " ".join(cmdline).lower()
    for substring, language, provider in KNOWN_LSP_ARGV:
        if substring in joined:
            return language, provider
    return None


def _in_workspace(cwd: str, root: Path) -> bool:
    if not cwd:
        return False
    try:
        resolved = Path(cwd).resolve(strict=False)
    except OSError, ValueError:
        return False
    root_resolved = root.resolve(strict=False)
    return resolved == root_resolved or root_resolved in resolved.parents


def detect_running_lsps(
    workspace_root: Path,
    *,
    process_iter: Iterable[Any] | None = None,
) -> tuple[tuple[DetectedLspProcess, ...], tuple[str, ...]]:
    """Enumerate running LSPs whose CWD is the workspace or below.

    ``process_iter`` accepts the result of ``psutil.process_iter(attrs=...)``
    or any iterable of Process-like stubs with ``pid`` and ``info`` attributes;
    production callers leave it ``None`` for the real psutil scan.
    """
    iterator: Iterable[Any] = (
        process_iter
        if process_iter is not None
        else psutil.process_iter(attrs=["name", "cmdline", "cwd"])
    )

    detections: list[DetectedLspProcess] = []
    warnings: list[str] = []
    for process in iterator:
        info = getattr(process, "info", {}) or {}
        cmdline: list[str] = list(info.get("cmdline") or [])
        match = _match(cmdline)
        if match is None:
            continue
        language, provider = match
        cwd_value = str(info.get("cwd") or "")
        if not cwd_value:
            warnings.append(f"pid {getattr(process, 'pid', '?')}: cwd unavailable")
            continue
        if not _in_workspace(cwd_value, workspace_root):
            continue
        name = str(info.get("name") or provider)
        detections.append(
            DetectedLspProcess(
                pid=getattr(process, "pid", 0),
                name=name,
                cmdline=tuple(cmdline),
                cwd=cwd_value,
                language=language,
                provider=provider,
            )
        )

    detections.sort(key=lambda item: (item.language, item.pid))
    return tuple(detections), tuple(warnings)
