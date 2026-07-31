"""Project immutable catalog facts through the fixed relation table surface."""

from __future__ import annotations

import asyncio
import collections.abc
import json

import soleaux.catalog.contracts
import soleaux.catalog.generation
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.tables
import soleaux.structural.snapshot
import soleaux.tables.evidence


class CatalogTableProducer:
    """Return already-normalized catalog records without source or parser work."""

    supported_tables = soleaux.contracts.tables.PRODUCER_SUPPORTED_TABLES[
        soleaux.contracts.tables.Producer.CATALOG
    ]

    def __init__(self, generation: soleaux.catalog.generation.CatalogGeneration) -> None:
        self._generation = generation

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        upstream_tables: collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]],
    ) -> collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        del semantic_mode, upstream_tables
        return await asyncio.to_thread(self._produce_rows, table_names, bundle)

    def _produce_rows(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
    ) -> collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        sources: dict[str, tuple[soleaux.catalog.contracts.CatalogRecord, ...]] = {
            "repository.projects": self._generation.facts.projects,
            "repository.dependencies": self._generation.facts.dependencies,
            "repository.scripts": self._generation.facts.scripts,
            "repository.configurations": self._generation.facts.configs,
            "repository.engines": self._generation.facts.engines,
            "repository.typescript_routes": self._generation.facts.typescript_routes,
            "repository.routes": self._generation.facts.routes,
            "repository.rules": self._generation.facts.rules,
            "repository.symbols": self._generation.facts.symbols,
            "repository.imports": self._generation.facts.imports,
            "repository.diagnostics": self._generation.facts.diagnostics,
            "repository.changes": self._generation.facts.changes,
            "repository.chunks": self._generation.facts.chunks,
        }
        return {
            table_name: tuple(
                self._row(bundle, table_name, record) for record in sources[table_name]
            )
            for table_name in table_names
        }

    def _row(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        table_name: str,
        record: soleaux.catalog.contracts.CatalogRecord,
    ) -> soleaux.contracts.frame.FactRow:
        raw = record.model_dump(mode="json")
        data = {
            str(key): value
            for key, value in raw.items()
            if key
            not in {
                "schema_version",
                "workspace_id",
                "source_path",
                "source_digest",
                "producer",
                "producer_version",
                "text",
            }
        }
        path = str(raw["source_path"])
        try:
            evidence = soleaux.tables.evidence.evidence_for_path(
                bundle,
                path=path,
                table=table_name,
                data=data,
                evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
                resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
                authority=soleaux.contracts.evidence.Authority.MANIFEST,
                provider="soleaux-catalog-generation",
                provider_version="1",
                start_line=int(raw.get("start_line", 1)),
                end_line=int(raw.get("end_line", 1)),
                byte_start=(int(raw["byte_start"]) if "byte_start" in raw else None),
                byte_end=int(raw["byte_end"]) if "byte_end" in raw else None,
            )
        except ValueError:
            if table_name != "repository.changes" or raw.get("operation") != "deleted":
                raise
            evidence = self._deleted_path_evidence(path, table_name, data, raw)
        return soleaux.contracts.frame.FactRow(
            table=table_name,
            data=data,
            evidence=evidence,
        )

    def _deleted_path_evidence(
        self,
        path: str,
        table_name: str,
        data: collections.abc.Mapping[str, object],
        raw: collections.abc.Mapping[str, object],
    ) -> soleaux.contracts.evidence.Evidence:
        position = soleaux.contracts.evidence.PositionRange(
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=1,
        )
        source_hash = str(raw["source_digest"])
        identity = json.dumps(
            {
                "table": table_name,
                "data": data,
                "path": path,
                "source_hash": source_hash,
                "source_fingerprint": self._generation.source_fingerprint,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return soleaux.contracts.evidence.Evidence(
            evidence_id=soleaux.contracts.repository.content_digest(identity),
            evidence_kind=soleaux.contracts.evidence.EvidenceKind.METADATA,
            resolution_status=soleaux.contracts.evidence.ResolutionStatus.RESOLVED,
            provider="soleaux-catalog-generation",
            provider_version="1",
            authority=soleaux.contracts.evidence.Authority.SOURCE,
            snapshot_id=self._generation.snapshot_id,
            path=path,
            range=position,
            source_hash=source_hash,
            source_fingerprint=self._generation.source_fingerprint,
            confidence=1,
            note="deleted path is anchored to its prior source digest",
        )
