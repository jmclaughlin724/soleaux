#!/usr/bin/env python3
"""Fail-closed Phase 0 contract drift test for the unified Soleaux profile."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "contracts" / "unified-mcp-profile-v2.json"
CONTEXT_PATH = ROOT / "contracts" / "context-packet-v2.schema.json"
IDENTITY_PATH = ROOT / "contracts" / "phase0-identity.json"
PROFILE_DOC_PATH = ROOT / "UNIFIED-MCP-PROFILE.md"
CONTEXT_DOC_PATH = ROOT / "CONTEXT-PACKET-V2.md"
BUNDLE_PARTS = tuple(ROOT.glob(".ci/phase0-contracts.part-*"))

EXPECTED_BUNDLE_PART_NAMES: Final = (
    "phase0-contracts.part-00",
    "phase0-contracts.part-01",
    "phase0-contracts.part-02",
)
EXPECTED_BUNDLE_B64_BYTES: Final = 26_360
EXPECTED_BUNDLE_B64_SHA256: Final = (
    "ee4cfde3ce76b01737fa08498950af8288c9f3a585b72f452d2917d739e1e73a"
)
EXPECTED_BUNDLE_TAR_SHA256: Final = (
    "6826368b15370b50f08e697e06ba9afb6b955455f2bb84cbd81088dc3bb2f564"
)

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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def contract_bundle_members() -> dict[str, bytes] | None:
    parts = sorted(BUNDLE_PARTS, key=lambda path: path.name)
    if not parts:
        if PROFILE_PATH.is_file() and CONTEXT_PATH.is_file():
            return None
        raise AssertionError("Phase 0 schemas and their hash-bound bundle are both absent")
    names = tuple(path.name for path in parts)
    if names != EXPECTED_BUNDLE_PART_NAMES:
        raise AssertionError(
            "Phase 0 contract bundle parts drifted: "
            f"expected {EXPECTED_BUNDLE_PART_NAMES}, got {names}"
        )
    encoded = (
        b"".join(path.read_bytes() for path in parts)
        .replace(b"\r", b"")
        .replace(b"\n", b"")
    )
    if len(encoded) != EXPECTED_BUNDLE_B64_BYTES:
        raise AssertionError(
            "Phase 0 contract bundle encoded byte count drifted: "
            f"expected {EXPECTED_BUNDLE_B64_BYTES}, got {len(encoded)}"
        )
    if sha256_bytes(encoded) != EXPECTED_BUNDLE_B64_SHA256:
        raise AssertionError("Phase 0 contract bundle encoded digest mismatch")
    try:
        archive = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise AssertionError("Phase 0 contract bundle is not strict base64") from error
    if sha256_bytes(archive) != EXPECTED_BUNDLE_TAR_SHA256:
        raise AssertionError("Phase 0 contract bundle archive digest mismatch")

    required = {
        "contracts/unified-mcp-profile-v2.json",
        "contracts/context-packet-v2.schema.json",
    }
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as bundle:
        for name in required:
            member = bundle.getmember(name)
            if not member.isfile():
                raise AssertionError(f"Phase 0 bundle member is not a file: {name}")
            stream = bundle.extractfile(member)
            if stream is None:
                raise AssertionError(f"Phase 0 bundle member is unreadable: {name}")
            members[name] = stream.read()
    return members


def contract_bytes(
    path: Path, bundle_member: str, bundle: dict[str, bytes] | None
) -> bytes:
    if path.is_file():
        direct = path.read_bytes()
        if bundle is not None and direct != bundle[bundle_member]:
            raise AssertionError(
                f"{path.relative_to(ROOT)} differs from its hash-bound bundle member"
            )
        return direct
    if bundle is None:
        raise AssertionError(f"missing contract schema: {path.relative_to(ROOT)}")
    return bundle[bundle_member]


def load_object_bytes(value: bytes, *, label: str) -> dict[str, object]:
    parsed = json.loads(value.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise AssertionError(f"{label} must contain one JSON object")
    return parsed


def load_path(path: Path) -> dict[str, object]:
    return load_object_bytes(path.read_bytes(), label=str(path.relative_to(ROOT)))


def assert_closed_schema(schema: object, *, label: str) -> None:
    if not isinstance(schema, dict):
        raise AssertionError(f"{label} must be an object schema")
    if schema.get("type") != "object":
        raise AssertionError(f"{label} root type must be object")
    if schema.get("additionalProperties") is not False:
        raise AssertionError(f"{label} must reject unknown fields")


def main() -> None:
    bundle = contract_bundle_members()
    profile_bytes = contract_bytes(
        PROFILE_PATH, "contracts/unified-mcp-profile-v2.json", bundle
    )
    context_bytes = contract_bytes(
        CONTEXT_PATH, "contracts/context-packet-v2.schema.json", bundle
    )
    profile = load_object_bytes(profile_bytes, label="unified MCP profile")
    context = load_object_bytes(context_bytes, label="Context Packet V2 schema")
    identity = load_path(IDENTITY_PATH)
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

    profile_sha = sha256_bytes(profile_bytes)
    context_sha = sha256_bytes(context_bytes)
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
                "schemaSource": (
                    "direct+bundle"
                    if PROFILE_PATH.is_file() and bundle is not None
                    else "direct"
                    if PROFILE_PATH.is_file()
                    else "bundle"
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
