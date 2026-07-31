"""Disk-generation retention contracts for the SQLite catalog store."""

import argparse
import datetime
import os
import pathlib
import subprocess
import sys
import time
import unittest.mock

import platformdirs
import pytest

import soleaux.catalog.contracts
import soleaux.catalog.generation
import soleaux.catalog.store
import soleaux.contracts.config
import soleaux.contracts.snapshot

_MEBIBYTE = 1024 * 1024
_PROCESS_TIMEOUT_SECONDS = 15


def _configure_cache(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[pathlib.Path, pathlib.Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cache_root = tmp_path / "cache"

    def cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return cache_root

    monkeypatch.setattr(platformdirs, "user_cache_path", cache_path)
    directory = soleaux.catalog.store.catalog_database_path(workspace).parent
    directory.mkdir(parents=True)
    return workspace, directory


def _write_generation(
    directory: pathlib.Path,
    fingerprint: str,
    *,
    main_size: int,
    wal_size: int,
    shm_size: int,
    modified_ns: int,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    main = directory / f"{fingerprint}.sqlite3"
    wal = pathlib.Path(f"{main}-wal")
    shm = pathlib.Path(f"{main}-shm")
    for path, size in ((main, main_size), (wal, wal_size), (shm, shm_size)):
        path.write_bytes(b"x" * size)
    os.utime(main, ns=(modified_ns, modified_ns))
    return main, wal, shm


def _catalog_generation(
    workspace: pathlib.Path,
    *,
    fingerprint: str,
    number: int,
) -> soleaux.catalog.generation.CatalogGeneration:
    snapshot = soleaux.contracts.snapshot.RepositorySnapshot(
        snapshot_id=f"main:{fingerprint[:16]}",
        workspace_id="main",
        root=str(workspace),
        created_at=datetime.datetime.now(datetime.UTC),
        source_fingerprint=fingerprint,
    )
    return soleaux.catalog.generation.catalog_generation_from_facts(
        generation=number,
        snapshot=snapshot,
        facts=soleaux.catalog.contracts.CatalogFacts(),
    )


def _hold_disk_generation(
    workspace_path: str,
    cache_path: str,
    fingerprint: str,
    ready_path: str,
    release_path: str,
) -> None:
    workspace = pathlib.Path(workspace_path)
    cache_root = pathlib.Path(cache_path)
    ready = pathlib.Path(ready_path)
    release = pathlib.Path(release_path)

    def child_cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return cache_root

    with unittest.mock.patch.object(
        platformdirs,
        "user_cache_path",
        child_cache_path,
    ):
        store = soleaux.catalog.store.CatalogStore(
            workspace,
            mode=soleaux.contracts.config.CatalogMode.DISK,
            retained_generations=1,
        )
        try:
            store.publish(
                _catalog_generation(
                    workspace,
                    fingerprint=fingerprint,
                    number=1,
                )
            )
            ready.touch()
            deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("catalog lease test release timed out")
                time.sleep(0.01)
        finally:
            store.close()


def _wait_for_ready(process: subprocess.Popen[str], ready: pathlib.Path) -> None:
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    while not ready.exists():
        returncode = process.poll()
        if returncode is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"catalog lease holder exited before readiness: {returncode}\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError("catalog lease holder readiness timed out")
        time.sleep(0.01)


def _lease_holder_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-disk-generation", action="store_true")
    parser.add_argument("--workspace")
    parser.add_argument("--cache")
    parser.add_argument("--fingerprint")
    parser.add_argument("--ready")
    parser.add_argument("--release")
    return parser


def _run_lease_holder() -> None:
    arguments = _lease_holder_parser().parse_args()
    if not arguments.hold_disk_generation:
        raise SystemExit("lease-holder mode is required")
    values = (
        arguments.workspace,
        arguments.cache,
        arguments.fingerprint,
        arguments.ready,
        arguments.release,
    )
    if not all(isinstance(value, str) and value for value in values):
        raise SystemExit("all lease-holder paths and identities are required")
    _hold_disk_generation(*values)


def test_disk_gc_bounds_aggregate_main_wal_and_shm_bytes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, directory = _configure_cache(tmp_path, monkeypatch)
    older = _write_generation(
        directory,
        "a" * 64,
        main_size=128 * 1024,
        wal_size=400 * 1024,
        shm_size=200 * 1024,
        modified_ns=1_000_000_000,
    )
    newer = _write_generation(
        directory,
        "b" * 64,
        main_size=128 * 1024,
        wal_size=400 * 1024,
        shm_size=200 * 1024,
        modified_ns=2_000_000_000,
    )
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=2,
        max_disk_size_mb=1,
    )

    store.open()
    store.close()

    assert all(path.is_file() for path in newer)
    assert not any(path.exists() for path in older)
    retained_bytes = sum(path.stat().st_size for path in directory.iterdir())
    assert retained_bytes <= _MEBIBYTE


def test_disk_gc_removes_only_orphaned_content_addressed_sidecars(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, directory = _configure_cache(tmp_path, monkeypatch)
    retained = _write_generation(
        directory,
        "a" * 64,
        main_size=1,
        wal_size=1,
        shm_size=1,
        modified_ns=1_000_000_000,
    )
    orphan_main = directory / f"{'b' * 64}.sqlite3"
    orphan_wal = pathlib.Path(f"{orphan_main}-wal")
    orphan_shm = pathlib.Path(f"{orphan_main}-shm")
    orphan_wal.write_bytes(b"wal")
    orphan_shm.write_bytes(b"shm")
    unrelated_sidecar = directory / "not-a-generation.sqlite3-wal"
    unrelated_sidecar.write_bytes(b"preserve")
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
    )

    store.open()
    store.close()

    assert all(path.is_file() for path in retained)
    assert orphan_wal.exists() is False
    assert orphan_shm.exists() is False
    assert unrelated_sidecar.read_bytes() == b"preserve"


def test_rejected_repository_local_implicit_disk_open_never_runs_gc(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def repository_cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return workspace / "platform-cache"

    monkeypatch.setattr(
        platformdirs,
        "user_cache_path",
        repository_cache_path,
    )
    directory = soleaux.catalog.store.catalog_database_path(workspace).parent
    directory.mkdir(parents=True)
    seeded_files = {
        directory / "unbound.sqlite3": b"preexisting-unbound",
        directory / f"{'a' * 64}.sqlite3": b"preexisting-a-main",
        directory / f"{'a' * 64}.sqlite3-wal": b"preexisting-a-wal",
        directory / f"{'a' * 64}.sqlite3-shm": b"preexisting-a-shm",
        directory / f"{'b' * 64}.sqlite3": b"preexisting-b-main",
    }
    for path, contents in seeded_files.items():
        path.write_bytes(contents)
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=1,
    )

    with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
        store.open()
    store.close()

    assert "disk catalog path must be outside the workspace" in str(raised.value)
    assert {path: path.read_bytes() for path in seeded_files} == seeded_files


def test_disk_gc_revalidates_its_deletion_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _directory = _configure_cache(tmp_path, monkeypatch)
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=1,
    )
    store.open()

    def repository_cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return workspace / "platform-cache"

    monkeypatch.setattr(
        platformdirs,
        "user_cache_path",
        repository_cache_path,
    )
    directory = soleaux.catalog.store.catalog_database_path(workspace).parent
    directory.mkdir(parents=True)
    seeded_files = {
        directory / "unbound.sqlite3": b"preexisting-unbound",
        directory / f"{'a' * 64}.sqlite3": b"preexisting-a-main",
        directory / f"{'a' * 64}.sqlite3-wal": b"preexisting-a-wal",
        directory / f"{'b' * 64}.sqlite3": b"preexisting-b-main",
    }
    for path, contents in seeded_files.items():
        path.write_bytes(contents)

    store.close()

    assert {path: path.read_bytes() for path in seeded_files} == seeded_files


def test_disk_gc_runs_during_live_fingerprint_publications_without_close(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, directory = _configure_cache(tmp_path, monkeypatch)
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=2,
    )
    fingerprints = tuple(character * 64 for character in ("a", "b", "c"))

    try:
        for number, fingerprint in enumerate(fingerprints, start=1):
            store.publish(
                _catalog_generation(
                    workspace,
                    fingerprint=fingerprint,
                    number=number,
                )
            )
            active_path = store.path
            assert active_path is not None
            assert active_path == directory / f"{fingerprint}.sqlite3"
            assert active_path.is_file()

        retained = {path.stem for path in directory.iterdir() if path.suffix == ".sqlite3"}
        assert len(retained) == 2
        assert fingerprints[-1] in retained
        assert store.metadata()["source_fingerprint"] == fingerprints[-1]
        active_path = store.path
        assert active_path is not None
        assert active_path.is_file()
    finally:
        store.close()


def test_disk_gc_never_unlinks_live_generation_that_exceeds_size_limit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, directory = _configure_cache(tmp_path, monkeypatch)
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=1,
        max_disk_size_mb=0,
    )
    fingerprint = "d" * 64

    try:
        store.publish(
            _catalog_generation(
                workspace,
                fingerprint=fingerprint,
                number=1,
            )
        )

        active_path = store.path
        assert active_path is not None
        assert active_path == directory / f"{fingerprint}.sqlite3"
        assert active_path.is_file()
        assert store.metadata()["source_fingerprint"] == fingerprint
    finally:
        store.close()


def test_disk_gc_skips_generation_leased_by_another_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, directory = _configure_cache(tmp_path, monkeypatch)
    leased_fingerprint = "e" * 64
    next_fingerprint = "f" * 64
    final_fingerprint = "0" * 64
    ready = tmp_path / "lease-holder.ready"
    release = tmp_path / "lease-holder.release"
    process = subprocess.Popen(
        (
            sys.executable,
            str(pathlib.Path(__file__).resolve()),
            "--hold-disk-generation",
            "--workspace",
            str(workspace),
            "--cache",
            str(directory.parents[1]),
            "--fingerprint",
            leased_fingerprint,
            "--ready",
            str(ready),
            "--release",
            str(release),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        retained_generations=1,
    )
    try:
        _wait_for_ready(process, ready)
        leased_main = directory / f"{leased_fingerprint}.sqlite3"
        leased_files = (
            leased_main,
            pathlib.Path(f"{leased_main}-wal"),
            pathlib.Path(f"{leased_main}-shm"),
        )
        assert all(path.is_file() for path in leased_files)

        store.publish(
            _catalog_generation(
                workspace,
                fingerprint=next_fingerprint,
                number=2,
            )
        )
        assert all(path.is_file() for path in leased_files)

        release.touch()
        assert process.wait(_PROCESS_TIMEOUT_SECONDS) == 0

        store.publish(
            _catalog_generation(
                workspace,
                fingerprint=final_fingerprint,
                number=3,
            )
        )
        assert not any(path.exists() for path in leased_files)
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(_PROCESS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(_PROCESS_TIMEOUT_SECONDS)
        store.close()


def test_disk_gc_failure_is_nonfatal_after_committed_publication(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _directory = _configure_cache(tmp_path, monkeypatch)
    fingerprint = "1" * 64
    store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
    )

    def fail_gc(_path: pathlib.Path) -> bool:
        raise PermissionError("simulated catalog GC failure")

    monkeypatch.setattr(
        soleaux.catalog.store,
        "_remove_disk_generation",
        fail_gc,
    )

    try:
        store.publish(
            _catalog_generation(
                workspace,
                fingerprint=fingerprint,
                number=1,
            )
        )

        assert store.metadata()["source_fingerprint"] == fingerprint
        active_path = store.path
        assert active_path is not None
        assert active_path.is_file()
    finally:
        store.close()


def test_unavailable_advisory_lock_backend_only_rejects_disk_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _directory = _configure_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(soleaux.catalog.store, "_fcntl", None)
    memory_store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.MEMORY,
    )
    disk_store = soleaux.catalog.store.CatalogStore(
        workspace,
        mode=soleaux.contracts.config.CatalogMode.DISK,
    )

    try:
        memory_fingerprint = "2" * 64
        memory_store.publish(
            _catalog_generation(
                workspace,
                fingerprint=memory_fingerprint,
                number=1,
            )
        )
        assert memory_store.metadata()["source_fingerprint"] == memory_fingerprint

        with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
            disk_store.open()
        assert str(raised.value) == (
            "disk catalog generation leases require an advisory-lock backend"
        )
    finally:
        memory_store.close()
        disk_store.close()


if __name__ == "__main__":
    _run_lease_holder()
