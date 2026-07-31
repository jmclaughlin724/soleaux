"""Canonical public-client projection for the zero-MCP fixture."""

from __future__ import annotations

import json
import typing

import fastmcp
import pydantic


def _dump_model(model: pydantic.BaseModel) -> dict[str, object]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def canonical_bytes(payload: dict[str, object]) -> bytes:
    serialized = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{serialized}\n".encode()


def _canonical_resource_content(
    uri: str,
    content: pydantic.BaseModel,
) -> dict[str, object]:
    payload = _dump_model(content)
    if uri != "soleaux://about":
        return payload
    text = payload.get("text")
    assert isinstance(text, str)
    about = typing.cast(dict[str, object], json.loads(text))
    configuration = about.get("configuration")
    identity = about.get("identity")
    storage = about.get("storage")
    assert isinstance(configuration, dict)
    assert isinstance(identity, dict)
    assert isinstance(storage, dict)
    configuration["digest"] = "<configuration-digest>"
    identity["process_epoch"] = "<process-epoch>"
    identity["workspace_root"] = "<workspace-root>"
    identity["workspace_trust_digest"] = "<workspace-trust-digest>"
    identity["configuration_digest"] = "<configuration-digest>"
    build = typing.cast("dict[str, object]", identity).get("build")
    if isinstance(build, dict):
        build["git_sha"] = "<git-sha>"
        if "build_time_utc" in build:
            build["build_time_utc"] = "<build-time-utc>"
        python = typing.cast("dict[str, object]", build).get("python")
        if isinstance(python, dict):
            python["version_info"] = "<python-version-info>"
            python["version"] = "<python-version>"
    storage["generation"] = "<catalog-generation>"
    storage["snapshot_id"] = "<snapshot-id>"
    storage["source_fingerprint"] = "<source-fingerprint>"
    storage["expected_path"] = "<catalog-path>"
    storage["fts_available"] = "<fts-available>"
    storage["materialized_generation"] = "<materialized-generation>"
    storage["enrichment_settled"] = "<enrichment-settled>"
    storage["published_table_count"] = "<published-table-count>"
    storage["attempted_table_count"] = "<attempted-table-count>"
    payload["text"] = json.dumps(about, indent=2)
    return payload


async def canonical_projection_from_client(
    client: fastmcp.Client[typing.Any],
) -> dict[str, object]:
    tools = await client.list_tools()
    resources = await client.list_resources()
    resource_templates = await client.list_resource_templates()
    prompts = await client.list_prompts()
    projected_resources: list[dict[str, object]] = []
    for resource in resources:
        contents = await client.read_resource(resource.uri)
        projected_resources.append(
            {
                "definition": _dump_model(resource),
                "contents": [
                    _canonical_resource_content(str(resource.uri), content) for content in contents
                ],
            }
        )

    return {
        "tools": [_dump_model(tool) for tool in tools],
        "resources": projected_resources,
        "resource_templates": [_dump_model(template) for template in resource_templates],
        "prompts": [_dump_model(prompt) for prompt in prompts],
    }


async def canonical_projection(
    server: fastmcp.FastMCP[dict[str, typing.Any]],
) -> dict[str, object]:
    async with fastmcp.Client(server) as client:
        return await canonical_projection_from_client(client)
