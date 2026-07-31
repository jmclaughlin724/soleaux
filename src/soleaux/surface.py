"""Serialization of the registered FastMCP component surface (`soleaux.mcp/v1`).

The decorated tool handlers and resource readers in `soleaux.server` are the
single metadata owner. This module derives every non-wire catalog surface —
`soleaux://about`, CLI discovery, packaged guidance blocks, and drift tests —
from that attached component metadata. Zero prompts, zero resource templates.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import enum
import hashlib
import json
import typing

import fastmcp.decorators
import fastmcp.resources.function_resource
import fastmcp.tools.function_tool
import mcp_types
import pydantic

import soleaux.catalog.contracts
import soleaux.contracts.requests

_OBJECT_MAPPING_ADAPTER = pydantic.TypeAdapter(dict[str, object])


class ToolEffect(enum.StrEnum):
    """Authoritative effect class; MCP annotations remain transport hints."""

    READ_ONLY = "read_only"
    WORKSPACE_MUTATING = "workspace_mutating"
    PROCESS_MUTATING = "process_mutating"


@dataclasses.dataclass(frozen=True)
class SemanticModeMeta:
    """Agent-facing meaning for one semantic guarantee."""

    mode: soleaux.contracts.requests.SemanticMode
    description: str
    default: bool = False


SEMANTIC_MODES: tuple[SemanticModeMeta, ...] = (
    SemanticModeMeta(
        mode=soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE,
        description=(
            "default for exploration; return partial evidence when a provider is unavailable"
        ),
        default=True,
    ),
    SemanticModeMeta(
        mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
        description="skip all Language Server Protocol work",
    ),
    SemanticModeMeta(
        mode=soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED,
        description="fail when semantic coverage is incomplete",
    ),
)


def readonly_annotations() -> mcp_types.ToolAnnotations:
    return mcp_types.ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def mutating_annotations(*, destructive: bool) -> mcp_types.ToolAnnotations:
    return mcp_types.ToolAnnotations(
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=False,
        open_world_hint=False,
    )


def soleaux_tool_meta(
    *,
    summary: str,
    effect: ToolEffect = ToolEffect.READ_ONLY,
    external: bool = False,
    previewable: bool = False,
    self_validating: bool = True,
) -> dict[str, typing.Any]:
    """Product metadata carried in each tool's `meta["soleaux"]` namespace."""
    return {
        "soleaux": {
            "summary": summary,
            "effect": effect.value,
            "external": external,
            "previewable": previewable,
            "self_validating": self_validating,
        }
    }


def _tool_meta(
    handler: collections.abc.Callable[..., object],
) -> fastmcp.tools.function_tool.ToolMeta:
    metadata = fastmcp.decorators.get_fastmcp_meta(handler)
    if not isinstance(metadata, fastmcp.tools.function_tool.ToolMeta):
        raise TypeError(f"{handler!r} is not decorated with @tool")
    return metadata


def _resource_meta(
    reader: collections.abc.Callable[..., object],
) -> fastmcp.resources.function_resource.ResourceMeta:
    metadata = fastmcp.decorators.get_fastmcp_meta(reader)
    if not isinstance(metadata, fastmcp.resources.function_resource.ResourceMeta):
        raise TypeError(f"{reader!r} is not decorated with @resource")
    return metadata


def tool_effect(handler: collections.abc.Callable[..., object]) -> ToolEffect:
    return ToolEffect(str(tool_descriptor(handler)["effect"]))


def tool_annotations(handler: collections.abc.Callable[..., object]) -> mcp_types.ToolAnnotations:
    annotations = _tool_meta(handler).annotations
    if annotations is None:
        raise TypeError(f"{_tool_meta(handler).name} declares no annotations")
    return annotations


def tool_descriptor(handler: collections.abc.Callable[..., object]) -> dict[str, str | bool]:
    """Flatten one decorated handler into the catalog row shape."""
    metadata = _tool_meta(handler)
    if metadata.name is None or metadata.description is None:
        raise TypeError(f"{handler!r} must declare an explicit name and description")
    namespace_value = (metadata.meta or {}).get("soleaux")
    if not isinstance(namespace_value, dict):
        raise TypeError(f"{metadata.name} carries no meta['soleaux'] namespace")
    namespace = _OBJECT_MAPPING_ADAPTER.validate_python(namespace_value, strict=True)
    summary = namespace.get("summary")
    effect = namespace.get("effect")
    external = namespace.get("external")
    previewable = namespace.get("previewable")
    self_validating = namespace.get("self_validating")
    if (
        not isinstance(summary, str)
        or not isinstance(effect, str)
        or not isinstance(external, bool)
        or not isinstance(previewable, bool)
        or not isinstance(self_validating, bool)
    ):
        raise TypeError(f"{metadata.name} carries an incomplete meta['soleaux'] namespace")
    return {
        "name": metadata.name,
        "summary": summary,
        "description": metadata.description,
        "effect": ToolEffect(effect).value,
        "external": external,
        "previewable": previewable,
        "self_validating": self_validating,
    }


def resource_descriptor(reader: collections.abc.Callable[..., object]) -> dict[str, str]:
    metadata = _resource_meta(reader)
    if metadata.name is None or metadata.description is None or metadata.mime_type is None:
        raise TypeError(f"{reader!r} must declare a name, description, and mime type")
    return {
        "uri": metadata.uri,
        "name": metadata.name,
        "description": metadata.description,
        "mime_type": metadata.mime_type,
    }


def tool_catalog() -> list[dict[str, str | bool]]:
    """Serialize the registered tool components for non-FastMCP discovery surfaces."""
    from soleaux import server

    return [tool_descriptor(handler) for handler in server.LOCAL_TOOLS]


def resource_catalog() -> list[dict[str, str]]:
    """Serialize the registered resource components for diagnostic discovery surfaces."""
    from soleaux import server

    return [resource_descriptor(reader) for reader in server.LOCAL_RESOURCES]


def tool_names() -> tuple[str, ...]:
    return tuple(str(descriptor["name"]) for descriptor in tool_catalog())


def resource_uris() -> tuple[str, ...]:
    return tuple(descriptor["uri"] for descriptor in resource_catalog())


def catalog_payload() -> dict[str, object]:
    """Return the complete local catalog projected from registered components."""
    tools = tool_catalog()
    resources = resource_catalog()
    return {
        "schema_version": soleaux.catalog.contracts.CATALOG_SCHEMA_VERSION,
        "tools": tools,
        "resources": resources,
        "tool_count": len(tools),
        "resource_count": len(resources),
        "prompts": [],
        "resource_templates": [],
    }


def catalog_digest() -> str:
    """Return a stable identity for the complete local component contract."""
    encoded = json.dumps(
        catalog_payload(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_mode_catalog() -> list[dict[str, str | bool]]:
    """Serialize semantic guarantees for capability discovery."""
    return [
        {
            "mode": metadata.mode.value,
            "description": metadata.description,
            "default": metadata.default,
        }
        for metadata in SEMANTIC_MODES
    ]


def semantic_mode_guidance_markdown() -> str:
    """Render the canonical semantic-mode guidance shared by packaged surfaces."""
    return "\n".join(
        (
            f"- `{metadata.mode.value}`"
            f"{' (default)' if metadata.default else ''}: {metadata.description}"
        )
        for metadata in SEMANTIC_MODES
    )


def tool_catalog_block(catalog: collections.abc.Iterable[dict[str, str | bool]]) -> str:
    """Render the `{name, summary, description}` JSON block embedded in guidance."""
    rows = [
        {
            "name": descriptor["name"],
            "summary": descriptor["summary"],
            "description": descriptor["description"],
        }
        for descriptor in catalog
    ]
    return json.dumps(rows, indent=2)


def replace_marked_block(document: str, *, start: str, end: str, replacement: str) -> str:
    """Replace the region between two exact marker lines, keeping the markers."""
    head, start_found, rest = document.partition(start)
    if not start_found:
        raise ValueError(f"missing start marker {start!r}")
    _, end_found, tail = rest.partition(end)
    if not end_found:
        raise ValueError(f"missing end marker {end!r}")
    return f"{head}{start}\n{replacement}\n{end}{tail}"
