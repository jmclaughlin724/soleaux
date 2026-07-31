"""Detector unit tests."""

from __future__ import annotations

import pathlib

import soleaux.provisioning.detect_editor
import soleaux.provisioning.detect_mcp
import soleaux.provisioning.detect_processes

# ---------- detect_processes ----------


class _StubProcess:
    def __init__(self, pid: int, info: dict[str, object]) -> None:
        self.pid = pid
        self.info = info


def test_processes_match_pylance_cmdline_in_workspace(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            100,
            {
                "name": "pylance",
                "cmdline": ["pylance", "--stdio"],
                "cwd": str(tmp_path),
            },
        )
    ]

    detections, warnings = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert warnings == ()
    assert len(detections) == 1
    assert detections[0].pid == 100
    assert detections[0].language == "python"
    assert detections[0].provider == "pylance"


def test_processes_skip_when_cwd_outside_workspace(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            200,
            {
                "name": "rust-analyzer",
                "cmdline": ["rust-analyzer"],
                "cwd": "/other/path",
            },
        )
    ]

    detections, _ = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert detections == ()


def test_processes_warn_when_cwd_missing(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            300,
            {"name": "pyright-langserver", "cmdline": ["pyright-langserver"], "cwd": ""},
        )
    ]

    detections, warnings = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert detections == ()
    assert len(warnings) == 1
    assert "300" in warnings[0]


def test_processes_skip_unrelated_cmdlines(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            400,
            {"name": "python", "cmdline": ["python", "manage.py"], "cwd": str(tmp_path)},
        )
    ]

    detections, _ = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert detections == ()


def test_processes_match_typescript_language_server(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            500,
            {
                "name": "node",
                "cmdline": ["typescript-language-server", "--stdio"],
                "cwd": str(tmp_path / "subdir"),
            },
        )
    ]
    (tmp_path / "subdir").mkdir()

    detections, _ = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert len(detections) == 1
    assert detections[0].language == "typescript"


def test_processes_match_postgres_language_server(tmp_path: pathlib.Path) -> None:
    stub = [
        _StubProcess(
            5432,
            {
                "name": "node",
                "cmdline": ["postgres-language-server", "lsp-proxy"],
                "cwd": str(tmp_path),
            },
        )
    ]

    detections, warnings = soleaux.provisioning.detect_processes.detect_running_lsps(
        tmp_path, process_iter=stub
    )

    assert warnings == ()
    assert len(detections) == 1
    assert detections[0].language == "sql"
    assert detections[0].provider == "postgres-language-server"


# ---------- detect_editor ----------


def test_detect_editor_no_settings_returns_empty(tmp_path: pathlib.Path) -> None:
    assert soleaux.provisioning.detect_editor.detect_editor_configs(tmp_path) == ()


def test_detect_editor_picks_up_python_language_server(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"python.languageServer": "Pylance"}', encoding="utf-8")

    detections = soleaux.provisioning.detect_editor.detect_editor_configs(tmp_path)

    assert len(detections) == 1
    d = detections[0]
    assert d.key == "python.languageServer"
    assert d.disable_value == "None"
    assert d.language == "python"
    assert d.path == ".vscode/settings.json"


def test_detect_editor_skips_already_disabled(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"python.languageServer": "None"}', encoding="utf-8")

    detections = soleaux.provisioning.detect_editor.detect_editor_configs(tmp_path)

    assert detections == ()


def test_detect_editor_parses_json_with_comments(tmp_path: pathlib.Path) -> None:
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '// header comment\n{\n  "typescript.tsdk": "node_modules/typescript/lib"\n}\n',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_editor.detect_editor_configs(tmp_path)

    assert len(detections) == 1
    assert detections[0].language == "typescript"


# ---------- detect_mcp ----------


def test_detect_mcp_no_files_returns_empty(tmp_path: pathlib.Path) -> None:
    assert soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path) == ()


def test_detect_mcp_flags_competing_ast_grep(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"ast-grep": {"command": "ast-grep", "args": ["lsp"]}}}',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path)

    assert len(detections) == 1
    d = detections[0]
    assert d.host == ".mcp.json"
    assert d.name == "ast-grep"
    assert d.command == ("ast-grep", "lsp")
    assert d.competes is True


def test_detect_mcp_does_not_flag_existing_soleaux(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"soleaux": {"command": "uvx", "args": ["soleaux"]}}}',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path)

    assert len(detections) == 1
    assert detections[0].name == "soleaux"
    assert detections[0].competes is False


def test_detect_mcp_reads_codex_toml(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.pyright]\ncommand = "pyright-langserver"\nargs = ["--stdio"]\n',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path)

    assert len(detections) == 1
    d = detections[0]
    assert d.host == ".codex/config.toml"
    assert d.competes is True


def test_detect_mcp_reads_opencode_direct_local_server(tmp_path: pathlib.Path) -> None:
    (tmp_path / "opencode.json").write_text(
        '{"mcp":{"gopls":{"type":"local","command":["gopls"],"enabled":true}}}',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path)

    assert len(detections) == 1
    assert detections[0].host == "opencode.json"
    assert detections[0].name == "gopls"
    assert detections[0].command == ("gopls",)
    assert detections[0].competes is True


def test_detect_mcp_ignores_legacy_opencode_nested_servers(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "opencode.json").write_text(
        '{"mcp":{"servers":{"gopls":{"command":"gopls"}}}}',
        encoding="utf-8",
    )

    detections = soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path)

    assert detections == ()


def test_detect_mcp_handles_invalid_json(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".mcp.json").write_text("not json at all {{", encoding="utf-8")

    assert soleaux.provisioning.detect_mcp.detect_mcp_registrations(tmp_path) == ()
