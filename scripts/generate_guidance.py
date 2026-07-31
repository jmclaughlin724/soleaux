"""Regenerate the marker-delimited guidance blocks from the registered components.

The decorated FastMCP components in `soleaux.server` are the canonical catalog
owner; this script projects them into the packaged documentation so the checked
in guidance can never drift silently. Run from the package directory:

    uv run --locked --package soleaux python scripts/generate_guidance.py
"""

from __future__ import annotations

import pathlib

import soleaux.server as server
import soleaux.surface as surface

RESOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "soleaux" / "resources"
TOOL_CATALOG_DOCUMENTS = (
    RESOURCE_ROOT / "docs" / "agent-workflow.md",
    RESOURCE_ROOT / "docs" / "tool-catalog.md",
    RESOURCE_ROOT / "skills" / "soleaux" / "SKILL.md",
)
INSTRUCTIONS_DOCUMENT = RESOURCE_ROOT / "docs" / "server-instructions.md"

TOOL_CATALOG_START = "<!-- soleaux-tool-catalog:start -->"
TOOL_CATALOG_END = "<!-- soleaux-tool-catalog:end -->"
INSTRUCTIONS_START = "<!-- soleaux-server-instructions:start -->"
INSTRUCTIONS_END = "<!-- soleaux-server-instructions:end -->"


def _rewrite(path: pathlib.Path, *, start: str, end: str, body: str) -> bool:
    document = path.read_text(encoding="utf-8")
    rewritten = surface.replace_marked_block(document, start=start, end=end, replacement=body)
    if rewritten == document:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def main() -> None:
    catalog_body = f"\n```json\n{surface.tool_catalog_block(surface.tool_catalog())}\n```\n"
    instructions_body = f"\n```text\n{server.SERVER_INSTRUCTIONS}\n```\n"

    changed: list[pathlib.Path] = []
    for document in TOOL_CATALOG_DOCUMENTS:
        if _rewrite(document, start=TOOL_CATALOG_START, end=TOOL_CATALOG_END, body=catalog_body):
            changed.append(document)
    if _rewrite(
        INSTRUCTIONS_DOCUMENT,
        start=INSTRUCTIONS_START,
        end=INSTRUCTIONS_END,
        body=instructions_body,
    ):
        changed.append(INSTRUCTIONS_DOCUMENT)

    for document in changed:
        print(f"rewrote {document.relative_to(RESOURCE_ROOT.parents[2])}")
    if not changed:
        print("guidance blocks already match the registered components")


if __name__ == "__main__":
    main()
