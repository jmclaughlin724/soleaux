"""Attach workflow: planning, idempotent apply, dry-run, extras, backups."""

from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest
from _assertions import raises_with_message

from soleaux.provisioning import attach as attach_mod
from soleaux.provisioning.contracts import AdoptExtraMissingError


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _codex_servers(root: Path) -> dict[str, object]:
    config = root / ".codex" / "config.toml"
    if not config.is_file():
        return {}
    return dict(tomllib.loads(config.read_text(encoding="utf-8")).get("mcp_servers", {}))


def test_plan_on_empty_repo_covers_every_artifact(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    plan = attach_mod.build_plan(root)

    kinds = [action.kind for action in plan.actions]
    assert kinds == [
        "register_mcp",
        "register_mcp",
        "register_mcp",
        "write_soleaux_toml",
        "write_deployment",
    ]
    assert ".mcp.json" in plan.actions[0].target_path
    assert ".codex/config.toml" in plan.actions[1].target_path
    assert "opencode.json" in plan.actions[2].target_path


def test_apply_is_idempotent_and_registers_the_bridge_shape(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    options = attach_mod.AttachOptions(command="uvx", args_prefix=("soleaux",))

    first = attach_mod.apply_plan(attach_mod.build_plan(root, options=options), options=options)
    assert len(first.written) == 5
    assert not first.skipped

    mcp_json = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    entry = mcp_json["mcpServers"]["soleaux"]
    assert entry["command"] == "uvx"
    assert entry["args"] == ["soleaux", "bridge", "claude"]

    codex = _codex_servers(root)
    soleaux_entry = codex["soleaux"]
    assert isinstance(soleaux_entry, dict)
    assert soleaux_entry["command"] == "uvx"
    assert soleaux_entry["args"] == ["soleaux", "bridge", "codex"]
    assert soleaux_entry["enabled"] is True
    # Command shape only: attach never writes approval-mode keys.
    assert "default_tools_approval_mode" not in soleaux_entry
    assert "tools" not in soleaux_entry

    opencode = json.loads((root / "opencode.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["soleaux"]["command"] == ["uvx", "soleaux", "bridge", "opencode"]

    assert (root / "soleaux.toml").is_file()
    deployment = json.loads((root / "soleaux.deployment.json").read_text(encoding="utf-8"))
    assert deployment["schema_version"] == "soleaux.local-deployment/v2"
    assert deployment["service_label"] == f"dev.soleaux.{root.name}"
    assert deployment["workspace_root"] == str(root.resolve())

    second = attach_mod.apply_plan(attach_mod.build_plan(root, options=options), options=options)
    assert second.written == ()
    assert len(second.skipped) == 3  # only the three registrations are re-rendered
    assert second.backups == ()


def test_plan_skips_existing_soleaux_toml_and_deployment(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "soleaux.toml").write_text("# mine\n", encoding="utf-8")
    (root / "soleaux.deployment.json").write_text("{}\n", encoding="utf-8")

    plan = attach_mod.build_plan(root)

    assert [action.kind for action in plan.actions] == ["register_mcp"] * 3
    attach_mod.apply_plan(plan)
    assert (root / "soleaux.toml").read_text(encoding="utf-8") == "# mine\n"
    assert (root / "soleaux.deployment.json").read_text(encoding="utf-8") == "{}\n"


def test_apply_backs_up_preexisting_host_files_once(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    codex_dir = root / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text("[mcp_servers]\n", encoding="utf-8")

    result = attach_mod.apply_plan(attach_mod.build_plan(root))

    backed_up = {record.original_path for record in result.backups}
    assert backed_up == {".mcp.json", ".codex/config.toml"}
    assert "soleaux" in json.loads((root / ".mcp.json").read_text())["mcpServers"]
    assert "soleaux" in _codex_servers(root)


def test_attach_refuses_when_the_extra_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    def missing_spec(name: str) -> None:
        return None

    monkeypatch.setattr(importlib.util, "find_spec", missing_spec)

    with pytest.raises(AdoptExtraMissingError):
        attach_mod.build_plan(_repo(tmp_path))


def test_shared_records_a_warning_and_still_writes_v2(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    options = attach_mod.AttachOptions(shared=True)

    plan = attach_mod.build_plan(root, options=options)

    assert plan.warnings
    assert "machine registry" in plan.warnings[0]
    assert any(action.kind == "write_deployment" for action in plan.actions)


def test_unknown_action_kind_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    plan = attach_mod.build_plan(root)
    bogus = plan.actions[0].model_copy(update={"kind": "explode"})
    plan = plan.model_copy(update={"actions": (bogus,)})

    with raises_with_message(ValueError, "unsupported attach action"):
        attach_mod.apply_plan(plan)


def test_render_plan_reports_already_attached(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    attach_mod.apply_plan(attach_mod.build_plan(root))
    plan = attach_mod.build_plan(root)

    # Registrations still appear in the plan (apply skips them); only the
    # one-shot artifacts vanish.
    assert all(action.kind == "register_mcp" for action in plan.actions)

    empty_plan = plan.model_copy(update={"actions": ()})
    assert (
        attach_mod.render_plan(empty_plan)
        == "No attach actions planned. Workspace is already attached."
    )


async def test_cli_attach_dry_run_and_apply(tmp_path: Path) -> None:
    from soleaux.cli import run_cli

    root = _repo(tmp_path)
    dry_out = io.StringIO()
    assert await run_cli(["attach", "--repo", str(root), "--dry-run", "--yes"], stdout=dry_out) == 0
    assert "Planned attach actions" in dry_out.getvalue()
    assert not (root / ".mcp.json").exists()

    out = io.StringIO()
    assert await run_cli(["attach", "--repo", str(root), "--yes"], stdout=out) == 0
    text = out.getvalue()
    assert "wrote: register_mcp" in text
    assert "MCP configs are consistent" in text
    assert "soleaux --root" in text and "check mcp" in text
