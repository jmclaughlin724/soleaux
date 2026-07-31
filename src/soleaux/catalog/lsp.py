"""Generation-bound LSP facts normalized into the repository catalog."""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pydantic

import soleaux.catalog.contracts
import soleaux.contracts.coverage
import soleaux.contracts.positions
import soleaux.contracts.repository
import soleaux.lsp.contracts
import soleaux.lsp.operations
import soleaux.structural.snapshot

LSP_CATALOG_PRODUCER = "soleaux.lsp"
LSP_CATALOG_VERSION = "1"
LSP_PROTOCOL_VERSION = "3.17"


class LspCatalogError(ValueError):
    """An LSP result does not belong to the captured catalog generation."""


@dataclasses.dataclass(slots=True)
class _WorkspaceBoundary:
    workspace_id: str
    root: pathlib.Path


def merge_lsp_resolution(
    facts: soleaux.catalog.contracts.CatalogFacts,
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    resolution: soleaux.lsp.operations.CapabilityResolution,
) -> soleaux.catalog.contracts.CatalogFacts:
    """Merge verified semantic facts without retaining provider wire objects."""
    generation = resolution.generation
    if generation is None or resolution.provider_identity is None:
        return facts
    if resolution.status not in {
        soleaux.contracts.coverage.FrameStatus.COMPLETE,
        soleaux.contracts.coverage.FrameStatus.PARTIAL,
    }:
        return facts
    _verify_generation(bundle, resolution)

    engine = _engine_fact(resolution)
    engines = _merge_engine(facts.engines, engine)
    symbols = _merge_symbols(
        facts.symbols,
        _symbol_facts(bundle, resolution, engine.engine_id),
    )
    diagnostics = facts.diagnostics
    if resolution.capability is soleaux.lsp.contracts.LspCapability.DIAGNOSTICS:
        diagnostics = _merge_diagnostics(
            facts.diagnostics,
            _diagnostic_facts(bundle, resolution, engine.engine_id),
            project_id=generation.project_id,
            engine_id=engine.engine_id,
            path=generation.requested_file,
        )
    return facts.model_copy(
        update={
            "engines": engines,
            "symbols": symbols,
            "diagnostics": diagnostics,
        }
    )


def _verify_generation(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    resolution: soleaux.lsp.operations.CapabilityResolution,
) -> None:
    generation = resolution.generation
    if generation is None:
        raise LspCatalogError("semantic result has no generation")
    if generation.workspace_id != bundle.snapshot.workspace_id:
        raise LspCatalogError("semantic result belongs to another workspace")
    captured = {
        row.path: row.content_hash for row in bundle.snapshot.files if row.path in bundle.contents
    }
    expected = {
        generation.requested_file: generation.requested_hash,
        **{item.path: item.content_hash for item in generation.dependencies},
        **{item.path: item.content_hash for item in generation.controls},
    }
    if any(captured.get(path) != digest for path, digest in expected.items()):
        raise LspCatalogError("semantic result source digests are stale")


def _engine_fact(
    resolution: soleaux.lsp.operations.CapabilityResolution,
) -> soleaux.catalog.contracts.EngineFact:
    generation = resolution.generation
    identity = resolution.provider_identity
    if generation is None or identity is None or generation.requested_hash is None:
        raise LspCatalogError("semantic result has no complete engine evidence")
    runtime_version = identity.server_info.version if identity.server_info is not None else None
    reported_name = identity.server_info.name if identity.server_info is not None else None
    engine_id = f"lsp:{generation.project_id}:{identity.configured_name}"
    return soleaux.catalog.contracts.EngineFact(
        workspace_id=generation.workspace_id,
        source_path=generation.requested_file,
        source_digest=generation.requested_hash,
        producer=LSP_CATALOG_PRODUCER,
        producer_version=LSP_CATALOG_VERSION,
        project_id=generation.project_id,
        engine_id=engine_id,
        role=soleaux.catalog.contracts.EngineRole.LSP,
        package_name=identity.configured_name,
        package_version=identity.configured_version,
        runtime_version=runtime_version,
        protocol_version=LSP_PROTOCOL_VERSION,
        process_id=identity.process_id,
        process_epoch=identity.process_epoch,
        reported_name=reported_name,
        capabilities=(resolution.capability.value,),
        available=True,
        coverage="initialized",
        omitted_reasons=(
            ()
            if runtime_version is not None
            else ("provider initialize response omitted serverInfo.version",)
        ),
    )


def _merge_engine(
    existing: tuple[soleaux.catalog.contracts.EngineFact, ...],
    engine: soleaux.catalog.contracts.EngineFact,
) -> tuple[soleaux.catalog.contracts.EngineFact, ...]:
    previous = next((item for item in existing if item.engine_id == engine.engine_id), None)
    capabilities = tuple(
        sorted(
            {
                *engine.capabilities,
                *(previous.capabilities if previous is not None else ()),
            }
        )
    )
    updated = engine.model_copy(update={"capabilities": capabilities})
    return tuple(
        sorted(
            (
                *(item for item in existing if item.engine_id != engine.engine_id),
                updated,
            ),
            key=lambda item: (item.project_id, item.engine_id),
        )
    )


def _symbol_facts(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    resolution: soleaux.lsp.operations.CapabilityResolution,
    engine_id: str,
) -> tuple[soleaux.catalog.contracts.SymbolFact, ...]:
    generation = resolution.generation
    if generation is None:
        return ()
    boundary = _WorkspaceBoundary(
        workspace_id=bundle.snapshot.workspace_id,
        root=pathlib.Path(bundle.snapshot.root),
    )
    captured = {row.path: row for row in bundle.snapshot.files}
    symbols: list[soleaux.catalog.contracts.SymbolFact] = []
    for symbol in resolution.symbols:
        try:
            path = soleaux.contracts.repository.RepositoryPath.admit(
                boundary, symbol.location.uri
            ).value
            content = bundle.contents[path]
            captured_file = captured[path]
            byte_start, byte_end = _byte_range(
                content,
                symbol.location.range,
                resolution.position_encoding,
            )
        except KeyError, UnicodeDecodeError, ValueError:
            continue
        name = symbol.name
        if not name:
            continue
        logical_identity = (
            f"{generation.workspace_id}\0{generation.project_id}\0{path}\0"
            f"{name}\0lsp\0{byte_start}\0{byte_end}"
        ).encode()
        symbol_id = soleaux.contracts.repository.content_digest(logical_identity)
        revision_identity = (
            f"{symbol_id}\0{engine_id}\0{captured_file.content_hash}\0{generation.fingerprint}"
        ).encode()
        symbols.append(
            soleaux.catalog.contracts.SymbolFact(
                workspace_id=generation.workspace_id,
                source_path=path,
                source_digest=captured_file.content_hash,
                producer=LSP_CATALOG_PRODUCER,
                producer_version=LSP_CATALOG_VERSION,
                symbol_id=symbol_id,
                revision_id=soleaux.contracts.repository.content_digest(revision_identity),
                project_id=generation.project_id,
                path=path,
                name=name,
                symbol_kind=soleaux.lsp.operations.symbol_kind_name(symbol.kind) or "symbol",
                byte_start=byte_start,
                byte_end=byte_end,
                engine_id=engine_id,
                coverage="semantic",
            )
        )
    return tuple(
        sorted(
            symbols,
            key=lambda item: (item.path, item.byte_start, item.byte_end, item.symbol_id),
        )
    )


def _merge_symbols(
    existing: tuple[soleaux.catalog.contracts.SymbolFact, ...],
    additions: tuple[soleaux.catalog.contracts.SymbolFact, ...],
) -> tuple[soleaux.catalog.contracts.SymbolFact, ...]:
    replacement_keys = {(item.engine_id, item.symbol_id) for item in additions}
    retained = (
        item for item in existing if (item.engine_id, item.symbol_id) not in replacement_keys
    )
    return tuple(
        sorted(
            (*retained, *additions),
            key=lambda item: (
                item.project_id,
                item.path,
                item.byte_start,
                item.engine_id,
                item.symbol_id,
            ),
        )
    )


def _diagnostic_facts(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    resolution: soleaux.lsp.operations.CapabilityResolution,
    engine_id: str,
) -> tuple[soleaux.catalog.contracts.DiagnosticFact, ...]:
    generation = resolution.generation
    if generation is None or generation.requested_hash is None:
        return ()
    payload = resolution.payload
    if not isinstance(payload, list):
        return ()
    content = bundle.contents.get(generation.requested_file)
    if content is None:
        return ()
    diagnostics: list[soleaux.catalog.contracts.DiagnosticFact] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        mapping = raw
        raw_range = mapping.get("range")
        message = mapping.get("message")
        if not isinstance(raw_range, dict) or not isinstance(message, str) or not message:
            continue
        try:
            diagnostic_range = soleaux.lsp.contracts.LspRange.model_validate(raw_range)
            byte_start, byte_end = _byte_range(
                content,
                diagnostic_range,
                resolution.position_encoding,
            )
        except UnicodeDecodeError, ValueError:
            continue
        code_value = mapping.get("code")
        code = str(code_value) if isinstance(code_value, (str, int)) else None
        category = _diagnostic_category(mapping.get("severity"))
        identity_payload = json.dumps(
            {
                "workspace_id": generation.workspace_id,
                "project_id": generation.project_id,
                "engine_id": engine_id,
                "path": generation.requested_file,
                "source_digest": generation.requested_hash,
                "category": category,
                "code": code,
                "message": message,
                "byte_start": byte_start,
                "byte_end": byte_end,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        diagnostics.append(
            soleaux.catalog.contracts.DiagnosticFact(
                workspace_id=generation.workspace_id,
                source_path=generation.requested_file,
                source_digest=generation.requested_hash,
                producer=LSP_CATALOG_PRODUCER,
                producer_version=LSP_CATALOG_VERSION,
                diagnostic_id=soleaux.contracts.repository.content_digest(identity_payload),
                project_id=generation.project_id,
                path=generation.requested_file,
                engine_id=engine_id,
                category=category,
                code=code,
                message=message,
                byte_start=byte_start,
                byte_end=byte_end,
                coverage="semantic",
            )
        )
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (item.byte_start, item.byte_end, item.diagnostic_id),
        )
    )


def _merge_diagnostics(
    existing: tuple[soleaux.catalog.contracts.DiagnosticFact, ...],
    additions: tuple[soleaux.catalog.contracts.DiagnosticFact, ...],
    *,
    project_id: str,
    engine_id: str,
    path: str,
) -> tuple[soleaux.catalog.contracts.DiagnosticFact, ...]:
    retained = (
        item
        for item in existing
        if not (item.project_id == project_id and item.engine_id == engine_id and item.path == path)
    )
    return tuple(
        sorted(
            (*retained, *additions),
            key=lambda item: (
                item.project_id,
                item.path,
                item.byte_start,
                item.engine_id,
                item.diagnostic_id,
            ),
        )
    )


def _byte_range(
    content: bytes,
    value: soleaux.lsp.contracts.LspRange,
    position_encoding: str | None,
) -> tuple[int, int]:
    codec = soleaux.contracts.positions.PositionCodec(content)
    encoding = soleaux.contracts.positions.PositionEncoding(
        position_encoding or soleaux.contracts.positions.PositionEncoding.UTF16
    )
    return (
        _byte_offset(codec, value.start.line, value.start.character, encoding),
        _byte_offset(codec, value.end.line, value.end.character, encoding),
    )


def _byte_offset(
    codec: soleaux.contracts.positions.PositionCodec,
    line: int,
    character: int,
    encoding: soleaux.contracts.positions.PositionEncoding,
) -> int:
    if encoding is not soleaux.contracts.positions.PositionEncoding.UTF8:
        return codec.point_to_byte(line, character, encoding=encoding)
    line_start = codec.point_to_byte(line, 0)
    byte_offset = line_start + character
    if codec.byte_to_point(byte_offset).line != line:
        raise ValueError("UTF-8 LSP position crosses a line boundary")
    return byte_offset


def _diagnostic_category(value: pydantic.JsonValue) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return "diagnostic"
    return {
        1: "error",
        2: "warning",
        3: "information",
        4: "hint",
    }.get(value, "diagnostic")
