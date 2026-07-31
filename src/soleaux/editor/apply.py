"""Conflict-safe per-file atomic application of stored editor previews."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import pathlib
import stat
import tempfile
import typing

import soleaux.contracts.repository
import soleaux.editor.contracts
import soleaux.editor.preview


class ReplaceFile(typing.Protocol):
    """The dirfd-relative atomic replacement operation used by apply."""

    def __call__(
        self,
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None: ...


@dataclasses.dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int


@dataclasses.dataclass(frozen=True, slots=True)
class _WorkspaceBinding:
    root_path: pathlib.Path
    descriptor: int
    identity: _FileIdentity


@dataclasses.dataclass(frozen=True, slots=True)
class _DirectoryBinding:
    descriptor: int
    relative_parts: tuple[str, ...]
    identity: _FileIdentity


@dataclasses.dataclass(frozen=True, slots=True)
class _TargetBinding:
    item: soleaux.editor.preview.NormalizedFileEdit
    directory: _DirectoryBinding
    name: str
    preimage_identity: _FileIdentity
    mode: int


@dataclasses.dataclass(frozen=True, slots=True)
class _StagedFile:
    name: str
    identity: _FileIdentity


@dataclasses.dataclass(frozen=True, slots=True)
class _StagedImages:
    postimage: _StagedFile
    preimage: _StagedFile


@dataclasses.dataclass(frozen=True, slots=True)
class _AppliedTarget:
    target: _TargetBinding
    staged: _StagedImages


class _CommitAncestryConflict(soleaux.editor.preview.EditorPreviewError):
    """A target ancestry binding changed during commit."""


class _RollbackAncestryConflict(soleaux.editor.preview.EditorPreviewError):
    """A target ancestry binding changed during rollback."""


async def apply_stored_preview(
    record: soleaux.editor.preview.StoredPreview,
    *,
    replace_file: ReplaceFile = os.replace,
) -> soleaux.editor.contracts.ApplyPayload:
    """Apply one claimed preview, rolling back only exact task-written postimages."""
    preflight_error = _preflight(record)
    if preflight_error is not None:
        return _conflicted(record, preflight_error)

    try:
        _require_secure_directory_operations()
        with contextlib.ExitStack() as descriptors:
            workspace = _bind_workspace(record.root, descriptors)
            targets = _bind_targets(record, workspace, descriptors)
            with tempfile.TemporaryDirectory(
                prefix=".soleaux-apply-",
                dir=workspace.root_path.parent,
                ignore_cleanup_errors=True,
            ) as directory:
                staging_path = pathlib.Path(directory)
                staging_stat = staging_path.stat(follow_symlinks=False)
                staging_descriptor = _open_absolute_directory(staging_path)
                with contextlib.ExitStack() as staging_descriptors:
                    staging_descriptors.callback(os.close, staging_descriptor)
                    if _identity(staging_stat) != _identity(os.fstat(staging_descriptor)):
                        raise soleaux.editor.preview.EditorPreviewError(
                            "editor staging directory identity changed while it was opened"
                        )
                    staged: dict[str, _StagedImages] = {}
                    for index, target in enumerate(targets):
                        postimage_name = f"{index}.post"
                        preimage_name = f"{index}.pre"
                        staged[target.item.path] = _StagedImages(
                            postimage=_write_stage(
                                postimage_name,
                                target.item.postimage,
                                target.mode,
                                dir_fd=staging_descriptor,
                            ),
                            preimage=_write_stage(
                                preimage_name,
                                target.item.preimage,
                                target.mode,
                                dir_fd=staging_descriptor,
                            ),
                        )
                    return await _replace_staged_files(
                        record,
                        workspace=workspace,
                        targets=targets,
                        staging_descriptor=staging_descriptor,
                        staged=staged,
                        replace_file=replace_file,
                    )
    except (OSError, soleaux.editor.preview.EditorPreviewError, RuntimeError, ValueError) as exc:
        return _conflicted(record, f"could not stage preview: {exc}")


async def _replace_staged_files(
    record: soleaux.editor.preview.StoredPreview,
    *,
    workspace: _WorkspaceBinding,
    targets: tuple[_TargetBinding, ...],
    staging_descriptor: int,
    staged: dict[str, _StagedImages],
    replace_file: ReplaceFile,
) -> soleaux.editor.contracts.ApplyPayload:
    applied: list[_AppliedTarget] = []
    states: dict[str, soleaux.editor.contracts.FileApplyResult] = {}
    failure: BaseException | None = None
    commit_conflict: _CommitAncestryConflict | None = None
    for target in targets:
        item = target.item
        try:
            await asyncio.sleep(0)
            images = staged[item.path]
            applied_target = _commit_target(
                workspace,
                target,
                images,
                staging_descriptor=staging_descriptor,
                replace_file=replace_file,
            )
            applied.append(applied_target)
            states[item.path] = _file_result(item, soleaux.editor.contracts.ApplyState.APPLIED)
        except BaseException as exc:
            failure = exc
            if isinstance(exc, _CommitAncestryConflict):
                commit_conflict = exc
            else:
                try:
                    _require_directory_binding(
                        workspace,
                        target.directory,
                        path=item.path,
                        phase="commit",
                        error_type=_CommitAncestryConflict,
                    )
                except _CommitAncestryConflict as ancestry_exc:
                    commit_conflict = ancestry_exc
            images = staged[item.path]
            if _is_exact_task_postimage(target, images.postimage):
                applied.append(_AppliedTarget(target=target, staged=images))
                states[item.path] = _file_result(
                    item,
                    soleaux.editor.contracts.ApplyState.APPLIED,
                    live_hash=item.postimage_hash,
                )
            break

    if failure is None:
        return soleaux.editor.contracts.ApplyPayload(
            preview_id=record.payload.preview_id,
            state=soleaux.editor.contracts.ApplyState.APPLIED,
            files=tuple(states[item.path] for item in record.files),
        )

    rollback_complete = True
    rollback_errors: list[str] = []
    commit_conflict_directories: set[tuple[str, ...]] = set()
    if commit_conflict is not None:
        commit_conflict_directories.update(
            applied_target.target.directory.relative_parts for applied_target in applied
        )
    for applied_target in reversed(applied):
        target = applied_target.target
        item = target.item
        allow_unbound_ancestry = target.directory.relative_parts in commit_conflict_directories
        try:
            restored = _rollback_target(
                workspace,
                applied_target,
                staging_descriptor=staging_descriptor,
                replace_file=replace_file,
                allow_unbound_ancestry=allow_unbound_ancestry,
            )
            states[item.path] = _file_result(
                item,
                soleaux.editor.contracts.ApplyState.ROLLED_BACK,
                live_hash=restored,
            )
        except (OSError, soleaux.editor.preview.EditorPreviewError) as exc:
            rollback_complete = False
            rollback_error: BaseException = exc
            if (
                not allow_unbound_ancestry
                and not _directory_binding_matches(
                    workspace,
                    target.directory,
                )
                and not isinstance(exc, _RollbackAncestryConflict)
            ):
                rollback_error = _RollbackAncestryConflict(
                    f"edit target directory identity changed during rollback for {item.path!r}"
                )
            rollback_errors.append(str(rollback_error))
            states[item.path] = _file_result(
                item,
                soleaux.editor.contracts.ApplyState.PARTIAL_FAILURE,
                live_hash=_safe_bound_live_hash(workspace, target),
            )

    for item in record.files:
        if item.path not in states:
            states[item.path] = _file_result(
                item,
                soleaux.editor.contracts.ApplyState.CONFLICTED,
                live_hash=_safe_live_hash(record.root, item.path),
            )
    if isinstance(failure, asyncio.CancelledError):
        raise failure
    primary_failure: BaseException = commit_conflict or failure
    message = str(primary_failure)
    if rollback_errors:
        message = f"{message}; rollback conflict: {'; '.join(rollback_errors)}"
    if not applied or (commit_conflict is not None and rollback_complete):
        state = soleaux.editor.contracts.ApplyState.CONFLICTED
    elif rollback_complete:
        state = soleaux.editor.contracts.ApplyState.ROLLED_BACK
    else:
        state = soleaux.editor.contracts.ApplyState.PARTIAL_FAILURE
    return soleaux.editor.contracts.ApplyPayload(
        preview_id=record.payload.preview_id,
        state=state,
        files=tuple(states[item.path] for item in record.files),
        message=message,
    )


def _require_secure_directory_operations() -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if (
        not isinstance(directory_flag, int)
        or directory_flag == 0
        or not isinstance(nofollow_flag, int)
        or nofollow_flag == 0
        or os.open not in os.supports_dir_fd
        or os.rename not in os.supports_dir_fd
        or os.access not in os.supports_dir_fd
    ):
        raise soleaux.editor.preview.EditorPreviewError(
            "secure directory-relative editor apply is unsupported on this platform"
        )


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identity(status: os.stat_result) -> _FileIdentity:
    return _FileIdentity(device=status.st_dev, inode=status.st_ino)


def _bind_workspace(
    root: pathlib.Path,
    descriptors: contextlib.ExitStack,
) -> _WorkspaceBinding:
    root_path = root.resolve(strict=True)
    path_status = root_path.stat(follow_symlinks=False)
    descriptor = _open_absolute_directory(root_path)
    descriptors.callback(os.close, descriptor)
    descriptor_status = os.fstat(descriptor)
    if not stat.S_ISDIR(descriptor_status.st_mode):
        raise soleaux.editor.preview.EditorPreviewError(
            f"workspace root is not a directory: {str(root_path)!r}"
        )
    if _identity(path_status) != _identity(descriptor_status):
        raise soleaux.editor.preview.EditorPreviewError(
            "workspace root identity changed while it was opened"
        )
    return _WorkspaceBinding(
        root_path=root_path,
        descriptor=descriptor,
        identity=_identity(descriptor_status),
    )


def _bind_targets(
    record: soleaux.editor.preview.StoredPreview,
    workspace: _WorkspaceBinding,
    descriptors: contextlib.ExitStack,
) -> tuple[_TargetBinding, ...]:
    directories: dict[tuple[str, ...], _DirectoryBinding] = {
        (): _DirectoryBinding(
            descriptor=workspace.descriptor,
            relative_parts=(),
            identity=workspace.identity,
        )
    }
    targets: list[_TargetBinding] = []
    for item in record.files:
        admitted = soleaux.editor.preview.admit_edit_path(workspace.root_path, item.path)
        admitted_status = admitted.stat(follow_symlinks=False)
        admitted_parent_status = admitted.parent.stat(follow_symlinks=False)
        parts = pathlib.PurePosixPath(item.path).parts
        parent_parts = tuple(parts[:-1])
        directory = directories.get(parent_parts)
        if directory is None:
            descriptor = _open_relative_directory(workspace.descriptor, parent_parts)
            descriptors.callback(os.close, descriptor)
            directory_status = os.fstat(descriptor)
            if not stat.S_ISDIR(directory_status.st_mode):
                raise soleaux.editor.preview.EditorPreviewError(
                    f"edit target parent is not a directory: {item.path!r}"
                )
            directory = _DirectoryBinding(
                descriptor=descriptor,
                relative_parts=parent_parts,
                identity=_identity(directory_status),
            )
            directories[parent_parts] = directory
        if _identity(admitted_parent_status) != directory.identity:
            raise soleaux.editor.preview.EditorPreviewError(
                f"edit target directory identity changed during validation for {item.path!r}"
            )
        _require_directory_binding(
            workspace,
            directory,
            path=item.path,
            phase="validation",
            error_type=soleaux.editor.preview.EditorPreviewError,
        )
        content, target_status = _read_regular_at(
            directory.descriptor,
            parts[-1],
            display_path=item.path,
        )
        if _identity(admitted_status) != _identity(target_status):
            raise soleaux.editor.preview.EditorPreviewError(
                f"edit target identity changed during validation for {item.path!r}"
            )
        if not target_status.st_mode & 0o222:
            raise soleaux.editor.preview.EditorPreviewError(
                f"edit target is not writable: {item.path!r}"
            )
        if not os.access(".", os.W_OK, dir_fd=directory.descriptor):
            raise soleaux.editor.preview.EditorPreviewError(
                f"edit target directory is not writable: {item.path!r}"
            )
        if soleaux.contracts.repository.content_digest(content) != item.preimage_hash:
            raise soleaux.editor.preview.EditorPreviewError(
                f"preimage drift detected for {item.path!r}"
            )
        targets.append(
            _TargetBinding(
                item=item,
                directory=directory,
                name=parts[-1],
                preimage_identity=_identity(target_status),
                mode=stat.S_IMODE(target_status.st_mode),
            )
        )
    return tuple(targets)


def _open_absolute_directory(path: pathlib.Path) -> int:
    if not path.is_absolute() or not path.anchor:
        raise soleaux.editor.preview.EditorPreviewError(
            f"directory path is not absolute: {str(path)!r}"
        )
    descriptor = os.open(path.anchor, _directory_flags())
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(
                part,
                _directory_flags(),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_relative_directory(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            next_descriptor = os.open(
                part,
                _directory_flags(),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _directory_binding_matches(
    workspace: _WorkspaceBinding,
    directory: _DirectoryBinding,
) -> bool:
    try:
        root_descriptor = _open_absolute_directory(workspace.root_path)
        with contextlib.ExitStack() as descriptors:
            descriptors.callback(os.close, root_descriptor)
            if _identity(os.fstat(root_descriptor)) != workspace.identity:
                return False
            parent_descriptor = _open_relative_directory(
                root_descriptor,
                directory.relative_parts,
            )
            descriptors.callback(os.close, parent_descriptor)
            return _identity(os.fstat(parent_descriptor)) == directory.identity
    except OSError, RuntimeError, ValueError, soleaux.editor.preview.EditorPreviewError:
        return False


def _require_directory_binding(
    workspace: _WorkspaceBinding,
    directory: _DirectoryBinding,
    *,
    path: str,
    phase: str,
    error_type: type[soleaux.editor.preview.EditorPreviewError],
) -> None:
    if not _directory_binding_matches(workspace, directory):
        raise error_type(f"edit target directory identity changed during {phase} for {path!r}")


def _read_regular_at(
    directory_descriptor: int,
    name: str,
    *,
    display_path: str,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
    except (OSError, ValueError) as exc:
        raise soleaux.editor.preview.EditorPreviewError(
            f"edit target cannot be read safely: {display_path!r}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise soleaux.editor.preview.EditorPreviewError(
                f"edit target is not a regular file: {display_path!r}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(), opened
    except OSError as exc:
        raise soleaux.editor.preview.EditorPreviewError(
            f"edit target cannot be read safely: {display_path!r}"
        ) from exc
    finally:
        os.close(descriptor)


def _require_file_image(
    directory_descriptor: int,
    name: str,
    *,
    display_path: str,
    expected_identity: _FileIdentity,
    expected_hash: str,
    label: str,
) -> str:
    content, status = _read_regular_at(
        directory_descriptor,
        name,
        display_path=display_path,
    )
    live_hash = soleaux.contracts.repository.content_digest(content)
    if _identity(status) != expected_identity or live_hash != expected_hash:
        raise soleaux.editor.preview.EditorPreviewError(
            f"{label} identity or content drift detected for {display_path!r}"
        )
    return live_hash


def _commit_target(
    workspace: _WorkspaceBinding,
    target: _TargetBinding,
    staged: _StagedImages,
    *,
    staging_descriptor: int,
    replace_file: ReplaceFile,
) -> _AppliedTarget:
    item = target.item
    _require_directory_binding(
        workspace,
        target.directory,
        path=item.path,
        phase="commit",
        error_type=_CommitAncestryConflict,
    )
    _require_file_image(
        target.directory.descriptor,
        target.name,
        display_path=item.path,
        expected_identity=target.preimage_identity,
        expected_hash=item.preimage_hash,
        label="preimage",
    )
    _require_file_image(
        staging_descriptor,
        staged.postimage.name,
        display_path=f"staged postimage for {item.path}",
        expected_identity=staged.postimage.identity,
        expected_hash=item.postimage_hash,
        label="staged postimage",
    )
    replace_file(
        staged.postimage.name,
        target.name,
        src_dir_fd=staging_descriptor,
        dst_dir_fd=target.directory.descriptor,
    )
    _require_file_image(
        target.directory.descriptor,
        target.name,
        display_path=item.path,
        expected_identity=staged.postimage.identity,
        expected_hash=item.postimage_hash,
        label="postimage",
    )
    _require_directory_binding(
        workspace,
        target.directory,
        path=item.path,
        phase="commit",
        error_type=_CommitAncestryConflict,
    )
    return _AppliedTarget(target=target, staged=staged)


def _is_exact_task_postimage(
    target: _TargetBinding,
    staged_postimage: _StagedFile,
) -> bool:
    try:
        _require_file_image(
            target.directory.descriptor,
            target.name,
            display_path=target.item.path,
            expected_identity=staged_postimage.identity,
            expected_hash=target.item.postimage_hash,
            label="postimage",
        )
    except OSError, soleaux.editor.preview.EditorPreviewError:
        return False
    return True


def _rollback_target(
    workspace: _WorkspaceBinding,
    applied: _AppliedTarget,
    *,
    staging_descriptor: int,
    replace_file: ReplaceFile,
    allow_unbound_ancestry: bool,
) -> str:
    target = applied.target
    item = target.item
    ancestry_was_bound = _directory_binding_matches(workspace, target.directory)
    _require_file_image(
        target.directory.descriptor,
        target.name,
        display_path=item.path,
        expected_identity=applied.staged.postimage.identity,
        expected_hash=item.postimage_hash,
        label="rollback postimage",
    )
    _require_file_image(
        staging_descriptor,
        applied.staged.preimage.name,
        display_path=f"staged preimage for {item.path}",
        expected_identity=applied.staged.preimage.identity,
        expected_hash=item.preimage_hash,
        label="staged preimage",
    )
    replace_file(
        applied.staged.preimage.name,
        target.name,
        src_dir_fd=staging_descriptor,
        dst_dir_fd=target.directory.descriptor,
    )
    restored = _require_file_image(
        target.directory.descriptor,
        target.name,
        display_path=item.path,
        expected_identity=applied.staged.preimage.identity,
        expected_hash=item.preimage_hash,
        label="restored preimage",
    )
    if not allow_unbound_ancestry and (
        not ancestry_was_bound or not _directory_binding_matches(workspace, target.directory)
    ):
        raise _RollbackAncestryConflict(
            f"edit target directory identity changed during rollback for {item.path!r}"
        )
    return restored


def _safe_bound_live_hash(
    workspace: _WorkspaceBinding,
    target: _TargetBinding,
) -> str | None:
    if not _directory_binding_matches(workspace, target.directory):
        return None
    try:
        content, _status = _read_regular_at(
            target.directory.descriptor,
            target.name,
            display_path=target.item.path,
        )
        return soleaux.contracts.repository.content_digest(content)
    except OSError, soleaux.editor.preview.EditorPreviewError:
        return None


def _preflight(record: soleaux.editor.preview.StoredPreview) -> str | None:
    paths = tuple(item.path for item in record.files)
    if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
        return "preview file ordering is invalid"
    if paths != record.payload.affected_paths:
        return "preview affected paths do not match stored edits"
    if record.payload.preimage_hashes != {item.path: item.preimage_hash for item in record.files}:
        return "preview preimage hashes do not match stored edits"
    if record.payload.postimage_hashes != {item.path: item.postimage_hash for item in record.files}:
        return "preview postimage hashes do not match stored edits"
    if record.payload.patches != tuple(patch for item in record.files for patch in item.patches):
        return "preview patch ordering does not match stored edits"

    for item in record.files:
        integrity_error = _stored_edit_integrity_error(item)
        if integrity_error is not None:
            return integrity_error
    return None


def _stored_edit_integrity_error(item: soleaux.editor.preview.NormalizedFileEdit) -> str | None:
    if soleaux.contracts.repository.content_digest(item.preimage) != item.preimage_hash:
        return f"stored preimage hash is invalid for {item.path!r}"
    if soleaux.contracts.repository.content_digest(item.postimage) != item.postimage_hash:
        return f"stored postimage hash is invalid for {item.path!r}"
    expected_order = tuple(
        sorted(
            item.patches,
            key=lambda patch: (patch.start_byte, patch.end_byte, patch.new_text),
        )
    )
    if item.patches != expected_order:
        return f"stored patch ordering is invalid for {item.path!r}"

    previous = None
    reconstructed = item.preimage
    for patch in item.patches:
        if patch.path != item.path or patch.preimage_hash != item.preimage_hash:
            return f"stored patch binding is invalid for {item.path!r}"
        if patch.end_byte < patch.start_byte or patch.end_byte > len(item.preimage):
            return f"stored patch range is invalid for {item.path!r}"
        if previous is not None and (
            patch.start_byte < previous.end_byte or patch.start_byte == previous.start_byte
        ):
            return f"stored patches overlap for {item.path!r}"
        previous = patch
    try:
        for patch in reversed(item.patches):
            replacement = patch.new_text.encode("utf-8")
            reconstructed = (
                reconstructed[: patch.start_byte] + replacement + reconstructed[patch.end_byte :]
            )
    except UnicodeEncodeError:
        return f"stored replacement is not valid UTF-8 for {item.path!r}"
    if reconstructed != item.postimage:
        return f"stored postimage does not match patches for {item.path!r}"
    return None


def _write_stage(
    name: str,
    content: bytes,
    mode: int,
    *,
    dir_fd: int,
) -> _StagedFile:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode,
        dir_fd=dir_fd,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        identity = _identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    return _StagedFile(name=name, identity=identity)


def _conflicted(
    record: soleaux.editor.preview.StoredPreview, message: str
) -> soleaux.editor.contracts.ApplyPayload:
    return soleaux.editor.contracts.ApplyPayload(
        preview_id=record.payload.preview_id,
        state=soleaux.editor.contracts.ApplyState.CONFLICTED,
        files=tuple(
            _file_result(
                item,
                soleaux.editor.contracts.ApplyState.CONFLICTED,
                live_hash=_safe_live_hash(record.root, item.path),
            )
            for item in record.files
        ),
        message=message,
    )


def _file_result(
    item: soleaux.editor.preview.NormalizedFileEdit,
    state: soleaux.editor.contracts.ApplyState,
    *,
    live_hash: str | None = None,
) -> soleaux.editor.contracts.FileApplyResult:
    return soleaux.editor.contracts.FileApplyResult(
        path=item.path,
        state=state,
        preimage_hash=item.preimage_hash,
        postimage_hash=item.postimage_hash,
        live_hash=live_hash,
    )


def _safe_live_hash(root: pathlib.Path, path: str) -> str | None:
    try:
        return soleaux.contracts.repository.content_digest(
            soleaux.editor.preview.read_regular_file(
                soleaux.editor.preview.admit_edit_path(root, path)
            )
        )
    except OSError, soleaux.editor.preview.EditorPreviewError:
        return None
