"""Bounded PostgreSQL parser-document promotion into generic catalog facts."""

from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Sequence

import _host_root
import pytest
from _assertions import raises_with_message

import soleaux.catalog.postgresql
import soleaux.postgresql.analyzer
import soleaux.postgresql.contracts
import soleaux.postgresql.node_runtime
import soleaux.structural.supervisor
import soleaux.structural.worker
from soleaux.catalog.contracts import (
    CatalogFacts,
    ChunkFact,
    ProjectFact,
    ProjectKind,
)
from soleaux.contracts.repository import content_digest

REPOSITORY_ROOT = _host_root.require_host_root()


def _string(value: str) -> dict[str, object]:
    return {"String": {"sval": value}}


def _name(*parts: str) -> list[dict[str, object]]:
    return [_string(part) for part in parts]


def _range_var(name: str, *, schema: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"relname": name}
    if schema is not None:
        payload["schemaname"] = schema
    return {"RangeVar": payload}


def _type_name(*parts: str) -> dict[str, object]:
    return {"TypeName": {"names": _name(*parts)}}


def _parameter(
    type_name: str,
    *,
    mode: str = "FUNC_PARAM_DEFAULT",
) -> dict[str, object]:
    return {
        "FunctionParameter": {
            "mode": mode,
            "argType": _type_name(type_name),
        }
    }


def _document(
    statements: Sequence[tuple[str, str, dict[str, object]]],
    *,
    prefix: str = "",
    separator: str = "\n",
    suffix: str = "",
) -> tuple[str, soleaux.postgresql.node_runtime.ParserDocument]:
    source_parts = [prefix]
    raw_statements: list[dict[str, object]] = []
    tokens: list[soleaux.postgresql.node_runtime.ScanToken] = []
    cursor = len(prefix.encode("utf-8"))
    for index, (sql, kind, payload) in enumerate(statements):
        if index:
            source_parts.append(separator)
            cursor += len(separator.encode("utf-8"))
        encoded = sql.encode("utf-8")
        source_parts.append(sql)
        tokens.extend(
            (
                soleaux.postgresql.node_runtime.ScanToken(
                    start=cursor,
                    end=cursor + 1,
                    text=sql[0],
                    token_type=258,
                    token_name="IDENT",
                ),
                soleaux.postgresql.node_runtime.ScanToken(
                    start=cursor + len(encoded) - 1,
                    end=cursor + len(encoded),
                    text=";",
                    token_type=soleaux.postgresql.analyzer.SEMICOLON_TOKEN_TYPE,
                    token_name="ASCII_59",
                ),
            )
        )
        raw_statements.append({"stmt": {kind: payload}})
        cursor += len(encoded)
    source_parts.append(suffix)
    source = "".join(source_parts)
    return source, soleaux.postgresql.node_runtime.ParserDocument(
        parse_tree={"version": 170004, "stmts": raw_statements},
        tokens=tuple(tokens),
        parser_version=soleaux.postgresql.node_runtime.PARSER_VERSION,
        postgresql_version=170004,
    )


def _context(
    source: str,
    *,
    path: str = "schema.sql",
    source_lane: soleaux.postgresql.contracts.SourceLane | None = None,
) -> soleaux.catalog.postgresql.PostgreSqlCatalogContext:
    return soleaux.catalog.postgresql.PostgreSqlCatalogContext(
        snapshot_id="snapshot-1",
        path=path,
        source_digest=content_digest(source.encode("utf-8")),
        source_lane=(
            source_lane
            if source_lane is not None
            else soleaux.catalog.postgresql.source_lane_for_path(path)
        ),
    )


def _generic_chunk(path: str, source: bytes) -> ChunkFact:
    digest = content_digest(source)
    return ChunkFact(
        workspace_id="main",
        source_path=path,
        source_digest=digest,
        producer="generic",
        producer_version="1",
        chunk_id=content_digest(f"{path}\0generic".encode()),
        path=path,
        language_id="sql",
        chunk_kind="source",
        start_line=1,
        end_line=1,
        byte_start=0,
        byte_end=len(source),
        text=source.decode("utf-8"),
    )


def _declaration_kind(
    declaration: soleaux.postgresql.contracts.DeclarationFact,
) -> soleaux.postgresql.contracts.ObjectKind:
    identity = declaration.identity
    if isinstance(
        identity,
        (
            soleaux.postgresql.contracts.ObjectIdentity,
            soleaux.postgresql.contracts.RoutineIdentity,
        ),
    ):
        return identity.kind
    raise AssertionError("catalog declarations cannot be column identities")


@pytest.mark.parametrize(
    "path",
    [
        "generated/fixture/test/migration/schema.sql",
        "schemas/accounts.sql",
        "database/history/001.sql",
        "arbitrary/custom/layout.sql",
    ],
)
def test_source_lane_has_no_built_in_path_vocabulary(path: str) -> None:
    assert (
        soleaux.catalog.postgresql.source_lane_for_path(path)
        is soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
    )


def test_source_lane_uses_only_caller_supplied_roots_and_rejects_ambiguity() -> None:
    lane_roots = {
        soleaux.postgresql.contracts.SourceLane.DESIRED_STATE: ("alpha",),
        soleaux.postgresql.contracts.SourceLane.TEST: ("alpha/checks",),
    }

    assert (
        soleaux.catalog.postgresql.source_lane_for_path(
            "alpha/checks/example.sql",
            lane_roots=lane_roots,
        )
        is soleaux.postgresql.contracts.SourceLane.TEST
    )
    assert (
        soleaux.catalog.postgresql.source_lane_for_path(
            "unmapped/example.sql",
            lane_roots=lane_roots,
        )
        is soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED
    )
    with raises_with_message(ValueError, "ambiguous"):
        soleaux.catalog.postgresql.source_lane_for_path(
            "same/example.sql",
            lane_roots={
                soleaux.postgresql.contracts.SourceLane.TEST: ("same",),
                soleaux.postgresql.contracts.SourceLane.FIXTURE: ("same",),
            },
        )


def test_ambiguous_target_order_preserves_the_public_canonical_order() -> None:
    short_signature = soleaux.postgresql.contracts.RoutineIdentity(
        kind=soleaux.postgresql.contracts.ObjectKind.FUNCTION,
        schema="app",
        name="work",
        signature=soleaux.postgresql.contracts.RoutineSignature(
            input_argument_types=("integer",),
        ),
    )
    long_signature = short_signature.model_copy(
        update={
            "signature": soleaux.postgresql.contracts.RoutineSignature(
                input_argument_types=("integer", "text"),
            )
        }
    )

    resolution = soleaux.catalog.postgresql._target_resolution(  # pyright: ignore[reportPrivateUsage]
        (short_signature, long_signature),
    )

    assert resolution.state is soleaux.postgresql.contracts.ResolutionState.AMBIGUOUS
    assert tuple(
        identity.signature.input_argument_types
        for identity in resolution.candidates
        if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity)
    ) == (("integer", "text"), ("integer",))


def test_extracts_every_statement_and_only_bounded_source_provable_creates() -> None:
    statements: list[tuple[str, str, dict[str, object]]] = [
        ("CREATE SCHEMA app;", "CreateSchemaStmt", {"schemaname": "app"}),
        ("CREATE EXTENSION citext;", "CreateExtensionStmt", {"extname": "citext"}),
        (
            "CREATE TYPE app.mood AS ENUM ('ok');",
            "CreateEnumStmt",
            {"typeName": _name("app", "mood")},
        ),
        (
            "CREATE DOMAIN app.email AS text;",
            "CreateDomainStmt",
            {"domainname": _name("app", "email")},
        ),
        (
            "CREATE TYPE app.address AS (city text);",
            "CompositeTypeStmt",
            {"typevar": _range_var("address", schema="app")},
        ),
        (
            "CREATE TYPE app.span AS RANGE (subtype=int4);",
            "CreateRangeStmt",
            {"typeName": _name("app", "span")},
        ),
        (
            "CREATE SEQUENCE app.ids;",
            "CreateSeqStmt",
            {"sequence": _range_var("ids", schema="app")},
        ),
        (
            "CREATE TABLE app.accounts (id int PRIMARY KEY);",
            "CreateStmt",
            {
                "relation": _range_var("accounts", schema="app"),
                "tableElts": [{"Constraint": {"contype": "CONSTR_PRIMARY"}}],
            },
        ),
        (
            "CREATE TABLE app.accounts_1 PARTITION OF app.accounts FOR VALUES IN (1);",
            "CreateStmt",
            {
                "relation": _range_var("accounts_1", schema="app"),
                "partbound": {"PartitionBoundSpec": {}},
            },
        ),
        (
            "CREATE FOREIGN TABLE app.remote_accounts (id int) SERVER remote;",
            "CreateForeignTableStmt",
            {"base": {"relation": _range_var("remote_accounts", schema="app")}},
        ),
        (
            "CREATE OR REPLACE VIEW app.active AS SELECT 1;",
            "ViewStmt",
            {"view": _range_var("active", schema="app"), "replace": True},
        ),
        (
            "CREATE MATERIALIZED VIEW app.summary AS SELECT 1;",
            "CreateTableAsStmt",
            {
                "objtype": "OBJECT_MATVIEW",
                "into": {"IntoClause": {"rel": _range_var("summary", schema="app")}},
            },
        ),
        (
            "CREATE TABLE app.snapshot AS SELECT 1;",
            "CreateTableAsStmt",
            {
                "objtype": "OBJECT_TABLE",
                "into": {"IntoClause": {"rel": _range_var("snapshot", schema="app")}},
            },
        ),
        (
            "CREATE INDEX account_idx ON app.accounts (id);",
            "IndexStmt",
            {
                "idxname": "account_idx",
                "relation": _range_var("accounts", schema="app"),
            },
        ),
        (
            "CREATE OR REPLACE FUNCTION app.lookup(integer, OUT text, INOUT uuid) RETURNS text;",
            "CreateFunctionStmt",
            {
                "funcname": _name("app", "lookup"),
                "replace": True,
                "parameters": [
                    _parameter("integer"),
                    _parameter("text", mode="FUNC_PARAM_OUT"),
                    _parameter("uuid", mode="FUNC_PARAM_INOUT"),
                ],
            },
        ),
        (
            "CREATE PROCEDURE app.refresh();",
            "CreateFunctionStmt",
            {
                "funcname": _name("app", "refresh"),
                "is_procedure": True,
                "parameters": [],
            },
        ),
        (
            "CREATE AGGREGATE app.total(integer);",
            "DefineStmt",
            {
                "kind": "OBJECT_AGGREGATE",
                "defnames": _name("app", "total"),
                "args": [_type_name("integer")],
            },
        ),
        ("CREATE ROLE application;", "CreateRoleStmt", {"role": "application"}),
        ("ALTER TABLE app.accounts ADD COLUMN name text;", "AlterTableStmt", {}),
        ("SELECT 1;", "SelectStmt", {}),
    ]
    source, document = _document(statements)
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )

    assert len(extraction.statements) == len(statements)
    assert [fact.source.statement_index for fact in extraction.statements] == list(
        range(len(statements))
    )
    assert all(
        fact.source.location.kind is soleaux.postgresql.contracts.LocationKind.EXACT_RANGE
        for fact in extraction.statements
    )
    assert [_declaration_kind(declaration) for declaration in extraction.declarations] == [
        soleaux.postgresql.contracts.ObjectKind.SCHEMA,
        soleaux.postgresql.contracts.ObjectKind.EXTENSION,
        soleaux.postgresql.contracts.ObjectKind.ENUM,
        soleaux.postgresql.contracts.ObjectKind.DOMAIN,
        soleaux.postgresql.contracts.ObjectKind.COMPOSITE_TYPE,
        soleaux.postgresql.contracts.ObjectKind.RANGE_TYPE,
        soleaux.postgresql.contracts.ObjectKind.SEQUENCE,
        soleaux.postgresql.contracts.ObjectKind.TABLE,
        soleaux.postgresql.contracts.ObjectKind.PARTITION,
        soleaux.postgresql.contracts.ObjectKind.FOREIGN_TABLE,
        soleaux.postgresql.contracts.ObjectKind.VIEW,
        soleaux.postgresql.contracts.ObjectKind.MATERIALIZED_VIEW,
        soleaux.postgresql.contracts.ObjectKind.TABLE,
        soleaux.postgresql.contracts.ObjectKind.INDEX,
        soleaux.postgresql.contracts.ObjectKind.FUNCTION,
        soleaux.postgresql.contracts.ObjectKind.PROCEDURE,
        soleaux.postgresql.contracts.ObjectKind.AGGREGATE,
        soleaux.postgresql.contracts.ObjectKind.ROLE,
    ]
    function = extraction.declarations[14]
    assert isinstance(function.identity, soleaux.postgresql.contracts.RoutineIdentity)
    assert function.action is soleaux.postgresql.contracts.DeclarationAction.CREATE_OR_REPLACE
    assert function.identity.signature.input_argument_types == ("integer", "uuid")
    assert [omission.statement_kind for omission in extraction.omissions] == [
        "CreateStmt",
        "AlterTableStmt",
    ]


def test_real_parser_extracts_relations_routines_security_and_plpgsql() -> None:
    installation = soleaux.postgresql.node_runtime.resolve_parser_installation(REPOSITORY_ROOT)
    if installation is None:
        pytest.skip("the pinned @libpg-query/parser package is not installed")
    source = """\
CREATE ROLE app_user;
CREATE TABLE app.accounts (
  id bigint,
  owner_id uuid,
  CONSTRAINT accounts_owner_fk FOREIGN KEY(owner_id) REFERENCES auth.users(id)
);
CREATE FUNCTION app.seed() RETURNS integer LANGUAGE sql AS $$SELECT 1$$;
CREATE FUNCTION app.worker() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  PERFORM app.seed();
  EXECUTE format('select %I', 'id');
END
$$;
CREATE POLICY tenant ON app.accounts TO app_user
  USING (owner_id = auth.uid());
CREATE TRIGGER sync BEFORE INSERT ON app.accounts
  FOR EACH ROW EXECUTE FUNCTION app.worker();
CREATE EVENT TRIGGER ddl_log ON ddl_command_end
  EXECUTE FUNCTION app.worker();
CREATE PUBLICATION pub FOR TABLE app.accounts;
CREATE SUBSCRIPTION sub CONNECTION 'host=localhost'
  PUBLICATION pub WITH (connect=false);
"""
    runtime = soleaux.postgresql.node_runtime.NodeParserRuntime(installation)
    try:
        document = runtime.analyze(source)
    finally:
        runtime.close()

    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )
    identities = tuple(declaration.identity for declaration in extraction.declarations)
    object_kinds = {
        identity.kind
        for identity in identities
        if isinstance(identity, soleaux.postgresql.contracts.ObjectIdentity)
    }
    scoped_kinds = {
        identity.kind
        for identity in identities
        if isinstance(identity, soleaux.postgresql.contracts.ScopedObjectIdentity)
    }
    column_names = {
        identity.name
        for identity in identities
        if isinstance(identity, soleaux.postgresql.contracts.ColumnIdentity)
    }
    routine_names = {
        identity.name
        for identity in identities
        if isinstance(identity, soleaux.postgresql.contracts.RoutineIdentity)
    }

    assert len(extraction.statements) == 9
    assert object_kinds == {
        soleaux.postgresql.contracts.ObjectKind.ROLE,
        soleaux.postgresql.contracts.ObjectKind.TABLE,
        soleaux.postgresql.contracts.ObjectKind.EVENT_TRIGGER,
        soleaux.postgresql.contracts.ObjectKind.PUBLICATION,
        soleaux.postgresql.contracts.ObjectKind.SUBSCRIPTION,
    }
    assert scoped_kinds == {
        soleaux.postgresql.contracts.ObjectKind.CONSTRAINT,
        soleaux.postgresql.contracts.ObjectKind.TRIGGER,
        soleaux.postgresql.contracts.ObjectKind.POLICY,
    }
    assert column_names == {"id", "owner_id"}
    assert routine_names == {"seed", "worker"}
    assert extraction.omissions == ()
    assert any(
        reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.RELATION
        and reference.name_parts == ("auth", "users")
        for reference in extraction.references
    )
    assert any(
        call.call_kind is soleaux.postgresql.contracts.CallKind.FUNCTION
        and call.callee_parts == ("app", "seed")
        and call.source.statement_index == 3
        and call.resolution.state is soleaux.postgresql.contracts.ResolutionState.RESOLVED
        for call in extraction.calls
    )
    assert (
        sum(
            call.call_kind is soleaux.postgresql.contracts.CallKind.TRIGGER
            and call.callee_parts == ("app", "worker")
            and call.resolution.state is soleaux.postgresql.contracts.ResolutionState.RESOLVED
            for call in extraction.calls
        )
        == 2
    )
    assert any(
        reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.DYNAMIC_SQL
        and reference.source.statement_index == 3
        and reference.resolution.state is soleaux.postgresql.contracts.ResolutionState.PARTIAL
        for reference in extraction.references
    )

    assert extraction.repository_resolved is False
    (extraction,) = soleaux.catalog.postgresql.resolve_postgresql_catalog((extraction,))
    assert extraction.repository_resolved is True
    merged = soleaux.catalog.postgresql.merge_postgresql_catalog(
        CatalogFacts(),
        workspace_id="main",
        sources={extraction.context.path: source.encode("utf-8")},
        extractions=(extraction,),
    )
    role = next(
        symbol
        for symbol in merged.symbols
        if symbol.symbol_kind == soleaux.postgresql.contracts.ObjectKind.ROLE.value
    )
    worker = next(symbol for symbol in merged.symbols if symbol.name == "worker")
    seed = next(symbol for symbol in merged.symbols if symbol.name == "seed")
    assert all(symbol.coverage == "semantic" for symbol in merged.symbols)
    assert len(merged.engines) == 1
    engine = merged.engines[0]
    assert engine.available is True
    assert engine.coverage == "semantic"
    assert engine.capabilities == (
        "calls",
        "checker_symbols",
        "definitions",
        "diagnostics",
        "references",
    )
    assert len(role.references) == 1
    assert role.references[0].name == "app_user"
    assert [call.callee for call in worker.calls] == [
        "app.worker",
        "app.worker",
    ]
    assert [call.callee for call in seed.calls] == ["app.seed"]


def test_real_parser_extracts_unwrapped_call_statement() -> None:
    installation = soleaux.postgresql.node_runtime.resolve_parser_installation(REPOSITORY_ROOT)
    if installation is None:
        pytest.skip("the pinned @libpg-query/parser package is not installed")
    source = """\
CREATE PROCEDURE app.work() LANGUAGE sql AS $$SELECT 1$$;
CALL app.work();
"""
    runtime = soleaux.postgresql.node_runtime.NodeParserRuntime(installation)
    try:
        document = runtime.analyze(source)
    finally:
        runtime.close()

    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )

    call = next(
        call
        for call in extraction.calls
        if call.call_kind is soleaux.postgresql.contracts.CallKind.PROCEDURE
    )
    assert call.callee_parts == ("app", "work")
    assert call.resolution.state is soleaux.postgresql.contracts.ResolutionState.RESOLVED
    assert any(
        reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.ROUTINE
        and reference.name_parts == ("app", "work")
        for reference in extraction.references
    )


def test_repository_resolution_replays_migrations_and_projects_desired_state() -> None:
    installation = soleaux.postgresql.node_runtime.resolve_parser_installation(REPOSITORY_ROOT)
    if installation is None:
        pytest.skip("the pinned @libpg-query/parser package is not installed")
    runtime = soleaux.postgresql.node_runtime.NodeParserRuntime(installation)

    def extract(
        source: str,
        path: str,
        source_lane: soleaux.postgresql.contracts.SourceLane,
    ) -> soleaux.catalog.postgresql.PostgreSqlCatalogExtraction:
        document = runtime.analyze(source)
        return soleaux.catalog.postgresql.extract_postgresql_catalog(
            source,
            document,
            _context(source, path=path, source_lane=source_lane),
        )

    try:
        migrations = (
            extract(
                "CREATE TABLE app.accounts (id integer);",
                "migration/001_accounts.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
            extract(
                "SELECT * FROM app.accounts;",
                "migration/002_read_accounts.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
            extract(
                "DROP TABLE app.accounts;",
                "migration/003_drop_accounts.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
            extract(
                "SELECT * FROM app.accounts;",
                "migration/004_read_dropped_accounts.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
        )
        desired = extract(
            "CREATE TABLE app.accounts (id integer);",
            "schema/accounts.sql",
            soleaux.postgresql.contracts.SourceLane.DESIRED_STATE,
        )
        test_query = extract(
            "SELECT * FROM app.accounts;",
            "test/accounts.sql",
            soleaux.postgresql.contracts.SourceLane.TEST,
        )
        same_file_drop = (
            migrations[0],
            extract(
                "DROP TABLE app.accounts;\nSELECT * FROM app.accounts;",
                "migration/002_drop_then_read.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
        )
        rename_migrations = (
            migrations[0],
            extract(
                "ALTER TABLE app.accounts RENAME TO users;\n"
                "SELECT * FROM app.accounts;\n"
                "SELECT * FROM app.users;",
                "migration/002_rename_then_read.sql",
                soleaux.postgresql.contracts.SourceLane.MIGRATION_HISTORY,
            ),
        )
    finally:
        runtime.close()

    resolved_migrations = soleaux.catalog.postgresql.resolve_postgresql_catalog(migrations)
    read_before_drop = next(
        reference
        for reference in resolved_migrations[1].references
        if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.RELATION
    )
    read_after_drop = next(
        reference
        for reference in resolved_migrations[3].references
        if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.RELATION
    )
    assert (
        read_before_drop.resolution.state is soleaux.postgresql.contracts.ResolutionState.RESOLVED
    )
    assert (
        read_after_drop.resolution.state is soleaux.postgresql.contracts.ResolutionState.UNRESOLVED
    )

    _, resolved_test = soleaux.catalog.postgresql.resolve_postgresql_catalog((desired, test_query))
    desired_reference = next(
        reference
        for reference in resolved_test.references
        if reference.reference_kind is soleaux.postgresql.contracts.ReferenceKind.RELATION
    )
    assert (
        desired_reference.resolution.state is soleaux.postgresql.contracts.ResolutionState.RESOLVED
    )

    _, resolved_same_file_drop = soleaux.catalog.postgresql.resolve_postgresql_catalog(
        same_file_drop
    )
    dropped_reference = next(
        reference
        for reference in resolved_same_file_drop.references
        if reference.source.statement_index == 1
    )
    assert (
        dropped_reference.resolution.state
        is soleaux.postgresql.contracts.ResolutionState.UNRESOLVED
    )

    _, resolved_rename = soleaux.catalog.postgresql.resolve_postgresql_catalog(rename_migrations)
    renamed_references = {
        reference.name_parts: reference.resolution.state
        for reference in resolved_rename.references
        if reference.source.statement_index in {1, 2}
    }
    assert renamed_references == {
        ("app", "accounts"): soleaux.postgresql.contracts.ResolutionState.UNRESOLVED,
        ("app", "users"): soleaux.postgresql.contracts.ResolutionState.RESOLVED,
    }


def test_incomplete_or_unqualified_routines_are_explicitly_omitted() -> None:
    source, document = _document(
        [
            (
                "CREATE FUNCTION lookup(integer) RETURNS integer;",
                "CreateFunctionStmt",
                {
                    "funcname": _name("lookup"),
                    "parameters": [_parameter("integer")],
                },
            ),
            (
                "CREATE FUNCTION app.lookup(varchar(20)) RETURNS text;",
                "CreateFunctionStmt",
                {
                    "funcname": _name("app", "lookup"),
                    "parameters": [
                        {
                            "FunctionParameter": {
                                "mode": "FUNC_PARAM_IN",
                                "argType": {
                                    "TypeName": {
                                        "names": _name("varchar"),
                                        "typmods": [{"A_Const": {}}],
                                    }
                                },
                            }
                        }
                    ],
                },
            ),
        ]
    )
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )

    assert extraction.declarations == ()
    assert len(extraction.omissions) == 2
    assert all("complete input signature" in item.reason for item in extraction.omissions)


def test_unsupported_lifecycle_actions_remain_distinct() -> None:
    source, document = _document(
        [
            ("ALTER TABLE app.accounts RENAME TO users;", "RenameStmt", {}),
            ("DROP TABLE app.accounts;", "DropStmt", {}),
            (
                "ALTER TABLE app.accounts ATTACH PARTITION app.accounts_1;",
                "AlterTableStmt",
                {"cmds": [{"AlterTableCmd": {"subtype": "AT_AttachPartition"}}]},
            ),
            (
                "ALTER TABLE app.accounts DETACH PARTITION app.accounts_1;",
                "AlterTableStmt",
                {"cmds": [{"AlterTableCmd": {"subtype": "AT_DetachPartition"}}]},
            ),
        ]
    )
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )

    assert extraction.declarations == ()
    assert [omission.action for omission in extraction.omissions] == [
        soleaux.postgresql.contracts.DeclarationAction.RENAME,
        soleaux.postgresql.contracts.DeclarationAction.DROP,
        soleaux.postgresql.contracts.DeclarationAction.ATTACH,
        soleaux.postgresql.contracts.DeclarationAction.DETACH,
    ]


def test_merge_uses_logical_symbol_identity_and_statement_plus_gap_chunks() -> None:
    path = "migration/001_accounts.sql"
    prefix = "-- leading authority\n"
    separator = "\n-- between statements\n"
    suffix = "\n-- trailing authority\n"
    source, document = _document(
        [
            (
                "CREATE TABLE app.accounts (id int);",
                "CreateStmt",
                {"relation": _range_var("accounts", schema="app")},
            ),
            ("SELECT 1;", "SelectStmt", {}),
        ],
        prefix=prefix,
        separator=separator,
        suffix=suffix,
    )
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source, path=path),
    )
    other_source = b"SELECT 2;"
    facts = CatalogFacts(
        projects=(
            ProjectFact(
                workspace_id="main",
                source_path="pyproject.toml",
                source_digest="a" * 64,
                producer="test",
                producer_version="1",
                project_id="main:python:.",
                root_path="",
                manifest_path="pyproject.toml",
                kind=ProjectKind.PYTHON,
            ),
        ),
        chunks=(
            _generic_chunk(path, source.encode("utf-8")),
            _generic_chunk("other.sql", other_source),
        ),
    )
    merged = soleaux.catalog.postgresql.merge_postgresql_catalog(
        facts,
        workspace_id="main",
        sources={path: source.encode("utf-8")},
        extractions=(extraction,),
    )

    path_chunks = [chunk for chunk in merged.chunks if chunk.path == path]
    assert b"".join(chunk.text.encode("utf-8") for chunk in path_chunks) == source.encode("utf-8")
    assert [chunk.chunk_kind for chunk in path_chunks] == [
        "postgresql_gap",
        "postgresql_statement",
        "postgresql_gap",
        "postgresql_statement",
        "postgresql_gap",
    ]
    assert [chunk.path for chunk in merged.chunks if chunk.path == "other.sql"] == ["other.sql"]
    assert len(merged.symbols) == 1
    symbol = merged.symbols[0]
    assert symbol.name == "accounts"
    assert symbol.project_id == "main:python:."
    assert symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID

    desired_path = "schemas/accounts.sql"
    desired_extraction = extraction.model_copy(
        update={
            "context": extraction.context.model_copy(
                update={
                    "path": desired_path,
                    "source_lane": soleaux.postgresql.contracts.SourceLane.DESIRED_STATE,
                }
            ),
            "statements": tuple(
                statement.model_copy(
                    update={
                        "source": statement.source.model_copy(
                            update={
                                "path": desired_path,
                                "source_lane": (
                                    soleaux.postgresql.contracts.SourceLane.DESIRED_STATE
                                ),
                            }
                        )
                    }
                )
                for statement in extraction.statements
            ),
            "declarations": tuple(
                declaration.model_copy(
                    update={
                        "source": declaration.source.model_copy(
                            update={
                                "path": desired_path,
                                "source_lane": (
                                    soleaux.postgresql.contracts.SourceLane.DESIRED_STATE
                                ),
                            }
                        )
                    }
                )
                for declaration in extraction.declarations
            ),
        }
    )
    merged_again = soleaux.catalog.postgresql.merge_postgresql_catalog(
        merged,
        workspace_id="main",
        sources={
            path: source.encode("utf-8"),
            desired_path: source.encode("utf-8"),
        },
        extractions=(extraction, desired_extraction),
    )
    revisions = [
        item
        for item in merged_again.symbols
        if item.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    ]
    assert len(revisions) == 2
    assert revisions[0].symbol_id == revisions[1].symbol_id
    assert revisions[0].revision_id != revisions[1].revision_id


def test_worker_optional_payload_reuses_the_single_parser_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, document = _document(
        [
            (
                "CREATE TABLE app.accounts (id int);",
                "CreateStmt",
                {"relation": _range_var("accounts", schema="app")},
            )
        ]
    )
    root = soleaux.postgresql.analyzer.build_postgresql_root(source, document)
    analysis = soleaux.postgresql.analyzer.PostgreSqlAnalysis(
        root=root,
        document=document,
    )
    calls = 0

    def analyze_once(
        _source: str, _language: str
    ) -> soleaux.postgresql.analyzer.PostgreSqlAnalysis:
        nonlocal calls
        calls += 1
        return analysis

    monkeypatch.setattr(
        soleaux.structural.worker,
        "_postgresql_analysis",
        analyze_once,
    )
    response = soleaux.structural.worker._extract(
        {
            "language": "PostgreSQL",
            "path": "schema.sql",
            "content_b64": base64.b64encode(source.encode("utf-8")).decode("ascii"),
            "projections": [],
            "rules": [],
            "postgresql_catalog": _context(source).model_dump(mode="json"),
        },
        root_factories=soleaux.structural.worker.ROOT_FACTORIES,
        parses=1,
    )

    assert calls == 1
    assert response["status"] == "ok"
    payload = response["postgresql_catalog"]
    extraction = soleaux.catalog.postgresql.PostgreSqlCatalogExtraction.model_validate(payload)
    assert len(extraction.statements) == 1
    assert len(extraction.declarations) == 1


def test_supervisor_cache_payload_round_trips_the_typed_postgresql_result() -> None:
    source, document = _document(
        [
            (
                "CREATE TABLE app.accounts (id int);",
                "CreateStmt",
                {"relation": _range_var("accounts", schema="app")},
            )
        ]
    )
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )
    encoded = json.dumps(
        {
            "status": "ok",
            "fragments": [],
            "diagnostics": [],
            "postgresql_catalog": extraction.model_dump(mode="json"),
            "stats": {
                "parses": 1,
                "parse_ms": 0.1,
                "truncated": False,
                "unsupported": [],
            },
        }
    ).encode()
    decoded = soleaux.structural.supervisor._decode_result(encoded)

    assert decoded.fragments == ()
    assert decoded.diagnostics == ()
    assert decoded.parses == 1
    assert decoded.parse_ms == 0.1
    assert decoded.truncated is False
    assert decoded.unsupported == ()
    assert decoded.postgresql_catalog == extraction


def test_failure_warning_states_that_generic_chunks_remain() -> None:
    assert soleaux.catalog.postgresql.postgresql_catalog_failure_warning(
        "schema.sql",
        error_type="parser_unavailable",
        message="managed parser is not provisioned",
    ) == (
        "schema.sql: PostgreSQL catalog parser_unavailable; "
        "generic chunks retained: managed parser is not provisioned"
    )


def test_parser_diagnostics_promote_into_the_generic_catalog() -> None:
    source, document = _document([("BROKEN;", "ERROR", {"message": "syntax error"})])
    document = dataclasses.replace(
        document,
        recovered=True,
        issues=(
            soleaux.postgresql.node_runtime.ParserIssue(
                message="syntax error",
                byte_start=0,
                byte_end=len(source.encode("utf-8")),
            ),
        ),
    )
    extraction = soleaux.catalog.postgresql.extract_postgresql_catalog(
        source,
        document,
        _context(source),
    )

    merged = soleaux.catalog.postgresql.merge_postgresql_catalog(
        CatalogFacts(),
        workspace_id="main",
        sources={extraction.context.path: source.encode("utf-8")},
        extractions=(extraction,),
    )

    assert len(merged.diagnostics) == 1
    diagnostic = merged.diagnostics[0]
    assert diagnostic.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    assert diagnostic.category == "error"
    assert diagnostic.code == "parse_error"
    assert diagnostic.message == "syntax error"
    assert merged.engines[0].available is False
    assert merged.engines[0].capabilities == ()
