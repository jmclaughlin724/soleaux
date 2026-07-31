"""Evidence construction from exact snapshot bytes or their persisted digest."""

from __future__ import annotations

import collections.abc
import json

import soleaux.contracts.evidence
import soleaux.contracts.repository
import soleaux.structural.snapshot


def _provided_or_default(value: int | None, default: int) -> int:
    return default if value is None else value


def evidence_for_path(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    path: str,
    table: str,
    data: collections.abc.Mapping[str, object],
    evidence_kind: soleaux.contracts.evidence.EvidenceKind,
    resolution_status: soleaux.contracts.evidence.ResolutionStatus,
    authority: soleaux.contracts.evidence.Authority,
    provider: str,
    provider_version: str,
    confidence: float = 1.0,
    note: str = "",
    start_line: int = 1,
    start_column: int = 1,
    end_line: int | None = None,
    end_column: int | None = None,
    byte_start: int | None = None,
    byte_end: int | None = None,
) -> soleaux.contracts.evidence.Evidence:
    """Create one stable evidence record over an exact captured-file identity."""
    captured = bundle.files_by_path.get(path)
    if captured is None:
        raise ValueError(f"evidence path is absent from the snapshot: {path!r}")
    content = bundle.contents.get(path)
    source_hash = (
        captured.content_hash
        if content is None
        else soleaux.contracts.repository.content_digest(content)
    )
    resolved_end_line = _provided_or_default(
        end_line,
        max(captured.end_line + 1, start_line),
    )
    resolved_end_column = _provided_or_default(
        end_column,
        max(captured.end_column + 1, 1),
    )
    position = soleaux.contracts.evidence.PositionRange(
        start_line=start_line,
        start_column=start_column,
        end_line=resolved_end_line,
        end_column=resolved_end_column,
        byte_start=byte_start,
        byte_end=byte_end,
    )
    identity = json.dumps(
        {
            "table": table,
            "data": data,
            "path": path,
            "range": position.model_dump(mode="json"),
            "source_hash": source_hash,
            "source_fingerprint": bundle.snapshot.source_fingerprint,
            "provider": provider,
            "provider_version": provider_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return soleaux.contracts.evidence.Evidence(
        evidence_id=soleaux.contracts.repository.content_digest(identity),
        evidence_kind=evidence_kind,
        resolution_status=resolution_status,
        provider=provider,
        provider_version=provider_version,
        authority=authority,
        snapshot_id=bundle.snapshot.snapshot_id,
        path=path,
        range=position,
        source_hash=source_hash,
        source_fingerprint=bundle.snapshot.source_fingerprint,
        confidence=confidence,
        note=note,
    )


def derived_evidence(
    source: soleaux.contracts.evidence.Evidence,
    *,
    table: str,
    data: collections.abc.Mapping[str, object],
    resolution_status: soleaux.contracts.evidence.ResolutionStatus = (
        soleaux.contracts.evidence.ResolutionStatus.RESOLVED
    ),
    confidence: float | None = None,
    note: str = "",
) -> soleaux.contracts.evidence.Evidence:
    """Derive one row without increasing the source fact's confidence."""
    derived_confidence = (
        source.confidence if confidence is None else min(confidence, source.confidence)
    )
    identity = json.dumps(
        {
            "source_evidence_id": source.evidence_id,
            "table": table,
            "data": data,
            "resolution_status": resolution_status.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return soleaux.contracts.evidence.Evidence(
        evidence_id=soleaux.contracts.repository.content_digest(identity),
        evidence_kind=soleaux.contracts.evidence.EvidenceKind.HEURISTIC,
        resolution_status=resolution_status,
        provider="soleaux-relations",
        provider_version="1",
        authority=soleaux.contracts.evidence.Authority.INFERRED,
        snapshot_id=source.snapshot_id,
        path=source.path,
        range=source.range,
        source_hash=source.source_hash,
        source_fingerprint=source.source_fingerprint,
        confidence=derived_confidence,
        note=note,
    )
