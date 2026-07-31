"""Zero-config startup and workspace authorization (AC03, D022)."""

import os
import subprocess
from pathlib import Path

import pytest
from fastmcp import Client

from soleaux.contracts.config import config_digest
from soleaux.contracts.workspace import (
    AllowedWorkspaceSet,
    RootEscapeError,
    TrustDigestMismatchError,
    UnauthorizedRootError,
)
from soleaux.server import mcp


def _launch(root: Path, workspace_id: str = "main") -> AllowedWorkspaceSet:
    return AllowedWorkspaceSet.from_launch(
        [(workspace_id, str(root))],
        config_digest=config_digest(b""),
    )


def test_construction_and_describe_spawn_no_child_processes() -> None:
    """Import/construct/describe start no worker and no language server."""
    result = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    descendants = [line for line in result.stdout.splitlines() if line.strip()]
    assert descendants == [], f"unexpected child processes: {descendants}"


async def test_about_read_creates_no_snapshot_or_inventory() -> None:
    async with Client(mcp) as client:
        contents = await client.read_resource("soleaux://about")
    assert contents


def test_single_root_selects_by_default(tmp_path: Path) -> None:
    workspaces = _launch(tmp_path)
    assert workspaces.get(None).workspace_id == "main"
    assert workspaces.workspace_ids == ("main",)


def test_multiple_roots_require_explicit_selection(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    workspaces = AllowedWorkspaceSet.from_launch(
        [("first", str(first)), ("second", str(second))],
        config_digest=config_digest(b""),
    )
    with pytest.raises(UnauthorizedRootError):
        workspaces.get(None)
    assert workspaces.get("second").root == second.resolve()


def test_unauthorized_readable_root_is_rejected(tmp_path: Path) -> None:
    workspaces = _launch(tmp_path)
    with pytest.raises(UnauthorizedRootError):
        workspaces.get("other")


def test_launch_root_rejections(tmp_path: Path) -> None:
    with pytest.raises(UnauthorizedRootError):
        _launch(tmp_path / "missing")
    with pytest.raises(UnauthorizedRootError):
        _launch(Path("file:///tmp"))
    with pytest.raises(UnauthorizedRootError):
        _launch(Path("/dev/null"))
    with pytest.raises(UnauthorizedRootError):
        _launch(tmp_path, workspace_id="bad\x00id")


def test_empty_readable_root_with_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    escape = root / "escape"
    escape.symlink_to(Path("/etc"))
    workspaces = _launch(root)
    with pytest.raises(RootEscapeError):
        workspaces.admit(workspaces.get(None), "escape")
    assert workspaces.admit(workspaces.get(None), ".") == root.resolve()


def test_admit_rejects_traversal_nul_and_absolute_escape(tmp_path: Path) -> None:
    workspaces = _launch(tmp_path)
    root = workspaces.get(None)
    with pytest.raises(RootEscapeError):
        workspaces.admit(root, "../outside")
    with pytest.raises(RootEscapeError):
        workspaces.admit(root, "a\x00b")
    with pytest.raises(RootEscapeError):
        workspaces.admit(root, "/etc/hosts")


def test_trust_digest_binds_the_exact_launch_root(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    digest = config_digest(b"config-bytes")
    workspaces = AllowedWorkspaceSet.from_launch(
        [("first", str(first)), ("second", str(second))],
        config_digest=digest,
    )
    expected = workspaces.get("first").trust_digest
    assert workspaces.verify_trust("first", expected).workspace_id == "first"
    with pytest.raises(TrustDigestMismatchError):
        workspaces.verify_trust("second", expected)
    with pytest.raises(TrustDigestMismatchError):
        workspaces.verify_trust("first", config_digest(b"other-bytes"))
