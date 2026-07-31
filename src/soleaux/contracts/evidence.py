"""Evidence row schema (`soleaux.evidence/v1`).

Every fact row carries one Evidence record. The schema makes it impossible for
an unresolved ast-grep match to masquerade as a semantic edge.
"""

from __future__ import annotations

import enum
import typing

import pydantic

import soleaux.contracts.validation

EVIDENCE_SCHEMA_VERSION = "soleaux.evidence/v1"

MAX_NOTE_LENGTH = 280


class EvidenceKind(enum.StrEnum):
    """Producer class for one fact."""

    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    METADATA = "metadata"
    HEURISTIC = "heuristic"


class ResolutionStatus(enum.StrEnum):
    """How far the producer resolved this fact."""

    RESOLVED = "resolved"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"
    CANDIDATE = "candidate"


class Authority(enum.StrEnum):
    """Claim basis for one fact."""

    SOURCE = "source"
    MANIFEST = "manifest"
    GOVERNANCE = "governance"
    GENERATED = "generated"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class PositionRange(pydantic.BaseModel):
    """One-based line/column range with optional byte offsets."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    start_line: int = pydantic.Field(ge=1)
    start_column: int = pydantic.Field(ge=1)
    end_line: int = pydantic.Field(ge=1)
    end_column: int = pydantic.Field(ge=1)
    byte_start: int | None = pydantic.Field(default=None, ge=0)
    byte_end: int | None = pydantic.Field(default=None, ge=0)


class Evidence(pydantic.BaseModel):
    """One bounded, source-traceable evidence record."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.evidence/v1"] = EVIDENCE_SCHEMA_VERSION
    evidence_id: str = pydantic.Field(min_length=1)
    evidence_kind: EvidenceKind
    resolution_status: ResolutionStatus
    provider: str = pydantic.Field(min_length=1)
    provider_version: str = pydantic.Field(min_length=1)
    authority: Authority
    snapshot_id: str = pydantic.Field(min_length=1)
    path: str = pydantic.Field(min_length=1)
    range: PositionRange
    source_hash: str
    source_fingerprint: str = pydantic.Field(min_length=1)
    confidence: float = pydantic.Field(ge=0.0, le=1.0)
    note: str = ""

    @pydantic.field_validator("path")
    @classmethod
    def _path_is_workspace_relative(cls, value: str) -> str:
        if soleaux.contracts.validation.starts_with_absolute_path(value):
            msg = "path must be workspace-relative, not absolute"
            raise ValueError(msg)
        if ".." in value.split("/"):
            msg = "path must not contain '..' segments"
            raise ValueError(msg)
        return value

    @pydantic.field_validator("source_hash")
    @classmethod
    def _hash_is_lowercase_sha256(cls, value: str) -> str:
        if not soleaux.contracts.validation.is_lowercase_sha256(value):
            msg = "source_hash must be lowercase hex SHA-256 over exact captured bytes"
            raise ValueError(msg)
        return value

    @pydantic.field_validator("note")
    @classmethod
    def _note_is_bounded(cls, value: str) -> str:
        if len(value) > MAX_NOTE_LENGTH:
            msg = f"note exceeds {MAX_NOTE_LENGTH} characters"
            raise ValueError(msg)
        if soleaux.contracts.validation.starts_with_absolute_path(value):
            msg = "note must not contain absolute paths"
            raise ValueError(msg)
        return value
