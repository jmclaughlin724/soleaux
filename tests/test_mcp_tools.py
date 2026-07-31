"""All MCP handlers are thin adapters over one lifespan-owned service."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from typing import Any

import _assertions
import _processes
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StdioTransport
from fastmcp.resources import resource

import soleaux.server
from soleaux.contracts.context import (
    ContextGap,
    ContextSection,
    TaskContextItem,
    TaskContextPacket,
)

CATALOG_READY_ATTEMPTS = 100
CATALOG_READY_DELAY_SECONDS = 0.05


def _request_payload(result: CallToolResult) -> dict[str, object]:
    return _assertions.object_mapping(result.structured_content)


def _rows(payload: dict[str, object]) -> list[dict[str, object]]:
    return [_assertions.object_mapping(row) for row in _assertions.object_list(payload["rows"])]


def _gap_codes(value: object) -> list[str]:
    codes: list[str] = []
    for raw_gap in _assertions.object_list(value):
        gap = _assertions.object_mapping(raw_gap)
        code = gap.get("code")
        assert isinstance(code, str)
        codes.append(code)
    return codes


async def _search_until_rows(
    client: Client[Any],
    request: dict[str, object],
) -> dict[str, object]:
    payload: dict[str, object] = {}
    for _ in range(CATALOG_READY_ATTEMPTS):
        payload = _request_payload(
            await client.call_tool(
                "search",
                {"request": request},
            )
        )
        assert payload["status"] == "ok"
        if _rows(payload):
            return payload
        await asyncio.sleep(CATALOG_READY_DELAY_SECONDS)
    return payload


def test_context_renderer_preserves_semantic_indexes_within_the_byte_budget() -> None:
    owner = TaskContextItem(
        table="authority.owners",
        section=ContextSection.CANONICAL_OWNER,
        identity="owner:context",
        summary="The canonical context owner.",
        data={"detail": "x" * 16_384},
        evidence_id="evidence-owner",
        path="tools/soleaux/src/soleaux/server.py",
        start_line=1,
        end_line=1,
        relation_distance=0,
    )
    packet = TaskContextPacket(
        objective="é" * 32_768,
        retrieval_engine="sqlite-fts5",
        canonical_owners=(owner,),
        gaps=(
            ContextGap(
                code="required_gap",
                message="A required coverage gap must remain visible.",
            ),
        ),
        ranked_candidate_count=1,
        related_fact_count=0,
        returned_item_count=1,
        coverage_complete=False,
    )

    rendered = soleaux.server._render_task_context(packet, max_bytes=4_096)

    assert len(rendered.encode("utf-8")) <= 4_096
    assert "`required_gap`" in rendered
    assert "## Section index" in rendered
    assert "- Canonical owners: 1" in rendered
    assert soleaux.server._RENDER_TRUNCATION_NOTICE in rendered


def test_context_renderer_orders_semantic_sections_before_gap_detail() -> None:
    def item(
        section: ContextSection,
        table: str,
        identity: str,
    ) -> TaskContextItem:
        return TaskContextItem(
            table=table,
            section=section,
            identity=identity,
            summary=f"summary for {identity}",
            data={},
            evidence_id=identity,
            path="src/mod.py",
            start_line=1,
            end_line=1,
            relation_distance=0,
        )

    packet = TaskContextPacket(
        objective="explain the renderer",
        retrieval_engine="sqlite-fts5",
        sources=(item(ContextSection.SOURCE, "source.context", "ev-src"),),
        canonical_owners=(item(ContextSection.CANONICAL_OWNER, "authority.owners", "ev-own"),),
        validation_routes=(item(ContextSection.VALIDATION_ROUTE, "repository.scripts", "ev-val"),),
        gaps=tuple(
            ContextGap(code="coverage_omission", message=f"omission {index}") for index in range(40)
        ),
        ranked_candidate_count=3,
        related_fact_count=2,
        returned_item_count=3,
        coverage_complete=False,
    )

    rendered = soleaux.server._render_task_context(packet, max_bytes=65_535)

    intro = rendered.index("# Soleaux task context")
    index = rendered.index("## Section index")
    objective = rendered.index("## Objective")
    profile = rendered.index("## Retrieval profile")
    gaps = rendered.index("## Coverage gaps (40)")
    owners = rendered.index("## Canonical owners (1)")
    assert intro < index < objective < profile < gaps < owners
    assert rendered.count("- `coverage_omission`") == 32
    assert "8 further gaps omitted from this rendering" in rendered
    assert "- Canonical owners: 1" in rendered
    assert "- Validation routes: 1" in rendered


async def test_in_memory_tools_execute_the_service_workflow(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    async with Client(soleaux.server.create_server(tmp_path)) as client:
        described = _request_payload(await client.call_tool("describe", {"request": {}}))
        searched = _request_payload(
            await client.call_tool(
                "search",
                {"request": {"query": "answer"}},
            )
        )
        context = _request_payload(
            await client.call_tool(
                "context",
                {
                    "request": {
                        "objective": "find the answer implementation",
                        "paths": ["main.py"],
                    }
                },
            )
        )
        queried = _request_payload(
            await client.call_tool(
                "query",
                {"request": {"include_tables": ["repository.files"]}},
            )
        )
        unsupported_semantic_query = _request_payload(
            await client.call_tool(
                "query",
                {"request": {"include_tables": ["semantic.symbols"]}},
            )
        )
        ownership = _request_payload(
            await client.call_tool(
                "owners",
                {"request": {"policy": "policy:none-declared"}},
            )
        )
        navigated = _request_payload(
            await client.call_tool(
                "navigate",
                {
                    "request": {
                        "operation": "definition",
                        "path": "main.py",
                        "line": 1,
                        "column": 5,
                        "semantic_mode": "syntax_only",
                    }
                },
            )
        )
        inspected = _request_payload(
            await client.call_tool(
                "inspect",
                {
                    "request": {
                        "operation": "diagnostics",
                        "path": "main.py",
                        "line": 1,
                        "column": 1,
                        "semantic_mode": "syntax_only",
                    }
                },
            )
        )
        preview = _request_payload(
            await client.call_tool(
                "preview",
                {
                    "request": {
                        "path": "main.py",
                        "line": 1,
                        "column": 5,
                        "operation": "rename",
                        "new_name": "renamed",
                        "semantic_mode": "syntax_only",
                    }
                },
            )
        )

    assert described["status"] == "ok"
    assert searched["status"] == "ok"
    assert context["status"] == "ok"
    assert queried["status"] == "ok"
    assert unsupported_semantic_query["status"] == "ok"
    assert unsupported_semantic_query["rows"] == []
    semantic_coverage = unsupported_semantic_query["coverage"]
    assert isinstance(semantic_coverage, dict)
    assert semantic_coverage["status"] == "unsupported"
    assert ownership["status"] == "ok"
    assert navigated["status"] == "ok"
    assert inspected["status"] == "ok"
    assert preview["status"] == "error"
    error = preview["error"]
    assert isinstance(error, dict)
    assert error["error_type"] == "preview_failed"


async def test_context_resolves_one_requested_fastmcp_resource(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("answer = 42\n", encoding="utf-8")

    @resource(
        "reference://canonical/task",
        name="canonical-task",
        mime_type="text/markdown",
    )
    def canonical_task() -> str:
        return "# Canonical task\n\nThe answer owner is `main.py`."

    server = soleaux.server.create_server(tmp_path)
    server.add_resource(canonical_task)
    async with Client(server) as client:
        result = await client.call_tool(
            "context",
            {
                "request": {
                    "objective": "locate the answer owner",
                    "resource_uris": ["reference://canonical/task"],
                }
            },
        )
        missing_result = await client.call_tool(
            "context",
            {
                "request": {
                    "objective": "locate the answer owner",
                    "resource_uris": ["reference://canonical/missing"],
                }
            },
        )

    payload = _request_payload(result)
    data = _assertions.object_mapping(payload["data"])
    references = _assertions.object_list(data["external_references"])
    assert references == [
        {
            "content": "# Canonical task\n\nThe answer owner is `main.py`.",
            "error": None,
            "media_type": "text/markdown",
            "sha256": "fcb4c7eb9ff69f7d99bc28560964d93046f11d8292f355a432283e44bf5dc534",
            "title": None,
            "truncated": False,
            "uri": "reference://canonical/task",
        }
    ]
    assert result.content
    human = result.content[0]
    assert getattr(human, "type", None) == "text"
    text = getattr(human, "text", "")
    assert text.startswith("# Soleaux task context")
    assert "<untrusted-reference" in text
    assert not text.lstrip().startswith("{")
    missing_payload = _request_payload(missing_result)
    missing_data = _assertions.object_mapping(missing_payload["data"])
    assert _gap_codes(missing_data["gaps"]).count("resource_unavailable") == 1


async def test_context_bounds_configured_resources_by_utf8_bytes(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("answer = 42\n", encoding="utf-8")
    full_content = "é" * 32_769

    @resource(
        "reference://canonical/oversized",
        name="oversized-reference",
        mime_type="text/plain",
    )
    def oversized_reference() -> str:
        return full_content

    server = soleaux.server.create_server(tmp_path)
    server.add_resource(oversized_reference)
    async with Client(server) as client:
        result = await client.call_tool(
            "context",
            {
                "request": {
                    "max_bytes": 262_144,
                    "objective": "read the configured reference",
                    "resource_uris": ["reference://canonical/oversized"],
                }
            },
        )

    payload = _request_payload(result)
    data = _assertions.object_mapping(payload["data"])
    references = _assertions.object_list(data["external_references"])
    assert references == [
        {
            "content": "é" * 32_768,
            "error": None,
            "media_type": "text/plain",
            "sha256": hashlib.sha256(full_content.encode()).hexdigest(),
            "title": None,
            "truncated": True,
            "uri": "reference://canonical/oversized",
        }
    ]
    assert _gap_codes(data["gaps"]).count("resource_content_limit") == 1


async def test_symbol_search_filters_scope_and_bound_rows(
    tmp_path: Path,
) -> None:
    declarations = "".join(f"noise_{index} = {index}\n" for index in range(101))
    (tmp_path / "main.py").write_text(
        (
            f"{declarations}late_unique_symbol = 1\nlate_match_one = 1\n"
            "late_match_two = 2\nscoped_match = 3\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "excluded.py").write_text("scoped_match = 4\n", encoding="utf-8")
    async with Client(soleaux.server.create_server(tmp_path)) as client:
        searched = await _search_until_rows(
            client,
            {
                "query": "LATE_UNIQUE",
                "kinds": ["symbol"],
                "semantic_mode": "syntax_only",
            },
        )
        scoped = _request_payload(
            await client.call_tool(
                "search",
                {
                    "request": {
                        "query": "scoped_match",
                        "kinds": ["symbol"],
                        "semantic_mode": "syntax_only",
                        "paths": ["main.py"],
                    }
                },
            )
        )
        limited = _request_payload(
            await client.call_tool(
                "search",
                {
                    "request": {
                        "query": "late_match",
                        "kinds": ["symbol"],
                        "semantic_mode": "syntax_only",
                        "limit": 1,
                    }
                },
            )
        )

    assert searched["status"] == "ok"
    assert [row["name"] for row in _rows(searched)] == ["late_unique_symbol"]
    coverage = searched["coverage"]
    assert isinstance(coverage, dict)
    assert coverage["status"] == "complete"

    assert scoped["status"] == "ok"
    assert [row["path"] for row in _rows(scoped)] == ["main.py"]

    assert limited["status"] == "ok"
    assert len(_rows(limited)) == 1
    assert limited["next_cursor"] is not None
    typed_limited = _assertions.object_mapping(limited["coverage"])
    assert typed_limited["status"] == "truncated"
    omitted = _assertions.string_list(typed_limited["omitted_reasons"])
    assert "search row limit reached" in omitted


async def test_query_cursor_pages_one_snapshot_and_rejects_argument_drift(
    tmp_path: Path,
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(f"{name[0]} = 1\n", encoding="utf-8")

    async with Client(soleaux.server.create_server(tmp_path)) as client:
        first = _request_payload(
            await client.call_tool(
                "query",
                {
                    "request": {
                        "include_tables": ["repository.files"],
                        "limit": 1,
                    }
                },
            )
        )
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        second = _request_payload(
            await client.call_tool(
                "query",
                {
                    "request": {
                        "include_tables": ["repository.files"],
                        "limit": 1,
                        "cursor": cursor,
                    }
                },
            )
        )
        drifted = _request_payload(
            await client.call_tool(
                "query",
                {
                    "request": {
                        "include_tables": ["repository.projects"],
                        "limit": 1,
                        "cursor": cursor,
                    }
                },
            )
        )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert _rows(first)[0]["path"] != _rows(second)[0]["path"]
    assert drifted["status"] == "error"
    error = drifted["error"]
    assert isinstance(error, dict)
    assert error["error_type"] == "invalid_cursor"


async def test_real_stdio_zero_config_workflow(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "soleaux"],
        env=_processes.minimum_environment(),
        cwd=str(tmp_path),
        keep_alive=False,
    )

    async with Client(transport) as client:
        searched = await _search_until_rows(
            client,
            {"query": "answer", "kinds": ["symbol"]},
        )
        context = _request_payload(
            await client.call_tool(
                "context",
                {
                    "request": {
                        "objective": "find the answer implementation",
                        "paths": ["main.py"],
                    }
                },
            )
        )

    assert searched["status"] == "ok"
    assert context["status"] == "ok"
    rows = _rows(searched)
    assert rows
    assert rows[0]["name"] == "answer"


async def test_real_stdio_enumerates_next_routes(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"next":"16.3.0-preview.6"}}\n',
        encoding="utf-8",
    )
    route_file = tmp_path / "app" / "blog" / "[slug]" / "page.tsx"
    route_file.parent.mkdir(parents=True)
    route_file.write_text("export default function Page() { return null }\n", encoding="utf-8")

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "soleaux"],
        env=_processes.minimum_environment(),
        cwd=str(tmp_path),
        keep_alive=False,
    )

    async with Client(transport) as client:
        routed = await _search_until_rows(
            client,
            {
                "query": "blog",
                "kinds": ["route"],
                "semantic_mode": "syntax_only",
            },
        )

    assert routed["status"] == "ok"
    rows = _rows(routed)
    assert [(row["path"], row["route"]) for row in rows] == [
        ("app/blog/[slug]/page.tsx", "/blog/[slug]")
    ]
