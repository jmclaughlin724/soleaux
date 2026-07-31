"""CLI subcommand: `soleaux adopt`."""

from __future__ import annotations

import asyncio
import collections.abc
import io
import pathlib
import sys
import typing

import soleaux.cli


def _seed_workspace(root: pathlib.Path) -> None:
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(
        '{"python.languageServer": "Pylance"}',
        encoding="utf-8",
    )


def _run_with_stderr(
    coro: collections.abc.Coroutine[typing.Any, typing.Any, int], err: io.StringIO
) -> int:
    """Run a CLI coroutine with sys.stderr swapped so the refusal path can write."""
    original = sys.stderr
    sys.stderr = err  # type: ignore[assignment]
    try:
        return asyncio.run(coro)
    finally:
        sys.stderr = original


def test_parser_accepts_adopt_subcommand() -> None:
    parser = soleaux.cli.create_parser()
    args = parser.parse_args(["adopt", "--dry-run"])
    assert args.command == "adopt"
    assert args.dry_run is True
    assert args.yes is False
    assert args.revert is False


def test_parser_accepts_target_choices() -> None:
    parser = soleaux.cli.create_parser()
    args = parser.parse_args(["adopt", "--target", "editor", "--target", "mcp"])
    assert args.target == ["editor", "mcp"]


def test_run_cli_dry_run_writes_nothing(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    out = io.StringIO()

    exit_code = _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt", "--dry-run"], stdout=out),
        io.StringIO(),
    )

    assert exit_code == 0
    # settings.json untouched.
    settings = (tmp_path / ".vscode" / "settings.json").read_text()
    assert "Pylance" in settings
    # No backup directory created.
    assert not (tmp_path / ".soleaux-backups").exists()
    assert "python.languageServer" in out.getvalue()


def test_run_cli_yes_writes_files_non_interactively(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    out = io.StringIO()

    exit_code = _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt", "--yes"], stdout=out),
        io.StringIO(),
    )

    assert exit_code == 0
    settings = (tmp_path / ".vscode" / "settings.json").read_text()
    assert "null" in settings
    assert "wrote:" in out.getvalue()


def test_run_cli_without_yes_refuses_when_not_a_tty(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    out = io.StringIO()
    err = io.StringIO()

    exit_code = _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt"], stdout=out),
        err,
    )

    assert exit_code == 1
    # No writes performed.
    assert "Pylance" in (tmp_path / ".vscode" / "settings.json").read_text()
    assert "Refusing to apply" in err.getvalue()


def test_run_cli_revert_restores_files(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    original = (tmp_path / ".vscode" / "settings.json").read_text()
    # First, apply.
    _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt", "--yes"], stdout=io.StringIO()),
        io.StringIO(),
    )
    # Now revert.
    out = io.StringIO()
    exit_code = _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt", "--revert"], stdout=out),
        io.StringIO(),
    )

    assert exit_code == 0
    assert (tmp_path / ".vscode" / "settings.json").read_text() == original
    assert "reverted" in out.getvalue()


def test_run_cli_revert_with_no_backups_returns_failure(tmp_path: pathlib.Path) -> None:
    err = io.StringIO()

    exit_code = _run_with_stderr(
        soleaux.cli.run_cli(["--root", str(tmp_path), "adopt", "--revert"], stdout=io.StringIO()),
        err,
    )

    assert exit_code == 1
    assert "No backups found" in err.getvalue()
