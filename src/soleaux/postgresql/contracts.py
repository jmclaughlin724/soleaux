"""Closed PostgreSQL 17 contracts shared by every implementation plane.

The module owns normalized, serializable PostgreSQL facts. Parser and provider
objects terminate before this boundary; source positions and coverage reuse the
producer-neutral Soleaux contracts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soleaux.contracts.coverage import FrameStatus, RowFileByteDepthLimits
from soleaux.contracts.positions import Point, PointRange
from soleaux.contracts.validation import is_lowercase_sha256

POSTGRESQL_CONTRACT_SCHEMA_VERSION = "soleaux.postgresql/v1"


class PostgreSqlToolchain(BaseModel):
    """The only dialect and exact analyzer versions covered by this contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialect: Literal["PostgreSQL"] = "PostgreSQL"
    dialect_major: Literal[17] = 17
    provider_package: Literal["@postgres-language-server/cli"] = "@postgres-language-server/cli"
    provider_version: Literal["0.25.4"] = "0.25.4"
    parser_package: Literal["@libpg-query/parser"] = "@libpg-query/parser"
    parser_version: Literal["17.6.10"] = "17.6.10"
    parser_postgresql_major: Literal[17] = 17
    parser_delivery: Literal["runtime_provisioned_node_worker"] = "runtime_provisioned_node_worker"


class SemanticMode(StrEnum):
    """Whether facts depend only on captured source or also external database state."""

    OFFLINE = "offline"
    CONNECTED = "connected"


class Operation(StrEnum):
    """PostgreSQL operations whose support must be reported explicitly."""

    SYMBOL_SEARCH = "symbol_search"
    DEFINITION = "definition"
    REFERENCES = "references"
    DIAGNOSTICS = "diagnostics"
    COMPLETION = "completion"
    HOVER = "hover"
    TYPE_CHECK = "type_check"
    PLPGSQL_DIAGNOSTICS = "plpgsql_diagnostics"
    CODE_ACTION = "code_action"
    RESTART_PROVIDER = "restart_provider"
    SIGNATURE_HELP = "signature_help"
    IMPLEMENTATION = "implementation"
    CALL_HIERARCHY = "call_hierarchy"
    RENAME = "rename"
    FORMAT_DOCUMENT = "format_document"
    FORMAT_RANGE = "format_range"
    COMMAND_CODE_ACTION = "command_code_action"
    STATEMENT_EXECUTION = "statement_execution"


class OperationSupport(StrEnum):
    """Required offline core, optional connected enrichment, or unavailable."""

    CORE = "core"
    CONNECTED_ENRICHMENT = "connected_enrichment"
    UNAVAILABLE = "unavailable"


class OperationCapability(BaseModel):
    """One operation's fixed support and permissible semantic modes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Operation
    support: OperationSupport
    semantic_modes: tuple[SemanticMode, ...]

    @model_validator(mode="after")
    def _support_matches_modes(self) -> Self:
        expected_modes = {
            OperationSupport.CORE: (SemanticMode.OFFLINE, SemanticMode.CONNECTED),
            OperationSupport.CONNECTED_ENRICHMENT: (SemanticMode.CONNECTED,),
            OperationSupport.UNAVAILABLE: (),
        }[self.support]
        if self.semantic_modes != expected_modes:
            raise ValueError(f"{self.support.value} requires modes {expected_modes!r}")
        return self


class ObjectKind(StrEnum):
    """Persistent PostgreSQL object categories covered by source extraction."""

    SCHEMA = "schema"
    EXTENSION = "extension"
    ROLE = "role"
    ENUM = "enum"
    DOMAIN = "domain"
    COMPOSITE_TYPE = "composite_type"
    RANGE_TYPE = "range_type"
    SEQUENCE = "sequence"
    TABLE = "table"
    PARTITION = "partition"
    FOREIGN_TABLE = "foreign_table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FUNCTION = "function"
    PROCEDURE = "procedure"
    AGGREGATE = "aggregate"
    INDEX = "index"
    CONSTRAINT = "constraint"
    TRIGGER = "trigger"
    EVENT_TRIGGER = "event_trigger"
    POLICY = "policy"
    PUBLICATION = "publication"
    SUBSCRIPTION = "subscription"


class ObjectIdentity(BaseModel):
    """Stable non-routine identity: kind, optional schema, and parser-written name."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    identity_type: Literal["object"] = "object"
    kind: ObjectKind
    schema_name: str | None = Field(default=None, alias="schema", min_length=1)
    name: str = Field(min_length=1)


class ScopedObjectIdentity(BaseModel):
    """Stable identity for objects whose namespace is an owning relation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_type: Literal["scoped_object"] = "scoped_object"
    kind: Literal[ObjectKind.CONSTRAINT, ObjectKind.TRIGGER, ObjectKind.POLICY]
    relation: ObjectIdentity
    name: str = Field(min_length=1)


class RoutineSignature(BaseModel):
    """Routine sameness uses input argument type names exactly as written."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_argument_types: tuple[Annotated[str, Field(min_length=1)], ...]
    type_name_comparison: Literal["as_written"] = "as_written"


class RoutineIdentity(BaseModel):
    """Stable function, procedure, or aggregate identity."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    identity_type: Literal["routine"] = "routine"
    kind: Literal[ObjectKind.FUNCTION, ObjectKind.PROCEDURE, ObjectKind.AGGREGATE]
    schema_name: str = Field(alias="schema", min_length=1)
    name: str = Field(min_length=1)
    signature: RoutineSignature


class ColumnIdentity(BaseModel):
    """Stable column identity within one relation identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_type: Literal["column"] = "column"
    relation: ObjectIdentity
    name: str = Field(min_length=1)


type PostgreSqlIdentity = Annotated[
    ObjectIdentity | ScopedObjectIdentity | RoutineIdentity | ColumnIdentity,
    Field(discriminator="identity_type"),
]


class LocationKind(StrEnum):
    """The strongest source location the analyzer could prove."""

    EXACT_RANGE = "exact_range"
    START_ONLY = "start_only"
    LINE_ONLY = "line_only"
    UNKNOWN = "unknown"


class SourceLocation(BaseModel):
    """Exact range, start point, line-only position, or explicit unknown location."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: LocationKind
    range: PointRange | None = None
    point: Point | None = None
    line: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _location_matches_kind(self) -> Self:
        has_range = self.range is not None
        has_point = self.point is not None
        has_line = self.line is not None
        valid = {
            LocationKind.EXACT_RANGE: has_range and not has_point and not has_line,
            LocationKind.START_ONLY: has_point and not has_range and not has_line,
            LocationKind.LINE_ONLY: has_line and not has_range and not has_point,
            LocationKind.UNKNOWN: not has_range and not has_point and not has_line,
        }[self.kind]
        if not valid:
            raise ValueError(f"location payload does not match {self.kind.value}")
        return self


class SourceLane(StrEnum):
    """Repository provenance carried by facts but excluded from identity."""

    UNCLASSIFIED = "unclassified"
    DESIRED_STATE = "desired_state"
    MIGRATION_HISTORY = "migration_history"
    TEST = "test"
    GENERATED = "generated"
    FIXTURE = "fixture"


class SourceAnchor(BaseModel):
    """Snapshot- and parser-bound source evidence for one PostgreSQL fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1)
    parser_generation: str = Field(min_length=1)
    path: str = Field(min_length=1)
    statement_index: int = Field(ge=0)
    source_lane: SourceLane
    location: SourceLocation


class StatementFact(BaseModel):
    """One ordered statement with an exact scanner-derived range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceAnchor
    statement_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def _statement_has_exact_range(self) -> Self:
        if self.source.location.kind is not LocationKind.EXACT_RANGE:
            raise ValueError("statement facts require an exact scanner-derived range")
        return self


class DeclarationAction(StrEnum):
    """Source-level object lifecycle action."""

    CREATE = "create"
    ALTER = "alter"
    RENAME = "rename"
    DROP = "drop"
    CREATE_OR_REPLACE = "create_or_replace"
    ATTACH = "attach"
    DETACH = "detach"


class DeclarationFact(BaseModel):
    """One PostgreSQL declaration or object-lifecycle fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceAnchor
    action: DeclarationAction
    identity: PostgreSqlIdentity
    previous_identity: PostgreSqlIdentity | None = None


class ReferenceKind(StrEnum):
    """Kinds emitted through the public `syntax.references` contract."""

    RELATION = "relation"
    COLUMN = "column"
    ROUTINE = "routine"
    TYPE = "type"
    COLLATION = "collation"
    CONSTRAINT = "constraint"
    TRIGGER = "trigger"
    POLICY = "policy"
    ROLE = "role"
    OPERATOR = "operator"
    CAST = "cast"
    EXTENSION = "extension"
    DYNAMIC_SQL = "dynamic_sql"


class ResolutionState(StrEnum):
    """Deterministic PostgreSQL target-resolution outcome."""

    CANDIDATE = "candidate"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class TargetResolution(BaseModel):
    """Resolved target, deterministic ambiguous candidates, or explicit uncertainty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ResolutionState
    target: PostgreSqlIdentity | None = None
    candidates: tuple[PostgreSqlIdentity, ...] = ()

    @model_validator(mode="after")
    def _state_matches_targets(self) -> Self:
        if self.state is ResolutionState.RESOLVED:
            if self.target is None or self.candidates:
                raise ValueError("resolved targets require exactly one target")
        elif self.state is ResolutionState.AMBIGUOUS:
            if self.target is not None or len(self.candidates) < 2:
                raise ValueError("ambiguous targets require at least two candidates")
        elif self.target is not None or self.candidates:
            raise ValueError(f"{self.state.value} targets cannot claim resolved identities")
        return self


class ReferenceFact(BaseModel):
    """One source reference candidate and its optional semantic resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceAnchor
    reference_kind: ReferenceKind
    name_parts: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1)
    resolution: TargetResolution = Field(
        default_factory=lambda: TargetResolution(state=ResolutionState.CANDIDATE)
    )


class CallKind(StrEnum):
    """PostgreSQL invocation categories."""

    FUNCTION = "function"
    PROCEDURE = "procedure"
    TRIGGER = "trigger"


class CallFact(BaseModel):
    """One routine, procedure, or trigger call candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceAnchor
    call_kind: CallKind
    callee_parts: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1)
    argument_count: int = Field(ge=0)
    resolution: TargetResolution = Field(
        default_factory=lambda: TargetResolution(state=ResolutionState.CANDIDATE)
    )


class DiagnosticOrigin(StrEnum):
    """Normalized producer of one PostgreSQL diagnostic."""

    PARSER = "parser"
    RESOLVER = "resolver"
    PROVIDER = "provider"
    DATABASE = "database"


class DiagnosticSeverity(StrEnum):
    """Provider-neutral diagnostic severity."""

    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"
    HINT = "hint"


class DiagnosticFact(BaseModel):
    """One package-owned diagnostic with no raw provider payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceAnchor
    origin: DiagnosticOrigin
    severity: DiagnosticSeverity
    message: str = Field(min_length=1)
    code: str | None = None


class ErrorKind(StrEnum):
    """Failures that PostgreSQL implementations must preserve distinctly."""

    PARSE = "parse"
    RESOLUTION = "resolution"
    PROVIDER = "provider"
    DATABASE = "database"
    TIMEOUT = "timeout"
    TRUNCATION = "truncation"


class AnalysisError(BaseModel):
    """One normalized analysis failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ErrorKind
    message: str = Field(min_length=1)
    source: SourceAnchor | None = None
    operation: Operation | None = None
    retryable: bool = False


class ExternalStateStatus(StrEnum):
    """Whether a result consulted external database state."""

    NOT_CONSULTED = "not_consulted"
    CONSULTED = "consulted"


class ExternalStateDisclosure(BaseModel):
    """Opaque connected-state disclosure that cannot replace repository authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ExternalStateStatus = ExternalStateStatus.NOT_CONSULTED
    database_state_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    observed_at: datetime | None = None
    source_authority: Literal["repository_source"] = "repository_source"

    @field_validator("database_state_fingerprint")
    @classmethod
    def _fingerprint_is_lowercase_sha256(cls, value: str | None) -> str | None:
        if value is not None and not is_lowercase_sha256(value):
            raise ValueError("database state fingerprint must be lowercase hexadecimal SHA-256")
        return value

    @model_validator(mode="after")
    def _consulted_state_has_evidence(self) -> Self:
        has_fingerprint = self.database_state_fingerprint is not None
        has_observation = self.observed_at is not None
        if self.status is ExternalStateStatus.CONSULTED:
            if not has_fingerprint or not has_observation:
                raise ValueError(
                    "consulted external state requires a fingerprint and observation time"
                )
        elif has_fingerprint or has_observation:
            raise ValueError("unconsulted external state cannot carry database observations")
        return self


class PostgreSqlBudgets(BaseModel):
    """Frozen parser, provider, snapshot, row, byte, depth, and time limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame_limits: RowFileByteDepthLimits = RowFileByteDepthLimits(
        max_rows=200,
        max_files=4096,
        max_bytes=32 * 1024 * 1024,
        max_depth=8,
    )
    max_file_bytes: int = Field(default=4 * 1024 * 1024, ge=1)
    analysis_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    parser_timeout_seconds: float = Field(default=15.0, gt=0, allow_inf_nan=False)
    lsp_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)


class Applicability(StrEnum):
    """Prerequisite applicability, distinct from implementation support."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"


class DerivedPrerequisite(BaseModel):
    """One semantic prerequisite for a PostgreSQL derived table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    derived_table: Literal["derived.dependencies", "derived.consumers"]
    prerequisite: Literal["semantic.imports", "semantic.references", "semantic.calls"]
    applicability: Applicability
    preserves_semantic_requirement: Literal[True] = True


_OPERATION_MATRIX = (
    *(
        OperationCapability(
            operation=operation,
            support=OperationSupport.CORE,
            semantic_modes=(SemanticMode.OFFLINE, SemanticMode.CONNECTED),
        )
        for operation in (
            Operation.SYMBOL_SEARCH,
            Operation.DEFINITION,
            Operation.REFERENCES,
            Operation.DIAGNOSTICS,
        )
    ),
    *(
        OperationCapability(
            operation=operation,
            support=OperationSupport.CONNECTED_ENRICHMENT,
            semantic_modes=(SemanticMode.CONNECTED,),
        )
        for operation in (
            Operation.COMPLETION,
            Operation.HOVER,
            Operation.TYPE_CHECK,
            Operation.PLPGSQL_DIAGNOSTICS,
            Operation.CODE_ACTION,
            Operation.RESTART_PROVIDER,
        )
    ),
    *(
        OperationCapability(
            operation=operation,
            support=OperationSupport.UNAVAILABLE,
            semantic_modes=(),
        )
        for operation in (
            Operation.SIGNATURE_HELP,
            Operation.IMPLEMENTATION,
            Operation.CALL_HIERARCHY,
            Operation.RENAME,
            Operation.FORMAT_DOCUMENT,
            Operation.FORMAT_RANGE,
            Operation.COMMAND_CODE_ACTION,
            Operation.STATEMENT_EXECUTION,
        )
    ),
)

_DERIVED_PREREQUISITES = (
    DerivedPrerequisite(
        derived_table="derived.dependencies",
        prerequisite="semantic.imports",
        applicability=Applicability.NOT_APPLICABLE,
    ),
    DerivedPrerequisite(
        derived_table="derived.dependencies",
        prerequisite="semantic.references",
        applicability=Applicability.APPLICABLE,
    ),
    DerivedPrerequisite(
        derived_table="derived.consumers",
        prerequisite="semantic.imports",
        applicability=Applicability.NOT_APPLICABLE,
    ),
    DerivedPrerequisite(
        derived_table="derived.consumers",
        prerequisite="semantic.references",
        applicability=Applicability.APPLICABLE,
    ),
    DerivedPrerequisite(
        derived_table="derived.consumers",
        prerequisite="semantic.calls",
        applicability=Applicability.APPLICABLE,
    ),
)


class PostgreSqlContract(BaseModel):
    """Executable freeze for PostgreSQL integration decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["soleaux.postgresql/v1"] = POSTGRESQL_CONTRACT_SCHEMA_VERSION
    toolchain: PostgreSqlToolchain = Field(default_factory=PostgreSqlToolchain)
    semantic_modes: tuple[SemanticMode, ...] = (SemanticMode.OFFLINE, SemanticMode.CONNECTED)
    operations: tuple[OperationCapability, ...] = _OPERATION_MATRIX
    coverage_states: tuple[FrameStatus, ...] = tuple(FrameStatus)
    type_name_comparison: Literal["as_written"] = "as_written"
    routine_signature_arguments: Literal["input_only"] = "input_only"
    derived_prerequisites: tuple[DerivedPrerequisite, ...] = _DERIVED_PREREQUISITES
    table_availability_source: Literal["producer_capabilities"] = "producer_capabilities"
    budgets: PostgreSqlBudgets = Field(default_factory=PostgreSqlBudgets)


POSTGRESQL_CONTRACT = PostgreSqlContract()
