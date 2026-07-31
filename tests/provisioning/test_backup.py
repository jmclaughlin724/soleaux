"""Backup module: file copy, manifest, revert."""

from __future__ import annotations

import json
import pathlib
import stat

import _assertions
import pytest

import soleaux.provisioning.backup


def test_backup_file_copies_and_records(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"key": "value"}', encoding="utf-8")

    record = soleaux.provisioning.backup.backup_file(tmp_path, target)

    assert record.original_path == "settings.json"
    assert record.backup_path.startswith(".soleaux-backups/settings.json.")
    backup_path = tmp_path / record.backup_path
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == '{"key": "value"}'
    backup_dir = tmp_path / ".soleaux-backups"
    manifest = backup_dir / "manifest.json"
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600


def test_backup_manifest_appends_across_calls(tmp_path: pathlib.Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")

    soleaux.provisioning.backup.backup_file(tmp_path, a)
    soleaux.provisioning.backup.backup_file(tmp_path, b)

    manifest = json.loads((tmp_path / ".soleaux-backups" / "manifest.json").read_text())
    assert len(manifest["backups"]) == 2
    assert {entry["original_path"] for entry in manifest["backups"]} == {"a.txt", "b.txt"}


def test_backup_nested_relative_path_flattened(tmp_path: pathlib.Path) -> None:
    nested = tmp_path / ".vscode" / "settings.json"
    nested.parent.mkdir()
    nested.write_text("{}", encoding="utf-8")

    record = soleaux.provisioning.backup.backup_file(tmp_path, nested)

    assert record.original_path == ".vscode/settings.json"
    assert ".vscode__settings.json" in record.backup_path


def test_backup_and_restore_preserve_exact_file_mode(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o640)

    record = soleaux.provisioning.backup.backup_file(tmp_path, target)
    backup_path = tmp_path / record.backup_path
    target.write_text('{"changed":true}\n', encoding="utf-8")
    target.chmod(0o600)
    soleaux.provisioning.backup.restore(tmp_path)

    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o640
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_revert_restores_originals_in_reverse(tmp_path: pathlib.Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A1", encoding="utf-8")
    b.write_text("B1", encoding="utf-8")
    soleaux.provisioning.backup.backup_file(tmp_path, a)
    soleaux.provisioning.backup.backup_file(tmp_path, b)

    a.write_text("A2", encoding="utf-8")
    b.write_text("B2", encoding="utf-8")

    restored = soleaux.provisioning.backup.restore(tmp_path)

    assert set(restored) == {"a.txt", "b.txt"}
    assert a.read_text(encoding="utf-8") == "A1"
    assert b.read_text(encoding="utf-8") == "B1"


def test_revert_no_manifest_returns_empty(tmp_path: pathlib.Path) -> None:
    assert soleaux.provisioning.backup.restore(tmp_path) == []


def test_revert_rejects_missing_backup_files_without_write(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("orig", encoding="utf-8")
    record = soleaux.provisioning.backup.backup_file(tmp_path, target)
    target.write_text("changed", encoding="utf-8")
    manifest = tmp_path / ".soleaux-backups" / "manifest.json"
    manifest_before = manifest.read_bytes()
    (tmp_path / record.backup_path).unlink()

    with _assertions.raises_with_message(
        soleaux.provisioning.backup.BackupManifestError,
        "missing",
    ):
        soleaux.provisioning.backup.restore(tmp_path)

    assert target.read_text(encoding="utf-8") == "changed"
    assert manifest.read_bytes() == manifest_before


def test_backup_rejects_symlink_source_before_creating_storage(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    target = workspace / "settings.json"
    target.symlink_to(outside)

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.backup_file(workspace, target)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert not (workspace / ".soleaux-backups").exists()


def test_backup_rejects_symlink_storage_before_external_write(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "settings.json"
    target.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".soleaux-backups").symlink_to(outside, target_is_directory=True)

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.backup_file(workspace, target)

    assert list(outside.iterdir()) == []


def test_restore_preflights_manifest_paths_before_any_write(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backup_dir = workspace / ".soleaux-backups"
    backup_dir.mkdir()
    safe = workspace / "safe.txt"
    safe.write_text("changed\n", encoding="utf-8")
    (backup_dir / "safe.backup").write_text("original\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (backup_dir / "escape.backup").write_text("escaped\n", encoding="utf-8")
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backups": [
                    {
                        "backup_path": ".soleaux-backups/escape.backup",
                        "original_path": "../outside.txt",
                        "timestamp": "first",
                    },
                    {
                        "backup_path": ".soleaux-backups/safe.backup",
                        "original_path": "safe.txt",
                        "timestamp": "second",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with _assertions.raises_with_message(ValueError, "workspace"):
        soleaux.provisioning.backup.restore(workspace)

    assert safe.read_text(encoding="utf-8") == "changed\n"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_restore_rejects_symlink_destination_before_external_write(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backup_dir = workspace / ".soleaux-backups"
    backup_dir.mkdir()
    (backup_dir / "target.backup").write_text("restored\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (workspace / "target.txt").symlink_to(outside)
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backups": [
                    {
                        "backup_path": ".soleaux-backups/target.backup",
                        "original_path": "target.txt",
                        "timestamp": "now",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.restore(workspace)

    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_backup_rejects_malformed_manifest_without_changing_history(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    backup_dir = tmp_path / ".soleaux-backups"
    backup_dir.mkdir()
    manifest = backup_dir / "manifest.json"
    malformed = b'{"backups": ['
    manifest.write_bytes(malformed)

    with _assertions.raises_with_message(
        soleaux.provisioning.backup.BackupManifestError,
        "malformed",
    ):
        soleaux.provisioning.backup.backup_file(tmp_path, target)

    assert manifest.read_bytes() == malformed
    assert [path.name for path in backup_dir.iterdir()] == ["manifest.json"]
    assert target.read_text(encoding="utf-8") == "target\n"


def test_restore_rejects_mixed_invalid_manifest_before_valid_restore(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("changed\n", encoding="utf-8")
    backup_dir = tmp_path / ".soleaux-backups"
    backup_dir.mkdir()
    (backup_dir / "valid.backup").write_text("original\n", encoding="utf-8")
    manifest = backup_dir / "manifest.json"
    payload = json.dumps(
        {
            "backups": [
                {
                    "backup_path": ".soleaux-backups/valid.backup",
                    "original_path": "target.txt",
                    "timestamp": "valid",
                },
                {
                    "backup_path": ".soleaux-backups/invalid.backup",
                    "original_path": "other.txt",
                },
            ]
        }
    ).encode("utf-8")
    manifest.write_bytes(payload)

    with _assertions.raises_with_message(
        soleaux.provisioning.backup.BackupManifestError,
        "malformed",
    ):
        soleaux.provisioning.backup.restore(tmp_path)

    assert target.read_text(encoding="utf-8") == "changed\n"
    assert manifest.read_bytes() == payload
    assert not (tmp_path / "other.txt").exists()


def test_restore_rejects_manifest_without_required_backups_field(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("unchanged\n", encoding="utf-8")
    backup_dir = tmp_path / ".soleaux-backups"
    backup_dir.mkdir()
    manifest = backup_dir / "manifest.json"
    payload = b"{}"
    manifest.write_bytes(payload)

    with _assertions.raises_with_message(
        soleaux.provisioning.backup.BackupManifestError,
        "malformed",
    ):
        soleaux.provisioning.backup.restore(tmp_path)

    assert target.read_text(encoding="utf-8") == "unchanged\n"
    assert manifest.read_bytes() == payload


def test_restore_rejects_original_path_inside_backup_storage(
    tmp_path: pathlib.Path,
) -> None:
    backup_dir = tmp_path / ".soleaux-backups"
    backup_dir.mkdir()
    source = backup_dir / "source.backup"
    source.write_text("replacement\n", encoding="utf-8")
    manifest = backup_dir / "manifest.json"
    payload = json.dumps(
        {
            "backups": [
                {
                    "backup_path": ".soleaux-backups/source.backup",
                    "original_path": ".soleaux-backups/manifest.json",
                    "timestamp": "now",
                }
            ]
        }
    ).encode("utf-8")
    manifest.write_bytes(payload)

    with _assertions.raises_with_message(ValueError, "backup storage"):
        soleaux.provisioning.backup.restore(tmp_path)

    assert manifest.read_bytes() == payload


def test_restore_rejects_case_variant_backup_storage_destination(
    tmp_path: pathlib.Path,
) -> None:
    backup_dir = tmp_path / ".soleaux-backups"
    backup_dir.mkdir()
    source = backup_dir / "source.backup"
    source.write_text("replacement\n", encoding="utf-8")
    manifest = backup_dir / "manifest.json"
    payload = json.dumps(
        {
            "backups": [
                {
                    "backup_path": ".soleaux-backups/source.backup",
                    "original_path": ".Soleaux-Backups/manifest.json",
                    "timestamp": "now",
                }
            ]
        }
    ).encode("utf-8")
    manifest.write_bytes(payload)

    with _assertions.raises_with_message(ValueError, "backup storage"):
        soleaux.provisioning.backup.restore(tmp_path)

    assert manifest.read_bytes() == payload
    assert {path.name for path in tmp_path.iterdir()} == {".soleaux-backups"}


def test_backup_source_symlink_swap_after_admission_cannot_escape(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "target.txt"
    source.write_text("target\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    original_read = soleaux.provisioning.backup.WorkspaceIo.read_file
    swapped = False

    def race_read(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
    ) -> soleaux.provisioning.backup.FileSnapshot:
        nonlocal swapped
        if path.as_posix == "target.txt" and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return original_read(workspace_io, path)

    monkeypatch.setattr(soleaux.provisioning.backup.WorkspaceIo, "read_file", race_read)

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.backup_file(workspace, source)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert source.is_symlink()
    assert not (workspace / ".soleaux-backups").exists()


def test_restore_backup_symlink_swap_after_validation_cannot_escape(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("changed\n", encoding="utf-8")
    backup_dir = workspace / ".soleaux-backups"
    backup_dir.mkdir()
    backup_path = backup_dir / "target.backup"
    backup_path.write_text("original\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backups": [
                    {
                        "backup_path": ".soleaux-backups/target.backup",
                        "original_path": "target.txt",
                        "timestamp": "now",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    original_read = soleaux.provisioning.backup.WorkspaceIo.read_file
    swapped = False

    def race_read(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
    ) -> soleaux.provisioning.backup.FileSnapshot:
        nonlocal swapped
        if path.as_posix == ".soleaux-backups/target.backup" and not swapped:
            swapped = True
            backup_path.unlink()
            backup_path.symlink_to(outside)
        return original_read(workspace_io, path)

    monkeypatch.setattr(soleaux.provisioning.backup.WorkspaceIo, "read_file", race_read)

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.restore(workspace)

    assert target.read_text(encoding="utf-8") == "changed\n"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_backup_destination_symlink_swap_after_admission_cannot_escape(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "target.txt"
    source.write_text("target\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    original_write = soleaux.provisioning.backup.WorkspaceIo.write_bytes_exclusive
    swapped = False

    def race_write(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
        data: bytes,
        *,
        mode: int,
    ) -> None:
        nonlocal swapped
        if path.as_posix.startswith(".soleaux-backups/") and not swapped:
            swapped = True
            destination = workspace_io.absolute(path)
            destination.parent.mkdir()
            destination.symlink_to(outside)
        original_write(workspace_io, path, data, mode=mode)

    monkeypatch.setattr(
        soleaux.provisioning.backup.WorkspaceIo,
        "write_bytes_exclusive",
        race_write,
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.backup_file(workspace, source)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert not (workspace / ".soleaux-backups" / "manifest.json").exists()


def test_manifest_symlink_swap_after_admission_cannot_escape(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "target.txt"
    source.write_text("target\n", encoding="utf-8")
    backup_dir = workspace / ".soleaux-backups"
    backup_dir.mkdir()
    manifest = backup_dir / "manifest.json"
    manifest.write_text('{"backups":[]}', encoding="utf-8")
    detached_manifest = backup_dir / "manifest.original"
    outside = tmp_path / "outside.json"
    outside.write_text('{"outside":true}', encoding="utf-8")
    original_read = soleaux.provisioning.backup.WorkspaceIo.read_file
    swapped = False

    def race_read(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
    ) -> soleaux.provisioning.backup.FileSnapshot:
        nonlocal swapped
        if path.as_posix == ".soleaux-backups/manifest.json" and not swapped:
            swapped = True
            manifest.rename(detached_manifest)
            manifest.symlink_to(outside)
        return original_read(workspace_io, path)

    monkeypatch.setattr(soleaux.provisioning.backup.WorkspaceIo, "read_file", race_read)

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.backup.backup_file(workspace, source)

    assert outside.read_text(encoding="utf-8") == '{"outside":true}'
    assert detached_manifest.read_text(encoding="utf-8") == '{"backups":[]}'
    assert {path.name for path in backup_dir.iterdir()} == {
        "manifest.original",
        "manifest.json",
    }
