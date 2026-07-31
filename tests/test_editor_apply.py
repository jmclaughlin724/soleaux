"""D009: apply only confirmed, live, preimage-bound previews."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from _assertions import raises_with_message
from test_editor_preview import make_editor_service

import soleaux.analysis.service
from soleaux.contracts.requests import (
    ApplyEditRequest,
    PreviewEditRequest,
    PreviewOperation,
    RenameTarget,
)
from soleaux.contracts.results import ResultStatus
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.editor.apply import apply_stored_preview
from soleaux.editor.contracts import ApplyPayload, ApplyState
from soleaux.editor.preview import (
    PreviewLookupError,
    PreviewRegistry,
    StoredPreview,
    normalize_workspace_edit,
)
from soleaux.structural.snapshot import RepositorySnapshotter


async def _rename_apply_request(
    service: soleaux.analysis.service.SoleauxService,
) -> ApplyEditRequest:
    preview = await service.preview(
        PreviewEditRequest(
            operation=PreviewOperation.RENAME,
            path="main.py",
            target=RenameTarget.POSITION,
            line=1,
            column=5,
            new_name="renamed",
            strict=True,
        )
    )
    assert preview.data is not None
    return ApplyEditRequest(
        preview_id=str(preview.data["preview_id"]),
        digest=str(preview.data["digest"]),
        confirm=True,
    )


async def test_confirmed_apply_succeeds_once_and_replay_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("def target():\n    return 1\n", encoding="utf-8")
    service, _resolver = await make_editor_service(tmp_path)
    try:
        preview = await service.preview(
            PreviewEditRequest(
                operation=PreviewOperation.RENAME,
                path="main.py",
                target=RenameTarget.POSITION,
                line=1,
                column=5,
                new_name="renamed",
                strict=True,
            )
        )
        assert preview.data is not None
        request = ApplyEditRequest(
            preview_id=str(preview.data["preview_id"]),
            digest=str(preview.data["digest"]),
            confirm=True,
        )
        unconfirmed = await service.apply(
            ApplyEditRequest(
                preview_id=request.preview_id,
                digest=request.digest,
            )
        )
        applied = await service.apply(request)
        replay = await service.apply(request)
    finally:
        await service.aclose()

    assert unconfirmed.status is ResultStatus.ERROR
    assert unconfirmed.data is not None
    assert unconfirmed.data["state"] == ApplyState.CONFLICTED
    assert applied.status is ResultStatus.OK
    assert applied.data is not None
    assert applied.data["state"] == ApplyState.APPLIED
    assert source.read_text(encoding="utf-8").startswith("def renamed")
    assert replay.status is ResultStatus.ERROR
    assert replay.data is not None
    assert replay.data["state"] == ApplyState.CONFLICTED


async def test_partial_apply_always_invalidates_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    service, _resolver = await make_editor_service(tmp_path)
    request = await _rename_apply_request(service)
    dirty: list[tuple[str, tuple[str, ...]]] = []
    notifications = 0

    async def partial_apply(record: StoredPreview) -> ApplyPayload:
        return ApplyPayload(
            preview_id=record.payload.preview_id,
            state=ApplyState.PARTIAL_FAILURE,
        )

    def mark_dirty(workspace_id: str, paths: tuple[str, ...]) -> None:
        dirty.append((workspace_id, paths))

    def notify_dirty() -> None:
        nonlocal notifications
        notifications += 1

    monkeypatch.setattr(soleaux.analysis.service, "apply_stored_preview", partial_apply)
    monkeypatch.setattr(service._frames, "mark_dirty", mark_dirty)
    monkeypatch.setattr(service._catalog_indexer, "notify_dirty", notify_dirty)
    try:
        result = await service.apply(request)
    finally:
        await service.aclose()

    assert result.data is not None
    assert result.data["state"] == ApplyState.PARTIAL_FAILURE
    assert dirty == [("workspace", ("main.py",))]
    assert notifications == 1


async def test_cancelled_apply_always_invalidates_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    service, _resolver = await make_editor_service(tmp_path)
    request = await _rename_apply_request(service)
    dirty: list[tuple[str, tuple[str, ...]]] = []
    notifications = 0

    async def cancelled_apply(_record: StoredPreview) -> ApplyPayload:
        raise asyncio.CancelledError

    def mark_dirty(workspace_id: str, paths: tuple[str, ...]) -> None:
        dirty.append((workspace_id, paths))

    def notify_dirty() -> None:
        nonlocal notifications
        notifications += 1

    monkeypatch.setattr(soleaux.analysis.service, "apply_stored_preview", cancelled_apply)
    monkeypatch.setattr(service._frames, "mark_dirty", mark_dirty)
    monkeypatch.setattr(service._catalog_indexer, "notify_dirty", notify_dirty)
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.apply(request)
    finally:
        await service.aclose()

    assert dirty == [("workspace", ("main.py",))]
    assert notifications == 1


async def test_drift_expiry_permission_and_symlink_fail_before_write(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.py"
    source.write_text("def target():\n    return 1\n", encoding="utf-8")
    service, _resolver = await make_editor_service(tmp_path)
    try:
        preview = await service.preview(
            PreviewEditRequest(
                operation=PreviewOperation.RENAME,
                path="main.py",
                line=1,
                column=5,
                new_name="renamed",
                strict=True,
            )
        )
        assert preview.data is not None
        source.write_text("concurrent\n", encoding="utf-8")
        drift = await service.apply(
            ApplyEditRequest(
                preview_id=str(preview.data["preview_id"]),
                digest=str(preview.data["digest"]),
                confirm=True,
            )
        )
    finally:
        await service.aclose()
    assert drift.data is not None
    assert drift.data["state"] == ApplyState.CONFLICTED
    assert source.read_text(encoding="utf-8") == "concurrent\n"

    source.write_text("def target():\n    return 1\n", encoding="utf-8")
    expiring_service, resolver = await make_editor_service(
        tmp_path,
        preview_ttl_seconds=0.001,
    )
    try:
        preview = await expiring_service.preview(
            PreviewEditRequest(
                operation=PreviewOperation.RENAME,
                path="main.py",
                line=1,
                column=5,
                new_name="renamed",
                strict=True,
            )
        )
        assert preview.data is not None
        await asyncio.sleep(0.01)
        expired = await expiring_service.apply(
            ApplyEditRequest(
                preview_id=str(preview.data["preview_id"]),
                digest=str(preview.data["digest"]),
                confirm=True,
            )
        )
    finally:
        del resolver
        await expiring_service.aclose()
    assert expired.data is not None
    assert expired.data["state"] == ApplyState.CONFLICTED


async def test_preview_claim_rejects_unknown_and_cross_epoch(tmp_path: Path) -> None:
    registry, payload = await _issued_two_file_preview(tmp_path)

    with raises_with_message(PreviewLookupError, "unknown"):
        registry.claim(
            preview_id="unknown-preview",
            digest=payload.digest,
            workspace_id="workspace",
            current_process_epoch="process",
            current_provider_epoch=0,
        )
    with raises_with_message(PreviewLookupError, "another workspace"):
        registry.claim(
            preview_id=payload.preview_id,
            digest=payload.digest,
            workspace_id="other-workspace",
            current_process_epoch="process",
            current_provider_epoch=0,
        )
    with raises_with_message(PreviewLookupError, "another process epoch"):
        registry.claim(
            preview_id=payload.preview_id,
            digest=payload.digest,
            workspace_id="workspace",
            current_process_epoch="other-process",
            current_provider_epoch=0,
        )
    with raises_with_message(PreviewLookupError, "provider epoch is stale"):
        registry.claim(
            preview_id=payload.preview_id,
            digest=payload.digest,
            workspace_id="workspace",
            current_process_epoch="process",
            current_provider_epoch=1,
        )


async def test_mid_sequence_failure_rolls_back_exact_written_postimages(
    tmp_path: Path,
) -> None:
    record = await _claimed_two_file_preview(tmp_path)
    replacements = 0

    def fail_second_postimage(
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal replacements
        if src.endswith(".post"):
            replacements += 1
            if replacements == 2:
                raise OSError("simulated second replace failure")
        os.replace(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    result = await apply_stored_preview(record, replace_file=fail_second_postimage)

    assert result.state is ApplyState.ROLLED_BACK
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "b = 1\n"


async def test_rollback_preserves_concurrent_writer_after_partial_apply(
    tmp_path: Path,
) -> None:
    record = await _claimed_two_file_preview(tmp_path)
    replacements = 0

    def concurrent_failure(
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal replacements
        if src.endswith(".post"):
            replacements += 1
            if replacements == 2:
                (tmp_path / "a.py").write_text("concurrent = 9\n", encoding="utf-8")
                raise OSError("simulated failure after concurrent write")
        os.replace(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    result = await apply_stored_preview(record, replace_file=concurrent_failure)

    assert result.state is ApplyState.PARTIAL_FAILURE
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "concurrent = 9\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "b = 1\n"


async def test_cancellation_rolls_back_exact_written_postimages(tmp_path: Path) -> None:
    record = await _claimed_two_file_preview(tmp_path)
    apply_task: asyncio.Task[object] | None = None
    replacements = 0

    def cancel_after_first_postimage(
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal replacements
        os.replace(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if src.endswith(".post"):
            replacements += 1
            if replacements == 1:
                assert apply_task is not None
                apply_task.cancel()

    apply_task = asyncio.create_task(
        apply_stored_preview(record, replace_file=cancel_after_first_postimage)
    )
    with pytest.raises(asyncio.CancelledError):
        await apply_task

    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "b = 1\n"


async def test_permission_and_symlink_races_conflict_without_external_write(
    tmp_path: Path,
) -> None:
    record = await _claimed_two_file_preview(tmp_path)
    first = tmp_path / "a.py"
    first.chmod(0o444)
    try:
        permission = await apply_stored_preview(record)
    finally:
        first.chmod(0o644)
    assert permission.state is ApplyState.CONFLICTED
    assert first.read_text(encoding="utf-8") == "a = 1\n"

    record = await _claimed_two_file_preview(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    first.unlink()
    first.symlink_to(outside)
    symlink = await apply_stored_preview(record)
    assert symlink.state is ApplyState.CONFLICTED
    assert outside.read_text(encoding="utf-8") == "outside\n"


async def test_parent_symlink_swap_during_commit_conflicts_without_external_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = await _claimed_two_file_preview(workspace, parent="src")
    parent = workspace / "src"
    detached = workspace / "src-detached"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_first = outside / "a.py"
    outside_second = outside / "b.py"
    outside_first.write_text("outside a\n", encoding="utf-8")
    outside_second.write_text("outside b\n", encoding="utf-8")
    swapped = False

    def swap_parent_before_commit(
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal swapped
        if src.endswith(".post") and not swapped:
            swapped = True
            parent.rename(detached)
            parent.symlink_to(outside, target_is_directory=True)
        os.replace(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    result = await apply_stored_preview(record, replace_file=swap_parent_before_commit)

    assert result.state is ApplyState.CONFLICTED
    assert result.message is not None
    assert "directory identity changed" in result.message
    assert outside_first.read_text(encoding="utf-8") == "outside a\n"
    assert outside_second.read_text(encoding="utf-8") == "outside b\n"
    assert (detached / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (detached / "b.py").read_text(encoding="utf-8") == "b = 1\n"


async def test_parent_symlink_swap_during_rollback_reports_partial_without_external_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = await _claimed_two_file_preview(workspace, parent="src")
    parent = workspace / "src"
    detached = workspace / "src-detached"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_first = outside / "a.py"
    outside_second = outside / "b.py"
    outside_first.write_text("outside a\n", encoding="utf-8")
    outside_second.write_text("outside b\n", encoding="utf-8")
    postimages = 0

    def swap_parent_during_rollback(
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        nonlocal postimages
        if src.endswith(".post"):
            postimages += 1
            if postimages == 2:
                raise OSError("simulated second replace failure")
        elif src.endswith(".pre"):
            parent.rename(detached)
            parent.symlink_to(outside, target_is_directory=True)
        os.replace(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    result = await apply_stored_preview(record, replace_file=swap_parent_during_rollback)

    assert result.state is ApplyState.PARTIAL_FAILURE
    assert result.message is not None
    assert "directory identity changed" in result.message
    assert outside_first.read_text(encoding="utf-8") == "outside a\n"
    assert outside_second.read_text(encoding="utf-8") == "outside b\n"
    assert (detached / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (detached / "b.py").read_text(encoding="utf-8") == "b = 1\n"


async def _claimed_two_file_preview(tmp_path: Path, *, parent: str | None = None):
    registry, payload = await _issued_two_file_preview(tmp_path, parent=parent)
    return registry.claim(
        preview_id=payload.preview_id,
        digest=payload.digest,
        workspace_id="workspace",
        current_process_epoch="process",
        current_provider_epoch=0,
    )


async def _issued_two_file_preview(tmp_path: Path, *, parent: str | None = None):
    directory = tmp_path if parent is None else tmp_path / parent
    directory.mkdir(parents=True, exist_ok=True)
    first = directory / "a.py"
    second = directory / "b.py"
    first.unlink(missing_ok=True)
    second.unlink(missing_ok=True)
    first.write_text("a = 1\n", encoding="utf-8")
    second.write_text("b = 1\n", encoding="utf-8")
    first_path = first.relative_to(tmp_path).as_posix()
    second_path = second.relative_to(tmp_path).as_posix()
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="apply-test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(scope=(first_path, second_path))
    normalized = normalize_workspace_edit(
        {
            "changes": {
                first.as_uri(): [
                    {
                        "range": _range(0, 4, 0, 5),
                        "newText": "2",
                    }
                ],
                second.as_uri(): [
                    {
                        "range": _range(0, 4, 0, 5),
                        "newText": "2",
                    }
                ],
            }
        },
        root=tmp_path,
        bundle=bundle,
        position_encoding="utf-16",
        document_versions={},
    )
    registry = PreviewRegistry(process_epoch="process")
    payload = registry.issue(
        workspace_id="workspace",
        root=tmp_path,
        provider_name="fixture-lsp",
        provider_config_digest="fixture-config",
        project_id="workspace:.",
        project_root="",
        project_config_digest="0" * 64,
        compiler_identity="fixture-lsp:initialize",
        provider_epoch=0,
        generation_fingerprint="generation",
        operation="rename",
        target={"path": first_path},
        position_encoding="utf-16",
        normalized=normalized,
    )
    return registry, payload


def _range(
    start_line: int,
    start_character: int,
    end_line: int,
    end_character: int,
) -> dict[str, dict[str, int]]:
    return {
        "start": {"line": start_line, "character": start_character},
        "end": {"line": end_line, "character": end_character},
    }
