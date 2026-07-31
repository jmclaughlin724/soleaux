"""Closed editor preview/apply result contracts (D009, D027)."""

from __future__ import annotations

import datetime
import enum
import typing

import pydantic

import soleaux.lsp.contracts

PREVIEW_SCHEMA_VERSION = "soleaux.preview/v1"


class ApplyState(enum.StrEnum):
    """Exact outcome of one preview application attempt."""

    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    PARTIAL_FAILURE = "partial_failure"
    CONFLICTED = "conflicted"


class EditPatch(pydantic.BaseModel):
    """One normalized, repository-relative text replacement."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    range: soleaux.lsp.contracts.LspRange
    start_byte: int = pydantic.Field(ge=0)
    end_byte: int = pydantic.Field(ge=0)
    new_text: str
    preimage_hash: str = pydantic.Field(min_length=64, max_length=64)


class PreviewPayload(pydantic.BaseModel):
    """Reviewable no-write result bound to one process and provider generation."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.preview/v1"] = PREVIEW_SCHEMA_VERSION
    preview_id: str = pydantic.Field(min_length=1)
    digest: str = pydantic.Field(min_length=64, max_length=64)
    workspace_id: str = pydantic.Field(min_length=1)
    process_epoch: str = pydantic.Field(min_length=1)
    origin: typing.Literal["lsp", "structural"] = "lsp"
    provider_name: str = pydantic.Field(min_length=1)
    provider_epoch: int = pydantic.Field(ge=0)
    engine_version: str | None = None
    rule_digest: str | None = pydantic.Field(default=None, min_length=64, max_length=64)
    generation_fingerprint: str = pydantic.Field(min_length=1)
    operation: str = pydantic.Field(min_length=1)
    target: dict[str, object]
    affected_paths: tuple[str, ...]
    preimage_hashes: dict[str, str]
    postimage_hashes: dict[str, str]
    position_encoding: str = pydantic.Field(min_length=1)
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    patches: tuple[EditPatch, ...]
    diff: str
    diff_truncated: bool


class FileApplyResult(pydantic.BaseModel):
    """Observed state for one file in an apply transaction."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    state: ApplyState
    preimage_hash: str = pydantic.Field(min_length=64, max_length=64)
    postimage_hash: str = pydantic.Field(min_length=64, max_length=64)
    live_hash: str | None = pydantic.Field(default=None, min_length=64, max_length=64)


class ApplyPayload(pydantic.BaseModel):
    """Exact per-file and overall result of an apply attempt."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.preview/v1"] = PREVIEW_SCHEMA_VERSION
    preview_id: str = pydantic.Field(min_length=1)
    state: ApplyState
    files: tuple[FileApplyResult, ...] = ()
    message: str | None = None
