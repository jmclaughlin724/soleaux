"""LSP process results persist only through generation-bound catalog facts."""

from __future__ import annotations

import datetime
import pathlib

import soleaux.catalog.contracts
import soleaux.catalog.lsp
import soleaux.contracts.coverage
import soleaux.contracts.repository
import soleaux.contracts.snapshot
import soleaux.lsp.contracts
import soleaux.lsp.generation
import soleaux.lsp.operations
import soleaux.structural.snapshot


def _bundle(root: pathlib.Path) -> soleaux.structural.snapshot.SnapshotBundle:
    content = "target = '😀'\n".encode()
    path = "src/main.py"
    captured = soleaux.contracts.snapshot.CapturedFile(
        workspace_id="workspace",
        path=path,
        content_hash=soleaux.contracts.repository.content_digest(content),
        byte_start=0,
        byte_end=len(content),
        start_line=0,
        start_column=0,
        end_line=1,
        end_column=0,
        encoding="utf-8",
        newline="lf",
        language="Python",
        producer_id="test",
        producer_version="1",
        producer_config_digest="test",
        claim_basis=soleaux.contracts.snapshot.ClaimBasis.SYNTAX,
    )
    snapshot = soleaux.contracts.snapshot.RepositorySnapshot(
        snapshot_id="workspace:test",
        workspace_id="workspace",
        root=str(root),
        created_at=datetime.datetime.now(datetime.UTC),
        files=(captured,),
        source_fingerprint="fixture",
        changed_during_analysis=False,
    )
    return soleaux.structural.snapshot.SnapshotBundle(
        snapshot=snapshot,
        contents={path: content},
        notes=(),
    )


def _generation(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
) -> soleaux.lsp.generation.SemanticGeneration:
    return soleaux.lsp.generation.SemanticGeneration.from_snapshot(
        bundle,
        provider_name="pyright",
        provider_config_digest="provider-config",
        process_epoch=3,
        requested_file="src/main.py",
        project_identity=soleaux.lsp.generation.SemanticProjectIdentity(
            project_id="python:fixture",
            project_root="",
            project_config_digest="a" * 64,
            compiler_identity="pyright@1.1.390",
        ),
    )


def _process_identity() -> soleaux.lsp.contracts.ProviderProcessIdentity:
    return soleaux.lsp.contracts.ProviderProcessIdentity(
        configured_name="pyright",
        configured_version="1.1.390",
        server_info=soleaux.lsp.contracts.ServerInfo(name="pyright", version="1.1.407"),
        process_id=4312,
        process_epoch=3,
    )


def test_lsp_diagnostics_persist_actual_process_identity_and_byte_ranges(
    tmp_path: pathlib.Path,
) -> None:
    bundle = _bundle(tmp_path)
    generation = _generation(bundle)
    resolution = soleaux.lsp.operations.CapabilityResolution(
        capability=soleaux.lsp.contracts.LspCapability.DIAGNOSTICS,
        status=soleaux.contracts.coverage.FrameStatus.COMPLETE,
        generation=generation,
        provider_identity=_process_identity(),
        position_encoding="utf-16",
        payload=[
            {
                "range": {
                    "start": {"line": 0, "character": 10},
                    "end": {"line": 0, "character": 12},
                },
                "severity": 1,
                "code": "reportGeneralTypeIssues",
                "message": "fixture diagnostic",
            }
        ],
    )

    merged = soleaux.catalog.lsp.merge_lsp_resolution(
        soleaux.catalog.contracts.CatalogFacts(), bundle, resolution
    )

    assert len(merged.engines) == 1
    engine = merged.engines[0]
    assert engine.role is soleaux.catalog.contracts.EngineRole.LSP
    assert engine.package_version == "1.1.390"
    assert engine.runtime_version == "1.1.407"
    assert engine.process_id == 4312
    assert engine.process_epoch == 3
    assert engine.capabilities == ("diagnostics",)
    assert len(merged.diagnostics) == 1
    diagnostic = merged.diagnostics[0]
    assert diagnostic.byte_start == len(b"target = '")
    assert diagnostic.byte_end == len("target = '😀".encode())
    assert diagnostic.source_digest == generation.requested_hash


def test_lsp_symbols_normalize_file_uris_without_provider_objects(tmp_path: pathlib.Path) -> None:
    bundle = _bundle(tmp_path)
    generation = _generation(bundle)
    location = soleaux.lsp.contracts.LspLocation(
        uri=(tmp_path / "src" / "main.py").as_uri(),
        range=soleaux.lsp.contracts.LspRange(
            start=soleaux.lsp.contracts.LspPosition(line=0, character=0),
            end=soleaux.lsp.contracts.LspPosition(line=0, character=6),
        ),
    )
    resolution = soleaux.lsp.operations.CapabilityResolution(
        capability=soleaux.lsp.contracts.LspCapability.WORKSPACE_SYMBOL,
        status=soleaux.contracts.coverage.FrameStatus.COMPLETE,
        generation=generation,
        provider_identity=_process_identity(),
        position_encoding="utf-16",
        symbols=(
            soleaux.lsp.operations.SymbolIdentity.from_location(
                location,
                provider_name="pyright",
                generation_fingerprint=generation.fingerprint,
                name="target",
            ),
        ),
        payload=[],
    )

    merged = soleaux.catalog.lsp.merge_lsp_resolution(
        soleaux.catalog.contracts.CatalogFacts(), bundle, resolution
    )

    assert len(merged.symbols) == 1
    symbol = merged.symbols[0]
    assert symbol.path == "src/main.py"
    assert symbol.name == "target"
    assert symbol.byte_start == 0
    assert symbol.byte_end == 6
    assert symbol.engine_id == "lsp:python:fixture:pyright"
