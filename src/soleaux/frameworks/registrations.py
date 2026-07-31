"""Project detected framework registrations into `framework.registrations`.

Rows are built only from bytes already frozen in the request snapshot, so a
registration can always anchor to exact evidence. Anything a detector could not
prove is reported as a coverage note instead of being silently dropped: zero
rows means "none found" only when this producer reports complete coverage.
"""

from __future__ import annotations

import collections.abc

import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.snapshot
import soleaux.frameworks.contracts
import soleaux.frameworks.nextjs
import soleaux.structural.fragments
import soleaux.structural.snapshot
import soleaux.tables.evidence

TABLE = "framework.registrations"
PROVIDER_VERSION = "1"

# Prefixes `RepositorySnapshotter` uses when a bound stopped the capture.
_CAPTURE_BOUND_PREFIXES: tuple[str, ...] = (
    "deadline exceeded during capture",
    "file count limit ",
    "byte limit ",
)
# Prefixes that name one file the snapshot deliberately excluded.
_SKIPPED_FILE_PREFIXES: tuple[str, ...] = (
    "skipped binary file ",
    "skipped oversized file ",
    "skipped non-UTF-8 file ",
)


def _snapshot_notes(bundle: soleaux.structural.snapshot.SnapshotBundle) -> list[str]:
    """Turn snapshot capture bounds into coverage reasons for this table."""
    notes: list[str] = []
    for note in bundle.notes:
        if note.startswith(_CAPTURE_BOUND_PREFIXES):
            notes.append(
                f"snapshot capture was bounded ({note}); registration coverage may be incomplete"
            )
            continue
        for prefix in _SKIPPED_FILE_PREFIXES:
            if note.startswith(prefix) and soleaux.frameworks.nextjs.is_metadata_stem(
                note[len(prefix) :]
            ):
                notes.append(
                    f"{note[len(prefix) :]} is a metadata route file that was not "
                    "captured; no registration emitted"
                )
    return notes


def _row(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    captured: soleaux.contracts.snapshot.CapturedFile,
    registration: soleaux.frameworks.contracts.Registration,
) -> soleaux.contracts.frame.FactRow:
    """Build one row anchored to the whole registration file.

    The anchor is unconditionally whole-file: `evidence_for_path` folds the range
    into the evidence identity, so a range that varied with co-requested tables
    would give the same logical row different ids across queries.
    """
    data: dict[str, object] = {
        # Keys other soleaux surfaces read generically.
        "path": registration.path,
        "kind": registration.kind.value,
        "name": registration.route or registration.kind.value,
        "start_line": 1,
        "start_column": 1,
        # The registration fact.
        "framework": registration.framework,
        "route": registration.route,
        "router": registration.router.value if registration.router else None,
        # Provenance upstream discards.
        "project_dir": registration.project_dir,
        # Structure upstream never exposes.
        "dynamic_segments": [
            {
                "segment": segment.segment,
                "param": segment.param,
                "kind": segment.kind.value,
                "index": segment.index,
            }
            for segment in registration.dynamic_segments
        ],
        "route_groups": list(registration.route_groups),
        "parallel_slots": list(registration.parallel_slots),
        "intercepting_marker": registration.intercepting_marker,
        "intercepting_target": registration.intercepting_target,
    }
    return soleaux.contracts.frame.FactRow(
        table=TABLE,
        data=data,
        evidence=soleaux.tables.evidence.evidence_for_path(
            bundle,
            path=registration.path,
            table=TABLE,
            data=data,
            evidence_kind=soleaux.contracts.evidence.EvidenceKind.STRUCTURAL,
            # A registration derived from a convention is fully resolved: the file
            # is known exactly. Reduced confidence marks an odd-but-certain
            # pattern, never uncertainty about whether the row belongs.
            resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
            authority=soleaux.contracts.evidence.Authority.SOURCE,
            provider=f"soleaux-frameworks-{registration.framework}",
            provider_version=PROVIDER_VERSION,
            confidence=registration.confidence,
            note=registration.note,
            byte_start=0,
            byte_end=captured.byte_end,
        ),
    )


def _next_config_analyses(
    fragments: collections.abc.Sequence[soleaux.structural.fragments.SyntaxFragment],
) -> dict[str, soleaux.frameworks.nextjs.NextConfigAnalysis]:
    return {
        fragment.path: soleaux.frameworks.nextjs.NextConfigAnalysis.from_fragment(fragment)
        for fragment in fragments
        if fragment.projection == soleaux.frameworks.nextjs.NEXT_CONFIG_PROJECTION
    }


def detectors(
    config_fragments: collections.abc.Sequence[soleaux.structural.fragments.SyntaxFragment] = (),
) -> tuple[soleaux.frameworks.nextjs.NextDetector, ...]:
    """The active detectors, request-scoped to this snapshot's config facts.

    Single owner: `describe` advertises what this returns, and enumeration runs
    exactly it, so a capability can never be advertised without being run.
    """
    return (soleaux.frameworks.nextjs.NextDetector(_next_config_analyses(config_fragments)),)


def build_registrations(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    config_fragments: collections.abc.Sequence[soleaux.structural.fragments.SyntaxFragment] = (),
) -> tuple[tuple[soleaux.contracts.frame.FactRow, ...], tuple[str, ...]]:
    """Enumerate framework registrations and why coverage is not authoritative."""
    captured_by_path = {captured.path: captured for captured in bundle.snapshot.files}
    # `RepositorySnapshotter` inventories in path order, and every detector's own
    # output is sorted by `Registration.sort_key`, so no sort is needed here.
    paths = list(captured_by_path)

    rows: list[soleaux.contracts.frame.FactRow] = []
    notes = _snapshot_notes(bundle)
    for detector in detectors(config_fragments):
        registrations, detector_notes = detector.enumerate(paths, bundle.contents)
        notes.extend(detector_notes)
        for registration in registrations:
            captured = captured_by_path.get(registration.path)
            # Evidence requires exact captured bytes; a path the snapshot never
            # admitted cannot be claimed.
            if captured is None or registration.path not in bundle.contents:
                continue
            rows.append(_row(bundle, captured, registration))
    # Match the planner's own convention so a mixed request makes clear which
    # table degraded the frame.
    return tuple(rows), tuple(f"{TABLE}: {note}" for note in notes)
