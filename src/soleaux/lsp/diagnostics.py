"""Generation-bound per-URI state for LSP push and pull diagnostics."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[str, object])


class DiagnosticProtocolError(ValueError):
    """A provider diagnostic payload violates the negotiated LSP contract."""


class _PublishDiagnosticsParams(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    uri: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=0)
    diagnostics: list[dict[str, JsonValue]]


class _FullDiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, strict=True)

    kind: Literal["full"]
    result_id: str | None = Field(default=None, alias="resultId")
    items: list[dict[str, JsonValue]]


class _UnchangedDiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, strict=True)

    kind: Literal["unchanged"]
    result_id: str = Field(min_length=1, alias="resultId")


@dataclass(frozen=True, slots=True)
class DiagnosticState:
    """One diagnostic result proven against a document and provider generation."""

    uri: str
    items: tuple[dict[str, JsonValue], ...]
    version: int | None
    result_id: str | None
    provider_epoch: int
    generation_fingerprint: str
    updated_at: float


@dataclass(frozen=True, slots=True)
class _DiagnosticBinding:
    uri: str
    document_version: int
    provider_epoch: int
    generation_fingerprint: str


class DiagnosticStateStore:
    """The single owner of normalized diagnostic state for one LSP broker."""

    def __init__(self) -> None:
        self._bindings: dict[str, _DiagnosticBinding] = {}
        self._states: dict[str, DiagnosticState] = {}
        self._events: dict[str, asyncio.Event] = {}

    def bind(
        self,
        uri: str,
        *,
        document_version: int,
        provider_epoch: int,
        generation_fingerprint: str,
    ) -> None:
        """Bind future provider results to one exact open-document generation."""
        if not uri:
            raise ValueError("diagnostic URI must not be empty")
        if document_version < 0:
            raise ValueError("diagnostic document version must be non-negative")
        if provider_epoch < 0:
            raise ValueError("diagnostic provider epoch must be non-negative")
        if not generation_fingerprint:
            raise ValueError("diagnostic generation fingerprint must not be empty")

        binding = _DiagnosticBinding(
            uri=uri,
            document_version=document_version,
            provider_epoch=provider_epoch,
            generation_fingerprint=generation_fingerprint,
        )
        self._bindings[uri] = binding
        state = self._states.get(uri)
        event = self._events.setdefault(uri, asyncio.Event())
        if state is None or not self._compatible(state, binding):
            self._states.pop(uri, None)
            event.clear()
        else:
            event.set()

    def publish(self, params: object) -> bool:
        """Replace one URI's push state; return false for stale or invalid data."""
        try:
            publication = _PublishDiagnosticsParams.model_validate(params)
        except ValidationError:
            return False
        binding = self._bindings.get(publication.uri)
        if binding is None:
            return False
        if publication.version is not None and publication.version != binding.document_version:
            return False

        current = self._states.get(publication.uri)
        if (
            current is not None
            and publication.version is not None
            and current.version is not None
            and publication.version < current.version
        ):
            return False
        state = DiagnosticState(
            uri=publication.uri,
            items=tuple(publication.diagnostics),
            version=publication.version,
            result_id=None,
            provider_epoch=binding.provider_epoch,
            generation_fingerprint=binding.generation_fingerprint,
            updated_at=time.monotonic(),
        )
        self._states[publication.uri] = state
        self._events.setdefault(publication.uri, asyncio.Event()).set()
        return True

    def apply_pull_report(self, uri: str, report: object) -> DiagnosticState:
        """Normalize one full or unchanged pull report into retained URI state."""
        binding = self._bindings.get(uri)
        if binding is None:
            raise DiagnosticProtocolError(f"diagnostic URI is not bound: {uri!r}")
        if not isinstance(report, dict):
            raise DiagnosticProtocolError("invalid document diagnostic report")
        mapping = _OBJECT_MAPPING_ADAPTER.validate_python(report, strict=True)
        kind = mapping.get("kind")
        try:
            if kind == "full":
                parsed: _FullDiagnosticReport | _UnchangedDiagnosticReport = (
                    _FullDiagnosticReport.model_validate(mapping)
                )
            elif kind == "unchanged":
                parsed = _UnchangedDiagnosticReport.model_validate(mapping)
            else:
                raise DiagnosticProtocolError("invalid document diagnostic report kind")
        except ValidationError as exc:
            raise DiagnosticProtocolError("invalid document diagnostic report") from exc

        if isinstance(parsed, _FullDiagnosticReport):
            items = tuple(parsed.items)
        else:
            current = self.current(
                uri,
                document_version=binding.document_version,
                provider_epoch=binding.provider_epoch,
                generation_fingerprint=binding.generation_fingerprint,
            )
            if current is None or current.result_id is None:
                raise DiagnosticProtocolError(
                    "unchanged diagnostic report requires prior compatible state"
                )
            items = current.items

        state = DiagnosticState(
            uri=uri,
            items=items,
            version=binding.document_version,
            result_id=parsed.result_id,
            provider_epoch=binding.provider_epoch,
            generation_fingerprint=binding.generation_fingerprint,
            updated_at=time.monotonic(),
        )
        self._states[uri] = state
        self._events.setdefault(uri, asyncio.Event()).set()
        return state

    def current(
        self,
        uri: str,
        *,
        document_version: int,
        provider_epoch: int,
        generation_fingerprint: str,
    ) -> DiagnosticState | None:
        """Return state only when every freshness dimension matches."""
        state = self._states.get(uri)
        if state is None:
            return None
        binding = _DiagnosticBinding(
            uri=uri,
            document_version=document_version,
            provider_epoch=provider_epoch,
            generation_fingerprint=generation_fingerprint,
        )
        return state if self._compatible(state, binding) else None

    def previous_result_id(
        self,
        uri: str,
        *,
        document_version: int,
        provider_epoch: int,
        generation_fingerprint: str,
    ) -> str | None:
        """Return the pull cursor only for compatible retained state."""
        state = self.current(
            uri,
            document_version=document_version,
            provider_epoch=provider_epoch,
            generation_fingerprint=generation_fingerprint,
        )
        return state.result_id if state is not None else None

    async def wait(
        self,
        uri: str,
        *,
        document_version: int,
        provider_epoch: int,
        generation_fingerprint: str,
        timeout: float,
    ) -> DiagnosticState | None:
        """Wait for state matching one URI, document version, epoch, and generation."""
        if timeout <= 0:
            raise ValueError("diagnostic wait timeout must be positive")
        event = self._events.setdefault(uri, asyncio.Event())
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            state = self.current(
                uri,
                document_version=document_version,
                provider_epoch=provider_epoch,
                generation_fingerprint=generation_fingerprint,
            )
            if state is not None:
                return state
            event.clear()
            state = self.current(
                uri,
                document_version=document_version,
                provider_epoch=provider_epoch,
                generation_fingerprint=generation_fingerprint,
            )
            if state is not None:
                return state
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                async with asyncio.timeout(remaining):
                    await event.wait()
            except TimeoutError:
                return None

    def invalidate(self) -> None:
        """Discard result IDs and items while preserving live URI bindings."""
        self._states.clear()
        for event in self._events.values():
            event.set()

    def clear(self) -> None:
        """Release every provider-epoch binding and wake bounded waiters."""
        self._bindings.clear()
        self._states.clear()
        for event in self._events.values():
            event.set()
        self._events.clear()

    @staticmethod
    def _compatible(state: DiagnosticState, binding: _DiagnosticBinding) -> bool:
        version_matches = state.version is None or state.version == binding.document_version
        return (
            state.uri == binding.uri
            and version_matches
            and state.provider_epoch == binding.provider_epoch
            and state.generation_fingerprint == binding.generation_fingerprint
        )
