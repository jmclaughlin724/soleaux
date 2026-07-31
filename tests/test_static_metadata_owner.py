"""Every catalog surface derives from the registered FastMCP components."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib.resources import files

import _assertions
from fastmcp import Client
from mcp_types import TextResourceContents

from soleaux import surface
from soleaux.server import LOCAL_TOOLS, SERVER_INSTRUCTIONS, mcp

TOOL_CATALOG_START = "<!-- soleaux-tool-catalog:start -->"
TOOL_CATALOG_END = "<!-- soleaux-tool-catalog:end -->"

SOLEAUX_META_FIELDS = ("summary", "effect", "external", "previewable", "self_validating")


def _resource_text(contents: Sequence[object]) -> str:
    assert len(contents) == 1
    content = contents[0]
    assert isinstance(content, TextResourceContents)
    return content.text


def _guidance_rows(value: object) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = []
    for raw_entry in _assertions.object_list(value):
        entry = _assertions.object_mapping(raw_entry)
        name = entry.get("name")
        summary = entry.get("summary")
        description = entry.get("description")
        assert isinstance(name, str)
        assert isinstance(summary, str)
        assert isinstance(description, str)
        catalog.append({"name": name, "summary": summary, "description": description})
    return catalog


def _document_catalog(document: str) -> object:
    section = document.split(TOOL_CATALOG_START, maxsplit=1)[1].split(
        TOOL_CATALOG_END,
        maxsplit=1,
    )[0]
    payload = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return json.loads(payload)


async def test_wire_components_match_the_surface_descriptors() -> None:
    expected = [surface.tool_descriptor(handler) for handler in LOCAL_TOOLS]

    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == [descriptor["name"] for descriptor in expected]
    for tool, handler, descriptor in zip(tools, LOCAL_TOOLS, expected, strict=True):
        assert tool.description == descriptor["description"]
        assert tool.annotations is not None
        assert tool.annotations.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ) == surface.tool_annotations(handler).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        assert tool.meta is not None
        assert tool.meta["soleaux"] == {field: descriptor[field] for field in SOLEAUX_META_FIELDS}
    assert mcp.instructions == SERVER_INSTRUCTIONS


async def test_static_surfaces_reference_the_component_catalog() -> None:
    expected_catalog = surface.tool_catalog()
    expected_guidance = _guidance_rows(expected_catalog)
    expected_resources = surface.resource_catalog()

    async with Client(mcp) as client:
        about = await client.read_resource("soleaux://about")
        guide = await client.read_resource("soleaux://guide")

    typed_about = _assertions.object_mapping(json.loads(_resource_text(about)))
    assert typed_about["tools"] == expected_catalog
    assert typed_about["resources"] == expected_resources

    guide_text = _resource_text(guide)
    tool_catalog_document = (
        files("soleaux.resources").joinpath("docs/tool-catalog.md").read_text(encoding="utf-8")
    )

    for document in (guide_text, tool_catalog_document):
        assert _guidance_rows(_document_catalog(document)) == expected_guidance
