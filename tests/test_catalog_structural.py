"""Generation-time structural promotion: symbols, imports, exports, routes."""

from __future__ import annotations

import pathlib
import typing

import soleaux.analysis.service
import soleaux.catalog.contracts
import soleaux.catalog.structural
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.structural.fragments

_DIGEST = "a" * 64


class _Evidence(typing.TypedDict):
    workspace_id: str
    source_path: str
    source_digest: str
    producer: str
    producer_version: str


def _evidence(source_path: str = "package.json", source_digest: str = _DIGEST) -> _Evidence:
    return {
        "workspace_id": "main",
        "source_path": source_path,
        "source_digest": source_digest,
        "producer": "test",
        "producer_version": "1",
    }


def _fragment(
    projection: str,
    *,
    kind: str,
    name: str | None,
    path: str,
    byte_start: int,
    byte_end: int,
    attributes: dict[str, typing.Any] | None = None,
) -> soleaux.structural.fragments.SyntaxFragment:
    return soleaux.structural.fragments.SyntaxFragment(
        projection=projection,
        kind=kind,
        name=name,
        path=path,
        language="Tsx",
        byte_start=byte_start,
        byte_end=byte_end,
        start_line=0,
        start_column=0,
        end_line=0,
        end_column=0,
        attributes=attributes or {},
    )


def _project(project_id: str, root_path: str) -> soleaux.catalog.contracts.ProjectFact:
    return soleaux.catalog.contracts.ProjectFact(
        **_evidence(),
        project_id=project_id,
        root_path=root_path,
        manifest_path=f"{root_path}/package.json" if root_path else "package.json",
        kind=soleaux.catalog.contracts.ProjectKind.NODE,
    )


def _route(source_path: str) -> soleaux.catalog.contracts.RouteFact:
    return soleaux.catalog.contracts.RouteFact(
        **_evidence(source_path=source_path),
        route_id="b" * 64,
        project_id="main:node:.",
        framework="nextjs",
        route="/api/health",
        registration_kind="app_route",
        confidence=1.0,
        complete=True,
    )


def test_project_id_for_path_prefers_the_longest_root() -> None:
    projects = (
        _project("main:node:.", ""),
        _project("main:node:packages/app", "packages/app"),
    )
    assert (
        soleaux.catalog.structural.project_id_for_path(projects, "packages/app/src/index.ts")
        == "main:node:packages/app"
    )
    assert (
        soleaux.catalog.structural.project_id_for_path(projects, "scripts/tool.ts") == "main:node:."
    )
    assert soleaux.catalog.structural.project_id_for_path((), "scripts/tool.ts") == ""


def test_merge_promotes_symbols_imports_and_route_evidence() -> None:
    path = "app/api/health/route.ts"
    facts = soleaux.catalog.contracts.CatalogFacts(
        projects=(_project("main:node:.", ""),), routes=(_route(path),)
    )
    fragments = (
        _fragment(
            "syntax.imports",
            kind="import",
            name="next/server",
            path=path,
            byte_start=0,
            byte_end=30,
        ),
        _fragment(
            "syntax.exports", kind="export", name="GET", path=path, byte_start=32, byte_end=90
        ),
        _fragment(
            "syntax.declarations",
            kind="function",
            name="GET",
            path=path,
            byte_start=39,
            byte_end=90,
        ),
        _fragment(
            "syntax.exports", kind="export", name="POST", path=path, byte_start=92, byte_end=160
        ),
        _fragment(
            "syntax.declarations",
            kind="function",
            name="POST",
            path=path,
            byte_start=99,
            byte_end=160,
        ),
        _fragment(
            "syntax.exports",
            kind="export",
            name="runtime",
            path=path,
            byte_start=162,
            byte_end=200,
            attributes={"initializer_text": '"edge"'},
        ),
        _fragment(
            "syntax.declarations",
            kind="variable",
            name="runtime",
            path=path,
            byte_start=175,
            byte_end=199,
        ),
    )
    merged = soleaux.catalog.structural.merge_structural_facts(
        facts,
        workspace_id="main",
        extracted={
            path: soleaux.catalog.structural.ExtractedFile(
                digest=_DIGEST, language="Tsx", fragments=fragments
            )
        },
    )

    symbols = {symbol.name: symbol for symbol in merged.symbols}
    assert set(symbols) == {"GET", "POST", "runtime"}
    assert all(symbol.coverage == "syntactic" for symbol in symbols.values())
    assert all(
        symbol.engine_id == soleaux.structural.fragments.AST_GREP_ANALYZER_ID
        for symbol in symbols.values()
    )
    assert all(symbol.exported for symbol in symbols.values())
    assert symbols["GET"].project_id == "main:node:."

    assert [imported.specifier for imported in merged.imports] == ["next/server"]
    assert merged.imports[0].engine_id == soleaux.structural.fragments.AST_GREP_ANALYZER_ID

    route = merged.routes[0]
    assert route.methods == ("GET", "POST")
    assert route.runtime == "edge"
    assert route.complete is True


def test_merge_replaces_stale_structural_facts_and_defers_to_semantic() -> None:
    path = "app/api/health/route.ts"
    stale = soleaux.catalog.contracts.SymbolFact(
        **_evidence(source_path=path, source_digest="c" * 64),
        symbol_id="d" * 64,
        revision_id="e" * 64,
        project_id="main:node:.",
        path=path,
        name="OLD",
        symbol_kind="function",
        byte_start=0,
        byte_end=10,
        engine_id=soleaux.structural.fragments.AST_GREP_ANALYZER_ID,
        coverage="syntactic",
    )
    semantic = stale.model_copy(
        update={
            "symbol_id": "f" * 64,
            "name": "GET",
            "byte_start": 39,
            "byte_end": 90,
            "engine_id": "lsp:typescript-language-server",
            "coverage": "semantic",
        }
    )
    facts = soleaux.catalog.contracts.CatalogFacts(
        projects=(_project("main:node:.", ""),), symbols=(stale, semantic)
    )
    fragments = (
        _fragment(
            "syntax.declarations",
            kind="function",
            name="GET",
            path=path,
            byte_start=39,
            byte_end=90,
        ),
        _fragment(
            "syntax.declarations",
            kind="function",
            name="HEAD",
            path=path,
            byte_start=95,
            byte_end=140,
        ),
    )
    merged = soleaux.catalog.structural.merge_structural_facts(
        facts,
        workspace_id="main",
        extracted={
            path: soleaux.catalog.structural.ExtractedFile(
                digest=_DIGEST, language="Tsx", fragments=fragments
            )
        },
    )

    names = [(symbol.name, symbol.engine_id) for symbol in merged.symbols]
    assert ("OLD", soleaux.structural.fragments.AST_GREP_ANALYZER_ID) not in names
    assert ("GET", "lsp:typescript-language-server") in names
    assert ("GET", soleaux.structural.fragments.AST_GREP_ANALYZER_ID) not in names
    assert ("HEAD", soleaux.structural.fragments.AST_GREP_ANALYZER_ID) in names


async def test_catalog_bundle_promotes_route_methods_and_symbols(tmp_path: pathlib.Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "root", "dependencies": {"next": "16.0.0"}}\n',
        encoding="utf-8",
    )
    route_dir = tmp_path / "app" / "api" / "health"
    route_dir.mkdir(parents=True)
    (route_dir / "route.ts").write_text(
        'export function GET() {\n  return new Response("ok");\n}\n'
        'export async function POST() {\n  return new Response("ok");\n}\n'
        'export const runtime = "edge";\n',
        encoding="utf-8",
    )
    (tmp_path / "helper.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        routed = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="health", kinds=[soleaux.contracts.requests.SearchKind.ROUTE]
            )
        )
        assert routed.status is soleaux.contracts.results.ResultStatus.OK
        assert routed.rows
        assert routed.rows[0]["methods"] == ["GET", "POST"]
        assert routed.rows[0]["runtime"] == "edge"

        answers = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer", kinds=[soleaux.contracts.requests.SearchKind.SYMBOL]
            )
        )
        assert answers.status is soleaux.contracts.results.ResultStatus.OK
        assert answers.rows
        assert answers.rows[0]["symbol_kind"] == "function"

        exported = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="GET", kinds=[soleaux.contracts.requests.SearchKind.SYMBOL]
            )
        )
        assert exported.status is soleaux.contracts.results.ResultStatus.OK
        assert exported.rows
        assert any(row["exported"] is True for row in exported.rows)
        assert service.structural_worker_started is True

        jobs_after_first = service.structural_completed_jobs
        again = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="health", kinds=[soleaux.contracts.requests.SearchKind.ROUTE]
            )
        )
        assert again.status is soleaux.contracts.results.ResultStatus.OK
        assert again.rows is not None
        assert service.structural_completed_jobs == jobs_after_first
