"""AnalysisFrame: the single canonical pipeline output (D017).

Server, CLI, doctor, inspect, and benchmark adapters all consume exactly this
model; there is no second graph model and nothing persists across requests.
"""

from __future__ import annotations

import typing

import pydantic

import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.requests


class FactRow(pydantic.BaseModel):
    """One typed table row with its mandatory evidence record."""

    model_config = pydantic.ConfigDict(extra="forbid")

    table: str = pydantic.Field(min_length=1)
    data: dict[str, typing.Any]
    evidence: soleaux.contracts.evidence.Evidence


class AnalysisFrame(pydantic.BaseModel):
    """The one frame every surface serializes from."""

    model_config = pydantic.ConfigDict(extra="forbid")

    snapshot_id: str = pydantic.Field(min_length=1)
    workspace_id: str = pydantic.Field(min_length=1)
    semantic_mode: soleaux.contracts.requests.SemanticMode
    coverage: soleaux.contracts.coverage.Coverage
    tables: dict[str, tuple[FactRow, ...]] = pydantic.Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
