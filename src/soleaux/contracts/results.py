"""Response envelope (`soleaux.mcp/v1`) shared by every tool and adapter."""

from __future__ import annotations

import enum
import typing

import pydantic

import soleaux.contracts.context
import soleaux.contracts.coverage
import soleaux.contracts.evidence

ENVELOPE_SCHEMA_VERSION = "soleaux.mcp/v1"


class ResultStatus(enum.StrEnum):
    """Envelope status."""

    OK = "ok"
    ERROR = "error"


class SuggestedRequest(pydantic.BaseModel):
    """One inert suggestion; it never enables a table or producer."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    tool: str = pydantic.Field(min_length=1)
    args: dict[str, typing.Any] = pydantic.Field(default_factory=dict)


class ErrorDetail(pydantic.BaseModel):
    """Typed failure detail."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    error_type: str = pydantic.Field(min_length=1)
    message: str = pydantic.Field(min_length=1)
    retryable: bool = False


class ResponseEnvelope[DataT = dict[str, typing.Any]](pydantic.BaseModel):
    """The one response envelope for every Soleaux surface."""

    model_config = pydantic.ConfigDict(extra="forbid")

    schema_version: typing.Literal["soleaux.mcp/v1"] = ENVELOPE_SCHEMA_VERSION
    product_version: str = pydantic.Field(min_length=1)
    request_id: str = pydantic.Field(min_length=1)
    workspace_id: str | None = None
    snapshot_id: str | None = None
    status: ResultStatus
    data: DataT | None = None
    rows: list[dict[str, typing.Any]] | None = None
    evidence: list[soleaux.contracts.evidence.Evidence] = pydantic.Field(
        default_factory=list[soleaux.contracts.evidence.Evidence]
    )
    coverage: soleaux.contracts.coverage.Coverage | None = None
    warnings: list[str] = pydantic.Field(default_factory=list[str])
    next_cursor: str | None = None
    suggested_next_requests: list[SuggestedRequest] = pydantic.Field(
        default_factory=list[SuggestedRequest]
    )
    error: ErrorDetail | None = None


class TaskContextEnvelope(ResponseEnvelope[soleaux.contracts.context.TaskContextPacket]):
    """Response envelope whose data is the typed v1 task-context packet."""
