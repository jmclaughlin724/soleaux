"""The fixed FastMCP catalog is closed, exact, and annotation-complete."""

from __future__ import annotations

import pathlib
import typing

import _assertions
import fastmcp
import fastmcp.server.providers.local_provider
import fastmcp.utilities.mcp_server_config
import fastmcp.utilities.mcp_server_config.v1.sources.filesystem
import pydantic
import pytest

import soleaux
import soleaux.server
import soleaux.surface


def _dump_model(model: pydantic.BaseModel) -> dict[str, object]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _object_dict(value: object) -> dict[str, object]:
    return _assertions.object_mapping(value)


async def _catalog_projection(server: fastmcp.FastMCP[typing.Any]) -> dict[str, object]:
    async with fastmcp.Client(server) as client:
        return {
            "tools": [_dump_model(tool) for tool in await client.list_tools()],
            "resources": [_dump_model(resource) for resource in await client.list_resources()],
            "resource_templates": [
                _dump_model(template) for template in await client.list_resource_templates()
            ],
            "prompts": [_dump_model(prompt) for prompt in await client.list_prompts()],
        }


async def test_catalog_has_exact_tools_resources_and_no_dynamic_surfaces() -> None:
    async with fastmcp.Client(soleaux.server.mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()

    assert [tool.name for tool in tools] == list(soleaux.surface.tool_names())
    local_resources = {
        str(resource.uri) for resource in resources if str(resource.uri).startswith("soleaux://")
    }
    assert local_resources == set(soleaux.surface.resource_uris())
    assert templates == []
    assert prompts == []
    assert isinstance(
        soleaux.server.mcp.providers[0], fastmcp.server.providers.local_provider.LocalProvider
    )


async def test_installed_filesystem_source_factory_matches_direct_factory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = pathlib.Path(__file__).resolve().parents[1] / "fastmcp.json"
    config = fastmcp.utilities.mcp_server_config.MCPServerConfig.from_file(profile)
    source = config.source
    assert isinstance(
        source, fastmcp.utilities.mcp_server_config.v1.sources.filesystem.FileSystemSource
    )
    source.path = str((profile.parent / source.path).resolve())
    monkeypatch.chdir(tmp_path)

    configured = await source.load_server()
    direct = soleaux.server.create_server(tmp_path)

    assert configured is not direct
    assert len(configured.providers) == 1
    assert isinstance(
        configured.providers[0], fastmcp.server.providers.local_provider.LocalProvider
    )
    assert await _catalog_projection(configured) == await _catalog_projection(direct)


async def test_tool_schemas_annotations_and_descriptions_match_metadata() -> None:
    async with fastmcp.Client(soleaux.server.mcp) as client:
        tools = await client.list_tools()

    by_name = {tool.name: tool for tool in tools}
    for handler in soleaux.server.LOCAL_TOOLS:
        descriptor = soleaux.surface.tool_descriptor(handler)
        tool = by_name[str(descriptor["name"])]
        assert tool.description == descriptor["description"]
        assert tool.annotations is not None
        assert tool.annotations.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ) == soleaux.surface.tool_annotations(handler).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        tool_payload = _dump_model(tool)
        input_schema = tool_payload["inputSchema"]
        output_schema = tool_payload["outputSchema"]
        assert isinstance(input_schema, dict)
        assert isinstance(output_schema, dict)
        assert input_schema["additionalProperties"] is False
        assert output_schema["additionalProperties"] is False

    readonly = {
        "describe",
        "search",
        "context",
        "query",
        "owners",
        "navigate",
        "inspect",
        "preview",
    }
    for name in readonly:
        annotations = by_name[name].annotations
        assert annotations is not None
        payload = annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert payload["readOnlyHint"] is True
        assert payload["idempotentHint"] is True

    apply_annotations = by_name["edit"].annotations
    assert apply_annotations is not None
    apply_payload = apply_annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert apply_payload["readOnlyHint"] is False
    assert apply_payload["idempotentHint"] is False

    restart_annotations = by_name["restart_lsp"].annotations
    assert restart_annotations is not None
    restart_payload = restart_annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert restart_payload["readOnlyHint"] is False
    assert restart_payload["idempotentHint"] is False


async def test_common_request_selectors_are_discoverable(tmp_path: pathlib.Path) -> None:
    async with fastmcp.Client(soleaux.server.create_server(tmp_path)) as client:
        tools = await client.list_tools()

    for tool in tools:
        payload = _dump_model(tool)
        input_schema = _object_dict(payload["inputSchema"])
        input_properties = _object_dict(input_schema["properties"])
        request_schema = _object_dict(input_properties["request"])
        request_properties = _object_dict(request_schema["properties"])
        assert {"semantic_mode", "workspace_id"} <= request_properties.keys()
        if tool.name == "search":
            assert {"cursor", "paths", "kinds"} <= request_properties.keys()
            assert "structural" not in request_properties
        if tool.name == "query":
            assert {"include_tables", "exclude_tables", "seed_keys"} <= request_properties.keys()
        if tool.name == "navigate":
            assert {"operation", "path", "line", "column"} <= request_properties.keys()
        if tool.name == "inspect":
            assert {"operation", "path", "line", "column"} <= request_properties.keys()
