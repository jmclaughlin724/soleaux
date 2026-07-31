"""Descriptor-confined provisioning I/O, backup manifests, and rollback."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pydantic

from soleaux.provisioning.contracts import BackupRecord

_BACKUP_DIR = ".soleaux-backups"
_MANIFEST = "manifest.json"
_TS_FORMAT = "%Y%m%dT%H%M%SZ"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def _is_backup_storage_name(value: str) -> bool:
    return value.casefold() == _BACKUP_DIR.casefold()


class ProvisioningPathError(ValueError):
    """A provisioning path is not a contained, symlink-free workspace path."""


class BackupManifestError(ValueError):
    """The backup manifest is malformed or references unusable files."""


class _BackupManifest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    backups: tuple[BackupRecord, ...]


@dataclass(frozen=True)
class AdmittedPath:
    """One lexical workspace-relative path admitted by ``WorkspaceIo``."""

    relative: Path
    role: str

    @property
    def as_posix(self) -> str:
        return self.relative.as_posix()


@dataclass(frozen=True)
class FileSnapshot:
    data: bytes
    mode: int


@dataclass(frozen=True)
class _PreparedBackup:
    source: AdmittedPath
    destination: AdmittedPath
    record: BackupRecord


@dataclass(frozen=True)
class _PreparedRestore:
    source: AdmittedPath
    destination: AdmittedPath
    original_path: str


class WorkspaceIo:
    """Own all provisioning filesystem access beneath one opened workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self.requested_root = workspace_root
        self.root = self._resolve_workspace(workspace_root)
        self._root_fd = os.open(self.root, _DIRECTORY_FLAGS)

    def __enter__(self) -> WorkspaceIo:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    @classmethod
    def for_target(cls, target: Path) -> tuple[WorkspaceIo, AdmittedPath]:
        """Anchor a standalone writer at the nearest real, non-symlink ancestor."""
        if any(_is_backup_storage_name(part) for part in target.parts):
            raise ProvisioningPathError(
                "provisioning target path must not target Soleaux backup storage"
            )
        root = target.parent
        while root.is_symlink() or not root.exists():
            parent = root.parent
            if parent == root:
                raise ProvisioningPathError(f"cannot resolve a safe root for {target}")
            root = parent
        workspace_io = cls(root)
        try:
            admitted = workspace_io.admit_target(target, role="provisioning target path")
        except BaseException:
            workspace_io.close()
            raise
        return workspace_io, admitted

    @staticmethod
    def _resolve_workspace(workspace_root: Path) -> Path:
        if "\0" in str(workspace_root):
            raise ProvisioningPathError("workspace path must not contain NUL bytes")
        try:
            resolved = workspace_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProvisioningPathError(
                f"workspace path cannot be resolved: {workspace_root}"
            ) from exc
        if not resolved.is_dir():
            raise ProvisioningPathError(f"workspace path is not a directory: {workspace_root}")
        return resolved

    @staticmethod
    def _validate_relative(relative: Path, *, role: str) -> Path:
        if "\0" in str(relative):
            raise ProvisioningPathError(f"{role} must not contain NUL bytes")
        if relative.is_absolute() or not relative.parts:
            raise ProvisioningPathError(f"{role} must be a workspace-relative file path")
        if ".." in relative.parts:
            raise ProvisioningPathError(f"{role} must stay inside the workspace")
        return relative

    def admit(self, candidate: Path, *, role: str) -> AdmittedPath:
        if "\0" in str(candidate):
            raise ProvisioningPathError(f"{role} must not contain NUL bytes")
        lexical_root = (
            self.requested_root
            if self.requested_root.is_absolute()
            else Path.cwd() / self.requested_root
        )
        lexical_candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
        relative: Path | None = None
        for base in (lexical_root, self.root):
            try:
                relative = lexical_candidate.relative_to(base)
            except ValueError:
                continue
            break
        if relative is None:
            raise ProvisioningPathError(f"{role} must stay inside the workspace")
        return self.admit_relative(relative, role=role)

    def admit_relative(self, relative: str | Path, *, role: str) -> AdmittedPath:
        admitted = AdmittedPath(self._validate_relative(Path(relative), role=role), role)
        self._preflight(admitted)
        return admitted

    def admit_target(self, candidate: Path, *, role: str) -> AdmittedPath:
        admitted = self.admit(candidate, role=role)
        self._reject_backup_storage_target(admitted)
        return admitted

    def admit_relative_target(self, relative: str | Path, *, role: str) -> AdmittedPath:
        admitted = self.admit_relative(relative, role=role)
        self._reject_backup_storage_target(admitted)
        return admitted

    @staticmethod
    def _reject_backup_storage_target(path: AdmittedPath) -> None:
        if _is_backup_storage_name(path.relative.parts[0]):
            raise ProvisioningPathError(f"{path.role} must not target Soleaux backup storage")

    def absolute(self, path: AdmittedPath) -> Path:
        """Return a display-only path; filesystem operations stay descriptor-relative."""
        return self.root / path.relative

    def _preflight(self, path: AdmittedPath) -> None:
        parent_fd = self._open_parent(path, create=False, missing_ok=True)
        if parent_fd is None:
            return
        try:
            leaf_stat = self._leaf_stat(parent_fd, path.relative.name)
            if leaf_stat is not None and stat.S_ISLNK(leaf_stat.st_mode):
                raise ProvisioningPathError(
                    f"{path.role} must not traverse a symlink: {self.absolute(path)}"
                )
        finally:
            os.close(parent_fd)

    def _open_parent(
        self,
        path: AdmittedPath,
        *,
        create: bool,
        missing_ok: bool = False,
    ) -> int | None:
        current_fd = os.dup(self._root_fd)
        try:
            for index, component in enumerate(path.relative.parts[:-1]):
                while True:
                    try:
                        component_stat = os.stat(
                            component,
                            dir_fd=current_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        if not create:
                            if missing_ok:
                                os.close(current_fd)
                                return None
                            raise
                        directory_mode = (
                            0o700 if index == 0 and _is_backup_storage_name(component) else 0o777
                        )
                        with contextlib.suppress(FileExistsError):
                            os.mkdir(component, directory_mode, dir_fd=current_fd)
                        continue
                    if stat.S_ISLNK(component_stat.st_mode):
                        raise ProvisioningPathError(
                            f"{path.role} must not traverse a symlink: {component}"
                        )
                    if not stat.S_ISDIR(component_stat.st_mode):
                        raise ProvisioningPathError(
                            f"{path.role} has a non-directory ancestor: {component}"
                        )
                    try:
                        next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
                    except OSError as exc:
                        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                            raise ProvisioningPathError(
                                f"{path.role} must not traverse a symlink: {component}"
                            ) from exc
                        raise
                    os.close(current_fd)
                    current_fd = next_fd
                    break
            return current_fd
        except BaseException:
            os.close(current_fd)
            raise

    @staticmethod
    def _leaf_stat(parent_fd: int, leaf: str) -> os.stat_result | None:
        try:
            return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def exists(self, path: AdmittedPath) -> bool:
        parent_fd = self._open_parent(path, create=False, missing_ok=True)
        if parent_fd is None:
            return False
        try:
            leaf_stat = self._leaf_stat(parent_fd, path.relative.name)
            if leaf_stat is not None and stat.S_ISLNK(leaf_stat.st_mode):
                raise ProvisioningPathError(
                    f"{path.role} must not traverse a symlink: {self.absolute(path)}"
                )
            return leaf_stat is not None
        finally:
            os.close(parent_fd)

    def is_file(self, path: AdmittedPath) -> bool:
        parent_fd = self._open_parent(path, create=False, missing_ok=True)
        if parent_fd is None:
            return False
        try:
            leaf_stat = self._leaf_stat(parent_fd, path.relative.name)
            if leaf_stat is not None and stat.S_ISLNK(leaf_stat.st_mode):
                raise ProvisioningPathError(
                    f"{path.role} must not traverse a symlink: {self.absolute(path)}"
                )
            return leaf_stat is not None and stat.S_ISREG(leaf_stat.st_mode)
        finally:
            os.close(parent_fd)

    def read_file(self, path: AdmittedPath) -> FileSnapshot:
        parent_fd = self._open_parent(path, create=False)
        assert parent_fd is not None
        descriptor = -1
        try:
            try:
                descriptor = os.open(path.relative.name, _READ_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ProvisioningPathError(
                        f"{path.role} must not traverse a symlink: {self.absolute(path)}"
                    ) from exc
                raise
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ProvisioningPathError(f"{path.role} is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                data = stream.read()
            return FileSnapshot(data=data, mode=stat.S_IMODE(file_stat.st_mode))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent_fd)

    def read_optional(self, path: AdmittedPath) -> FileSnapshot | None:
        try:
            return self.read_file(path)
        except FileNotFoundError:
            return None

    @staticmethod
    def _staging_name() -> str:
        return f".soleaux-write-{secrets.token_hex(12)}"

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("short write while provisioning")
            remaining = remaining[written:]
        os.fsync(descriptor)

    def _stage_bytes(
        self,
        parent_fd: int,
        data: bytes,
        *,
        mode: int,
        exact_mode: bool,
    ) -> str:
        for _attempt in range(32):
            staging = self._staging_name()
            try:
                descriptor = os.open(staging, _WRITE_FLAGS, mode, dir_fd=parent_fd)
            except FileExistsError:
                continue
            try:
                self._write_all(descriptor, data)
                if exact_mode:
                    os.fchmod(descriptor, mode)
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return staging
        raise FileExistsError("could not reserve an atomic provisioning staging file")

    def write_bytes_atomic(
        self,
        path: AdmittedPath,
        data: bytes,
        *,
        mode: int | None = None,
    ) -> None:
        parent_fd = self._open_parent(path, create=True)
        assert parent_fd is not None
        staging: str | None = None
        try:
            leaf_stat = self._leaf_stat(parent_fd, path.relative.name)
            if leaf_stat is not None:
                if stat.S_ISLNK(leaf_stat.st_mode):
                    raise ProvisioningPathError(
                        f"{path.role} must not traverse a symlink: {self.absolute(path)}"
                    )
                if not stat.S_ISREG(leaf_stat.st_mode):
                    raise ProvisioningPathError(f"{path.role} is not a regular file")
            write_mode = mode
            if write_mode is None:
                write_mode = stat.S_IMODE(leaf_stat.st_mode) if leaf_stat is not None else 0o666
            staging = self._stage_bytes(
                parent_fd,
                data,
                mode=write_mode,
                exact_mode=leaf_stat is not None or mode is not None,
            )
            replacement_stat = self._leaf_stat(parent_fd, path.relative.name)
            if replacement_stat is not None and stat.S_ISLNK(replacement_stat.st_mode):
                raise ProvisioningPathError(
                    f"{path.role} changed to a symlink before write: {self.absolute(path)}"
                )
            os.replace(
                staging,
                path.relative.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            staging = None
            os.fsync(parent_fd)
        finally:
            if staging is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(staging, dir_fd=parent_fd)
            os.close(parent_fd)

    def write_bytes_exclusive(
        self,
        path: AdmittedPath,
        data: bytes,
        *,
        mode: int,
    ) -> None:
        parent_fd = self._open_parent(path, create=True)
        assert parent_fd is not None
        staging: str | None = None
        try:
            leaf_stat = self._leaf_stat(parent_fd, path.relative.name)
            if leaf_stat is not None and stat.S_ISLNK(leaf_stat.st_mode):
                raise ProvisioningPathError(
                    f"{path.role} changed to a symlink before write: {self.absolute(path)}"
                )
            if leaf_stat is not None:
                raise FileExistsError(f"provisioning destination already exists: {path.as_posix}")
            staging = self._stage_bytes(
                parent_fd,
                data,
                mode=mode,
                exact_mode=True,
            )
            os.link(
                staging,
                path.relative.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(staging, dir_fd=parent_fd)
            staging = None
            os.fsync(parent_fd)
        finally:
            if staging is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(staging, dir_fd=parent_fd)
            os.close(parent_fd)


def _timestamp() -> str:
    return datetime.now(tz=UTC).strftime(_TS_FORMAT)


def _manifest_path(workspace_io: WorkspaceIo) -> AdmittedPath:
    return workspace_io.admit_relative(
        Path(_BACKUP_DIR) / _MANIFEST,
        role="backup manifest path",
    )


def _read_manifest(workspace_io: WorkspaceIo) -> _BackupManifest:
    snapshot = workspace_io.read_optional(_manifest_path(workspace_io))
    if snapshot is None:
        return _BackupManifest(backups=())
    try:
        return _BackupManifest.model_validate_json(snapshot.data, strict=True)
    except (pydantic.ValidationError, UnicodeDecodeError) as exc:
        raise BackupManifestError("backup manifest is malformed; no files were changed") from exc


def _backup_record_paths(
    workspace_io: WorkspaceIo,
    record: BackupRecord,
) -> tuple[AdmittedPath, AdmittedPath]:
    original = workspace_io.admit_relative_target(
        record.original_path,
        role="manifest original path",
    )
    backup_path = workspace_io.admit_relative(
        record.backup_path,
        role="manifest backup path",
    )
    if backup_path.relative.parts[0] != _BACKUP_DIR:
        raise BackupManifestError("manifest backup path must stay inside the backup directory")
    return original, backup_path


def _validated_restores(
    workspace_io: WorkspaceIo,
    manifest: _BackupManifest,
) -> list[_PreparedRestore]:
    prepared: list[_PreparedRestore] = []
    for record in reversed(manifest.backups):
        original, backup_path = _backup_record_paths(workspace_io, record)
        if not workspace_io.is_file(backup_path):
            raise BackupManifestError(f"manifest backup file is missing: {record.backup_path}")
        prepared.append(
            _PreparedRestore(
                source=backup_path,
                destination=original,
                original_path=record.original_path,
            )
        )
    return prepared


def _write_manifest(workspace_io: WorkspaceIo, manifest: _BackupManifest) -> None:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    workspace_io.write_bytes_atomic(
        _manifest_path(workspace_io),
        payload,
        mode=0o600,
    )


def _flatten(relative_path: str, timestamp: str) -> str:
    """``.vscode/settings.json`` -> ``.vscode__settings.json.<ts>``."""
    return f"{relative_path.replace('/', '__').replace('\\', '__')}.{timestamp}"


def _prepare_backup(
    workspace_io: WorkspaceIo,
    source: AdmittedPath,
    *,
    reserved: set[Path],
) -> _PreparedBackup:
    if not workspace_io.is_file(source):
        raise ProvisioningPathError(f"backup source path is not a file: {source.as_posix}")
    timestamp = _timestamp()
    filename = _flatten(source.as_posix, timestamp)
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f".{sequence}"
        destination = workspace_io.admit_relative(
            Path(_BACKUP_DIR) / f"{filename}{suffix}",
            role="backup destination path",
        )
        if destination.relative not in reserved and not workspace_io.exists(destination):
            break
        sequence += 1
    reserved.add(destination.relative)
    record = BackupRecord(
        original_path=source.as_posix,
        backup_path=destination.as_posix,
        timestamp=timestamp,
    )
    return _PreparedBackup(source=source, destination=destination, record=record)


def _backup_files(
    workspace_io: WorkspaceIo,
    targets: list[AdmittedPath],
) -> list[BackupRecord]:
    """Validate all history and inputs before creating any backup file."""
    if not targets:
        return []
    manifest = _read_manifest(workspace_io)
    _validated_restores(workspace_io, manifest)
    reserved: set[Path] = set()
    prepared = [_prepare_backup(workspace_io, target, reserved=reserved) for target in targets]
    snapshots = [(item, workspace_io.read_file(item.source)) for item in prepared]
    for item, snapshot in snapshots:
        workspace_io.write_bytes_exclusive(
            item.destination,
            snapshot.data,
            mode=snapshot.mode,
        )
    records = [item.record for item in prepared]
    updated = _BackupManifest(backups=(*manifest.backups, *records))
    _write_manifest(workspace_io, updated)
    return records


def backup_file(workspace_root: Path, target: Path) -> BackupRecord:
    """Copy one contained, symlink-free file into the backup directory."""
    with WorkspaceIo(workspace_root) as workspace_io:
        source = workspace_io.admit_target(target, role="backup source path")
        return _backup_files(workspace_io, [source])[0]


def restore(workspace_root: Path) -> list[str]:
    """Strictly validate the entire manifest, then restore it in reverse order."""
    with WorkspaceIo(workspace_root) as workspace_io:
        prepared = _validated_restores(workspace_io, _read_manifest(workspace_io))
        snapshots = [(item, workspace_io.read_file(item.source)) for item in prepared]
        for item, snapshot in snapshots:
            workspace_io.write_bytes_atomic(
                item.destination,
                snapshot.data,
                mode=snapshot.mode,
            )
        return [item.original_path for item, _snapshot in snapshots]
