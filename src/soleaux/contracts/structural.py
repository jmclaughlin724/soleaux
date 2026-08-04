"""The `soleaux.structural/v1` contract: matchers, findings, and rewrites.

One closed, engine-neutral surface for structural search, lint, and rewrite
preview. Callers select a registered language and supply either one inline
matcher or one rule reference; they never supply parser packages, filesystem
paths, commands, or raw YAML. Engines return serialized findings and edits
only — a live AST handle never crosses this boundary.
"""

from __future__ import annotations

import enum
import typing

import pydantic

STRUCTURAL_SCHEMA_VERSION = "soleaux.structural/v1"

MAX_PATTERN_CHARS = 2048
MAX_RULE_BYTES = 16 * 1024
MAX_CAPTURES_PER_FINDING = 16
MAX_CAPTURE_CHARS = 200


class StructuralBackend(enum.StrEnum):
    """Exactly one configured engine serves each request path."""

    PYTHON = "python"
    NAPI = "napi"
    RUST = "rust"


class FixTransformKind(enum.StrEnum):
    """The stable ast-grep 0.45.0 metavariable transformations."""

    REPLACE = "replace"
    SUBSTRING = "substring"
    CONVERT = "convert"


class _ContractBase(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")


class FixTransform(_ContractBase):
    """One named transformation applied to a metavariable before templating."""

    kind: FixTransformKind
    source: str = pydantic.Field(min_length=1, max_length=200)
    replace: str | None = pydantic.Field(default=None, max_length=200)
    by: str | None = pydantic.Field(default=None, max_length=200)
    start_char: int | None = None
    end_char: int | None = None
    to_case: str | None = pydantic.Field(default=None, max_length=32)
    separated_by: tuple[str, ...] = ()


class FixConfig(_ContractBase):
    """A structured fix: template plus optional boundary expansion."""

    template: str = pydantic.Field(max_length=MAX_PATTERN_CHARS)
    expand_start: str | None = pydantic.Field(default=None, max_length=200)
    expand_end: str | None = pydantic.Field(default=None, max_length=200)


class InlinePattern(_ContractBase):
    """One bounded pattern matcher against a registered language."""

    kind: typing.Literal["pattern"] = "pattern"
    language: str = pydantic.Field(min_length=1, max_length=64)
    pattern: str = pydantic.Field(min_length=1, max_length=MAX_PATTERN_CHARS)
    fix: str | FixConfig | None = None
    transforms: dict[str, FixTransform] = pydantic.Field(default_factory=dict)


class InlineRule(_ContractBase):
    """One bounded structured rule with official matching fields only."""

    kind: typing.Literal["rule"] = "rule"
    language: str = pydantic.Field(min_length=1, max_length=64)
    rule: dict[str, typing.Any] = pydantic.Field(min_length=1)
    constraints: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    utils: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    fix: str | FixConfig | None = None
    transforms: dict[str, FixTransform] = pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="after")
    def _rule_stays_bounded(self) -> InlineRule:
        import json

        encoded = json.dumps(
            {"rule": self.rule, "constraints": self.constraints, "utils": self.utils},
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > MAX_RULE_BYTES:
            raise ValueError(f"inline rule exceeds {MAX_RULE_BYTES} bytes")
        return self


class RuleReference(_ContractBase):
    """One Soleaux-packaged or workspace-configured rule selected by exact ID."""

    kind: typing.Literal["rule_ref"] = "rule_ref"
    rule_id: str = pydantic.Field(min_length=1, max_length=200)


StructuralMatcher = typing.Annotated[
    InlinePattern | InlineRule | RuleReference,
    pydantic.Field(discriminator="kind"),
]


class StructuralCapture(_ContractBase):
    """One bounded metavariable capture."""

    name: str = pydantic.Field(min_length=1, max_length=64)
    text: str = pydantic.Field(max_length=MAX_CAPTURE_CHARS)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)


class StructuralFinding(_ContractBase):
    """One normalized match with UTF-8 byte and line/column coordinates."""

    schema_version: typing.Literal["soleaux.structural/v1"] = STRUCTURAL_SCHEMA_VERSION
    path: str = pydantic.Field(min_length=1)
    rule_id: str | None = None
    engine: StructuralBackend
    engine_version: str = pydantic.Field(min_length=1)
    language: str = pydantic.Field(min_length=1)
    severity: str | None = None
    message: str | None = None
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    start_line: int = pydantic.Field(ge=0)
    start_column: int = pydantic.Field(ge=0)
    end_line: int = pydantic.Field(ge=0)
    end_column: int = pydantic.Field(ge=0)
    text_preview: str = pydantic.Field(default="", max_length=200)
    captures: tuple[StructuralCapture, ...] = pydantic.Field(
        default=(),
        max_length=MAX_CAPTURES_PER_FINDING,
    )


class StructuralEdit(_ContractBase):
    """One byte-exact replacement inside a single file."""

    path: str = pydantic.Field(min_length=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    inserted_text: str


class StructuralUnsupportedError(ValueError):
    """A matcher requested a capability outside the stable v1 contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: Rule keys the v1 contract accepts inside `InlineRule.rule` and configured
#: rule files: official matching, applicability, and composition fields.
SUPPORTED_RULE_FIELDS = frozenset(
    {
        "all",
        "any",
        "not",
        "matches",
        "pattern",
        "kind",
        "regex",
        "nthChild",
        "range",
        "inside",
        "has",
        "follows",
        "precedes",
    }
)

#: Experimental rewriter fields the v1 contract rejects with a typed error.
REJECTED_REWRITER_FIELDS = frozenset({"rewriters", "rewrite", "applyRewriters"})


def validate_rule_fields(rule: dict[str, typing.Any]) -> None:
    """Reject unknown or experimental fields with a typed unsupported error."""
    for key in rule:
        if key in REJECTED_REWRITER_FIELDS:
            raise StructuralUnsupportedError(
                f"experimental rewriter field {key!r} is outside soleaux.structural/v1"
            )
        if key not in SUPPORTED_RULE_FIELDS:
            raise StructuralUnsupportedError(f"rule field {key!r} is outside soleaux.structural/v1")
