"""Fixed versioned relation catalog (`soleaux.tables/v1`).

The catalog is fixed in code. Each descriptor declares schema version,
prerequisites, producer, semantic requirement, cost class, default limits, and
coverage semantics. `include_tables` selects; `exclude_tables` is a hard
prohibition; suggestions are inert and never enable a table or producer.
"""

from __future__ import annotations

import collections.abc
import enum
import types
import typing

import pydantic

TABLES_SCHEMA_VERSION = "soleaux.tables/v1"


class Producer(enum.StrEnum):
    """Owning producer plane for one table."""

    SNAPSHOT = "snapshot"
    CATALOG = "catalog"
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    AUTHORITY = "authority"
    DERIVED = "derived"
    IMPORTED = "imported"


PRODUCER_SUPPORTED_TABLES: collections.abc.Mapping[Producer, frozenset[str]] = (
    types.MappingProxyType(
        {
            Producer.SNAPSHOT: frozenset({"repository.files"}),
            Producer.CATALOG: frozenset(
                {
                    "repository.projects",
                    "repository.dependencies",
                    "repository.scripts",
                    "repository.configurations",
                    "repository.engines",
                    "repository.typescript_routes",
                    "repository.routes",
                    "repository.rules",
                    "repository.symbols",
                    "repository.imports",
                    "repository.diagnostics",
                    "repository.changes",
                    "repository.chunks",
                }
            ),
            Producer.STRUCTURAL: frozenset(
                {
                    "syntax.call_sites",
                    "syntax.declarations",
                    "syntax.exports",
                    "syntax.imports",
                    "syntax.members",
                    "syntax.references",
                    "framework.registrations",
                    "entrypoint_candidates",
                    "quality.standards",
                    "tests",
                }
            ),
            Producer.SEMANTIC: frozenset(
                {
                    "semantic.symbols",
                    "semantic.definitions",
                    "semantic.references",
                    "semantic.implementations",
                    "semantic.imports",
                    "semantic.calls",
                    "quality.diagnostics",
                }
            ),
            Producer.AUTHORITY: frozenset(
                {
                    "authority.entrypoints",
                    "authority.owners",
                    "authority.policies",
                    "authority.bindings",
                    "authority.conflicts",
                }
            ),
            Producer.DERIVED: frozenset(
                {
                    "derived.dependencies",
                    "derived.consumers",
                    "derived.impact",
                    "derived.cycles",
                    "derived.dead_code_candidates",
                }
            ),
            Producer.IMPORTED: frozenset({"coverage"}),
        }
    )
)


class TableEvidenceKind(enum.StrEnum):
    """Evidence class carried by a table's rows."""

    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    METADATA = "metadata"
    DERIVED = "derived"


class SemanticRequirement(enum.StrEnum):
    """Semantic prerequisite for producing the table."""

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class CostClass(enum.StrEnum):
    """Relative production cost."""

    CHEAP = "cheap"
    MODERATE = "moderate"
    EXPENSIVE = "expensive"


class TableDescriptor(pydantic.BaseModel):
    """One fixed catalog entry."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str = pydantic.Field(min_length=1)
    schema_version: typing.Literal["soleaux.tables/v1"] = TABLES_SCHEMA_VERSION
    availability: typing.Literal["available", "unavailable"]
    unavailable_reason: str | None = None
    prerequisites: tuple[str, ...] = ()
    producer: Producer
    evidence_kind: TableEvidenceKind
    semantic_requirement: SemanticRequirement = SemanticRequirement.NONE
    cost_class: CostClass = CostClass.CHEAP
    default_row_limit: int = pydantic.Field(default=200, ge=1)
    coverage_semantics: str = pydantic.Field(min_length=1)
    meaning: str = pydantic.Field(min_length=1)


def _descriptor(
    name: str,
    producer: Producer,
    evidence_kind: TableEvidenceKind,
    meaning: str,
    *,
    prerequisites: tuple[str, ...] = (),
    semantic_requirement: SemanticRequirement = SemanticRequirement.NONE,
    cost_class: CostClass = CostClass.CHEAP,
    default_row_limit: int = 200,
    coverage_semantics: str = "authoritative only under complete coverage",
) -> TableDescriptor:
    available = name in PRODUCER_SUPPORTED_TABLES[producer]
    return TableDescriptor(
        name=name,
        producer=producer,
        evidence_kind=evidence_kind,
        meaning=meaning,
        availability="available" if available else "unavailable",
        unavailable_reason=(
            None
            if available
            else f"{name}: producer {producer.value} does not implement this table"
        ),
        prerequisites=prerequisites,
        semantic_requirement=semantic_requirement,
        cost_class=cost_class,
        default_row_limit=default_row_limit,
        coverage_semantics=coverage_semantics,
    )


TABLE_CATALOG: tuple[TableDescriptor, ...] = (
    _descriptor(
        "repository.files",
        Producer.SNAPSHOT,
        TableEvidenceKind.METADATA,
        "Eligible files and hashes",
    ),
    _descriptor(
        "repository.projects",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Workspace packages and project owners",
    ),
    _descriptor(
        "repository.dependencies",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Declared dependencies with resolved catalog specifiers and usage",
    ),
    _descriptor(
        "repository.scripts",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Package-owned commands, typechecks, and build prerequisites",
    ),
    _descriptor(
        "repository.configurations",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Configuration roots, parser owners, closures, and coverage",
    ),
    _descriptor(
        "repository.engines",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Declared, loaded, binary, API, LSP, and typecheck engine identities",
    ),
    _descriptor(
        "repository.typescript_routes",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Per-project TypeScript configs, roots, libraries, engines, parity, and typechecks",
    ),
    _descriptor(
        "repository.routes",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Framework routes, registrations, methods, runtimes, and source owners",
    ),
    _descriptor(
        "repository.rules",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Canonical structural-policy rules, applicability, and bundle identities",
    ),
    _descriptor(
        "repository.symbols",
        Producer.CATALOG,
        TableEvidenceKind.SEMANTIC,
        "Byte-bound semantic symbol revisions and engine identities",
        semantic_requirement=SemanticRequirement.OPTIONAL,
    ),
    _descriptor(
        "repository.imports",
        Producer.CATALOG,
        TableEvidenceKind.SEMANTIC,
        "Resolved and unresolved source import and dynamic-load edges",
        semantic_requirement=SemanticRequirement.OPTIONAL,
    ),
    _descriptor(
        "repository.diagnostics",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Generation-bound compiler and language-server diagnostics",
        semantic_requirement=SemanticRequirement.OPTIONAL,
    ),
    _descriptor(
        "repository.changes",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Latest bounded generation delta with added, changed, and deleted paths",
    ),
    _descriptor(
        "repository.chunks",
        Producer.CATALOG,
        TableEvidenceKind.METADATA,
        "Deterministic bounded retrieval chunks",
        default_row_limit=100,
    ),
    _descriptor(
        "syntax.declarations",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Declarations and lexical scopes",
    ),
    _descriptor(
        "syntax.members",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Class members and their nearest class owners",
    ),
    _descriptor(
        "syntax.imports",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Import syntax and unresolved module candidates",
        coverage_semantics="candidates; unresolved until semantic resolution",
    ),
    _descriptor(
        "syntax.exports",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Exports and re-exports",
    ),
    _descriptor(
        "syntax.call_sites",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Syntactic calls with callee positions",
        coverage_semantics="candidates; never projected into semantic or derived tables",
    ),
    _descriptor(
        "syntax.references",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Unresolved source reference candidates",
        coverage_semantics=(
            "candidates; unresolved until semantic resolution. Languages without a "
            "separate reference syntax report this as not applicable rather than empty"
        ),
    ),
    _descriptor(
        "framework.registrations",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Routes, handlers, jobs, plugins",
    ),
    _descriptor(
        "entrypoint_candidates",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Executables, handlers, jobs, public roots",
        coverage_semantics="candidates",
    ),
    _descriptor(
        "quality.standards",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Repository policy findings, or explicit unsupported coverage when unavailable",
    ),
    _descriptor(
        "semantic.symbols",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Canonical identity where supported",
        prerequisites=("repository.symbols", "repository.engines"),
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "semantic.definitions",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Definition targets for selected seeds",
        prerequisites=("semantic.symbols",),
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "semantic.references",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Reference locations",
        prerequisites=("semantic.symbols",),
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "semantic.implementations",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Implementation targets",
        prerequisites=("semantic.symbols",),
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "semantic.imports",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Resolved module/import edges",
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "semantic.calls",
        Producer.SEMANTIC,
        TableEvidenceKind.SEMANTIC,
        "Resolved call edges",
        semantic_requirement=SemanticRequirement.REQUIRED,
        cost_class=CostClass.EXPENSIVE,
    ),
    _descriptor(
        "quality.diagnostics",
        Producer.SEMANTIC,
        TableEvidenceKind.METADATA,
        "Diagnostics with provider coverage",
        prerequisites=("repository.diagnostics", "repository.engines"),
        semantic_requirement=SemanticRequirement.OPTIONAL,
        cost_class=CostClass.MODERATE,
    ),
    _descriptor(
        "authority.entrypoints",
        Producer.AUTHORITY,
        TableEvidenceKind.METADATA,
        "Declared application/package/runtime roots",
    ),
    _descriptor(
        "authority.owners",
        Producer.AUTHORITY,
        TableEvidenceKind.METADATA,
        "Typed ownership claims under the four-tier precedence (D030)",
    ),
    _descriptor(
        "authority.policies",
        Producer.AUTHORITY,
        TableEvidenceKind.METADATA,
        "Canonical consumer records with preserved vocabulary and canonicality evidence",
    ),
    _descriptor(
        "authority.bindings",
        Producer.AUTHORITY,
        TableEvidenceKind.METADATA,
        "Consumer-authored field relationships and neutral repository evidence edges",
        prerequisites=(
            "repository.imports",
            "repository.scripts",
            "repository.configurations",
            "syntax.imports",
            "framework.registrations",
            "tests",
        ),
    ),
    _descriptor(
        "authority.conflicts",
        Producer.AUTHORITY,
        TableEvidenceKind.METADATA,
        "One evidence row per competing or redundant ownership or governance claim",
        prerequisites=(
            "repository.imports",
            "repository.scripts",
            "repository.configurations",
            "syntax.imports",
            "framework.registrations",
            "tests",
        ),
        coverage_semantics=(
            "complete coverage may contain a conflicted or redundant decision state"
        ),
    ),
    _descriptor(
        "tests",
        Producer.STRUCTURAL,
        TableEvidenceKind.STRUCTURAL,
        "Declarations found in recognized test files (no coverage or execution claim)",
    ),
    _descriptor(
        "coverage",
        Producer.IMPORTED,
        TableEvidenceKind.METADATA,
        "Coverage inputs from trusted CI artifacts",
    ),
    _descriptor(
        "derived.dependencies",
        Producer.DERIVED,
        TableEvidenceKind.DERIVED,
        "Forward dependency view",
        prerequisites=("semantic.imports",),
    ),
    _descriptor(
        "derived.consumers",
        Producer.DERIVED,
        TableEvidenceKind.DERIVED,
        "Reverse dependency/reference/call view over resolved edges only",
        prerequisites=("semantic.imports", "semantic.calls"),
    ),
    _descriptor(
        "derived.impact",
        Producer.DERIVED,
        TableEvidenceKind.DERIVED,
        "Bounded paths from selected seeds",
        prerequisites=("derived.dependencies",),
        cost_class=CostClass.EXPENSIVE,
    ),
    _descriptor(
        "derived.cycles",
        Producer.DERIVED,
        TableEvidenceKind.DERIVED,
        "Deterministic SCCs over resolved edges",
        prerequisites=("derived.dependencies",),
    ),
    _descriptor(
        "derived.dead_code_candidates",
        Producer.DERIVED,
        TableEvidenceKind.DERIVED,
        "Qualified unreachable candidates; never certain under partial coverage",
        prerequisites=("authority.entrypoints", "derived.consumers"),
        coverage_semantics="qualified candidates; partial coverage forbids certainty",
    ),
)

CATALOG_BY_NAME: dict[str, TableDescriptor] = {
    descriptor.name: descriptor for descriptor in TABLE_CATALOG
}


def _supports_syntax_only_materialization(
    table_name: str,
    *,
    visiting: frozenset[str] = frozenset(),
) -> bool:
    """Return whether the table's full prerequisite closure is syntax-only."""
    if table_name in visiting:
        raise ValueError(f"cyclic table prerequisite: {table_name}")
    descriptor = CATALOG_BY_NAME[table_name]
    if (
        descriptor.availability != "available"
        or descriptor.producer is Producer.SEMANTIC
        or descriptor.semantic_requirement is SemanticRequirement.REQUIRED
    ):
        return False
    next_visiting = visiting | {table_name}
    return all(
        _supports_syntax_only_materialization(
            prerequisite,
            visiting=next_visiting,
        )
        for prerequisite in descriptor.prerequisites
    )


SYNTAX_ONLY_MATERIALIZED_TABLES: tuple[str, ...] = tuple(
    descriptor.name
    for descriptor in TABLE_CATALOG
    if _supports_syntax_only_materialization(descriptor.name)
)


class UnknownTableError(ValueError):
    """Raised when a request names a table outside the fixed catalog."""


def validate_table_selection(
    include_tables: list[str],
    exclude_tables: list[str],
) -> tuple[TableDescriptor, ...]:
    """Resolve the requested tables in catalog order.

    Unknown names raise `UnknownTableError`; excluded names are removed even
    when included; selection never enables producers beyond the requested set.
    """
    unknown = sorted({*include_tables, *exclude_tables} - CATALOG_BY_NAME.keys())
    if unknown:
        msg = f"unknown table(s): {', '.join(unknown)}"
        raise UnknownTableError(msg)
    excluded = frozenset(exclude_tables)
    return tuple(
        descriptor
        for descriptor in TABLE_CATALOG
        if descriptor.name in include_tables and descriptor.name not in excluded
    )
