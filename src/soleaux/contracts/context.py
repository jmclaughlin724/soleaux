"""Typed task-context packet (`soleaux.context/v1`).

The packet is the bounded, single-call handoff used before repository work
begins.  It keeps repository evidence, consumer relationships, configured
external references, and honest coverage gaps structurally distinct.
"""

from __future__ import annotations

import enum
import hashlib
import typing

import pydantic

CONTEXT_SCHEMA_VERSION = "soleaux.context/v1"
MAX_PACKET_GAPS = 64
_SHA256_HEX_CHARACTERS = frozenset("0123456789abcdef")


class ContextSection(enum.StrEnum):
    """Closed packet sections for task-relevant repository evidence."""

    SOURCE = "source"
    CANONICAL_OWNER = "canonical_owner"
    CONSUMER = "consumer"
    CONSTRAINT = "constraint"
    CONFLICT = "conflict"
    VALIDATION_ROUTE = "validation_route"
    SUPPORTING_FACT = "supporting_fact"


class ContextReference(pydantic.BaseModel):
    """One caller-supplied or configured resource resolved before analysis."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    uri: str = pydantic.Field(min_length=1, max_length=2048)
    title: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    media_type: str | None = pydantic.Field(default=None, min_length=1, max_length=255)
    content: str = pydantic.Field(max_length=65536)
    sha256: str | None = pydantic.Field(
        default=None,
        description="SHA-256 of the complete content before response truncation.",
    )
    truncated: bool = False
    error: str | None = pydantic.Field(default=None, min_length=1, max_length=1024)

    @pydantic.model_validator(mode="after")
    def _validate_digest(self) -> typing.Self:
        if self.sha256 is None:
            return self
        if len(self.sha256) != 64 or any(
            character not in _SHA256_HEX_CHARACTERS for character in self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        if (
            not self.truncated
            and hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.sha256
        ):
            raise ValueError("sha256 must match the complete reference content")
        return self


class TaskContextItem(pydantic.BaseModel):
    """One ranked fact in the relation-complete task packet."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    table: str = pydantic.Field(min_length=1)
    section: ContextSection
    identity: str = pydantic.Field(min_length=1, max_length=2048)
    summary: str = pydantic.Field(min_length=1, max_length=1024)
    data: dict[str, typing.Any]
    evidence_id: str = pydantic.Field(min_length=1)
    path: str = pydantic.Field(min_length=1)
    start_line: int = pydantic.Field(ge=1)
    end_line: int = pydantic.Field(ge=1)
    relation_distance: int = pydantic.Field(ge=0, le=3)


class ContextGap(pydantic.BaseModel):
    """One explicit reason the packet cannot claim complete coverage."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    code: str = pydantic.Field(min_length=1, max_length=128)
    message: str = pydantic.Field(min_length=1, max_length=1024)
    table: str | None = pydantic.Field(default=None, min_length=1)
    path: str | None = pydantic.Field(default=None, min_length=1)


class TaskContextPacket(pydantic.BaseModel):
    """The complete bounded context needed to start one repository task."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.context/v1"] = CONTEXT_SCHEMA_VERSION
    objective: str = pydantic.Field(min_length=1, max_length=65536)
    paths: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    retrieval_engine: str = pydantic.Field(min_length=1)
    relation_depth: int = pydantic.Field(default=2, ge=0, le=3)
    sources: tuple[TaskContextItem, ...] = ()
    canonical_owners: tuple[TaskContextItem, ...] = ()
    consumers: tuple[TaskContextItem, ...] = ()
    constraints: tuple[TaskContextItem, ...] = ()
    conflicts: tuple[TaskContextItem, ...] = ()
    validation_routes: tuple[TaskContextItem, ...] = ()
    supporting_facts: tuple[TaskContextItem, ...] = ()
    external_references: tuple[ContextReference, ...] = ()
    gaps: tuple[ContextGap, ...] = pydantic.Field(default=(), max_length=MAX_PACKET_GAPS)
    ranked_candidate_count: int = pydantic.Field(ge=0)
    related_fact_count: int = pydantic.Field(ge=0)
    returned_item_count: int = pydantic.Field(ge=0)
    response_truncated: bool = False
    coverage_complete: bool

    @property
    def items(self) -> tuple[TaskContextItem, ...]:
        """Return all packet items in stable section order."""
        return (
            *self.sources,
            *self.canonical_owners,
            *self.consumers,
            *self.constraints,
            *self.conflicts,
            *self.validation_routes,
            *self.supporting_facts,
        )
