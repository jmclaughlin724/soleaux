"""RepositorySnapshotter: request-scoped frozen read sets (D001, D017, D022).

Inventory uses bounded `git ls-files -co --exclude-standard -z` argv when
available and a deterministic ignore-aware walker otherwise; no shell is ever
invoked. Each admissible file is read once, then the read-set hash is rechecked
before finalization; drift yields one bounded retry, then typed
`changed_during_analysis` coverage.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path

from soleaux.contracts.repository import (
    LANGUAGE_REGISTRY,
    RepositoryPath,
    content_digest,
)
from soleaux.contracts.snapshot import CapturedFile, ClaimBasis, RepositorySnapshot
from soleaux.contracts.workspace import RootEscapeError, WorkspaceRoot
from soleaux.postgresql.runtime import build_safe_environment

SNAPSHOT_PRODUCER_ID = "repository-snapshotter"
SNAPSHOT_PRODUCER_VERSION = "1"

BINARY_SNIFF_BYTES = 8192

LANGUAGE_BY_EXTENSION = dict(LANGUAGE_REGISTRY.structural_by_extension())

_FALLBACK_IGNORED_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".turbo",
        ".next",
        "coverage",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


@dataclass(frozen=True)
class SnapshotLimits:
    """Bounding limits for one capture."""

    max_files: int = 4096
    max_bytes: int = 32 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024
    deadline_seconds: float = 10.0


@dataclass(frozen=True)
class SnapshotBundle:
    """The frozen snapshot model plus its captured bytes."""

    snapshot: RepositorySnapshot
    contents: dict[str, bytes]
    notes: tuple[str, ...]

    @cached_property
    def files_by_path(self) -> dict[str, CapturedFile]:
        """Index captured-file identity once for evidence materialization."""
        return {item.path: item for item in self.snapshot.files}


class SnapshotDriftError(Exception):
    """The read set changed during analysis after one bounded retry."""


def snapshot_fingerprint(files: Sequence[CapturedFile]) -> str:
    """Hash one normalized captured-file identity set."""
    hasher = hashlib.sha256()
    for row in sorted(files, key=lambda item: item.path):
        hasher.update(row.path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(row.content_hash.encode("ascii"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _config_digest(limits: SnapshotLimits) -> str:
    payload = json.dumps(limits.__dict__, sort_keys=True)
    return content_digest(payload.encode("utf-8"))


def _newline_kind(content: bytes) -> str:
    sample = content[:BINARY_SNIFF_BYTES]
    if b"\r\n" in sample:
        return "crlf"
    if b"\n" in sample:
        return "lf"
    return "none"


def _is_binary(content: bytes) -> bool:
    return b"\x00" in content[:BINARY_SNIFF_BYTES]


def _end_position(text: str) -> tuple[int, int]:
    lines = text.split("\n")
    return len(lines) - 1, len(lines[-1])


class RepositorySnapshotter:
    """Builds frozen request-scoped snapshots for one authorized root."""

    def __init__(self, workspace: WorkspaceRoot, limits: SnapshotLimits | None = None) -> None:
        self._workspace = workspace
        self._limits = limits or SnapshotLimits()
        self._last_inventory: tuple[str, ...] = ()
        self._inventory_signatures: dict[str, tuple[int, int, int, int, int]] = {}
        self._capture_signatures: dict[str, tuple[int, int, int, int, int]] = {}
        self._capture_stable = True

    @property
    def last_inventory(self) -> tuple[str, ...]:
        """Return the exact candidate inventory used by the last capture."""
        return self._last_inventory

    @property
    def inventory_signatures(self) -> dict[str, tuple[int, int, int, int, int]]:
        """Return cheap identities for all candidates observed by the last capture."""
        return dict(self._inventory_signatures)

    @property
    def capture_signatures(self) -> dict[str, tuple[int, int, int, int, int]]:
        """Return cheap identities for files admitted into the last bundle."""
        return dict(self._capture_signatures)

    async def inventory(self) -> tuple[str, ...]:
        """Return the bounded Git/fallback candidate inventory without reading files."""
        return tuple(await self._inventory(None, None))

    async def capture(
        self,
        scope: tuple[str, ...] | None = None,
        *,
        path_prefixes: tuple[str, ...] | None = None,
    ) -> SnapshotBundle:
        """Capture the eligible read set, with one bounded retry on drift."""
        if scope is not None and path_prefixes is not None:
            raise ValueError("capture accepts either exact scope or path prefixes")
        path_prefixes = path_prefixes or None
        bundle = await self._capture_once(scope, path_prefixes)
        if await self._fingerprint_matches(bundle):
            return bundle
        bundle = await self._capture_once(scope, path_prefixes)
        if await self._fingerprint_matches(bundle):
            return bundle
        snapshot = bundle.snapshot.model_copy(update={"changed_during_analysis": True})
        return SnapshotBundle(
            snapshot=snapshot,
            contents=bundle.contents,
            notes=(*bundle.notes, "read set changed during analysis"),
        )

    async def _capture_once(
        self,
        scope: tuple[str, ...] | None,
        path_prefixes: tuple[str, ...] | None,
    ) -> SnapshotBundle:
        deadline = time.monotonic() + self._limits.deadline_seconds
        candidates = await self._inventory(scope, path_prefixes)
        self._last_inventory = tuple(candidates)
        contents: dict[str, bytes] = {}
        files: list[CapturedFile] = []
        notes: list[str] = []
        inventory_signatures: dict[str, tuple[int, int, int, int, int]] = {}
        signatures: dict[str, tuple[int, int, int, int, int]] = {}
        capture_stable = True
        total_bytes = 0
        for relative in candidates:
            if time.monotonic() > deadline:
                notes.append("deadline exceeded during capture")
                break
            if len(files) >= self._limits.max_files:
                notes.append(f"file count limit {self._limits.max_files} reached")
                break
            try:
                resolved = self._workspace_admit(relative)
            except RootEscapeError:
                notes.append(f"skipped escaping path {relative}")
                continue
            try:
                before = resolved.stat()
                content = resolved.read_bytes()
                after = resolved.stat()
            except FileNotFoundError, IsADirectoryError, PermissionError:
                continue
            before_signature = self._file_signature(before)
            after_signature = self._file_signature(after)
            inventory_signatures[relative] = after_signature
            if len(content) > self._limits.max_file_bytes:
                notes.append(f"skipped oversized file {relative}")
                continue
            if total_bytes + len(content) > self._limits.max_bytes:
                notes.append(f"byte limit {self._limits.max_bytes} reached")
                break
            if _is_binary(content):
                notes.append(f"skipped binary file {relative}")
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                notes.append(f"skipped non-UTF-8 file {relative}")
                continue
            total_bytes += len(content)
            contents[relative] = content
            files.append(self._row(relative, content, text))
            capture_stable = capture_stable and before_signature == after_signature
            signatures[relative] = after_signature
        self._capture_signatures = signatures
        self._inventory_signatures = inventory_signatures
        self._capture_stable = capture_stable
        fingerprint = snapshot_fingerprint(files)
        snapshot = RepositorySnapshot(
            snapshot_id=f"{self._workspace.workspace_id}:{fingerprint[:16]}",
            workspace_id=self._workspace.workspace_id,
            root=str(self._workspace.root),
            created_at=datetime.now(UTC),
            files=tuple(files),
            source_fingerprint=fingerprint,
            changed_during_analysis=False,
        )
        return SnapshotBundle(snapshot=snapshot, contents=contents, notes=tuple(notes))

    def _row(self, relative: str, content: bytes, text: str) -> CapturedFile:
        end_line, end_column = _end_position(text)
        repository_path = RepositoryPath.admit(self._workspace, relative)
        language = LANGUAGE_REGISTRY.detect(repository_path)
        return CapturedFile(
            workspace_id=self._workspace.workspace_id,
            path=repository_path.value,
            content_hash=content_digest(content),
            byte_start=0,
            byte_end=len(content),
            start_line=0,
            start_column=0,
            end_line=end_line,
            end_column=end_column,
            encoding="utf-8",
            newline=_newline_kind(content),
            language=language.structural_language if language is not None else None,
            language_id=language.language_id if language is not None else None,
            parser_id=language.parser_id if language is not None else None,
            producer_id=SNAPSHOT_PRODUCER_ID,
            producer_version=SNAPSHOT_PRODUCER_VERSION,
            producer_config_digest=_config_digest(self._limits),
            # Captured bytes are syntax-plane inputs; the claim-basis enum has no
            # metadata entry, so snapshot rows claim the syntax basis.
            claim_basis=ClaimBasis.SYNTAX,
        )

    async def _fingerprint_matches(self, bundle: SnapshotBundle) -> bool:
        if not self._capture_stable:
            return False
        if set(bundle.contents) != set(self._capture_signatures):
            return False
        for relative in bundle.contents:
            resolved = self._workspace_admit(relative)
            try:
                live_signature = self._file_signature(resolved.stat())
            except FileNotFoundError, IsADirectoryError, PermissionError:
                return False
            if live_signature != self._capture_signatures[relative]:
                return False
        return True

    @staticmethod
    def _file_signature(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )

    def _workspace_admit(self, relative: str) -> Path:
        from soleaux.contracts.workspace import AllowedWorkspaceSet

        return AllowedWorkspaceSet((self._workspace,)).admit(self._workspace, relative)

    async def _inventory(
        self,
        scope: tuple[str, ...] | None,
        path_prefixes: tuple[str, ...] | None,
    ) -> list[str]:
        if scope is not None:
            return sorted(set(scope))
        listed = await self._git_inventory(path_prefixes)
        if listed is not None:
            return listed
        return self._fallback_inventory(path_prefixes)

    async def _git_inventory(
        self,
        path_prefixes: tuple[str, ...] | None,
    ) -> list[str] | None:
        command = [
            "git",
            "--literal-pathspecs",
            "-C",
            str(self._workspace.root),
            "ls-files",
            "-co",
            "--exclude-standard",
            "-z",
        ]
        if path_prefixes is not None:
            command.extend(("--", *path_prefixes))
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=build_safe_environment({}, environment_names=()),
            )
        except OSError:
            return None
        cap = self._limits.max_files * 64 + 4096
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self._limits.deadline_seconds
            )
        except asyncio.CancelledError:
            await _kill_and_reap(proc)
            raise
        except TimeoutError:
            await _kill_and_reap(proc)
            return None
        if proc.returncode != 0:
            return None
        entries = [
            entry.decode("utf-8", errors="surrogateescape")
            for entry in out[:cap].split(b"\x00")
            if entry
        ]
        return sorted(set(entries))

    def _fallback_inventory(
        self,
        path_prefixes: tuple[str, ...] | None,
    ) -> list[str]:
        root = self._workspace.root
        results: set[str] = set()
        stack: list[Path] = []
        if path_prefixes is None:
            stack.append(root)
        else:
            for relative in path_prefixes:
                if any(part in _FALLBACK_IGNORED_NAMES for part in Path(relative).parts):
                    continue
                candidate = self._workspace_admit(relative)
                if candidate.is_dir():
                    stack.append(candidate)
                elif candidate.is_file():
                    results.add(candidate.relative_to(root).as_posix())
        visited: set[Path] = set()
        while stack:
            directory = stack.pop()
            if directory in visited:
                continue
            visited.add(directory)
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except FileNotFoundError, PermissionError:
                continue
            for entry in entries:
                if entry.name in _FALLBACK_IGNORED_NAMES:
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                results.add(os.path.relpath(entry.path, root))
                if len(results) >= self._limits.max_files * 4:
                    return sorted(results)
        return sorted(results)


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Kill one owned inventory process and wait until the child is reaped."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()
