"""Closed request models for the ten-tool MCP catalog.

D029: one shared `semantic_mode` enum with exactly `syntax_only`,
`best_available` (default), and `semantic_required`; `required` is not an
alias and unknown values fail validation.

`SemanticOperation` and `InspectOperation` live here as the canonical MCP
contract surface; `soleaux.lsp.contracts` re-imports them so the LSP layer
keeps its single navigation-capability mapping.
"""

from __future__ import annotations

import enum
import typing

import pydantic

import soleaux.contracts.structural
from soleaux.contracts.context import ContextReference

_MAX_SEARCH_PATHS = 256
_MAX_CONTEXT_REFERENCES = 32
SearchPath = typing.Annotated[str, pydantic.Field(min_length=1)]


def _wire_enum[EnumT: enum.StrEnum](
    enum_type: type[EnumT],
) -> pydantic.BeforeValidator:
    def decode(value: object) -> object:
        return enum_type(value) if isinstance(value, str) else value

    return pydantic.BeforeValidator(decode)


class SemanticMode(enum.StrEnum):
    """Semantic resolution mode shared by every semantic-capable request."""

    SYNTAX_ONLY = "syntax_only"
    BEST_AVAILABLE = "best_available"
    SEMANTIC_REQUIRED = "semantic_required"


class SearchKind(enum.StrEnum):
    """The closed vocabulary of searchable typed catalog facts."""

    CHUNK = "chunk"
    FILE = "file"
    PROJECT = "project"
    DEPENDENCY = "dependency"
    SCRIPT = "script"
    CONFIG = "config"
    TASK = "task"
    ROUTE = "route"
    RULE = "rule"
    SYMBOL = "symbol"
    IMPORT = "import"
    DIAGNOSTIC = "diagnostic"
    CHANGE = "change"
    POLICY = "policy"


class PreviewOperation(enum.StrEnum):
    """The editor preview kinds: LSP-textual plus one structural rewrite."""

    RENAME = "rename"
    FORMAT_DOCUMENT = "format_document"
    FORMAT_RANGE = "format_range"
    CODE_ACTION = "code_action"
    STRUCTURAL_REWRITE = "structural_rewrite"


class RenameTarget(enum.StrEnum):
    """How a rename target is selected before the LSP request."""

    NAME = "name"
    POSITION = "position"


class SemanticOperation(enum.StrEnum):
    """The seven semantic navigation operations mapped to LSP methods."""

    DEFINITION = "definition"
    REFERENCES = "references"
    IMPLEMENTATION = "implementation"
    HOVER = "hover"
    CALL_HIERARCHY = "call_hierarchy"
    INCOMING_CALLS = "incoming_calls"
    OUTGOING_CALLS = "outgoing_calls"


class InspectOperation(enum.StrEnum):
    """The four inspect operations mapped to LspCapability."""

    DIAGNOSTICS = "diagnostics"
    COMPLETION = "completion"
    SIGNATURE_HELP = "signature_help"
    CODE_ACTIONS = "code_actions"


class OwnershipView(enum.StrEnum):
    """The ownership projection returned by one paginated request."""

    DECISIONS = "decisions"
    IDENTITIES = "identities"


class _RequestBase(pydantic.BaseModel):
    """Common closed base: workspace selection and semantic mode."""

    model_config = pydantic.ConfigDict(extra="forbid")

    workspace_id: str | None = None
    semantic_mode: typing.Annotated[SemanticMode, _wire_enum(SemanticMode)] = (
        SemanticMode.BEST_AVAILABLE
    )


class SearchRequest(_RequestBase):
    """Ranked repository facts from one lifecycle-published SQLite generation."""

    query: str = pydantic.Field(min_length=1, max_length=2048)
    kinds: list[typing.Annotated[SearchKind, _wire_enum(SearchKind)]] = pydantic.Field(
        default_factory=list[SearchKind],
        max_length=len(SearchKind),
    )
    paths: list[SearchPath] = pydantic.Field(
        default_factory=list,
        max_length=_MAX_SEARCH_PATHS,
    )
    context_lines: int = pydantic.Field(default=2, ge=0, le=20)
    cursor: str | None = None
    limit: int = pydantic.Field(default=20, ge=1, le=200)


class LintRequest(_RequestBase):
    """Run configured workspace rules; findings are honest, bounded evidence."""

    paths: list[SearchPath] = pydantic.Field(
        default_factory=list,
        max_length=_MAX_SEARCH_PATHS,
    )
    rule_ids: list[typing.Annotated[str, pydantic.Field(min_length=1, max_length=200)]] = (
        pydantic.Field(
            default_factory=list,
            max_length=100,
        )
    )
    severities: list[typing.Annotated[str, pydantic.Field(min_length=1, max_length=32)]] = (
        pydantic.Field(
            default_factory=list,
            max_length=10,
        )
    )
    limit: int = pydantic.Field(default=100, ge=1, le=1000)


class ContextRequest(_RequestBase):
    """Bounded task context assembled from repository and configured resources."""

    objective: str = pydantic.Field(min_length=1, max_length=65536)
    paths: list[SearchPath] = pydantic.Field(
        default_factory=list,
        max_length=_MAX_SEARCH_PATHS,
    )
    references: list[ContextReference] = pydantic.Field(
        default_factory=list[ContextReference],
        max_length=_MAX_CONTEXT_REFERENCES,
    )
    resource_uris: list[typing.Annotated[str, pydantic.Field(min_length=1, max_length=2048)]] = (
        pydantic.Field(
            default_factory=list,
            max_length=_MAX_CONTEXT_REFERENCES,
        )
    )
    max_bytes: int = pydantic.Field(default=32768, ge=1, le=262144)
    limit: int = pydantic.Field(default=50, ge=1, le=200)

    @pydantic.model_validator(mode="after")
    def _validate_context_resources(self) -> typing.Self:
        reference_uris = [reference.uri for reference in self.references]
        if len(reference_uris) != len(set(reference_uris)):
            raise ValueError("references must use unique URIs")
        if len(self.resource_uris) != len(set(self.resource_uris)):
            raise ValueError("resource_uris must be unique")
        if set(reference_uris) & set(self.resource_uris):
            raise ValueError("a URI cannot appear in both references and resource_uris")
        return self


class OwnershipRequest(_RequestBase):
    """Discover policy identities or resolve them into governance relationships."""

    policy: str = pydantic.Field(min_length=1, max_length=1024)
    paths: list[SearchPath] = pydantic.Field(
        default_factory=list,
        max_length=_MAX_SEARCH_PATHS,
    )
    view: typing.Annotated[OwnershipView, _wire_enum(OwnershipView)] = OwnershipView.DECISIONS
    cursor: str | None = None
    limit: int = pydantic.Field(default=100, ge=1, le=200)


class PreviewEditRequest(_RequestBase):
    """No-write editor preview bound to workspace, epoch, target, and hashes."""

    operation: typing.Annotated[PreviewOperation, _wire_enum(PreviewOperation)]
    path: str | None = pydantic.Field(default=None, min_length=1)
    structural: soleaux.contracts.structural.StructuralMatcher | None = None
    paths: list[str] = pydantic.Field(default_factory=list, max_length=256)
    target: typing.Annotated[RenameTarget, _wire_enum(RenameTarget)] | None = None
    line: int | None = pydantic.Field(default=None, ge=1)
    column: int | None = pydantic.Field(default=None, ge=1)
    symbol_name: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    symbol_kind: str | None = pydantic.Field(default=None, min_length=1, max_length=64)
    new_name: str | None = pydantic.Field(default=None, min_length=1)
    end_line: int | None = pydantic.Field(default=None, ge=1)
    end_column: int | None = pydantic.Field(default=None, ge=1)
    action_index: int | None = pydantic.Field(default=None, ge=0, le=99)
    strict: bool = False

    @pydantic.model_validator(mode="after")
    def _validate_operation_arguments(self) -> typing.Self:
        if self.operation is PreviewOperation.STRUCTURAL_REWRITE:
            if self.structural is None:
                raise ValueError("structural_rewrite requires a structural matcher")
            if (
                self.path is not None
                or self.strict
                or any(
                    value is not None
                    for value in (
                        self.target,
                        self.line,
                        self.column,
                        self.symbol_name,
                        self.symbol_kind,
                        self.new_name,
                        self.end_line,
                        self.end_column,
                        self.action_index,
                    )
                )
            ):
                raise ValueError("structural_rewrite accepts only structural and paths")
            return self
        if self.structural is not None or self.paths:
            raise ValueError("structural arguments are only valid for structural_rewrite")
        if self.path is None:
            raise ValueError(f"{self.operation.value} requires path")
        if self.operation is PreviewOperation.RENAME:
            if self.new_name is None:
                raise ValueError("rename preview requires new_name")
            if (
                self.end_line is not None
                or self.end_column is not None
                or self.action_index is not None
            ):
                raise ValueError("rename accepts no range end or action selection")
            target = self.target
            if target is None:
                target = (
                    RenameTarget.NAME
                    if self.symbol_name is not None and not self.strict
                    else RenameTarget.POSITION
                )
            if self.strict and target is not RenameTarget.POSITION:
                raise ValueError("strict rename requires target='position'")
            if target is RenameTarget.NAME and self.symbol_name is None:
                raise ValueError("rename target='name' requires symbol_name")
            if target is RenameTarget.NAME and (self.line is not None or self.column is not None):
                raise ValueError("rename target='name' accepts no position")
            if target is RenameTarget.POSITION and (self.line is None or self.column is None):
                raise ValueError("rename target='position' requires line and column")
            if target is RenameTarget.POSITION and (
                self.symbol_name is not None or self.symbol_kind is not None
            ):
                raise ValueError("rename target='position' accepts no symbol selector")
            return self

        if self.strict or self.target is not None or self.symbol_name is not None:
            raise ValueError("rename-only arguments are not valid for this preview operation")
        if self.symbol_kind is not None or self.new_name is not None:
            raise ValueError("rename-only arguments are not valid for this preview operation")
        if self.operation is PreviewOperation.FORMAT_DOCUMENT:
            if any(
                value is not None
                for value in (
                    self.line,
                    self.column,
                    self.end_line,
                    self.end_column,
                    self.action_index,
                )
            ):
                raise ValueError("format_document accepts no range or action selection")
            return self
        if self.operation is PreviewOperation.FORMAT_RANGE:
            if any(
                value is None
                for value in (
                    self.line,
                    self.column,
                    self.end_line,
                    self.end_column,
                )
            ):
                raise ValueError("format_range requires line, column, end_line, and end_column")
            if self.action_index is not None:
                raise ValueError("format_range accepts no action selection")
            return self
        if self.line is None or self.column is None:
            raise ValueError("code_action requires line and column")
        if self.action_index is None:
            raise ValueError("code_action requires action_index")
        if (self.end_line is None) != (self.end_column is None):
            raise ValueError("code_action range end requires both end_line and end_column")
        return self


class ApplyEditRequest(_RequestBase):
    """Apply exactly one unexpired preview after preimage revalidation."""

    preview_id: str = pydantic.Field(min_length=1)
    digest: str = pydantic.Field(min_length=1)
    confirm: bool = False


class RestartLanguageServersRequest(_RequestBase):
    """Restart selected language-server sessions; process-mutating."""

    provider: str | None = None
    language: str | None = None
    path: str | None = None


class DescribeRequest(_RequestBase):
    """Introspection: product, catalog, provider, storage, and transport identity."""


class QueryRequest(_RequestBase):
    """Explicit table batch over the fixed catalog; hard-prohibited extras."""

    include_tables: list[typing.Annotated[str, pydantic.Field(min_length=1, max_length=200)]] = (
        pydantic.Field(min_length=1, max_length=200)
    )
    exclude_tables: list[typing.Annotated[str, pydantic.Field(min_length=1, max_length=200)]] = (
        pydantic.Field(default_factory=list, max_length=200)
    )
    seed_keys: list[typing.Annotated[str, pydantic.Field(min_length=3, max_length=1024)]] = (
        pydantic.Field(default_factory=list, max_length=100)
    )
    cursor: str | None = None
    limit: int = pydantic.Field(default=50, ge=1, le=200)

    @pydantic.model_validator(mode="after")
    def _validate_include_tables_non_empty(self) -> typing.Self:
        if not self.include_tables:
            raise ValueError("include_tables must contain at least one table")
        return self


class NavigateRequest(_RequestBase):
    """One bounded semantic navigation target at one exact captured generation."""

    operation: typing.Annotated[SemanticOperation, _wire_enum(SemanticOperation)]
    path: str | None = pydantic.Field(default=None, min_length=1)
    line: int | None = pydantic.Field(default=None, ge=1)
    column: int | None = pydantic.Field(default=None, ge=1)
    symbol_name: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    symbol_kind: str | None = pydantic.Field(default=None, min_length=1, max_length=64)
    limit: int = pydantic.Field(default=50, ge=1, le=200)

    @pydantic.model_validator(mode="after")
    def _validate_target(self) -> typing.Self:
        if self.symbol_name is not None:
            if self.line is not None or self.column is not None:
                raise ValueError("name navigation accepts no position")
            return self
        if self.symbol_kind is not None:
            raise ValueError("symbol_kind requires symbol_name")
        if self.path is None or self.line is None or self.column is None:
            raise ValueError("position navigation requires path, line, and column")
        return self


class InspectRequest(_RequestBase):
    """One LSP capability inspection at one position."""

    operation: typing.Annotated[InspectOperation, _wire_enum(InspectOperation)]
    path: str = pydantic.Field(min_length=1)
    line: int = pydantic.Field(ge=1)
    column: int = pydantic.Field(ge=1)
    limit: int = pydantic.Field(default=50, ge=1, le=200)
