"""RepositorySnapshot contract: the request-scoped read set (D001, D017)."""

from __future__ import annotations

import datetime
import enum

import pydantic


class ClaimBasis(enum.StrEnum):
    """What a captured fact may legitimately claim."""

    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    MANIFEST = "manifest"
    POLICY = "policy"
    REGISTRATION = "registration"
    HISTORY = "history"
    DERIVED = "derived"


class CapturedFile(pydantic.BaseModel):
    """One admitted file with its content identity and capture provenance."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = pydantic.Field(min_length=1)
    path: str = pydantic.Field(min_length=1)
    content_hash: str = pydantic.Field(min_length=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    start_line: int = pydantic.Field(ge=0)
    start_column: int = pydantic.Field(ge=0)
    end_line: int = pydantic.Field(ge=0)
    end_column: int = pydantic.Field(ge=0)
    encoding: str = "utf-8"
    newline: str = "lf"
    language: str | None = None
    language_id: str | None = None
    parser_id: str | None = None
    digest_algorithm: str = "sha256"
    producer_id: str = pydantic.Field(min_length=1)
    producer_version: str = pydantic.Field(min_length=1)
    producer_config_digest: str = pydantic.Field(min_length=1)
    claim_basis: ClaimBasis


class RepositorySnapshot(pydantic.BaseModel):
    """A frozen view of the live checkout for exactly one request (D001)."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = pydantic.Field(min_length=1)
    workspace_id: str = pydantic.Field(min_length=1)
    root: str = pydantic.Field(min_length=1)
    created_at: datetime.datetime
    files: tuple[CapturedFile, ...] = ()
    source_fingerprint: str = pydantic.Field(min_length=1)
    changed_during_analysis: bool = False
