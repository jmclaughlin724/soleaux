"""Bounded import of explicitly configured local coverage artifacts."""

from __future__ import annotations

import collections.abc
import json
import pathlib
import typing

import pydantic

import soleaux.contracts.config
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.tables
import soleaux.structural.snapshot

COVERAGE_ARTIFACT_SCHEMA_VERSION = "soleaux.coverage/v1"
MAX_COVERAGE_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_COVERAGE_RECORDS = 10_000


class _CoverageArtifactError(Exception):
    """A content-independent coverage artifact rejection."""


def _read_coverage_artifact(root: pathlib.Path, relative: str) -> bytes:
    try:
        artifact_path = (root / relative).resolve(strict=True)
    except OSError, RuntimeError:
        raise _CoverageArtifactError("could not be read") from None
    if not artifact_path.is_relative_to(root) or not artifact_path.is_file():
        raise _CoverageArtifactError("is not a contained regular file")
    try:
        with artifact_path.open("rb") as artifact_file:
            raw = artifact_file.read(MAX_COVERAGE_ARTIFACT_BYTES + 1)
    except OSError:
        raise _CoverageArtifactError("could not be read") from None
    if len(raw) > MAX_COVERAGE_ARTIFACT_BYTES:
        raise _CoverageArtifactError(f"exceeds the {MAX_COVERAGE_ARTIFACT_BYTES}-byte limit")
    return raw


def _parse_coverage_artifact(raw: bytes) -> CoverageArtifact:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, UnicodeDecodeError:
        raise _CoverageArtifactError("is not valid JSON") from None
    try:
        return CoverageArtifact.model_validate(parsed)
    except pydantic.ValidationError:
        raise _CoverageArtifactError("does not match the required schema") from None


class CoverageRecord(pydantic.BaseModel):
    """One metric from a trusted continuous-integration coverage artifact."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    subject: str = pydantic.Field(min_length=1, max_length=2048)
    metric: str = pydantic.Field(min_length=1, max_length=128)
    value: float = pydantic.Field(allow_inf_nan=False)
    hits: int | None = pydantic.Field(default=None, ge=0)
    total: int | None = pydantic.Field(default=None, ge=0)

    @pydantic.model_validator(mode="after")
    def _validate_counts(self) -> CoverageRecord:
        if self.hits is not None and self.total is not None and self.hits > self.total:
            raise ValueError("coverage hits cannot exceed total")
        return self


class CoverageArtifact(pydantic.BaseModel):
    """The closed JSON document accepted by ``format = "soleaux_json"``."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.coverage/v1"] = COVERAGE_ARTIFACT_SCHEMA_VERSION
    run_id: str = pydantic.Field(min_length=1, max_length=512)
    snapshot_id: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    source_fingerprint: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    records: tuple[CoverageRecord, ...] = pydantic.Field(max_length=MAX_COVERAGE_RECORDS)


class ImportedTableProducer:
    """Read only configured, contained local artifacts; never retrieve a URL."""

    supported_tables = soleaux.contracts.tables.PRODUCER_SUPPORTED_TABLES[
        soleaux.contracts.tables.Producer.IMPORTED
    ]

    def __init__(
        self, root: pathlib.Path, config: soleaux.contracts.config.CoverageImportConfig
    ) -> None:
        self._root = root.resolve()
        self._config = config
        self._coverage_notes: tuple[str, ...] = ()

    def coverage_notes(self) -> tuple[str, ...]:
        return self._coverage_notes

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        upstream_tables: collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]],
    ) -> collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        del semantic_mode, upstream_tables
        self._coverage_notes = ()
        if "coverage" not in table_names:
            return {}
        if not self._config.artifacts:
            self._coverage_notes = ("coverage: no trusted local artifacts are configured",)
            return {}

        rows: list[soleaux.contracts.frame.FactRow] = []
        notes: list[str] = []
        valid_artifacts = 0
        for configured in self._config.artifacts:
            relative = pathlib.PurePosixPath(configured.path).as_posix()
            try:
                raw = _read_coverage_artifact(self._root, relative)
                artifact = _parse_coverage_artifact(raw)
            except _CoverageArtifactError as exc:
                notes.append(f"coverage artifact {relative!r} {exc}")
                continue

            valid_artifacts += 1
            artifact_hash = soleaux.contracts.repository.content_digest(raw)
            snapshot_match = self._snapshot_match(artifact, bundle)
            if snapshot_match is not True:
                reason = (
                    "declares no snapshot identity"
                    if snapshot_match is None
                    else "does not match the current snapshot"
                )
                notes.append(f"coverage artifact {relative!r} {reason}")
            for record in artifact.records:
                data = {
                    "artifact_path": relative,
                    "artifact_hash": artifact_hash,
                    "artifact_format": configured.format,
                    "run_id": artifact.run_id,
                    "declared_snapshot_id": artifact.snapshot_id,
                    "declared_source_fingerprint": artifact.source_fingerprint,
                    "snapshot_match": snapshot_match,
                    **record.model_dump(mode="json"),
                }
                rows.append(
                    soleaux.contracts.frame.FactRow(
                        table="coverage",
                        data=data,
                        evidence=self._evidence(
                            bundle,
                            path=relative,
                            artifact_hash=artifact_hash,
                            artifact_bytes=len(raw),
                            data=data,
                            snapshot_match=snapshot_match,
                        ),
                    )
                )

        self._coverage_notes = tuple(dict.fromkeys(notes))
        if valid_artifacts == 0:
            return {}
        return {
            "coverage": tuple(
                sorted(
                    rows,
                    key=lambda row: (
                        str(row.data["artifact_path"]),
                        str(row.data["run_id"]),
                        str(row.data["subject"]),
                        str(row.data["metric"]),
                    ),
                )
            )
        }

    @staticmethod
    def _snapshot_match(
        artifact: CoverageArtifact,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
    ) -> bool | None:
        checks: list[bool] = []
        if artifact.snapshot_id is not None:
            checks.append(artifact.snapshot_id == bundle.snapshot.snapshot_id)
        if artifact.source_fingerprint is not None:
            checks.append(artifact.source_fingerprint == bundle.snapshot.source_fingerprint)
        return all(checks) if checks else None

    @staticmethod
    def _evidence(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        path: str,
        artifact_hash: str,
        artifact_bytes: int,
        data: collections.abc.Mapping[str, object],
        snapshot_match: bool | None,
    ) -> soleaux.contracts.evidence.Evidence:
        identity = json.dumps(
            {
                "table": "coverage",
                "path": path,
                "artifact_hash": artifact_hash,
                "data": data,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return soleaux.contracts.evidence.Evidence(
            evidence_id=soleaux.contracts.repository.content_digest(identity),
            evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
            resolution_status=(
                soleaux.contracts.evidence.ResolutionStatus.RESOLVED
                if snapshot_match is True
                else soleaux.contracts.evidence.ResolutionStatus.PARTIAL
            ),
            provider="soleaux-coverage-import",
            provider_version="1",
            authority=soleaux.contracts.evidence.Authority.GENERATED,
            snapshot_id=bundle.snapshot.snapshot_id,
            path=path,
            range=soleaux.contracts.evidence.PositionRange(
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=1,
                byte_start=0,
                byte_end=artifact_bytes,
            ),
            source_hash=artifact_hash,
            source_fingerprint=bundle.snapshot.source_fingerprint,
            confidence=1.0 if snapshot_match is True else 0.5,
            note="explicitly configured local continuous-integration artifact",
        )
