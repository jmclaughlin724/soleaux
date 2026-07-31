"""Cold-start catalog profiles, restore identity, and explicit promotion."""

from __future__ import annotations

import json
import pathlib

import pytest
from _assertions import raises_with_message, string_list
from fastmcp import Client

from soleaux.analysis.frame import AnalysisFrameBuilder
from soleaux.analysis.service import SoleauxService
from soleaux.catalog.generation import CatalogGeneration
from soleaux.catalog.indexer import CatalogPublicationProfile
from soleaux.catalog.store import (
    CatalogLifecycleState,
    CatalogStore,
    CatalogStoreError,
    PreparedMaterializedPublication,
)
from soleaux.contracts.config import CatalogConfig, CatalogMode, ResolvedConfig
from soleaux.contracts.requests import DescribeRequest, OwnershipRequest
from soleaux.contracts.tables import SYNTAX_ONLY_MATERIALIZED_TABLES
from soleaux.contracts.workspace import AllowedWorkspaceSet, WorkspaceRoot
from soleaux.server import create_server
from soleaux.structural.snapshot import SnapshotBundle

_AUTHORITY_CLOSURE = (
    "repository.scripts",
    "repository.configurations",
    "repository.imports",
    "syntax.imports",
    "framework.registrations",
    "authority.policies",
    "authority.bindings",
    "authority.conflicts",
    "tests",
)
_STARTUP_CLOSURE = ("repository.chunks", "repository.files")


def _write_governance_fixture(root: pathlib.Path) -> None:
    (root / "soleaux.toml").write_text(
        'schema_version = "soleaux.config/v1"\n'
        "\n"
        "[[governance.sources]]\n"
        'id = "first"\n'
        'path = "first.md"\n'
        'format = "markdown"\n'
        'selector = { kind = "markdown_table", heading = "First registry", occurrence = 1 }\n'
        'identity_field = "Policy"\n'
        'relationships = [{ field = "Steward", required = true }]\n'
        "\n"
        "[[governance.sources]]\n"
        'id = "second"\n'
        'path = "second.md"\n'
        'format = "markdown"\n'
        'selector = { kind = "markdown_table", heading = "Second registry", occurrence = 1 }\n'
        'identity_field = "Policy"\n'
        'relationships = [{ field = "Steward", required = true }]\n',
        encoding="utf-8",
    )
    (root / "first.md").write_text(
        "# First registry\n\n"
        "| Policy | Steward |\n"
        "| --- | --- |\n"
        "| policy:first | `src/first.py` |\n",
        encoding="utf-8",
    )
    (root / "second.md").write_text(
        "# Second registry\n\n"
        "| Policy | Steward |\n"
        "| --- | --- |\n"
        "| policy:second | `src/second.py` |\n",
        encoding="utf-8",
    )
    source = root / "src"
    source.mkdir()
    (source / "first.py").write_text(
        "from src.second import second\n\nfirst = second\n",
        encoding="utf-8",
    )
    (source / "second.py").write_text("second = 2\n", encoding="utf-8")


def _disk_config() -> ResolvedConfig:
    return ResolvedConfig.default().model_copy(
        update={"catalog": CatalogConfig(mode=CatalogMode.DISK)}
    )


async def test_injected_frame_builder_must_match_publication_storage_namespace(
    tmp_path: pathlib.Path,
) -> None:
    workspaces = AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="profile-storage-namespace",
    )
    full_builder = AnalysisFrameBuilder()
    authority_builder = AnalysisFrameBuilder(
        storage_namespace=CatalogPublicationProfile.AUTHORITY.value
    )
    try:
        with raises_with_message(ValueError, "storage namespace does not match"):
            SoleauxService(
                workspaces,
                frame_builder=full_builder,
                publication_profile=CatalogPublicationProfile.AUTHORITY,
            )
        with raises_with_message(ValueError, "storage namespace does not match"):
            SoleauxService(
                workspaces,
                frame_builder=authority_builder,
                publication_profile=CatalogPublicationProfile.FULL,
            )

        service = SoleauxService(
            workspaces,
            frame_builder=authority_builder,
            publication_profile=CatalogPublicationProfile.AUTHORITY,
        )
        assert service._frames is authority_builder
        await service.aclose()
    finally:
        await full_builder.aclose()
        await authority_builder.aclose()


async def test_authority_profile_is_planner_derived_and_source_complete(
    tmp_path: pathlib.Path,
) -> None:
    _write_governance_fixture(tmp_path)
    async with SoleauxService.from_root(
        tmp_path,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    ) as service:
        assert SoleauxService.AUTHORITY_READ_TABLES == (
            "authority.policies",
            "authority.bindings",
            "authority.conflicts",
        )
        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY
        assert service.publication_attempted_tables == _AUTHORITY_CLOSURE

        first = await service.ownership(OwnershipRequest(policy="first.md"))
        second = await service.ownership(OwnershipRequest(policy="second.md"))

        assert first.coverage is not None
        assert first.coverage.status.value == "complete"
        assert first.data is not None
        assert first.data["policy"]["policy_id"] == "policy:first"
        assert second.coverage is not None
        assert second.coverage.status.value == "complete"
        assert second.data is not None
        assert second.data["policy"]["policy_id"] == "policy:second"

        workspace_id = service.workspace_ids[0]
        store = service._frames.existing_catalog_store(workspace_id)
        assert store is not None
        publication = service._catalog_indexer._publications[workspace_id]
        materialized = store.read_materialized(workspace_id, limit=1)
        assert frozenset(publication.attempted_tables) == frozenset(_AUTHORITY_CLOSURE)
        assert frozenset(materialized.published_tables) == frozenset(_AUTHORITY_CLOSURE)
        assert publication.enrichment_complete is True


async def test_authority_disk_restore_is_exact_and_does_not_assert_full_enrichment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _write_governance_fixture(root)
    cache_root = tmp_path / "cache"

    def test_catalog_path(
        _workspace_root: pathlib.Path,
        source_fingerprint: str | None = None,
        *,
        storage_namespace: str | None = None,
    ) -> pathlib.Path:
        filename = (
            f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
        )
        directory = cache_root if storage_namespace is None else cache_root / storage_namespace
        return directory / filename

    monkeypatch.setattr(
        "soleaux.catalog.store.catalog_database_path",
        test_catalog_path,
    )
    config = _disk_config()

    first = SoleauxService.from_root(
        root,
        config=config,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )
    await first.start()
    assert first.structural_worker_started is True
    await first.aclose()

    restored = SoleauxService.from_root(
        root,
        config=config,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )
    await restored.start()
    try:
        assert restored.publication_profile is CatalogPublicationProfile.AUTHORITY
        assert restored.structural_worker_started is False
        assert restored.structural_completed_jobs == 0
    finally:
        await restored.aclose()

    promoted = SoleauxService.from_root(root, config=config)
    await promoted.start()
    try:
        assert promoted.publication_profile is CatalogPublicationProfile.FULL
        assert promoted.structural_worker_started is False
        assert promoted.structural_completed_jobs == 0
        await promoted._catalog_indexer.settle()
        assert promoted.structural_worker_started is True
        workspace_id = promoted.workspace_ids[0]
        store = promoted._frames.existing_catalog_store(workspace_id)
        assert store is not None
        persisted = store.materialized_publication(workspace_id)
        assert persisted is not None
        assert frozenset(persisted.attempted_tables) == frozenset(SYNTAX_ONLY_MATERIALIZED_TABLES)
    finally:
        await promoted.aclose()


async def test_full_and_authority_disk_services_materialize_in_isolated_namespaces(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _write_governance_fixture(root)
    cache_root = tmp_path / "cache"

    def test_cache_path(_appname: str, *, appauthor: bool) -> pathlib.Path:
        assert appauthor is False
        return cache_root

    monkeypatch.setattr(
        "platformdirs.user_cache_path",
        test_cache_path,
    )
    config = _disk_config()
    full = SoleauxService.from_root(root, config=config)
    authority = SoleauxService.from_root(
        root,
        config=config,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )

    try:
        await full.start()
        full_workspace_id = full.workspace_ids[0]
        full_store = full._frames.existing_catalog_store(full_workspace_id)
        assert full_store is not None
        startup = full_store.materialized_publication(full_workspace_id)
        assert startup is not None
        assert startup.enrichment_settled is False
        assert startup.attempted_tables == _STARTUP_CLOSURE
        assert full.structural_worker_started is False

        await full._catalog_indexer.settle()
        full_path = full_store.path
        assert full_path is not None
        full_described = await full.describe(DescribeRequest(workspace_id=full_workspace_id))
        assert full_described.data is not None
        full_storage = full_described.data["storage"]
        assert full_storage["storage_namespace"] is None
        assert pathlib.Path(full_storage["expected_path"]).parent == full_path.parent

        await authority.start()
        authority_workspace_id = authority.workspace_ids[0]
        authority_store = authority._frames.existing_catalog_store(authority_workspace_id)
        assert authority_store is not None
        authority_path = authority_store.path
        assert authority_path is not None

        assert authority_path != full_path
        assert authority_path.parent.name == CatalogPublicationProfile.AUTHORITY.value
        assert authority_path.parent.parent == full_path.parent

        full_materialized = full_store.materialized_publication(full_workspace_id)
        authority_materialized = authority_store.materialized_publication(authority_workspace_id)
        assert full_materialized is not None
        assert authority_materialized is not None
        assert frozenset(full_materialized.attempted_tables) == frozenset(
            SYNTAX_ONLY_MATERIALIZED_TABLES
        )
        assert frozenset(authority_materialized.attempted_tables) == frozenset(_AUTHORITY_CLOSURE)

        described = await authority.describe(DescribeRequest(workspace_id=authority_workspace_id))
        assert described.data is not None
        storage = described.data["storage"]
        assert storage["storage_namespace"] == CatalogPublicationProfile.AUTHORITY.value
        assert pathlib.Path(storage["expected_path"]).parent == authority_path.parent

        await authority.aclose()
        assert full_path.is_file()
        full_after_authority_gc = full_store.materialized_publication(full_workspace_id)
        assert full_after_authority_gc is not None
        assert frozenset(full_after_authority_gc.attempted_tables) == frozenset(
            SYNTAX_ONLY_MATERIALIZED_TABLES
        )

        restored_authority = CatalogStore(
            root,
            mode=CatalogMode.DISK,
            storage_namespace=CatalogPublicationProfile.AUTHORITY.value,
            config_digest=authority._config_digest,
        )
        try:
            restored_generation = restored_authority.load()
            assert restored_generation is not None
            assert restored_authority.path == authority_path
        finally:
            restored_authority.close()
    finally:
        await authority.aclose()
        await full.aclose()


async def test_authority_server_tools_read_only_the_lifecycle_published_profile(
    tmp_path: pathlib.Path,
) -> None:
    _write_governance_fixture(tmp_path)
    service = SoleauxService.from_root(
        tmp_path,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )
    server = create_server(
        tmp_path,
        service_factory=lambda: service,
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )

    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        assert len(tools) == 10
        assert len(resources) == 7
        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY

        owners = await client.call_tool(
            "owners",
            {"request": {"policy": "first.md"}},
        )
        assert owners.structured_content is not None
        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY

        queried = await client.call_tool(
            "query",
            {"request": {"include_tables": ["repository.files"]}},
        )
        assert queried.structured_content is not None
        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY
        assert service.publication_attempted_tables == _AUTHORITY_CLOSURE


async def test_multi_workspace_promotion_prepares_every_root_before_publication(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "first.md").write_text("# First\n", encoding="utf-8")
    (second_root / "second.md").write_text("# Second\n", encoding="utf-8")
    service = SoleauxService.from_launch(
        (("first", first_root), ("second", second_root)),
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )
    await service.start()
    original_catalog_bundle = service._frames.catalog_bundle
    first_store = service._frames.existing_catalog_store("first")
    assert first_store is not None
    original_publish = first_store.publish_prepared_materialized
    publication_attempts: list[tuple[str, ...]] = []

    def record_publication(
        prepared: PreparedMaterializedPublication,
        *,
        retained_generations: int,
    ) -> None:
        attempted_tables = string_list(json.loads(prepared.attempted_tables_json))
        publication_attempts.append(tuple(attempted_tables))
        original_publish(
            prepared,
            retained_generations=retained_generations,
        )

    async def fail_second_workspace(
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        if workspace.workspace_id == "second":
            raise RuntimeError("synthetic second-workspace preparation failure")
        return await original_catalog_bundle(workspace, validate=validate)

    monkeypatch.setattr(
        service._frames,
        "catalog_bundle",
        fail_second_workspace,
    )
    monkeypatch.setattr(
        first_store,
        "publish_prepared_materialized",
        record_publication,
    )
    try:
        with raises_with_message(
            RuntimeError,
            "catalog FULL preparation failed for second",
        ):
            await service.ensure_full_catalog()

        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY
        assert publication_attempts == []
        for workspace_id in service.workspace_ids:
            publication = service._catalog_indexer._publications[workspace_id]
            store = service._frames.existing_catalog_store(workspace_id)
            assert publication.profile is CatalogPublicationProfile.AUTHORITY
            assert store is not None
            retained = store.read_materialized(workspace_id, limit=1)
            assert retained.state is CatalogLifecycleState.READY
            assert frozenset(retained.published_tables) == frozenset(_AUTHORITY_CLOSURE)
            owners = await service.ownership(
                OwnershipRequest(
                    policy="missing-policy",
                    workspace_id=workspace_id,
                )
            )
            assert owners.coverage is not None
            assert owners.coverage.status.value == "complete"

        monkeypatch.setattr(
            service._frames,
            "catalog_bundle",
            original_catalog_bundle,
        )
        await service.ensure_full_catalog()

        assert publication_attempts == [tuple(sorted(SYNTAX_ONLY_MATERIALIZED_TABLES))]
        assert service.publication_profile is CatalogPublicationProfile.FULL
        assert all(
            publication.profile is CatalogPublicationProfile.FULL
            for publication in service._catalog_indexer._publications.values()
        )
    finally:
        await service.aclose()


async def test_later_workspace_publication_failure_rolls_back_authority(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "first.md").write_text("# First\n", encoding="utf-8")
    (second_root / "second.md").write_text("# Second\n", encoding="utf-8")
    service = SoleauxService.from_launch(
        (("first", first_root), ("second", second_root)),
        publication_profile=CatalogPublicationProfile.AUTHORITY,
    )
    await service.start()
    second_store = service._frames.existing_catalog_store("second")
    assert second_store is not None
    original_publish = second_store.publish_prepared_materialized

    def fail_full_publication(
        prepared: PreparedMaterializedPublication,
        *,
        retained_generations: int,
    ) -> None:
        attempted_tables = string_list(json.loads(prepared.attempted_tables_json))
        if set(attempted_tables) == set(SYNTAX_ONLY_MATERIALIZED_TABLES):
            raise CatalogStoreError("synthetic second-workspace publication failure")
        original_publish(
            prepared,
            retained_generations=retained_generations,
        )

    monkeypatch.setattr(
        second_store,
        "publish_prepared_materialized",
        fail_full_publication,
    )
    try:
        with raises_with_message(
            CatalogStoreError,
            "synthetic second-workspace publication failure",
        ):
            await service.ensure_full_catalog()

        assert service.publication_profile is CatalogPublicationProfile.AUTHORITY
        for workspace_id in service.workspace_ids:
            publication = service._catalog_indexer._publications[workspace_id]
            store = service._frames.existing_catalog_store(workspace_id)
            assert publication.profile is CatalogPublicationProfile.AUTHORITY
            assert store is not None
            retained = store.read_materialized(workspace_id, limit=1)
            assert retained.state is CatalogLifecycleState.READY
            assert frozenset(retained.published_tables) == frozenset(_AUTHORITY_CLOSURE)

        monkeypatch.setattr(
            second_store,
            "publish_prepared_materialized",
            original_publish,
        )
        await service.ensure_full_catalog()

        assert service.publication_profile is CatalogPublicationProfile.FULL
        assert all(
            publication.profile is CatalogPublicationProfile.FULL
            for publication in service._catalog_indexer._publications.values()
        )
    finally:
        await service.aclose()


async def test_normal_service_starts_full(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    async with SoleauxService.from_root(tmp_path) as service:
        assert service.publication_profile is CatalogPublicationProfile.FULL
        assert service.publication_attempted_tables == SYNTAX_ONLY_MATERIALIZED_TABLES
