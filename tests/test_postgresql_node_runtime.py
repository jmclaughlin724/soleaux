"""PG-E: managed PostgreSQL Node parser, adapter positions, and lifecycle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable
from unittest.mock import patch

import _assertions
import _host_root
import pytest

import soleaux.postgresql.node_runtime as node_runtime
from soleaux.analysis.service import SoleauxService
from soleaux.postgresql.analyzer import (
    PostgreSqlLocationState,
    analyze_postgresql,
    build_postgresql_root,
    postgresql_root,
)
from soleaux.postgresql.node_runtime import (
    MANAGED_PREFIX_ENV,
    PARSER_PACKAGE,
    PARSER_VERSION,
    NodeParserDeadlineError,
    NodeParserProvisionError,
    NodeParserRuntime,
    NodeParserUnavailableError,
    ParserDocument,
    ParserInstallation,
    ScanToken,
    managed_parser_version,
    provision_parser,
    resolve_parser_installation,
)
from soleaux.postgresql.runtime import build_safe_environment
from soleaux.structural.projections import AnalyzerNode
from soleaux.structural.supervisor import StructuralWorkerSupervisor

REPOSITORY_ROOT = _host_root.require_host_root()
INSTALLED_PARSER = REPOSITORY_ROOT / "node_modules" / "@libpg-query" / "parser"


class _PostgreSqlPosition(Protocol):
    index: int
    line: int
    column: int
    utf16_column: int


class _PostgreSqlRange(Protocol):
    start: _PostgreSqlPosition
    end: _PostgreSqlPosition


@runtime_checkable
class _PostgreSqlNode(AnalyzerNode, Protocol):
    native_byte_offset: int | None

    def range(self) -> _PostgreSqlRange: ...


def _managed_prefix(tmp_path: Path, *, real_parser: bool = False) -> Path:
    prefix = tmp_path / "managed"
    prefix.mkdir()
    (prefix / "package.json").write_text(
        '{"name":"soleaux-postgresql-parser-runtime","private":true}\n',
        encoding="utf-8",
    )
    package = prefix / "node_modules" / "@libpg-query" / "parser"
    package.parent.mkdir(parents=True)
    if real_parser:
        if not INSTALLED_PARSER.is_dir():
            pytest.skip("the pinned @libpg-query/parser package is not installed")
        package.symlink_to(INSTALLED_PARSER.resolve(), target_is_directory=True)
    else:
        package.mkdir()
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": PARSER_PACKAGE,
                    "version": PARSER_VERSION,
                    "main": "index.cjs",
                }
            ),
            encoding="utf-8",
        )
        (package / "index.cjs").write_text(
            """
class SqlError extends Error {
  constructor(message, cursorPosition) {
    super(message);
    this.sqlDetails = { cursorPosition };
  }
}
module.exports = {
  SqlError,
  hasSqlDetails(error) { return error instanceof SqlError; },
  async loadModule() {},
  scanSync(source) {
    if (source === "HANG") {
      const end = Date.now() + 60000;
      while (Date.now() < end) {}
    }
    return { version: 170004, tokens: [] };
  },
  parseSync(source) {
    if (source === "INVALID") throw new SqlError("invalid SQL", 3);
    return { version: 170004, stmts: [] };
  },
};
""".strip()
            + "\n",
            encoding="utf-8",
        )
    return prefix


def _installation(prefix: Path) -> ParserInstallation:
    installation = resolve_parser_installation(prefix)
    assert installation is not None
    return installation


def _walk(root: AnalyzerNode) -> list[AnalyzerNode]:
    nodes: list[AnalyzerNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(reversed(node.children()))
    return nodes


def _data(value: object) -> dict[str, object]:
    return _assertions.object_mapping(value)


def _postgresql_node(node: AnalyzerNode) -> _PostgreSqlNode:
    assert isinstance(node, _PostgreSqlNode)
    return node


def test_discovery_is_read_only_and_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = tmp_path / "managed"
    monkeypatch.setenv(MANAGED_PREFIX_ENV, str(prefix))
    assert resolve_parser_installation() is None
    assert managed_parser_version() == "unavailable"
    assert not prefix.exists()

    _managed_prefix(tmp_path)
    installation = resolve_parser_installation()
    assert installation is not None
    assert installation.prefix == prefix
    assert installation.version == PARSER_VERSION
    assert managed_parser_version() == PARSER_VERSION


def test_provisioning_is_explicit_exact_and_mocked(
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "managed"
    completed = subprocess.CompletedProcess(args=(), returncode=0, stdout="", stderr="")

    def install(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        package = prefix / "node_modules" / "@libpg-query" / "parser"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": PARSER_PACKAGE, "version": PARSER_VERSION}),
            encoding="utf-8",
        )
        return completed

    with (
        patch("soleaux.postgresql.node_runtime.shutil.which", return_value="/mock/npm"),
        patch("soleaux.postgresql.node_runtime.subprocess.run", side_effect=install) as run,
    ):
        installation = provision_parser(prefix)

    assert installation.version == PARSER_VERSION
    run.assert_called_once_with(
        (
            "/mock/npm",
            "install",
            "--prefix",
            str(prefix),
            "--save-exact",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            f"{PARSER_PACKAGE}@{PARSER_VERSION}",
        ),
        cwd=prefix,
        env=build_safe_environment({}, environment_names=()),
        capture_output=True,
        text=True,
        timeout=120.0,
        check=False,
    )


def test_provisioning_reports_offline_failure_without_echoing_output(
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "managed"
    secret_output = "registry token=do-not-echo"
    completed = subprocess.CompletedProcess(
        args=(),
        returncode=1,
        stdout="",
        stderr=secret_output,
    )

    with (
        patch("soleaux.postgresql.node_runtime.shutil.which", return_value="/mock/npm"),
        patch("soleaux.postgresql.node_runtime.subprocess.run", return_value=completed),
        pytest.raises(NodeParserProvisionError) as excinfo,
    ):
        provision_parser(prefix)

    assert "exit 1" in str(excinfo.value)
    assert secret_output not in str(excinfo.value)


def test_real_parser_runs_from_an_isolated_managed_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.delenv("NODE_PATH", raising=False)
    runtime = NodeParserRuntime(_installation(prefix))
    try:
        document = runtime.analyze("SELECT '🐘';\nSELECT 2;")
        pid = runtime.pid
        assert pid is not None
        assert document.parser_version == PARSER_VERSION
        assert document.offset_unit == "utf8_byte"
        assert [token.token_type for token in document.tokens if token.text == ";"] == [59, 59]
        assert document.tokens[1].text == "'🐘'"
        assert document.tokens[1].end - document.tokens[1].start == 6
    finally:
        runtime.close()
    assert runtime.started is False
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_existing_supervised_worker_uses_the_managed_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    monkeypatch.setenv(MANAGED_PREFIX_ENV, str(prefix))
    supervisor = StructuralWorkerSupervisor()
    try:
        result = await supervisor.extract(
            language="PostgreSQL",
            path="schema.sql",
            content=b"SELECT 1; SELECT 2;",
            projections=("syntax.spans",),
        )
        assert result.parses == 1
        assert result.unsupported == ()
        assert [fragment.kind for fragment in result.fragments] == [
            "file",
            "SelectStmt",
            "SelectStmt",
        ]

        recovered = await supervisor.extract(
            language="PostgreSQL",
            path="broken.sql",
            content="SELECT 🐘 FROM ;".encode(),
            projections=("syntax.spans",),
        )
        assert [fragment.kind for fragment in recovered.fragments] == ["file", "ERROR"]
        assert len(recovered.diagnostics) == 1
        assert recovered.diagnostics[0].message == (
            "syntax error; partial structural facts returned"
        )
    finally:
        await supervisor.aclose()
    assert supervisor.started is False


def test_adapter_uses_scanner_statement_ranges_and_utf16_columns(tmp_path: Path) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    source = "SELECT '🐘';\nSELECT * FROM café;"
    runtime = NodeParserRuntime(_installation(prefix))
    try:
        root = postgresql_root(source, "PostgreSQL", runtime)
    finally:
        runtime.close()

    statements = list(root.children())
    assert [statement.text() for statement in statements] == ["SELECT '🐘';", "SELECT * FROM café;"]
    second_start = _postgresql_node(statements[1]).range().start
    assert second_start.index == len("SELECT '🐘';\n".encode())
    assert second_start.line == 1
    assert second_start.column == 0
    assert second_start.utf16_column == 0

    range_var = _postgresql_node(next(node for node in _walk(root) if node.kind() == "RangeVar"))
    expected_byte = len("SELECT '🐘';\nSELECT * FROM ".encode())
    assert range_var.native_byte_offset == expected_byte
    assert range_var.range().start.index == expected_byte
    assert range_var.range().start.column == len("SELECT * FROM ")
    assert range_var.range().start.utf16_column == len("SELECT * FROM ")


def test_error_cursor_is_code_points_not_utf8_bytes(tmp_path: Path) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    runtime = NodeParserRuntime(_installation(prefix))
    try:
        analysis = analyze_postgresql("SELECT 🐘 FROM ;", "PostgreSQL", runtime)
    finally:
        runtime.close()

    assert [node.kind() for node in analysis.root.children()] == ["ERROR"]
    assert analysis.document.recovered is True
    assert len(analysis.document.issues) == 1
    assert analysis.document.issues[0].byte_start == 17


def test_scanner_recovery_retains_valid_statements_around_a_parse_error(
    tmp_path: Path,
) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    runtime = NodeParserRuntime(_installation(prefix))
    try:
        analysis = analyze_postgresql(
            "SELECT 1; SELCT broken; SELECT 2;",
            "PostgreSQL",
            runtime,
        )
    finally:
        runtime.close()

    assert analysis.document.recovered is True
    assert len(analysis.document.issues) == 1
    assert [node.kind() for node in analysis.root.children()] == [
        "SelectStmt",
        "ERROR",
        "SelectStmt",
    ]
    assert [node.text() for node in analysis.root.children()] == [
        "SELECT 1;",
        "SELCT broken;",
        "SELECT 2;",
    ]


def test_plpgsql_queries_preserve_do_block_and_function_statement_context(
    tmp_path: Path,
) -> None:
    prefix = _managed_prefix(tmp_path, real_parser=True)
    source = (
        "DO $$BEGIN PERFORM app.one(); END$$;\n"
        "CREATE FUNCTION app.f() RETURNS void LANGUAGE plpgsql "
        "AS $$BEGIN PERFORM app.two(); END$$;"
    )
    runtime = NodeParserRuntime(_installation(prefix))
    try:
        document = runtime.analyze(source)
    finally:
        runtime.close()

    assert [query.line for query in document.embedded_queries] == [0, 1]
    assert all(
        query.dynamic is False and query.parse_tree is not None
        for query in document.embedded_queries
    )


def test_absent_zero_and_unknown_locations_remain_distinct() -> None:
    source = "abc"
    parse_tree: dict[str, object] = {
        "version": 170004,
        "stmts": [
            {"stmt": {"AbsentNode": {}}},
            {"stmt": {"ZeroNode": {"location": 0}}},
            {"stmt": {"UnknownNode": {"location": -1}}},
        ],
    }
    document = ParserDocument(
        parse_tree=parse_tree,
        tokens=(
            ScanToken(0, 1, "a", 258, "IDENT"),
            ScanToken(1, 2, ";", 59, "ASCII_59"),
            ScanToken(2, 3, "b", 258, "IDENT"),
        ),
        parser_version=PARSER_VERSION,
        postgresql_version=170004,
    )
    root = build_postgresql_root(source, document)
    nodes = list(root.children())
    assert nodes[0].location_state is PostgreSqlLocationState.ABSENT
    assert nodes[0].native_byte_offset is None
    assert nodes[1].location_state is PostgreSqlLocationState.KNOWN
    assert nodes[1].native_byte_offset == 0
    assert nodes[2].location_state is PostgreSqlLocationState.UNKNOWN_SENTINEL
    assert nodes[2].native_byte_offset is None


def test_deadline_kills_and_reaps_the_node_child(tmp_path: Path) -> None:
    prefix = _managed_prefix(tmp_path)
    runtime = NodeParserRuntime(
        _installation(prefix),
        deadline_seconds=0.05,
        shutdown_grace_seconds=0.2,
    )
    with pytest.raises(NodeParserDeadlineError):
        runtime.analyze("HANG")
    assert runtime.started is False
    runtime.close()


def test_packaged_worker_resource_is_present() -> None:
    from importlib.resources import files

    resource = (
        files("soleaux").joinpath("resources").joinpath("postgresql").joinpath("node_worker.cjs")
    )
    text = resource.read_text(encoding="utf-8")
    assert 'createRequire(join(managedPrefix, "package.json"))' in text
    assert 'PARSER_VERSION = "17.6.10"' in text
    assert ".split(" not in text


async def test_doctor_reports_the_managed_node_parser_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = _managed_prefix(tmp_path)
    monkeypatch.setenv(MANAGED_PREFIX_ENV, str(prefix))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SoleauxService.from_root(workspace)
    try:
        response = await service.doctor()
    finally:
        await service.aclose()

    data = _data(response.data)
    probe = _data(data["probe"])
    assert probe["postgresql_parser_version"] == PARSER_VERSION


def test_runtime_ignores_node_path_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "missing"
    monkeypatch.setenv(MANAGED_PREFIX_ENV, str(prefix))
    monkeypatch.setenv("NODE_PATH", str(REPOSITORY_ROOT / "node_modules"))
    assert resolve_parser_installation() is None
    assert not prefix.exists()


def test_parser_package_fixture_is_not_copied_from_repository(tmp_path: Path) -> None:
    prefix = _managed_prefix(tmp_path)
    package = prefix / "node_modules" / "@libpg-query" / "parser"
    assert not package.is_symlink()
    assert package.resolve() != INSTALLED_PARSER.resolve()
    assert shutil.which("node") is not None


def test_node_floor_rejects_unsupported_node(tmp_path: Path) -> None:
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\necho v20.19.0\n", encoding="utf-8")
    fake_node.chmod(0o755)

    with pytest.raises(NodeParserUnavailableError) as excinfo:
        node_runtime._require_supported_node(str(fake_node))
    assert "requires Node.js >= 24" in str(excinfo.value)


def test_node_floor_rejects_unparseable_node_version(tmp_path: Path) -> None:
    fake_node = tmp_path / "node"
    fake_node.write_text("#!/bin/sh\necho not-a-version\n", encoding="utf-8")
    fake_node.chmod(0o755)

    with pytest.raises(NodeParserUnavailableError) as excinfo:
        node_runtime._require_supported_node(str(fake_node))
    assert "did not report a version" in str(excinfo.value)


def test_node_floor_accepts_supported_node() -> None:
    discovered = shutil.which("node")
    assert discovered is not None
    node_runtime._require_supported_node(discovered)
