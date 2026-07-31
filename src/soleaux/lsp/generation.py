"""SemanticGeneration snapshot barrier and reconciliation plan (D023)."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from soleaux.contracts.repository import content_digest
from soleaux.structural.snapshot import SnapshotBundle


class SemanticGenerationError(ValueError):
    """The frozen snapshot bundle violates its captured-file contract."""


class SemanticGenerationStatus(StrEnum):
    """Whether every requested semantic input is verified in the frozen bundle."""

    VERIFIED = "verified"
    UNVERIFIED_WORKSPACE_INPUTS = "unverified_workspace_inputs"


class GenerationInput(BaseModel):
    """One exact file identity admitted into a semantic generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class SemanticProjectIdentity(BaseModel):
    """The project/compiler/config boundary that owns one semantic session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str = Field(min_length=1)
    project_root: str
    project_config_digest: str = Field(min_length=64, max_length=64)
    compiler_identity: str = Field(min_length=1)

    @classmethod
    def fallback(
        cls,
        bundle: SnapshotBundle,
        *,
        provider_name: str,
        requested_file: str,
        control_paths: tuple[str, ...],
    ) -> Self:
        """Build a deterministic workspace project identity when no catalog route exists."""
        captured_by_path = {
            row.path: row.content_hash
            for row in bundle.snapshot.files
            if row.path in bundle.contents
        }
        controls = tuple((path, captured_by_path.get(path)) for path in sorted(set(control_paths)))
        project_root = _nearest_control_root(requested_file, tuple(path for path, _ in controls))
        project_id = f"{bundle.snapshot.workspace_id}:{project_root or '.'}"
        payload = json.dumps(
            {
                "project_id": project_id,
                "project_root": project_root,
                "controls": controls,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            project_id=project_id,
            project_root=project_root,
            project_config_digest=content_digest(payload),
            compiler_identity=f"{provider_name}:initialize",
        )


class SemanticGeneration(BaseModel):
    """Immutable identity for all workspace inputs visible to one semantic request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    provider_config_digest: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    project_root: str
    project_config_digest: str = Field(min_length=64, max_length=64)
    compiler_identity: str = Field(min_length=1)
    process_epoch: int = Field(ge=0)
    requested_file: str = Field(min_length=1)
    requested_hash: str | None
    dependencies: tuple[GenerationInput, ...]
    controls: tuple[GenerationInput, ...]
    missing_dependencies: tuple[str, ...]
    missing_controls: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    snapshot_changed_during_analysis: bool
    verification_issues: tuple[str, ...]
    status: SemanticGenerationStatus
    complete: bool
    fingerprint: str = Field(min_length=1)

    @classmethod
    def from_snapshot(
        cls,
        bundle: SnapshotBundle,
        *,
        provider_name: str,
        provider_config_digest: str,
        process_epoch: int,
        requested_file: str,
        dependency_paths: tuple[str, ...] = (),
        control_paths: tuple[str, ...] = (),
        project_identity: SemanticProjectIdentity | None = None,
    ) -> Self:
        """Build a generation solely from already captured bytes and hashes."""
        identity = project_identity or SemanticProjectIdentity.fallback(
            bundle,
            provider_name=provider_name,
            requested_file=requested_file,
            control_paths=control_paths,
        )
        captured_by_path = _captured_inputs(bundle)
        requested = captured_by_path.get(requested_file)
        requested_hash = requested.content_hash if requested is not None else None

        control_names = tuple(sorted(set(control_paths) - {requested_file}))
        dependency_names = tuple(
            sorted(set(dependency_paths) - set(control_names) - {requested_file})
        )
        dependencies, missing_dependencies = _partition_inputs(
            captured_by_path,
            dependency_names,
        )
        controls, missing_controls = _partition_inputs(captured_by_path, control_names)

        missing_inputs = tuple(
            sorted(
                {
                    *missing_dependencies,
                    *missing_controls,
                    *(() if requested is not None else (requested_file,)),
                }
            )
        )
        verification_issues = tuple(
            issue
            for issue, present in (
                ("missing_inputs", bool(missing_inputs)),
                (
                    "snapshot_changed_during_analysis",
                    bundle.snapshot.changed_during_analysis,
                ),
            )
            if present
        )
        complete = not verification_issues
        status = (
            SemanticGenerationStatus.VERIFIED
            if complete
            else SemanticGenerationStatus.UNVERIFIED_WORKSPACE_INPUTS
        )
        fingerprint = _generation_fingerprint(
            workspace_id=bundle.snapshot.workspace_id,
            provider_name=provider_name,
            provider_config_digest=provider_config_digest,
            project_id=identity.project_id,
            project_root=identity.project_root,
            project_config_digest=identity.project_config_digest,
            compiler_identity=identity.compiler_identity,
            process_epoch=process_epoch,
            requested_file=requested_file,
            requested_hash=requested_hash,
            dependencies=dependencies,
            controls=controls,
            missing_dependencies=missing_dependencies,
            missing_controls=missing_controls,
            snapshot_changed_during_analysis=bundle.snapshot.changed_during_analysis,
        )
        return cls(
            workspace_id=bundle.snapshot.workspace_id,
            provider_name=provider_name,
            provider_config_digest=provider_config_digest,
            project_id=identity.project_id,
            project_root=identity.project_root,
            project_config_digest=identity.project_config_digest,
            compiler_identity=identity.compiler_identity,
            process_epoch=process_epoch,
            requested_file=requested_file,
            requested_hash=requested_hash,
            dependencies=dependencies,
            controls=controls,
            missing_dependencies=missing_dependencies,
            missing_controls=missing_controls,
            missing_inputs=missing_inputs,
            snapshot_changed_during_analysis=bundle.snapshot.changed_during_analysis,
            verification_issues=verification_issues,
            status=status,
            complete=complete,
            fingerprint=fingerprint,
        )


class ReconciliationActionKind(StrEnum):
    """Protocol action required to advance one LSP session generation."""

    DID_CHANGE = "did_change"
    DID_CHANGE_WATCHED_FILES = "did_change_watched_files"
    RESTART = "restart"


class ReconciliationAction(BaseModel):
    """One deterministic protocol action over exact workspace-relative paths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ReconciliationActionKind
    paths: tuple[str, ...]
    reason: str = Field(min_length=1)


class GenerationReconciliationPlan(BaseModel):
    """The complete action plan between two immutable semantic generations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    before_fingerprint: str = Field(min_length=1)
    after_fingerprint: str = Field(min_length=1)
    actions: tuple[ReconciliationAction, ...]
    semantic_complete: bool
    missing_inputs: tuple[str, ...]


class SemanticGenerationBarrier:
    """Compute session reconciliation without reading the live workspace."""

    @staticmethod
    def plan_reconciliation(
        before: SemanticGeneration,
        after: SemanticGeneration,
        *,
        open_documents: frozenset[str],
        watched_files_supported: bool,
    ) -> GenerationReconciliationPlan:
        """Plan didChange, watched-file notification, or restart from exact hashes."""
        actions: tuple[ReconciliationAction, ...]
        session_identity_changed = (
            before.workspace_id != after.workspace_id
            or before.provider_name != after.provider_name
            or before.project_id != after.project_id
            or before.project_root != after.project_root
            or before.project_config_digest != after.project_config_digest
            or before.compiler_identity != after.compiler_identity
            or before.process_epoch != after.process_epoch
        )
        provider_config_changed = before.provider_config_digest != after.provider_config_digest
        changed_controls = _changed_paths(_control_hashes(before), _control_hashes(after))

        if session_identity_changed or provider_config_changed or changed_controls:
            reasons: list[str] = []
            if session_identity_changed:
                reasons.append("session identity changed")
            if provider_config_changed:
                reasons.append("provider configuration changed")
            if changed_controls:
                reasons.append("control files changed")
            actions = (
                ReconciliationAction(
                    kind=ReconciliationActionKind.RESTART,
                    paths=changed_controls,
                    reason=", ".join(reasons),
                ),
            )
        else:
            changed_inputs = _changed_paths(_semantic_hashes(before), _semantic_hashes(after))
            open_changes = tuple(path for path in changed_inputs if path in open_documents)
            unopened_changes = tuple(path for path in changed_inputs if path not in open_documents)
            if unopened_changes and not watched_files_supported:
                actions = (
                    ReconciliationAction(
                        kind=ReconciliationActionKind.RESTART,
                        paths=changed_inputs,
                        reason="changed unopened inputs cannot be notified",
                    ),
                )
            else:
                planned: list[ReconciliationAction] = []
                if open_changes:
                    planned.append(
                        ReconciliationAction(
                            kind=ReconciliationActionKind.DID_CHANGE,
                            paths=open_changes,
                            reason="open document hashes changed",
                        )
                    )
                if unopened_changes:
                    planned.append(
                        ReconciliationAction(
                            kind=ReconciliationActionKind.DID_CHANGE_WATCHED_FILES,
                            paths=unopened_changes,
                            reason="unopened workspace input hashes changed",
                        )
                    )
                actions = tuple(planned)

        return GenerationReconciliationPlan(
            before_fingerprint=before.fingerprint,
            after_fingerprint=after.fingerprint,
            actions=actions,
            semantic_complete=after.complete,
            missing_inputs=after.missing_inputs,
        )


def _captured_inputs(bundle: SnapshotBundle) -> dict[str, GenerationInput]:
    captured: dict[str, GenerationInput] = {}
    for row in bundle.snapshot.files:
        if row.path in captured:
            msg = f"duplicate captured-file path {row.path!r}"
            raise SemanticGenerationError(msg)
        content = bundle.contents.get(row.path)
        if content is None:
            continue
        actual_hash = content_digest(content)
        if actual_hash != row.content_hash:
            msg = f"captured bytes do not match snapshot hash for {row.path!r}"
            raise SemanticGenerationError(msg)
        captured[row.path] = GenerationInput(path=row.path, content_hash=row.content_hash)
    return captured


def _partition_inputs(
    captured: dict[str, GenerationInput],
    paths: tuple[str, ...],
) -> tuple[tuple[GenerationInput, ...], tuple[str, ...]]:
    present = tuple(captured[path] for path in paths if path in captured)
    missing = tuple(path for path in paths if path not in captured)
    return present, missing


def _generation_fingerprint(
    *,
    workspace_id: str,
    provider_name: str,
    provider_config_digest: str,
    project_id: str,
    project_root: str,
    project_config_digest: str,
    compiler_identity: str,
    process_epoch: int,
    requested_file: str,
    requested_hash: str | None,
    dependencies: tuple[GenerationInput, ...],
    controls: tuple[GenerationInput, ...],
    missing_dependencies: tuple[str, ...],
    missing_controls: tuple[str, ...],
    snapshot_changed_during_analysis: bool,
) -> str:
    payload = {
        "workspace_id": workspace_id,
        "provider_name": provider_name,
        "provider_config_digest": provider_config_digest,
        "project_id": project_id,
        "project_root": project_root,
        "project_config_digest": project_config_digest,
        "compiler_identity": compiler_identity,
        "process_epoch": process_epoch,
        "requested_file": requested_file,
        "requested_hash": requested_hash,
        "dependencies": [(item.path, item.content_hash) for item in dependencies],
        "controls": [(item.path, item.content_hash) for item in controls],
        "missing_dependencies": missing_dependencies,
        "missing_controls": missing_controls,
        "snapshot_changed_during_analysis": snapshot_changed_during_analysis,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return content_digest(canonical)


def _nearest_control_root(requested_file: str, control_paths: tuple[str, ...]) -> str:
    requested_parts = requested_file.split("/")
    candidates: list[str] = []
    for control_path in control_paths:
        parent, _, _name = control_path.rpartition("/")
        parent_parts = parent.split("/") if parent else []
        if requested_parts[: len(parent_parts)] == parent_parts:
            candidates.append(parent)
    return max(candidates, key=lambda value: (value.count("/"), len(value)), default="")


def _control_hashes(generation: SemanticGeneration) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {item.path: item.content_hash for item in generation.controls}
    hashes.update(dict.fromkeys(generation.missing_controls))
    return hashes


def _semantic_hashes(generation: SemanticGeneration) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {
        generation.requested_file: generation.requested_hash,
        **{item.path: item.content_hash for item in generation.dependencies},
    }
    hashes.update(dict.fromkeys(generation.missing_dependencies))
    return hashes


def _changed_paths(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> tuple[str, ...]:
    return tuple(
        path for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)
    )
