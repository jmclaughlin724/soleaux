"""Keyed reusable LSP sessions behind the SemanticGeneration barrier (D023)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from soleaux.analysis.task_registry import TaskRegistry
from soleaux.contracts.budget import LspSessionBudget
from soleaux.contracts.repository import RepositoryPath, content_digest
from soleaux.lsp.broker import LspBroker, LspBrokerError
from soleaux.lsp.contracts import (
    EditorSessionContext,
    LanguageServerSpec,
    TextDocumentSyncKind,
)
from soleaux.lsp.generation import (
    ReconciliationActionKind,
    SemanticGeneration,
    SemanticGenerationBarrier,
    SemanticProjectIdentity,
)
from soleaux.lsp.providers import ConfiguredProvider
from soleaux.structural.snapshot import SnapshotBundle

_MAX_RESTART_BACKOFF_SECONDS = 1.0
_INITIAL_RESTART_BACKOFF_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class SessionBaseKey:
    """Stable session identity before the process epoch is applied."""

    workspace_id: str
    provider_name: str
    provider_config_digest: str
    project_id: str
    project_root: str
    project_config_digest: str
    compiler_identity: str


@dataclass(slots=True)
class _WorkspaceBoundary:
    workspace_id: str
    root: Path


@dataclass(slots=True)
class _SessionRecord:
    epoch: int
    broker: LspBroker
    generations: dict[str, SemanticGeneration] = field(
        default_factory=dict[str, SemanticGeneration]
    )
    open_documents: set[str] = field(default_factory=set[str])


class LspSessionManager:
    """Own keyed process reuse, reconciliation, restart epochs, and shared requests."""

    def __init__(self, budget: LspSessionBudget | None = None) -> None:
        self._budget = budget or LspSessionBudget()
        self._records: dict[SessionBaseKey, _SessionRecord] = {}
        self._epochs: dict[SessionBaseKey, int] = {}
        self._failure_counts: dict[SessionBaseKey, int] = {}
        self._session_locks: dict[SessionBaseKey, asyncio.Lock] = {}
        self._starts = TaskRegistry()
        self._reconciliations = TaskRegistry()
        self._requests = TaskRegistry()

    @property
    def active_session_count(self) -> int:
        """Number of initialized provider processes owned by this manager."""
        return len(self._records)

    @property
    def active_provider_pids(self) -> tuple[int, ...]:
        """Return the PIDs currently owned by initialized provider sessions."""
        return tuple(
            pid for record in self._records.values() if (pid := record.broker.pid) is not None
        )

    @property
    def pending_request_count(self) -> int:
        """Return the response slots retained by all active brokers."""
        return sum(record.broker.pending_request_count for record in self._records.values())

    @property
    def in_flight_task_count(self) -> int:
        """Return all shared start, reconciliation, and request tasks."""
        return self._starts.in_flight + self._reconciliations.in_flight + self._requests.in_flight

    @staticmethod
    def base_key(
        *,
        workspace_id: str,
        provider: ConfiguredProvider,
        project_identity: SemanticProjectIdentity | None = None,
    ) -> SessionBaseKey:
        """Build the full provider/project/compiler/config session key."""
        identity = project_identity or SemanticProjectIdentity(
            project_id=f"{workspace_id}:.",
            project_root="",
            project_config_digest=content_digest(provider.config_digest.encode("utf-8")),
            compiler_identity=f"{provider.provider_name}:initialize",
        )
        return SessionBaseKey(
            workspace_id=workspace_id,
            provider_name=provider.provider_name,
            provider_config_digest=provider.config_digest,
            project_id=identity.project_id,
            project_root=identity.project_root,
            project_config_digest=identity.project_config_digest,
            compiler_identity=identity.compiler_identity,
        )

    @staticmethod
    def key_for_generation(generation: SemanticGeneration) -> SessionBaseKey:
        """Rebuild the exact pre-epoch session identity carried by a generation."""
        return SessionBaseKey(
            workspace_id=generation.workspace_id,
            provider_name=generation.provider_name,
            provider_config_digest=generation.provider_config_digest,
            project_id=generation.project_id,
            project_root=generation.project_root,
            project_config_digest=generation.project_config_digest,
            compiler_identity=generation.compiler_identity,
        )

    def keys_for_provider(
        self,
        *,
        workspace_id: str,
        provider: ConfiguredProvider,
    ) -> tuple[SessionBaseKey, ...]:
        """Return every known project session for one configured provider."""
        keys = set(self._records).union(self._epochs)
        return tuple(
            sorted(
                (
                    key
                    for key in keys
                    if key.workspace_id == workspace_id
                    and key.provider_name == provider.provider_name
                    and key.provider_config_digest == provider.config_digest
                ),
                key=lambda key: (
                    key.project_root,
                    key.project_id,
                    key.project_config_digest,
                    key.compiler_identity,
                ),
            )
        )

    def process_epoch(self, key: SessionBaseKey) -> int:
        """Current process epoch for generation construction."""
        return self._epochs.get(key, 0)

    def is_running(self, key: SessionBaseKey) -> bool:
        """Whether the selected provider currently owns a live session."""
        return key in self._records

    def editor_context(self, generation: SemanticGeneration) -> EditorSessionContext:
        """Return immutable edit-validation state for an exact generation."""
        key = self.key_for_generation(generation)
        record = self._records.get(key)
        if record is None or record.epoch != generation.process_epoch:
            raise ValueError("semantic provider generation is no longer active")
        capabilities = record.broker.capabilities
        if capabilities is None:
            raise ValueError("semantic provider has no negotiated capabilities")
        return EditorSessionContext(
            workspace_id=key.workspace_id,
            provider_name=key.provider_name,
            provider_config_digest=key.provider_config_digest,
            project_id=key.project_id,
            project_root=key.project_root,
            project_config_digest=key.project_config_digest,
            compiler_identity=key.compiler_identity,
            process_epoch=record.epoch,
            position_encoding=capabilities.position_encoding,
            document_versions=record.broker.document_versions,
        )

    async def prepare(
        self,
        *,
        provider: ConfiguredProvider,
        spec: LanguageServerSpec,
        generation: SemanticGeneration,
        bundle: SnapshotBundle,
    ) -> LspBroker | None:
        """Start lazily and reconcile the exact generation.

        A `None` result means reconciliation restarted the provider. The caller
        must rebuild the generation with the manager's new process epoch.
        """
        key = self.key_for_generation(generation)
        if (
            generation.provider_name != key.provider_name
            or generation.provider_config_digest != key.provider_config_digest
            or provider.provider_name != key.provider_name
            or provider.config_digest != key.provider_config_digest
        ):
            msg = "generation provider identity does not match the configured provider"
            raise ValueError(msg)
        await self._retire_superseded_project_sessions(key)
        reconciliation_key = (
            key,
            generation.process_epoch,
            generation.fingerprint,
            "reconcile-v1",
        )
        return await self._reconciliations.share(
            reconciliation_key,
            lambda: self._prepare_once(
                key=key,
                provider=provider,
                spec=spec,
                generation=generation,
                bundle=bundle,
            ),
        )

    async def _retire_superseded_project_sessions(self, current: SessionBaseKey) -> None:
        """Close prior compiler/config identities for the same logical project."""
        superseded = tuple(
            key
            for key in self._records
            if key != current
            and key.workspace_id == current.workspace_id
            and key.provider_name == current.provider_name
            and key.project_id == current.project_id
        )
        for key in superseded:
            lock = self._session_locks.setdefault(key, asyncio.Lock())
            async with lock:
                if key in self._records:
                    await self._restart_unlocked(key)

    async def _prepare_once(
        self,
        *,
        key: SessionBaseKey,
        provider: ConfiguredProvider,
        spec: LanguageServerSpec,
        generation: SemanticGeneration,
        bundle: SnapshotBundle,
    ) -> LspBroker | None:
        lock = self._session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if generation.process_epoch != self.process_epoch(key):
                return None
            broker = await self._acquire(key=key, spec=spec, epoch=generation.process_epoch)
            record = self._records[key]
            self._prune_evicted_documents(
                record,
                workspace_id=key.workspace_id,
                provider=provider,
                broker=broker,
            )
            content = bundle.contents.get(generation.requested_file)
            if content is None:
                msg = f"requested document {generation.requested_file!r} is not captured"
                raise ValueError(msg)
            uri = _document_uri(key.workspace_id, provider.root, generation.requested_file)
            current_version = broker.document_versions.get(uri, 0)
            expected_version = current_version or 1
            previous = record.generations.get(generation.requested_file)
            if previous is not None:
                watched_files_supported = bool(
                    broker.registrations_by_method("workspace/didChangeWatchedFiles")
                )
                plan = SemanticGenerationBarrier.plan_reconciliation(
                    previous,
                    generation,
                    open_documents=frozenset(record.open_documents),
                    watched_files_supported=watched_files_supported,
                )
                if any(action.kind is ReconciliationActionKind.RESTART for action in plan.actions):
                    await self._restart_unlocked(key)
                    return None
                if any(
                    action.kind is ReconciliationActionKind.DID_CHANGE
                    and generation.requested_file in action.paths
                    for action in plan.actions
                ):
                    expected_version = current_version + 1
                broker.bind_diagnostic_generation(
                    uri,
                    document_version=expected_version,
                    generation_fingerprint=generation.fingerprint,
                )
                capabilities = broker.capabilities
                for action in plan.actions:
                    if (
                        action.kind is ReconciliationActionKind.DID_CHANGE
                        and capabilities is not None
                        and capabilities.text_document_sync is TextDocumentSyncKind.NONE
                    ):
                        await self._restart_unlocked(key)
                        return None
                    await self._apply_action(
                        action.kind,
                        action.paths,
                        provider=provider,
                        broker=broker,
                        bundle=bundle,
                        workspace_id=key.workspace_id,
                    )
            else:
                broker.bind_diagnostic_generation(
                    uri,
                    document_version=expected_version,
                    generation_fingerprint=generation.fingerprint,
                )
            if generation.requested_file not in record.open_documents:
                await broker.open_document(uri, spec.language, content.decode("utf-8"))
                record.open_documents.add(generation.requested_file)
                self._prune_evicted_documents(
                    record,
                    workspace_id=key.workspace_id,
                    provider=provider,
                    broker=broker,
                )
            document_version = broker.document_versions.get(uri)
            if document_version is None:
                raise ValueError(f"requested document {generation.requested_file!r} is not open")
            broker.bind_diagnostic_generation(
                uri,
                document_version=document_version,
                generation_fingerprint=generation.fingerprint,
            )
            record.generations[generation.requested_file] = generation
            return broker

    @staticmethod
    def _prune_evicted_documents(
        record: _SessionRecord,
        *,
        workspace_id: str,
        provider: ConfiguredProvider,
        broker: LspBroker,
    ) -> None:
        live_uris = broker.open_document_uris
        record.open_documents.intersection_update(
            path
            for path in record.open_documents
            if _document_uri(workspace_id, provider.root, path) in live_uris
        )

    async def _apply_action(
        self,
        kind: ReconciliationActionKind,
        paths: tuple[str, ...],
        *,
        provider: ConfiguredProvider,
        broker: LspBroker,
        bundle: SnapshotBundle,
        workspace_id: str,
    ) -> None:
        if kind is ReconciliationActionKind.DID_CHANGE:
            for path in paths:
                content = bundle.contents.get(path)
                if content is None:
                    msg = f"changed open document {path!r} is not captured"
                    raise ValueError(msg)
                await broker.update_document(
                    _document_uri(workspace_id, provider.root, path),
                    content.decode("utf-8"),
                )
            return
        if kind is ReconciliationActionKind.DID_CHANGE_WATCHED_FILES:
            changes = [
                {
                    "uri": _document_uri(workspace_id, provider.root, path),
                    "type": 2 if path in bundle.contents else 3,
                }
                for path in paths
            ]
            await broker.send_notification(
                "workspace/didChangeWatchedFiles",
                {"changes": changes},
            )

    async def _acquire(
        self,
        *,
        key: SessionBaseKey,
        spec: LanguageServerSpec,
        epoch: int,
    ) -> LspBroker:
        record = self._records.get(key)
        if record is not None and record.epoch == epoch:
            return record.broker
        start_key = (key, epoch, "start-v1")
        return await self._starts.share(
            start_key,
            lambda: self._start_broker(key=key, spec=spec, epoch=epoch),
        )

    async def _start_broker(
        self,
        *,
        key: SessionBaseKey,
        spec: LanguageServerSpec,
        epoch: int,
    ) -> LspBroker:
        existing = self._records.get(key)
        if existing is not None and existing.epoch == epoch:
            return existing.broker
        failures = self._failure_counts.get(key, 0)
        if failures:
            delay = min(
                _INITIAL_RESTART_BACKOFF_SECONDS * (2 ** (failures - 1)),
                _MAX_RESTART_BACKOFF_SECONDS,
            )
            await asyncio.sleep(delay)
        broker = LspBroker(spec, self._budget, process_epoch=epoch)
        try:
            await broker.start()
        except BaseException:
            self._failure_counts[key] = failures + 1
            self._epochs[key] = max(self.process_epoch(key), epoch + 1)
            await broker.shutdown()
            raise
        self._failure_counts.pop(key, None)
        self._records[key] = _SessionRecord(epoch=epoch, broker=broker)
        return broker

    async def request(
        self,
        *,
        broker: LspBroker,
        generation: SemanticGeneration,
        method: str,
        params: dict[str, Any],
        response_schema: str,
        timeout: float = 10.0,
    ) -> object:
        """Share equivalent generation-bound LSP requests."""
        normalized_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
        key = (
            generation.workspace_id,
            generation.provider_name,
            generation.provider_config_digest,
            generation.process_epoch,
            generation.fingerprint,
            method,
            normalized_params,
            response_schema,
        )
        result = await self._requests.share(
            key,
            lambda: broker.request(method, params, timeout=timeout),
        )
        self._assert_current_generation(broker, generation)
        return result

    def _assert_current_generation(
        self,
        broker: LspBroker,
        generation: SemanticGeneration,
    ) -> None:
        """Discard results from a process or document generation superseded in flight."""
        matching_records = [
            (key, record) for key, record in self._records.items() if record.broker is broker
        ]
        if not matching_records:
            return
        key = self.key_for_generation(generation)
        record = self._records.get(key)
        current = (
            record.generations.get(generation.requested_file)
            if record is not None and record.broker is broker
            else None
        )
        if (
            record is None
            or record.epoch != generation.process_epoch
            or current is None
            or current.fingerprint != generation.fingerprint
        ):
            raise LspBrokerError("semantic provider result belongs to a stale generation")

    async def restart(self, key: SessionBaseKey) -> None:
        """Restart one selected provider identity and advance its process epoch."""
        lock = self._session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self._restart_unlocked(key)

    async def restart_if_running(
        self,
        key: SessionBaseKey,
    ) -> tuple[bool, int, int, int | None]:
        """Stop one selected live provider without provisioning a replacement."""
        lock = self._session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            record = self._records.get(key)
            old_epoch = self.process_epoch(key)
            if record is None:
                return False, old_epoch, old_epoch, None
            old_pid = record.broker.pid
            old_epoch = record.epoch
            await self._restart_unlocked(key)
            return True, old_epoch, self.process_epoch(key), old_pid

    async def _restart_unlocked(self, key: SessionBaseKey) -> None:
        record = self._records.pop(key, None)
        current_epoch = record.epoch if record is not None else self.process_epoch(key)
        self._epochs[key] = max(self.process_epoch(key), current_epoch + 1)
        if record is not None:
            await record.broker.shutdown()

    async def shutdown(self) -> None:
        """Cancel task-owned work and reap every provider process."""
        await self._requests.cancel_all()
        await self._reconciliations.cancel_all()
        await self._starts.cancel_all()
        records = tuple(self._records.values())
        self._records.clear()
        await asyncio.gather(
            *(record.broker.shutdown() for record in records),
            return_exceptions=True,
        )


def _document_uri(workspace_id: str, root: Path, relative_path: str) -> str:
    boundary = _WorkspaceBoundary(workspace_id=workspace_id, root=root)
    return RepositoryPath.admit(boundary, relative_path).file_uri(boundary)
