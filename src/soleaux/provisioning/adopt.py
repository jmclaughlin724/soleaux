"""The adopt orchestrator: detect → plan → consent → apply.

The CLI subcommand is a thin adapter; this module owns the workflow.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from soleaux.provisioning import backup, detect_editor, detect_mcp, detect_processes
from soleaux.provisioning.contracts import (
    AdoptExtraMissingError,
    AdoptionAction,
    AdoptionPlan,
    AdoptionResult,
    BackupRecord,
    DetectionReport,
)
from soleaux.provisioning.editor_writer import render_disabled_editor_setting
from soleaux.provisioning.mcp_writer import render_registration

Target = Literal["editor", "mcp", "providers"]
_DEFAULT_TARGETS: tuple[Target, ...] = ("editor", "mcp", "providers")
_HOST_FILES: tuple[str, ...] = (".mcp.json", ".codex/config.toml", "opencode.json")


def _ensure_extra_installed() -> None:
    """Raise AdoptExtraMissingError when the [adopt] extra is not importable."""
    import importlib.util

    missing = [
        name for name in ("json5", "psutil", "tomlkit") if importlib.util.find_spec(name) is None
    ]
    if missing:
        msg = (
            "soleaux adopt requires the [adopt] extra. "
            f"Install with: pip install 'soleaux[adopt]' (missing: {', '.join(missing)})"
        )
        raise AdoptExtraMissingError(msg)


@dataclass(frozen=True)
class DetectOptions:
    """Optional injection point for tests."""

    process_iter: Any = None


class _Applier(Protocol):
    def __call__(
        self,
        workspace_io: backup.WorkspaceIo,
        target: backup.AdmittedPath,
        action: AdoptionAction,
        *,
        force: bool,
    ) -> bool: ...


def detect(workspace_root: Path, *, options: DetectOptions | None = None) -> DetectionReport:
    """Run every detector and return the aggregated read-only report."""
    _ensure_extra_installed()
    opts = options or DetectOptions()
    processes, warnings = detect_processes.detect_running_lsps(
        workspace_root,
        process_iter=opts.process_iter,
    )
    return DetectionReport(
        workspace_root=str(workspace_root),
        processes=processes,
        editor_configs=detect_editor.detect_editor_configs(workspace_root),
        mcp_registrations=detect_mcp.detect_mcp_registrations(workspace_root),
        warnings=warnings,
    )


def build_plan(
    report: DetectionReport,
    *,
    targets: Iterable[Target] = _DEFAULT_TARGETS,
    languages: Iterable[str] | None = None,
) -> AdoptionPlan:
    """Derive a deterministic AdoptionPlan from a DetectionReport."""
    target_set = set(targets)
    admitted = {lang.lower() for lang in languages} if languages else None
    workspace_root = Path(report.workspace_root)
    actions: list[AdoptionAction] = []

    def _keep(language: str) -> bool:
        return admitted is None or language.lower() in admitted

    if "editor" in target_set:
        for d in report.editor_configs:
            if not _keep(d.language):
                continue
            actions.append(
                AdoptionAction(
                    kind="disable_editor",
                    description=f"Set {d.key} = {d.disable_value} in {d.path}",
                    target_path=str(workspace_root / d.path),
                    language=d.language,
                    key=d.key,
                    value=d.disable_value,
                )
            )

    if "mcp" in target_set:
        for rel in _HOST_FILES:
            actions.append(
                AdoptionAction(
                    kind="register_mcp",
                    description=f"Register soleaux in {rel} via `uvx soleaux`",
                    target_path=str(workspace_root / rel),
                    language="host",
                )
            )

    if "providers" in target_set:
        seen: set[str] = set()
        for d in report.processes:
            if d.language in seen or not _keep(d.language):
                continue
            seen.add(d.language)
            actions.append(
                AdoptionAction(
                    kind="emit_provider",
                    description=f"Append `[providers.{d.provider}]` block to soleaux.toml",
                    target_path=str(workspace_root / "soleaux.toml"),
                    language=d.language,
                    provider=d.provider,
                )
            )

    actions.sort(key=lambda a: (a.kind, a.target_path, a.language))
    return AdoptionPlan(workspace_root=str(workspace_root), actions=tuple(actions))


def render_plan(plan: AdoptionPlan) -> str:
    """Human-readable plan for the consent prompt."""
    if not plan.actions:
        return "No adoption actions planned. Workspace already on soleaux."
    lines = [f"Planned adoption actions for {plan.workspace_root}:", ""]
    for i, action in enumerate(plan.actions, start=1):
        lines.append(f"  {i}. [{action.kind}] {action.description}")
        lines.append(f"     target: {action.target_path}")
    return "\n".join(lines)


def apply_plan(
    plan: AdoptionPlan,
    *,
    selected: Iterable[AdoptionAction] | None = None,
    force: bool = False,
) -> AdoptionResult:
    """Apply one plan (or a selected subset). Each file is backed up once."""
    requested_root = Path(plan.workspace_root)
    actions = list(selected) if selected is not None else list(plan.actions)

    written: list[str] = []
    skipped: list[str] = []
    backed_up: set[Path] = set()
    with backup.WorkspaceIo(requested_root) as workspace_io:
        prepared: list[tuple[AdoptionAction, backup.AdmittedPath, _Applier]] = []
        backup_targets: list[backup.AdmittedPath] = []
        for action in actions:
            applier = _APPLIERS.get(action.kind)
            if applier is None:
                raise ValueError(f"unsupported adoption action: {action.kind}")
            target = workspace_io.admit_target(
                Path(action.target_path),
                role="adoption target path",
            )
            if workspace_io.is_file(target) and target.relative not in backed_up:
                backup_targets.append(target)
                backed_up.add(target.relative)
            prepared.append((action, target, applier))

        backups: list[BackupRecord] = backup._backup_files(workspace_io, backup_targets)
        for action, target, applier in prepared:
            if applier(workspace_io, target, action, force=force):
                written.append(f"{action.kind}:{target.as_posix}")
            else:
                skipped.append(f"{action.kind}:{target.as_posix}")
        workspace_root = workspace_io.root

    return AdoptionResult(
        workspace_root=str(workspace_root),
        backups=tuple(backups),
        written=tuple(written),
        skipped=tuple(skipped),
    )


def _emit_provider_block(
    workspace_io: backup.WorkspaceIo,
    target: backup.AdmittedPath,
    action: AdoptionAction,
    *,
    force: bool,
) -> bool:
    """Append a commented ``[providers.<name>]`` block to soleaux.toml."""
    provider = action.provider or "tool-here"
    block = (
        f"# [providers.{provider}]\n"
        f"# Uncomment and edit to let soleaux drive {action.language} analysis:\n"
        f'# command = ["{provider}"]\n'
        f"# extensions = []\n"
        f"# enabled = true"
    )
    snapshot = workspace_io.read_optional(target)
    if snapshot is not None:
        existing = snapshot.data.decode("utf-8")
        if block in existing:
            return False
        block = existing.rstrip() + "\n\n" + block + "\n"
    else:
        block += "\n"
    workspace_io.write_bytes_atomic(target, block.encode("utf-8"))
    return True


def _disable_editor(
    workspace_io: backup.WorkspaceIo,
    target: backup.AdmittedPath,
    action: AdoptionAction,
    *,
    force: bool,
) -> bool:
    if action.key is None or action.value is None:
        return False
    snapshot = workspace_io.read_optional(target)
    rendered = render_disabled_editor_setting(
        snapshot.data if snapshot is not None else None,
        action.key,
        action.value,
    )
    if rendered is None:
        return False
    workspace_io.write_bytes_atomic(target, rendered)
    return True


def _register(
    workspace_io: backup.WorkspaceIo,
    target: backup.AdmittedPath,
    action: AdoptionAction,
    *,
    force: bool,
) -> bool:
    snapshot = workspace_io.read_optional(target)
    rendered = render_registration(
        target.relative.name,
        snapshot.data if snapshot is not None else None,
        force=force,
    )
    if rendered is None:
        return False
    workspace_io.write_bytes_atomic(target, rendered)
    return True


_APPLIERS: dict[str, _Applier] = {
    "disable_editor": _disable_editor,
    "register_mcp": _register,
    "emit_provider": _emit_provider_block,
}


def revert(workspace_root: Path) -> list[str]:
    """Restore the most recent set of backups."""
    return backup.restore(workspace_root)
