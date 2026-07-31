"""Package-owned semantic operation and response normalization (D029, D031)."""

from __future__ import annotations

import bisect
import hashlib
import json

import pydantic

import soleaux.contracts.coverage
import soleaux.contracts.positions
import soleaux.lsp.contracts
import soleaux.lsp.generation

_NAVIGATION_CAPABILITY: dict[
    soleaux.lsp.contracts.SemanticOperation, soleaux.lsp.contracts.LspCapability
] = {
    soleaux.lsp.contracts.SemanticOperation.DEFINITION: (
        soleaux.lsp.contracts.LspCapability.DEFINITION
    ),
    soleaux.lsp.contracts.SemanticOperation.REFERENCES: (
        soleaux.lsp.contracts.LspCapability.REFERENCES
    ),
    soleaux.lsp.contracts.SemanticOperation.IMPLEMENTATION: (
        soleaux.lsp.contracts.LspCapability.IMPLEMENTATION
    ),
    soleaux.lsp.contracts.SemanticOperation.HOVER: soleaux.lsp.contracts.LspCapability.HOVER,
    soleaux.lsp.contracts.SemanticOperation.CALL_HIERARCHY: (
        soleaux.lsp.contracts.LspCapability.PREPARE_CALL_HIERARCHY
    ),
    soleaux.lsp.contracts.SemanticOperation.INCOMING_CALLS: (
        soleaux.lsp.contracts.LspCapability.INCOMING_CALLS
    ),
    soleaux.lsp.contracts.SemanticOperation.OUTGOING_CALLS: (
        soleaux.lsp.contracts.LspCapability.OUTGOING_CALLS
    ),
}

_CAPABILITY_FLAG: dict[soleaux.lsp.contracts.LspCapability, str] = {
    soleaux.lsp.contracts.LspCapability.DEFINITION: "definition_provider",
    soleaux.lsp.contracts.LspCapability.IMPLEMENTATION: "implementation_provider",
    soleaux.lsp.contracts.LspCapability.REFERENCES: "references_provider",
    soleaux.lsp.contracts.LspCapability.WORKSPACE_SYMBOL: "workspace_symbol_provider",
    soleaux.lsp.contracts.LspCapability.FORMAT_DOCUMENT: "document_formatting_provider",
    soleaux.lsp.contracts.LspCapability.FORMAT_RANGE: "document_range_formatting_provider",
    soleaux.lsp.contracts.LspCapability.CODE_ACTIONS: "code_action_provider",
    soleaux.lsp.contracts.LspCapability.COMPLETION: "completion_provider",
    soleaux.lsp.contracts.LspCapability.DIAGNOSTICS: "diagnostic_provider",
    soleaux.lsp.contracts.LspCapability.HOVER: "hover_provider",
    soleaux.lsp.contracts.LspCapability.INCOMING_CALLS: "call_hierarchy_provider",
    soleaux.lsp.contracts.LspCapability.OUTGOING_CALLS: "call_hierarchy_provider",
    soleaux.lsp.contracts.LspCapability.SIGNATURE_HELP: "signature_help_provider",
    soleaux.lsp.contracts.LspCapability.PREPARE_CALL_HIERARCHY: "call_hierarchy_provider",
    soleaux.lsp.contracts.LspCapability.RENAME: "rename_provider",
    soleaux.lsp.contracts.LspCapability.RENAME_STRICT: "rename_provider",
}

_SYMBOL_KIND_BY_NAME: dict[str, int] = {
    "file": 1,
    "module": 2,
    "namespace": 3,
    "package": 4,
    "class": 5,
    "method": 6,
    "property": 7,
    "field": 8,
    "constructor": 9,
    "enum": 10,
    "interface": 11,
    "function": 12,
    "variable": 13,
    "constant": 14,
    "string": 15,
    "number": 16,
    "boolean": 17,
    "array": 18,
    "object": 19,
    "key": 20,
    "null": 21,
    "enummember": 22,
    "struct": 23,
    "event": 24,
    "operator": 25,
    "typeparameter": 26,
}
_SYMBOL_NAME_BY_KIND = {value: key for key, value in _SYMBOL_KIND_BY_NAME.items()}


class LspPayloadError(ValueError):
    """A provider returned JSON that cannot satisfy the package-owned contract."""


class _JsonPayload(pydantic.BaseModel):
    """Pydantic boundary that retains a concrete JsonValue field type."""

    model_config = pydantic.ConfigDict(extra="forbid")

    value: pydantic.JsonValue


class SymbolIdentity(pydantic.BaseModel):
    """Canonical provider/location identity with generation-bound evidence."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    symbol_id: str = pydantic.Field(min_length=1)
    provider_name: str = pydantic.Field(min_length=1)
    generation_fingerprint: str = pydantic.Field(min_length=1)
    name: str | None = None
    kind: int | None = pydantic.Field(default=None, ge=1, le=26)
    location: soleaux.lsp.contracts.LspLocation

    @classmethod
    def from_location(
        cls,
        location: soleaux.lsp.contracts.LspLocation,
        *,
        provider_name: str,
        generation_fingerprint: str,
        name: str | None = None,
        kind: int | None = None,
    ) -> SymbolIdentity:
        """Build a stable symbol ID without folding generation into identity."""
        payload = {
            "provider_name": provider_name,
            "uri": location.uri,
            "range": location.range.model_dump(mode="json"),
            "name": name,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return cls(
            symbol_id=hashlib.sha256(canonical).hexdigest(),
            provider_name=provider_name,
            generation_fingerprint=generation_fingerprint,
            name=name,
            kind=kind,
            location=location,
        )


class WorkspaceSymbolCandidate(pydantic.BaseModel):
    """One exact, navigable workspace-symbol candidate."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str = pydantic.Field(min_length=1)
    kind: int | None = pydantic.Field(default=None, ge=1, le=26)
    location: soleaux.lsp.contracts.LspLocation


class WorkspaceSymbolMatchSet(pydantic.BaseModel):
    """Deterministically ordered, memory-bounded workspace-symbol matches."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[WorkspaceSymbolCandidate, ...] = ()
    truncated: bool = False


class CapabilityResolution(pydantic.BaseModel):
    """Normalized result for one package-owned LSP capability."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    capability: soleaux.lsp.contracts.LspCapability
    status: soleaux.contracts.coverage.FrameStatus
    generation: soleaux.lsp.generation.SemanticGeneration | None
    provider_identity: soleaux.lsp.contracts.ProviderProcessIdentity | None = None
    position_encoding: str | None = None
    locations: tuple[soleaux.lsp.contracts.LspLocation, ...] = ()
    symbols: tuple[SymbolIdentity, ...] = ()
    payload: pydantic.JsonValue = None
    omitted_reasons: tuple[str, ...] = ()


class SemanticResolution(CapabilityResolution):
    """Normalized result for one semantic navigation operation."""

    operation: soleaux.lsp.contracts.SemanticOperation


def navigation_capability(
    operation: soleaux.lsp.contracts.SemanticOperation,
) -> soleaux.lsp.contracts.LspCapability:
    """Map one closed navigation enum to the single capability catalog."""
    return _NAVIGATION_CAPABILITY[operation]


def capability_method(capability: soleaux.lsp.contracts.LspCapability) -> str:
    """Return the LSP method owned by the 17-capability catalog."""
    return soleaux.lsp.contracts.CAPABILITY_LSP_METHOD[capability]


def capability_supported(
    capabilities: soleaux.lsp.contracts.ServerCapabilities,
    capability: soleaux.lsp.contracts.LspCapability,
) -> bool:
    """Evaluate static initialize capabilities for one package capability."""
    if capability is soleaux.lsp.contracts.LspCapability.RESTART:
        return True
    attribute = _CAPABILITY_FLAG.get(capability)
    return bool(attribute and getattr(capabilities, attribute))


def normalize_json_payload(value: object) -> pydantic.JsonValue:
    """Validate an untrusted provider result as bounded JSON-compatible data."""
    try:
        return _JsonPayload.model_validate({"value": value}).value
    except ValueError as exc:
        msg = "provider result is not valid JSON data"
        raise LspPayloadError(msg) from exc


def lsp_position_from_user(
    content: bytes,
    *,
    line: int,
    column: int,
    position_encoding: str,
) -> soleaux.lsp.contracts.LspPosition:
    """Convert one-based Unicode code-point coordinates to negotiated LSP units."""
    if line < 1 or column < 1:
        msg = "user positions are one-based"
        raise ValueError(msg)
    codec = soleaux.contracts.positions.PositionCodec(content)
    zero_based_line = line - 1
    code_point_column = column - 1
    byte_offset = codec.point_to_byte(zero_based_line, code_point_column)
    point = codec.byte_to_point(byte_offset)
    if position_encoding == "utf-8":
        line_start = codec.point_to_byte(zero_based_line, 0)
        character = byte_offset - line_start
    elif position_encoding == "utf-16":
        character = point.utf16_column
    elif position_encoding == "utf-32":
        character = point.column
    else:
        msg = f"unsupported negotiated position encoding {position_encoding!r}"
        raise ValueError(msg)
    return soleaux.lsp.contracts.LspPosition(line=zero_based_line, character=character)


def user_position_from_lsp(
    content: bytes,
    *,
    line: int,
    character: int,
    position_encoding: str,
) -> tuple[int, int]:
    """Convert negotiated LSP units to one-based Unicode code-point coordinates."""
    try:
        encoding = soleaux.contracts.positions.PositionEncoding(position_encoding)
        codec = soleaux.contracts.positions.PositionCodec(content)
        if encoding is soleaux.contracts.positions.PositionEncoding.UTF8:
            line_start = codec.point_to_byte(line, 0)
            offset = line_start + character
            point = codec.byte_to_point(offset)
            if point.line != line:
                raise ValueError("provider position crosses a line boundary")
        else:
            offset = codec.point_to_byte(line, character, encoding=encoding)
            point = codec.byte_to_point(offset)
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"provider returned an invalid {position_encoding} position"
        raise LspPayloadError(msg) from exc
    return point.line + 1, point.column + 1


def symbol_kind_name(kind: int | None) -> str | None:
    """Return the stable wire name for one LSP SymbolKind value."""
    return _SYMBOL_NAME_BY_KIND.get(kind) if kind is not None else None


def workspace_symbol_candidates(
    value: object,
    *,
    symbol_name: str,
    symbol_kind: str | None = None,
    uri: str | None = None,
    limit: int,
) -> WorkspaceSymbolMatchSet:
    """Filter exact workspace symbols into a deterministic bounded top-k."""
    if limit < 1:
        raise ValueError("workspace symbol candidate limit must be positive")
    payload = normalize_json_payload(value)
    if not isinstance(payload, list):
        raise LspPayloadError("workspace symbol provider returned no candidate list")
    expected_kind = _symbol_kind_code(symbol_kind)
    ordered: list[
        tuple[
            tuple[str, int, int, int, int, int, str],
            WorkspaceSymbolCandidate,
        ]
    ] = []
    truncated = False
    for raw_candidate in payload:
        if not isinstance(raw_candidate, dict) or raw_candidate.get("name") != symbol_name:
            continue
        raw_kind = raw_candidate.get("kind")
        kind = raw_kind if isinstance(raw_kind, int) and not isinstance(raw_kind, bool) else None
        if expected_kind is not None and kind != expected_kind:
            continue
        raw_location = raw_candidate.get("location", raw_candidate)
        if not isinstance(raw_location, dict):
            continue
        location = _location_from_mapping(raw_location)
        if location is None or (uri is not None and location.uri != uri):
            continue
        candidate = WorkspaceSymbolCandidate(
            name=symbol_name,
            kind=kind,
            location=location,
        )
        key = _workspace_symbol_key(candidate)
        keys = [item[0] for item in ordered]
        insertion_index = bisect.bisect_left(keys, key)
        if insertion_index < len(ordered) and ordered[insertion_index][0] == key:
            continue
        ordered.insert(insertion_index, (key, candidate))
        if len(ordered) > limit:
            ordered.pop()
            truncated = True
    return WorkspaceSymbolMatchSet(
        candidates=tuple(candidate for _key, candidate in ordered),
        truncated=truncated,
    )


def locations_from_payload(
    value: pydantic.JsonValue,
) -> tuple[soleaux.lsp.contracts.LspLocation, ...]:
    """Normalize Location, LocationLink, or arrays of either."""
    candidates = value if isinstance(value, list) else [value]
    locations: list[soleaux.lsp.contracts.LspLocation] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        location = _location_from_mapping(candidate)
        if location is not None:
            locations.append(location)
    return tuple(locations)


def symbols_from_payload(
    value: pydantic.JsonValue,
    *,
    provider_name: str,
    generation_fingerprint: str,
) -> tuple[SymbolIdentity, ...]:
    """Normalize call-hierarchy items and call edges to canonical identities."""
    candidates = value if isinstance(value, list) else [value]
    symbols: list[SymbolIdentity] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("from") or candidate.get("to") or candidate
        if not isinstance(nested, dict):
            continue
        location = _location_from_mapping(nested)
        if location is None:
            continue
        raw_name = nested.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        raw_kind = nested.get("kind")
        kind = raw_kind if isinstance(raw_kind, int) and not isinstance(raw_kind, bool) else None
        symbols.append(
            SymbolIdentity.from_location(
                location,
                provider_name=provider_name,
                generation_fingerprint=generation_fingerprint,
                name=name,
                kind=kind if kind is None or 1 <= kind <= 26 else None,
            )
        )
    return tuple(symbols)


def _location_from_mapping(
    value: dict[str, pydantic.JsonValue],
) -> soleaux.lsp.contracts.LspLocation | None:
    raw_uri = value.get("uri") or value.get("targetUri")
    raw_range = (
        value.get("selectionRange")
        or value.get("targetSelectionRange")
        or value.get("range")
        or value.get("targetRange")
    )
    if not isinstance(raw_uri, str) or not isinstance(raw_range, dict):
        return None
    try:
        return soleaux.lsp.contracts.LspLocation(
            uri=raw_uri,
            range=soleaux.lsp.contracts.LspRange.model_validate(raw_range),
        )
    except ValueError as exc:
        msg = "provider location has an invalid range"
        raise LspPayloadError(msg) from exc


def _symbol_kind_code(symbol_kind: str | None) -> int | None:
    if symbol_kind is None:
        return None
    normalized = "".join(character for character in symbol_kind.casefold() if character.isalnum())
    expected = _SYMBOL_KIND_BY_NAME.get(normalized)
    if expected is None:
        raise LspPayloadError(f"unknown symbol_kind {symbol_kind!r}")
    return expected


def _workspace_symbol_key(
    candidate: WorkspaceSymbolCandidate,
) -> tuple[str, int, int, int, int, int, str]:
    location = candidate.location
    return (
        location.uri,
        location.range.start.line,
        location.range.start.character,
        location.range.end.line,
        location.range.end.character,
        candidate.kind or 0,
        candidate.name,
    )
