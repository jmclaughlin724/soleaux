"""Schema-preserving ownership graph service, MCP, query, and CLI contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Never, cast

import pytest
from _assertions import object_mapping
from fastmcp import Client
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

import soleaux.authority.governance as governance
import soleaux.authority.resolver as authority_resolver
from soleaux.analysis.service import (
    _OWNERSHIP_RESPONSE_TARGET_BYTES,
    MAX_OWNERSHIP_RESPONSE_BYTES,
    SoleauxService,
)
from soleaux.catalog.reader import CatalogReader
from soleaux.catalog.store import MaterializedRead
from soleaux.contracts.requests import (
    ContextRequest,
    OwnershipRequest,
    OwnershipView,
    SemanticMode,
)
from soleaux.contracts.results import ResponseEnvelope
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.server import create_server
from soleaux.structural.snapshot import RepositorySnapshotter, SnapshotBundle

_INTENT = (
    "Explain one canonical consumer record, its consumer-authored field "
    "relationships, neutral repository evidence, and conflicting or redundant "
    "declarations."
)


def _write_fixture(root: Path) -> None:
    files = {
        "soleaux.toml": (
            'schema_version = "soleaux.config/v1"\n\n'
            "[[governance.sources]]\n"
            'id = "registry"\n'
            'path = "ownership.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Canonical ownership registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            "relationships = [\n"
            '  { field = "Steward", required = true },\n'
            '  { field = "Route", required = true },\n'
            '  { field = "Check", required = true },\n'
            "]\n\n"
            "[[governance.sources]]\n"
            'id = "overrides"\n'
            'path = "ownership.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Override registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            "relationships = [\n"
            '  { field = "Steward" },\n'
            '  { field = "Route" },\n'
            "]\n"
        ),
        "ownership.md": (
            "# Canonical ownership registry\n\n"
            "| Policy | Steward | Route | Check | Flavor |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| policy:fixture | `docs/first.md`, `docs/second.md` | "
            "`.review/fixture.conf` | `tests/test_policy.py` | review-bot |\n"
            "| policy:second | `docs/second.md` | `.review/fixture.conf` | "
            "`tests/test_policy.py` | other-bot |\n"
            "| policy:missing | `docs/missing.md` | `.review/missing.conf` | "
            "`tests/missing.py` | unknown |\n"
            "\n# Override registry\n\n"
            "| Policy | Steward | Route |\n"
            "| --- | --- | --- |\n"
            "| policy:fixture | `docs/second.md` | `.review/fixture.conf` |\n"
        ),
        "docs/first.md": "# First\n",
        "docs/second.md": "# Second\n",
        ".review/fixture.conf": "deny unsafe\n",
        "src/policy.py": "POLICY = True\n",
        "schema.sql": "select 1;\n",
        "unrelated.json": '{"2026":{"value":true}}\n',
        "tests/test_policy.py": "def test_policy(): assert True\n",
        "package.json": '{"scripts":{"verify":"pytest tests/test_policy.py"}}\n',
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def _bundle_with_contents(
    root: Path,
    contents: dict[str, bytes],
) -> SnapshotBundle:
    anchor = root / "anchor.txt"
    anchor.write_text("fixture\n", encoding="utf-8")
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="governance-performance-test",
    ).get("workspace")
    captured = await RepositorySnapshotter(workspace).capture(scope=("anchor.txt",))
    return replace(captured, contents=contents)


def test_ownership_request_has_no_product_specific_platform_filter() -> None:
    schema = OwnershipRequest.model_json_schema()
    properties = schema["properties"]

    assert "platforms" not in properties
    assert "cursor" in properties
    assert "view" in properties
    request = OwnershipRequest(policy="policy:fixture", cursor="opaque")
    assert request.policy == "policy:fixture"
    assert request.cursor == "opaque"
    assert request.view is OwnershipView.DECISIONS
    assert (
        OwnershipRequest.model_validate({"policy": "policy:fixture", "view": "identities"}).view
        is OwnershipView.IDENTITIES
    )


async def test_context_assembles_source_and_authority_evidence_for_one_objective(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    async with SoleauxService.from_root(tmp_path) as service:
        assert service.active_language_server_count == 0
        await service._catalog_indexer.settle()
        response = await service.context(
            ContextRequest(
                objective="review policy fixture steward route and conflicts",
                paths=["ownership.md"],
            )
        )
        assert service.active_language_server_count == 0

    assert response.status.value == "ok"
    assert response.data is not None
    assert response.data.schema_version == "soleaux.context/v1"
    tables = {item.table for item in response.data.items}
    assert "source.context" in tables
    assert {
        "authority.policies",
        "authority.bindings",
        "authority.conflicts",
    } <= tables
    assert {item.table for item in response.data.canonical_owners} >= {"authority.policies"}
    assert {item.table for item in response.data.consumers} >= {"authority.bindings"}
    assert {item.table for item in response.data.conflicts} >= {"authority.conflicts"}
    assert response.suggested_next_requests == []


async def test_one_mcp_call_returns_dynamic_roles_conflicts_and_neutral_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FASTMCP_MCP_CAMELCASE_COMPAT", "false")
    _write_fixture(tmp_path)
    service = SoleauxService.from_root(tmp_path)
    server = create_server(tmp_path, service_factory=lambda: service)

    async with Client(server, mode="auto") as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        protocol_version = client.protocol_version
        await service._catalog_indexer.settle()
        response = await client.call_tool(
            "owners",
            {"request": {"policy": "policy:fixture"}},
        )
        payload = response.structured_content
        assert payload is not None
        assert service.active_language_server_count == 0
        assert service.structural_worker_started

    assert protocol_version in MODERN_PROTOCOL_VERSIONS
    tool = tools["owners"]
    assert tool.description is not None
    assert _INTENT in tool.description
    tool_payload = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    input_schema = object_mapping(cast(object, tool_payload["inputSchema"]))
    input_properties = object_mapping(input_schema["properties"])
    request_schema = object_mapping(input_properties["request"])
    request_properties = object_mapping(request_schema["properties"])
    assert "cursor" in request_properties
    assert tool.annotations is not None
    annotations = tool.annotations.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    assert annotations["readOnlyHint"] is True
    assert annotations["destructiveHint"] is False

    raw_data: object = payload["data"]
    data = object_mapping(raw_data)
    assert data["state"] == "conflicted"
    raw_binding_ids: object = data["binding_ids"]
    binding_ids = object_mapping(raw_binding_ids)
    assert set(binding_ids) == {"check", "route", "steward"}
    assert all(binding_ids.values())
    assert data["missing_roles"] == []
    assert data["evidence_binding_ids"]
    assert data["conflict_ids"]
    assert payload["coverage"]["status"] == "complete"
    assert len(json.dumps(payload).encode("utf-8")) < MAX_OWNERSHIP_RESPONSE_BYTES


async def test_exact_id_and_path_scope_select_the_same_policy(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        by_id = await service.ownership(OwnershipRequest(policy="policy:fixture"))
        by_path = await service.ownership(OwnershipRequest(policy="docs/first.md"))
        docs_only = await service.ownership(
            OwnershipRequest(policy="policy:fixture", paths=["docs"])
        )

    assert by_id.data is not None
    assert by_path.data is not None
    assert by_id.data["policy"]["policy_id"] == "policy:fixture"
    assert by_path.data["policy"]["policy_id"] == "policy:fixture"
    assert docs_only.rows is not None
    assert all(
        str(row["target"]).startswith("docs/")
        for row in docs_only.rows
        if row["table"] in {"authority.bindings", "authority.conflicts"}
    )


async def test_ownership_reads_published_authority_without_request_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_fixture(tmp_path)

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()

        def reject_request_rebuild(*_args: object, **_kwargs: object) -> Never:
            pytest.fail("ownership request rebuilt the analysis frame")

        def reject_authority_resolution(*_args: object, **_kwargs: object) -> Never:
            pytest.fail("ownership request reran the authority resolver")

        with monkeypatch.context() as request_patch:
            request_patch.setattr(service._frames, "build", reject_request_rebuild)
            request_patch.setattr(
                authority_resolver.AuthorityResolver,
                "resolve",
                reject_authority_resolution,
            )
            response = await service.ownership(OwnershipRequest(policy="policy:fixture"))

    assert response.status.value == "ok"
    assert response.data is not None
    assert response.data["policy"]["policy_id"] == "policy:fixture"


async def test_selected_missing_target_degrades_only_its_record(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        selected = await service.ownership(OwnershipRequest(policy="policy:fixture"))
        missing = await service.ownership(OwnershipRequest(policy="policy:missing"))

    assert selected.coverage is not None
    assert selected.coverage.status.value == "complete"
    assert not any("docs/missing.md" in warning for warning in selected.warnings)
    assert missing.coverage is not None
    assert missing.coverage.status.value == "partial"
    assert missing.data is not None
    assert missing.data["state"] == "incomplete"
    assert any("docs/missing.md" in warning for warning in missing.warnings)


async def test_source_scope_returns_every_policy_relationship_in_one_response(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    for relative in (
        ".review/missing.conf",
        "docs/missing.md",
        "tests/missing.py",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        response = await service.ownership(
            OwnershipRequest(
                policy="ownership.md",
                limit=200,
            )
        )

    assert response.coverage is not None
    assert response.coverage.status.value == "complete"
    assert response.data is not None
    assert response.data["state"] == "ambiguous"
    ownerships = response.data["ownerships"]
    assert [record["policy"]["policy_id"] for record in ownerships] == [
        "policy:fixture",
        "policy:missing",
        "policy:second",
    ]
    assert all(record["coverage"]["status"] == "complete" for record in ownerships)
    fixture_record = next(
        record for record in ownerships if record["policy"]["policy_id"] == "policy:fixture"
    )
    assert fixture_record["evidence_binding_ids"]
    assert fixture_record["conflict_ids"]
    assert response.rows is not None
    assert {row["table"] for row in response.rows} == {
        "authority.bindings",
        "authority.conflicts",
        "authority.policies",
    }
    assert {row["policy_id"] for row in response.rows if row["table"] == "authority.policies"} == {
        "policy:fixture",
        "policy:missing",
        "policy:second",
    }


async def test_source_selector_matches_exact_policy_beyond_relationship_graph_limit(
    tmp_path: Path,
) -> None:
    targets = tuple(f"docs/owner-{index:03}.md" for index in range(257))
    (tmp_path / "soleaux.toml").write_text(
        (
            'schema_version = "soleaux.config/v1"\n\n'
            "[[governance.sources]]\n"
            'id = "registry"\n'
            'path = "ownership.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Ownership registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            'relationships = [{ field = "Owner" }]\n'
        ),
        encoding="utf-8",
    )
    (tmp_path / "ownership.md").write_text(
        (
            "# Ownership registry\n\n"
            "| Policy | Owner |\n"
            "| --- | --- |\n"
            f"| policy:large | {', '.join(f'`{target}`' for target in targets)} |\n"
        ),
        encoding="utf-8",
    )
    for target in targets:
        path = tmp_path / target
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Owner\n", encoding="utf-8")

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        by_id_pages = [await service.ownership(OwnershipRequest(policy="policy:large", limit=200))]
        while by_id_pages[-1].next_cursor is not None:
            by_id_pages.append(
                await service.ownership(
                    OwnershipRequest(
                        policy="policy:large",
                        limit=200,
                        cursor=by_id_pages[-1].next_cursor,
                    )
                )
            )
        by_source_pages = [
            await service.ownership(OwnershipRequest(policy="ownership.md", limit=200))
        ]
        while by_source_pages[-1].next_cursor is not None:
            by_source_pages.append(
                await service.ownership(
                    OwnershipRequest(
                        policy="ownership.md",
                        limit=200,
                        cursor=by_source_pages[-1].next_cursor,
                    )
                )
            )

    by_id = by_id_pages[0]
    by_source = by_source_pages[0]
    assert by_id.data is not None
    assert by_source.data is not None
    assert by_id.data["policy"]["policy_id"] == "policy:large"
    assert by_source.data["policy"]["policy_id"] == "policy:large"
    assert by_id.data["state"] == by_source.data["state"]
    assert by_id.data["binding_ids"] == by_source.data["binding_ids"]
    assert by_id.data["total_rows"] == by_source.data["total_rows"]
    assert by_id.data["total_rows"] > 256
    by_id_binding_ids = [
        binding_id
        for page in by_id_pages
        if page.data is not None
        for binding_id in page.data["binding_ids"]["owner"]
    ]
    by_source_binding_ids = [
        binding_id
        for page in by_source_pages
        if page.data is not None
        for binding_id in page.data["binding_ids"]["owner"]
    ]
    assert by_id_binding_ids == by_source_binding_ids
    assert len(by_id_binding_ids) == len(targets)
    assert len(by_id_binding_ids) == len(set(by_id_binding_ids))


async def test_one_mcp_ownership_call_resolves_policy_beyond_initial_authority_page(
    tmp_path: Path,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        (
            'schema_version = "soleaux.config/v1"\n\n'
            "[[governance.sources]]\n"
            'id = "registry"\n'
            'path = "ownership.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Ownership registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            'relationships = [{ field = "Owner" }]\n'
        ),
        encoding="utf-8",
    )
    declarations = "\n".join(f"| policy:{index:04} | `docs/owner.md` |" for index in range(1001))
    (tmp_path / "ownership.md").write_text(
        (f"# Ownership registry\n\n| Policy | Owner |\n| --- | --- |\n{declarations}\n"),
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "owner.md").write_text("# Owner\n", encoding="utf-8")

    service = SoleauxService.from_root(tmp_path)
    server = create_server(tmp_path, service_factory=lambda: service)
    async with Client(server) as client:
        await service._catalog_indexer.settle()
        response = await client.call_tool(
            "owners",
            {"request": {"policy": "policy:1000"}},
        )

    payload = response.structured_content
    assert payload is not None
    assert payload["status"] == "ok"
    data = object_mapping(cast(object, payload["data"]))
    policy = object_mapping(data["policy"])
    assert policy["policy_id"] == "policy:1000"
    assert data["response_truncated"] is False
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["status"] == "complete"


async def test_identity_view_paginates_many_source_policies_and_binds_cursor_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_ids = [f"policy:{index:04d}" for index in range(1001)]
    (tmp_path / "soleaux.toml").write_text(
        (
            'schema_version = "soleaux.config/v1"\n\n'
            "[[governance.sources]]\n"
            'id = "registry"\n'
            'path = "ownership.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Ownership registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            'relationships = [{ field = "Owner", target_kind = "reference" }]\n'
        ),
        encoding="utf-8",
    )
    declarations = "\n".join(f"| {policy_id} | `shared-owner` |" for policy_id in policy_ids)
    (tmp_path / "ownership.md").write_text(
        (f"# Ownership registry\n\n| Policy | Owner |\n| --- | --- |\n{declarations}\n"),
        encoding="utf-8",
    )

    reads: list[
        tuple[
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            str | None,
            int,
            int,
            int,
            int,
        ]
    ] = []
    original_tables = CatalogReader.tables

    def observing_tables(
        reader: CatalogReader,
        workspace_id: str,
        *,
        include_tables: tuple[str, ...],
        path_prefixes: tuple[str, ...] = (),
        policy_ids: tuple[str, ...] = (),
        seed_keys: tuple[str, ...] = (),
        ownership_selector: str | None = None,
        relation_depth: int = 0,
        limit: int,
        offset: int,
    ) -> MaterializedRead:
        result = original_tables(
            reader,
            workspace_id,
            include_tables=include_tables,
            path_prefixes=path_prefixes,
            policy_ids=policy_ids,
            seed_keys=seed_keys,
            ownership_selector=ownership_selector,
            relation_depth=relation_depth,
            limit=limit,
            offset=offset,
        )
        reads.append(
            (
                include_tables,
                path_prefixes,
                policy_ids,
                ownership_selector,
                limit,
                offset,
                len(result.rows),
                result.total_rows,
            )
        )
        return result

    monkeypatch.setattr(CatalogReader, "tables", observing_tables)
    pages: list[ResponseEnvelope] = []
    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        first = await service.ownership(
            OwnershipRequest(
                policy="ownership.md",
                view=OwnershipView.IDENTITIES,
                limit=37,
            )
        )
        pages.append(first)
        assert first.next_cursor is not None
        wrong_view = await service.ownership(
            OwnershipRequest(
                policy="ownership.md",
                view=OwnershipView.DECISIONS,
                limit=37,
                cursor=first.next_cursor,
            )
        )
        cursor = first.next_cursor
        while cursor is not None:
            page = await service.ownership(
                OwnershipRequest(
                    policy="ownership.md",
                    view=OwnershipView.IDENTITIES,
                    limit=37,
                    cursor=cursor,
                )
            )
            pages.append(page)
            cursor = page.next_cursor
        exact = await service.ownership(OwnershipRequest(policy=policy_ids[-1], limit=37))

    assert wrong_view.error is not None
    assert wrong_view.error.error_type == "invalid_cursor"
    assert exact.error is None
    assert exact.data is not None
    assert exact.data["policy"]["policy_id"] == policy_ids[-1]
    assert len(pages) > 1
    assert all(
        len(page.model_dump_json().encode("utf-8")) < MAX_OWNERSHIP_RESPONSE_BYTES for page in pages
    )
    assert all(
        page.coverage is not None and page.coverage.status.value == "truncated"
        for page in pages[:-1]
    )
    final = pages[-1]
    assert final.coverage is not None
    assert final.coverage.status.value == "complete"
    assert final.next_cursor is None

    discovered_ids: list[str] = []
    for page in pages:
        assert page.data is not None
        assert page.rows is not None
        assert page.data["view"] == OwnershipView.IDENTITIES.value
        assert page.data["returned_rows"] == len(page.rows)
        row_identities: list[dict[str, str]] = []
        for row in page.rows:
            policy_id = row["policy_id"]
            source_path = row["source_path"]
            assert isinstance(policy_id, str)
            assert isinstance(source_path, str)
            row_identities.append(
                {
                    "policy_id": policy_id,
                    "source_path": source_path,
                }
            )
        assert all(row["table"] == "authority.policies" for row in page.rows)
        assert page.data["identities"] == row_identities
        discovered_ids.extend(identity["policy_id"] for identity in row_identities)

    assert sorted(discovered_ids) == policy_ids
    assert len(discovered_ids) == len(set(discovered_ids))
    assert pages[0].data is not None
    assert pages[0].data["total_rows"] == len(policy_ids)
    identity_reads = [
        read
        for read in reads
        if read[0] == ("authority.policies",)
        and not read[1]
        and not read[2]
        and read[3] == "ownership.md"
        and read[4] == 37
    ]
    assert len(identity_reads) == len(pages)
    assert all(read[4] == 37 for read in identity_reads)
    assert all(read[6] <= 37 for read in identity_reads)
    assert all(read[7] == len(policy_ids) for read in identity_reads)
    assert max(read[6] for read in identity_reads) < len(policy_ids)
    exact_reads = [read for read in reads if read[3] == policy_ids[-1]]
    assert [read[0] for read in exact_reads] == [SoleauxService.AUTHORITY_READ_TABLES]


async def test_unavailable_canonical_source_parser_is_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_fixture(tmp_path)

    def unavailable_markdown_tables(_text: str) -> tuple[tuple[()], str]:
        return (), "Markdown AST parser is unavailable"

    monkeypatch.setattr(
        governance,
        "_markdown_tables",
        unavailable_markdown_tables,
    )

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        response = await service.ownership(OwnershipRequest(policy="policy:fixture"))

    assert response.coverage is not None
    assert response.coverage.status.value == "partial"
    assert any("Markdown AST parser is unavailable" in warning for warning in response.warnings)


async def test_ownership_pages_two_thousand_bindings_without_skips(
    tmp_path: Path,
) -> None:
    targets = [f"owner-{index:04d}" for index in range(2000)]
    links = "<br>".join(f"`{target}`" for target in targets)
    files = {
        "soleaux.toml": (
            'schema_version = "soleaux.config/v1"\n\n'
            "[[governance.sources]]\n"
            'id = "registry"\n'
            'path = "docs/registry.md"\n'
            'format = "markdown"\n'
            'selector = { kind = "markdown_table", '
            'heading = "Canonical registry", occurrence = 1 }\n'
            'identity_field = "Policy"\n'
            'relationships = [{ field = "Links", target_kind = "reference" }]\n'
        ),
        "docs/registry.md": f"""\
# Canonical registry

| Policy | Links |
| --- | --- |
| policy:large | {links} |
""",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        first = await service.ownership(OwnershipRequest(policy="policy:large", limit=200))
        second = await service.ownership(OwnershipRequest(policy="policy:large", limit=200))
        pages = [first]
        cursor = first.next_cursor
        while cursor is not None:
            page = await service.ownership(
                OwnershipRequest(
                    policy="policy:large",
                    limit=200,
                    cursor=cursor,
                )
            )
            pages.append(page)
            cursor = page.next_cursor

    first_bytes = first.model_dump_json().encode("utf-8")
    second_bytes = second.model_dump_json().encode("utf-8")
    assert len(first_bytes) <= _OWNERSHIP_RESPONSE_TARGET_BYTES
    assert len(first_bytes) < MAX_OWNERSHIP_RESPONSE_BYTES
    assert len(second_bytes) < MAX_OWNERSHIP_RESPONSE_BYTES
    assert first.data is not None
    assert second.data is not None
    assert first.data["response_truncated"] is True
    assert first.data["returned_rows"] < first.data["total_rows"]
    assert first.data["returned_rows"] < 200
    assert first.data["returned_rows"] == second.data["returned_rows"]
    assert first.next_cursor is not None
    assert first.coverage is not None
    assert first.coverage.status.value == "truncated"
    assert "ownership response row or byte limit reached" in first.coverage.omitted_reasons
    assert len(pages) > 1
    assert all(
        len(page.model_dump_json().encode("utf-8")) < MAX_OWNERSHIP_RESPONSE_BYTES for page in pages
    )
    assert all(
        page.coverage is not None and page.coverage.status.value == "truncated"
        for page in pages[:-1]
    )
    assert all(page.data is not None and page.data["returned_rows"] > 0 for page in pages[:-1])
    final = pages[-1]
    assert final.next_cursor is None
    assert final.coverage is not None
    assert final.coverage.status.value == "complete"
    assert "ownership response row or byte limit reached" not in final.coverage.omitted_reasons
    assert final.data is not None
    assert final.data["response_truncated"] is False

    page_rows = [row for page in pages for row in page.rows or []]
    assert len(page_rows) == first.data["total_rows"]
    serialized_rows = [json.dumps(row, sort_keys=True) for row in page_rows]
    assert len(serialized_rows) == len(set(serialized_rows))
    assert {
        row["target"]
        for row in page_rows
        if row["table"] == "authority.bindings" and row.get("binding_kind") == "declared"
    } == set(targets)
    page_binding_ids = [
        binding_id
        for page in pages
        if page.data is not None
        for binding_id in page.data["binding_ids"]["links"]
    ]
    declared_binding_ids = [
        row["binding_id"]
        for row in page_rows
        if row["table"] == "authority.bindings" and row.get("binding_kind") == "declared"
    ]
    assert page_binding_ids == declared_binding_ids
    assert len(page_binding_ids) == len(targets)
    assert len(page_binding_ids) == len(set(page_binding_ids))


async def test_ownership_decisions_fail_closed_above_sqlite_graph_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_fixture(tmp_path)
    monkeypatch.setattr("soleaux.analysis.service._MAX_OWNERSHIP_GRAPH_ROWS", 1)

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        response = await service.ownership(
            OwnershipRequest(
                policy="policy:fixture",
                view=OwnershipView.DECISIONS,
            )
        )

    assert response.error is not None
    assert response.error.error_type == "ownership_graph_too_large"
    assert response.data is None


async def test_ownership_cursor_rejects_invalid_mismatch_drift_and_expiry(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    service = SoleauxService.from_root(tmp_path, cursor_ttl_seconds=60)
    async with service:
        await service._catalog_indexer.settle()
        first = await service.ownership(OwnershipRequest(policy="policy:fixture", limit=1))
        assert first.next_cursor is not None

        invalid = await service.ownership(
            OwnershipRequest(
                policy="policy:fixture",
                limit=1,
                cursor="not-a-cursor",
            )
        )
        mismatched_requests = (
            OwnershipRequest(
                policy="policy:second",
                limit=1,
                cursor=first.next_cursor,
            ),
            OwnershipRequest(
                policy="policy:fixture",
                paths=["docs"],
                limit=1,
                cursor=first.next_cursor,
            ),
            OwnershipRequest(
                policy="policy:fixture",
                semantic_mode=SemanticMode.SYNTAX_ONLY,
                limit=1,
                cursor=first.next_cursor,
            ),
            OwnershipRequest(
                policy="policy:fixture",
                view=OwnershipView.IDENTITIES,
                limit=1,
                cursor=first.next_cursor,
            ),
            OwnershipRequest(
                policy="policy:fixture",
                limit=2,
                cursor=first.next_cursor,
            ),
        )
        mismatched = [await service.ownership(request) for request in mismatched_requests]

        (tmp_path / "docs" / "first.md").write_text(
            "# First changed\n",
            encoding="utf-8",
        )
        await service._catalog_indexer.refresh(
            service._workspaces.get(None),
            force=True,
        )
        drifted = await service.ownership(
            OwnershipRequest(
                policy="policy:fixture",
                limit=1,
                cursor=first.next_cursor,
            )
        )

    for response in (invalid, *mismatched):
        assert response.error is not None
        assert response.error.error_type == "invalid_cursor"
    assert drifted.error is not None
    assert drifted.error.error_type == "cursor_drift"

    expiring = SoleauxService.from_root(tmp_path, cursor_ttl_seconds=0.001)
    async with expiring:
        await expiring._catalog_indexer.settle()
        expiring_first = await expiring.ownership(
            OwnershipRequest(policy="policy:fixture", limit=1)
        )
        assert expiring_first.next_cursor is not None
        await asyncio.sleep(0.01)
        expired = await expiring.ownership(
            OwnershipRequest(
                policy="policy:fixture",
                limit=1,
                cursor=expiring_first.next_cursor,
            )
        )

    assert expired.error is not None
    assert expired.error.error_type == "invalid_cursor"


async def test_ownership_cursor_rejects_same_generation_reordered_republication(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        first = await service.ownership(OwnershipRequest(policy="policy:fixture", limit=1))
        assert first.next_cursor is not None

        workspace_id = service._workspaces.get(None).workspace_id
        store = service._frames.existing_catalog_store(workspace_id)
        publication = service._catalog_indexer._publications[workspace_id]
        assert store is not None
        materialized = store.read_materialized(workspace_id, limit=2_147_483_647)
        reordered_rows = tuple(reversed(tuple(item.row for item in materialized.rows)))
        store.publish_materialized(
            materialized.frame,
            generation=materialized.generation,
            source_fingerprint=materialized.source_fingerprint,
            rows=reordered_rows,
            kinds={item.row.evidence.evidence_id: item.kind for item in materialized.rows},
            relationships=service._catalog_indexer._relationships(reordered_rows),
            retained_generations=service._config.catalog.retained_generations,
            enrichment_settled=publication.enrichment_complete,
            attempted_tables=publication.attempted_tables,
        )
        republished = store.read_materialized(workspace_id, limit=1)
        assert republished.generation == materialized.generation
        assert republished.publication_revision > materialized.publication_revision

        drifted = await service.ownership(
            OwnershipRequest(
                policy="policy:fixture",
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert drifted.error is not None
    assert drifted.error.error_type == "cursor_drift"


async def test_structured_references_scan_only_repository_shaped_wildcards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents = {
        "references.json": json.dumps(
            {
                "literal": "docs/real.md",
                "patterns": [
                    ".codex/rules/*.rules",
                    "docs/*.md",
                    "apps/**",
                    "**/*.ts",
                    "*.json",
                ],
                "non_paths": [
                    "*",
                    "**",
                    "workspace:*",
                    "bundle:**",
                    "repo:apps/**",
                    "1.*",
                ],
            }
        ).encode(),
        ".codex/rules/example.rules": b"fixture\n",
        "apps/web/page.ts": b"export {};\n",
        "docs/real.md": b"# Real\n",
    }
    bundle = await _bundle_with_contents(tmp_path, contents)
    resolved_patterns: list[str] = []
    resolve_paths = governance.resolve_paths

    def count_resolutions(
        pattern: str,
        candidates: frozenset[str],
    ) -> tuple[str, ...]:
        resolved_patterns.append(pattern)
        return resolve_paths(pattern, candidates)

    monkeypatch.setattr(governance, "resolve_paths", count_resolutions)

    references = governance._structured_references(bundle, excluded_paths=set())

    assert references["references.json"] == (
        "**/*.ts",
        "*.json",
        ".codex/rules/*.rules",
        "apps/**",
        "docs/*.md",
        "docs/real.md",
    )
    assert sorted(resolved_patterns) == [
        "**/*.ts",
        "*.json",
        ".codex/rules/*.rules",
        "apps/**",
        "docs/*.md",
    ]


async def test_structured_reference_resolution_is_cached_at_high_cardinality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured_files = {
        f"records/{index:03d}.json": json.dumps(
            {
                "first": "docs/*.md",
                "duplicate": "docs/*.md",
                "dependency": "*",
                "version": "1.*",
            }
        ).encode()
        for index in range(100)
    }
    candidate_files = {f"generated/{index:04d}.txt": b"fixture\n" for index in range(3000)}
    bundle = await _bundle_with_contents(
        tmp_path,
        {
            **structured_files,
            **candidate_files,
            "docs/real.md": b"# Real\n",
        },
    )
    resolved_patterns: list[str] = []
    resolve_paths = governance.resolve_paths

    def count_resolutions(
        pattern: str,
        candidates: frozenset[str],
    ) -> tuple[str, ...]:
        resolved_patterns.append(pattern)
        return resolve_paths(pattern, candidates)

    monkeypatch.setattr(governance, "resolve_paths", count_resolutions)

    references = governance._structured_references(bundle, excluded_paths=set())

    assert len(bundle.contents) == 3101
    assert len(references) == len(structured_files)
    assert set(references.values()) == {("docs/*.md",)}
    assert resolved_patterns == ["docs/*.md"]
