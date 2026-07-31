"""Immutable catalog indexes and disposable SQLite projections."""

import asyncio
import datetime
import pathlib
import sqlite3
import threading
import time
from typing import cast

import platformdirs
import pytest
from fastmcp import Client

import soleaux.analysis.frame
import soleaux.analysis.service
import soleaux.catalog.contracts
import soleaux.catalog.generation
import soleaux.catalog.postgresql
import soleaux.catalog.search
import soleaux.catalog.store
import soleaux.catalog.structural
import soleaux.catalog.tables
import soleaux.contracts.config
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.positions
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.snapshot
import soleaux.contracts.workspace
import soleaux.postgresql.contracts
import soleaux.server
import soleaux.structural.fragments
import soleaux.structural.snapshot
import soleaux.structural.supervisor
import soleaux.tables.evidence


def _external_catalog_path(
    workspace_root: pathlib.Path,
    filename: str = "catalog.sqlite3",
) -> pathlib.Path:
    return workspace_root.parent / f"{workspace_root.name}-catalog-state" / filename


def snapshot_bundle(
    root: pathlib.Path, contents: dict[str, bytes]
) -> soleaux.structural.snapshot.SnapshotBundle:
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(root))],
        config_digest="d" * 64,
    ).get(None)
    rows: list[soleaux.contracts.snapshot.CapturedFile] = []
    for path, content in sorted(contents.items()):
        repository_path = soleaux.contracts.repository.RepositoryPath.admit(workspace, path)
        language = soleaux.contracts.repository.LANGUAGE_REGISTRY.detect(repository_path)
        text = content.decode("utf-8")
        rows.append(
            soleaux.contracts.snapshot.CapturedFile(
                workspace_id="main",
                path=repository_path.value,
                content_hash=soleaux.contracts.repository.content_digest(content),
                byte_start=0,
                byte_end=len(content),
                start_line=0,
                start_column=0,
                end_line=text.count("\n"),
                end_column=len(text.rsplit("\n", 1)[-1]),
                language=language.structural_language if language else None,
                language_id=language.language_id if language else None,
                parser_id=language.parser_id if language else None,
                producer_id="test",
                producer_version="1",
                producer_config_digest="d" * 64,
                claim_basis=soleaux.contracts.snapshot.ClaimBasis.SYNTAX,
            )
        )
    fingerprint = soleaux.contracts.repository.content_digest(
        "\0".join(f"{row.path}\0{row.content_hash}" for row in rows).encode("utf-8")
    )
    return soleaux.structural.snapshot.SnapshotBundle(
        snapshot=soleaux.contracts.snapshot.RepositorySnapshot(
            snapshot_id=f"main:{fingerprint[:16]}",
            workspace_id="main",
            root=str(root),
            created_at=datetime.datetime.now(datetime.UTC),
            files=tuple(rows),
            source_fingerprint=fingerprint,
        ),
        contents=dict(contents),
        notes=(),
    )


def test_ranked_match_modes_share_safe_token_semantics(tmp_path: pathlib.Path) -> None:
    bundle = snapshot_bundle(
        tmp_path,
        {
            "alpha.md": b"alpha only\n",
            "beta.md": b"beta only\n",
        },
    )
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(
        bundle,
        generation=1,
    )

    assert soleaux.catalog.search.fts_match_expression("alpha beta") == '"alpha" "beta" *'
    assert (
        soleaux.catalog.search.fts_match_expression(
            "alpha beta",
            match_mode=soleaux.catalog.search.SearchMatchMode.ANY,
        )
        == '"alpha" OR "beta" *'
    )
    all_hits, _ = soleaux.catalog.search.linear_search(
        generation,
        "alpha beta",
        limit=10,
    )
    any_hits, _ = soleaux.catalog.search.linear_search(
        generation,
        "alpha beta",
        limit=10,
        match_mode=soleaux.catalog.search.SearchMatchMode.ANY,
    )

    assert all_hits == ()
    assert {hit.path for hit in any_hits if hit.kind == "chunk"} == {
        "alpha.md",
        "beta.md",
    }


async def test_memory_catalog_skips_duplicate_restart_projection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text("# Example\n", encoding="utf-8")
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        soleaux.structural.supervisor.StructuralWorkerSupervisor(),
        config=config,
        config_content_digest=config_content_digest,
    )
    store = builder.ensure_catalog_store(workspace)

    def unexpected_publish(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("memory mode must not serialize the restart projection")

    monkeypatch.setattr(store, "publish", unexpected_publish)
    generation, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert generation.workspace_id == workspace.workspace_id
    assert store.mode is soleaux.contracts.config.CatalogMode.MEMORY


async def test_catalog_table_projection_keeps_event_loop_responsive(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = snapshot_bundle(tmp_path, {"README.md": b"# Example\n"})
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(
        bundle,
        generation=1,
    )
    producer = soleaux.catalog.tables.CatalogTableProducer(generation)
    original_row = producer._row
    projection_started = threading.Event()
    allow_projection = threading.Event()

    def blocking_row(
        selected_bundle: soleaux.structural.snapshot.SnapshotBundle,
        table_name: str,
        record: soleaux.catalog.contracts.CatalogRecord,
    ) -> soleaux.contracts.frame.FactRow:
        projection_started.set()
        allow_projection.wait(timeout=1)
        return original_row(selected_bundle, table_name, record)

    monkeypatch.setattr(producer, "_row", blocking_row)

    async def heartbeat() -> float:
        await asyncio.sleep(0.05)
        return time.perf_counter()

    started_at = time.perf_counter()
    heartbeat_task = asyncio.create_task(heartbeat())
    projection_task = asyncio.create_task(
        producer.produce(
            ("repository.chunks",),
            bundle,
            soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            {},
        )
    )
    assert await asyncio.to_thread(projection_started.wait, 1)
    heartbeat_at = await heartbeat_task
    allow_projection.set()
    projected = await projection_task

    assert heartbeat_at - started_at < 0.25
    assert len(projected["repository.chunks"]) == 1


def test_generation_indexes_projects_dependencies_and_deterministic_chunks(
    tmp_path: pathlib.Path,
) -> None:
    bundle = snapshot_bundle(
        tmp_path,
        {
            "package.json": (
                b'{"name":"fixture","dependencies":{"yaml":"2.8.0"},'
                b'"scripts":{"typecheck":"tsc --noEmit"}}'
            ),
            "src/main.ts": b'export const needle = "catalog";\n',
        },
    )
    builder = soleaux.catalog.generation.CatalogGenerationBuilder()
    first = builder.build(bundle, generation=1)
    second = builder.build(bundle, generation=2)

    assert first.projects_by_id["main:node:."].name == "fixture"
    assert first.dependencies_by_package["yaml"][0].resolved_specifier == "2.8.0"
    assert tuple(first.chunks_by_id) == tuple(second.chunks_by_id)
    assert all(
        len(chunk.text.encode("utf-8")) <= soleaux.catalog.generation.DEFAULT_CHUNK_BYTES
        for chunk in first.facts.chunks
    )


def test_generation_splits_long_utf8_lines_at_the_byte_bound(tmp_path: pathlib.Path) -> None:
    content = ("é" * (soleaux.catalog.generation.DEFAULT_CHUNK_BYTES + 1)).encode()
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(
        snapshot_bundle(tmp_path, {"large.ts": content}),
        generation=1,
    )

    assert len(generation.facts.chunks) == 3
    assert b"".join(chunk.text.encode() for chunk in generation.facts.chunks) == content
    assert all(
        len(chunk.text.encode()) <= soleaux.catalog.generation.DEFAULT_CHUNK_BYTES
        for chunk in generation.facts.chunks
    )
    assert {chunk.start_line for chunk in generation.facts.chunks} == {1}
    assert {chunk.end_line for chunk in generation.facts.chunks} == {1}


def test_generation_and_sqlite_update_only_changed_chunks(tmp_path: pathlib.Path) -> None:
    builder = soleaux.catalog.generation.CatalogGenerationBuilder()
    first_bundle = snapshot_bundle(
        tmp_path,
        {
            "a.ts": b"export const stable = 1;\n",
            "b.ts": b"export const changed = 1;\n",
        },
    )
    first = builder.build(first_bundle, generation=1)
    second_bundle = snapshot_bundle(
        tmp_path,
        {
            "a.ts": b"export const stable = 1;\n",
            "b.ts": b"export const changed = 2;\n",
        },
    )
    changed = soleaux.catalog.generation.changed_snapshot_paths(
        first.snapshot, second_bundle.snapshot
    )
    second = builder.update(
        first,
        second_bundle,
        generation=2,
        changed_paths=changed,
    )
    stable_chunk = first.chunks_by_path["a.ts"][0]

    assert changed == frozenset({"b.ts"})
    assert second.chunks_by_path["a.ts"] == (stable_chunk,)
    assert second.chunks_by_path["b.ts"] != first.chunks_by_path["b.ts"]

    database = _external_catalog_path(tmp_path)
    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.DISK, path=database
    ) as store:
        store.publish(first)
        with sqlite3.connect(database) as connection:
            stable_rowid = connection.execute(
                "SELECT rowid FROM chunks WHERE chunk_id = ?",
                (stable_chunk.chunk_id,),
            ).fetchone()
        store.publish(
            second,
            previous_fingerprint=first.source_fingerprint,
            changed_paths=changed,
        )
    with sqlite3.connect(database) as connection:
        updated_rowid = connection.execute(
            "SELECT rowid FROM chunks WHERE chunk_id = ?",
            (stable_chunk.chunk_id,),
        ).fetchone()

    assert stable_rowid == updated_rowid


def test_incremental_generation_retains_every_unchanged_structural_fact(
    tmp_path: pathlib.Path,
) -> None:
    builder = soleaux.catalog.generation.CatalogGenerationBuilder()
    first_bundle = snapshot_bundle(
        tmp_path,
        {
            "stable.ts": b'import "stable-package";\nexport const stable = 1;\n',
            "changed.ts": b'import "changed-package";\nexport const changed = 1;\n',
        },
    )
    base = builder.build(first_bundle, generation=1)
    captured = {item.path: item for item in first_bundle.snapshot.files}

    def extracted(path: str, name: str) -> soleaux.catalog.structural.ExtractedFile:
        return soleaux.catalog.structural.ExtractedFile(
            digest=captured[path].content_hash,
            language="TypeScript",
            fragments=(
                soleaux.structural.fragments.SyntaxFragment(
                    projection="syntax.imports",
                    kind="import",
                    name=f"{name}-package",
                    path=path,
                    language="TypeScript",
                    byte_start=0,
                    byte_end=20,
                    start_line=0,
                    start_column=0,
                    end_line=0,
                    end_column=20,
                ),
                soleaux.structural.fragments.SyntaxFragment(
                    projection="syntax.declarations",
                    kind="constant",
                    name=name,
                    path=path,
                    language="TypeScript",
                    byte_start=21,
                    byte_end=44,
                    start_line=1,
                    start_column=0,
                    end_line=1,
                    end_column=23,
                ),
            ),
        )

    promoted = soleaux.catalog.structural.merge_structural_facts(
        base.facts,
        workspace_id="main",
        extracted={
            "stable.ts": extracted("stable.ts", "stable"),
            "changed.ts": extracted("changed.ts", "changed"),
        },
    )
    first = soleaux.catalog.generation.catalog_generation_from_facts(
        generation=2,
        snapshot=first_bundle.snapshot,
        facts=promoted,
    )
    second_bundle = snapshot_bundle(
        tmp_path,
        {
            "stable.ts": first_bundle.contents["stable.ts"],
            "changed.ts": b'import "changed-package";\nexport const changed = 2;\n',
        },
    )
    changed_paths = soleaux.catalog.generation.changed_snapshot_paths(
        first.snapshot,
        second_bundle.snapshot,
    )
    second = builder.update(
        first,
        second_bundle,
        generation=3,
        changed_paths=changed_paths,
    )

    assert changed_paths == frozenset({"changed.ts"})
    assert {
        (symbol.path, symbol.name)
        for symbol in second.facts.symbols
        if symbol.producer == soleaux.catalog.structural.STRUCTURAL_PRODUCER
    } == {("stable.ts", "stable")}
    assert {
        (imported.path, imported.specifier)
        for imported in second.facts.imports
        if imported.producer == soleaux.catalog.structural.STRUCTURAL_PRODUCER
    } == {("stable.ts", "stable-package")}


def test_incremental_manifest_change_rebinds_unchanged_structural_project_closure(
    tmp_path: pathlib.Path,
) -> None:
    builder = soleaux.catalog.generation.CatalogGenerationBuilder()
    module_path = "packages/child/module.py"
    schema_path = "packages/child/schema.sql"
    first_bundle = snapshot_bundle(
        tmp_path,
        {
            "pyproject.toml": (
                b'[project]\nname = "root"\nversion = "1.0.0"\ndependencies = ["pyyaml>=6.0"]\n'
            ),
            module_path: b"import yaml\nVALUE = 1\n",
            schema_path: b"select 1;\n",
        },
    )
    base = builder.build(first_bundle, generation=1)
    captured = {item.path: item for item in first_bundle.snapshot.files}
    promoted = soleaux.catalog.structural.merge_structural_facts(
        base.facts,
        workspace_id="main",
        extracted={
            module_path: soleaux.catalog.structural.ExtractedFile(
                digest=captured[module_path].content_hash,
                language="Python",
                fragments=(
                    soleaux.structural.fragments.SyntaxFragment(
                        projection="syntax.imports",
                        kind="import",
                        name="yaml",
                        path=module_path,
                        language="Python",
                        byte_start=0,
                        byte_end=11,
                        start_line=0,
                        start_column=0,
                        end_line=0,
                        end_column=11,
                    ),
                    soleaux.structural.fragments.SyntaxFragment(
                        projection="syntax.declarations",
                        kind="constant",
                        name="VALUE",
                        path=module_path,
                        language="Python",
                        byte_start=12,
                        byte_end=21,
                        start_line=1,
                        start_column=0,
                        end_line=1,
                        end_column=9,
                    ),
                ),
            )
        },
    )
    postgresql_engine = soleaux.catalog.contracts.EngineFact(
        workspace_id="main",
        source_path=schema_path,
        source_digest=captured[schema_path].content_hash,
        producer=soleaux.catalog.postgresql.POSTGRESQL_CATALOG_PRODUCER,
        producer_version="1",
        project_id="main:python:.",
        engine_id=soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID,
        role=soleaux.catalog.contracts.EngineRole.API,
        package_name="@libpg-query/parser",
        package_version="1",
        available=True,
        coverage="syntactic",
    )
    first = soleaux.catalog.generation.catalog_generation_from_facts(
        generation=2,
        snapshot=first_bundle.snapshot,
        facts=promoted.model_copy(update={"engines": (*promoted.engines, postgresql_engine)}),
    )
    second_bundle = snapshot_bundle(
        tmp_path,
        {
            **first_bundle.contents,
            "packages/child/pyproject.toml": (
                b'[project]\nname = "child"\nversion = "1.0.0"\ndependencies = ["pyyaml==7.0"]\n'
            ),
        },
    )
    changed_paths = soleaux.catalog.generation.changed_snapshot_paths(
        first.snapshot,
        second_bundle.snapshot,
    )
    second = builder.update(
        first,
        second_bundle,
        generation=3,
        changed_paths=changed_paths,
    )
    child_project_id = "main:python:packages/child"

    assert changed_paths == frozenset({"packages/child/pyproject.toml"})
    assert {
        symbol.project_id for symbol in second.facts.symbols if symbol.source_path == module_path
    } == {child_project_id}
    assert {
        imported.project_id
        for imported in second.facts.imports
        if imported.source_path == module_path
    } == {child_project_id}
    direct_dependencies = [
        dependency
        for dependency in second.facts.dependencies
        if dependency.source_path == module_path
        and dependency.usage is soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT
    ]
    assert [
        (
            dependency.project_id,
            dependency.package_name,
            dependency.declared_specifier,
            dependency.resolved_specifier,
        )
        for dependency in direct_dependencies
    ] == [(child_project_id, "pyyaml", "pyyaml==7.0", "pyyaml==7.0")]
    assert any(
        engine.project_id == child_project_id
        and engine.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
        for engine in second.facts.engines
    )


def test_incremental_unrelated_edit_preserves_route_export_enrichment(
    tmp_path: pathlib.Path,
) -> None:
    builder = soleaux.catalog.generation.CatalogGenerationBuilder()
    route_path = "app/api/health/route.ts"
    first_bundle = snapshot_bundle(
        tmp_path,
        {
            "package.json": b'{"name":"fixture","dependencies":{"next":"16.0.0"}}',
            route_path: (
                b"export function GET() { return new Response('ok'); }\n"
                b'export const runtime = "edge";\n'
            ),
            "README.md": b"# Before\n",
        },
    )
    base = builder.build(first_bundle, generation=1)
    captured = {item.path: item for item in first_bundle.snapshot.files}
    promoted = soleaux.catalog.structural.merge_structural_facts(
        base.facts,
        workspace_id="main",
        extracted={
            route_path: soleaux.catalog.structural.ExtractedFile(
                digest=captured[route_path].content_hash,
                language="TypeScript",
                fragments=(
                    soleaux.structural.fragments.SyntaxFragment(
                        projection="syntax.exports",
                        kind="export",
                        name="GET",
                        path=route_path,
                        language="TypeScript",
                        byte_start=0,
                        byte_end=52,
                        start_line=0,
                        start_column=0,
                        end_line=0,
                        end_column=52,
                    ),
                    soleaux.structural.fragments.SyntaxFragment(
                        projection="syntax.exports",
                        kind="export",
                        name="runtime",
                        path=route_path,
                        language="TypeScript",
                        byte_start=53,
                        byte_end=83,
                        start_line=1,
                        start_column=0,
                        end_line=1,
                        end_column=30,
                        attributes={"initializer_text": '"edge"'},
                    ),
                ),
            )
        },
    )
    first = soleaux.catalog.generation.catalog_generation_from_facts(
        generation=2,
        snapshot=first_bundle.snapshot,
        facts=promoted,
    )
    second_bundle = snapshot_bundle(
        tmp_path,
        {
            **first_bundle.contents,
            "README.md": b"# After\n",
        },
    )
    changed_paths = soleaux.catalog.generation.changed_snapshot_paths(
        first.snapshot,
        second_bundle.snapshot,
    )
    second = builder.update(
        first,
        second_bundle,
        generation=3,
        changed_paths=changed_paths,
    )

    assert changed_paths == frozenset({"README.md"})
    assert len(second.facts.routes) == 1
    assert second.facts.routes[0].methods == ("GET",)
    assert second.facts.routes[0].runtime == "edge"


def test_incremental_republish_without_changed_paths_keeps_fts_documents_unique(
    tmp_path: pathlib.Path,
) -> None:
    """The structural-enrichment republish must not duplicate non-chunk docs."""
    bundle = snapshot_bundle(
        tmp_path,
        {"package.json": b'{"name":"fixture","dependencies":{"yaml":"2.8.0"}}'},
    )
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = _external_catalog_path(tmp_path)

    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.DISK, path=database
    ) as store:
        store.publish(generation)
        store.publish(
            generation,
            previous_fingerprint=generation.source_fingerprint,
            changed_paths=frozenset(),
        )
        if store.fts_available:
            hits, _ = store.search_ranked(
                soleaux.catalog.search.fts_match_expression("yaml"),
                kinds=("dependency",),
                limit=10,
            )
            assert len(hits) == 1


def test_sqlite_projection_is_private_transactional_and_fts_capable(
    tmp_path: pathlib.Path,
) -> None:
    bundle = snapshot_bundle(
        tmp_path,
        {
            "package.json": b'{"name":"fixture","dependencies":{"yaml":"2.8.0"}}',
            "src/main.ts": b'export const needle = "catalog";\n',
        },
    )
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = _external_catalog_path(tmp_path)

    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.DISK, path=database
    ) as store:
        store.publish(generation)
        metadata = store.metadata()
        assert metadata["generation"] == "1"
        assert metadata["source_fingerprint"] == generation.source_fingerprint
        with sqlite3.connect(database) as connection:
            projection_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                )
            }
        assert not any(
            "embedding" in name.casefold() or "vector" in name.casefold()
            for name in projection_names
        )
        if store.fts_available:
            hits, has_more = store.search_ranked(
                soleaux.catalog.search.fts_match_expression("needle"), limit=10
            )
            assert hits
            assert has_more is False
            chunk_hits = [hit for hit in hits if hit.kind == "chunk"]
            assert chunk_hits[0].fact_key.startswith("chunk:")
            assert chunk_hits[0].path == "src/main.ts"
            file_hits, _ = store.search_ranked(
                soleaux.catalog.search.fts_match_expression("main"),
                kinds=("file",),
                limit=10,
            )
            assert [hit.fact_key for hit in file_hits] == ["path:src/main.ts"]

    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.DISK, path=database
    ) as reopened:
        restored = reopened.load()
        assert restored is not None
        assert restored.number == generation.number
        assert restored.snapshot == generation.snapshot
        assert restored.facts == generation.facts
        assert restored.source_fingerprint == generation.source_fingerprint

    assert database.is_file()
    assert database.stat().st_mode & 0o077 == 0
    assert database.parent.stat().st_mode & 0o077 == 0


def test_catalog_generation_is_bound_to_configuration_identity(
    tmp_path: pathlib.Path,
) -> None:
    bundle = snapshot_bundle(tmp_path, {"README.md": b"configuration-bound\n"})
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = _external_catalog_path(tmp_path)

    with soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
        config_digest="a" * 64,
    ) as store:
        store.publish(generation)
        assert store.metadata()["config_digest"] == "a" * 64

    with soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=database,
        config_digest="b" * 64,
    ) as reopened:
        assert reopened.load() is None

        assert reopened.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
        assert reopened.mode is soleaux.contracts.config.CatalogMode.MEMORY
        assert reopened.fallback_reason is not None
        assert "configuration identity" in reopened.fallback_reason


def test_catalog_modes_preserve_automatic_fallback_contract() -> None:
    assert tuple(mode.value for mode in soleaux.contracts.config.CatalogMode) == (
        "auto",
        "disk",
        "memory",
        "off",
    )


@pytest.mark.parametrize("operation", ("mark_building", "mark_failure"))
def test_lifecycle_writes_normalize_sqlite_failures(
    tmp_path: pathlib.Path,
    operation: str,
) -> None:
    with soleaux.catalog.store.CatalogStore(tmp_path) as store:
        connection = cast(sqlite3.Connection, store._connection)
        connection.execute("DROP TABLE catalog_state")

        with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
            if operation == "mark_building":
                store.mark_building("main")
            else:
                store.mark_failure("main", "injected failure")

    assert "catalog lifecycle update failed" in str(raised.value)


@pytest.mark.parametrize(
    "relative_path",
    (None, "state/catalog.sqlite3"),
    ids=("workspace-root", "workspace-descendant"),
)
def test_disk_mode_rejects_repository_local_path_before_write(
    tmp_path: pathlib.Path,
    relative_path: str | None,
) -> None:
    database = tmp_path if relative_path is None else tmp_path / relative_path
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    store = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    )

    try:
        with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
            store.open()
    finally:
        store.close()

    assert "disk catalog path must be outside the workspace" in str(raised.value)
    assert store.requested_mode is soleaux.contracts.config.CatalogMode.DISK
    assert store.mode is soleaux.contracts.config.CatalogMode.DISK
    assert store.fallback_reason is None
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before


def test_explicit_auto_mode_falls_back_before_repository_local_write(
    tmp_path: pathlib.Path,
) -> None:
    database = tmp_path / "state" / "catalog.sqlite3"

    with soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=database,
    ) as store:
        assert store.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
        assert store.mode is soleaux.contracts.config.CatalogMode.MEMORY
        assert store.path is None
        assert store.fallback_reason is not None
        assert "disk catalog path must be outside the workspace" in store.fallback_reason

    assert database.parent.exists() is False


@pytest.mark.parametrize(
    ("mode", "expected_mode"),
    (
        (
            soleaux.contracts.config.CatalogMode.DISK,
            soleaux.contracts.config.CatalogMode.DISK,
        ),
        (
            soleaux.contracts.config.CatalogMode.AUTO,
            soleaux.contracts.config.CatalogMode.MEMORY,
        ),
    ),
)
def test_platform_cache_inside_workspace_never_writes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: soleaux.contracts.config.CatalogMode,
    expected_mode: soleaux.contracts.config.CatalogMode,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = {path.relative_to(workspace) for path in workspace.rglob("*")}

    def repository_cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return workspace / "platform-cache"

    monkeypatch.setattr(
        platformdirs,
        "user_cache_path",
        repository_cache_path,
    )
    store = soleaux.catalog.store.CatalogStore(workspace, mode=mode)

    try:
        if mode is soleaux.contracts.config.CatalogMode.DISK:
            with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
                store.open()
            assert "disk catalog path must be outside the workspace" in str(raised.value)
        else:
            store.open()
            assert store.fallback_reason is not None
            assert "disk catalog path must be outside the workspace" in store.fallback_reason
    finally:
        store.close()

    assert store.mode is expected_mode
    assert {path.relative_to(workspace) for path in workspace.rglob("*")} == before


def test_auto_mode_recovers_from_corrupt_disk_catalog(tmp_path: pathlib.Path) -> None:
    database = _external_catalog_path(tmp_path)
    database.parent.mkdir()
    database.write_bytes(b"not a sqlite database")
    with soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=database,
    ) as store:
        assert store.load() is None
        assert store.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
        assert store.mode is soleaux.contracts.config.CatalogMode.MEMORY
        assert store.path is None
        assert store.fallback_reason is not None
        assert "catalog open failed" in store.fallback_reason


def test_disk_mode_fails_closed_for_corrupt_catalog(tmp_path: pathlib.Path) -> None:
    database = _external_catalog_path(tmp_path)
    database.parent.mkdir()
    database.write_bytes(b"not a sqlite database")
    store = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    )
    try:
        with pytest.raises(soleaux.catalog.store.CatalogStoreError) as raised:
            store.open()
    finally:
        store.close()

    assert "cannot open catalog store" in str(raised.value)
    assert store.requested_mode is soleaux.contracts.config.CatalogMode.DISK
    assert store.mode is soleaux.contracts.config.CatalogMode.DISK
    assert store.path == database
    assert store.fallback_reason is None


def test_auto_mode_recovers_from_private_directory_failure(
    tmp_path: pathlib.Path,
) -> None:
    blocked_parent = _external_catalog_path(tmp_path, "not-a-directory")
    blocked_parent.parent.mkdir()
    blocked_parent.write_text("blocked", encoding="utf-8")
    with soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=blocked_parent / "catalog.sqlite3",
    ) as store:
        assert store.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
        assert store.mode is soleaux.contracts.config.CatalogMode.MEMORY
        assert store.path is None
        assert store.fallback_reason is not None
        assert "catalog open failed" in store.fallback_reason


def test_auto_mode_recovers_from_locked_disk_catalog(tmp_path: pathlib.Path) -> None:
    bundle = snapshot_bundle(tmp_path, {"README.md": b"locked\n"})
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = _external_catalog_path(tmp_path)
    store = soleaux.catalog.store.CatalogStore(
        tmp_path,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=database,
    )
    store.open()
    blocker = sqlite3.connect(database, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        store.publish(generation)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        store.close()

    assert store.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
    assert store.mode is soleaux.contracts.config.CatalogMode.MEMORY
    assert store.fallback_reason is not None
    assert "catalog publish failed" in store.fallback_reason


def test_auto_mode_recovers_from_workspace_identity_mismatch(
    tmp_path: pathlib.Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    bundle = snapshot_bundle(first_root, {"README.md": b"identity\n"})
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = tmp_path / "state" / "catalog.sqlite3"

    with soleaux.catalog.store.CatalogStore(
        first_root,
        mode=soleaux.contracts.config.CatalogMode.DISK,
        path=database,
    ) as store:
        store.publish(generation)

    with soleaux.catalog.store.CatalogStore(
        second_root,
        mode=soleaux.contracts.config.CatalogMode.AUTO,
        path=database,
    ) as reopened:
        assert reopened.load() is None

        assert reopened.requested_mode is soleaux.contracts.config.CatalogMode.AUTO
        assert reopened.mode is soleaux.contracts.config.CatalogMode.MEMORY
        assert reopened.fallback_reason is not None
        assert "workspace identity" in reopened.fallback_reason


def test_memory_and_off_modes_have_no_disk_artifact(tmp_path: pathlib.Path) -> None:
    bundle = snapshot_bundle(tmp_path, {"README.md": b"needle\n"})
    generation = soleaux.catalog.generation.CatalogGenerationBuilder().build(bundle, generation=1)
    database = tmp_path / "catalog.sqlite3"

    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.MEMORY, path=database
    ) as memory:
        memory.publish(generation)
        assert memory.path is None
        assert memory.metadata()["generation"] == "1"
    with soleaux.catalog.store.CatalogStore(
        tmp_path, mode=soleaux.contracts.config.CatalogMode.OFF, path=database
    ) as disabled:
        disabled.publish(generation)
        assert disabled.path is None
        assert disabled.metadata() == {}

    assert database.exists() is False


def test_freshness_signatures_skip_inventory_symlinks_to_directories(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "directory-link").symlink_to(target, target_is_directory=True)
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    ).get(None)

    assert (
        soleaux.analysis.frame.AnalysisFrameBuilder._inventory_signatures(
            workspace,
            ("directory-link",),
        )
        == {}
    )


def test_persisted_snapshot_digest_builds_evidence_without_source_bytes(
    tmp_path: pathlib.Path,
) -> None:
    bundle = snapshot_bundle(tmp_path, {"package.json": b'{"name":"fixture"}'})
    metadata_only = soleaux.structural.snapshot.SnapshotBundle(
        snapshot=bundle.snapshot,
        contents={},
        notes=("restored",),
    )

    evidence = soleaux.tables.evidence.evidence_for_path(
        metadata_only,
        path="package.json",
        table="repository.projects",
        data={"project_id": "main:node:."},
        evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
        resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
        authority=soleaux.contracts.evidence.Authority.MANIFEST,
        provider="test",
        provider_version="1",
    )

    assert evidence.source_hash == bundle.snapshot.files[0].content_hash


def _postgresql_extract_result(
    *,
    content: bytes,
    context: soleaux.catalog.postgresql.PostgreSqlCatalogContext,
    object_name: str | None = "accounts",
    reference_name: str | None = None,
    retention_metadata: bool = False,
) -> soleaux.structural.supervisor.ExtractResult:
    location = soleaux.postgresql.contracts.SourceLocation(
        kind=soleaux.postgresql.contracts.LocationKind.EXACT_RANGE,
        range=soleaux.contracts.positions.PositionCodec(content).byte_range_to_points(
            0,
            len(content),
        ),
    )
    anchor = soleaux.postgresql.contracts.SourceAnchor(
        snapshot_id=context.snapshot_id,
        parser_generation="@libpg-query/parser@17.6.10",
        path=context.path,
        statement_index=0,
        source_lane=context.source_lane,
        location=location,
    )
    extraction = soleaux.catalog.postgresql.PostgreSqlCatalogExtraction(
        context=context,
        parser_version="17.6.10",
        postgresql_version=170004,
        statements=(
            soleaux.postgresql.contracts.StatementFact(
                source=anchor,
                statement_kind="CreateStmt",
            ),
        ),
        declarations=(
            (
                soleaux.postgresql.contracts.DeclarationFact(
                    source=anchor,
                    action=soleaux.postgresql.contracts.DeclarationAction.CREATE,
                    identity=soleaux.postgresql.contracts.ObjectIdentity(
                        kind=soleaux.postgresql.contracts.ObjectKind.TABLE,
                        schema="app",
                        name=object_name,
                    ),
                ),
            )
            if object_name is not None
            else ()
        ),
        references=(
            (
                soleaux.postgresql.contracts.ReferenceFact(
                    source=anchor,
                    reference_kind=soleaux.postgresql.contracts.ReferenceKind.RELATION,
                    name_parts=("app", reference_name),
                ),
            )
            if reference_name is not None
            else ()
        ),
        diagnostics=(
            (
                soleaux.postgresql.contracts.DiagnosticFact(
                    source=anchor,
                    origin=soleaux.postgresql.contracts.DiagnosticOrigin.RESOLVER,
                    severity=soleaux.postgresql.contracts.DiagnosticSeverity.WARNING,
                    message="retained PostgreSQL diagnostic",
                    code="retained-diagnostic",
                ),
            )
            if retention_metadata
            else ()
        ),
        omissions=(
            (
                soleaux.catalog.postgresql.PostgreSqlCatalogOmission(
                    statement_index=1,
                    statement_kind="AlterDomainStmt",
                    reason="retained PostgreSQL omission",
                ),
            )
            if retention_metadata
            else ()
        ),
    )
    return soleaux.structural.supervisor.ExtractResult(
        fragments=(),
        diagnostics=(),
        parses=1,
        parse_ms=0.1,
        truncated=False,
        unsupported=(),
        postgresql_catalog=extraction,
    )


async def test_search_indexes_postgresql_declarations_through_the_mcp_tool(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"CREATE TABLE app.accounts (id integer);\n"
    (tmp_path / "schema.sql").write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        if kwargs.get("postgresql_catalog") is None:
            return soleaux.structural.supervisor.ExtractResult(
                fragments=(),
                diagnostics=(),
                parses=1,
                parse_ms=0.1,
                truncated=False,
                unsupported=(),
            )
        assert kwargs["language"] == "PostgreSQL"
        assert kwargs["path"] == "schema.sql"
        assert kwargs["content"] == source
        assert kwargs["projections"] == ()
        context = kwargs["postgresql_catalog"]
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        return _postgresql_extract_result(content=source, context=context)

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_bytes = soleaux.contracts.config.resolved_config_bytes(config)
    config_content_digest = soleaux.contracts.config.config_digest(config_bytes)
    workspaces = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    )
    service = soleaux.analysis.service.SoleauxService(
        workspaces,
        config=config,
        frame_builder=soleaux.analysis.frame.AnalysisFrameBuilder(
            supervisor,
            config=config,
            config_content_digest=config_content_digest,
        ),
        config_content_digest=config_content_digest,
    )
    server = soleaux.server.create_server(
        tmp_path,
        config=config,
        service_factory=lambda: service,
    )

    async with Client(server, mode="auto") as client:
        await service._catalog_indexer.settle()
        result = await client.call_tool(
            "search",
            {"request": {"query": "accounts", "kinds": ["symbol"]}},
        )

    assert result.structured_content is not None
    payload = cast(dict[str, object], result.structured_content)
    raw_rows = payload.get("rows")
    assert isinstance(raw_rows, list)
    typed_rows = cast(list[object], raw_rows)
    rows = [cast(dict[str, object], row) for row in typed_rows if isinstance(row, dict)]
    assert any(
        row.get("path") == "schema.sql"
        and row.get("name") == "accounts"
        and row.get("engine_id") == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
        for row in rows
    ), rows


async def test_postgresql_worker_failure_retains_generic_chunks_and_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"CREATE TABLE app.accounts (id integer);\n"
    (tmp_path / "schema.sql").write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        assert kwargs["postgresql_catalog"] is not None
        raise soleaux.structural.supervisor.WorkerJobError(
            "parser_unavailable",
            "managed parser is not provisioned",
        )

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_bytes = soleaux.contracts.config.resolved_config_bytes(config)
    config_content_digest = soleaux.contracts.config.config_digest(config_bytes)
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    generation, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert any(chunk.path == "schema.sql" for chunk in generation.facts.chunks)
    assert any(
        warning
        == (
            "schema.sql: PostgreSQL catalog parser_unavailable; generic chunks retained: "
            "managed parser is not provisioned"
        )
        for warning in generation.facts.warnings
    )


async def test_postgresql_projection_batches_off_the_event_loop(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {
        "accounts.sql": b"CREATE TABLE app.accounts (id integer);\n",
        "users.sql": b"CREATE TABLE app.users (id integer);\n",
    }
    for path, source in sources.items():
        (tmp_path / path).write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        content = kwargs["content"]
        context = kwargs["postgresql_catalog"]
        assert isinstance(content, bytes)
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        return _postgresql_extract_result(
            content=content,
            context=context,
            object_name=pathlib.PurePosixPath(context.path).stem,
        )

    monkeypatch.setattr(supervisor, "extract", extract)
    original_merge = soleaux.analysis.frame.merge_postgresql_catalog
    projection_started = threading.Event()
    allow_projection = threading.Event()
    started_at: list[float] = []
    batch_sizes: list[int] = []

    def blocking_merge(
        facts: soleaux.catalog.contracts.CatalogFacts,
        *,
        workspace_id: str,
        sources: dict[str, bytes],
        extractions: tuple[soleaux.catalog.postgresql.PostgreSqlCatalogExtraction, ...],
    ) -> soleaux.catalog.contracts.CatalogFacts:
        started_at.append(time.perf_counter())
        batch_sizes.append(len(extractions))
        projection_started.set()
        allow_projection.wait(timeout=1)
        return original_merge(
            facts,
            workspace_id=workspace_id,
            sources=sources,
            extractions=extractions,
        )

    monkeypatch.setattr(
        soleaux.analysis.frame,
        "merge_postgresql_catalog",
        blocking_merge,
    )
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )
    projection = asyncio.create_task(builder.catalog_bundle(workspace))
    try:
        assert await asyncio.wait_for(
            asyncio.to_thread(projection_started.wait, 1),
            timeout=1.5,
        )
        assert time.perf_counter() - started_at[0] < 0.5
    finally:
        allow_projection.set()
    generation, _bundle = await projection
    await builder.aclose()

    assert batch_sizes == [2]
    assert {
        symbol.name
        for symbol in generation.facts.symbols
        if symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    } == {"accounts", "users"}


async def test_incremental_catalog_retains_unchanged_postgresql_facts(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {
        "changed.sql": b"CREATE TABLE app.changed_accounts (id integer);\n",
        "stable.sql": b"CREATE TABLE app.stable_accounts (id integer);\n",
    }
    for path, source in sources.items():
        (tmp_path / path).write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    extracted_paths: list[str] = []

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        content = kwargs["content"]
        context = kwargs["postgresql_catalog"]
        assert isinstance(content, bytes)
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        extracted_paths.append(context.path)
        return _postgresql_extract_result(
            content=content,
            context=context,
            object_name=("stable_accounts" if context.path == "stable.sql" else "changed_accounts"),
            retention_metadata=context.path == "stable.sql",
        )

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    first, _bundle = await builder.catalog_bundle(workspace)
    sources["changed.sql"] = b"CREATE TABLE app.changed_accounts (id bigint);\n"
    (tmp_path / "changed.sql").write_bytes(sources["changed.sql"])
    builder.mark_dirty(workspace.workspace_id, ("changed.sql",))
    second, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert {
        symbol.name
        for symbol in first.facts.symbols
        if symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    } == {"changed_accounts", "stable_accounts"}
    assert {
        symbol.name
        for symbol in second.facts.symbols
        if symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    } == {"changed_accounts", "stable_accounts"}
    first_stable_diagnostics = tuple(
        diagnostic.diagnostic_id
        for diagnostic in first.facts.diagnostics
        if diagnostic.path == "stable.sql"
        and diagnostic.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    )
    second_stable_diagnostics = tuple(
        diagnostic.diagnostic_id
        for diagnostic in second.facts.diagnostics
        if diagnostic.path == "stable.sql"
        and diagnostic.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
    )
    stable_warning = next(
        warning
        for warning in first.facts.warnings
        if warning.startswith("stable.sql: PostgreSQL catalog omitted ")
    )
    assert first_stable_diagnostics
    assert second_stable_diagnostics == first_stable_diagnostics
    assert stable_warning in second.facts.warnings
    assert any(
        engine.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
        for engine in second.facts.engines
    )
    assert extracted_paths.count("changed.sql") == 2
    assert extracted_paths.count("stable.sql") == 1


async def test_unrelated_edit_retains_unique_postgresql_chunks(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_source = b"CREATE TABLE app.accounts (id integer);\n"
    readme_source = b"# Before\n"
    (tmp_path / "schema.sql").write_bytes(sql_source)
    (tmp_path / "README.md").write_bytes(readme_source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    extracted_paths: list[str] = []

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        content = kwargs["content"]
        context = kwargs["postgresql_catalog"]
        assert isinstance(content, bytes)
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        extracted_paths.append(context.path)
        return _postgresql_extract_result(content=content, context=context)

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    first, _bundle = await builder.catalog_bundle(workspace)
    (tmp_path / "README.md").write_bytes(b"# After\n")
    builder.mark_dirty(workspace.workspace_id, ("README.md",))
    second, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    first_chunks = tuple(
        chunk.chunk_id
        for chunk in first.facts.chunks
        if chunk.path == "schema.sql"
        and chunk.producer == soleaux.catalog.postgresql.POSTGRESQL_CATALOG_PRODUCER
    )
    second_chunks = tuple(
        chunk.chunk_id
        for chunk in second.facts.chunks
        if chunk.path == "schema.sql"
        and chunk.producer == soleaux.catalog.postgresql.POSTGRESQL_CATALOG_PRODUCER
    )
    assert first_chunks
    assert second_chunks == first_chunks
    assert len(second_chunks) == len(set(second_chunks))
    assert extracted_paths == ["schema.sql"]


async def test_incremental_postgresql_resolution_uses_all_current_raw_extractions(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_path = "schema/accounts.sql"
    query_path = "tests/query.sql"
    schema_source = b"CREATE TABLE app.accounts (id integer);\n"
    query_source = b"SELECT * FROM app.users;\n"
    (tmp_path / "schema").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / schema_path).write_bytes(schema_source)
    (tmp_path / query_path).write_bytes(query_source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    extracted_paths: list[str] = []

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        content = kwargs["content"]
        context = kwargs["postgresql_catalog"]
        assert isinstance(content, bytes)
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        extracted_paths.append(context.path)
        if context.path == query_path:
            return _postgresql_extract_result(
                content=content,
                context=context,
                object_name=None,
                reference_name="users",
            )
        return _postgresql_extract_result(
            content=content,
            context=context,
            object_name="users" if b"users" in content else "accounts",
        )

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default().model_copy(
        update={
            "postgresql": soleaux.contracts.config.PostgreSqlConfig(
                lane_roots={
                    soleaux.postgresql.contracts.SourceLane.DESIRED_STATE: ("schema",),
                    soleaux.postgresql.contracts.SourceLane.TEST: ("tests",),
                }
            )
        }
    )
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    first, _bundle = await builder.catalog_bundle(workspace)
    schema_source = b"CREATE TABLE app.users (id integer);\n"
    (tmp_path / schema_path).write_bytes(schema_source)
    builder.mark_dirty(workspace.workspace_id, (schema_path,))
    second, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert not any(symbol.name == "users" for symbol in first.facts.symbols)
    users = next(symbol for symbol in second.facts.symbols if symbol.name == "users")
    assert tuple(reference.path for reference in users.references) == (query_path,)
    assert extracted_paths.count(schema_path) == 2
    assert extracted_paths.count(query_path) == 1


async def test_persistent_postgresql_failure_does_not_advance_generation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"CREATE TABLE app.accounts (id integer);\n"
    (tmp_path / "schema.sql").write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    attempts = 0

    async def extract(**_kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        nonlocal attempts
        attempts += 1
        raise soleaux.structural.supervisor.WorkerUnavailableError("worker restart in progress")

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    first, _bundle = await builder.catalog_bundle(workspace)
    second, _bundle = await builder.catalog_bundle(workspace)
    third, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert attempts == 3
    assert first.number == second.number == third.number
    assert first.facts == second.facts == third.facts


@pytest.mark.parametrize("failure_mode", ["worker_unavailable", "missing_payload"])
async def test_postgresql_transient_failure_retries_the_same_fingerprint(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    source = b"CREATE TABLE app.accounts (id integer);\n"
    (tmp_path / "schema.sql").write_bytes(source)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    attempts = 0

    async def extract(**kwargs: object) -> soleaux.structural.supervisor.ExtractResult:
        nonlocal attempts
        attempts += 1
        context = kwargs["postgresql_catalog"]
        assert isinstance(context, soleaux.catalog.postgresql.PostgreSqlCatalogContext)
        if attempts == 1:
            if failure_mode == "worker_unavailable":
                raise soleaux.structural.supervisor.WorkerUnavailableError(
                    "worker restart in progress"
                )
            return soleaux.structural.supervisor.ExtractResult(
                fragments=(),
                diagnostics=(),
                parses=1,
                parse_ms=0.1,
                truncated=False,
                unsupported=(),
            )
        return _postgresql_extract_result(content=source, context=context)

    monkeypatch.setattr(supervisor, "extract", extract)
    config = soleaux.contracts.config.ResolvedConfig.default()
    config_content_digest = soleaux.contracts.config.config_digest(
        soleaux.contracts.config.resolved_config_bytes(config)
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest=config_content_digest,
    ).get(None)
    builder = soleaux.analysis.frame.AnalysisFrameBuilder(
        supervisor,
        config=config,
        config_content_digest=config_content_digest,
    )

    first, _bundle = await builder.catalog_bundle(workspace)
    second, _bundle = await builder.catalog_bundle(workspace)
    await builder.aclose()

    assert attempts == 2
    assert not any(
        symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
        for symbol in first.facts.symbols
    )
    assert any(
        symbol.name == "accounts"
        and symbol.engine_id == soleaux.catalog.postgresql.POSTGRESQL_ENGINE_ID
        for symbol in second.facts.symbols
    )
    assert not any(
        warning.startswith("schema.sql: PostgreSQL catalog ") for warning in second.facts.warnings
    )
