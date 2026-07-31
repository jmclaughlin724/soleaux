"""D017/D021: zero-config AI workflow through the shared service."""

from __future__ import annotations

import pathlib

import pytest

import soleaux.analysis.frame
import soleaux.analysis.service
import soleaux.contracts.config
import soleaux.contracts.coverage
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.workspace
import soleaux.structural.snapshot


async def test_catalog_off_starts_service_and_returns_typed_disabled_read(
    tmp_path: pathlib.Path,
) -> None:
    config = soleaux.contracts.config.ResolvedConfig.default().model_copy(
        update={
            "catalog": soleaux.contracts.config.CatalogConfig(
                mode=soleaux.contracts.config.CatalogMode.OFF
            )
        }
    )

    async with soleaux.analysis.service.SoleauxService.from_root(
        tmp_path,
        config=config,
    ) as service:
        described = await service.describe(soleaux.contracts.requests.DescribeRequest())
        queried = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.files"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )

    assert described.status is soleaux.contracts.results.ResultStatus.OK
    assert queried.status is soleaux.contracts.results.ResultStatus.ERROR
    assert queried.error is not None
    assert queried.error.error_type == "catalog_disabled"
    assert queried.error.retryable is False


async def test_configured_quality_and_coverage_rows_are_lifecycle_queryable(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        (
            '[structural]\nproject_config = "sgconfig.yml"\n\n'
            "[[coverage.artifacts]]\n"
            'path = "ci/coverage.json"\n'
            'format = "soleaux_json"\n'
        ),
        encoding="utf-8",
    )
    (tmp_path / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n", encoding="utf-8")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "no-print.yml").write_text(
        (
            "id: no-print\n"
            "language: Python\n"
            "severity: warning\n"
            "message: avoid print\n"
            "rule:\n"
            "  pattern: print($A)\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("print('visible')\n", encoding="utf-8")
    coverage = tmp_path / "ci" / "coverage.json"
    coverage.parent.mkdir()
    coverage.write_text(
        (
            '{"schema_version":"soleaux.coverage/v1","run_id":"ci-42",'
            '"records":[{"subject":"main.py","metric":"line","value":1.0,'
            '"hits":1,"total":1}]}'
        ),
        encoding="utf-8",
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        response = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["quality.standards", "coverage"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=20,
            )
        )

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.rows is not None
    assert {row["table"] for row in response.rows} == {
        "coverage",
        "quality.standards",
    }
    standard = next(row for row in response.rows if row["table"] == "quality.standards")
    imported = next(row for row in response.rows if row["table"] == "coverage")
    assert standard["rule_id"] == "no-print"
    assert standard["path"] == "main.py"
    assert imported["subject"] == "main.py"
    assert imported["run_id"] == "ci-42"


async def test_search_and_context_read_the_lifecycle_catalog_without_lsp(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "from helper import answer\n\ndef run() -> int:\n    return answer()\n",
        encoding="utf-8",
    )
    (tmp_path / "helper.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        assert service.active_language_server_count == 0
        await service._catalog_indexer.settle()

        searched = await service.search(soleaux.contracts.requests.SearchRequest(query="answer"))
        assert searched.status is soleaux.contracts.results.ResultStatus.OK
        assert searched.rows
        assert {row["path"] for row in searched.rows} == {"helper.py", "main.py"}
        assert service.structural_worker_started is True
        assert service.active_language_server_count == 0

        symbols = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                kinds=[soleaux.contracts.requests.SearchKind.SYMBOL],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )
        assert symbols.status is soleaux.contracts.results.ResultStatus.OK
        assert symbols.rows
        assert {row["kind"] for row in symbols.rows} == {"symbol"}
        assert all(row["coverage"] == "syntactic" for row in symbols.rows)
        assert {row["path"] for row in symbols.rows} == {"helper.py"}
        assert {row["name"] for row in symbols.rows} == {"answer"}

        context = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="explain the implementation",
                paths=["helper.py"],
                max_bytes=8192,
            )
        )
        assert context.status is soleaux.contracts.results.ResultStatus.OK
        assert context.data is not None
        assert any(
            item.table == "source.context" and "return 42" in item.data["snippet"]
            for item in context.data.sources
        )

    assert service.closed is True
    assert service.structural_worker_started is False


def test_search_request_rejects_request_scoped_structural_execution() -> None:
    with pytest.raises(ValueError):
        soleaux.contracts.requests.SearchRequest.model_validate(
            {
                "query": "answer",
                "structural": {"kind": "rule_ref", "rule_id": "call-py"},
            }
        )


async def test_context_preserves_evidence_from_each_explicit_scope(
    tmp_path: pathlib.Path,
) -> None:
    for directory in ("first", "second"):
        scoped = tmp_path / directory
        scoped.mkdir()
        (scoped / "owner.md").write_text(
            f"# {directory}\n\nshared-policy\n",
            encoding="utf-8",
        )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="review shared-policy ownership",
                paths=["first", "second"],
                max_bytes=8192,
                limit=4,
            )
        )

    assert response.data is not None
    assert {item.path for item in response.data.sources} == {
        "first/owner.md",
        "second/owner.md",
    }


async def test_context_uses_any_token_ranked_retrieval(tmp_path: pathlib.Path) -> None:
    (tmp_path / "alpha.md").write_text("alpha evidence\n", encoding="utf-8")
    (tmp_path / "beta.md").write_text("beta evidence\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        response = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="alpha beta",
                limit=10,
            )
        )

    assert response.data is not None
    assert response.data.retrieval_engine == "sqlite-fts5"
    assert {item.path for item in response.data.sources} == {
        "alpha.md",
        "beta.md",
    }


async def test_search_suggestions_stay_in_the_fixed_tool_vocabulary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )

    def reject_semantic_producer(
        _workspace: object,
    ) -> None:
        raise AssertionError("suggestions must not construct a semantic producer")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        monkeypatch.setattr(service._frames, "semantic_resolver", reject_semantic_producer)
        response = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            )
        )
        assert response.status is soleaux.contracts.results.ResultStatus.OK
        tools = [item.tool for item in response.suggested_next_requests]
        assert tools == ["context", "navigate"]
        context_suggestion, symbol_suggestion = response.suggested_next_requests
        assert context_suggestion.args["objective"] == "answer"
        assert context_suggestion.args["semantic_mode"] == "syntax_only"
        assert isinstance(context_suggestion.args["paths"], list)
        assert symbol_suggestion.args["operation"] == "definition"
        assert symbol_suggestion.args["path"] == "main.py"
        assert isinstance(symbol_suggestion.args["line"], int)
        assert isinstance(symbol_suggestion.args["column"], int)
        assert symbol_suggestion.args["semantic_mode"] == "semantic_required"
        assert service.structural_worker_started is True


async def test_semantic_required_search_never_starts_request_path_enrichment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        before = service._catalog_reader.search(
            service.workspace_ids[0],
            query="answer",
            kinds=(),
            path_prefixes=(),
            limit=20,
            offset=0,
        )

        def unexpected_request_work(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("search must only read the published SQLite generation")

        monkeypatch.setattr(
            service._catalog_indexer,
            "published_bundle",
            unexpected_request_work,
        )
        monkeypatch.setattr(
            service._frames,
            "semantic_resolver",
            unexpected_request_work,
        )
        monkeypatch.setattr(
            service._frames,
            "capture",
            unexpected_request_work,
        )
        monkeypatch.setattr(
            service._frames,
            "build",
            unexpected_request_work,
        )
        response = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
            )
        )
        after = service._catalog_reader.search(
            service.workspace_ids[0],
            query="answer",
            kinds=(),
            path_prefixes=(),
            limit=20,
            offset=0,
        )

        assert response.status is soleaux.contracts.results.ResultStatus.ERROR
        assert response.error is not None
        assert response.error.error_type == "semantic_unavailable"
        assert response.data is not None
        assert response.data["generation"] == before.generation
        assert response.data["published_semantic_mode"] == "syntax_only"
        assert service.active_language_server_count == 0
        assert (
            after.generation,
            after.snapshot_id,
            after.source_fingerprint,
        ) == (
            before.generation,
            before.snapshot_id,
            before.source_fingerprint,
        )
        assert tuple(item.row for item in after.rows) == tuple(item.row for item in before.rows)


async def test_catalog_tools_only_read_the_startup_publication(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "main.py").write_text("answer = 42\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        workspace_id = service.workspace_ids[0]
        before = service._catalog_reader.tables(
            workspace_id,
            include_tables=("repository.files",),
            limit=20,
            offset=0,
        )

        def unexpected_request_work(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(
                "catalog tools must only read the lifecycle-published SQLite generation"
            )

        for owner, attribute in (
            (service._catalog_indexer, "published_bundle"),
            (service._catalog_indexer, "refresh"),
            (service._catalog_indexer, "wait_for_tables"),
            (service._frames, "base_catalog_bundle"),
            (service._frames, "build"),
            (service._frames, "capture"),
            (service._frames, "catalog_bundle"),
            (service._frames, "semantic_resolver"),
            (service._frames, "structural_engines"),
        ):
            monkeypatch.setattr(owner, attribute, unexpected_request_work)

        searched = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
            )
        )
        contextualized = await service.context(
            soleaux.contracts.requests.ContextRequest(objective="find answer")
        )
        strict_context = await service.context(
            soleaux.contracts.requests.ContextRequest(
                objective="find answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
            )
        )
        queried = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.files"],
            )
        )
        strict_query = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.files"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
            )
        )
        owned = await service.ownership(
            soleaux.contracts.requests.OwnershipRequest(policy="missing-policy")
        )
        strict_ownership = await service.ownership(
            soleaux.contracts.requests.OwnershipRequest(
                policy="missing-policy",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
            )
        )
        after = service._catalog_reader.tables(
            workspace_id,
            include_tables=("repository.files",),
            limit=20,
            offset=0,
        )

        assert searched.status is soleaux.contracts.results.ResultStatus.ERROR
        assert searched.error is not None
        assert searched.error.error_type == "semantic_unavailable"
        assert contextualized.status is soleaux.contracts.results.ResultStatus.OK
        assert queried.status is soleaux.contracts.results.ResultStatus.OK
        assert owned.status is soleaux.contracts.results.ResultStatus.OK
        for strict_response in (strict_context, strict_query, strict_ownership):
            assert strict_response.status is soleaux.contracts.results.ResultStatus.ERROR
            assert strict_response.error is not None
            assert strict_response.error.error_type == "semantic_unavailable"
        assert service.active_language_server_count == 0
        assert service.structural_worker_started is False
        assert (
            after.generation,
            after.publication_revision,
            after.snapshot_id,
            after.source_fingerprint,
        ) == (
            before.generation,
            before.publication_revision,
            before.snapshot_id,
            before.source_fingerprint,
        )


async def test_search_cursor_uses_one_published_ranked_generation(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "main.py"
    source.write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
            )
        )
        assert first.next_cursor is not None
        second = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="answer",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert first.status is soleaux.contracts.results.ResultStatus.OK
    assert second.status is soleaux.contracts.results.ResultStatus.OK
    assert first.data is not None
    assert second.data is not None
    assert first.data["generation"] == second.data["generation"]
    assert first.rows is not None
    assert second.rows is not None
    assert {str(row["key"]) for row in first.rows}.isdisjoint(
        str(row["key"]) for row in second.rows
    )


async def test_search_cursor_resumes_without_replaying_rows_and_bounds_warnings(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "a.py").write_text("needle one\nneedle two\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle three\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("needle four\n", encoding="utf-8")
    for index in range(25):
        (tmp_path / f"binary-{index:02}.bin").write_bytes(b"\x00")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=2,
            )
        )
        assert first.next_cursor is not None
        second = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=2,
                cursor=first.next_cursor,
            )
        )

    assert first.rows is not None
    assert second.rows is not None
    first_keys = {str(row["key"]) for row in first.rows}
    second_keys = {str(row["key"]) for row in second.rows}
    assert len(first.rows) == 2
    assert second_keys
    assert first_keys.isdisjoint(second_keys)
    assert first.coverage is not None
    assert first.coverage.status is soleaux.contracts.coverage.FrameStatus.TRUNCATED
    assert "search row limit reached" in first.coverage.omitted_reasons
    for response in (first, second):
        assert len(response.warnings) <= soleaux.analysis.service.MAX_RESPONSE_WARNINGS
        assert all(
            len(warning) <= soleaux.analysis.service.MAX_WARNING_CHARS
            for warning in response.warnings
        )


async def test_search_cursor_binds_arguments_and_rejects_snapshot_drift(
    tmp_path: pathlib.Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    source = first_root / "main.py"
    source.write_text('needle = "one"\nsecond = "needle two"\n', encoding="utf-8")
    (first_root / "extra.py").write_text('extra = "needle extra"\n', encoding="utf-8")
    (second_root / "main.py").write_text('other = "needle other"\n', encoding="utf-8")

    service = soleaux.analysis.service.SoleauxService.from_launch(
        [("first", first_root), ("second", second_root)],
        cursor_ttl_seconds=60,
    )
    async with service:
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="needle",
                limit=1,
            )
        )
        assert first.next_cursor is not None

        mismatched_requests = (
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="other",
                limit=1,
                cursor=first.next_cursor,
            ),
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="needle",
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
                cursor=first.next_cursor,
            ),
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="needle",
                limit=2,
                cursor=first.next_cursor,
            ),
            soleaux.contracts.requests.SearchRequest(
                workspace_id="second",
                query="needle",
                limit=1,
                cursor=first.next_cursor,
            ),
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="needle",
                kinds=[soleaux.contracts.requests.SearchKind.SYMBOL],
                limit=1,
                cursor=first.next_cursor,
            ),
        )
        for mismatched_request in mismatched_requests:
            mismatched = await service.search(mismatched_request)
            assert mismatched.status is soleaux.contracts.results.ResultStatus.ERROR
            assert mismatched.error is not None
            assert mismatched.error.error_type == "invalid_cursor"

        source.write_text('needle = "changed"\nsecond = "needle two"\n', encoding="utf-8")
        await service._catalog_indexer.refresh(
            service._workspaces.get("first"),
            force=True,
        )
        drifted = await service.search(
            soleaux.contracts.requests.SearchRequest(
                workspace_id="first",
                query="needle",
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert drifted.status is soleaux.contracts.results.ResultStatus.ERROR
    assert drifted.error is not None
    assert drifted.error.error_type == "cursor_drift"


async def test_search_and_query_cursors_reject_same_snapshot_new_catalog_generation(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "a.py").write_text("needle one\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle two\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        searched = await service.search(
            soleaux.contracts.requests.SearchRequest(query="needle", limit=1)
        )
        queried = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.files"],
                limit=1,
            )
        )
        assert searched.next_cursor is not None
        assert queried.next_cursor is not None
        assert searched.snapshot_id == queried.snapshot_id

        store = service._frames.existing_catalog_store(service.workspace_ids[0])
        assert store is not None
        active = store.read_materialized(
            service.workspace_ids[0],
            limit=2_147_483_647,
        )
        materialized_rows = tuple(item.row for item in active.rows)
        store.publish_materialized(
            active.frame,
            generation=active.generation + 1,
            source_fingerprint=active.source_fingerprint,
            rows=materialized_rows,
            kinds={item.row.evidence.evidence_id: item.kind for item in active.rows},
            relationships=(),
            retained_generations=2,
        )
        search_drift = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                limit=1,
                cursor=searched.next_cursor,
            )
        )
        query_drift = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.files"],
                limit=1,
                cursor=queried.next_cursor,
            )
        )

    for response in (search_drift, query_drift):
        assert response.snapshot_id is None
        assert response.status is soleaux.contracts.results.ResultStatus.ERROR
        assert response.error is not None
        assert response.error.error_type == "cursor_drift"


async def test_query_paginates_beyond_materialization_page_with_exact_total(
    tmp_path: pathlib.Path,
) -> None:
    expected_paths = {f"record-{index:04}.txt" for index in range(1001)}
    for path in expected_paths:
        (tmp_path / path).write_text(f"{path}\n", encoding="utf-8")

    seen_paths: set[str] = set()
    cursor: str | None = None
    page_count = 0
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        while True:
            response = await service.query(
                soleaux.contracts.requests.QueryRequest(
                    include_tables=["repository.files"],
                    semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                    limit=200,
                    cursor=cursor,
                )
            )
            assert response.status is soleaux.contracts.results.ResultStatus.OK
            assert response.data is not None
            assert response.data["total_rows"] == len(expected_paths)
            assert response.rows is not None
            page_paths = {str(row["path"]) for row in response.rows}
            assert seen_paths.isdisjoint(page_paths)
            seen_paths.update(page_paths)
            page_count += 1
            cursor = response.next_cursor
            assert response.coverage is not None
            if cursor is None:
                assert response.coverage.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
                break
            assert response.coverage.status is soleaux.contracts.coverage.FrameStatus.TRUNCATED
            assert "query response row limit reached" in response.coverage.omitted_reasons

    assert page_count == 6
    assert seen_paths == expected_paths


async def test_seeded_non_typescript_query_cursor_uses_published_generation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"fixture","scripts":{"alpha":"echo alpha","beta":"echo beta"}}',
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text("export const value = 1;\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()

        async def unexpected_enrichment(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("query must not enrich the catalog on the request path")

        monkeypatch.setattr(
            service._frames,
            "enrich_typescript_catalog",
            unexpected_enrichment,
        )
        project_id = f"{service.workspace_ids[0]}:node:."
        first = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.scripts"],
                seed_keys=[f"project:{project_id}"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
            )
        )
        assert first.next_cursor is not None
        second = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=["repository.scripts"],
                seed_keys=[f"project:{project_id}"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert first.status is soleaux.contracts.results.ResultStatus.OK
    assert second.status is soleaux.contracts.results.ResultStatus.OK
    assert first.rows is not None
    assert second.rows is not None
    assert {
        str(first.rows[0]["name"]),
        str(second.rows[0]["name"]),
    } == {"alpha", "beta"}


async def test_capture_reconciles_only_dirty_bytes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder()
    await builder.capture(workspace)

    original = pathlib.Path.read_bytes
    reads: list[str] = []

    def tracked(path: pathlib.Path) -> bytes:
        reads.append(path.relative_to(tmp_path).as_posix())
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", tracked)
    first.write_text("first = 2\n", encoding="utf-8")
    reconciled = await builder.capture(workspace, validate=True)

    assert reads == ["first.py"]
    assert reconciled.contents["first.py"] == b"first = 2\n"
    assert reconciled.contents["second.py"] == b"second = 1\n"
    await builder.aclose()


async def test_capture_uses_dirty_hint_without_repository_inventory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder()
    await builder.capture(workspace)
    original = pathlib.Path.read_bytes
    reads: list[str] = []

    def tracked(path: pathlib.Path) -> bytes:
        reads.append(path.relative_to(tmp_path).as_posix())
        return original(path)

    async def reject_inventory(
        _snapshotter: soleaux.structural.snapshot.RepositorySnapshotter,
    ) -> tuple[str, ...]:
        raise AssertionError("dirty hints must not run a repository inventory")

    monkeypatch.setattr(pathlib.Path, "read_bytes", tracked)
    monkeypatch.setattr(
        soleaux.structural.snapshot.RepositorySnapshotter, "inventory", reject_inventory
    )
    first.write_text("first = 2\n", encoding="utf-8")
    builder.mark_dirty(workspace.workspace_id, ("first.py",))
    reconciled = await builder.capture(workspace)

    assert reads == ["first.py"]
    assert reconciled.contents["first.py"] == b"first = 2\n"
    assert reconciled.contents["second.py"] == b"second = 1\n"
    await builder.aclose()


async def test_search_paths_scope_rows_and_cursor_arguments(tmp_path: pathlib.Path) -> None:
    source_root = tmp_path / "src"
    nested_root = source_root / "nested"
    nested_root.mkdir(parents=True)
    (source_root / "a.py").write_text("needle one\n", encoding="utf-8")
    (nested_root / "b.py").write_text("needle two\n", encoding="utf-8")
    (tmp_path / "src-other").mkdir()
    (tmp_path / "src-other" / "c.py").write_text("needle excluded\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "d.md").write_text("needle docs\n", encoding="utf-8")

    scoped_paths = {"src/a.py", "src/nested/b.py"}
    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                paths=["src/nested", "src"],
                limit=1,
            )
        )
        assert first.next_cursor is not None
        second = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                paths=["src"],
                limit=1,
                cursor=first.next_cursor,
            )
        )
        mismatched = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="needle",
                paths=["docs"],
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert first.rows is not None
    assert second.rows is not None
    assert {str(row["path"]) for row in first.rows} <= scoped_paths
    assert {str(row["path"]) for row in second.rows} <= scoped_paths
    assert {str(row["key"]) for row in first.rows}.isdisjoint(
        str(row["key"]) for row in second.rows
    )
    assert mismatched.status is soleaux.contracts.results.ResultStatus.ERROR
    assert mismatched.error is not None
    assert mismatched.error.error_type == "invalid_cursor"


async def test_search_paths_reject_absolute_and_escaping_values(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("needle\n", encoding="utf-8")

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        absolute = await service.search(
            soleaux.contracts.requests.SearchRequest(query="needle", paths=[str(source)])
        )
        escaping = await service.search(
            soleaux.contracts.requests.SearchRequest(query="needle", paths=["../outside.py"])
        )

    for response in (absolute, escaping):
        assert response.status is soleaux.contracts.results.ResultStatus.ERROR
        assert response.error is not None
        assert response.error.error_type == "search_failed"


async def test_binary_snapshot_notes_are_exact_internally_and_bounded_in_responses(
    tmp_path: pathlib.Path,
) -> None:
    binary_count = 300
    for index in range(binary_count):
        (tmp_path / f"binary-{index:03}.bin").write_bytes(b"\x00")
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")

    workspaces = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="d" * 64,
    )
    bundle = await soleaux.structural.snapshot.RepositorySnapshotter(workspaces.get(None)).capture()
    assert len(bundle.notes) == binary_count
    assert all(note.startswith("skipped binary file ") for note in bundle.notes)

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        response = await service.search(
            soleaux.contracts.requests.SearchRequest(query="diagnosticProvider")
        )

    assert len(response.warnings) <= 20
    assert all(len(warning) <= 512 for warning in response.warnings)
    binary_warnings = [
        warning for warning in response.warnings if warning.startswith("skipped binary file:")
    ]
    assert len(binary_warnings) == 1
    assert "300 occurrences" in binary_warnings[0]
    assert "297 paths omitted" in binary_warnings[0]


def test_response_warning_compactor_reports_omitted_groups_and_bounds_text() -> None:
    warnings = [
        f"unique warning {index}"
        for index in range(soleaux.analysis.service.MAX_RESPONSE_WARNINGS + 5)
    ]
    warnings.append("x" * (soleaux.analysis.service.MAX_WARNING_CHARS + 100))

    compacted = soleaux.analysis.service.compact_response_warnings(warnings)

    assert len(compacted) == soleaux.analysis.service.MAX_RESPONSE_WARNINGS
    assert all(len(warning) <= soleaux.analysis.service.MAX_WARNING_CHARS for warning in compacted)
    assert compacted[-1] == "omitted 7 warning groups covering 7 warnings"
