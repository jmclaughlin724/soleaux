"""Serializable structural rows: the only output the structural plane emits."""

from __future__ import annotations

from importlib.metadata import version
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

PROJECTION_SCHEMA_VERSION = "soleaux.projection/v1"
EXTRACTOR_VERSION = "3"
AST_GREP_VERSION = "0.44.1"
STRUCTURAL_WORKER_CAPABILITIES = ("soleaux.structural/v1",)
LIBCST_VERSION = version("libcst")
AST_GREP_ANALYZER_ID = "structural:ast-grep"
LIBCST_ANALYZER_ID = "structural:libcst"


def analyzer_id_for(language: str) -> str:
    """The structural analyzer identity that owns one language's source facts."""
    return LIBCST_ANALYZER_ID if language.casefold() == "python" else AST_GREP_ANALYZER_ID


def analyzer_version_for(language: str) -> str:
    """The pinned version of the analyzer that produces rows for `language`.

    Language-to-analyzer is one-to-one, so this is the single value that binds a
    cached row to the parser that made it.
    """
    return LIBCST_VERSION if language.casefold() == "python" else AST_GREP_VERSION


MAX_TEXT_PREVIEW = 120


class SyntaxFragment(BaseModel):
    """One compact serializable row; never a live AST handle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["soleaux.projection/v1"] = PROJECTION_SCHEMA_VERSION
    projection: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    name: str | None = None
    path: str = Field(min_length=1)
    language: str = Field(min_length=1)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    start_line: int = Field(ge=0)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=0)
    end_column: int = Field(ge=0)
    text_preview: str = Field(default="", max_length=MAX_TEXT_PREVIEW)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class FragmentDiagnostic(BaseModel):
    """One structural diagnostic row (the structural side of quality.diagnostics)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    language: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    message: str = Field(min_length=1)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    start_line: int = Field(ge=0)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=0)
    end_column: int = Field(ge=0)
