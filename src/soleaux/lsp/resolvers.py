"""Semantic resolver orchestration over package-owned LSP sessions (D029)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from soleaux.contracts.budget import RequestBudget
from soleaux.contracts.coverage import FrameStatus
from soleaux.contracts.repository import RepositoryPath
from soleaux.contracts.requests import SemanticMode
from soleaux.lsp.broker import (
    LspBroker,
    LspBrokerError,
    SemanticProviderRequiredError,
)
from soleaux.lsp.contracts import (
    EditorSessionContext,
    LspCapability,
    LspLocation,
    LspRange,
    NavigationRequest,
    RestartResult,
    RestartSessionResult,
    RestartStatus,
    SemanticOperation,
    ServerCapabilities,
)
from soleaux.lsp.diagnostics import DiagnosticProtocolError
from soleaux.lsp.generation import SemanticGeneration, SemanticProjectIdentity
from soleaux.lsp.operations import (
    CapabilityResolution,
    LspPayloadError,
    SemanticResolution,
    SymbolIdentity,
    WorkspaceSymbolCandidate,
    WorkspaceSymbolMatchSet,
    capability_method,
    capability_supported,
    locations_from_payload,
    lsp_position_from_user,
    navigation_capability,
    normalize_json_payload,
    symbol_kind_name,
    symbols_from_payload,
    user_position_from_lsp,
    workspace_symbol_candidates,
)
from soleaux.lsp.providers import ConfiguredProvider, ProviderRegistry
from soleaux.lsp.sessions import LspSessionManager
from soleaux.structural.snapshot import SnapshotBundle

MAX_NAME_MATCHES = 20
NAME_MATCH_LIMIT_REASON = "name match limit reached"
NAME_NAVIGATION_DEADLINE_REASON = "name navigation deadline reached"
NAVIGATION_RESULT_LIMIT_REASON = "navigation result limit reached"
AMBIGUOUS_NAME_REASON = "ambiguous symbol name; refine with path or symbol_kind"
_DEFAULT_NAME_NAVIGATION_TIMEOUT_SECONDS = RequestBudget().default_timeout_seconds
_DIAGNOSTIC_READINESS_POLL_SECONDS = 0.01
_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[str, object])


def resolve_named_symbols(
    resolution: CapabilityResolution,
    *,
    name: str,
    kind: str | None,
    path: str | None,
) -> WorkspaceSymbolMatchSet:
    """Return exact, bounded workspace-symbol matches from one capability result."""
    if resolution.capability is not LspCapability.WORKSPACE_SYMBOL:
        raise ValueError("named symbol resolution requires the workspace_symbol capability")
    if resolution.payload is None:
        return WorkspaceSymbolMatchSet()
    return workspace_symbol_candidates(
        resolution.payload,
        symbol_name=name,
        symbol_kind=kind,
        uri=path,
        limit=MAX_NAME_MATCHES,
    )


class ModuleResolution(BaseModel):
    """One module-specifier resolution at an exact semantic generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_path: str = Field(min_length=1)
    specifier: str = Field(min_length=1)
    target_path: str | None = None
    generation_fingerprint: str = Field(min_length=1)
    complete: bool
    omitted_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _NamedSymbolMatch:
    candidate: WorkspaceSymbolCandidate
    path: str
    provider_name: str
    generation: SemanticGeneration


@dataclass(frozen=True)
class _NameLookup:
    matches: tuple[_NamedSymbolMatch, ...]
    status: FrameStatus
    omitted_reasons: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(slots=True)
class _WorkspaceBoundary:
    workspace_id: str
    root: Path


ProjectIdentityResolver = Callable[
    [SnapshotBundle, str, ConfiguredProvider, tuple[str, ...]],
    SemanticProjectIdentity,
]


@runtime_checkable
class ModuleResolver(Protocol):
    """Replaceable module/package-export resolver boundary."""

    async def resolve_module(
        self,
        *,
        source_path: str,
        specifier: str,
        generation: SemanticGeneration,
    ) -> ModuleResolution:
        """Resolve one module specifier without promoting syntax candidates."""
        ...


@runtime_checkable
class SymbolResolver(Protocol):
    """Replaceable canonical symbol-resolution boundary."""

    async def navigate(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
        *,
        dependency_paths: tuple[str, ...] = (),
        control_paths: tuple[str, ...] = (),
    ) -> SemanticResolution:
        """Resolve one closed semantic navigation operation."""
        ...


class SemanticResolver:
    """Lazy LSP-backed `SymbolResolver` and semantic-mode orchestrator."""

    def __init__(
        self,
        registry: ProviderRegistry,
        sessions: LspSessionManager | None = None,
        *,
        diagnostic_timeout_seconds: float = 5.0,
        name_navigation_timeout_seconds: float = _DEFAULT_NAME_NAVIGATION_TIMEOUT_SECONDS,
        project_identity_resolver: ProjectIdentityResolver | None = None,
    ) -> None:
        if diagnostic_timeout_seconds <= 0:
            raise ValueError("diagnostic timeout must be positive")
        if name_navigation_timeout_seconds <= 0:
            raise ValueError("name navigation timeout must be positive")
        self._registry = registry
        self._sessions = sessions or LspSessionManager()
        self._diagnostic_timeout_seconds = diagnostic_timeout_seconds
        self._name_navigation_timeout_seconds = name_navigation_timeout_seconds
        self._project_identity_resolver = project_identity_resolver

    @property
    def active_session_count(self) -> int:
        """Number of provider processes started by semantic requests."""
        return self._sessions.active_session_count

    @property
    def active_provider_pids(self) -> tuple[int, ...]:
        """Return the PIDs owned by active provider sessions."""
        return self._sessions.active_provider_pids

    @property
    def pending_request_count(self) -> int:
        """Return pending response slots across active provider sessions."""
        return self._sessions.pending_request_count

    @property
    def in_flight_task_count(self) -> int:
        """Return shared LSP lifecycle and request tasks."""
        return self._sessions.in_flight_task_count

    def configured_provider_for_path(self, path: str) -> ConfiguredProvider | None:
        """Return inert provider metadata for a workspace path without probing it."""
        return self._registry.configured_for_path(path)

    def editor_session_context(
        self,
        generation: SemanticGeneration,
    ) -> EditorSessionContext:
        """Return exact position/version state for a completed editor request."""
        return self._sessions.editor_context(generation)

    def process_epoch(
        self,
        *,
        workspace_id: str,
        provider_name: str,
        provider_config_digest: str,
        project_id: str | None = None,
        project_root: str | None = None,
        project_config_digest: str | None = None,
        compiler_identity: str | None = None,
    ) -> int:
        """Return the current epoch for one preview-bound provider identity."""
        provider = self._provider_by_identity(
            provider_name,
            provider_config_digest,
        )
        supplied = (
            project_id,
            project_root,
            project_config_digest,
            compiler_identity,
        )
        if (
            project_id is not None
            and project_root is not None
            and project_config_digest is not None
            and compiler_identity is not None
        ):
            identity = SemanticProjectIdentity(
                project_id=project_id,
                project_root=project_root,
                project_config_digest=project_config_digest,
                compiler_identity=compiler_identity,
            )
            key = self._sessions.base_key(
                workspace_id=workspace_id,
                provider=provider,
                project_identity=identity,
            )
            return self._sessions.process_epoch(key)
        if any(value is not None for value in supplied):
            raise ValueError("project session identity must be supplied as one complete record")
        keys = self._sessions.keys_for_provider(
            workspace_id=workspace_id,
            provider=provider,
        )
        if len(keys) > 1:
            raise ValueError("provider epoch is ambiguous without a project identity")
        key = (
            keys[0]
            if keys
            else self._sessions.base_key(
                workspace_id=workspace_id,
                provider=provider,
            )
        )
        return self._sessions.process_epoch(key)

    async def restart_selected(
        self,
        *,
        workspace_id: str,
        provider_name: str | None = None,
        language: str | None = None,
        path: str | None = None,
    ) -> RestartResult:
        """Restart only selected live providers and leave replacements lazy."""
        providers = self._restart_providers(
            provider_name=provider_name,
            language=language,
            path=path,
        )
        if not providers:
            selector = provider_name or language or path or "configured providers"
            unavailable = RestartSessionResult(
                provider_name=selector,
                status=RestartStatus.UNAVAILABLE,
                old_epoch=0,
                new_epoch=0,
                reason="no configured provider matches the restart selection",
            )
            return RestartResult(sessions=(unavailable,), restarted_sessions=0)

        results: list[RestartSessionResult] = []
        for provider in providers:
            keys = self._sessions.keys_for_provider(
                workspace_id=workspace_id,
                provider=provider,
            )
            if not keys:
                key = self._sessions.base_key(
                    workspace_id=workspace_id,
                    provider=provider,
                )
                epoch = self._sessions.process_epoch(key)
                results.append(
                    RestartSessionResult(
                        provider_name=provider.provider_name,
                        status=(
                            RestartStatus.NOT_RUNNING
                            if provider.executable_available()
                            else RestartStatus.UNAVAILABLE
                        ),
                        old_epoch=epoch,
                        new_epoch=epoch,
                        reason=(
                            None
                            if provider.executable_available()
                            else "configured provider executable is unavailable"
                        ),
                    )
                )
                continue
            for key in keys:
                restarted, old_epoch, new_epoch, old_pid = await self._sessions.restart_if_running(
                    key
                )
                results.append(
                    RestartSessionResult(
                        provider_name=provider.provider_name,
                        status=(
                            RestartStatus.RESTARTED if restarted else RestartStatus.NOT_RUNNING
                        ),
                        old_epoch=old_epoch,
                        new_epoch=new_epoch,
                        old_pid=old_pid,
                        new_pid=None,
                        reason=f"project={key.project_id}",
                    )
                )
        return RestartResult(
            sessions=tuple(results),
            restarted_sessions=sum(result.status is RestartStatus.RESTARTED for result in results),
        )

    async def execute_capability(
        self,
        capability: LspCapability,
        bundle: SnapshotBundle,
        *,
        path: str,
        line: int = 1,
        column: int = 1,
        arguments: Mapping[str, object] | None = None,
        semantic_mode: SemanticMode = SemanticMode.BEST_AVAILABLE,
        dependency_paths: tuple[str, ...] = (),
        control_paths: tuple[str, ...] = (),
    ) -> CapabilityResolution:
        """Execute one capability through the package-owned generation barrier."""
        if semantic_mode is SemanticMode.SYNTAX_ONLY:
            return CapabilityResolution(
                capability=capability,
                status=FrameStatus.UNSUPPORTED,
                generation=None,
                omitted_reasons=("semantic_mode=syntax_only",),
            )

        provider = self._registry.configured_for_path(path)
        if provider is None:
            return self._capability_gap(
                capability,
                semantic_mode=semantic_mode,
                reason=f"no provider configured for {Path(path).suffix or path!r}",
            )
        if not provider.executable_available():
            return self._capability_gap(
                capability,
                semantic_mode=semantic_mode,
                reason=f"configured provider {provider.provider_name!r} is not installed",
            )

        project_identity = self._project_identity(
            bundle,
            path=path,
            provider=provider,
            control_paths=control_paths,
        )
        session_key = self._sessions.base_key(
            workspace_id=bundle.snapshot.workspace_id,
            provider=provider,
            project_identity=project_identity,
        )
        if capability is LspCapability.RESTART:
            await self._sessions.restart(session_key)
            return CapabilityResolution(
                capability=capability,
                status=FrameStatus.COMPLETE,
                generation=None,
            )

        generation: SemanticGeneration | None = None
        broker: LspBroker | None = None
        spec = provider.to_spec(
            Path(path).suffix,
            project_root=_project_root(bundle, project_identity),
        )
        for _attempt in range(2):
            generation = SemanticGeneration.from_snapshot(
                bundle,
                provider_name=provider.provider_name,
                provider_config_digest=provider.config_digest,
                process_epoch=self._sessions.process_epoch(session_key),
                requested_file=path,
                dependency_paths=dependency_paths,
                control_paths=control_paths,
                project_identity=project_identity,
            )
            if semantic_mode is SemanticMode.SEMANTIC_REQUIRED and not generation.complete:
                msg = (
                    "semantic_provider_required: "
                    f"{generation.status.value} ({', '.join(generation.missing_inputs)})"
                )
                raise SemanticProviderRequiredError(msg)
            if generation.requested_hash is None:
                return CapabilityResolution(
                    capability=capability,
                    status=FrameStatus.PARTIAL,
                    generation=generation,
                    omitted_reasons=("requested file is absent from the frozen snapshot",),
                )
            try:
                broker = await self._sessions.prepare(
                    provider=provider,
                    spec=spec,
                    generation=generation,
                    bundle=bundle,
                )
            except LspBrokerError as exc:
                reason = f"provider {provider.provider_name!r} failure: {exc}"
                if semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                    msg = f"semantic_provider_required: {reason}"
                    raise SemanticProviderRequiredError(msg) from exc
                return CapabilityResolution(
                    capability=capability,
                    status=FrameStatus.PARTIAL,
                    generation=generation,
                    omitted_reasons=(reason,),
                )
            if broker is not None:
                break
        if generation is None or broker is None:
            msg = "semantic generation could not stabilize after provider restart"
            if semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                raise SemanticProviderRequiredError(f"semantic_provider_required: {msg}")
            return CapabilityResolution(
                capability=capability,
                status=FrameStatus.PARTIAL,
                generation=generation,
                omitted_reasons=(msg,),
            )

        try:
            capabilities = broker.capabilities
            method = capability_method(capability)
            capability_is_supported = capability is LspCapability.DIAGNOSTICS or (
                capabilities is not None
                and (
                    capability_supported(capabilities, capability)
                    or bool(broker.registrations_by_method(method))
                )
            )
            if not capability_is_supported:
                reason = f"provider {provider.provider_name!r} does not support {capability.value}"
                if semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                    raise SemanticProviderRequiredError(f"semantic_provider_required: {reason}")
                return CapabilityResolution(
                    capability=capability,
                    status=FrameStatus.UNSUPPORTED,
                    generation=generation,
                    omitted_reasons=(reason,),
                )
            if capabilities is None:
                raise LspBrokerError("initialized provider did not retain server capabilities")

            content = bundle.contents[path]
            position = lsp_position_from_user(
                content,
                line=line,
                column=column,
                position_encoding=capabilities.position_encoding,
            ).model_dump(mode="json")
            uri = _document_uri(bundle, provider, path)
            if capability is LspCapability.DIAGNOSTICS:
                document_version = broker.document_versions.get(uri)
                if document_version is None:
                    raise LspBrokerError("diagnostic document is not open")
                raw_result, diagnostic_omissions = await self._diagnostic_payload(
                    broker=broker,
                    generation=generation,
                    capabilities=capabilities,
                    uri=uri,
                    document_version=document_version,
                )
                if raw_result is None:
                    return CapabilityResolution(
                        capability=capability,
                        status=FrameStatus.PARTIAL,
                        generation=generation,
                        payload=[],
                        omitted_reasons=diagnostic_omissions,
                    )
            else:
                capability_arguments = dict(arguments or {})
                raw_user_range = capability_arguments.pop("userRange", None)
                if raw_user_range is not None:
                    capability_arguments["range"] = self._user_range_to_lsp(
                        raw_user_range,
                        content=content,
                        position_encoding=capabilities.position_encoding,
                    )
                params = self._capability_params(
                    capability,
                    uri=uri,
                    position=position,
                    arguments=capability_arguments,
                )
                raw_result = await self._sessions.request(
                    broker=broker,
                    generation=generation,
                    method=method,
                    params=params,
                    response_schema=f"{capability.value}-v1",
                )

            payload = normalize_json_payload(raw_result)
            return CapabilityResolution(
                capability=capability,
                status=FrameStatus.COMPLETE if generation.complete else FrameStatus.PARTIAL,
                generation=generation,
                provider_identity=broker.provider_identity,
                position_encoding=capabilities.position_encoding,
                locations=locations_from_payload(payload),
                symbols=symbols_from_payload(
                    payload,
                    provider_name=provider.provider_name,
                    generation_fingerprint=generation.fingerprint,
                ),
                payload=payload,
                omitted_reasons=() if generation.complete else generation.verification_issues,
            )
        except SemanticProviderRequiredError:
            raise
        except (LspBrokerError, LspPayloadError) as exc:
            await self._sessions.restart(session_key)
            reason = f"provider {provider.provider_name!r} failure: {exc}"
            if semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                raise SemanticProviderRequiredError(
                    f"semantic_provider_required: {reason}"
                ) from exc
            return CapabilityResolution(
                capability=capability,
                status=FrameStatus.FAILED,
                generation=generation,
                omitted_reasons=(reason,),
            )

    async def _diagnostic_payload(
        self,
        *,
        broker: LspBroker,
        generation: SemanticGeneration,
        capabilities: ServerCapabilities,
        uri: str,
        document_version: int,
    ) -> tuple[object | None, tuple[str, ...]]:
        """Prefer pull diagnostics, then fall back to compatible retained push state."""
        diagnostic_registrations = sorted(
            broker.registrations_by_method("textDocument/diagnostic"),
            key=lambda registration: registration.id,
        )
        pull_supported = bool(capabilities.diagnostic_provider or diagnostic_registrations)
        deadline = asyncio.get_running_loop().time() + self._diagnostic_timeout_seconds

        while not pull_supported:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            state = await broker.wait_for_diagnostics(
                uri,
                document_version=document_version,
                generation_fingerprint=generation.fingerprint,
                timeout=min(remaining, _DIAGNOSTIC_READINESS_POLL_SECONDS),
            )
            if state is not None:
                return list(state.items), ()
            diagnostic_registrations = sorted(
                broker.registrations_by_method("textDocument/diagnostic"),
                key=lambda registration: registration.id,
            )
            pull_supported = bool(capabilities.diagnostic_provider or diagnostic_registrations)

        if pull_supported:
            params: dict[str, object] = {"textDocument": {"uri": uri}}
            identifier = capabilities.diagnostic_identifier
            if diagnostic_registrations:
                registered_identifier = diagnostic_registrations[0].register_options.get(
                    "identifier"
                )
                if isinstance(registered_identifier, str) and registered_identifier:
                    identifier = registered_identifier
            if identifier is not None:
                params["identifier"] = identifier
            previous_result_id = broker.diagnostic_previous_result_id(
                uri,
                document_version=document_version,
                generation_fingerprint=generation.fingerprint,
            )
            if previous_result_id is not None:
                params["previousResultId"] = previous_result_id
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                try:
                    report = await self._sessions.request(
                        broker=broker,
                        generation=generation,
                        method="textDocument/diagnostic",
                        params=params,
                        response_schema="diagnostics-pull-v1",
                        timeout=remaining,
                    )
                    state = broker.apply_diagnostic_pull_report(uri, report)
                    return list(state.items), ()
                except DiagnosticProtocolError, LspBrokerError:
                    pass

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None, (
                "provider did not return diagnostics for the current document generation",
            )
        state = await broker.wait_for_diagnostics(
            uri,
            document_version=document_version,
            generation_fingerprint=generation.fingerprint,
            timeout=remaining,
        )
        if state is not None:
            return list(state.items), ()
        return None, ("provider did not return diagnostics for the current document generation",)

    async def navigate(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
        *,
        dependency_paths: tuple[str, ...] = (),
        control_paths: tuple[str, ...] = (),
    ) -> SemanticResolution:
        """Execute one navigation operation against the exact captured generation."""
        capability = navigation_capability(request.operation)
        if request.semantic_mode is SemanticMode.SYNTAX_ONLY:
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.UNSUPPORTED,
                generation=None,
                omitted_reasons=("semantic_mode=syntax_only",),
            )
        if request.symbol_name is not None:
            return await self._navigate_by_name(
                request,
                bundle,
                control_paths=control_paths,
            )
        if request.path is None or request.line is None or request.column is None:
            raise ValueError("position navigation requires path, line, and column")

        provider = self._registry.configured_for_path(request.path)
        if provider is None:
            return self._provider_gap(
                request,
                reason=f"no provider configured for {Path(request.path).suffix or request.path!r}",
            )
        if not provider.executable_available():
            return self._provider_gap(
                request,
                reason=f"configured provider {provider.provider_name!r} is not installed",
            )

        project_identity = self._project_identity(
            bundle,
            path=request.path,
            provider=provider,
            control_paths=control_paths,
        )
        session_key = self._sessions.base_key(
            workspace_id=bundle.snapshot.workspace_id,
            provider=provider,
            project_identity=project_identity,
        )
        generation: SemanticGeneration | None = None
        broker: LspBroker | None = None
        spec = provider.to_spec(
            Path(request.path).suffix,
            project_root=_project_root(bundle, project_identity),
        )
        for _attempt in range(2):
            generation = SemanticGeneration.from_snapshot(
                bundle,
                provider_name=provider.provider_name,
                provider_config_digest=provider.config_digest,
                process_epoch=self._sessions.process_epoch(session_key),
                requested_file=request.path,
                dependency_paths=dependency_paths,
                control_paths=control_paths,
                project_identity=project_identity,
            )
            if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED and not generation.complete:
                msg = (
                    "semantic_provider_required: "
                    f"{generation.status.value} ({', '.join(generation.missing_inputs)})"
                )
                raise SemanticProviderRequiredError(msg)
            if generation.requested_hash is None:
                return SemanticResolution(
                    operation=request.operation,
                    capability=capability,
                    status=FrameStatus.PARTIAL,
                    generation=generation,
                    omitted_reasons=("requested file is absent from the frozen snapshot",),
                )
            try:
                broker = await self._sessions.prepare(
                    provider=provider,
                    spec=spec,
                    generation=generation,
                    bundle=bundle,
                )
            except LspBrokerError as exc:
                reason = f"provider {provider.provider_name!r} failure: {exc}"
                if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                    msg = f"semantic_provider_required: {reason}"
                    raise SemanticProviderRequiredError(msg) from exc
                return SemanticResolution(
                    operation=request.operation,
                    capability=capability,
                    status=FrameStatus.PARTIAL,
                    generation=generation,
                    omitted_reasons=(reason,),
                )
            if broker is not None:
                break
        if generation is None or broker is None:
            msg = "semantic generation could not stabilize after provider restart"
            if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                raise SemanticProviderRequiredError(f"semantic_provider_required: {msg}")
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.PARTIAL,
                generation=generation,
                omitted_reasons=(msg,),
            )

        try:
            capabilities = broker.capabilities
            method = capability_method(capability)
            if capabilities is None or not (
                capability_supported(capabilities, capability)
                or broker.registrations_by_method(method)
            ):
                reason = f"provider {provider.provider_name!r} does not support {capability.value}"
                if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                    raise SemanticProviderRequiredError(f"semantic_provider_required: {reason}")
                return SemanticResolution(
                    operation=request.operation,
                    capability=capability,
                    status=FrameStatus.UNSUPPORTED,
                    generation=generation,
                    omitted_reasons=(reason,),
                )

            content = bundle.contents[request.path]
            position = lsp_position_from_user(
                content,
                line=request.line,
                column=request.column,
                position_encoding=capabilities.position_encoding,
            )
            uri = _document_uri(bundle, provider, request.path)
            raw_result = await self._request_navigation(
                request.operation,
                broker=broker,
                generation=generation,
                uri=uri,
                position=position.model_dump(mode="json"),
            )
            payload = normalize_json_payload(raw_result)
            locations = locations_from_payload(payload)

            if not locations and request.line >= 1:
                locations, payload = await self._try_adjacent_positions(
                    request=request,
                    broker=broker,
                    generation=generation,
                    uri=uri,
                    content=content,
                    position_encoding=capabilities.position_encoding,
                    primary_payload=payload,
                )

            symbols = symbols_from_payload(
                payload,
                provider_name=provider.provider_name,
                generation_fingerprint=generation.fingerprint,
            )
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.COMPLETE if generation.complete else FrameStatus.PARTIAL,
                generation=generation,
                provider_identity=broker.provider_identity,
                position_encoding=capabilities.position_encoding,
                locations=locations,
                symbols=symbols,
                payload=payload,
                omitted_reasons=() if generation.complete else generation.verification_issues,
            )
        except SemanticProviderRequiredError:
            raise
        except (LspBrokerError, LspPayloadError) as exc:
            await self._sessions.restart(session_key)
            reason = f"provider {provider.provider_name!r} failure: {exc}"
            if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
                raise SemanticProviderRequiredError(
                    f"semantic_provider_required: {reason}"
                ) from exc
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.FAILED,
                generation=generation,
                omitted_reasons=(reason,),
            )

    async def _navigate_by_name(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
        *,
        control_paths: tuple[str, ...],
    ) -> SemanticResolution:
        symbol_name = request.symbol_name
        if symbol_name is None:
            raise ValueError("name navigation requires symbol_name")
        capability = navigation_capability(request.operation)
        deadline = asyncio.get_running_loop().time() + self._name_navigation_timeout_seconds
        try:
            lookup = await self._lookup_named_symbols(
                request,
                bundle,
                deadline=deadline,
                control_paths=control_paths,
            )
        except LspPayloadError as exc:
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.FAILED,
                generation=None,
                omitted_reasons=(str(exc),),
            )

        if lookup.truncated:
            return self._name_candidate_resolution(
                request,
                lookup,
                status=FrameStatus.TRUNCATED,
            )
        if len(lookup.matches) != 1:
            status = lookup.status
            omitted_reasons = lookup.omitted_reasons
            if len(lookup.matches) > 1:
                status = FrameStatus.PARTIAL
                omitted_reasons = (*omitted_reasons, AMBIGUOUS_NAME_REASON)
            return self._name_candidate_resolution(
                request,
                _NameLookup(
                    matches=lookup.matches,
                    status=status,
                    omitted_reasons=_deduplicate_reasons(omitted_reasons),
                ),
                status=status,
            )

        match = lookup.matches[0]
        session = self.editor_session_context(match.generation)
        position = match.candidate.location.range.start
        try:
            line, column = user_position_from_lsp(
                bundle.contents[match.path],
                line=position.line,
                character=position.character,
                position_encoding=session.position_encoding,
            )
        except LspPayloadError as exc:
            return SemanticResolution(
                operation=request.operation,
                capability=capability,
                status=FrameStatus.FAILED,
                generation=match.generation,
                locations=(match.candidate.location,),
                payload=self._name_candidates_payload(lookup.matches),
                omitted_reasons=(str(exc),),
            )

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return self._name_deadline_resolution(request, lookup.matches)
        position_request = NavigationRequest(
            operation=request.operation,
            path=match.path,
            line=line,
            column=column,
            limit=request.limit,
            workspace_id=request.workspace_id,
            semantic_mode=request.semantic_mode,
        )
        try:
            async with asyncio.timeout(remaining):
                resolution = await self.navigate(
                    position_request,
                    bundle,
                    dependency_paths=tuple(
                        path for path in sorted(bundle.contents) if path != match.path
                    ),
                    control_paths=control_paths,
                )
        except TimeoutError:
            return self._name_deadline_resolution(request, lookup.matches)
        return self._limit_name_navigation_result(
            resolution,
            limit=request.limit,
            lookup=lookup,
        )

    async def _lookup_named_symbols(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
        *,
        deadline: float,
        control_paths: tuple[str, ...],
    ) -> _NameLookup:
        symbol_name = request.symbol_name
        if symbol_name is None:
            raise ValueError("name navigation requires symbol_name")
        match_limit = min(request.limit, MAX_NAME_MATCHES)
        representatives = self._name_representatives(request, bundle)
        if not representatives:
            path = request.path or "captured source"
            return _NameLookup(
                matches=(),
                status=FrameStatus.UNSUPPORTED,
                omitted_reasons=(f"no provider configured for {path!r}",),
            )

        matches: tuple[_NamedSymbolMatch, ...] = ()
        statuses: list[FrameStatus] = []
        omitted_reasons: list[str] = []
        truncated = False
        for index, (provider, representative_path) in enumerate(representatives):
            if len(matches) >= match_limit:
                if index < len(representatives):
                    truncated = True
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return _NameLookup(
                    matches=matches,
                    status=FrameStatus.TRUNCATED,
                    omitted_reasons=(NAME_NAVIGATION_DEADLINE_REASON,),
                    truncated=True,
                )
            try:
                async with asyncio.timeout(remaining):
                    resolution = await self.execute_capability(
                        LspCapability.WORKSPACE_SYMBOL,
                        bundle,
                        path=representative_path,
                        arguments={"query": symbol_name},
                        semantic_mode=request.semantic_mode,
                        dependency_paths=tuple(
                            path for path in sorted(bundle.contents) if path != representative_path
                        ),
                        control_paths=control_paths,
                    )
            except TimeoutError:
                return _NameLookup(
                    matches=matches,
                    status=FrameStatus.TRUNCATED,
                    omitted_reasons=(NAME_NAVIGATION_DEADLINE_REASON,),
                    truncated=True,
                )

            statuses.append(resolution.status)
            omitted_reasons.extend(resolution.omitted_reasons)
            generation = resolution.generation
            if generation is None:
                continue
            target_uri = (
                _document_uri(bundle, provider, request.path) if request.path is not None else None
            )
            candidate_set = resolve_named_symbols(
                resolution,
                name=symbol_name,
                kind=request.symbol_kind,
                path=target_uri,
            )
            provider_matches = tuple(
                _NamedSymbolMatch(
                    candidate=candidate,
                    path=path,
                    provider_name=provider.provider_name,
                    generation=generation,
                )
                for candidate in candidate_set.candidates
                if (
                    path := self._candidate_path(
                        candidate,
                        provider=provider,
                        bundle=bundle,
                    )
                )
                is not None
            )
            matches, combined_truncated = _merge_named_matches(
                matches,
                provider_matches,
                limit=match_limit,
            )
            truncated = truncated or candidate_set.truncated or combined_truncated
            if truncated:
                break

        if truncated:
            return _NameLookup(
                matches=matches,
                status=FrameStatus.TRUNCATED,
                omitted_reasons=(NAME_MATCH_LIMIT_REASON,),
                truncated=True,
            )
        if statuses and all(status is FrameStatus.COMPLETE for status in statuses):
            status = FrameStatus.COMPLETE
        elif statuses and all(status is FrameStatus.UNSUPPORTED for status in statuses):
            status = FrameStatus.UNSUPPORTED
        else:
            status = FrameStatus.PARTIAL
        return _NameLookup(
            matches=matches,
            status=status,
            omitted_reasons=_deduplicate_reasons(tuple(omitted_reasons)),
        )

    def _name_representatives(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
    ) -> tuple[tuple[ConfiguredProvider, str], ...]:
        if request.path is not None:
            provider = self._registry.configured_for_path(request.path)
            return ((provider, request.path),) if provider is not None else ()
        by_provider: dict[tuple[str, str], tuple[ConfiguredProvider, str]] = {}
        for path in sorted(bundle.contents):
            provider = self._registry.configured_for_path(path)
            if provider is None:
                continue
            identity = (provider.provider_name, provider.config_digest)
            by_provider.setdefault(identity, (provider, path))
        return tuple(by_provider[identity] for identity in sorted(by_provider))

    @staticmethod
    def _candidate_path(
        candidate: WorkspaceSymbolCandidate,
        *,
        provider: ConfiguredProvider,
        bundle: SnapshotBundle,
    ) -> str | None:
        paths_by_uri = {_document_uri(bundle, provider, path): path for path in bundle.contents}
        return paths_by_uri.get(candidate.location.uri)

    def _name_candidate_resolution(
        self,
        request: NavigationRequest,
        lookup: _NameLookup,
        *,
        status: FrameStatus,
    ) -> SemanticResolution:
        return SemanticResolution(
            operation=request.operation,
            capability=navigation_capability(request.operation),
            status=status,
            generation=None,
            locations=tuple(match.candidate.location for match in lookup.matches),
            symbols=tuple(
                SymbolIdentity.from_location(
                    match.candidate.location,
                    provider_name=match.provider_name,
                    generation_fingerprint=match.generation.fingerprint,
                    name=match.candidate.name,
                    kind=match.candidate.kind,
                )
                for match in lookup.matches
            ),
            payload=self._name_candidates_payload(lookup.matches),
            omitted_reasons=lookup.omitted_reasons,
        )

    def _name_deadline_resolution(
        self,
        request: NavigationRequest,
        matches: tuple[_NamedSymbolMatch, ...],
    ) -> SemanticResolution:
        return self._name_candidate_resolution(
            request,
            _NameLookup(
                matches=matches,
                status=FrameStatus.TRUNCATED,
                omitted_reasons=(NAME_NAVIGATION_DEADLINE_REASON,),
                truncated=True,
            ),
            status=FrameStatus.TRUNCATED,
        )

    @staticmethod
    def _public_name_candidates(
        matches: tuple[_NamedSymbolMatch, ...],
    ) -> list[dict[str, JsonValue]]:
        return [
            {
                "name": match.candidate.name,
                "symbol_kind": symbol_kind_name(match.candidate.kind),
                "symbol_kind_value": match.candidate.kind,
                "path": match.path,
                "line": match.candidate.location.range.start.line + 1,
                "column": match.candidate.location.range.start.character + 1,
                "provider": match.provider_name,
                "generation_fingerprint": match.generation.fingerprint,
            }
            for match in matches
        ]

    @classmethod
    def _name_candidates_payload(
        cls,
        matches: tuple[_NamedSymbolMatch, ...],
    ) -> JsonValue:
        return normalize_json_payload({"candidates": cls._public_name_candidates(matches)})

    @staticmethod
    def _limit_name_navigation_result(
        resolution: SemanticResolution,
        *,
        limit: int,
        lookup: _NameLookup,
    ) -> SemanticResolution:
        payload = resolution.payload
        payload_truncated = isinstance(payload, list) and len(payload) > limit
        limited_payload: JsonValue = payload
        if isinstance(payload, list) and payload_truncated:
            limited_payload = payload[:limit]
        truncated = (
            payload_truncated
            or len(resolution.locations) > limit
            or len(resolution.symbols) > limit
        )
        omitted_reasons = _deduplicate_reasons(
            (*lookup.omitted_reasons, *resolution.omitted_reasons)
        )
        status = resolution.status
        if truncated:
            status = FrameStatus.TRUNCATED
            omitted_reasons = (*omitted_reasons, NAVIGATION_RESULT_LIMIT_REASON)
        elif lookup.status is FrameStatus.PARTIAL and status is FrameStatus.COMPLETE:
            status = FrameStatus.PARTIAL
        return resolution.model_copy(
            update={
                "status": status,
                "locations": resolution.locations[:limit],
                "symbols": resolution.symbols[:limit],
                "payload": limited_payload,
                "omitted_reasons": omitted_reasons,
            }
        )

    async def _request_navigation(
        self,
        operation: SemanticOperation,
        *,
        broker: LspBroker,
        generation: SemanticGeneration,
        uri: str,
        position: dict[str, int],
    ) -> object:
        text_position: dict[str, object] = {
            "textDocument": {"uri": uri},
            "position": position,
        }
        if operation is SemanticOperation.REFERENCES:
            text_position["context"] = {"includeDeclaration": True}
        if operation not in {
            SemanticOperation.INCOMING_CALLS,
            SemanticOperation.OUTGOING_CALLS,
        }:
            return await self._sessions.request(
                broker=broker,
                generation=generation,
                method=capability_method(navigation_capability(operation)),
                params=text_position,
                response_schema="navigation-v1",
            )

        prepared = await self._sessions.request(
            broker=broker,
            generation=generation,
            method=capability_method(navigation_capability(SemanticOperation.CALL_HIERARCHY)),
            params=text_position,
            response_schema="call-hierarchy-items-v1",
        )
        prepared_payload = normalize_json_payload(prepared)
        if not isinstance(prepared_payload, list) or not prepared_payload:
            return []
        item = prepared_payload[0]
        if not isinstance(item, dict):
            return []
        return await self._sessions.request(
            broker=broker,
            generation=generation,
            method=capability_method(navigation_capability(operation)),
            params={"item": item},
            response_schema="call-hierarchy-calls-v1",
        )

    async def _try_adjacent_positions(
        self,
        *,
        request: NavigationRequest,
        broker: LspBroker,
        generation: SemanticGeneration,
        uri: str,
        content: bytes,
        position_encoding: str,
        primary_payload: Any,
    ) -> tuple[tuple[LspLocation, ...], Any]:
        """Retry with adjacent positions when the primary returns empty.

        LLMs frequently provide off-by-one line numbers or wrong columns.
        This tries up to three fallbacks before giving up.
        """
        line = request.line
        column = request.column
        if line is None or column is None:
            raise ValueError("adjacent position retries require a position target")
        fallbacks: list[tuple[int, int]] = []
        if column > 1:
            fallbacks.append((line, 1))
        if line > 1:
            fallbacks.append((line - 1, column))
        fallbacks.append((line + 1, column))
        fallbacks = fallbacks[:3]

        for fb_line, fb_column in fallbacks:
            try:
                fb_position = lsp_position_from_user(
                    content,
                    line=fb_line,
                    column=fb_column,
                    position_encoding=position_encoding,
                )
            except ValueError:
                continue
            try:
                fb_raw = await self._request_navigation(
                    request.operation,
                    broker=broker,
                    generation=generation,
                    uri=uri,
                    position=fb_position.model_dump(mode="json"),
                )
                fb_payload = normalize_json_payload(fb_raw)
                fb_locations = locations_from_payload(fb_payload)
                if fb_locations:
                    return fb_locations, fb_payload
            except LspBrokerError, LspPayloadError:
                continue

        primary_locations = locations_from_payload(primary_payload)
        return primary_locations, primary_payload

    @staticmethod
    def _user_range_to_lsp(
        value: object,
        *,
        content: bytes,
        position_encoding: str,
    ) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("userRange must be an object")
        user_range = _OBJECT_MAPPING_ADAPTER.validate_python(value, strict=True)
        converted: dict[str, object] = {}
        for boundary in ("start", "end"):
            raw_position = user_range.get(boundary)
            if not isinstance(raw_position, dict):
                raise ValueError(f"userRange.{boundary} must be an object")
            position = _OBJECT_MAPPING_ADAPTER.validate_python(raw_position, strict=True)
            line = position.get("line")
            column = position.get("column")
            if (
                isinstance(line, bool)
                or not isinstance(line, int)
                or isinstance(column, bool)
                or not isinstance(column, int)
            ):
                raise ValueError(f"userRange.{boundary} line and column must be integers")
            converted[boundary] = lsp_position_from_user(
                content,
                line=line,
                column=column,
                position_encoding=position_encoding,
            ).model_dump(mode="json")
        return converted

    @staticmethod
    def _capability_params(
        capability: LspCapability,
        *,
        uri: str,
        position: dict[str, int],
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        text_document = {"uri": uri}
        text_position: dict[str, Any] = {
            "textDocument": text_document,
            "position": position,
        }
        if capability is LspCapability.WORKSPACE_SYMBOL:
            query = arguments.get("query", "")
            if not isinstance(query, str):
                raise ValueError("workspace symbol query must be a string")
            return {"query": query}
        if capability in {
            LspCapability.FORMAT_DOCUMENT,
            LspCapability.FORMAT_RANGE,
        }:
            options = arguments.get("options", {"tabSize": 4, "insertSpaces": True})
            if not isinstance(options, dict):
                raise ValueError("formatting options must be an object")
            params: dict[str, Any] = {
                "textDocument": text_document,
                "options": options,
            }
            if capability is LspCapability.FORMAT_RANGE:
                default_range = {"start": position, "end": position}
                params["range"] = LspRange.model_validate(
                    arguments.get("range", default_range)
                ).model_dump(mode="json")
            return params
        if capability is LspCapability.CODE_ACTIONS:
            default_range = {"start": position, "end": position}
            return {
                "textDocument": text_document,
                "range": LspRange.model_validate(arguments.get("range", default_range)).model_dump(
                    mode="json"
                ),
                "context": arguments.get("context", {"diagnostics": []}),
            }
        if capability in {LspCapability.RENAME, LspCapability.RENAME_STRICT}:
            new_name = arguments.get("newName")
            if not isinstance(new_name, str) or not new_name:
                raise ValueError("rename requires a non-empty newName")
            return {**text_position, "newName": new_name}
        if capability is LspCapability.REFERENCES:
            return {**text_position, "context": {"includeDeclaration": True}}
        if capability in {
            LspCapability.INCOMING_CALLS,
            LspCapability.OUTGOING_CALLS,
        }:
            item = arguments.get("item")
            if not isinstance(item, dict):
                raise ValueError(f"{capability.value} requires a call hierarchy item")
            return {"item": item}
        return text_position

    @staticmethod
    def _capability_gap(
        capability: LspCapability,
        *,
        semantic_mode: SemanticMode,
        reason: str,
    ) -> CapabilityResolution:
        if semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
            raise SemanticProviderRequiredError(f"semantic_provider_required: {reason}")
        return CapabilityResolution(
            capability=capability,
            status=FrameStatus.UNSUPPORTED,
            generation=None,
            omitted_reasons=(reason,),
        )

    def _provider_gap(
        self,
        request: NavigationRequest,
        *,
        reason: str,
    ) -> SemanticResolution:
        if request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED:
            raise SemanticProviderRequiredError(f"semantic_provider_required: {reason}")
        return SemanticResolution(
            operation=request.operation,
            capability=navigation_capability(request.operation),
            status=FrameStatus.UNSUPPORTED,
            generation=None,
            omitted_reasons=(reason,),
        )

    def _provider_by_identity(
        self,
        provider_name: str,
        provider_config_digest: str,
    ) -> ConfiguredProvider:
        for provider in self._registry.providers:
            if (
                provider.provider_name == provider_name
                and provider.config_digest == provider_config_digest
            ):
                return provider
        raise ValueError("preview provider identity is no longer configured")

    def _project_identity(
        self,
        bundle: SnapshotBundle,
        *,
        path: str,
        provider: ConfiguredProvider,
        control_paths: tuple[str, ...],
    ) -> SemanticProjectIdentity:
        resolver = self._project_identity_resolver
        if resolver is not None:
            return resolver(bundle, path, provider, control_paths)
        return SemanticProjectIdentity.fallback(
            bundle,
            provider_name=provider.provider_name,
            requested_file=path,
            control_paths=control_paths,
        )

    def _restart_providers(
        self,
        *,
        provider_name: str | None,
        language: str | None,
        path: str | None,
    ) -> tuple[ConfiguredProvider, ...]:
        providers = list(self._registry.providers)
        if provider_name is not None:
            providers = [
                provider for provider in providers if provider.provider_name == provider_name
            ]
        if path is not None:
            selected = self._registry.configured_for_path(path)
            providers = (
                [provider for provider in providers if provider == selected]
                if selected is not None
                else []
            )
        if language is not None:
            normalized = _normalized_language(language)
            providers = [
                provider for provider in providers if normalized in _provider_languages(provider)
            ]
        return tuple(providers)

    async def shutdown(self) -> None:
        """Cancel task-owned work and reap every lazily started provider."""
        await self._sessions.shutdown()


def _document_uri(
    bundle: SnapshotBundle,
    provider: ConfiguredProvider,
    path: str,
) -> str:
    boundary = _WorkspaceBoundary(
        workspace_id=bundle.snapshot.workspace_id,
        root=provider.root,
    )
    return RepositoryPath.admit(boundary, path).file_uri(boundary)


def _project_root(
    bundle: SnapshotBundle,
    identity: SemanticProjectIdentity,
) -> Path:
    boundary = _WorkspaceBoundary(
        workspace_id=bundle.snapshot.workspace_id,
        root=Path(bundle.snapshot.root),
    )
    if not identity.project_root:
        return boundary.root.resolve(strict=True)
    return (
        RepositoryPath.admit(boundary, identity.project_root)
        .absolute(boundary)
        .resolve(strict=True)
    )


def _merge_named_matches(
    existing: tuple[_NamedSymbolMatch, ...],
    additions: tuple[_NamedSymbolMatch, ...],
    *,
    limit: int,
) -> tuple[tuple[_NamedSymbolMatch, ...], bool]:
    by_key = {_named_match_key(match): match for match in (*existing, *additions)}
    ordered = tuple(by_key[key] for key in sorted(by_key))
    return ordered[:limit], len(ordered) > limit


def _named_match_key(
    match: _NamedSymbolMatch,
) -> tuple[str, int, int, int, int, int, str]:
    location = match.candidate.location
    return (
        match.path,
        location.range.start.line,
        location.range.start.character,
        location.range.end.line,
        location.range.end.character,
        match.candidate.kind or 0,
        match.provider_name,
    )


def _deduplicate_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def _normalized_language(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _provider_languages(provider: ConfiguredProvider) -> set[str]:
    languages = {_normalized_language(extension) for extension in provider.extensions}
    for extension in provider.extensions:
        language = _normalized_language(provider.to_spec(extension).language)
        languages.add(language)
        if language.endswith("react"):
            languages.add(language.removesuffix("react"))
    return languages
