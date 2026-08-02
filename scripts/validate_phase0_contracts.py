#!/usr/bin/env python3
"""Fail-closed Phase 0 contract drift test for the unified Soleaux profile."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "contracts" / "unified-mcp-profile-v2.json"
CONTEXT_PATH = ROOT / "contracts" / "context-packet-v2.schema.json"
IDENTITY_PATH = ROOT / "contracts" / "phase0-identity.json"
PROFILE_DOC_PATH = ROOT / "UNIFIED-MCP-PROFILE.md"
CONTEXT_DOC_PATH = ROOT / "CONTEXT-PACKET-V2.md"

EXPECTED_TOOLS = [
    "context.compile",
    "code.search",
    "memory.search",
    "get_symbols",
    "registry.list",
    "registry.read",
    "repo_info",
    "navigate",
    "inspect",
    "preview",
    "edit",
    "restart_lsp",
]
EXPECTED_OPTIONAL = [
    "parse_and_validate_postgres_sql",
    "turborepo.packages",
    "next.get_routes",
]
EXPECTED_VERSION = "0.4.0-dev.5"
EXPECTED_PROFILE_SCHEMA = "soleaux.mcp.profile/v2"
EXPECTED_CONTEXT_SCHEMA = "soleaux.context/v2"
EXPECTED_ENVELOPE_SCHEMA = "soleaux.mcp/v2"
EXPECTED_CEILING = 12
REQUIRED_ENVELOPE_FIELDS = {
    "schema_version",
    "product_version",
    "request_id",
    "workspace_id",
    "snapshot_id",
    "workspace",
    "status",
    "data",
    "rows",
    "evidence",
    "coverage",
    "warnings",
    "next_cursor",
    "suggested_next_requests",
    "error",
    "source",
    "engine",
    "engine_version",
    "trust",
    "provenance",
    "cache_status",
    "truncated",
    "continuation_cursor",
    "sensitivity",
    "duration_us",
}


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain one JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_closed_schema(schema: object, *, label: str) -> None:
    if not isinstance(schema, dict):
        raise AssertionError(f"{label} must be an object schema")
    if schema.get("type") != "object":
        raise AssertionError(f"{label} root type must be object")
    if schema.get("additionalProperties") is not False:
        raise AssertionError(f"{label} must reject unknown fields")


def main() -> None:
    profile = load(PROFILE_PATH)
    context = load(CONTEXT_PATH)
    identity = load(IDENTITY_PATH)
    profile_doc = PROFILE_DOC_PATH.read_text(encoding="utf-8")
    context_doc = CONTEXT_DOC_PATH.read_text(encoding="utf-8")

    assert profile["schemaVersion"] == EXPECTED_PROFILE_SCHEMA
    assert profile["productVersion"] == EXPECTED_VERSION
    assert profile["productionClaimAllowed"] is False
    assert profile["serverLocalNames"] is True
    assert profile["hardCeiling"] == EXPECTED_CEILING
    assert profile["responseEnvelopeSchemaVersion"] == EXPECTED_ENVELOPE_SCHEMA
    assert profile["contextPacketSchemaVersion"] == EXPECTED_CONTEXT_SCHEMA
    assert profile["defaultProfile"] == EXPECTED_TOOLS
    assert profile["optionalTools"] == EXPECTED_OPTIONAL

    tools = profile["tools"]
    optional = profile["optionalDefinitions"]
    assert isinstance(tools, list) and len(tools) == EXPECTED_CEILING
    assert isinstance(optional, list) and len(optional) == len(EXPECTED_OPTIONAL)
    assert [entry["name"] for entry in tools] == EXPECTED_TOOLS
    assert [entry["name"] for entry in optional] == EXPECTED_OPTIONAL
    assert len({entry["name"] for entry in tools + optional}) == len(tools) + len(optional)

    for entry in tools + optional:
        name = entry["name"]
        assert_closed_schema(entry["inputSchema"], label=f"{name} input")
        assert_closed_schema(entry["outputSchema"], label=f"{name} output")
        required = set(entry["outputSchema"].get("required", []))
        missing = REQUIRED_ENVELOPE_FIELDS - required
        if missing:
            raise AssertionError(f"{name} output omitted envelope fields: {sorted(missing)}")

    substitution = profile["substitutionRule"]
    assert substitution["mode"] == "explicit_one_for_one"
    assert substitution["automaticSubstitution"] is False
    assert substitution["preserveSlotOrder"] is True
    assert substitution["maximumActiveTools"] == EXPECTED_CEILING

    assert context["properties"]["schema_version"]["const"] == EXPECTED_CONTEXT_SCHEMA
    assert context["properties"]["product_version"]["const"] == EXPECTED_VERSION
    required_context = set(context["required"])
    for field in (
        "sources",
        "canonical_owners",
        "consumers",
        "constraints",
        "conflicts",
        "validation_routes",
        "supporting_facts",
        "external_references",
        "requested_resources",
        "gaps",
        "coverage_complete",
        "coverage",
        "native",
    ):
        assert field in required_context
    assert context["properties"]["gaps"]["maxItems"] == 64
    assert context["properties"]["external_references"]["maxItems"] == 32
    assert context["properties"]["requested_resources"]["maxItems"] == 32
    assert context["properties"]["returned_item_count"]["maximum"] == 200
    assert context["properties"]["native"]["properties"]["selected_parsers_native"]["const"] is True
    assert context["properties"]["native"]["properties"]["selected_lsps_native"]["const"] is True

    profile_sha = digest(PROFILE_PATH)
    context_sha = digest(CONTEXT_PATH)
    assert identity == {
        "product": "Soleaux",
        "version": EXPECTED_VERSION,
        "productionClaimAllowed": False,
        "profileSchemaVersion": EXPECTED_PROFILE_SCHEMA,
        "contextSchemaVersion": EXPECTED_CONTEXT_SCHEMA,
        "hardCeiling": EXPECTED_CEILING,
        "profileManifestSha256": profile_sha,
        "contextSchemaSha256": context_sha,
    }
    assert profile_sha in profile_doc
    assert context_sha in profile_doc
    assert context_sha in context_doc
    for name in EXPECTED_TOOLS:
        assert f"`{name}`" in profile_doc

    print(
        json.dumps(
            {
                "status": "pass",
                "productVersion": EXPECTED_VERSION,
                "productionClaimAllowed": False,
                "canonicalToolCount": len(EXPECTED_TOOLS),
                "hardCeiling": EXPECTED_CEILING,
                "optionalCandidateCount": len(EXPECTED_OPTIONAL),
                "profileManifestSha256": profile_sha,
                "contextSchemaSha256": context_sha,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
