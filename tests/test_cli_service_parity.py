"""CLI adapters serialize the same SoleauxService results."""

from __future__ import annotations

import io
import json
import pathlib
from collections.abc import Mapping
from typing import cast

import pytest
from _assertions import object_mapping

import soleaux.analysis.service
import soleaux.cli
import soleaux.contracts.requests
import soleaux.postgresql.node_runtime
import soleaux.structural.rust_runtime
import soleaux.typescript.node_runtime


def _semantic_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized.pop("request_id", None)
    coverage = normalized.get("coverage")
    if isinstance(coverage, dict):
        normalized_coverage = object_mapping(cast(object, coverage))
        normalized_coverage.pop("deadline", None)
        normalized_coverage.pop("elapsed_ms", None)
        normalized["coverage"] = normalized_coverage
    return normalized


async def _cli_json(
    service: soleaux.analysis.service.SoleauxService,
    argv: list[str],
    *,
    expected_exit: int = 0,
) -> dict[str, object]:
    output = io.StringIO()
    exit_code = await soleaux.cli.run_cli(argv, service=service, stdout=output)
    assert exit_code == expected_exit
    parsed: object = json.loads(output.getvalue())
    return object_mapping(parsed)


async def test_doctor_cli_and_service_results_are_semantically_identical(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def needle() -> str:\n    return 'found'\n",
        encoding="utf-8",
    )
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        direct = await service.doctor(probe=False)
        via_cli = await _cli_json(service, ["doctor", "--json"])

    assert _semantic_payload(via_cli) == _semantic_payload(direct.model_dump(mode="json"))


async def test_describe_cli_does_not_start_catalog_lifecycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)

    async def unexpected_start() -> None:
        raise AssertionError("describe must not start catalog publication")

    monkeypatch.setattr(service, "start", unexpected_start)
    try:
        payload = await _cli_json(service, ["describe", "--json"])
    finally:
        await service.aclose()

    assert payload["status"] == "ok"
    assert service.structural_worker_started is False


async def test_analysis_cli_and_service_results_are_semantically_identical(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def needle() -> str:\n    return 'found'\n",
        encoding="utf-8",
    )
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        direct_describe = await service.describe(soleaux.contracts.requests.DescribeRequest())
        cli_describe = await _cli_json(service, ["describe", "--json"])

        direct_search = await service.search(
            soleaux.contracts.requests.SearchRequest(query="needle")
        )
        cli_search = await _cli_json(service, ["search", "needle", "--json"])

        direct_context = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="locate the needle implementation",
                paths=["main.py"],
            )
        )
        cli_context = await _cli_json(
            service,
            [
                "context",
                "locate the needle implementation",
                "--path",
                "main.py",
                "--json",
            ],
        )

        direct_query = await service.query(
            soleaux.contracts.requests.QueryRequest(include_tables=["repository.files"])
        )
        cli_query = await _cli_json(
            service,
            ["query", "--table", "repository.files", "--json"],
        )

        direct_navigate = await service.navigate(
            soleaux.contracts.requests.NavigateRequest(
                operation=soleaux.contracts.requests.SemanticOperation.DEFINITION,
                path="main.py",
                line=1,
                column=5,
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )
        cli_navigate = await _cli_json(
            service,
            [
                "navigate",
                "definition",
                "--path",
                "main.py",
                "--line",
                "1",
                "--column",
                "5",
                "--semantic-mode",
                "syntax_only",
                "--json",
            ],
        )

        direct_inspect = await service.inspect(
            soleaux.contracts.requests.InspectRequest(
                operation=soleaux.contracts.requests.InspectOperation.DIAGNOSTICS,
                path="main.py",
                line=1,
                column=1,
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )
        cli_inspect = await _cli_json(
            service,
            [
                "inspect",
                "diagnostics",
                "main.py",
                "--line",
                "1",
                "--column",
                "1",
                "--semantic-mode",
                "syntax_only",
                "--json",
            ],
        )

    for direct, via_cli in (
        (direct_describe, cli_describe),
        (direct_search, cli_search),
        (direct_context, cli_context),
        (direct_query, cli_query),
        (direct_navigate, cli_navigate),
        (direct_inspect, cli_inspect),
    ):
        assert _semantic_payload(via_cli) == _semantic_payload(direct.model_dump(mode="json"))


async def test_lint_cli_matches_the_service_and_signals_findings(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text(
        '[structural]\nproject_config = "sgconfig.yml"\n',
        encoding="utf-8",
    )
    (tmp_path / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "no-console.yml").write_text(
        (
            "id: no-console\n"
            "language: TypeScript\n"
            "severity: warning\n"
            "message: avoid console output\n"
            "rule:\n"
            "  pattern: console.log($A)\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text('console.log("hit");\n', encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        direct = await service.lint(soleaux.contracts.requests.LintRequest())
        via_cli = await _cli_json(service, ["lint"], expected_exit=1)

    assert direct.rows
    assert _semantic_payload(via_cli) == _semantic_payload(direct.model_dump(mode="json"))


async def test_lint_cli_returns_zero_when_no_findings_remain(tmp_path: pathlib.Path) -> None:
    (tmp_path / "soleaux.toml").write_text(
        '[structural]\nproject_config = "sgconfig.yml"\n',
        encoding="utf-8",
    )
    (tmp_path / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "no-console.yml").write_text(
        (
            "id: no-console\n"
            "language: TypeScript\n"
            "severity: warning\n"
            "message: avoid console output\n"
            "rule:\n"
            "  pattern: console.log($A)\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text('debug.trace("clean");\n', encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        payload = await _cli_json(service, ["lint"])

    assert payload["rows"] in (None, [])


async def test_cli_installs_exact_managed_runtimes_without_starting_service(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typescript_prefix = tmp_path / "typescript"
    parser_prefix = tmp_path / "postgresql"
    rust_binary = tmp_path / "rust" / "soleaux-ast-grep-worker"
    calls: list[str] = []

    def install_typescript() -> soleaux.typescript.node_runtime.TypeScriptRuntimeInstallation:
        calls.append("typescript-runtime")
        return soleaux.typescript.node_runtime.TypeScriptRuntimeInstallation(
            prefix=typescript_prefix,
            node_executable="/installed/node",
            ts_morph_version="28.0.0",
            native_version="7.0.2",
        )

    def install_parser() -> soleaux.postgresql.node_runtime.ParserInstallation:
        calls.append("postgresql-parser")
        return soleaux.postgresql.node_runtime.ParserInstallation(
            prefix=parser_prefix,
            package_json=parser_prefix / "node_modules/@libpg-query/parser/package.json",
            version="17.6.10",
        )

    def install_rust_worker() -> soleaux.structural.rust_runtime.RustWorkerInstallation:
        calls.append("ast-grep-rust")
        return soleaux.structural.rust_runtime.RustWorkerInstallation(
            version="0.44.1", binary_path=rust_binary
        )

    monkeypatch.setattr(
        "soleaux.typescript.node_runtime.provision_typescript_runtime",
        install_typescript,
    )
    monkeypatch.setattr(
        "soleaux.postgresql.node_runtime.provision_parser",
        install_parser,
    )
    monkeypatch.setattr(
        "soleaux.structural.rust_runtime.provision_rust_worker",
        install_rust_worker,
    )

    typescript_output = io.StringIO()
    parser_output = io.StringIO()
    rust_output = io.StringIO()
    assert (
        await soleaux.cli.run_cli(
            ["--root", str(tmp_path), "install", "typescript-runtime"],
            stdout=typescript_output,
        )
        == 0
    )
    assert (
        await soleaux.cli.run_cli(
            ["--root", str(tmp_path), "install", "postgresql-parser"],
            stdout=parser_output,
        )
        == 0
    )
    assert (
        await soleaux.cli.run_cli(
            ["--root", str(tmp_path), "install", "ast-grep-rust"],
            stdout=rust_output,
        )
        == 0
    )

    assert calls == ["typescript-runtime", "postgresql-parser", "ast-grep-rust"]
    assert typescript_output.getvalue() == (
        f"[OK] typescript-runtime: ts-morph 28.0.0, native TypeScript 7.0.2 "
        f"at {typescript_prefix}\n"
    )
    assert parser_output.getvalue() == (f"[OK] postgresql-parser: 17.6.10 at {parser_prefix}\n")
    assert rust_output.getvalue() == (f"[OK] ast-grep-rust: 0.44.1 at {rust_binary}\n")
