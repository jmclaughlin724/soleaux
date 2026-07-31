"""Zero-config discovery, authorization, and process-ephemeral cursors."""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import sys
import typing

import _assertions
import _processes
import fastmcp
import fastmcp.client.transports
import pytest

import soleaux.analysis.service
import soleaux.contracts.config
import soleaux.contracts.cursor
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.workspace
import soleaux.server
from scripts.zero_mcp_fixture import (
    canonical_bytes,
    canonical_projection,
    canonical_projection_from_client,
)

ZERO_MCP_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "contracts" / "d019-zero-mcp.json"
ZERO_MCP_SHA256 = "d87930cd9fb839c22581c90da5eee11003caae669c04b20e73f345cf8cbf8ed0"


def _unexpected_mcp_boundary(*_args: object, **_kwargs: object) -> typing.Never:
    raise AssertionError("zero-mcp construction must not create a backend boundary")


def test_missing_and_empty_config_match_from_repo_and_nested_directory(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    (tmp_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

    missing_root = soleaux.analysis.service.SoleauxService.discover_root(nested)
    assert missing_root == tmp_path
    missing = soleaux.contracts.config.load_config(missing_root).public_payload()
    assert missing == {
        "catalog": {
            "mode": "memory",
            "retained_generations": 2,
            "max_disk_size_mb": 512,
        },
        "governance": {"sources": []},
        "health": {
            "logs_retention_days": 7,
            "temp_retention_hours": 24,
            "archived_sessions_retention_days": 14,
            "max_logs_db_size_mb": 500,
        },
        "lsp": {"diagnostic_timeout_seconds": 5.0},
        "postgresql": {"lane_roots": {}},
        "providers": {},
        "schema_version": "soleaux.config/v1",
        "structural": {
            "backend": "python",
            "project_config": None,
            "languages": {},
        },
        "skills": {
            "enabled": False,
            "roots": [],
            "reload": False,
            "main_file_name": "SKILL.md",
            "supporting_files": "template",
        },
        "workspaces": [],
    }

    (tmp_path / "soleaux.toml").write_text("", encoding="utf-8")
    empty = soleaux.contracts.config.load_config(
        soleaux.analysis.service.SoleauxService.discover_root(tmp_path)
    ).public_payload()
    assert empty == missing


@pytest.mark.parametrize(
    "config_content",
    [
        pytest.param(None, id="missing"),
        pytest.param(b"", id="empty"),
        pytest.param(b"# comment only\n", id="comment-only"),
        pytest.param(b"mcp = {}\n", id="explicit-empty-mcp"),
    ],
)
async def test_zero_mcp_variants_match_the_canonical_projection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    config_content: bytes | None,
) -> None:
    if config_content is not None:
        (tmp_path / "soleaux.toml").write_bytes(config_content)
    for target in (
        "fastmcp.server.providers.proxy.ProxyClient",
        "fastmcp.server.providers.proxy.StatefulProxyClient",
        "fastmcp.client.transports.StdioTransport",
        "fastmcp.server.providers.proxy.ProxyProvider",
    ):
        monkeypatch.setattr(target, _unexpected_mcp_boundary)
    actual_bytes = canonical_bytes(
        await canonical_projection(soleaux.server.create_server(tmp_path))
    )
    expected_bytes = ZERO_MCP_FIXTURE.read_bytes()

    assert hashlib.sha256(expected_bytes).hexdigest() == ZERO_MCP_SHA256
    assert canonical_bytes(json.loads(expected_bytes)) == expected_bytes
    assert actual_bytes == expected_bytes


def test_zero_mcp_fixture_normalizes_platform_fts_availability() -> None:
    fixture = json.loads(ZERO_MCP_FIXTURE.read_bytes())
    about_resource = next(
        resource
        for resource in fixture["resources"]
        if resource["definition"]["uri"] == "soleaux://about"
    )
    about = json.loads(about_resource["contents"][0]["text"])

    assert about["storage"]["fts_available"] == "<fts-available>"


async def test_real_stdio_zero_mcp_matches_the_canonical_fixture(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".git").mkdir()
    transport = fastmcp.client.transports.StdioTransport(
        command=sys.executable,
        args=["-m", "soleaux", "--root", str(tmp_path)],
        env=_processes.minimum_environment(),
        cwd=str(tmp_path),
        keep_alive=False,
    )

    async with fastmcp.Client(transport, init_timeout=10, timeout=10) as client:
        actual_bytes = canonical_bytes(await canonical_projection_from_client(client))

    assert actual_bytes == ZERO_MCP_FIXTURE.read_bytes()


async def test_disabled_backends_never_construct_an_mcp_boundary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for target in (
        "fastmcp.server.providers.proxy.ProxyClient",
        "fastmcp.server.providers.proxy.StatefulProxyClient",
        "fastmcp.client.transports.StdioTransport",
        "fastmcp.server.providers.proxy.ProxyProvider",
    ):
        monkeypatch.setattr(target, _unexpected_mcp_boundary)
    config = soleaux.contracts.config.ResolvedConfig(
        mcp={
            "disabled": soleaux.contracts.config.McpBackendConfig(
                command=["must-not-run"], enabled=False
            ),
        }
    )

    async with fastmcp.Client(soleaux.server.create_server(tmp_path, config=config)) as client:
        tools = await client.list_tools()

    assert len(tools) == 10


async def test_multiple_roots_require_selection_and_reject_aliases(
    tmp_path: pathlib.Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    first.mkdir()
    second.mkdir()
    third.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(first, target_is_directory=True)

    service = soleaux.analysis.service.SoleauxService.from_launch(
        [("first", first), ("second", second)]
    )
    async with service:
        missing = await service.search(soleaux.contracts.requests.SearchRequest(query="value"))
        selected = await service.search(
            soleaux.contracts.requests.SearchRequest(query="value", workspace_id="second")
        )
        rejected = await service.search(
            soleaux.contracts.requests.SearchRequest(query="value", workspace_id="third")
        )

    assert missing.status is soleaux.contracts.results.ResultStatus.ERROR
    assert selected.status is soleaux.contracts.results.ResultStatus.OK
    assert rejected.status is soleaux.contracts.results.ResultStatus.ERROR
    with _assertions.raises_with_message(
        soleaux.contracts.workspace.UnauthorizedRootError, "duplicate resolved launch root"
    ):
        soleaux.analysis.service.SoleauxService.from_launch([("first", first), ("alias", alias)])


async def test_cursor_rejects_argument_change_drift_expiry_and_restart(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "main.py"
    source.write_text("def target_first(): pass\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def target_second(): pass\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path, cursor_ttl_seconds=60)
    async with service:
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(query="target", limit=1)
        )
        assert first.status is soleaux.contracts.results.ResultStatus.OK
        assert first.next_cursor is not None

        changed_args = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="different", limit=1, cursor=first.next_cursor
            )
        )
        assert changed_args.status is soleaux.contracts.results.ResultStatus.ERROR
        assert changed_args.error is not None
        assert changed_args.error.error_type == "invalid_cursor"

        source.write_text(
            "def target_first(): pass\n\ndef target_third(): pass\n",
            encoding="utf-8",
        )
        still_published = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="target", limit=1, cursor=first.next_cursor
            )
        )
        assert still_published.status is soleaux.contracts.results.ResultStatus.OK

        await service._catalog_indexer.refresh(
            service._workspaces.get(None),
            force=True,
        )
        drifted = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="target", limit=1, cursor=first.next_cursor
            )
        )
        assert drifted.status is soleaux.contracts.results.ResultStatus.ERROR
        assert drifted.error is not None
        assert drifted.error.error_type == "cursor_drift"

    restarted = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    async with restarted:
        old_process = await restarted.search(
            soleaux.contracts.requests.SearchRequest(
                query="target", limit=1, cursor=first.next_cursor
            )
        )
    assert old_process.status is soleaux.contracts.results.ResultStatus.ERROR
    assert old_process.error is not None
    assert old_process.error.error_type == "invalid_cursor"

    expiring = soleaux.analysis.service.SoleauxService.from_root(tmp_path, cursor_ttl_seconds=0.001)
    async with expiring:
        expiring_first = await expiring.search(
            soleaux.contracts.requests.SearchRequest(query="target", limit=1)
        )
        assert expiring_first.next_cursor is not None
        await asyncio.sleep(0.01)
        expired = await expiring.search(
            soleaux.contracts.requests.SearchRequest(
                query="target", limit=1, cursor=expiring_first.next_cursor
            )
        )
    assert expired.status is soleaux.contracts.results.ResultStatus.ERROR
    assert expired.error is not None
    assert expired.error.error_type == "invalid_cursor"


async def test_cursor_rejects_same_generation_republication(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "main.py").write_text("def target_first(): pass\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def target_second(): pass\n", encoding="utf-8")
    service = soleaux.analysis.service.SoleauxService.from_root(tmp_path)
    async with service:
        first = await service.search(
            soleaux.contracts.requests.SearchRequest(query="target", limit=1)
        )
        assert first.next_cursor is not None

        workspace_id = service._workspaces.get(None).workspace_id
        store = service._frames.existing_catalog_store(workspace_id)
        publication = service._catalog_indexer._publications[workspace_id]
        assert store is not None
        materialized = store.read_materialized(workspace_id, limit=100_000)
        rows = tuple(item.row for item in materialized.rows)
        store.publish_materialized(
            materialized.frame,
            generation=materialized.generation,
            source_fingerprint=materialized.source_fingerprint,
            rows=rows,
            kinds={item.row.evidence.evidence_id: item.kind for item in materialized.rows},
            relationships=service._catalog_indexer._relationships(rows),
            retained_generations=service._config.catalog.retained_generations,
            enrichment_settled=publication.enrichment_complete,
            attempted_tables=publication.attempted_tables,
        )
        assert (
            store.read_materialized(workspace_id, limit=1).publication_revision
            > materialized.publication_revision
        )

        drifted = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="target",
                limit=1,
                cursor=first.next_cursor,
            )
        )

    assert drifted.error is not None
    assert drifted.error.error_type == "cursor_drift"


async def test_cursor_registry_sweeps_expired_tokens_and_bounds_active_churn() -> None:
    codec = soleaux.analysis.service._CursorCodec(ttl_seconds=0.001, max_states=3)

    def payload(offset: int) -> soleaux.contracts.cursor.CursorPayload:
        return soleaux.contracts.cursor.CursorPayload(
            process_epoch=codec.process_epoch,
            workspace_id="workspace",
            snapshot_id="snapshot",
            query_digest="query",
            limit=1,
            offset=offset,
        )

    tokens = [
        codec.encode(
            payload(offset),
            catalog_generation=1,
            publication_revision=1,
        )
        for offset in range(1, 5)
    ]
    assert len(codec._states) == 3
    with pytest.raises(soleaux.analysis.service.CursorError):
        codec.decode(tokens[0])
    assert codec.decode(tokens[-1])[0].offset == 4

    await asyncio.sleep(0.01)
    current = codec.encode(
        payload(5),
        catalog_generation=1,
        publication_revision=1,
    )
    assert list(codec._states) == [current]
    with pytest.raises(soleaux.analysis.service.CursorError):
        codec.decode(tokens[-1])
