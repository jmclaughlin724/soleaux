"""MCP writer tests across .mcp.json, .codex/config.toml, opencode.json."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tomllib

import pydantic
import pytest

import soleaux.provisioning.mcp_writer as mcp_writer

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _read_json(path: pathlib.Path) -> dict[str, pydantic.JsonValue]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_register_creates_mcp_json_entry(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".mcp.json"

    changed = mcp_writer.register_in_mcp_json(target)

    assert changed is True
    data = _read_json(target)
    servers = data.get("mcpServers")
    assert isinstance(servers, dict)
    soleaux_entry = servers.get("soleaux")
    assert isinstance(soleaux_entry, dict)
    assert soleaux_entry.get("command") == "uvx"
    args = soleaux_entry.get("args")
    assert isinstance(args, list)
    assert args == ["soleaux"]


def test_register_preserves_existing_servers_in_mcp_json(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".mcp.json"
    target.write_text(
        '{"mcpServers": {"other": {"command": "other-tool"}}}',
        encoding="utf-8",
    )

    mcp_writer.register_in_mcp_json(target)

    data = _read_json(target)
    servers = data.get("mcpServers")
    assert isinstance(servers, dict)
    assert "other" in servers
    assert "soleaux" in servers


def test_register_is_idempotent_when_healthy_soleaux_present(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".mcp.json"
    target.write_text(
        '{"mcpServers": {"soleaux": {"command": "uvx", "args": ["soleaux"]}}}',
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")

    changed = mcp_writer.register_in_mcp_json(target)

    assert changed is False
    assert target.read_text(encoding="utf-8") == original


def test_register_force_overwrites_unhealthy_soleaux(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".mcp.json"
    target.write_text(
        '{"mcpServers": {"soleaux": {"command": "pnpm", "args": ["soleaux:dev"]}}}',
        encoding="utf-8",
    )

    changed = mcp_writer.register_in_mcp_json(target, force=True)

    assert changed is True
    data = _read_json(target)
    servers = data.get("mcpServers")
    assert isinstance(servers, dict)
    soleaux_entry = servers.get("soleaux")
    assert isinstance(soleaux_entry, dict)
    assert soleaux_entry.get("command") == "uvx"


def test_register_codex_writes_validatable_toml(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".codex" / "config.toml"

    changed = mcp_writer.register_in_codex_config(target)

    assert changed is True
    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    soleaux_entry = parsed["mcp_servers"]["soleaux"]
    assert soleaux_entry["command"] == "uvx"
    assert soleaux_entry["args"] == ["soleaux"]
    assert soleaux_entry["enabled"] is True
    # MCP host approval is configured independently by the client.
    assert "default_tools_approval_mode" not in soleaux_entry


def test_register_codex_scrubs_stale_approval_gate(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text(
        '[mcp_servers.soleaux]\ncommand = "uvx"\nargs = ["soleaux"]\n'
        'default_tools_approval_mode = "prompt"\n',
        encoding="utf-8",
    )

    changed = mcp_writer.register_in_codex_config(target)

    assert changed is True
    parsed = tomllib.loads(target.read_text(encoding="utf-8"))
    assert "default_tools_approval_mode" not in parsed["mcp_servers"]["soleaux"]


def test_register_codex_preserves_comments(tmp_path: pathlib.Path) -> None:
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir()
    target.write_text(
        '# user comment\n[mcp_servers.existing]\ncommand = "other"\n',
        encoding="utf-8",
    )

    mcp_writer.register_in_codex_config(target)

    text = target.read_text(encoding="utf-8")
    assert "# user comment" in text
    assert "existing" in text


def test_register_opencode_writes_direct_local_server(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "opencode.json"

    mcp_writer.register_in_opencode_json(target)

    data = _read_json(target)
    mcp = data.get("mcp")
    assert isinstance(mcp, dict)
    assert "servers" not in mcp
    soleaux_entry = mcp.get("soleaux")
    assert isinstance(soleaux_entry, dict)
    assert soleaux_entry == {
        "command": ["uvx", "soleaux"],
        "enabled": True,
        "type": "local",
    }


def test_register_opencode_preserves_unrelated_config(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "opencode.json"
    target.write_text(
        json.dumps(
            {
                "instructions": ["AGENTS.md"],
                "mcp": {
                    "other": {
                        "type": "remote",
                        "url": "https://example.com/mcp",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    mcp_writer.register_in_opencode_json(target)

    data = _read_json(target)
    assert data["instructions"] == ["AGENTS.md"]
    mcp = data.get("mcp")
    assert isinstance(mcp, dict)
    assert mcp["other"] == {
        "type": "remote",
        "url": "https://example.com/mcp",
    }
    assert "soleaux" in mcp


def test_register_opencode_migrates_only_legacy_soleaux_entry(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "opencode.json"
    target.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "other": {"command": "other-tool"},
                        "soleaux": {"command": "uvx", "args": ["soleaux"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    changed = mcp_writer.register_in_opencode_json(target)

    assert changed is True
    data = _read_json(target)
    mcp = data.get("mcp")
    assert isinstance(mcp, dict)
    assert mcp["servers"] == {"other": {"command": "other-tool"}}
    assert mcp["soleaux"] == {
        "command": ["uvx", "soleaux"],
        "enabled": True,
        "type": "local",
    }


def test_register_opencode_is_idempotent_for_direct_local_server(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "opencode.json"
    target.write_text(
        '{"mcp":{"soleaux":{"type":"local","command":["uvx","soleaux"],"enabled":true}}}',
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")

    changed = mcp_writer.register_in_opencode_json(target)

    assert changed is False
    assert target.read_text(encoding="utf-8") == original


def test_register_opencode_output_passes_installed_parser(
    tmp_path: pathlib.Path,
) -> None:
    executable = shutil.which("opencode")
    if executable is None:
        pytest.skip("OpenCode is required for its authoritative config parser")
    target = tmp_path / "opencode.json"
    mcp_writer.register_in_opencode_json(target)
    parser_state = tmp_path / "opencode-state"
    parser_state.mkdir()

    result = subprocess.run(
        (executable, "debug", "config", "--pure"),
        cwd=_REPOSITORY_ROOT,
        env={
            "HOME": str(parser_state),
            "OPENCODE_CONFIG_CONTENT": target.read_text(encoding="utf-8"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "PATH": os.environ.get("PATH", os.defpath),
            "XDG_CACHE_HOME": str(parser_state),
            "XDG_CONFIG_HOME": str(parser_state),
        },
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    soleaux_entry = parsed["mcp"]["soleaux"]
    assert soleaux_entry["command"] == ["uvx", "soleaux"]
    assert soleaux_entry["enabled"] is True
    assert soleaux_entry["type"] == "local"


def test_register_dispatches_on_filename(tmp_path: pathlib.Path) -> None:
    mcp_path = tmp_path / ".mcp.json"

    changed = mcp_writer.register(mcp_path)

    assert changed is True
    assert mcp_path.is_file()


def test_register_dispatch_returns_false_for_unknown_filename(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "unknown.txt"

    assert mcp_writer.register(target) is False
