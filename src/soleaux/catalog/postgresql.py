"""Bounded PostgreSQL source extraction and catalog promotion.

The managed PostgreSQL parser owns syntax. This module consumes its validated
``ParserDocument`` once, emits the closed PostgreSQL fact contracts, and
projects only source-provable declarations into the generic catalog.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import json
import pathlib
import typing

import pydantic

import soleaux.catalog.contracts
import soleaux.catalog.structural
import soleaux.contracts.positions
import soleaux.contracts.repository
import soleaux.contracts.validation
import soleaux.postgresql.analyzer
import soleaux.postgresql.contracts
import soleaux.postgresql.node_runtime

POSTGRESQL_CATALOG_SCHEMA_VERSION = "soleaux.postgresql-catalog/v1"
POSTGRESQL_CATALOG_PRODUCER = "soleaux-postgresql-catalog"
POSTGRESQL_ENGINE_ID = "postgresql:libpg-query"

_OBJECT_ADAPTER = pydantic.TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = pydantic.TypeAdapter(list[object])


class PostgreSqlCatalogContext(pydantic.BaseModel):
    """Snapshot-bound context sent to the structural worker with captured bytes."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.postgresql-catalog/v1"] = (
        POSTGRESQL_CATALOG_SCHEMA_VERSION
    )
    snapshot_id: str = pydantic.Field(min_length=1)
    path: str = pydantic.Field(min_length=1)
    source_digest: str = pydantic.Field(min_length=64, max_length=64)
    source_lane: soleaux.postgresql.contracts.SourceLane

    @pydantic.field_validator("source_digest")
    @classmethod
    def _digest_is_lowercase_sha256(cls, value: str) -> str:
        if not soleaux.contracts.validation.is_lowercase_sha256(value):
            raise ValueError("source digest must be lowercase hexadecimal SHA-256")
        return value


class PostgreSqlCatalogOmission(pydantic.BaseModel):
    """One lifecycle declaration deliberately outside bounded promotion."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    statement_index: int = pydantic.Field(ge=0)
    statement_kind: str = pydantic.Field(min_length=1)
    action: soleaux.postgresql.contracts.DeclarationAction | None = None
    reason: str = pydantic.Field(min_length=1)


class PostgreSqlCatalogExtraction(pydantic.BaseModel):
    """Serializable parser-bound payload returned by the structural worker."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.postgresql-catalog/v1"] = (
        POSTGRESQL_CATALOG_SCHEMA_VERSION
    )
    context: PostgreSqlCatalogContext
    parser_version: str = pydantic.Field(min_length=1)
    postgresql_version: int = pydantic.Field(ge=170000, lt=180000)
    repository_resolved: bool = False
    statements: tuple[soleaux.postgresql.contracts.StatementFact, ...]
    declarations: tuple[soleaux.postgresql.contracts.DeclarationFact, ...]
    references: tuple[soleaux.postgresql.contracts.ReferenceFact, ...] = ()
    calls: tuple[soleaux.postgresql.contracts.CallFact, ...] = ()
    diagnostics: tuple[soleaux.postgresql.contracts.DiagnosticFact, ...] = ()
    omissions: tuple[PostgreSqlCatalogOmission, ...] = ()


def source_lane_for_path(
    path: str,
    *,
    lane_roots: collections.abc.Mapping[
        soleaux.postgresql.contracts.SourceLane, collections.abc.Sequence[str]
    ]
    | None = None,
) -> soleaux.postgresql.contracts.SourceLane:
    """Classify a repository path only from caller-supplied canonical evidence."""
    if lane_roots is None:
        return soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
    path_parts = _relative_path_parts(path)
    matches: list[tuple[int, soleaux.postgresql.contracts.SourceLane]] = []
    for lane, roots in lane_roots.items():
        if lane is soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED:
            continue
        for root in roots:
            root_parts = _relative_path_parts(root)
            if path_parts[: len(root_parts)] == root_parts:
                matches.append((len(root_parts), lane))
    if not matches:
        return soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
    depth = max(item[0] for item in matches)
    lanes = {lane for match_depth, lane in matches if match_depth == depth}
    if len(lanes) != 1:
        raise ValueError("source lane evidence is ambiguous at the most specific root")
    return lanes.pop()


def _relative_path_parts(path: str) -> tuple[str, ...]:
    candidate = pathlib.PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("source lane paths must be normalized repository-relative paths")
    return tuple(part for part in candidate.parts if part != ".")


def extract_postgresql_catalog(
    source: str,
    document: soleaux.postgresql.node_runtime.ParserDocument,
    context: PostgreSqlCatalogContext,
    *,
    root: soleaux.postgresql.analyzer.PostgreSqlAnalyzerNode | None = None,
) -> PostgreSqlCatalogExtraction:
    """Extract normalized PostgreSQL facts from one already-parsed document."""
    content = source.encode("utf-8")
    if soleaux.contracts.repository.content_digest(content) != context.source_digest:
        raise ValueError("PostgreSQL catalog context digest does not match captured source")
    analyzer_root = root or soleaux.postgresql.analyzer.build_postgresql_root(source, document)
    raw_statements = _optional_object_list(document.parse_tree.get("stmts"))
    if raw_statements is None:
        raise ValueError("PostgreSQL parse tree does not contain a statements list")
    statement_nodes = tuple(analyzer_root.children())
    if len(raw_statements) != len(statement_nodes):
        raise ValueError("PostgreSQL statement tree and scanner ranges disagree")

    codec = soleaux.contracts.positions.PositionCodec(content)
    statements: list[soleaux.postgresql.contracts.StatementFact] = []
    declarations: list[soleaux.postgresql.contracts.DeclarationFact] = []
    references: list[soleaux.postgresql.contracts.ReferenceFact] = []
    calls: list[soleaux.postgresql.contracts.CallFact] = []
    diagnostics: list[soleaux.postgresql.contracts.DiagnosticFact] = []
    omissions: list[PostgreSqlCatalogOmission] = []
    parser_generation = f"@libpg-query/parser@{document.parser_version}"

    paired_statements = zip(raw_statements, statement_nodes, strict=True)
    for index, (raw_statement, node) in enumerate(paired_statements):
        statement = _mapping(raw_statement, "raw statement")
        statement_kind, payload = _statement_node(statement.get("stmt"))
        node_range = node.range()
        location = soleaux.postgresql.contracts.SourceLocation(
            kind=soleaux.postgresql.contracts.LocationKind.EXACT_RANGE,
            range=codec.byte_range_to_points(node_range.start.index, node_range.end.index),
        )
        anchor = soleaux.postgresql.contracts.SourceAnchor(
            snapshot_id=context.snapshot_id,
            parser_generation=parser_generation,
            path=context.path,
            statement_index=index,
            source_lane=context.source_lane,
            location=location,
        )
        statements.append(
            soleaux.postgresql.contracts.StatementFact(source=anchor, statement_kind=statement_kind)
        )
        extracted_declarations, reasons = _declarations(statement_kind, payload, anchor)
        declarations.extend(extracted_declarations)
        statement_references, statement_calls = _statement_relations(
            statement_kind,
            payload,
            anchor,
            codec=codec,
            tokens=document.tokens,
        )
        references.extend(statement_references)
        calls.extend(statement_calls)
        if statement_kind == "ERROR":
            message = _string(payload.get("message")) or "PostgreSQL parser rejected statement"
            diagnostics.append(
                soleaux.postgresql.contracts.DiagnosticFact(
                    source=anchor,
                    origin=soleaux.postgresql.contracts.DiagnosticOrigin.PARSER,
                    severity=soleaux.postgresql.contracts.DiagnosticSeverity.ERROR,
                    message=message,
                    code="parse_error",
                )
            )
        omissions.extend(
            PostgreSqlCatalogOmission(
                statement_index=index,
                statement_kind=statement_kind,
                action=_unsupported_action(statement_kind, payload),
                reason=reason,
            )
            for reason in reasons
        )

    embedded_references, embedded_calls = _embedded_relations(
        document.embedded_queries,
        statements,
    )
    references.extend(embedded_references)
    calls.extend(embedded_calls)
    diagnostics.extend(_document_diagnostics(document, statements))
    references, calls = _resolve_within_document(
        tuple(declarations),
        tuple(references),
        tuple(calls),
    )
    return PostgreSqlCatalogExtraction(
        context=context,
        parser_version=document.parser_version,
        postgresql_version=document.postgresql_version,
        statements=tuple(statements),
        declarations=tuple(declarations),
        references=tuple(references),
        calls=tuple(calls),
        diagnostics=tuple(_deduplicate_diagnostics(diagnostics)),
        omissions=tuple(omissions),
    )


def resolve_postgresql_catalog(
    extractions: collections.abc.Sequence[PostgreSqlCatalogExtraction],
) -> tuple[PostgreSqlCatalogExtraction, ...]:
    """Resolve a caller-ordered repository projection without guessing search paths."""
    desired_declarations = tuple(
        declaration
        for extraction in extractions
        if extraction.context.source_lane is soleaux.postgresql.contracts.SourceLane.DESIRED_STATE
        for declaration in extraction.declarations
    )
    desired_identities = _final_identities(desired_declarations)
    migration_state: dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity] = {}
    resolved: list[PostgreSqlCatalogExtraction] = []
    for extraction in extractions:
        lane = extraction.context.source_lane
        if lane is soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY:
            base_identities = tuple(migration_state[key] for key in sorted(migration_state))
        elif lane in {
            soleaux.postgresql.contracts.SourceLane.DESIRED_STATE,
            soleaux.postgresql.contracts.SourceLane.TEST,
            soleaux.postgresql.contracts.SourceLane.GENERATED,
            soleaux.postgresql.contracts.SourceLane.FIXTURE,
        }:
            base_identities = desired_identities
        else:
            base_identities = ()
        references, calls = _resolve_repository_relationships(
            base_identities,
            extraction.declarations,
            extraction.references,
            extraction.calls,
        )
        resolved.append(
            extraction.model_copy(
                update={
                    "repository_resolved": True,
                    "references": references,
                    "calls": calls,
                }
            )
        )
        if lane is soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY:
            _apply_declarations(migration_state, extraction.declarations)
    return tuple(resolved)


def rebind_postgresql_catalog(
    extraction: PostgreSqlCatalogExtraction,
    context: PostgreSqlCatalogContext,
) -> PostgreSqlCatalogExtraction:
    """Bind an unchanged raw extraction to the current repository snapshot."""
    if extraction.repository_resolved:
        raise ValueError("only raw PostgreSQL catalog extractions can be rebound")
    if extraction.context.path != context.path:
        raise ValueError("PostgreSQL catalog path changed without reparsing")
    if extraction.context.source_digest != context.source_digest:
        raise ValueError("PostgreSQL catalog digest changed without reparsing")

    def source(
        anchor: soleaux.postgresql.contracts.SourceAnchor,
    ) -> soleaux.postgresql.contracts.SourceAnchor:
        return anchor.model_copy(
            update={
                "snapshot_id": context.snapshot_id,
                "source_lane": context.source_lane,
            }
        )

    return extraction.model_copy(
        update={
            "context": context,
            "statements": tuple(
                statement.model_copy(update={"source": source(statement.source)})
                for statement in extraction.statements
            ),
            "declarations": tuple(
                declaration.model_copy(update={"source": source(declaration.source)})
                for declaration in extraction.declarations
            ),
            "references": tuple(
                reference.model_copy(update={"source": source(reference.source)})
                for reference in extraction.references
            ),
            "calls": tuple(
                call.model_copy(update={"source": source(call.source)}) for call in extraction.calls
            ),
            "diagnostics": tuple(
                diagnostic.model_copy(update={"source": source(diagnostic.source)})
                for diagnostic in extraction.diagnostics
            ),
        }
    )


def merge_postgresql_catalog(
    facts: soleaux.catalog.contracts.CatalogFacts,
    *,
    workspace_id: str,
    sources: collections.abc.Mapping[str, bytes],
    extractions: collections.abc.Sequence[PostgreSqlCatalogExtraction],
) -> soleaux.catalog.contracts.CatalogFacts:
    """Atomically replace selected paths from one resolved repository projection."""
    selected = tuple(extractions)
    paths = tuple(extraction.context.path for extraction in selected)
    if len(paths) != len(set(paths)):
        raise ValueError("PostgreSQL catalog projection paths must be unique")
    if set(sources) != set(paths):
        raise ValueError("PostgreSQL catalog sources must exactly match projection paths")
    if not selected:
        return facts

    path_set = frozenset(paths)
    affected_project_ids: set[str] = set()
    projected_symbols: list[soleaux.catalog.contracts.SymbolFact] = []
    projected_chunks: list[soleaux.catalog.contracts.ChunkFact] = []
    projected_diagnostics: list[soleaux.catalog.contracts.DiagnosticFact] = []
    projected_engines: dict[str, soleaux.catalog.contracts.EngineFact] = {}
    projected_warnings: list[str] = []
    for extraction in selected:
        context = extraction.context
        source = sources[context.path]
        if soleaux.contracts.repository.content_digest(source) != context.source_digest:
            raise ValueError("PostgreSQL extraction no longer matches captured source")
        project_id = (
            soleaux.catalog.structural.project_id_for_path(facts.projects, context.path)
            or f"{workspace_id}:postgresql:."
        )
        affected_project_ids.add(project_id)
        projected_symbols.extend(
            _symbol_from_declaration(
                declaration,
                workspace_id=workspace_id,
                project_id=project_id,
                parser_version=extraction.parser_version,
                source_digest=context.source_digest,
                semantic=extraction.repository_resolved,
            )
            for declaration in extraction.declarations
        )
        projected_chunks.extend(
            _postgresql_chunks(
                workspace_id=workspace_id,
                source=source.decode("utf-8"),
                extraction=extraction,
            )
        )
        projected_diagnostics.extend(
            _catalog_diagnostics(
                workspace_id=workspace_id,
                project_id=project_id,
                source=source,
                extraction=extraction,
            )
        )
        projected_engines[project_id] = _postgresql_engine(
            workspace_id=workspace_id,
            project_id=project_id,
            extraction=extraction,
        )
        warning_prefix = f"{context.path}: PostgreSQL catalog "
        projected_warnings.extend(
            f"{warning_prefix}omitted {omission.statement_kind} statement "
            f"{omission.statement_index}: {omission.reason}"
            for omission in extraction.omissions
        )

    retained_symbols = tuple(
        symbol
        for symbol in facts.symbols
        if not (symbol.engine_id == POSTGRESQL_ENGINE_ID and symbol.path in path_set)
    )
    enriched_symbols = _enrich_postgresql_symbols(
        (*retained_symbols, *projected_symbols),
        workspace_id=workspace_id,
        sources=sources,
        extractions=selected,
    )
    retained_chunks = tuple(chunk for chunk in facts.chunks if chunk.path not in path_set)
    retained_diagnostics = tuple(
        diagnostic
        for diagnostic in facts.diagnostics
        if not (diagnostic.engine_id == POSTGRESQL_ENGINE_ID and diagnostic.path in path_set)
    )
    retained_engines = tuple(
        item
        for item in facts.engines
        if not (item.engine_id == POSTGRESQL_ENGINE_ID and item.project_id in affected_project_ids)
    )
    warning_prefixes = tuple(f"{path}: PostgreSQL catalog " for path in paths)
    retained_warnings = tuple(
        warning for warning in facts.warnings if not warning.startswith(warning_prefixes)
    )
    return facts.model_copy(
        update={
            "engines": tuple(
                sorted(
                    (*retained_engines, *projected_engines.values()),
                    key=lambda item: (item.project_id, item.engine_id),
                )
            ),
            "symbols": tuple(
                sorted(
                    enriched_symbols,
                    key=lambda item: (item.path, item.byte_start, item.symbol_id),
                )
            ),
            "chunks": tuple(
                sorted(
                    (*retained_chunks, *projected_chunks),
                    key=lambda item: (item.path, item.byte_start, item.chunk_id),
                )
            ),
            "diagnostics": tuple(
                sorted(
                    (*retained_diagnostics, *projected_diagnostics),
                    key=lambda item: (item.path, item.byte_start, item.diagnostic_id),
                )
            ),
            "warnings": tuple(dict.fromkeys((*retained_warnings, *projected_warnings))),
        }
    )


def postgresql_catalog_failure_warning(
    path: str,
    *,
    error_type: str,
    message: str,
) -> str:
    """Return the stable fallback warning used when generic chunks remain."""
    detail = message.strip()[:280] or "no parser detail"
    return f"{path}: PostgreSQL catalog {error_type}; generic chunks retained: {detail}"


def _declarations(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[tuple[soleaux.postgresql.contracts.DeclarationFact, ...], tuple[str, ...]]:
    if statement_kind == "DropStmt":
        return _drop_declarations(payload, source)
    primary, reasons = _primary_declaration(statement_kind, payload, source)
    declarations = [primary] if primary is not None else []
    member_declarations: tuple[soleaux.postgresql.contracts.DeclarationFact, ...] = ()
    member_reasons: tuple[str, ...] = ()
    if statement_kind == "CreateStmt" and primary is not None:
        if isinstance(primary.identity, soleaux.postgresql.contracts.ObjectIdentity):
            member_declarations, member_reasons = _table_member_declarations(
                payload.get("tableElts"),
                primary.identity,
                source,
            )
    elif statement_kind == "CreateForeignTableStmt" and primary is not None:
        base = _wrapped_mapping(payload.get("base"), "CreateStmt")
        if base is not None and isinstance(
            primary.identity, soleaux.postgresql.contracts.ObjectIdentity
        ):
            member_declarations, member_reasons = _table_member_declarations(
                base.get("tableElts"),
                primary.identity,
                source,
            )
    declarations.extend(member_declarations)
    return tuple(declarations), (*reasons, *member_reasons)


def _primary_declaration(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[soleaux.postgresql.contracts.DeclarationFact | None, tuple[str, ...]]:
    action = soleaux.postgresql.contracts.DeclarationAction.CREATE
    identity: soleaux.postgresql.contracts.PostgreSqlIdentity | None = None
    reasons: list[str] = []

    if statement_kind == "CreateSchemaStmt":
        name = _string(payload.get("schemaname"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.SCHEMA, (name,) if name else None
        )
    elif statement_kind == "CreateExtensionStmt":
        name = _string(payload.get("extname"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.EXTENSION, (name,) if name else None
        )
    elif statement_kind == "CreateEnumStmt":
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.ENUM, _name_parts(payload.get("typeName"))
        )
    elif statement_kind == "CreateDomainStmt":
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.DOMAIN, _name_parts(payload.get("domainname"))
        )
    elif statement_kind == "CompositeTypeStmt":
        identity = _relation_identity(
            soleaux.postgresql.contracts.ObjectKind.COMPOSITE_TYPE, payload.get("typevar")
        )
    elif statement_kind == "CreateRangeStmt":
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.RANGE_TYPE,
            _name_parts(payload.get("typeName")),
        )
    elif statement_kind == "CreateSeqStmt":
        identity = _relation_identity(
            soleaux.postgresql.contracts.ObjectKind.SEQUENCE, payload.get("sequence")
        )
    elif statement_kind == "CreateStmt":
        kind = (
            soleaux.postgresql.contracts.ObjectKind.PARTITION
            if payload.get("partbound") is not None
            else soleaux.postgresql.contracts.ObjectKind.TABLE
        )
        identity = _relation_identity(kind, payload.get("relation"))
    elif statement_kind == "CreateForeignTableStmt":
        base = _wrapped_mapping(payload.get("base"), "CreateStmt")
        identity = (
            _relation_identity(
                soleaux.postgresql.contracts.ObjectKind.FOREIGN_TABLE, base.get("relation")
            )
            if base is not None
            else None
        )
    elif statement_kind == "ViewStmt":
        identity = _relation_identity(
            soleaux.postgresql.contracts.ObjectKind.VIEW, payload.get("view")
        )
        action = (
            soleaux.postgresql.contracts.DeclarationAction.CREATE_OR_REPLACE
            if payload.get("replace") is True
            else soleaux.postgresql.contracts.DeclarationAction.CREATE
        )
    elif statement_kind == "CreateTableAsStmt":
        object_type = _enum_name(payload.get("objtype"))
        if object_type in {"OBJECT_MATVIEW", "OBJECT_TABLE"}:
            into = _wrapped_mapping(payload.get("into"), "IntoClause")
            identity = (
                _relation_identity(
                    (
                        soleaux.postgresql.contracts.ObjectKind.MATERIALIZED_VIEW
                        if object_type == "OBJECT_MATVIEW"
                        else soleaux.postgresql.contracts.ObjectKind.TABLE
                    ),
                    into.get("rel"),
                )
                if into is not None
                else None
            )
        else:
            return None, ("CREATE TABLE AS is outside bounded declaration promotion",)
    elif statement_kind == "IndexStmt":
        relation = _relation_parts(payload.get("relation"))
        index_name = _string(payload.get("idxname"))
        if relation is not None and index_name:
            identity = soleaux.postgresql.contracts.ObjectIdentity(
                kind=soleaux.postgresql.contracts.ObjectKind.INDEX,
                schema=".".join(relation[:-1]) or None,
                name=index_name,
            )
    elif statement_kind == "CreateFunctionStmt":
        identity = _function_identity(payload)
        action = (
            soleaux.postgresql.contracts.DeclarationAction.CREATE_OR_REPLACE
            if payload.get("replace") is True
            else soleaux.postgresql.contracts.DeclarationAction.CREATE
        )
        if identity is None:
            return None, ("routine requires a schema-qualified name and complete input signature",)
    elif statement_kind == "DefineStmt" and _enum_name(payload.get("kind")) == "OBJECT_AGGREGATE":
        identity = _aggregate_identity(payload)
        if identity is None:
            return None, (
                "aggregate requires a schema-qualified name and complete input signature",
            )
    elif statement_kind == "CreateRoleStmt":
        name = _string(payload.get("role"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.ROLE, (name,) if name else None
        )
    elif statement_kind == "CreateEventTrigStmt":
        name = _string(payload.get("trigname"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.EVENT_TRIGGER, (name,) if name else None
        )
    elif statement_kind == "CreatePublicationStmt":
        name = _string(payload.get("pubname"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.PUBLICATION, (name,) if name else None
        )
    elif statement_kind == "CreateSubscriptionStmt":
        name = _string(payload.get("subname"))
        identity = _object_identity(
            soleaux.postgresql.contracts.ObjectKind.SUBSCRIPTION, (name,) if name else None
        )
    elif statement_kind == "CreateTrigStmt":
        identity = _scoped_identity(
            soleaux.postgresql.contracts.ObjectKind.TRIGGER,
            payload.get("relation"),
            payload.get("trigname"),
        )
    elif statement_kind == "CreatePolicyStmt":
        identity = _scoped_identity(
            soleaux.postgresql.contracts.ObjectKind.POLICY,
            payload.get("table"),
            payload.get("policy_name"),
        )
    elif statement_kind == "AlterTableStmt":
        return _alter_table_declaration(payload, source)
    elif statement_kind == "RenameStmt":
        return _rename_declaration(payload, source)
    elif statement_kind in {
        "AlterFunctionStmt",
        "AlterOwnerStmt",
        "AlterObjectSchemaStmt",
        "AlterRoleStmt",
    }:
        identity = _altered_identity(statement_kind, payload)
        action = soleaux.postgresql.contracts.DeclarationAction.ALTER
    else:
        reason = _unsupported_reason(statement_kind, payload)
        return None, (reason,) if reason is not None else ()

    if identity is None:
        return None, ("declaration identity is not source-provable", *reasons)
    return soleaux.postgresql.contracts.DeclarationFact(
        source=source, action=action, identity=identity
    ), tuple(reasons)


def _table_member_declarations(
    value: object,
    relation: soleaux.postgresql.contracts.ObjectIdentity,
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[tuple[soleaux.postgresql.contracts.DeclarationFact, ...], tuple[str, ...]]:
    values = _optional_object_list(value)
    if values is None:
        return (), ()
    declarations: list[soleaux.postgresql.contracts.DeclarationFact] = []
    reasons: list[str] = []
    for item in values:
        column = _typed_mapping(item, "ColumnDef")
        if column is not None:
            column_name = _string(column.get("colname"))
            if column_name is not None:
                declarations.append(
                    soleaux.postgresql.contracts.DeclarationFact(
                        source=source,
                        action=soleaux.postgresql.contracts.DeclarationAction.CREATE,
                        identity=soleaux.postgresql.contracts.ColumnIdentity(
                            relation=relation, name=column_name
                        ),
                    )
                )
            constraints = _optional_object_list(column.get("constraints"))
            if constraints is not None:
                for constraint_value in constraints:
                    declaration = _constraint_declaration(
                        constraint_value,
                        relation,
                        source,
                    )
                    if declaration is None:
                        reasons.append("unnamed column constraint has no stable source identity")
                    else:
                        declarations.append(declaration)
            continue
        constraint = _typed_mapping(item, "Constraint")
        if constraint is None:
            continue
        declaration = _constraint_declaration(item, relation, source)
        if declaration is None:
            reasons.append("unnamed table constraint has no stable source identity")
        else:
            declarations.append(declaration)
    return tuple(declarations), tuple(reasons)


def _constraint_declaration(
    value: object,
    relation: soleaux.postgresql.contracts.ObjectIdentity,
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> soleaux.postgresql.contracts.DeclarationFact | None:
    constraint = _typed_mapping(value, "Constraint")
    name = _string(constraint.get("conname")) if constraint is not None else None
    if name is None:
        return None
    return soleaux.postgresql.contracts.DeclarationFact(
        source=source,
        action=soleaux.postgresql.contracts.DeclarationAction.CREATE,
        identity=soleaux.postgresql.contracts.ScopedObjectIdentity(
            kind=soleaux.postgresql.contracts.ObjectKind.CONSTRAINT,
            relation=relation,
            name=name,
        ),
    )


def _scoped_identity(
    kind: typing.Literal[
        soleaux.postgresql.contracts.ObjectKind.CONSTRAINT,
        soleaux.postgresql.contracts.ObjectKind.TRIGGER,
        soleaux.postgresql.contracts.ObjectKind.POLICY,
    ],
    relation_value: object,
    name_value: object,
) -> soleaux.postgresql.contracts.ScopedObjectIdentity | None:
    relation = _relation_identity(soleaux.postgresql.contracts.ObjectKind.TABLE, relation_value)
    name = _string(name_value)
    if relation is None or name is None:
        return None
    return soleaux.postgresql.contracts.ScopedObjectIdentity(
        kind=kind, relation=relation, name=name
    )


def _alter_table_declaration(
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[soleaux.postgresql.contracts.DeclarationFact | None, tuple[str, ...]]:
    relation = _relation_identity(
        _object_kind(_enum_name(payload.get("objtype")))
        or soleaux.postgresql.contracts.ObjectKind.TABLE,
        payload.get("relation"),
    )
    if relation is None:
        return None, ("ALTER relation identity is not source-provable",)
    commands = _optional_object_list(payload.get("cmds")) or []
    for value in commands:
        command = _wrapped_mapping(value, "AlterTableCmd")
        if command is None:
            continue
        subtype = _enum_name(command.get("subtype"))
        if subtype in {
            "AT_AttachPartition",
            "AT_DetachPartition",
            "AT_DetachPartitionFinalize",
        }:
            partition = _wrapped_mapping(command.get("def"), "PartitionCmd")
            identity = (
                _relation_identity(
                    soleaux.postgresql.contracts.ObjectKind.PARTITION, partition.get("name")
                )
                if partition is not None
                else None
            )
            if identity is None:
                return None, ("partition identity is not source-provable",)
            action = (
                soleaux.postgresql.contracts.DeclarationAction.ATTACH
                if subtype == "AT_AttachPartition"
                else soleaux.postgresql.contracts.DeclarationAction.DETACH
            )
            return soleaux.postgresql.contracts.DeclarationFact(
                source=source, action=action, identity=identity
            ), ()
    return (
        soleaux.postgresql.contracts.DeclarationFact(
            source=source,
            action=soleaux.postgresql.contracts.DeclarationAction.ALTER,
            identity=relation,
        ),
        (),
    )


def _rename_declaration(
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[soleaux.postgresql.contracts.DeclarationFact | None, tuple[str, ...]]:
    object_type = _enum_name(payload.get("renameType"))
    new_name = _string(payload.get("newname"))
    if new_name is None:
        return None, ("RENAME target name is not source-provable",)
    identity: soleaux.postgresql.contracts.PostgreSqlIdentity | None
    previous: soleaux.postgresql.contracts.PostgreSqlIdentity | None
    if object_type == "OBJECT_COLUMN":
        relation = _relation_identity(
            soleaux.postgresql.contracts.ObjectKind.TABLE, payload.get("relation")
        )
        old_name = _string(payload.get("subname"))
        if relation is None or old_name is None:
            return None, ("column rename identity is not source-provable",)
        previous = soleaux.postgresql.contracts.ColumnIdentity(relation=relation, name=old_name)
        identity = soleaux.postgresql.contracts.ColumnIdentity(relation=relation, name=new_name)
    elif object_type in {"OBJECT_CONSTRAINT", "OBJECT_TABCONSTRAINT"}:
        identity, previous = _renamed_scoped_identity(
            soleaux.postgresql.contracts.ObjectKind.CONSTRAINT,
            payload,
            new_name,
        )
    elif object_type == "OBJECT_TRIGGER":
        identity, previous = _renamed_scoped_identity(
            soleaux.postgresql.contracts.ObjectKind.TRIGGER,
            payload,
            new_name,
        )
    elif object_type == "OBJECT_POLICY":
        identity, previous = _renamed_scoped_identity(
            soleaux.postgresql.contracts.ObjectKind.POLICY,
            payload,
            new_name,
        )
    elif object_type in {"OBJECT_FUNCTION", "OBJECT_PROCEDURE", "OBJECT_AGGREGATE"}:
        previous = _routine_object_identity(payload.get("object"), object_type)
        if previous is None:
            return None, ("routine rename identity is not source-provable",)
        identity = previous.model_copy(update={"name": new_name})
    else:
        kind = _object_kind(object_type)
        previous = _relation_identity(kind, payload.get("relation")) if kind is not None else None
        if previous is None:
            parts = _name_parts(payload.get("object"))
            previous = _object_identity(kind, parts) if kind is not None else None
        if previous is None:
            return None, ("RENAME identity is not source-provable",)
        identity = previous.model_copy(update={"name": new_name})
    if identity is None or previous is None:
        return None, ("RENAME identity is not source-provable",)
    return (
        soleaux.postgresql.contracts.DeclarationFact(
            source=source,
            action=soleaux.postgresql.contracts.DeclarationAction.RENAME,
            identity=identity,
            previous_identity=previous,
        ),
        (),
    )


def _renamed_scoped_identity(
    kind: typing.Literal[
        soleaux.postgresql.contracts.ObjectKind.CONSTRAINT,
        soleaux.postgresql.contracts.ObjectKind.TRIGGER,
        soleaux.postgresql.contracts.ObjectKind.POLICY,
    ],
    payload: collections.abc.Mapping[str, object],
    new_name: str,
) -> tuple[
    soleaux.postgresql.contracts.ScopedObjectIdentity | None,
    soleaux.postgresql.contracts.ScopedObjectIdentity | None,
]:
    previous = _scoped_identity(
        kind,
        payload.get("relation"),
        payload.get("subname"),
    )
    return (
        previous.model_copy(update={"name": new_name}) if previous is not None else None,
        previous,
    )


def _drop_declarations(
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
) -> tuple[tuple[soleaux.postgresql.contracts.DeclarationFact, ...], tuple[str, ...]]:
    object_type = _enum_name(payload.get("removeType"))
    values = _optional_object_list(payload.get("objects"))
    if values is None:
        return (), ("DROP objects are not source-provable",)
    declarations: list[soleaux.postgresql.contracts.DeclarationFact] = []
    reasons: list[str] = []
    for value in values:
        identity = _dropped_identity(object_type, value)
        if identity is None:
            reasons.append(f"{object_type or 'DROP'} identity is not source-provable")
            continue
        declarations.append(
            soleaux.postgresql.contracts.DeclarationFact(
                source=source,
                action=soleaux.postgresql.contracts.DeclarationAction.DROP,
                identity=identity,
            )
        )
    return tuple(declarations), tuple(reasons)


def _dropped_identity(
    object_type: str, value: object
) -> soleaux.postgresql.contracts.PostgreSqlIdentity | None:
    if object_type in {"OBJECT_FUNCTION", "OBJECT_PROCEDURE", "OBJECT_AGGREGATE"}:
        return _routine_object_identity(value, object_type)
    parts = _list_parts(value)
    if object_type == "OBJECT_POLICY" and parts is not None and len(parts) >= 3:
        relation = _object_identity(soleaux.postgresql.contracts.ObjectKind.TABLE, parts[:-1])
        return (
            soleaux.postgresql.contracts.ScopedObjectIdentity(
                kind=soleaux.postgresql.contracts.ObjectKind.POLICY,
                relation=relation,
                name=parts[-1],
            )
            if relation is not None
            else None
        )
    if object_type == "OBJECT_TRIGGER" and parts is not None and len(parts) >= 3:
        relation = _object_identity(soleaux.postgresql.contracts.ObjectKind.TABLE, parts[:-1])
        return (
            soleaux.postgresql.contracts.ScopedObjectIdentity(
                kind=soleaux.postgresql.contracts.ObjectKind.TRIGGER,
                relation=relation,
                name=parts[-1],
            )
            if relation is not None
            else None
        )
    kind = _object_kind(object_type)
    return _object_identity(kind, parts) if kind is not None else None


def _routine_object_identity(
    value: object, object_type: str
) -> soleaux.postgresql.contracts.RoutineIdentity | None:
    payload = _wrapped_mapping(value, "ObjectWithArgs")
    if payload is None:
        return None
    parts = _name_parts(payload.get("objname"))
    arguments = _optional_object_list(payload.get("objargs"))
    if parts is None or len(parts) < 2 or arguments is None:
        return None
    rendered_arguments: list[str] = []
    for argument in arguments:
        rendered = _type_name(argument)
        if rendered is None:
            return None
        rendered_arguments.append(rendered)
    if object_type == "OBJECT_FUNCTION":
        kind: typing.Literal[
            soleaux.postgresql.contracts.ObjectKind.FUNCTION,
            soleaux.postgresql.contracts.ObjectKind.PROCEDURE,
            soleaux.postgresql.contracts.ObjectKind.AGGREGATE,
        ] = soleaux.postgresql.contracts.ObjectKind.FUNCTION
    elif object_type == "OBJECT_PROCEDURE":
        kind = soleaux.postgresql.contracts.ObjectKind.PROCEDURE
    elif object_type == "OBJECT_AGGREGATE":
        kind = soleaux.postgresql.contracts.ObjectKind.AGGREGATE
    else:
        return None
    return soleaux.postgresql.contracts.RoutineIdentity(
        kind=kind,
        schema=".".join(parts[:-1]),
        name=parts[-1],
        signature=soleaux.postgresql.contracts.RoutineSignature(
            input_argument_types=tuple(rendered_arguments)
        ),
    )


def _altered_identity(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
) -> soleaux.postgresql.contracts.PostgreSqlIdentity | None:
    object_type = _enum_name(payload.get("objtype"))
    if statement_kind == "AlterRoleStmt":
        role = _wrapped_mapping(payload.get("role"), "RoleSpec")
        name = _string(role.get("rolename")) if role is not None else None
        return _object_identity(
            soleaux.postgresql.contracts.ObjectKind.ROLE, (name,) if name else None
        )
    if statement_kind == "AlterFunctionStmt":
        return _routine_object_identity(payload.get("func"), object_type or "OBJECT_FUNCTION")
    kind = _object_kind(object_type)
    parts = _name_parts(payload.get("object"))
    return _object_identity(kind, parts) if kind is not None else None


def _object_kind(value: str) -> soleaux.postgresql.contracts.ObjectKind | None:
    return {
        "OBJECT_SCHEMA": soleaux.postgresql.contracts.ObjectKind.SCHEMA,
        "OBJECT_EXTENSION": soleaux.postgresql.contracts.ObjectKind.EXTENSION,
        "OBJECT_ROLE": soleaux.postgresql.contracts.ObjectKind.ROLE,
        "OBJECT_TYPE": soleaux.postgresql.contracts.ObjectKind.COMPOSITE_TYPE,
        "OBJECT_DOMAIN": soleaux.postgresql.contracts.ObjectKind.DOMAIN,
        "OBJECT_SEQUENCE": soleaux.postgresql.contracts.ObjectKind.SEQUENCE,
        "OBJECT_TABLE": soleaux.postgresql.contracts.ObjectKind.TABLE,
        "OBJECT_TABPARTITION": soleaux.postgresql.contracts.ObjectKind.PARTITION,
        "OBJECT_FOREIGN_TABLE": soleaux.postgresql.contracts.ObjectKind.FOREIGN_TABLE,
        "OBJECT_VIEW": soleaux.postgresql.contracts.ObjectKind.VIEW,
        "OBJECT_MATVIEW": soleaux.postgresql.contracts.ObjectKind.MATERIALIZED_VIEW,
        "OBJECT_INDEX": soleaux.postgresql.contracts.ObjectKind.INDEX,
        "OBJECT_EVENT_TRIGGER": soleaux.postgresql.contracts.ObjectKind.EVENT_TRIGGER,
        "OBJECT_PUBLICATION": soleaux.postgresql.contracts.ObjectKind.PUBLICATION,
        "OBJECT_SUBSCRIPTION": soleaux.postgresql.contracts.ObjectKind.SUBSCRIPTION,
    }.get(value)


def _unsupported_reason(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
) -> str | None:
    action = _unsupported_action(statement_kind, payload)
    if action is not None and action in {
        soleaux.postgresql.contracts.DeclarationAction.ALTER,
        soleaux.postgresql.contracts.DeclarationAction.RENAME,
        soleaux.postgresql.contracts.DeclarationAction.ATTACH,
        soleaux.postgresql.contracts.DeclarationAction.DETACH,
    }:
        return f"{action.value.upper()} declarations are not promoted"
    if statement_kind.startswith("Drop"):
        return "DROP declarations are not promoted"
    if statement_kind.startswith(("Create", "Composite", "Define")):
        return "CREATE declaration kind is outside bounded promotion"
    return None


def _unsupported_action(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
) -> soleaux.postgresql.contracts.DeclarationAction | None:
    if statement_kind.startswith("Drop"):
        return soleaux.postgresql.contracts.DeclarationAction.DROP
    if statement_kind.startswith("Rename"):
        return soleaux.postgresql.contracts.DeclarationAction.RENAME
    if statement_kind == "AlterTableStmt":
        commands = _optional_object_list(payload.get("cmds")) or []
        subtypes = tuple(
            _enum_name(command.get("subtype"))
            for value in commands
            if (command := _wrapped_mapping(value, "AlterTableCmd")) is not None
        )
        if any(subtype == "AT_AttachPartition" for subtype in subtypes):
            return soleaux.postgresql.contracts.DeclarationAction.ATTACH
        if any(
            subtype in {"AT_DetachPartition", "AT_DetachPartitionFinalize"} for subtype in subtypes
        ):
            return soleaux.postgresql.contracts.DeclarationAction.DETACH
    if statement_kind.startswith("Alter"):
        return soleaux.postgresql.contracts.DeclarationAction.ALTER
    if statement_kind.startswith(("Create", "Composite", "Define", "View", "Index")):
        return (
            soleaux.postgresql.contracts.DeclarationAction.CREATE_OR_REPLACE
            if payload.get("replace") is True
            else soleaux.postgresql.contracts.DeclarationAction.CREATE
        )
    return None


def _function_identity(
    payload: collections.abc.Mapping[str, object],
) -> soleaux.postgresql.contracts.RoutineIdentity | None:
    parts = _name_parts(payload.get("funcname"))
    raw_parameters = payload.get("parameters")
    signature = () if raw_parameters is None else _function_parameters(raw_parameters)
    if parts is None or len(parts) < 2 or signature is None:
        return None
    kind = (
        soleaux.postgresql.contracts.ObjectKind.PROCEDURE
        if payload.get("is_procedure") is True
        else soleaux.postgresql.contracts.ObjectKind.FUNCTION
    )
    return soleaux.postgresql.contracts.RoutineIdentity(
        kind=kind,
        schema=".".join(parts[:-1]),
        name=parts[-1],
        signature=soleaux.postgresql.contracts.RoutineSignature(input_argument_types=signature),
    )


def _aggregate_identity(
    payload: collections.abc.Mapping[str, object],
) -> soleaux.postgresql.contracts.RoutineIdentity | None:
    parts = _name_parts(payload.get("defnames"))
    raw_argument_values = _optional_object_list(payload.get("args"))
    if parts is None or len(parts) < 2 or raw_argument_values is None:
        return None
    arguments: list[str] = []
    for raw_argument in raw_argument_values:
        rendered = _type_name(raw_argument)
        if rendered is None:
            return None
        arguments.append(rendered)
    return soleaux.postgresql.contracts.RoutineIdentity(
        kind=soleaux.postgresql.contracts.ObjectKind.AGGREGATE,
        schema=".".join(parts[:-1]),
        name=parts[-1],
        signature=soleaux.postgresql.contracts.RoutineSignature(
            input_argument_types=tuple(arguments)
        ),
    )


def _function_parameters(value: object) -> tuple[str, ...] | None:
    parameters = _optional_object_list(value)
    if parameters is None:
        return None
    arguments: list[str] = []
    for raw_parameter in parameters:
        parameter = _wrapped_mapping(raw_parameter, "FunctionParameter")
        if parameter is None:
            return None
        mode = _enum_name(parameter.get("mode"))
        if mode in {"FUNC_PARAM_OUT", "FUNC_PARAM_TABLE"}:
            continue
        if mode not in {
            "",
            "FUNC_PARAM_DEFAULT",
            "FUNC_PARAM_IN",
            "FUNC_PARAM_INOUT",
            "FUNC_PARAM_VARIADIC",
        }:
            return None
        rendered = _type_name(parameter.get("argType"))
        if rendered is None:
            return None
        arguments.append(rendered)
    return tuple(arguments)


def _type_name(value: object) -> str | None:
    payload = _wrapped_mapping(value, "TypeName")
    if payload is None:
        return None
    if payload.get("setof") is True or payload.get("pct_type") is True:
        return None
    for field_name in ("typmods", "arrayBounds"):
        field = payload.get(field_name)
        if isinstance(field, list) and field:
            return None
    parts = _name_parts(payload.get("names"))
    return ".".join(parts) if parts else None


def _relation_identity(
    kind: soleaux.postgresql.contracts.ObjectKind, value: object
) -> soleaux.postgresql.contracts.ObjectIdentity | None:
    return _object_identity(kind, _relation_parts(value))


def _relation_parts(value: object) -> tuple[str, ...] | None:
    relation = _wrapped_mapping(value, "RangeVar")
    if relation is None:
        return None
    name = _string(relation.get("relname"))
    if not name:
        return None
    schema = _string(relation.get("schemaname"))
    return (schema, name) if schema else (name,)


def _object_identity(
    kind: soleaux.postgresql.contracts.ObjectKind,
    parts: tuple[str, ...] | None,
) -> soleaux.postgresql.contracts.ObjectIdentity | None:
    if not parts:
        return None
    return soleaux.postgresql.contracts.ObjectIdentity(
        kind=kind,
        schema=".".join(parts[:-1]) or None,
        name=parts[-1],
    )


def _name_parts(value: object) -> tuple[str, ...] | None:
    values = _optional_object_list(value)
    if not values:
        return None
    parts: list[str] = []
    for raw_part in values:
        if isinstance(raw_part, str):
            part = raw_part
        else:
            payload = _wrapped_mapping(raw_part, "String")
            part = _string(payload.get("sval")) if payload is not None else None
        if not part:
            return None
        parts.append(part)
    return tuple(parts)


def _list_parts(value: object) -> tuple[str, ...] | None:
    payload = _wrapped_mapping(value, "List")
    return _name_parts(payload.get("items")) if payload is not None else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _enum_name(value: object) -> str:
    return value if isinstance(value, str) else ""


def _wrapped_mapping(value: object, wrapper: str) -> dict[str, object] | None:
    mapping = _optional_mapping(value)
    if mapping is None:
        return None
    wrapped = mapping.get(wrapper)
    if wrapped is None:
        return mapping
    return _optional_mapping(wrapped)


def _typed_mapping(value: object, wrapper: str) -> dict[str, object] | None:
    mapping = _optional_mapping(value)
    if mapping is None or wrapper not in mapping:
        return None
    return _optional_mapping(mapping[wrapper])


def _statement_node(value: object) -> tuple[str, dict[str, object]]:
    mapping = _mapping(value, "statement node")
    if len(mapping) != 1:
        raise ValueError("PostgreSQL statement node must have one typed wrapper")
    kind, raw_payload = next(iter(mapping.items()))
    if not kind:
        raise ValueError("PostgreSQL statement node has an invalid typed wrapper")
    return kind, _mapping(raw_payload, kind)


def _mapping(value: object, label: str) -> dict[str, object]:
    mapping = _optional_mapping(value)
    if mapping is None:
        raise ValueError(f"{label} must be an object")
    return mapping


def _optional_mapping(value: object) -> dict[str, object] | None:
    try:
        return _OBJECT_ADAPTER.validate_python(value, strict=True)
    except pydantic.ValidationError:
        return None


def _optional_object_list(value: object) -> list[object] | None:
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except pydantic.ValidationError:
        return None


@dataclasses.dataclass(frozen=True, slots=True)
class _ParsedNode:
    kind: str
    payload: collections.abc.Mapping[str, object]
    path: tuple[str, ...]
    ancestors: tuple[str, ...]


def _statement_relations(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
    source: soleaux.postgresql.contracts.SourceAnchor,
    *,
    codec: soleaux.contracts.positions.PositionCodec | None,
    tokens: collections.abc.Sequence[soleaux.postgresql.node_runtime.ScanToken],
) -> tuple[
    tuple[soleaux.postgresql.contracts.ReferenceFact, ...],
    tuple[soleaux.postgresql.contracts.CallFact, ...],
]:
    references: list[soleaux.postgresql.contracts.ReferenceFact] = []
    calls: list[soleaux.postgresql.contracts.CallFact] = []
    for node in _walk_nodes(payload, ancestors=(statement_kind,)):
        anchor = _node_anchor(source, node.payload, codec=codec, tokens=tokens)
        if node.kind == "RangeVar":
            if _is_declaration_target(statement_kind, node.path):
                continue
            parts = _relation_parts(node.payload)
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.RELATION,
                        name_parts=parts,
                    )
                )
        elif node.kind == "ColumnRef":
            parts = _name_parts(node.payload.get("fields"))
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.COLUMN,
                        name_parts=parts,
                    )
                )
        elif node.kind == "FuncCall":
            parts = _name_parts(node.payload.get("funcname"))
            arguments = _optional_object_list(node.payload.get("args")) or []
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.ROUTINE,
                        name_parts=parts,
                    )
                )
                calls.append(
                    soleaux.postgresql.contracts.CallFact(
                        source=anchor,
                        call_kind=(
                            soleaux.postgresql.contracts.CallKind.PROCEDURE
                            if "CallStmt" in node.ancestors
                            else soleaux.postgresql.contracts.CallKind.FUNCTION
                        ),
                        callee_parts=parts,
                        argument_count=len(arguments),
                    )
                )
        elif node.kind == "TypeName":
            if _is_declared_type_name(statement_kind, node.path):
                continue
            parts = _name_parts(node.payload.get("names"))
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.TYPE,
                        name_parts=parts,
                    )
                )
        elif node.kind == "TypeCast":
            type_name = _wrapped_mapping(node.payload.get("typeName"), "TypeName")
            parts = _name_parts(type_name.get("names")) if type_name is not None else None
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.CAST,
                        name_parts=parts,
                        resolution=soleaux.postgresql.contracts.TargetResolution(
                            state=soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE
                        ),
                    )
                )
        elif node.kind == "CollateClause":
            parts = _name_parts(node.payload.get("collname"))
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.COLLATION,
                        name_parts=parts,
                    )
                )
        elif node.kind == "A_Expr":
            parts = _name_parts(node.payload.get("name"))
            if parts is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.OPERATOR,
                        name_parts=parts,
                        resolution=soleaux.postgresql.contracts.TargetResolution(
                            state=soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE
                        ),
                    )
                )
        elif node.kind == "RoleSpec":
            role_name = _string(node.payload.get("rolename"))
            if role_name is not None:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.ROLE,
                        name_parts=(role_name,),
                    )
                )
        elif node.kind == "DefElem" and node.payload.get("defname") == "owned_by":
            parts = _list_parts(node.payload.get("arg"))
            if parts is not None and len(parts) >= 2:
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.RELATION,
                        name_parts=parts[:-1],
                    )
                )
                references.append(
                    soleaux.postgresql.contracts.ReferenceFact(
                        source=anchor,
                        reference_kind=soleaux.postgresql.contracts.ReferenceKind.COLUMN,
                        name_parts=parts,
                    )
                )
    trigger_parts = _trigger_function_parts(statement_kind, payload)
    if trigger_parts is not None:
        references.append(
            soleaux.postgresql.contracts.ReferenceFact(
                source=source,
                reference_kind=soleaux.postgresql.contracts.ReferenceKind.ROUTINE,
                name_parts=trigger_parts,
            )
        )
        calls.append(
            soleaux.postgresql.contracts.CallFact(
                source=source,
                call_kind=soleaux.postgresql.contracts.CallKind.TRIGGER,
                callee_parts=trigger_parts,
                argument_count=0,
            )
        )
    return (
        tuple(_deduplicate_references(references)),
        tuple(_deduplicate_calls(calls)),
    )


def _walk_nodes(
    value: object,
    *,
    field_name: str = "",
    path: tuple[str, ...] = (),
    ancestors: tuple[str, ...] = (),
) -> collections.abc.Iterable[_ParsedNode]:
    values = _optional_object_list(value)
    if values is not None:
        for item in values:
            yield from _walk_nodes(
                item,
                field_name=field_name,
                path=path,
                ancestors=ancestors,
            )
        return
    mapping = _optional_mapping(value)
    if mapping is None:
        return
    kind: str | None = None
    payload: collections.abc.Mapping[str, object] = mapping
    if len(mapping) == 1:
        wrapper, wrapped = next(iter(mapping.items()))
        wrapped_mapping = _optional_mapping(wrapped)
        if wrapper[:1].isupper() and wrapped_mapping is not None:
            kind = wrapper
            payload = wrapped_mapping
    if kind is None:
        kind = _inferred_node_kind(field_name, mapping)
    next_ancestors = ancestors
    if kind is not None:
        yield _ParsedNode(
            kind=kind,
            payload=payload,
            path=path,
            ancestors=ancestors,
        )
        next_ancestors = (*ancestors, kind)
    for name, child in payload.items():
        if name in {"location", "stmt_location", "stmt_len"}:
            continue
        yield from _walk_nodes(
            child,
            field_name=name,
            path=(*path, name),
            ancestors=next_ancestors,
        )


def _inferred_node_kind(
    field_name: str,
    payload: collections.abc.Mapping[str, object],
) -> str | None:
    if isinstance(payload.get("relname"), str):
        return "RangeVar"
    if field_name in {"typeName", "argType", "returnType"} and isinstance(
        payload.get("names"), list
    ):
        return "TypeName"
    if field_name == "funccall" and isinstance(payload.get("funcname"), list):
        return "FuncCall"
    if isinstance(payload.get("roletype"), str):
        return "RoleSpec"
    return None


def _node_anchor(
    source: soleaux.postgresql.contracts.SourceAnchor,
    payload: collections.abc.Mapping[str, object],
    *,
    codec: soleaux.contracts.positions.PositionCodec | None,
    tokens: collections.abc.Sequence[soleaux.postgresql.node_runtime.ScanToken],
) -> soleaux.postgresql.contracts.SourceAnchor:
    location = payload.get("location")
    if codec is None or not isinstance(location, int) or isinstance(location, bool) or location < 0:
        return source
    token = next(
        (
            candidate
            for candidate in tokens
            if candidate.start <= location < candidate.end
            or candidate.start == location == candidate.end
        ),
        None,
    )
    location_payload = (
        soleaux.postgresql.contracts.SourceLocation(
            kind=soleaux.postgresql.contracts.LocationKind.EXACT_RANGE,
            range=codec.byte_range_to_points(token.start, token.end),
        )
        if token is not None
        else soleaux.postgresql.contracts.SourceLocation(
            kind=soleaux.postgresql.contracts.LocationKind.START_ONLY,
            point=codec.byte_to_point(location),
        )
    )
    return source.model_copy(update={"location": location_payload})


def _is_declaration_target(
    statement_kind: str,
    path: tuple[str, ...],
) -> bool:
    if statement_kind in {"CreateStmt", "CreateForeignTableStmt"}:
        return path[-1:] == ("relation",)
    if statement_kind == "CreateSeqStmt":
        return path[-1:] == ("sequence",)
    if statement_kind == "ViewStmt":
        return path[-1:] == ("view",)
    if statement_kind == "CreateTableAsStmt":
        return path[-2:] == ("into", "rel")
    return False


def _is_declared_type_name(
    statement_kind: str,
    path: tuple[str, ...],
) -> bool:
    return statement_kind in {"CreateEnumStmt", "CreateRangeStmt"} and path[-1:] == ("typeName",)


def _trigger_function_parts(
    statement_kind: str,
    payload: collections.abc.Mapping[str, object],
) -> tuple[str, ...] | None:
    if statement_kind in {"CreateTrigStmt", "CreateEventTrigStmt"}:
        return _name_parts(payload.get("funcname"))
    return None


def _embedded_relations(
    queries: collections.abc.Sequence[soleaux.postgresql.node_runtime.EmbeddedQuery],
    statements: collections.abc.Sequence[soleaux.postgresql.contracts.StatementFact],
) -> tuple[
    tuple[soleaux.postgresql.contracts.ReferenceFact, ...],
    tuple[soleaux.postgresql.contracts.CallFact, ...],
]:
    references: list[soleaux.postgresql.contracts.ReferenceFact] = []
    calls: list[soleaux.postgresql.contracts.CallFact] = []
    for query in queries:
        source = _line_source_anchor(query.line, statements)
        if source is None:
            continue
        if query.dynamic:
            references.append(
                soleaux.postgresql.contracts.ReferenceFact(
                    source=source,
                    reference_kind=soleaux.postgresql.contracts.ReferenceKind.DYNAMIC_SQL,
                    name_parts=("dynamic_sql",),
                    resolution=soleaux.postgresql.contracts.TargetResolution(
                        state=soleaux.postgresql.contracts.ResolutionState.PARTIAL
                    ),
                )
            )
            continue
        if query.parse_tree is None:
            references.append(
                soleaux.postgresql.contracts.ReferenceFact(
                    source=source,
                    reference_kind=soleaux.postgresql.contracts.ReferenceKind.DYNAMIC_SQL,
                    name_parts=("unparsed_plpgsql_expression",),
                    resolution=soleaux.postgresql.contracts.TargetResolution(
                        state=soleaux.postgresql.contracts.ResolutionState.PARTIAL
                    ),
                )
            )
            continue
        raw_statements = _optional_object_list(query.parse_tree.get("stmts"))
        if raw_statements is None:
            continue
        for raw_statement in raw_statements:
            statement = _mapping(raw_statement, "embedded statement")
            statement_kind, payload = _statement_node(statement.get("stmt"))
            embedded_references, embedded_calls = _statement_relations(
                statement_kind,
                payload,
                source,
                codec=None,
                tokens=(),
            )
            references.extend(embedded_references)
            calls.extend(embedded_calls)
    return (
        tuple(_deduplicate_references(references)),
        tuple(_deduplicate_calls(calls)),
    )


def _line_source_anchor(
    line: int,
    statements: collections.abc.Sequence[soleaux.postgresql.contracts.StatementFact],
) -> soleaux.postgresql.contracts.SourceAnchor | None:
    if not statements:
        return None
    source = next(
        (
            statement.source
            for statement in statements
            if statement.source.location.range is not None
            and statement.source.location.range.start.line
            <= line
            <= statement.source.location.range.end.line
        ),
        statements[0].source,
    )
    return source.model_copy(
        update={
            "location": soleaux.postgresql.contracts.SourceLocation(
                kind=soleaux.postgresql.contracts.LocationKind.LINE_ONLY,
                line=line,
            )
        }
    )


def _document_diagnostics(
    document: soleaux.postgresql.node_runtime.ParserDocument,
    statements: collections.abc.Sequence[soleaux.postgresql.contracts.StatementFact],
) -> tuple[soleaux.postgresql.contracts.DiagnosticFact, ...]:
    diagnostics: list[soleaux.postgresql.contracts.DiagnosticFact] = []
    for issue in document.issues:
        source = next(
            (
                statement.source
                for statement in statements
                if statement.source.location.range is not None
                and statement.source.location.range.start.byte <= issue.byte_start
                and issue.byte_end <= statement.source.location.range.end.byte
            ),
            statements[0].source if statements else None,
        )
        if source is not None:
            diagnostics.append(
                soleaux.postgresql.contracts.DiagnosticFact(
                    source=source,
                    origin=soleaux.postgresql.contracts.DiagnosticOrigin.PARSER,
                    severity=soleaux.postgresql.contracts.DiagnosticSeverity.ERROR,
                    message=issue.message,
                    code="parse_error",
                )
            )
    if document.plpgsql_error is not None and statements:
        diagnostics.append(
            soleaux.postgresql.contracts.DiagnosticFact(
                source=statements[0].source,
                origin=soleaux.postgresql.contracts.DiagnosticOrigin.PARSER,
                severity=soleaux.postgresql.contracts.DiagnosticSeverity.WARNING,
                message=f"PL/pgSQL analysis unavailable: {document.plpgsql_error[:240]}",
                code="plpgsql_parse_error",
            )
        )
    return tuple(diagnostics)


def _resolve_within_document(
    declarations: tuple[soleaux.postgresql.contracts.DeclarationFact, ...],
    references: tuple[soleaux.postgresql.contracts.ReferenceFact, ...],
    calls: tuple[soleaux.postgresql.contracts.CallFact, ...],
) -> tuple[
    list[soleaux.postgresql.contracts.ReferenceFact], list[soleaux.postgresql.contracts.CallFact]
]:
    lane = (
        declarations[0].source.source_lane
        if declarations
        else (
            references[0].source.source_lane
            if references
            else calls[0].source.source_lane
            if calls
            else soleaux.postgresql.contracts.SourceLane.DESIRED_STATE
        )
    )
    identities = _final_identities(declarations)
    resolved_references = [
        reference.model_copy(
            update={
                "resolution": _resolve_reference(
                    reference,
                    (
                        _identities_before(
                            declarations,
                            reference.source.statement_index,
                        )
                        if lane is soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY
                        or lane is soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
                        else identities
                    ),
                ),
            }
        )
        if reference.resolution.state is soleaux.postgresql.contracts.ResolutionState.CANDIDATE
        else reference
        for reference in references
    ]
    resolved_calls = [
        call.model_copy(
            update={
                "resolution": _resolve_call(
                    call,
                    (
                        _identities_before(
                            declarations,
                            call.source.statement_index,
                        )
                        if lane is soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY
                        or lane is soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
                        else identities
                    ),
                ),
            }
        )
        if call.resolution.state is soleaux.postgresql.contracts.ResolutionState.CANDIDATE
        else call
        for call in calls
    ]
    return resolved_references, resolved_calls


class _RepositoryIdentityLookup:
    """Incremental exact-match index for one repository-resolution sweep."""

    def __init__(
        self, identities: collections.abc.Sequence[soleaux.postgresql.contracts.PostgreSqlIdentity]
    ) -> None:
        self._identities: dict[
            tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity
        ] = {}
        self._matches: dict[
            tuple[str, ...],
            dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity],
        ] = {}
        for identity in identities:
            self._set(identity)

    def _set(self, identity: soleaux.postgresql.contracts.PostgreSqlIdentity) -> None:
        identity_key = _identity_key(identity)
        if identity_key in self._identities:
            self._discard(identity_key)
        self._identities[identity_key] = identity
        for match_key in _identity_match_keys(identity):
            self._matches.setdefault(match_key, {})[identity_key] = identity

    def _discard(self, identity_key: tuple[str, ...]) -> None:
        identity = self._identities.pop(identity_key, None)
        if identity is None:
            return
        for match_key in _identity_match_keys(identity):
            matches = self._matches[match_key]
            matches.pop(identity_key, None)
            if not matches:
                self._matches.pop(match_key)

    def apply(self, declaration: soleaux.postgresql.contracts.DeclarationFact) -> None:
        previous = declaration.previous_identity
        if previous is not None:
            self._discard(_identity_key(previous))
        identity_key = _identity_key(declaration.identity)
        if declaration.action is soleaux.postgresql.contracts.DeclarationAction.DROP:
            self._discard(identity_key)
        else:
            self._set(declaration.identity)

    def resolve_reference(
        self, reference: soleaux.postgresql.contracts.ReferenceFact
    ) -> soleaux.postgresql.contracts.TargetResolution:
        if reference.reference_kind in {
            soleaux.postgresql.contracts.ReferenceKind.OPERATOR,
            soleaux.postgresql.contracts.ReferenceKind.CAST,
        }:
            return soleaux.postgresql.contracts.TargetResolution(
                state=soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE
            )
        if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.DYNAMIC_SQL:
            return soleaux.postgresql.contracts.TargetResolution(
                state=soleaux.postgresql.contracts.ResolutionState.PARTIAL
            )
        match_key = _reference_match_key(reference)
        candidates = () if match_key is None else tuple(self._matches.get(match_key, {}).values())
        return _target_resolution(candidates)

    def resolve_call(
        self, call: soleaux.postgresql.contracts.CallFact
    ) -> soleaux.postgresql.contracts.TargetResolution:
        match_key = _call_match_key(call)
        return _target_resolution(tuple(self._matches.get(match_key, {}).values()))


def _qualified_match_parts(parts: collections.abc.Sequence[str]) -> tuple[str, str]:
    if len(parts) == 1:
        return "", parts[0]
    return ".".join(parts[:-1]), parts[-1]


def _identity_match_keys(
    identity: soleaux.postgresql.contracts.PostgreSqlIdentity,
) -> tuple[tuple[str, ...], ...]:
    if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity):
        schema = identity.schema_name
        return (
            (
                "reference",
                soleaux.postgresql.contracts.ReferenceKind.ROUTINE.value,
                schema,
                identity.name,
            ),
            (
                "call",
                schema,
                identity.name,
                str(len(identity.signature.input_argument_types)),
            ),
        )
    if isinstance(identity, soleaux.postgresql.contracts.ColumnIdentity):
        return (
            (
                "reference",
                soleaux.postgresql.contracts.ReferenceKind.COLUMN.value,
                identity.relation.schema_name or "",
                identity.relation.name,
                identity.name,
            ),
        )
    if isinstance(identity, soleaux.postgresql.contracts.ScopedObjectIdentity):
        reference_kind = {
            soleaux.postgresql.contracts.ObjectKind.CONSTRAINT: (
                soleaux.postgresql.contracts.ReferenceKind.CONSTRAINT
            ),
            soleaux.postgresql.contracts.ObjectKind.TRIGGER: (
                soleaux.postgresql.contracts.ReferenceKind.TRIGGER
            ),
            soleaux.postgresql.contracts.ObjectKind.POLICY: (
                soleaux.postgresql.contracts.ReferenceKind.POLICY
            ),
        }[identity.kind]
        return (
            (
                "reference",
                reference_kind.value,
                identity.relation.schema_name or "",
                identity.name,
            ),
        )
    if identity.kind in {
        soleaux.postgresql.contracts.ObjectKind.SEQUENCE,
        soleaux.postgresql.contracts.ObjectKind.TABLE,
        soleaux.postgresql.contracts.ObjectKind.PARTITION,
        soleaux.postgresql.contracts.ObjectKind.FOREIGN_TABLE,
        soleaux.postgresql.contracts.ObjectKind.VIEW,
        soleaux.postgresql.contracts.ObjectKind.MATERIALIZED_VIEW,
    }:
        reference_kind = soleaux.postgresql.contracts.ReferenceKind.RELATION
    elif identity.kind in {
        soleaux.postgresql.contracts.ObjectKind.ENUM,
        soleaux.postgresql.contracts.ObjectKind.DOMAIN,
        soleaux.postgresql.contracts.ObjectKind.COMPOSITE_TYPE,
        soleaux.postgresql.contracts.ObjectKind.RANGE_TYPE,
    }:
        reference_kind = soleaux.postgresql.contracts.ReferenceKind.TYPE
    elif identity.kind is soleaux.postgresql.contracts.ObjectKind.ROLE:
        reference_kind = soleaux.postgresql.contracts.ReferenceKind.ROLE
    elif identity.kind is soleaux.postgresql.contracts.ObjectKind.EXTENSION:
        reference_kind = soleaux.postgresql.contracts.ReferenceKind.EXTENSION
    else:
        return ()
    return (
        (
            "reference",
            reference_kind.value,
            identity.schema_name or "",
            identity.name,
        ),
    )


def _reference_match_key(
    reference: soleaux.postgresql.contracts.ReferenceFact,
) -> tuple[str, ...] | None:
    parts = reference.name_parts
    if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.COLUMN:
        if len(parts) == 3:
            return ("reference", reference.reference_kind.value, *parts)
        if len(parts) == 2:
            return ("reference", reference.reference_kind.value, "", *parts)
        return None
    schema, name = _qualified_match_parts(parts)
    return ("reference", reference.reference_kind.value, schema, name)


def _call_match_key(call: soleaux.postgresql.contracts.CallFact) -> tuple[str, ...]:
    schema, name = _qualified_match_parts(call.callee_parts)
    return ("call", schema, name, str(call.argument_count))


def _resolve_repository_reference(
    reference: soleaux.postgresql.contracts.ReferenceFact,
    identities: _RepositoryIdentityLookup,
) -> soleaux.postgresql.contracts.ReferenceFact:
    if reference.resolution.state in {
        soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE,
        soleaux.postgresql.contracts.ResolutionState.PARTIAL,
    }:
        return reference
    return reference.model_copy(update={"resolution": identities.resolve_reference(reference)})


def _resolve_repository_call(
    call: soleaux.postgresql.contracts.CallFact,
    identities: _RepositoryIdentityLookup,
) -> soleaux.postgresql.contracts.CallFact:
    if call.resolution.state in {
        soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE,
        soleaux.postgresql.contracts.ResolutionState.PARTIAL,
    }:
        return call
    return call.model_copy(update={"resolution": identities.resolve_call(call)})


def _resolve_repository_relationships(
    base_identities: collections.abc.Sequence[soleaux.postgresql.contracts.PostgreSqlIdentity],
    declarations: collections.abc.Sequence[soleaux.postgresql.contracts.DeclarationFact],
    references: collections.abc.Sequence[soleaux.postgresql.contracts.ReferenceFact],
    calls: collections.abc.Sequence[soleaux.postgresql.contracts.CallFact],
) -> tuple[
    tuple[soleaux.postgresql.contracts.ReferenceFact, ...],
    tuple[soleaux.postgresql.contracts.CallFact, ...],
]:
    reference_indexes: dict[int, list[int]] = {}
    for index, reference in enumerate(references):
        if reference.resolution.state not in {
            soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE,
            soleaux.postgresql.contracts.ResolutionState.PARTIAL,
        }:
            reference_indexes.setdefault(reference.source.statement_index, []).append(index)
    call_indexes: dict[int, list[int]] = {}
    for index, call in enumerate(calls):
        if call.resolution.state not in {
            soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE,
            soleaux.postgresql.contracts.ResolutionState.PARTIAL,
        }:
            call_indexes.setdefault(call.source.statement_index, []).append(index)
    event_indexes = sorted({*reference_indexes, *call_indexes})
    if not event_indexes:
        return tuple(references), tuple(calls)

    identities = _RepositoryIdentityLookup(base_identities)
    ordered_declarations = tuple(sorted(declarations, key=lambda item: item.source.statement_index))
    resolved_references = list(references)
    resolved_calls = list(calls)
    declaration_index = 0
    for statement_index in event_indexes:
        while (
            declaration_index < len(ordered_declarations)
            and ordered_declarations[declaration_index].source.statement_index < statement_index
        ):
            identities.apply(ordered_declarations[declaration_index])
            declaration_index += 1
        for reference_index in reference_indexes.get(statement_index, ()):
            resolved_references[reference_index] = _resolve_repository_reference(
                resolved_references[reference_index],
                identities,
            )
        for call_index in call_indexes.get(statement_index, ()):
            resolved_calls[call_index] = _resolve_repository_call(
                resolved_calls[call_index],
                identities,
            )
    return tuple(resolved_references), tuple(resolved_calls)


def _identities_before(
    declarations: collections.abc.Sequence[soleaux.postgresql.contracts.DeclarationFact],
    statement_index: int,
) -> tuple[soleaux.postgresql.contracts.PostgreSqlIdentity, ...]:
    state: dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity] = {}
    _apply_declarations(
        state,
        (
            declaration
            for declaration in declarations
            if declaration.source.statement_index < statement_index
        ),
    )
    return tuple(state[key] for key in sorted(state))


def _apply_declarations(
    state: dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity],
    declarations: collections.abc.Iterable[soleaux.postgresql.contracts.DeclarationFact],
) -> None:
    for declaration in sorted(
        declarations,
        key=lambda item: item.source.statement_index,
    ):
        _apply_declaration(state, declaration)


def _apply_declaration(
    state: dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity],
    declaration: soleaux.postgresql.contracts.DeclarationFact,
) -> None:
    previous = declaration.previous_identity
    if previous is not None:
        state.pop(_identity_key(previous), None)
    key = _identity_key(declaration.identity)
    if declaration.action is soleaux.postgresql.contracts.DeclarationAction.DROP:
        state.pop(key, None)
    else:
        state[key] = declaration.identity


def _final_identities(
    declarations: collections.abc.Sequence[soleaux.postgresql.contracts.DeclarationFact],
) -> tuple[soleaux.postgresql.contracts.PostgreSqlIdentity, ...]:
    current: dict[tuple[str, ...], soleaux.postgresql.contracts.PostgreSqlIdentity] = {}
    _apply_declarations(current, declarations)
    return tuple(current[key] for key in sorted(current))


def _resolve_reference(
    reference: soleaux.postgresql.contracts.ReferenceFact,
    identities: collections.abc.Sequence[soleaux.postgresql.contracts.PostgreSqlIdentity],
) -> soleaux.postgresql.contracts.TargetResolution:
    if reference.reference_kind in {
        soleaux.postgresql.contracts.ReferenceKind.OPERATOR,
        soleaux.postgresql.contracts.ReferenceKind.CAST,
    }:
        return soleaux.postgresql.contracts.TargetResolution(
            state=soleaux.postgresql.contracts.ResolutionState.UNAVAILABLE
        )
    if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.DYNAMIC_SQL:
        return soleaux.postgresql.contracts.TargetResolution(
            state=soleaux.postgresql.contracts.ResolutionState.PARTIAL
        )
    candidates = tuple(
        identity for identity in identities if _reference_matches(reference, identity)
    )
    return _target_resolution(candidates)


def _reference_matches(
    reference: soleaux.postgresql.contracts.ReferenceFact,
    identity: soleaux.postgresql.contracts.PostgreSqlIdentity,
) -> bool:
    parts = reference.name_parts
    if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.ROUTINE:
        return isinstance(
            identity, soleaux.postgresql.contracts.RoutineIdentity
        ) and _qualified_name_matches(
            parts,
            identity.schema_name,
            identity.name,
        )
    if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.COLUMN:
        if not isinstance(identity, soleaux.postgresql.contracts.ColumnIdentity):
            return False
        relation = identity.relation
        if len(parts) == 3:
            return (
                relation.schema_name == parts[0]
                and relation.name == parts[1]
                and identity.name == parts[2]
            )
        if len(parts) == 2 and relation.schema_name is None:
            return relation.name == parts[0] and identity.name == parts[1]
        return False
    if reference.reference_kind in {
        soleaux.postgresql.contracts.ReferenceKind.CONSTRAINT,
        soleaux.postgresql.contracts.ReferenceKind.TRIGGER,
        soleaux.postgresql.contracts.ReferenceKind.POLICY,
    }:
        kind = {
            soleaux.postgresql.contracts.ReferenceKind.CONSTRAINT: (
                soleaux.postgresql.contracts.ObjectKind.CONSTRAINT
            ),
            soleaux.postgresql.contracts.ReferenceKind.TRIGGER: (
                soleaux.postgresql.contracts.ObjectKind.TRIGGER
            ),
            soleaux.postgresql.contracts.ReferenceKind.POLICY: (
                soleaux.postgresql.contracts.ObjectKind.POLICY
            ),
        }[reference.reference_kind]
        return (
            isinstance(identity, soleaux.postgresql.contracts.ScopedObjectIdentity)
            and identity.kind is kind
            and _qualified_name_matches(
                parts,
                identity.relation.schema_name,
                identity.name,
            )
        )
    if not isinstance(identity, soleaux.postgresql.contracts.ObjectIdentity):
        return False
    if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.RELATION:
        expected_kinds: frozenset[soleaux.postgresql.contracts.ObjectKind] = frozenset(
            {
                soleaux.postgresql.contracts.ObjectKind.SEQUENCE,
                soleaux.postgresql.contracts.ObjectKind.TABLE,
                soleaux.postgresql.contracts.ObjectKind.PARTITION,
                soleaux.postgresql.contracts.ObjectKind.FOREIGN_TABLE,
                soleaux.postgresql.contracts.ObjectKind.VIEW,
                soleaux.postgresql.contracts.ObjectKind.MATERIALIZED_VIEW,
            }
        )
    elif reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.TYPE:
        expected_kinds = frozenset(
            {
                soleaux.postgresql.contracts.ObjectKind.ENUM,
                soleaux.postgresql.contracts.ObjectKind.DOMAIN,
                soleaux.postgresql.contracts.ObjectKind.COMPOSITE_TYPE,
                soleaux.postgresql.contracts.ObjectKind.RANGE_TYPE,
            }
        )
    elif reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.ROLE:
        expected_kinds = frozenset({soleaux.postgresql.contracts.ObjectKind.ROLE})
    elif reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.EXTENSION:
        expected_kinds = frozenset({soleaux.postgresql.contracts.ObjectKind.EXTENSION})
    else:
        expected_kinds = frozenset()
    return identity.kind in expected_kinds and _qualified_name_matches(
        parts,
        identity.schema_name,
        identity.name,
    )


def _resolve_call(
    call: soleaux.postgresql.contracts.CallFact,
    identities: collections.abc.Sequence[soleaux.postgresql.contracts.PostgreSqlIdentity],
) -> soleaux.postgresql.contracts.TargetResolution:
    candidates = tuple(
        identity
        for identity in identities
        if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity)
        and _qualified_name_matches(
            call.callee_parts,
            identity.schema_name,
            identity.name,
        )
        and len(identity.signature.input_argument_types) == call.argument_count
    )
    return _target_resolution(candidates)


def _qualified_name_matches(
    parts: collections.abc.Sequence[str],
    schema_name: str | None,
    name: str,
) -> bool:
    if len(parts) == 1:
        return schema_name is None and parts[0] == name
    return ".".join(parts[:-1]) == schema_name and parts[-1] == name


def _target_resolution(
    candidates: collections.abc.Sequence[soleaux.postgresql.contracts.PostgreSqlIdentity],
) -> soleaux.postgresql.contracts.TargetResolution:
    ordered = tuple(sorted(candidates, key=_identity_order_key))
    if not ordered:
        return soleaux.postgresql.contracts.TargetResolution(
            state=soleaux.postgresql.contracts.ResolutionState.UNRESOLVED
        )
    if len(ordered) == 1:
        return soleaux.postgresql.contracts.TargetResolution(
            state=soleaux.postgresql.contracts.ResolutionState.RESOLVED,
            target=ordered[0],
        )
    return soleaux.postgresql.contracts.TargetResolution(
        state=soleaux.postgresql.contracts.ResolutionState.AMBIGUOUS,
        candidates=ordered,
    )


def _identity_order_key(identity: soleaux.postgresql.contracts.PostgreSqlIdentity) -> str:
    return identity.model_dump_json(by_alias=True)


def _identity_key(identity: soleaux.postgresql.contracts.PostgreSqlIdentity) -> tuple[str, ...]:
    if isinstance(identity, soleaux.postgresql.contracts.ObjectIdentity):
        return (
            identity.identity_type,
            identity.kind.value,
            identity.schema_name or "",
            identity.name,
        )
    if isinstance(identity, soleaux.postgresql.contracts.ScopedObjectIdentity):
        return (
            identity.identity_type,
            identity.kind.value,
            identity.relation.kind.value,
            identity.relation.schema_name or "",
            identity.relation.name,
            identity.name,
        )
    if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity):
        return (
            identity.identity_type,
            identity.kind.value,
            identity.schema_name,
            identity.name,
            *identity.signature.input_argument_types,
        )
    return (
        identity.identity_type,
        identity.relation.kind.value,
        identity.relation.schema_name or "",
        identity.relation.name,
        identity.name,
    )


def _deduplicate_references(
    references: collections.abc.Iterable[soleaux.postgresql.contracts.ReferenceFact],
) -> list[soleaux.postgresql.contracts.ReferenceFact]:
    rows: dict[str, soleaux.postgresql.contracts.ReferenceFact] = {}
    for reference in references:
        rows.setdefault(reference.model_dump_json(), reference)
    return list(rows.values())


def _deduplicate_calls(
    calls: collections.abc.Iterable[soleaux.postgresql.contracts.CallFact],
) -> list[soleaux.postgresql.contracts.CallFact]:
    rows: dict[str, soleaux.postgresql.contracts.CallFact] = {}
    for call in calls:
        rows.setdefault(call.model_dump_json(), call)
    return list(rows.values())


def _deduplicate_diagnostics(
    diagnostics: collections.abc.Iterable[soleaux.postgresql.contracts.DiagnosticFact],
) -> list[soleaux.postgresql.contracts.DiagnosticFact]:
    rows: dict[str, soleaux.postgresql.contracts.DiagnosticFact] = {}
    for diagnostic in diagnostics:
        rows.setdefault(diagnostic.model_dump_json(), diagnostic)
    return list(rows.values())


def _postgresql_engine(
    *,
    workspace_id: str,
    project_id: str,
    extraction: PostgreSqlCatalogExtraction,
) -> soleaux.catalog.contracts.EngineFact:
    semantic = extraction.repository_resolved
    return soleaux.catalog.contracts.EngineFact(
        workspace_id=workspace_id,
        source_path=extraction.context.path,
        source_digest=extraction.context.source_digest,
        producer=POSTGRESQL_CATALOG_PRODUCER,
        producer_version=extraction.parser_version,
        project_id=project_id,
        engine_id=POSTGRESQL_ENGINE_ID,
        role=soleaux.catalog.contracts.EngineRole.API,
        package_name="@libpg-query/parser",
        package_version=extraction.parser_version,
        api_entrypoint="parseSync",
        capabilities=(
            (
                "calls",
                "checker_symbols",
                "definitions",
                "diagnostics",
                "references",
            )
            if semantic
            else ()
        ),
        available=semantic,
        coverage="semantic" if semantic else "syntactic",
        omitted_reasons=() if semantic else ("repository resolution not completed",),
    )


def _enrich_postgresql_symbols(
    symbols: collections.abc.Sequence[soleaux.catalog.contracts.SymbolFact],
    *,
    workspace_id: str,
    sources: collections.abc.Mapping[str, bytes],
    extractions: collections.abc.Sequence[PostgreSqlCatalogExtraction],
) -> tuple[soleaux.catalog.contracts.SymbolFact, ...]:
    references: dict[str, list[soleaux.catalog.contracts.SemanticLocation]] = {}
    calls: dict[str, list[soleaux.catalog.contracts.SemanticCallSite]] = {}
    replaced_paths = frozenset(extraction.context.path for extraction in extractions)
    for extraction in extractions:
        codec = soleaux.contracts.positions.PositionCodec(sources[extraction.context.path])
        for reference in extraction.references:
            target = reference.resolution.target
            bounds = _source_bounds(reference.source, codec)
            if target is None or bounds is None:
                continue
            references.setdefault(_symbol_id(workspace_id, target), []).append(
                soleaux.catalog.contracts.SemanticLocation(
                    path=reference.source.path,
                    byte_start=bounds[0],
                    byte_end=bounds[1],
                    kind=reference.reference_kind.value,
                    name=".".join(reference.name_parts),
                )
            )
        for call in extraction.calls:
            target = call.resolution.target
            bounds = _source_bounds(call.source, codec)
            if target is None or bounds is None:
                continue
            calls.setdefault(_symbol_id(workspace_id, target), []).append(
                soleaux.catalog.contracts.SemanticCallSite(
                    path=call.source.path,
                    byte_start=bounds[0],
                    byte_end=bounds[1],
                    callee=".".join(call.callee_parts),
                )
            )

    enriched: list[soleaux.catalog.contracts.SymbolFact] = []
    for symbol in symbols:
        retained_references = tuple(
            location for location in symbol.references if location.path not in replaced_paths
        )
        retained_calls = tuple(call for call in symbol.calls if call.path not in replaced_paths)
        enriched.append(
            symbol.model_copy(
                update={
                    "references": _unique_semantic_locations(
                        (*retained_references, *references.get(symbol.symbol_id, ()))
                    ),
                    "calls": _unique_semantic_calls(
                        (*retained_calls, *calls.get(symbol.symbol_id, ()))
                    ),
                }
            )
        )
    return tuple(enriched)


def _catalog_diagnostics(
    *,
    workspace_id: str,
    project_id: str,
    source: bytes,
    extraction: PostgreSqlCatalogExtraction,
) -> tuple[soleaux.catalog.contracts.DiagnosticFact, ...]:
    codec = soleaux.contracts.positions.PositionCodec(source)
    diagnostics: list[soleaux.catalog.contracts.DiagnosticFact] = []
    for diagnostic in extraction.diagnostics:
        bounds = _source_bounds(diagnostic.source, codec)
        if bounds is None:
            continue
        identity = (
            f"{workspace_id}\0{diagnostic.source.path}\0"
            f"{extraction.context.source_digest}\0{diagnostic.origin.value}\0"
            f"{diagnostic.severity.value}\0{diagnostic.code or ''}\0"
            f"{diagnostic.message}\0{bounds[0]}\0{bounds[1]}"
        ).encode()
        diagnostics.append(
            soleaux.catalog.contracts.DiagnosticFact(
                workspace_id=workspace_id,
                source_path=diagnostic.source.path,
                source_digest=extraction.context.source_digest,
                producer=POSTGRESQL_CATALOG_PRODUCER,
                producer_version=extraction.parser_version,
                diagnostic_id=soleaux.contracts.repository.content_digest(identity),
                project_id=project_id,
                path=diagnostic.source.path,
                engine_id=POSTGRESQL_ENGINE_ID,
                category=diagnostic.severity.value,
                code=diagnostic.code,
                message=diagnostic.message,
                byte_start=bounds[0],
                byte_end=bounds[1],
                coverage=diagnostic.origin.value,
            )
        )
    return tuple(diagnostics)


def _source_bounds(
    source: soleaux.postgresql.contracts.SourceAnchor,
    codec: soleaux.contracts.positions.PositionCodec,
) -> tuple[int, int] | None:
    location = source.location
    if location.range is not None:
        return location.range.start.byte, location.range.end.byte
    if location.point is not None:
        return location.point.byte, location.point.byte
    if location.line is None:
        return None
    try:
        byte = codec.point_to_byte(location.line, 0)
    except ValueError:
        return None
    return byte, byte


def _unique_semantic_locations(
    locations: collections.abc.Iterable[soleaux.catalog.contracts.SemanticLocation],
) -> tuple[soleaux.catalog.contracts.SemanticLocation, ...]:
    rows = {
        (
            location.path,
            location.byte_start,
            location.byte_end,
            location.kind or "",
            location.name or "",
        ): location
        for location in locations
    }
    return tuple(rows[key] for key in sorted(rows))


def _unique_semantic_calls(
    calls: collections.abc.Iterable[soleaux.catalog.contracts.SemanticCallSite],
) -> tuple[soleaux.catalog.contracts.SemanticCallSite, ...]:
    rows = {
        (
            call.path,
            call.byte_start,
            call.byte_end,
            call.callee,
            call.signature_text or "",
            call.return_type_text or "",
        ): call
        for call in calls
    }
    return tuple(rows[key] for key in sorted(rows))


def _symbol_id(workspace_id: str, identity: soleaux.postgresql.contracts.PostgreSqlIdentity) -> str:
    identity_payload = json.dumps(
        identity.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return soleaux.contracts.repository.content_digest(
        f"{workspace_id}\0{identity_payload}".encode()
    )


def _symbol_from_declaration(
    declaration: soleaux.postgresql.contracts.DeclarationFact,
    *,
    workspace_id: str,
    project_id: str,
    parser_version: str,
    source_digest: str,
    semantic: bool,
) -> soleaux.catalog.contracts.SymbolFact:
    location = declaration.source.location.range
    if location is None:
        raise ValueError("PostgreSQL declaration promotion requires an exact range")
    symbol_id = _symbol_id(workspace_id, declaration.identity)
    revision_id = soleaux.contracts.repository.content_digest(
        (
            f"{symbol_id}\0{declaration.source.path}\0"
            f"{source_digest}\0{declaration.source.parser_generation}\0"
            f"{location.start.byte}\0{location.end.byte}\0{declaration.action.value}"
        ).encode()
    )
    identity = declaration.identity
    signature: str | None = None
    if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity):
        symbol_kind = identity.kind.value
        signature = (
            f"{identity.schema_name}.{identity.name}"
            f"({', '.join(identity.signature.input_argument_types)})"
        )
    elif isinstance(identity, soleaux.postgresql.contracts.ObjectIdentity):
        symbol_kind = identity.kind.value
    elif isinstance(identity, soleaux.postgresql.contracts.ScopedObjectIdentity):
        symbol_kind = identity.kind.value
        signature = (
            f"{identity.relation.schema_name + '.' if identity.relation.schema_name else ''}"
            f"{identity.relation.name}.{identity.name}"
        )
    else:
        symbol_kind = "column"
        signature = (
            f"{identity.relation.schema_name + '.' if identity.relation.schema_name else ''}"
            f"{identity.relation.name}.{identity.name}"
        )
    semantic_location = soleaux.catalog.contracts.SemanticLocation(
        path=declaration.source.path,
        byte_start=location.start.byte,
        byte_end=location.end.byte,
        kind=declaration.action.value,
        name=identity.name,
    )
    return soleaux.catalog.contracts.SymbolFact(
        workspace_id=workspace_id,
        source_path=declaration.source.path,
        source_digest=source_digest,
        producer=POSTGRESQL_CATALOG_PRODUCER,
        producer_version=parser_version,
        symbol_id=symbol_id,
        revision_id=revision_id,
        project_id=project_id,
        path=declaration.source.path,
        name=identity.name,
        symbol_kind=symbol_kind,
        byte_start=location.start.byte,
        byte_end=location.end.byte,
        type_text=signature,
        signatures=(signature,) if signature is not None else (),
        declarations=(semantic_location,),
        definitions=(semantic_location,),
        engine_id=POSTGRESQL_ENGINE_ID,
        coverage="semantic" if semantic else "syntactic",
    )


def _postgresql_chunks(
    *,
    workspace_id: str,
    source: str,
    extraction: PostgreSqlCatalogExtraction,
) -> tuple[soleaux.catalog.contracts.ChunkFact, ...]:
    context = extraction.context
    content = source.encode("utf-8")
    codec = soleaux.contracts.positions.PositionCodec(content)
    ranges: list[tuple[int, int, str]] = []
    cursor = 0
    for statement in extraction.statements:
        location = statement.source.location.range
        if location is None:
            raise ValueError("PostgreSQL statement chunk requires an exact range")
        start, end = location.start.byte, location.end.byte
        if start < cursor or end < start or end > len(content):
            raise ValueError("PostgreSQL statement ranges overlap or exceed captured source")
        if cursor < start:
            ranges.append((cursor, start, "postgresql_gap"))
        ranges.append((start, end, "postgresql_statement"))
        cursor = end
    if cursor < len(content):
        ranges.append((cursor, len(content), "postgresql_gap"))

    chunks: list[soleaux.catalog.contracts.ChunkFact] = []
    for start, end, kind in ranges:
        if start == end:
            continue
        start_line, end_line = _chunk_lines(codec, start, end)
        identity = (
            f"{workspace_id}\0{context.path}\0{context.source_digest}\0{start}\0{end}"
        ).encode()
        chunks.append(
            soleaux.catalog.contracts.ChunkFact(
                workspace_id=workspace_id,
                source_path=context.path,
                source_digest=context.source_digest,
                producer=POSTGRESQL_CATALOG_PRODUCER,
                producer_version=extraction.parser_version,
                chunk_id=soleaux.contracts.repository.content_digest(identity),
                path=context.path,
                language_id="sql",
                chunk_kind=kind,
                start_line=start_line,
                end_line=end_line,
                byte_start=start,
                byte_end=end,
                text=content[start:end].decode("utf-8"),
            )
        )
    return tuple(chunks)


def _chunk_lines(
    codec: soleaux.contracts.positions.PositionCodec, start: int, end: int
) -> tuple[int, int]:
    start_point = codec.byte_to_point(start)
    end_point = codec.byte_to_point(end)
    start_line = start_point.line + 1
    inclusive_end = end_point.line + (1 if end_point.column > 0 else 0)
    return start_line, max(start_line, inclusive_end)
