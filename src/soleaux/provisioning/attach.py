"""The attach orchestrator: detect → plan → consent → apply.

One re-runnable command owns the consumer-integration shape: host MCP
registrations, a starter ``soleaux.toml``, and the v2 per-repo deployment
document. The CLI subcommand is a thin adapter; this module owns the
workflow. Idempotent: a repo that is already attached plans no actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from soleaux.contracts.deployment import LOCAL_DEPLOYMENT_SCHEMA_V2
from soleaux.provisioning import backup
from soleaux.provisioning.contracts import (
    AdoptExtraMissingError,
    AttachAction,
    AttachPlan,
    AttachResult,
    BackupRecord,
)
from soleaux.provisioning.mcp_writer import render_registration
from soleaux.provisioning.soleaux_toml import render_soleaux_toml

_HOST_REGISTRATIONS: tuple[tuple[str, str], ...] = (
    (".mcp.json", "claude"),
    (".codex/config.toml", "codex"),
    ("opencode.json", "opencode"),
)
_DEPLOYMENT_FILENAME = "soleaux.deployment.json"


@dataclass(frozen=True)
class AttachOptions:
    """The launcher shape written into host registrations."""

    command: str = "uvx"
    args_prefix: tuple[str, ...] = ("soleaux",)
    shared: bool = False


def _ensure_extra_installed() -> None:
    """Raise AdoptExtraMissingError when the [adopt] extra is not importable."""
    import importlib.util

    missing = [name for name in ("json5", "tomlkit") if importlib.util.find_spec(name) is None]
    if missing:
        msg = (
            "soleaux attach requires the [adopt] extra. "
            f"Install with: pip install 'soleaux[adopt]' (missing: {', '.join(missing)})"
        )
        raise AdoptExtraMissingError(msg)


def _deployment_document(workspace_root: Path) -> dict[str, object]:
    return {
        "endpoint": "http://soleaux.local/mcp",
        "schema_version": LOCAL_DEPLOYMENT_SCHEMA_V2,
        "service_label": f"dev.soleaux.{workspace_root.name}",
        "socket_relative_path": f"Library/Caches/Soleaux/{workspace_root.name}.sock",
        "workspace_root": str(workspace_root),
    }


def build_plan(
    workspace_root: Path,
    *,
    options: AttachOptions | None = None,
) -> AttachPlan:
    """Derive a deterministic AttachPlan from the repo's current state."""
    _ensure_extra_installed()
    opts = options or AttachOptions()
    root = workspace_root.resolve()
    warnings: list[str] = []
    if opts.shared:
        warnings.append(
            "--shared is recorded but the machine registry ships with the shared-service "
            "stage; the v2 per-repo deployment document is written for now."
        )

    actions: list[AttachAction] = []
    for relative, host in _HOST_REGISTRATIONS:
        args = (*opts.args_prefix, "bridge", host)
        actions.append(
            AttachAction(
                kind="register_mcp",
                description=(
                    f"Register the soleaux bridge in {relative} ({opts.command} {' '.join(args)})"
                ),
                target_path=str(root / relative),
            )
        )

    if not (root / "soleaux.toml").is_file():
        actions.append(
            AttachAction(
                kind="write_soleaux_toml",
                description="Write a starter soleaux.toml from existing workspace configs",
                target_path=str(root / "soleaux.toml"),
            )
        )

    deployment_path = root / _DEPLOYMENT_FILENAME
    if not deployment_path.is_file():
        actions.append(
            AttachAction(
                kind="write_deployment",
                description=f"Write the v2 per-repo deployment document {_DEPLOYMENT_FILENAME}",
                target_path=str(deployment_path),
            )
        )

    return AttachPlan(
        workspace_root=str(root),
        command=opts.command,
        actions=tuple(actions),
        warnings=tuple(warnings),
    )


def render_plan(plan: AttachPlan) -> str:
    """Human-readable plan for the consent prompt."""
    if not plan.actions:
        return "No attach actions planned. Workspace is already attached."
    lines = [f"Planned attach actions for {plan.workspace_root}:", ""]
    for index, action in enumerate(plan.actions, start=1):
        lines.append(f"  {index}. [{action.kind}] {action.description}")
        lines.append(f"     target: {action.target_path}")
    return "\n".join(lines)


def apply_plan(
    plan: AttachPlan,
    *,
    options: AttachOptions | None = None,
    force: bool = False,
) -> AttachResult:
    """Apply one plan. A file is backed up only when a write actually occurs."""
    opts = options or AttachOptions()
    requested_root = Path(plan.workspace_root)
    host_by_path = {str(Path(plan.workspace_root) / rel): host for rel, host in _HOST_REGISTRATIONS}

    written: list[str] = []
    skipped: list[str] = []
    created: list[backup.AdmittedPath] = []
    backups: list[BackupRecord] = []
    backed_up: set[Path] = set()
    with backup.WorkspaceIo(requested_root) as workspace_io:

        def ensure_backup(target: backup.AdmittedPath) -> None:
            if target.relative in backed_up or not workspace_io.is_file(target):
                return
            backups.extend(backup._backup_files(workspace_io, [target]))
            backed_up.add(target.relative)

        for action in plan.actions:
            target = workspace_io.admit_target(
                Path(action.target_path),
                role="attach target path",
            )
            existed_before = workspace_io.is_file(target)
            changed = False
            if action.kind == "register_mcp":
                host = host_by_path.get(action.target_path)
                if host is None:
                    raise ValueError(f"attach action has no host mapping: {action.target_path}")
                snapshot = workspace_io.read_optional(target)
                rendered = render_registration(
                    target.relative.name,
                    snapshot.data if snapshot is not None else None,
                    force=force,
                    command=opts.command,
                    args=(*opts.args_prefix, "bridge", host),
                )
                if rendered is not None:
                    ensure_backup(target)
                    workspace_io.write_bytes_atomic(target, rendered)
                    changed = True
            elif action.kind == "write_soleaux_toml":
                if not workspace_io.is_file(target):
                    content = render_soleaux_toml(workspace_io.root)
                    workspace_io.write_bytes_atomic(target, content.encode("utf-8"))
                    changed = True
            elif action.kind == "write_deployment":
                if not workspace_io.is_file(target):
                    document = _deployment_document(workspace_io.root)
                    workspace_io.write_bytes_atomic(
                        target,
                        (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                    )
                    changed = True
            else:
                raise ValueError(f"unsupported attach action: {action.kind}")

            if changed:
                written.append(f"{action.kind}:{target.as_posix}")
                if not existed_before:
                    created.append(target)
            else:
                skipped.append(f"{action.kind}:{target.as_posix}")
        backup.record_created(workspace_io, created)
        workspace_root = workspace_io.root

    return AttachResult(
        workspace_root=str(workspace_root),
        backups=tuple(backups),
        created=tuple(path.as_posix for path in created),
        written=tuple(written),
        skipped=tuple(skipped),
    )
