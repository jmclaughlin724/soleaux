"""RepositorySnapshot: deterministic frozen read sets with honest drift coverage."""

import asyncio
import pathlib
import subprocess
import sys
import unittest
import unittest.mock

import pytest

import soleaux.contracts.workspace
import soleaux.postgresql.runtime
import soleaux.structural.snapshot


def _workspace(root: pathlib.Path) -> soleaux.contracts.workspace.AllowedWorkspaceSet:
    return soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(root))], config_digest="d" * 64
    )


def _git(root: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("def tracked():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.py", ".gitignore")
    (tmp_path / "untracked.ts").write_text("export const x: number = 1;\n", encoding="utf-8")
    ignored = tmp_path / "node_modules" / "pkg"
    ignored.mkdir(parents=True)
    (ignored / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    return tmp_path


async def test_git_inventory_includes_tracked_and_untracked_not_ignored(
    git_repo: pathlib.Path,
) -> None:
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(git_repo).get(None))
    bundle = await snapshotter.capture()
    paths = [row.path for row in bundle.snapshot.files]
    assert paths == [".gitignore", "tracked.py", "untracked.ts"]
    assert bundle.snapshot.changed_during_analysis is False
    languages = {
        row.path: row.language for row in bundle.snapshot.files if row.path != ".gitignore"
    }
    assert languages == {
        "tracked.py": soleaux.structural.snapshot.LANGUAGE_BY_EXTENSION[".py"],
        "untracked.ts": soleaux.structural.snapshot.LANGUAGE_BY_EXTENSION[".ts"],
    }
    assert bundle.contents["tracked.py"].startswith(b"def tracked")


async def test_git_inventory_excludes_unlisted_host_state(
    git_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-reach-git")
    spawn = unittest.mock.AsyncMock(
        wraps=soleaux.structural.snapshot.asyncio.create_subprocess_exec
    )
    monkeypatch.setattr(soleaux.structural.snapshot.asyncio, "create_subprocess_exec", spawn)

    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(git_repo).get(None))
    await snapshotter.capture()

    environment = spawn.call_args.kwargs["env"]
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {},
        environment_names=(),
    )


async def test_cancelled_git_inventory_reaps_its_child_process(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_spawn = soleaux.structural.snapshot.asyncio.create_subprocess_exec
    child_created = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def spawn_blocked_inventory(
        *_args: object,
        **_kwargs: object,
    ) -> asyncio.subprocess.Process:
        child = await original_spawn(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        children.append(child)
        child_created.set()
        return child

    monkeypatch.setattr(
        soleaux.structural.snapshot.asyncio,
        "create_subprocess_exec",
        spawn_blocked_inventory,
    )
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    capture = asyncio.create_task(snapshotter.capture())
    await asyncio.wait_for(child_created.wait(), timeout=1)

    capture.cancel()
    with pytest.raises(asyncio.CancelledError):
        await capture

    assert len(children) == 1
    assert children[0].returncode is not None


async def test_capture_is_deterministic_across_identical_runs(git_repo: pathlib.Path) -> None:
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(git_repo).get(None))
    first = await snapshotter.capture()
    second = await snapshotter.capture()
    assert first.snapshot.source_fingerprint == second.snapshot.source_fingerprint
    assert [row.path for row in first.snapshot.files] == [row.path for row in second.snapshot.files]


async def test_capture_reads_each_admitted_source_once(
    git_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = pathlib.Path.read_bytes
    reads: dict[str, int] = {}

    def tracked(path: pathlib.Path) -> bytes:
        relative = path.relative_to(git_repo).as_posix()
        reads[relative] = reads.get(relative, 0) + 1
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", tracked)
    await soleaux.structural.snapshot.RepositorySnapshotter(
        _workspace(git_repo).get(None)
    ).capture()

    assert reads == {".gitignore": 1, "tracked.py": 1, "untracked.ts": 1}


async def test_fallback_walker_when_git_is_unavailable(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.js").write_text("y = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        soleaux.structural.snapshot.asyncio,
        "create_subprocess_exec",
        unittest.mock.AsyncMock(side_effect=FileNotFoundError),
    )
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    bundle = await snapshotter.capture()
    assert [row.path for row in bundle.snapshot.files] == ["src/a.py"]


async def test_binary_oversized_and_non_utf8_are_skipped_with_notes(tmp_path: pathlib.Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    (tmp_path / "big.txt").write_bytes(b"x" * 16)
    (tmp_path / "latin.txt").write_bytes(b"caf\xe9\n")
    (tmp_path / "ok.py").write_text("ok = True\n", encoding="utf-8")
    limits = soleaux.structural.snapshot.SnapshotLimits(max_file_bytes=12)
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(
        _workspace(tmp_path).get(None), limits
    )
    bundle = await snapshotter.capture()
    assert [row.path for row in bundle.snapshot.files] == ["ok.py"]
    assert any("binary" in note for note in bundle.notes)
    assert any("oversized" in note for note in bundle.notes)
    assert any("non-UTF-8" in note for note in bundle.notes)


async def test_empty_file_is_captured(tmp_path: pathlib.Path) -> None:
    (tmp_path / "empty.py").write_bytes(b"")
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    bundle = await snapshotter.capture()
    (row,) = bundle.snapshot.files
    assert row.byte_end == 0
    assert (row.end_line, row.end_column) == (0, 0)


async def test_symlink_escape_is_skipped(tmp_path: pathlib.Path) -> None:
    (tmp_path / "ok.py").write_text("ok = True\n", encoding="utf-8")
    (tmp_path / "escape").symlink_to(pathlib.Path("/etc/hosts"))
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    bundle = await snapshotter.capture()
    assert [row.path for row in bundle.snapshot.files] == ["ok.py"]
    assert any("escaping" in note for note in bundle.notes)


async def test_drift_yields_one_retry_then_changed_coverage(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    with unittest.mock.patch.object(
        soleaux.structural.snapshot.RepositorySnapshotter,
        "_fingerprint_matches",
        side_effect=[False, True],
    ):
        bundle = await snapshotter.capture()
    assert bundle.snapshot.changed_during_analysis is False
    with unittest.mock.patch.object(
        soleaux.structural.snapshot.RepositorySnapshotter,
        "_fingerprint_matches",
        side_effect=[False, False],
    ):
        drifted = await snapshotter.capture()
    assert drifted.snapshot.changed_during_analysis is True
    assert "read set changed during analysis" in drifted.notes


async def test_scoped_capture_selects_exact_paths(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 2\n", encoding="utf-8")
    snapshotter = soleaux.structural.snapshot.RepositorySnapshotter(_workspace(tmp_path).get(None))
    bundle = await snapshotter.capture(scope=("b.py",))
    assert [row.path for row in bundle.snapshot.files] == ["b.py"]
