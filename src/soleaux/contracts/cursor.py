"""Cursor contract (`soleaux.cursor/v1`).

Search continuation cursors bind process epoch, workspace, snapshot read set,
normalized query digest, and limits to one offset. This module owns only the
model and its validation; the service owns generation from a process-ephemeral
key.
"""

from __future__ import annotations

import typing

import pydantic

CURSOR_SCHEMA_VERSION = "soleaux.cursor/v1"


class CursorPayload(pydantic.BaseModel):
    """The decoded, validated cursor body."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.cursor/v1"] = CURSOR_SCHEMA_VERSION
    process_epoch: str = pydantic.Field(min_length=1)
    workspace_id: str = pydantic.Field(min_length=1)
    snapshot_id: str = pydantic.Field(min_length=1)
    query_digest: str = pydantic.Field(min_length=1)
    limit: int = pydantic.Field(ge=1)
    offset: int = pydantic.Field(ge=0)
