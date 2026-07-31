"""D017: deterministic, bounded request-local relation materialization."""

from __future__ import annotations

import pathlib

import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.workspace
import soleaux.relations.materializer
import soleaux.structural.snapshot
import soleaux.tables.evidence


async def _capture(tmp_path: pathlib.Path) -> soleaux.structural.snapshot.SnapshotBundle:
    files = {
        path: f"# {path}\n"
        for path in (
            "a.py",
            "b.py",
            "c.py",
            "core.py",
            "dead.py",
            "entry.py",
            "external.py",
            "left.py",
            "right.py",
            "used.py",
            "x.py",
            "y.py",
        )
    }
    for relative, content in files.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="topology-test",
    ).get("workspace")
    return await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=tuple(files)
    )


def _row(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    table: str,
    source_path: str,
    data: dict[str, object],
    evidence_kind: soleaux.contracts.evidence.EvidenceKind = (
        soleaux.contracts.evidence.EvidenceKind.SEMANTIC
    ),
    resolution_status: soleaux.contracts.evidence.ResolutionStatus = (
        soleaux.contracts.evidence.ResolutionStatus.RESOLVED
    ),
    authority: soleaux.contracts.evidence.Authority = (soleaux.contracts.evidence.Authority.SOURCE),
) -> soleaux.contracts.frame.FactRow:
    return soleaux.contracts.frame.FactRow(
        table=table,
        data=data,
        evidence=soleaux.tables.evidence.evidence_for_path(
            bundle,
            path=source_path,
            table=table,
            data=data,
            evidence_kind=evidence_kind,
            resolution_status=resolution_status,
            authority=authority,
            provider="topology-fixture",
            provider_version="1",
        ),
    )


def _import_edge(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    source_path: str,
    target_path: str,
    *,
    status: soleaux.contracts.evidence.ResolutionStatus = (
        soleaux.contracts.evidence.ResolutionStatus.RESOLVED
    ),
) -> soleaux.contracts.frame.FactRow:
    return _row(
        bundle,
        table="semantic.imports",
        source_path=source_path,
        data={
            "source_path": source_path,
            "target_path": target_path,
            "specifier": target_path,
        },
        resolution_status=status,
    )


async def test_dependencies_and_consumers_use_only_eligible_resolved_edges(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(tmp_path)
    materializer = soleaux.relations.materializer.DerivedMaterializer()
    resolved_import = _import_edge(bundle, "a.py", "b.py")
    candidate_external = _import_edge(
        bundle,
        "a.py",
        "external.py",
        status=soleaux.contracts.evidence.ResolutionStatus.CANDIDATE,
    )
    resolved_call = _row(
        bundle,
        table="semantic.calls",
        source_path="c.py",
        data={"source_path": "c.py", "target_path": "b.py", "callee": "run"},
    )
    structural_impostor = _row(
        bundle,
        table="semantic.imports",
        source_path="x.py",
        data={"source_path": "x.py", "target_path": "y.py", "specifier": "./y"},
        evidence_kind=soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
    )
    manifest_edge = _row(
        bundle,
        table="manifest.dependencies",
        source_path="entry.py",
        data={"source_path": "entry.py", "target_path": "core.py"},
        evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
        authority=soleaux.contracts.evidence.Authority.MANIFEST,
    )

    dependencies = materializer.dependencies(
        (resolved_import, candidate_external, structural_impostor, manifest_edge)
    )
    consumers = materializer.consumers(
        (resolved_import, candidate_external, structural_impostor, resolved_call)
    )

    assert [(row.data["source_path"], row.data["target_path"]) for row in dependencies] == [
        ("a.py", "b.py"),
        ("entry.py", "core.py"),
    ]
    assert [(row.data["source_path"], row.data["consumer_path"]) for row in consumers] == [
        ("b.py", "a.py"),
        ("b.py", "c.py"),
    ]


async def test_impact_is_deterministic_and_reports_truncation(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(tmp_path)
    materializer = soleaux.relations.materializer.DerivedMaterializer()
    imports = (
        _import_edge(bundle, "entry.py", "left.py"),
        _import_edge(bundle, "entry.py", "right.py"),
        _import_edge(bundle, "left.py", "core.py"),
        _import_edge(bundle, "right.py", "core.py"),
        _import_edge(bundle, "x.py", "y.py"),
    )
    dependencies = materializer.dependencies(imports)

    result = materializer.impact(
        dependencies,
        seeds=("core.py",),
        limits=soleaux.relations.materializer.TopologyLimits(
            max_rows=2, max_depth=4, max_bytes=4096, timeout_ms=1000
        ),
    )

    assert result.truncated is True
    assert result.reasons == ("row limit 2 reached",)
    assert [(row.data["path"], row.data["depth"]) for row in result.rows] == [
        ("left.py", 1),
        ("right.py", 1),
    ]


async def test_cycles_are_deterministic_across_input_order(tmp_path: pathlib.Path) -> None:
    bundle = await _capture(tmp_path)
    materializer = soleaux.relations.materializer.DerivedMaterializer()
    dependencies = materializer.dependencies(
        (
            _import_edge(bundle, "a.py", "b.py"),
            _import_edge(bundle, "b.py", "a.py"),
            _import_edge(bundle, "b.py", "c.py"),
            _import_edge(bundle, "x.py", "y.py"),
        )
    )

    forward = materializer.cycles(dependencies)
    reverse = materializer.cycles(tuple(reversed(dependencies)))

    assert [row.data for row in forward] == [row.data for row in reverse]
    assert len(forward) == 1
    assert forward[0].data["members"] == ("a.py", "b.py")


async def test_dead_code_candidates_keep_partial_and_dynamic_uncertainty(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(tmp_path)
    materializer = soleaux.relations.materializer.DerivedMaterializer()
    symbols = tuple(
        _row(
            bundle,
            table="semantic.symbols",
            source_path=path,
            data={"symbol_id": symbol_id, "path": path, "name": symbol_id},
        )
        for symbol_id, path in (
            ("entry", "entry.py"),
            ("used", "used.py"),
            ("dead", "dead.py"),
        )
    )
    entrypoint = _row(
        bundle,
        table="authority.entrypoints",
        source_path="entry.py",
        data={"target": "entry.py", "name": "entry", "entrypoint_kind": "application"},
        evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
        authority=soleaux.contracts.evidence.Authority.MANIFEST,
    )
    source_consumer = _import_edge(bundle, "entry.py", "used.py")
    consumer_data = {
        "source_path": "used.py",
        "consumer_path": "entry.py",
        "edge_kind": "semantic.imports",
    }
    consumer = soleaux.contracts.frame.FactRow(
        table="derived.consumers",
        data=consumer_data,
        evidence=soleaux.tables.evidence.derived_evidence(
            source_consumer.evidence,
            table="derived.consumers",
            data=consumer_data,
        ),
    )

    candidates = materializer.dead_code_candidates(
        symbols,
        entrypoints=(entrypoint,),
        consumers=(consumer,),
        semantic_complete=False,
        dynamic_inputs=True,
    )

    assert len(candidates) == 1
    assert candidates[0].data["symbol_id"] == "dead"
    assert candidates[0].data["certainty"] == "uncertain"
    assert candidates[0].data["uncertainty"] == (
        "dynamic relations present",
        "semantic coverage is partial",
    )
    assert (
        candidates[0].evidence.resolution_status
        is soleaux.contracts.evidence.ResolutionStatus.CANDIDATE
    )


async def test_edge_eligibility_is_shape_based_not_table_based(tmp_path: pathlib.Path) -> None:
    """A resolved semantic row with both path keys is an edge whatever its table.

    This is what lets a new language's resolved-reference producer feed the
    derived graphs without adding a case to the materializer.
    """
    bundle = await _capture(tmp_path)
    materializer = soleaux.relations.materializer.DerivedMaterializer()
    reference = _row(
        bundle,
        table="semantic.references",
        source_path="a.py",
        data={"source_path": "a.py", "target_path": "b.py"},
    )

    dependencies = materializer.dependencies((reference,))
    consumers = materializer.consumers((reference,))

    assert [(r.data["source_path"], r.data["target_path"]) for r in dependencies] == [
        ("a.py", "b.py")
    ]
    assert [(r.data["source_path"], r.data["consumer_path"]) for r in consumers] == [
        ("b.py", "a.py")
    ]


async def test_semantic_rows_without_a_path_pair_are_not_edges(tmp_path: pathlib.Path) -> None:
    """`semantic.symbols` rows carry `path`/`symbol_id`, so shape excludes them."""
    bundle = await _capture(tmp_path)
    symbol = _row(
        bundle,
        table="semantic.symbols",
        source_path="a.py",
        data={"symbol_id": "a::f", "path": "a.py", "name": "f"},
    )

    assert soleaux.relations.materializer.DerivedMaterializer().dependencies((symbol,)) == ()
