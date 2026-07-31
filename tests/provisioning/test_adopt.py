"""End-to-end adopt orchestration: detect → plan → apply → revert."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import types

import _assertions
import pytest

import soleaux.provisioning.adopt
import soleaux.provisioning.backup


def _seed_workspace(root: pathlib.Path) -> None:
    """Build a workspace with a Pylance selection and a competing .mcp.json entry."""
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text(
        '{"python.languageServer": "Pylance"}',
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"ast-grep": {"command": "ast-grep"}}}',
        encoding="utf-8",
    )


def test_detect_aggregates_findings_from_all_three_detectors(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)

    report = soleaux.provisioning.adopt.detect(tmp_path)

    assert any(d.language == "python" for d in report.editor_configs)
    assert any(d.name == "ast-grep" and d.competes for d in report.mcp_registrations)


def test_build_plan_default_targets_emits_editor_mcp_and_skips_providers_when_none(
    tmp_path: pathlib.Path,
) -> None:
    _seed_workspace(tmp_path)

    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    kinds = {a.kind for a in plan.actions}
    assert "disable_editor" in kinds
    assert "register_mcp" in kinds
    # No running LSPs detected → no emit_provider actions.
    assert "emit_provider" not in kinds


def test_postgres_process_produces_provider_adoption_output(tmp_path: pathlib.Path) -> None:
    process = types.SimpleNamespace(
        pid=5432,
        info={
            "name": "node",
            "cmdline": ["postgres-language-server", "lsp-proxy"],
            "cwd": str(tmp_path),
        },
    )
    report = soleaux.provisioning.adopt.detect(
        tmp_path,
        options=soleaux.provisioning.adopt.DetectOptions(process_iter=(process,)),
    )

    plan = soleaux.provisioning.adopt.build_plan(report, targets=("providers",))

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.kind == "emit_provider"
    assert action.language == "sql"
    assert action.provider == "postgres-language-server"
    assert action.description == (
        "Append `[providers.postgres-language-server]` block to soleaux.toml"
    )
    assert "[providers.postgres-language-server]" in soleaux.provisioning.adopt.render_plan(plan)


def test_build_plan_respects_targets_filter(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)

    plan = soleaux.provisioning.adopt.build_plan(
        soleaux.provisioning.adopt.detect(tmp_path), targets=("editor",)
    )

    assert all(a.kind == "disable_editor" for a in plan.actions)


def test_render_plan_lists_each_action_with_target(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)

    rendered = soleaux.provisioning.adopt.render_plan(
        soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))
    )

    assert "disable_editor" in rendered
    assert "register_mcp" in rendered
    assert ".vscode/settings.json" in rendered
    assert ".mcp.json" in rendered


def test_render_plan_handles_empty_plan() -> None:
    plan = soleaux.provisioning.adopt.AdoptionPlan(workspace_root="/x", actions=())

    rendered = soleaux.provisioning.adopt.render_plan(plan)

    assert "No adoption actions" in rendered


def test_apply_plan_writes_files_and_backs_up_changed_targets(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    result = soleaux.provisioning.adopt.apply_plan(plan)

    # Editor disable + 3 host registrations + guidance block = 5 writes
    assert len(result.written) == 5
    assert not result.skipped
    # Backups for original settings.json and .mcp.json
    backed_up_paths = {b.original_path for b in result.backups}
    assert ".vscode/settings.json" in backed_up_paths
    assert ".mcp.json" in backed_up_paths


def test_apply_plan_writes_the_gateway_guidance_block(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    soleaux.provisioning.adopt.apply_plan(plan)

    guidance = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert guidance.count("<!-- soleaux-gateway:start -->") == 1
    assert "soleaux mcp login" in guidance
    assert "soleaux.toml" in guidance


def test_guidance_block_prefers_an_existing_claude_md(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Workspace\n", encoding="utf-8")
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    soleaux.provisioning.adopt.apply_plan(plan)

    guidance = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert guidance.startswith("# Workspace\n")
    assert "<!-- soleaux-gateway:start -->" in guidance
    assert not (tmp_path / "AGENTS.md").exists()


def test_guidance_block_is_idempotent_and_replaces_between_markers(
    tmp_path: pathlib.Path,
) -> None:
    _seed_workspace(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Notes\n\n<!-- soleaux-gateway:start -->\nstale\n<!-- soleaux-gateway:end -->\n",
        encoding="utf-8",
    )
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))
    soleaux.provisioning.adopt.apply_plan(plan)

    first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "stale" not in first
    assert first.count("<!-- soleaux-gateway:start -->") == 1

    second_plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))
    result = soleaux.provisioning.adopt.apply_plan(second_plan)
    assert not any(w.startswith("write_guidance:") for w in result.written)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == first


def test_apply_plan_actually_disables_language_server(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    soleaux.provisioning.adopt.apply_plan(plan)

    settings = json.loads((tmp_path / ".vscode" / "settings.json").read_text())
    assert settings["python.languageServer"] is None


def test_apply_plan_actually_registers_soleaux_in_mcp_json(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))

    soleaux.provisioning.adopt.apply_plan(plan)

    data = json.loads((tmp_path / ".mcp.json").read_text())
    soleaux_entry = data["mcpServers"]["soleaux"]
    assert soleaux_entry["command"] == "uvx"
    assert soleaux_entry["args"] == ["soleaux"]
    # Pre-existing competitor preserved.
    assert "ast-grep" in data["mcpServers"]


def test_apply_plan_is_idempotent_on_second_run(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))
    soleaux.provisioning.adopt.apply_plan(plan)

    # Re-detect after the first apply; Pylance is now None and soleaux is healthy.
    second_report = soleaux.provisioning.adopt.detect(tmp_path)
    second_plan = soleaux.provisioning.adopt.build_plan(second_report)

    # No editor detection (Pylance already disabled); soleaux registrations
    # already healthy → skipped rather than written.
    editor_actions = [a for a in second_plan.actions if a.kind == "disable_editor"]
    assert editor_actions == []


def test_revert_restores_files_to_pre_apply_state(tmp_path: pathlib.Path) -> None:
    _seed_workspace(tmp_path)
    original_settings = (tmp_path / ".vscode" / "settings.json").read_text()
    plan = soleaux.provisioning.adopt.build_plan(soleaux.provisioning.adopt.detect(tmp_path))
    soleaux.provisioning.adopt.apply_plan(plan)

    restored = soleaux.provisioning.adopt.revert(tmp_path)

    assert ".vscode/settings.json" in restored
    assert ".mcp.json" in restored
    assert (tmp_path / ".vscode" / "settings.json").read_text() == original_settings


def _provider_action(target: pathlib.Path) -> soleaux.provisioning.adopt.AdoptionAction:
    return soleaux.provisioning.adopt.AdoptionAction(
        kind="emit_provider",
        description="Emit a Python provider",
        target_path=str(target),
        language="python",
        provider="pyright-langserver",
    )


def test_apply_plan_preflights_every_target_before_any_write(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    valid_target = workspace / "soleaux.toml"
    outside_target = tmp_path / "outside.toml"
    outside_target.write_text("outside\n", encoding="utf-8")
    traversal = workspace / ".." / outside_target.name
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(valid_target), _provider_action(traversal)),
    )

    with _assertions.raises_with_message(ValueError, "workspace"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert not valid_target.exists()
    assert outside_target.read_text(encoding="utf-8") == "outside\n"
    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_symlink_target_before_backup_or_write(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_target = tmp_path / "outside.toml"
    outside_target.write_text("outside\n", encoding="utf-8")
    target = workspace / "soleaux.toml"
    target.symlink_to(outside_target)
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(target),),
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert target.is_symlink()
    assert outside_target.read_text(encoding="utf-8") == "outside\n"
    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_symlink_parent_before_write(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / ".codex").symlink_to(outside, target_is_directory=True)
    target = workspace / ".codex" / "config.toml"
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(
            soleaux.provisioning.adopt.AdoptionAction(
                kind="register_mcp",
                description="Register Soleaux",
                target_path=str(target),
                language="host",
            ),
        ),
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert not (outside / "config.toml").exists()
    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_target_symlink_swap_after_admission(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "soleaux.toml"
    outside = tmp_path / "outside.toml"
    outside.write_text("outside\n", encoding="utf-8")
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(target),),
    )
    original_write = soleaux.provisioning.backup.WorkspaceIo.write_bytes_atomic
    swapped = False

    def race_write(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
        data: bytes,
        *,
        mode: int | None = None,
    ) -> None:
        nonlocal swapped
        if path.as_posix == "soleaux.toml" and not swapped:
            swapped = True
            target.symlink_to(outside)
        original_write(workspace_io, path, data, mode=mode)

    monkeypatch.setattr(
        soleaux.provisioning.backup.WorkspaceIo,
        "write_bytes_atomic",
        race_write,
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert target.is_symlink()
    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_parent_symlink_swap_after_admission(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = workspace / ".codex"
    parent.mkdir()
    detached = workspace / ".codex-detached"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "config.toml"
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(
            soleaux.provisioning.adopt.AdoptionAction(
                kind="register_mcp",
                description="Register Soleaux",
                target_path=str(target),
                language="host",
            ),
        ),
    )
    original_write = soleaux.provisioning.backup.WorkspaceIo.write_bytes_atomic
    swapped = False

    def race_write(
        workspace_io: soleaux.provisioning.backup.WorkspaceIo,
        path: soleaux.provisioning.backup.AdmittedPath,
        data: bytes,
        *,
        mode: int | None = None,
    ) -> None:
        nonlocal swapped
        if path.as_posix == ".codex/config.toml" and not swapped:
            swapped = True
            parent.rename(detached)
            parent.symlink_to(outside, target_is_directory=True)
        original_write(workspace_io, path, data, mode=mode)

    monkeypatch.setattr(
        soleaux.provisioning.backup.WorkspaceIo,
        "write_bytes_atomic",
        race_write,
    )

    with _assertions.raises_with_message(ValueError, "symlink"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert not (outside / "config.toml").exists()
    assert not (detached / "config.toml").exists()
    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_target_inside_backup_storage(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".soleaux-backups" / "provider.toml"
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(target),),
    )

    with _assertions.raises_with_message(ValueError, "backup storage"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert not (workspace / ".soleaux-backups").exists()


def test_apply_plan_rejects_case_variant_backup_storage_target(
    tmp_path: pathlib.Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".SOLEAUX-BACKUPS" / "provider.toml"
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(target),),
    )

    with _assertions.raises_with_message(ValueError, "backup storage"):
        soleaux.provisioning.adopt.apply_plan(plan)

    assert not (workspace / ".SOLEAUX-BACKUPS").exists()


def test_new_adoption_file_honors_process_umask(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "soleaux.toml"
    plan = soleaux.provisioning.adopt.AdoptionPlan(
        workspace_root=str(workspace),
        actions=(_provider_action(target),),
    )
    previous_umask = os.umask(0o027)
    try:
        soleaux.provisioning.adopt.apply_plan(plan)
    finally:
        os.umask(previous_umask)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o640
    assert mode & 0o022 == 0
