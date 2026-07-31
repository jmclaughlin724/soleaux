"""AnalysisFrame coverage block: status, counters, limits, and timing.

Zero rows mean "none found" only under complete coverage.
"""

from __future__ import annotations

import datetime
import enum

import pydantic

MAX_OMITTED_REASONS = 64


class FrameStatus(enum.StrEnum):
    """Pipeline coverage status for one AnalysisFrame."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    TRUNCATED = "truncated"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CHANGED_DURING_ANALYSIS = "changed_during_analysis"


class RowFileByteDepthLimits(pydantic.BaseModel):
    """Bounding limits that governed one frame."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    max_rows: int = pydantic.Field(ge=1)
    max_files: int = pydantic.Field(ge=1)
    max_bytes: int = pydantic.Field(ge=1)
    max_depth: int = pydantic.Field(ge=1)


class Coverage(pydantic.BaseModel):
    """The AnalysisFrame coverage contract from CONTRACTS.md."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    status: FrameStatus
    eligible_files: int = pydantic.Field(ge=0)
    examined_files: int = pydantic.Field(ge=0)
    parse_failures: int = pydantic.Field(ge=0)
    candidate_count: int = pydantic.Field(ge=0)
    resolution_attempts: int = pydantic.Field(ge=0)
    resolved_count: int = pydantic.Field(ge=0)
    unsupported_count: int = pydantic.Field(ge=0)
    failed_count: int = pydantic.Field(ge=0)
    omitted_reasons: tuple[str, ...] = pydantic.Field(
        default=(),
        max_length=MAX_OMITTED_REASONS,
    )
    deadline: datetime.datetime
    row_file_byte_depth_limits: RowFileByteDepthLimits
    elapsed_ms: float = pydantic.Field(ge=0.0)
