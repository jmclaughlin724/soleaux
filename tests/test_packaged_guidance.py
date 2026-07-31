"""Packaged guidance stays aligned with the typed product catalogs."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from pydantic import TypeAdapter

from soleaux import surface as component_surface
from soleaux.server import SERVER_INSTRUCTIONS

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = files("soleaux.resources")
TOOL_CATALOG_START = "<!-- soleaux-tool-catalog:start -->"
TOOL_CATALOG_END = "<!-- soleaux-tool-catalog:end -->"

EXPECTED_DOCS = {
    "agent-workflow.md",
    "adopt-guide.md",
    "editor-safety.md",
    "evidence-and-coverage.md",
    "postgresql-security.md",
    "provider-configuration.md",
    "quickstart.md",
    "server-instructions.md",
    "tool-catalog.md",
    "troubleshooting.md",
}
SEMANTIC_MODE_GUIDANCE_PATHS = (
    "docs/agent-workflow.md",
    "docs/provider-configuration.md",
    "skills/soleaux/SKILL.md",
)
_TOOL_CATALOG_ADAPTER = TypeAdapter(list[dict[str, str]])


def _read_resource(relative_path: str) -> str:
    return RESOURCE_ROOT.joinpath(relative_path).read_text(encoding="utf-8")


def _tool_catalog(document: str) -> list[dict[str, str]]:
    section = document.split(TOOL_CATALOG_START, maxsplit=1)[1].split(
        TOOL_CATALOG_END,
        maxsplit=1,
    )[0]
    payload = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    parsed: object = json.loads(payload)
    rows = _TOOL_CATALOG_ADAPTER.validate_python(parsed, strict=True)
    catalog: list[dict[str, str]] = []
    for typed_row in rows:
        assert set(typed_row) == {"name", "summary", "description"}
        catalog.append(typed_row)
    return catalog


def _expected_tools() -> list[dict[str, str]]:
    return [
        {
            "name": str(row["name"]),
            "summary": str(row["summary"]),
            "description": str(row["description"]),
        }
        for row in component_surface.tool_catalog()
    ]


def test_guidance_inventory_and_publication_status() -> None:
    docs = RESOURCE_ROOT.joinpath("docs")
    assert {item.name for item in docs.iterdir() if item.name.endswith(".md")} == EXPECTED_DOCS
    assert RESOURCE_ROOT.joinpath("skills/soleaux/SKILL.md").is_file()

    readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (PACKAGE_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "licensed under the MIT License" in readme
    assert "The project is licensed under MIT." in changelog
    license_text = (PACKAGE_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert not (PACKAGE_ROOT / "LICENSE.md").exists()


def test_generated_tool_catalogs_match_the_single_metadata_owner() -> None:
    expected = _expected_tools()
    generated_surfaces = (
        _read_resource("docs/agent-workflow.md"),
        _read_resource("docs/tool-catalog.md"),
        _read_resource("skills/soleaux/SKILL.md"),
    )

    for surface in generated_surfaces:
        assert _tool_catalog(surface) == expected
        assert "plans/2026-" not in surface


def test_server_instructions_match_the_component_owner() -> None:
    document = _read_resource("docs/server-instructions.md")
    assert SERVER_INSTRUCTIONS in document


def test_semantic_mode_guidance_matches_the_component_owner() -> None:
    expected = component_surface.semantic_mode_guidance_markdown()
    for relative_path in SEMANTIC_MODE_GUIDANCE_PATHS:
        assert expected in _read_resource(relative_path)
