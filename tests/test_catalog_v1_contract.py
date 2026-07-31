"""Independent release oracle for the accepted ``soleaux.catalog/v1`` surface."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib.resources import files

import _assertions
from fastmcp import Client
from mcp_types import TextResourceContents

from soleaux import cli, surface
from soleaux.server import mcp

EXPECTED_LOCAL_TOOLS = (
    "describe",
    "search",
    "context",
    "query",
    "owners",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
)
EXPECTED_LOCAL_RESOURCES = (
    "soleaux://about",
    "soleaux://guide",
    "soleaux://quickstart/v1",
    "soleaux://tables/v1",
    "soleaux://health/v1",
    "soleaux://providers/v1",
    "soleaux://skills/v1",
    "soleaux://mcp/v1",
)
EXPECTED_ANALYSIS_COMMANDS = frozenset(
    {
        "describe",
        "search",
        "context",
        "query",
        "navigate",
        "inspect",
        "lint",
        "doctor",
        "benchmark",
    }
)
MINIMUM_ANALYSIS_ARGUMENTS = {
    "describe": ("describe",),
    "search": ("search", "needle"),
    "context": ("context", "find the needle"),
    "query": ("query", "--table", "repository.files"),
    "navigate": ("navigate", "definition"),
    "inspect": ("inspect", "diagnostics", "main.py", "--line", "1", "--column", "1"),
    "lint": ("lint",),
    "doctor": ("doctor",),
    "benchmark": ("benchmark",),
}
REMOVED_LOCAL_TOOL_NAMES = frozenset(
    {
        "explain_ownership",
        "preview_edit",
        "apply_edit",
        "restart_language_servers",
    }
)
REMOVED_TOOL_ALIASES = frozenset(
    {
        "soleaux_lint",
        "soleaux_ownership",
        *REMOVED_LOCAL_TOOL_NAMES,
        *(f"soleaux_{name}" for name in EXPECTED_LOCAL_TOOLS),
        *(f"soleaux_{name}" for name in REMOVED_LOCAL_TOOL_NAMES),
    }
)
TOOL_CATALOG_START = "<!-- soleaux-tool-catalog:start -->"
TOOL_CATALOG_END = "<!-- soleaux-tool-catalog:end -->"


def _resource_text(contents: Sequence[object]) -> str:
    assert len(contents) == 1
    content = contents[0]
    assert isinstance(content, TextResourceContents)
    return content.text


def _generated_tool_names(document: str) -> tuple[str, ...]:
    section = document.split(TOOL_CATALOG_START, maxsplit=1)[1].split(
        TOOL_CATALOG_END,
        maxsplit=1,
    )[0]
    payload = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    parsed: object = json.loads(payload)
    return _row_strings(parsed, key="name")


def _row_strings(rows: object, *, key: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw_row in _assertions.object_list(rows):
        row = _assertions.object_mapping(raw_row)
        value = row.get(key)
        assert isinstance(value, str)
        values.append(value)
    return tuple(values)


async def test_catalog_v1_wire_about_and_surface_are_literal() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        about = await client.read_resource("soleaux://about")

    assert tuple(tool.name for tool in tools) == EXPECTED_LOCAL_TOOLS
    local_resource_uris = tuple(
        str(resource.uri) for resource in resources if str(resource.uri).startswith("soleaux://")
    )
    assert local_resource_uris == EXPECTED_LOCAL_RESOURCES
    assert templates == []
    assert prompts == []
    assert surface.tool_names() == EXPECTED_LOCAL_TOOLS
    assert surface.resource_uris() == EXPECTED_LOCAL_RESOURCES

    about_payload = _assertions.object_mapping(json.loads(_resource_text(about)))
    catalog = _assertions.object_mapping(about_payload.get("catalog"))
    assert catalog.get("schema_version") == "soleaux.catalog/v1"
    assert _row_strings(about_payload.get("tools"), key="name") == EXPECTED_LOCAL_TOOLS
    assert _row_strings(about_payload.get("resources"), key="uri") == EXPECTED_LOCAL_RESOURCES


def test_catalog_v1_cli_and_generated_guidance_are_literal() -> None:
    parser = cli.create_parser()
    assert frozenset(MINIMUM_ANALYSIS_ARGUMENTS) == EXPECTED_ANALYSIS_COMMANDS
    for command, arguments in MINIMUM_ANALYSIS_ARGUMENTS.items():
        assert parser.parse_args(arguments).command == command

    generated_documents = (
        files("soleaux.resources").joinpath("docs/agent-workflow.md").read_text("utf-8"),
        files("soleaux.resources").joinpath("docs/tool-catalog.md").read_text("utf-8"),
        files("soleaux.resources").joinpath("skills/soleaux/SKILL.md").read_text("utf-8"),
    )
    for document in generated_documents:
        assert _generated_tool_names(document) == EXPECTED_LOCAL_TOOLS
        assert REMOVED_TOOL_ALIASES.isdisjoint(document.split())
