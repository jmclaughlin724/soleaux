"""Product identity and the fixed MCP catalog (AC01, AC02)."""

import json
import tomllib
import typing
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import _host_root
import _processes
import fastmcp.server.server as fastmcp_server
import pytest
from _assertions import raises_with_message
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.local_provider.local_provider import LocalProvider
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from soleaux import surface
from soleaux._identity import resolve_build_identity
from soleaux.analysis.service import product_version
from soleaux.server import LOCAL_TOOLS, create_server, mcp

EXPECTED_TOOLS = set(surface.tool_names())
EXPECTED_RESOURCES = set(surface.resource_uris())
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_server_identity_and_local_provider_is_primary(tmp_path: Path) -> None:
    server = create_server(tmp_path)
    assert server.name == "Soleaux"
    assert isinstance(server.providers[0], LocalProvider)


def test_repeated_factory_construction_is_independent(tmp_path: Path) -> None:
    first = create_server(tmp_path)
    second = create_server(tmp_path)

    assert first is not second
    assert first.name == second.name == "Soleaux"
    assert len(first.providers) == len(second.providers) == 1
    assert isinstance(first.providers[0], LocalProvider)
    assert isinstance(second.providers[0], LocalProvider)
    assert first.providers[0] is not second.providers[0]


def test_fastmcp_json_matches_the_stable_schema() -> None:
    manifest = Path(__file__).resolve().parents[1] / "fastmcp.json"
    parsed = json.loads(manifest.read_text(encoding="utf-8"))
    assert parsed["$schema"] == "https://gofastmcp.com/public/schemas/fastmcp.json/v1.json"
    assert parsed["source"]["entrypoint"] == "create_server"
    assert (manifest.parent / parsed["source"]["path"]).is_file()
    assert parsed["deployment"]["transport"] == "stdio"


def test_fastmcp_skill_claim_matches_manifest_pin() -> None:
    repository_root = _host_root.require_host_root()
    manifest = tomllib.loads(
        (repository_root / "tools/soleaux/pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = manifest["project"]["dependencies"]
    fastmcp_pins = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, str) and dependency.startswith("fastmcp==")
    ]

    routing = (
        repository_root / ".agents/skills/fastmcp/references/version-and-source-routing.md"
    ).read_text(encoding="utf-8")
    marker = "**Anilize installs FastMCP `"
    claimed_version = routing.partition(marker)[2].partition("`")[0]

    assert claimed_version
    assert fastmcp_pins == [f"fastmcp=={claimed_version}"]


async def test_catalog_is_exactly_ten_tools_seven_resources() -> None:
    async with Client(mcp) as client:
        assert client.protocol_version in MODERN_PROTOCOL_VERSIONS
        tools = await client.list_tools()
        assert len(tools) == 10
        assert {tool.name for tool in tools} == EXPECTED_TOOLS
        resources = await client.list_resources()
        local_resources = {
            str(resource.uri)
            for resource in resources
            if str(resource.uri).startswith("soleaux://")
        }
        assert local_resources == EXPECTED_RESOURCES
        assert await client.list_prompts() == []
        assert await client.list_resource_templates() == []


async def test_tool_annotations_match_the_component_owner() -> None:
    expected = {
        str(surface.tool_descriptor(handler)["name"]): surface.tool_annotations(handler)
        for handler in LOCAL_TOOLS
    }
    async with Client(mcp) as client:
        tools = await client.list_tools()
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.model_dump(mode="json", by_alias=True, exclude_none=True) == expected[
            tool.name
        ].model_dump(mode="json", by_alias=True, exclude_none=True)


def test_tool_effect_descriptors_are_explicit_and_annotations_are_only_hints() -> None:
    descriptors = {str(row["name"]): row for row in surface.tool_catalog()}
    assert descriptors["search"]["effect"] == surface.ToolEffect.READ_ONLY.value
    assert descriptors["preview"]["previewable"] is True
    assert descriptors["edit"]["effect"] == surface.ToolEffect.WORKSPACE_MUTATING.value
    assert descriptors["edit"]["self_validating"] is True
    assert descriptors["restart_lsp"]["effect"] == surface.ToolEffect.PROCESS_MUTATING.value
    assert all(row["external"] is False for row in descriptors.values())


async def test_native_fastmcp_telemetry_names_tool_call_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[tuple[str, str, str | None]] = []

    @contextmanager
    def capture_server_span(
        name: str,
        method: str,
        _server_name: str,
        _component_type: str,
        _component_key: str,
        *,
        tool_name: str | None = None,
        **_attributes: object,
    ) -> Generator[Mock]:
        spans.append((name, method, tool_name))
        yield Mock()

    monkeypatch.setattr(fastmcp_server, "server_span", capture_server_span)

    async with Client(mcp, mode="auto") as client:
        with pytest.raises(ToolError):
            await client.call_tool("search", {"request": {}})

    assert ("tools/call search", "tools/call", "search") in spans


async def test_strict_input_validation_rejects_missing_fields() -> None:
    async with Client(mcp) as client:
        with raises_with_message(ToolError, "query"):
            await client.call_tool("search", {"request": {}})


async def test_resources_serve_packaged_content() -> None:
    async with Client(mcp, mode="auto") as client:
        about = await client.read_resource("soleaux://about")
        guide = await client.read_resource("soleaux://guide")
        quickstart = await client.read_resource("soleaux://quickstart/v1")
        tables = await client.read_resource("soleaux://tables/v1")
        health = await client.read_resource("soleaux://health/v1")
        providers = await client.read_resource("soleaux://providers/v1")
        skills = await client.read_resource("soleaux://skills/v1")
    about_text = getattr(about[0], "text", None)
    guide_text = getattr(guide[0], "text", None)
    quickstart_text = getattr(quickstart[0], "text", None)
    tables_text = getattr(tables[0], "text", None)
    health_text = getattr(health[0], "text", None)
    providers_text = getattr(providers[0], "text", None)
    skills_text = getattr(skills[0], "text", None)
    assert isinstance(about_text, str)
    assert isinstance(guide_text, str)
    assert isinstance(quickstart_text, str)
    assert isinstance(tables_text, str)
    assert isinstance(health_text, str)
    assert isinstance(providers_text, str)
    assert isinstance(skills_text, str)
    about_payload = json.loads(about_text)
    assert about_payload["product"]["name"] == "Soleaux"
    assert about_payload["product"]["version"] == product_version()
    assert len(about_payload["tools"]) == 10
    for name in (
        "search",
        "context",
        "owners",
        "navigate",
        "inspect",
    ):
        assert name in guide_text
    tables_payload = json.loads(tables_text)
    assert tables_payload["schema_version"] == "soleaux.tables/v1"
    assert tables_payload["tables"]
    health_payload = json.loads(health_text)
    assert health_payload["schema_version"] == "soleaux.health/v1"
    providers_payload = json.loads(providers_text)
    assert providers_payload["schema_version"] == "soleaux.providers/v1"
    assert providers_payload["providers"]
    skills_payload = json.loads(skills_text)
    assert skills_payload["schema_version"] == "soleaux.skills/v1"


async def test_zero_config_skills_resource_is_configured_only(tmp_path: Path) -> None:
    async with Client(create_server(tmp_path)) as client:
        skills = await client.read_resource("soleaux://skills/v1")

    skills_text = getattr(skills[0], "text", None)
    assert isinstance(skills_text, str)
    skills_payload = json.loads(skills_text)
    assert skills_payload == {
        "schema_version": "soleaux.skills/v1",
        "enabled": False,
        "reload": False,
        "main_file_name": "SKILL.md",
        "supporting_files": "template",
        "roots": [],
    }


async def test_wire_about_and_surface_metadata_agree_on_literal_v1_catalog() -> None:
    """The about resource, wire catalog, and surface metadata expose the same v1 identity."""
    expected_tools = (
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
    expected_resources = (
        "soleaux://about",
        "soleaux://guide",
        "soleaux://quickstart/v1",
        "soleaux://tables/v1",
        "soleaux://health/v1",
        "soleaux://providers/v1",
        "soleaux://skills/v1",
    )
    assert surface.tool_names() == expected_tools
    assert surface.resource_uris() == expected_resources
    async with Client(mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        templates = await client.list_resource_templates()
        about = await client.read_resource("soleaux://about")
    assert tuple(tool.name for tool in tools) == expected_tools
    assert (
        tuple(
            str(resource.uri)
            for resource in resources
            if str(resource.uri).startswith("soleaux://")
        )
        == expected_resources
    )
    assert prompts == []
    assert templates == []
    about_payload = json.loads(getattr(about[0], "text", ""))
    assert about_payload["schema_versions"]["envelope"] == "soleaux.mcp/v1"
    assert about_payload["schema_versions"]["context"] == "soleaux.context/v1"
    assert [tool["name"] for tool in about_payload["tools"]] == list(expected_tools)
    assert [resource["uri"] for resource in about_payload["resources"]] == list(expected_resources)


def test_editable_identity_resolves_git_sha() -> None:
    identity = resolve_build_identity()
    assert identity["install_source"] == "editable"
    assert identity["version"] == product_version()
    assert isinstance(identity["git_sha"], str) and identity["git_sha"]
    assert "version_info" in identity["python"]
    assert "version" in identity["python"]


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("wheel")
    _processes.run_checked(
        (
            _processes.required_executable("uv"),
            "build",
            "--wheel",
            "--no-sources",
            "--out-dir",
            str(out_dir),
            "--no-create-gitignore",
        ),
        cwd=PACKAGE_ROOT,
        environment=_processes.minimum_environment(
            {
                "UV_NO_SYNC": "1",
                "UV_OFFLINE": "1",
            }
        ),
    )
    matches = tuple(out_dir.glob("soleaux-*.whl"))
    assert len(matches) == 1
    return matches[0]


def test_wheel_includes_build_identity(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        identity_bytes = archive.read("soleaux/resources/build_identity.json")
    identity = json.loads(identity_bytes)
    assert identity["source"] == "wheel"
    assert identity["version"] == product_version()
    assert isinstance(identity["git_sha"], str) and identity["git_sha"]
    assert isinstance(identity["build_time_utc"], str) and identity["build_time_utc"]


async def test_describe_includes_build_identity(tmp_path: Path) -> None:
    async with Client(create_server(tmp_path)) as client:
        describe = await client.call_tool("describe", {"request": {}})
    text = typing.cast(str, typing.cast(typing.Any, describe.content[0]).text)
    payload = json.loads(text)
    build = payload["data"]["identity"]["build"]
    assert build["install_source"] == "editable"
    assert build["version"] == product_version()
    assert isinstance(build["git_sha"], str) and build["git_sha"]
    assert "version_info" in build["python"]


async def test_about_includes_build_identity(tmp_path: Path) -> None:
    async with Client(create_server(tmp_path)) as client:
        about = await client.read_resource("soleaux://about")
    payload = json.loads(getattr(about[0], "text", ""))
    build = payload["identity"]["build"]
    assert build["install_source"] == "editable"
    assert build["version"] == product_version()
    assert isinstance(build["git_sha"], str) and build["git_sha"]
    assert "version_info" in build["python"]
