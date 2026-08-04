"""RepositorySnapshot to AnalysisFrame orchestration and producer adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, TypeGuard

from pydantic import SecretStr

from soleaux.authority.governance import collect_policy_facts
from soleaux.authority.resolver import AuthorityResolver
from soleaux.catalog.contracts import (
    CatalogFacts,
    EngineFact,
    EngineRole,
    SemanticCallSite,
    SemanticLocation,
)
from soleaux.catalog.generation import (
    CatalogGeneration,
    CatalogGenerationBuilder,
    catalog_generation_from_facts,
    changed_snapshot_paths,
)
from soleaux.catalog.lsp import merge_lsp_resolution
from soleaux.catalog.postgresql import (
    PostgreSqlCatalogContext,
    PostgreSqlCatalogExtraction,
    merge_postgresql_catalog,
    postgresql_catalog_failure_warning,
    rebind_postgresql_catalog,
    resolve_postgresql_catalog,
    source_lane_for_path,
)
from soleaux.catalog.search import (
    RankedHit,
    SearchMatchMode,
    fts_match_expression,
    linear_search,
)
from soleaux.catalog.store import CatalogStore, CatalogStoreError
from soleaux.catalog.structural import (
    STRUCTURAL_CATALOG_PROJECTIONS,
    ExtractedFile,
    merge_structural_facts,
)
from soleaux.catalog.tables import CatalogTableProducer
from soleaux.catalog.typescript import (
    build_typescript_requests,
    merge_typescript_analysis,
    typescript_request_paths,
)
from soleaux.contracts.budget import StructuralCatalogBudget
from soleaux.contracts.config import (
    CatalogMode,
    ProviderConfig,
    ResolvedConfig,
    config_digest,
    resolved_config_bytes,
)
from soleaux.contracts.coverage import Coverage, FrameStatus, RowFileByteDepthLimits
from soleaux.contracts.evidence import Authority, EvidenceKind, ResolutionStatus
from soleaux.contracts.frame import AnalysisFrame, FactRow
from soleaux.contracts.positions import PositionCodec
from soleaux.contracts.repository import RepositoryPath, content_digest
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.snapshot import CapturedFile
from soleaux.contracts.tables import PRODUCER_SUPPORTED_TABLES, Producer
from soleaux.contracts.workspace import WorkspaceRoot
from soleaux.frameworks.nextjs import NEXT_CONFIG_PROJECTION, is_next_config_path
from soleaux.frameworks.registrations import build_registrations
from soleaux.lsp.generation import SemanticProjectIdentity
from soleaux.lsp.operations import CapabilityResolution
from soleaux.lsp.providers import ConfiguredProvider, ProviderRegistry
from soleaux.lsp.resolvers import SemanticResolver
from soleaux.postgresql.runtime import (
    capture_inherited_environment,
    environment_names_for_provider,
)
from soleaux.relations.materializer import DerivedMaterializer
from soleaux.relations.resolver import RelationResolver
from soleaux.structural.engines import StructuralEngines
from soleaux.structural.fragments import SyntaxFragment
from soleaux.structural.projections import SUPPORTED_CATALOG_LANGUAGES
from soleaux.structural.rules import load_packaged_rule, rule_supports_language
from soleaux.structural.snapshot import (
    RepositorySnapshotter,
    SnapshotBundle,
    SnapshotLimits,
    snapshot_fingerprint,
)
from soleaux.structural.standards import WorkspaceStandardsAnalyzer
from soleaux.structural.supervisor import (
    MAX_CONTENT_BYTES,
    StructuralWorkerSupervisor,
    WorkerJobError,
    WorkerUnavailableError,
)
from soleaux.tables.evidence import evidence_for_path
from soleaux.tables.imported import ImportedTableProducer
from soleaux.tables.planner import TablePlanner, TableProducer
from soleaux.typescript.node_runtime import (
    TypeScriptNodeRuntime,
    TypeScriptRuntimeError,
    resolve_typescript_installation,
)

_DIRECT_PROJECTIONS = frozenset(
    {
        "syntax.call_sites",
        "syntax.declarations",
        "syntax.exports",
        "syntax.imports",
        "syntax.members",
        "syntax.references",
    }
)
_CANDIDATE_PROJECTIONS = frozenset(
    {
        "syntax.call_sites",
        "syntax.imports",
        "syntax.references",
        "entrypoint_candidates",
    }
)
_DECLARATION_FILTERS = frozenset({"entrypoint_candidates", "tests"})
_REGISTRATION_TABLE = "framework.registrations"
_RECONCILE_INTERVAL_SECONDS = 2.0
_UNSUPPORTED_PROJECTION_SAMPLE_PATHS = 3
_BASE_AUTHORITY_SOURCE_TABLES = frozenset(
    {
        "repository.projects",
        "repository.scripts",
    }
)
_TYPESCRIPT_CATALOG_TABLES = frozenset(
    {
        "repository.dependencies",
        "repository.engines",
        "repository.symbols",
        "repository.imports",
        "repository.diagnostics",
        "repository.routes",
        "repository.typescript_routes",
    }
)
_FileSignature = tuple[int, int, int, int, int]


def _is_object_sequence(
    value: object,
) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


@dataclass(frozen=True)
class FrameBuild:
    """The canonical frame and exact frozen bytes that produced it."""

    frame: AnalysisFrame
    bundle: SnapshotBundle
    catalog_generation: CatalogGeneration | None


@dataclass(frozen=True)
class _CachedPostgreSqlExtraction:
    """Raw parser output and exact captured bytes retained across generations."""

    source: bytes
    extraction: PostgreSqlCatalogExtraction


def _merge_postgresql_projection(
    facts: CatalogFacts,
    *,
    workspace_id: str,
    sources: Mapping[str, bytes],
    bindings: Sequence[tuple[PostgreSqlCatalogExtraction, PostgreSqlCatalogContext]],
) -> CatalogFacts:
    raw = tuple(rebind_postgresql_catalog(extraction, context) for extraction, context in bindings)
    return merge_postgresql_catalog(
        facts,
        workspace_id=workspace_id,
        sources=sources,
        extractions=resolve_postgresql_catalog(raw),
    )


class SnapshotTableProducer:
    """Project captured file identities into `repository.files`."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.SNAPSHOT]

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        del semantic_mode, upstream_tables
        if "repository.files" not in table_names:
            return {}
        return await asyncio.to_thread(self._produce_rows, bundle)

    @staticmethod
    def _produce_rows(
        bundle: SnapshotBundle,
    ) -> Mapping[str, tuple[FactRow, ...]]:
        rows: list[FactRow] = []
        for captured in sorted(bundle.snapshot.files, key=lambda item: item.path):
            data = {
                "path": captured.path,
                "content_hash": captured.content_hash,
                "language": captured.language,
                "byte_count": captured.byte_end,
                "encoding": captured.encoding,
                "newline": captured.newline,
            }
            rows.append(
                FactRow(
                    table="repository.files",
                    data=data,
                    evidence=evidence_for_path(
                        bundle,
                        path=captured.path,
                        table="repository.files",
                        data=data,
                        evidence_kind=EvidenceKind.METADATA,
                        resolution_status=ResolutionStatus.RESOLVED,
                        authority=Authority.SOURCE,
                        provider="repository-snapshotter",
                        provider_version="1",
                    ),
                )
            )
        return {"repository.files": tuple(rows)}


class _BaseAuthorityTableProducer:
    """Publish declaration authority while deferring repository-wide inference."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.AUTHORITY]

    def __init__(
        self,
        resolver: AuthorityResolver,
        *,
        governance_paths: frozenset[str],
    ) -> None:
        self._resolver = resolver
        self._governance_paths = governance_paths
        self._coverage_notes: tuple[str, ...] = ()

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        manifest_paths = {
            row.evidence.path
            for table_name in _BASE_AUTHORITY_SOURCE_TABLES
            for row in upstream_tables.get(table_name, ())
        }
        retained_paths = self._governance_paths.union(manifest_paths)
        declaration_bundle = SnapshotBundle(
            snapshot=bundle.snapshot,
            contents={
                path: content if path in retained_paths else b""
                for path, content in bundle.contents.items()
            },
            notes=bundle.notes,
        )
        output = await self._resolver.produce(
            table_names,
            declaration_bundle,
            semantic_mode,
            {},
        )
        self._coverage_notes = tuple(
            dict.fromkeys(
                (
                    *self._resolver.coverage_notes(),
                    "authority base publication defers complete repository relationship inference",
                )
            )
        )
        return output

    def coverage_notes(self) -> tuple[str, ...]:
        return self._coverage_notes


class StructuralTableProducer:
    """Run selected projections once per captured file through the lazy worker."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.STRUCTURAL]

    def __init__(
        self,
        supervisor: StructuralWorkerSupervisor,
        *,
        prime_tables: Sequence[str] = (),
        policy: WorkspaceStandardsAnalyzer | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._prime_tables = tuple(prime_tables)
        self._policy = policy
        self._fragments: dict[str, list[SyntaxFragment]] = {}
        self._prepared_snapshot_id: str | None = None
        self._registrations: tuple[FactRow, ...] = ()
        self._coverage_notes: tuple[str, ...] = ()

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        del semantic_mode, upstream_tables
        await self.prepare(
            bundle,
            tuple(
                table_name
                for table_name in (*self._prime_tables, *table_names)
                if table_name != "quality.standards"
            ),
        )
        output = await asyncio.to_thread(
            self._rows_for_tables,
            bundle,
            tuple(
                table_name
                for table_name in table_names
                if table_name in self.supported_tables and table_name != "quality.standards"
            ),
        )
        notes = list(self._coverage_notes)
        if "quality.standards" in table_names:
            if self._policy is None:
                notes.append(
                    "quality.standards: configured structural-policy analyzer is unavailable"
                )
            else:
                result = await self._policy.scan(bundle)
                notes.extend(result.warnings)
                if result.available:
                    output["quality.standards"] = result.rows
        self._coverage_notes = tuple(dict.fromkeys(notes))
        return output

    def _rows_for_tables(
        self,
        bundle: SnapshotBundle,
        table_names: tuple[str, ...],
    ) -> dict[str, tuple[FactRow, ...]]:
        return {table_name: self._rows_for_table(bundle, table_name) for table_name in table_names}

    def coverage_notes(self) -> tuple[str, ...]:
        """Reasons the last registration pass was not authoritative."""
        return self._coverage_notes

    async def fragments(
        self,
        bundle: SnapshotBundle,
        table_name: str,
    ) -> tuple[SyntaxFragment, ...]:
        await self.prepare(bundle, (*self._prime_tables, table_name))
        return tuple(self._fragments.get(table_name, ()))

    async def symbol_rows(
        self,
        bundle: SnapshotBundle,
        query: str,
        *,
        row_limit: int,
    ) -> tuple[tuple[FactRow, ...], bool]:
        """Return bounded declaration-name matches without materializing unrelated rows."""
        target_count = row_limit + 1
        normalized_query = query.casefold()
        fragments: list[SyntaxFragment] = []
        worker_truncated = False
        for captured in sorted(bundle.snapshot.files, key=lambda item: item.path):
            language = captured.language
            if language is None:
                continue
            content = bundle.contents[captured.path]
            if normalized_query not in content.decode("utf-8").casefold():
                continue
            remaining = target_count - len(fragments)
            if remaining <= 0:
                break
            result = await self._supervisor.extract(
                language=language,
                path=captured.path,
                content=content,
                projections=("syntax.declarations",),
                symbol_query=query,
                symbol_max_results=remaining,
                workspace_id=bundle.snapshot.workspace_id,
            )
            worker_truncated = worker_truncated or result.truncated
            fragments.extend(
                fragment
                for fragment in result.fragments
                if fragment.projection == "syntax.declarations"
            )
            if len(fragments) >= target_count or worker_truncated:
                break
        self._fragments["syntax.declarations"] = fragments
        rows = self._rows_for_table(bundle, "syntax.declarations")
        truncated = worker_truncated or len(rows) > row_limit
        return rows[:row_limit], truncated

    async def rule_rows(
        self,
        bundle: SnapshotBundle,
        rule_id: str,
    ) -> tuple[tuple[FactRow, ...], bool]:
        """Evaluate one packaged rule over every compatible captured file."""
        rule = load_packaged_rule(rule_id)
        table_name = f"rules.{rule.id}"
        fragments: list[SyntaxFragment] = []
        truncated = False
        for captured in sorted(bundle.snapshot.files, key=lambda item: item.path):
            if captured.language is None or not rule_supports_language(rule, captured.language):
                continue
            result = await self._supervisor.extract(
                language=captured.language,
                path=captured.path,
                content=bundle.contents[captured.path],
                projections=(),
                rules=(rule.id,),
                workspace_id=bundle.snapshot.workspace_id,
            )
            truncated = truncated or result.truncated
            fragments.extend(
                fragment for fragment in result.fragments if fragment.projection == table_name
            )
        self._fragments[table_name] = fragments
        return self._rows_for_table(bundle, table_name), truncated

    async def prepare(
        self,
        bundle: SnapshotBundle,
        table_names: Sequence[str],
    ) -> None:
        if self._prepared_snapshot_id == bundle.snapshot.snapshot_id and all(
            table_name in self._fragments for table_name in table_names
        ):
            return
        if self._prepared_snapshot_id != bundle.snapshot.snapshot_id:
            self._fragments.clear()
            self._registrations = ()
            self._coverage_notes = ()
            self._prepared_snapshot_id = bundle.snapshot.snapshot_id

        requested = set(table_names)
        needs_registrations = (
            _REGISTRATION_TABLE in requested and _REGISTRATION_TABLE not in self._fragments
        )
        projections = {table_name for table_name in requested if table_name in _DIRECT_PROJECTIONS}
        if requested & _DECLARATION_FILTERS:
            projections.add("syntax.declarations")
        if needs_registrations:
            projections.add(NEXT_CONFIG_PROJECTION)
        missing = tuple(sorted(projections - self._fragments.keys()))
        if not missing and not needs_registrations:
            for table_name in requested:
                self._fragments.setdefault(table_name, [])
            return

        diagnostic_notes: list[str] = []
        unsupported_counts: dict[tuple[str, str], int] = {}
        unsupported_samples: dict[tuple[str, str], list[str]] = {}
        for table_name in missing:
            self._fragments.setdefault(table_name, [])
        for captured in sorted(bundle.snapshot.files, key=lambda item: item.path):
            if captured.language is None:
                continue
            file_projections = tuple(
                projection
                for projection in missing
                if projection != NEXT_CONFIG_PROJECTION or is_next_config_path(captured.path)
            )
            if not file_projections:
                continue
            result = await self._supervisor.extract(
                language=captured.language,
                path=captured.path,
                content=bundle.contents[captured.path],
                projections=file_projections,
                workspace_id=bundle.snapshot.workspace_id,
            )
            diagnostic_notes.extend(
                f"{diagnostic.path}: structural {diagnostic.severity}: "
                f"{' '.join(diagnostic.message.split())[:512]}"
                for diagnostic in result.diagnostics
            )
            for projection in result.unsupported:
                key = (projection, captured.language)
                unsupported_counts[key] = unsupported_counts.get(key, 0) + 1
                sample = unsupported_samples.setdefault(key, [])
                if len(sample) < _UNSUPPORTED_PROJECTION_SAMPLE_PATHS:
                    sample.append(captured.path)
            for fragment in result.fragments:
                self._fragments.setdefault(fragment.projection, []).append(fragment)

        # Per-file unsupported notes amplify to thousands of identical reasons at
        # repository scale; one note per (projection, language) keeps the same
        # signal without exhausting consumer byte budgets.
        for projection, language in sorted(unsupported_counts):
            count = unsupported_counts[(projection, language)]
            samples = ", ".join(unsupported_samples[(projection, language)])
            diagnostic_notes.append(
                f"structural projection {projection!r} is unsupported for {language} "
                f"({count} files; e.g. {samples})"
            )

        registration_notes: tuple[str, ...] = ()
        if needs_registrations:
            self._registrations, registration_notes = build_registrations(
                bundle,
                config_fragments=self._fragments.get(NEXT_CONFIG_PROJECTION, ()),
            )
            self._fragments.setdefault(_REGISTRATION_TABLE, [])
        self._coverage_notes = tuple(
            dict.fromkeys(
                (
                    *self._coverage_notes,
                    *diagnostic_notes,
                    *registration_notes,
                )
            )
        )

        declarations = tuple(self._fragments.get("syntax.declarations", ()))
        if "entrypoint_candidates" in requested:
            self._fragments["entrypoint_candidates"] = [
                fragment
                for fragment in declarations
                if fragment.name in {"main", "__main__"} or fragment.path.endswith("__main__.py")
            ]
        if "tests" in requested:
            self._fragments["tests"] = [
                fragment for fragment in declarations if self._is_test_path(fragment.path)
            ]
        for table_name in requested:
            self._fragments.setdefault(table_name, [])

    def _rows_for_table(
        self,
        bundle: SnapshotBundle,
        table_name: str,
    ) -> tuple[FactRow, ...]:
        if table_name == _REGISTRATION_TABLE:
            return self._registrations
        rows: list[FactRow] = []
        for fragment in sorted(
            self._fragments.get(table_name, ()),
            key=lambda item: (item.path, item.byte_start, item.byte_end, item.name or ""),
        ):
            data = {
                "path": fragment.path,
                "kind": fragment.kind,
                "name": fragment.name,
                "language": fragment.language,
                "text_preview": fragment.text_preview,
                "attributes": fragment.attributes,
                "byte_start": fragment.byte_start,
                "byte_end": fragment.byte_end,
                "start_line": fragment.start_line + 1,
                "start_column": fragment.start_column + 1,
                "end_line": fragment.end_line + 1,
                "end_column": fragment.end_column + 1,
            }
            rows.append(
                FactRow(
                    table=table_name,
                    data=data,
                    evidence=evidence_for_path(
                        bundle,
                        path=fragment.path,
                        table=table_name,
                        data=data,
                        evidence_kind=EvidenceKind.STRUCTURAL,
                        resolution_status=(
                            ResolutionStatus.CANDIDATE
                            if table_name in _CANDIDATE_PROJECTIONS
                            else ResolutionStatus.RESOLVED
                        ),
                        authority=Authority.SOURCE,
                        provider="soleaux-structural-worker",
                        provider_version="1",
                        confidence=0.6 if table_name in _CANDIDATE_PROJECTIONS else 1.0,
                        start_line=fragment.start_line + 1,
                        start_column=fragment.start_column + 1,
                        end_line=fragment.end_line + 1,
                        end_column=fragment.end_column + 1,
                        byte_start=fragment.byte_start,
                        byte_end=fragment.byte_end,
                    ),
                )
            )
        return tuple(rows)

    @staticmethod
    def _is_test_path(path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or ".test." in name
            or ".spec." in name
        )


class SemanticTableProducer:
    """Project normalized semantic catalog facts and resolve relation candidates."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.SEMANTIC]
    _LOCATION_FIELDS: ClassVar[dict[str, tuple[str, str]]] = {
        "semantic.definitions": ("definitions", "definitions"),
        "semantic.references": ("references", "references"),
        "semantic.implementations": ("implementations", "implementations"),
    }
    _SYMBOL_CAPABILITIES = frozenset({"checker_symbols", "language_service", "workspace_symbol"})
    _DIAGNOSTIC_CAPABILITIES = frozenset(
        {
            "diagnostics",
            "syntactic_diagnostics",
            "bind_diagnostics",
            "semantic_diagnostics",
            "suggestion_diagnostics",
            "declaration_diagnostics",
            "program_diagnostics",
            "global_diagnostics",
            "config_diagnostics",
        }
    )

    def __init__(
        self,
        structural: StructuralTableProducer,
        semantic: SemanticResolver,
    ) -> None:
        self._structural = structural
        self._semantic = semantic
        self._coverage_notes: tuple[str, ...] = ()

    def coverage_notes(self) -> tuple[str, ...]:
        return self._coverage_notes

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        output: dict[str, tuple[FactRow, ...]] = {}
        notes: list[str] = []
        engines = self._engine_capabilities(upstream_tables.get("repository.engines", ()))
        source_symbols = upstream_tables.get("repository.symbols", ())
        semantic_symbols = tuple(
            row for row in source_symbols if row.data.get("coverage") == "semantic"
        )
        syntactic_symbols = tuple(
            row for row in source_symbols if row.data.get("coverage") != "semantic"
        )
        symbol_engine_ids = {
            str(row.data.get("engine_id", ""))
            for row in semantic_symbols
            if row.data.get("engine_id")
        }
        symbol_capable_engines = self._capable_engines(
            engines,
            self._SYMBOL_CAPABILITIES,
        )
        symbol_plane_available = bool(semantic_symbols or symbol_capable_engines)

        if "semantic.symbols" in table_names:
            if symbol_plane_available:
                projected_symbols = tuple(
                    row
                    for source in semantic_symbols
                    if (row := self._symbol_row(bundle, source)) is not None
                )
                output["semantic.symbols"] = tuple(
                    sorted(
                        projected_symbols,
                        key=lambda row: (
                            str(row.data.get("path", "")),
                            int(row.data.get("byte_start", 0)),
                            int(row.data.get("byte_end", 0)),
                            str(row.data.get("symbol_id", "")),
                        ),
                    )
                )
                if len(projected_symbols) != len(semantic_symbols):
                    notes.append(
                        "semantic.symbols: malformed or stale normalized facts were omitted"
                    )
                missing_engines = symbol_engine_ids - engines.keys()
                if missing_engines:
                    notes.append(
                        "semantic.symbols: semantic engine identity is missing for "
                        f"{sorted(missing_engines)}"
                    )
                if syntactic_symbols:
                    notes.append(
                        "semantic.symbols: syntactic catalog symbols lack semantic enrichment"
                    )
            else:
                notes.append(
                    "semantic.symbols: no normalized semantic symbol provider is available"
                )

        for table_name, (field, capability) in self._LOCATION_FIELDS.items():
            if table_name not in table_names:
                continue
            if not symbol_plane_available:
                notes.append(f"{table_name}: semantic symbol prerequisite is unavailable")
                continue
            capable_engines = self._capable_engines(engines, frozenset({capability}))
            eligible_symbols = tuple(
                row
                for row in semantic_symbols
                if str(row.data.get("engine_id", "")) in capable_engines
            )
            if not capable_engines:
                notes.append(f"{table_name}: no normalized provider advertises {capability}")
                continue
            location_rows = tuple(
                location_row
                for source in eligible_symbols
                for location_row in self._location_rows(
                    bundle,
                    table_name=table_name,
                    source=source,
                    field=field,
                )
            )
            output[table_name] = tuple(
                sorted(
                    location_rows,
                    key=lambda row: (
                        str(row.data.get("source_symbol_id", "")),
                        str(row.data.get("path", "")),
                        int(row.data.get("byte_start", 0)),
                        int(row.data.get("byte_end", 0)),
                    ),
                )
            )
            expected_locations = 0
            for source in eligible_symbols:
                raw_locations = source.data.get(field)
                if _is_object_sequence(raw_locations):
                    expected_locations += len(raw_locations)
            if len(location_rows) != expected_locations:
                notes.append(f"{table_name}: malformed or stale normalized locations were omitted")
            if len(eligible_symbols) != len(semantic_symbols):
                notes.append(
                    f"{table_name}: some semantic symbol providers do not advertise {capability}"
                )

        imports: tuple[SyntaxFragment, ...] = ()
        calls: tuple[SyntaxFragment, ...] = ()
        if "semantic.imports" in table_names:
            imports = await self._structural.fragments(bundle, "syntax.imports")
        if "semantic.calls" in table_names:
            calls = await self._structural.fragments(bundle, "syntax.call_sites")
        relation_tables = tuple(
            table_name
            for table_name in table_names
            if table_name in {"semantic.imports", "semantic.calls"}
        )
        if relation_tables:
            relation_output = await RelationResolver(
                import_candidates=imports,
                call_candidates=calls,
                symbol_resolver=self._semantic,
            ).produce(relation_tables, bundle, semantic_mode, {})
            output.update(relation_output)
            if "semantic.calls" in relation_tables:
                call_engines = self._capable_engines(engines, frozenset({"calls"}))
                catalog_calls = tuple(
                    call_row
                    for source in semantic_symbols
                    if str(source.data.get("engine_id", "")) in call_engines
                    for call_row in self._call_rows(bundle, source)
                )
                output["semantic.calls"] = tuple(
                    sorted(
                        (*output.get("semantic.calls", ()), *catalog_calls),
                        key=lambda row: (
                            row.evidence.path,
                            row.evidence.range.byte_start or 0,
                            row.evidence.range.byte_end or 0,
                            row.evidence.evidence_id,
                        ),
                    )
                )
            for table_name, rows in relation_output.items():
                if any(
                    row.evidence.resolution_status is not ResolutionStatus.RESOLVED for row in rows
                ):
                    notes.append(
                        f"{table_name}: one or more candidates lack exact semantic resolution"
                    )

        if "quality.diagnostics" in table_names:
            diagnostics = upstream_tables.get("repository.diagnostics", ())
            capable_engines = self._capable_engines(
                engines,
                self._DIAGNOSTIC_CAPABILITIES,
            )
            if diagnostics or capable_engines:
                projected_diagnostic_rows: list[FactRow] = []
                for source in diagnostics:
                    projected = self._diagnostic_row(bundle, source)
                    if projected is not None:
                        projected_diagnostic_rows.append(projected)
                projected_diagnostics = tuple(projected_diagnostic_rows)
                output["quality.diagnostics"] = tuple(
                    sorted(
                        projected_diagnostics,
                        key=lambda row: (
                            str(row.data.get("path", "")),
                            int(row.data.get("byte_start", 0)),
                            int(row.data.get("byte_end", 0)),
                            str(row.data.get("diagnostic_id", "")),
                        ),
                    )
                )
                if len(projected_diagnostics) != len(diagnostics):
                    notes.append(
                        "quality.diagnostics: malformed or stale normalized facts were omitted"
                    )
                missing_engines = {
                    str(row.data.get("engine_id", ""))
                    for row in diagnostics
                    if row.data.get("engine_id") and str(row.data["engine_id"]) not in engines
                }
                if missing_engines:
                    notes.append(
                        "quality.diagnostics: diagnostic engine identity is missing for "
                        f"{sorted(missing_engines)}"
                    )
            else:
                notes.append("quality.diagnostics: no normalized diagnostic provider is available")

        self._coverage_notes = tuple(dict.fromkeys(notes))
        return output

    @staticmethod
    def _engine_capabilities(
        rows: tuple[FactRow, ...],
    ) -> dict[str, frozenset[str]]:
        capabilities: dict[str, frozenset[str]] = {}
        for row in rows:
            engine_id = row.data.get("engine_id")
            raw_capabilities = row.data.get("capabilities")
            if not isinstance(engine_id, str) or not engine_id:
                continue
            if row.data.get("available") is not True:
                continue
            capability_values = raw_capabilities if _is_object_sequence(raw_capabilities) else ()
            capabilities[engine_id] = frozenset(str(capability) for capability in capability_values)
        return capabilities

    @staticmethod
    def _capable_engines(
        engines: Mapping[str, frozenset[str]],
        accepted: frozenset[str],
    ) -> frozenset[str]:
        return frozenset(
            engine_id
            for engine_id, capabilities in engines.items()
            if capabilities.intersection(accepted)
        )

    @classmethod
    def _symbol_row(
        cls,
        bundle: SnapshotBundle,
        source: FactRow,
    ) -> FactRow | None:
        data = {
            key: value
            for key, value in source.data.items()
            if key
            not in {
                "declarations",
                "definitions",
                "implementations",
                "references",
                "calls",
            }
        }
        return cls._semantic_row(
            bundle,
            table_name="semantic.symbols",
            data=data,
            path=str(source.data.get("path", source.evidence.path)),
            byte_start=int(source.data.get("byte_start", 0)),
            byte_end=int(source.data.get("byte_end", 0)),
            provider=str(source.data.get("engine_id", "semantic-catalog")),
            evidence_kind=EvidenceKind.SEMANTIC,
        )

    @classmethod
    def _location_rows(
        cls,
        bundle: SnapshotBundle,
        *,
        table_name: str,
        source: FactRow,
        field: str,
    ) -> tuple[FactRow, ...]:
        raw_locations = source.data.get(field)
        if not _is_object_sequence(raw_locations):
            return ()
        rows: list[FactRow] = []
        for raw in raw_locations:
            try:
                location = SemanticLocation.model_validate(raw)
            except ValueError:
                continue
            data: dict[str, object] = {
                "source_symbol_id": source.data.get("symbol_id"),
                "source_revision_id": source.data.get("revision_id"),
                "project_id": source.data.get("project_id"),
                "source_path": source.data.get("path"),
                "source_name": source.data.get("name"),
                "path": location.path,
                "byte_start": location.byte_start,
                "byte_end": location.byte_end,
                "kind": location.kind,
                "name": location.name,
                "engine_id": source.data.get("engine_id"),
            }
            row = cls._semantic_row(
                bundle,
                table_name=table_name,
                data=data,
                path=location.path,
                byte_start=location.byte_start,
                byte_end=location.byte_end,
                provider=str(source.data.get("engine_id", "semantic-catalog")),
                evidence_kind=EvidenceKind.SEMANTIC,
            )
            if row is not None:
                rows.append(row)
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    str(row.data.get("source_symbol_id", "")),
                    str(row.data.get("path", "")),
                    int(row.data.get("byte_start", 0)),
                    int(row.data.get("byte_end", 0)),
                ),
            )
        )

    @classmethod
    def _call_rows(
        cls,
        bundle: SnapshotBundle,
        source: FactRow,
    ) -> tuple[FactRow, ...]:
        raw_calls = source.data.get("calls")
        if not _is_object_sequence(raw_calls):
            return ()
        rows: list[FactRow] = []
        for raw in raw_calls:
            try:
                call = SemanticCallSite.model_validate(raw)
            except ValueError:
                continue
            data: dict[str, object] = {
                "source_path": call.path,
                "target_path": source.data.get("path"),
                "target_uri": None,
                "callee": call.callee,
                "symbol_id": source.data.get("symbol_id"),
                "generation_fingerprint": source.evidence.source_fingerprint,
                "dynamic": False,
                "external": False,
                "generated": False,
                "overload_count": 1,
                "target_line": source.evidence.range.start_line,
                "target_column": source.evidence.range.start_column,
            }
            row = cls._semantic_row(
                bundle,
                table_name="semantic.calls",
                data=data,
                path=call.path,
                byte_start=call.byte_start,
                byte_end=call.byte_end,
                provider=str(source.data.get("engine_id", "semantic-catalog")),
                evidence_kind=EvidenceKind.SEMANTIC,
            )
            if row is not None:
                rows.append(row)
        return tuple(rows)

    @classmethod
    def _diagnostic_row(
        cls,
        bundle: SnapshotBundle,
        source: FactRow,
    ) -> FactRow | None:
        data = dict(source.data)
        return cls._semantic_row(
            bundle,
            table_name="quality.diagnostics",
            data=data,
            path=str(source.data.get("path", source.evidence.path)),
            byte_start=int(source.data.get("byte_start", 0)),
            byte_end=int(source.data.get("byte_end", 0)),
            provider=str(source.data.get("engine_id", "diagnostic-catalog")),
            evidence_kind=EvidenceKind.METADATA,
        )

    @staticmethod
    def _semantic_row(
        bundle: SnapshotBundle,
        *,
        table_name: str,
        data: dict[str, object],
        path: str,
        byte_start: int,
        byte_end: int,
        provider: str,
        evidence_kind: EvidenceKind,
    ) -> FactRow | None:
        content = bundle.contents.get(path)
        if content is None or byte_start > byte_end or byte_end > len(content):
            return None
        codec = PositionCodec(content)
        try:
            start = codec.byte_to_point(byte_start)
            end = codec.byte_to_point(byte_end)
        except UnicodeDecodeError, ValueError:
            return None
        return FactRow(
            table=table_name,
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=path,
                table=table_name,
                data=data,
                evidence_kind=evidence_kind,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=Authority.SOURCE,
                provider=provider,
                provider_version="1",
                start_line=start.line + 1,
                start_column=start.column + 1,
                end_line=end.line + 1,
                end_column=end.column + 1,
                byte_start=byte_start,
                byte_end=byte_end,
            ),
        )


def build_provider_registry(
    root: Path,
    config: ResolvedConfig,
) -> ProviderRegistry:
    """Merge built-in defaults with [providers.*] config overrides and customs."""
    if not config.providers:
        return ProviderRegistry.default(
            root,
            logs_retention_days=config.health.logs_retention_days,
            temp_retention_hours=config.health.temp_retention_hours,
        )

    from soleaux.lsp.providers import BUILTIN_PROVIDERS

    defaults = ProviderRegistry.default(
        root,
        logs_retention_days=config.health.logs_retention_days,
        temp_retention_hours=config.health.temp_retention_hours,
    )
    builtin_names = {p.name for p in BUILTIN_PROVIDERS}

    providers: list[ConfiguredProvider] = []
    overridden: set[str] = set()

    for name, cfg in config.providers.items():
        if name in builtin_names or name in {p.provider_name for p in defaults.providers}:
            overridden.add(name)
            if not cfg.enabled:
                continue
            if cfg.command is None:
                providers.extend(p for p in defaults.providers if p.provider_name == name)
                continue
            providers.append(
                _build_configured_provider(
                    name,
                    cfg,
                    root,
                    logs_retention_days=config.health.logs_retention_days,
                    temp_retention_hours=config.health.temp_retention_hours,
                )
            )
        elif cfg.enabled and cfg.command is not None:
            providers.append(
                _build_configured_provider(
                    name,
                    cfg,
                    root,
                    logs_retention_days=config.health.logs_retention_days,
                    temp_retention_hours=config.health.temp_retention_hours,
                )
            )

    for provider in defaults.providers:
        if provider.provider_name not in overridden:
            providers.append(provider)

    if not providers:
        return ProviderRegistry.default(root)
    return ProviderRegistry(tuple(providers))


def _build_configured_provider(
    name: str,
    cfg: ProviderConfig,
    root: Path,
    *,
    logs_retention_days: int,
    temp_retention_hours: int,
) -> ConfiguredProvider:
    """Build a ConfiguredProvider from a ProviderConfig entry."""
    assert cfg.command is not None
    argv = tuple(cfg.command)
    provider_name = Path(argv[0]).name
    environment_names = environment_names_for_provider(name) or environment_names_for_provider(
        provider_name
    )
    environment = capture_inherited_environment(environment_names)
    provider_root = (root / cfg.root_dir).resolve(strict=True)
    digest_payload = {
        "argv": list(argv),
        "environment_names": environment_names,
        "extensions": list(cfg.extensions),
        "initialization_options": cfg.initialization_options,
        "root": str(provider_root),
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ConfiguredProvider(
        provider_name=provider_name,
        provider_version="unprobed",
        argv=argv,
        extensions=tuple(ext.removeprefix(".").lower() for ext in cfg.extensions),
        initialization_options=dict(cfg.initialization_options),
        root=provider_root,
        config_digest=digest,
        environment_names=environment_names,
        environment={name: SecretStr(value) for name, value in environment.items()},
        logs_retention_days=logs_retention_days,
        temp_retention_hours=temp_retention_hours,
    )


class AnalysisFrameBuilder:
    """Build the one canonical frame while keeping analyzers lazy and request-local."""

    def __init__(
        self,
        supervisor: StructuralWorkerSupervisor | None = None,
        *,
        config: ResolvedConfig | None = None,
        configs: dict[str, ResolvedConfig] | None = None,
        config_content_digest: str | None = None,
        storage_namespace: str | None = None,
    ) -> None:
        self._supervisor = supervisor or StructuralWorkerSupervisor()
        self._semantic: dict[str, SemanticResolver] = {}
        self._structural_engines: dict[str, StructuralEngines] = {}
        self._config = config or ResolvedConfig.default()
        self._configs = dict(configs) if configs is not None else {}
        self._config_digest = config_content_digest or config_digest(
            resolved_config_bytes(self._config)
        )
        self._storage_namespace = storage_namespace
        self._captured_bundles: dict[str, SnapshotBundle] = {}
        self._capture_inventory: dict[str, tuple[str, ...]] = {}
        self._capture_signatures: dict[str, dict[str, _FileSignature]] = {}
        self._capture_dirty_hints: dict[str, set[str]] = {}
        self._last_reconciled_at: dict[str, float] = {}
        self._capture_locks: dict[str, asyncio.Lock] = {}
        self._catalog_generations: dict[str, CatalogGeneration] = {}
        self._catalog_stores: dict[str, CatalogStore] = {}
        self._catalog_locks: dict[str, asyncio.Lock] = {}
        self._catalog_loaded_from_store: set[str] = set()
        self._catalog_base_fingerprints: dict[str, str] = {}
        self._catalog_incremental_bases: dict[str, CatalogGeneration] = {}
        self._catalog_last_validated_at: dict[str, float] = {}
        self._catalog_warnings: dict[str, str] = {}
        self._typescript_runtime: TypeScriptNodeRuntime | None = None
        self._typescript_enriched_projects: dict[str, set[str]] = {}
        self._typescript_warning: str | None = None
        self._structural_catalog_budget = StructuralCatalogBudget()
        self._structural_extracted: dict[str, dict[str, str]] = {}
        self._postgresql_extractions: dict[
            str,
            dict[str, _CachedPostgreSqlExtraction],
        ] = {}
        self._structural_enriched_fingerprints: dict[str, str] = {}

    @property
    def structural_supervisor(self) -> StructuralWorkerSupervisor:
        return self._supervisor

    @property
    def storage_namespace(self) -> str | None:
        """Return the catalog storage namespace owned by this builder."""
        return self._storage_namespace

    def ranked_search(
        self,
        workspace_id: str,
        generation: CatalogGeneration,
        query: str,
        *,
        kinds: tuple[str, ...] = (),
        path_prefixes: tuple[str, ...] = (),
        limit: int,
        offset: int = 0,
        match_mode: SearchMatchMode = SearchMatchMode.ALL,
    ) -> tuple[tuple[RankedHit, ...], bool, str]:
        """Rank facts with FTS5 when possible; `(hits, has_more, engine)`.

        The linear engine serves punctuation-only queries and FTS-less builds
        with the same deterministic ordering, so coverage can always name the
        engine that produced (or could not produce) each row.
        """
        store = self._catalog_stores.get(workspace_id)
        match_expression = fts_match_expression(query, match_mode=match_mode)
        if store is not None and store.fts_available and match_expression:
            hits, has_more = store.search_ranked(
                match_expression,
                kinds=kinds,
                path_prefixes=path_prefixes,
                limit=limit,
                offset=offset,
            )
            return hits, has_more, "fts5"
        hits, has_more = linear_search(
            generation,
            query,
            kinds=kinds,
            path_prefixes=path_prefixes,
            limit=limit,
            offset=offset,
            match_mode=match_mode,
        )
        return hits, has_more, "linear"

    @property
    def structural_worker_started(self) -> bool:
        return self._supervisor.started

    @property
    def structural_worker_pid(self) -> int | None:
        return self._supervisor.pid

    @property
    def structural_completed_jobs(self) -> int:
        return self._supervisor.completed_jobs

    @property
    def active_language_server_count(self) -> int:
        return sum(resolver.active_session_count for resolver in self._semantic.values())

    def _config_for(self, workspace_id: str) -> ResolvedConfig:
        """One workspace's own config, falling back to the launch config."""
        return self._configs.get(workspace_id, self._config)

    async def capture(
        self,
        workspace: WorkspaceRoot,
        *,
        scope: tuple[str, ...] | None = None,
        path_prefixes: tuple[str, ...] | None = None,
        validate: bool = False,
    ) -> SnapshotBundle:
        lock = self._capture_locks.setdefault(workspace.workspace_id, asyncio.Lock())
        async with lock:
            bundle = self._captured_bundles.get(workspace.workspace_id)
            if bundle is not None:
                bundle = await self._reconcile_capture(
                    workspace,
                    bundle,
                    force=validate,
                )
            if bundle is None:
                snapshotter = RepositorySnapshotter(workspace)
                bundle = await snapshotter.capture()
                self._captured_bundles[workspace.workspace_id] = bundle
                self._capture_inventory[workspace.workspace_id] = snapshotter.last_inventory
                self._capture_signatures[workspace.workspace_id] = snapshotter.inventory_signatures
                self._last_reconciled_at[workspace.workspace_id] = time.monotonic()
        if scope is None and path_prefixes is None:
            return bundle
        selected = (
            frozenset(scope)
            if scope is not None
            else frozenset(
                path
                for path in bundle.contents
                if any(
                    path == prefix or path.startswith(f"{prefix.rstrip('/')}/")
                    for prefix in path_prefixes or ()
                )
            )
        )
        files = tuple(item for item in bundle.snapshot.files if item.path in selected)
        return SnapshotBundle(
            snapshot=bundle.snapshot.model_copy(update={"files": files}),
            contents={
                path: content for path, content in bundle.contents.items() if path in selected
            },
            notes=tuple(
                note
                for note in bundle.notes
                if not note.startswith("skipped ") or any(path in note for path in selected)
            ),
        )

    def mark_dirty(self, workspace_id: str, paths: Sequence[str]) -> None:
        """Accept watcher/editor hints; reconciliation remains authoritative."""
        self._capture_dirty_hints.setdefault(workspace_id, set()).update(paths)

    def invalidate_capture(self, workspace_id: str) -> None:
        """Discard captured bytes after a committed in-process mutation."""
        self._captured_bundles.pop(workspace_id, None)
        self._capture_inventory.pop(workspace_id, None)
        self._capture_signatures.pop(workspace_id, None)
        self._capture_dirty_hints.pop(workspace_id, None)
        self._last_reconciled_at.pop(workspace_id, None)
        self._catalog_generations.pop(workspace_id, None)
        self._catalog_base_fingerprints.pop(workspace_id, None)
        self._catalog_incremental_bases.pop(workspace_id, None)
        self._structural_extracted.pop(workspace_id, None)
        self._postgresql_extractions.pop(workspace_id, None)
        self._structural_enriched_fingerprints.pop(workspace_id, None)

    def catalog_for_bundle(self, bundle: SnapshotBundle) -> CatalogGeneration:
        """Return or publish the immutable generation for exact captured bytes."""
        workspace_id = bundle.snapshot.workspace_id
        current = self._catalog_generations.get(workspace_id)
        if current is not None and current.source_fingerprint == bundle.snapshot.source_fingerprint:
            return current
        generation_number = 1 if current is None else current.number + 1
        changed_paths = (
            frozenset(bundle.contents)
            if current is None
            else changed_snapshot_paths(current.snapshot, bundle.snapshot)
        )
        builder = CatalogGenerationBuilder()
        if current is None:
            generation = builder.build(
                bundle,
                generation=generation_number,
                inventory=self._capture_inventory.get(workspace_id, ()),
                inventory_signatures=self._capture_signatures.get(workspace_id, {}),
            )
        else:
            generation = builder.update(
                current,
                bundle,
                generation=generation_number,
                changed_paths=changed_paths,
                inventory=self._capture_inventory.get(workspace_id, ()),
                inventory_signatures=self._capture_signatures.get(workspace_id, {}),
            )
        self._catalog_generations[workspace_id] = generation
        self._catalog_loaded_from_store.discard(workspace_id)
        self._typescript_enriched_projects.pop(workspace_id, None)
        store = self._catalog_store(
            bundle.snapshot.workspace_id,
            Path(bundle.snapshot.root),
        )
        self._publish_persistent_generation(
            store,
            generation,
            previous_fingerprint=current.source_fingerprint if current is not None else None,
            changed_paths=changed_paths,
        )
        return generation

    async def catalog_bundle(
        self,
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Return a fresh hot generation, hydrating a valid restart projection first."""
        workspace_id = workspace.workspace_id
        lock = self._catalog_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            generation, bundle = await self._resolve_catalog_bundle(workspace, validate=validate)
            generation = self._expand_base_catalog(workspace, generation, bundle)
            return await self.enrich_structural_catalog(workspace, generation, bundle)

    async def bootstrap_catalog_bundle(
        self,
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Restore exact SQLite state or build only bounded retrieval facts."""
        workspace_id = workspace.workspace_id
        lock = self._catalog_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            store = self._catalog_store(workspace_id, workspace.root)
            current = self._catalog_generations.get(workspace_id)
            bundle = await self.capture(workspace, validate=validate)
            loaded: CatalogGeneration | None = None
            if current is None and store.mode is CatalogMode.DISK:
                try:
                    loaded = store.load(
                        source_fingerprint=bundle.snapshot.source_fingerprint,
                    )
                except CatalogStoreError as exc:
                    self._handle_catalog_store_error(workspace_id, store, exc)
            candidate = current or loaded
            if (
                candidate is not None
                and candidate.snapshot_id == bundle.snapshot.snapshot_id
                and candidate.source_fingerprint == bundle.snapshot.source_fingerprint
            ):
                self._catalog_generations[workspace_id] = candidate
                if loaded is not None:
                    self._catalog_loaded_from_store.add(workspace_id)
                    self._catalog_last_validated_at[workspace_id] = time.monotonic()
                    persisted = store.materialized_publication(workspace_id)
                    if persisted is not None and not persisted.enrichment_settled:
                        self._catalog_base_fingerprints[workspace_id] = candidate.source_fingerprint
                return candidate, bundle

            generation_number = 1 if candidate is None else candidate.number + 1
            generation = CatalogGenerationBuilder().build_base(
                bundle,
                generation=generation_number,
                inventory=self._capture_inventory.get(workspace_id, ()),
                inventory_signatures=self._capture_signatures.get(workspace_id, {}),
            )
            self._catalog_generations[workspace_id] = generation
            self._catalog_loaded_from_store.discard(workspace_id)
            self._catalog_base_fingerprints[workspace_id] = generation.source_fingerprint
            self._publish_persistent_generation(
                store,
                generation,
                previous_fingerprint=(
                    candidate.source_fingerprint if candidate is not None else None
                ),
                changed_paths=frozenset(bundle.contents),
            )
            return generation, bundle

    def _expand_base_catalog(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
        bundle: SnapshotBundle,
    ) -> CatalogGeneration:
        workspace_id = workspace.workspace_id
        if self._catalog_base_fingerprints.get(workspace_id) != generation.source_fingerprint:
            return generation
        previous = self._catalog_incremental_bases.pop(workspace_id, None)
        builder = CatalogGenerationBuilder()
        if previous is None:
            expanded = builder.build(
                bundle,
                generation=generation.number + 1,
                inventory=self._capture_inventory.get(workspace_id, ()),
                inventory_signatures=self._capture_signatures.get(workspace_id, {}),
            )
        else:
            expanded = builder.update(
                previous,
                bundle,
                generation=generation.number + 1,
                changed_paths=changed_snapshot_paths(previous.snapshot, bundle.snapshot),
                inventory=self._capture_inventory.get(workspace_id, ()),
                inventory_signatures=self._capture_signatures.get(workspace_id, {}),
            )
        self._catalog_generations[workspace_id] = expanded
        self._catalog_base_fingerprints.pop(workspace_id, None)
        store = self._catalog_store(workspace_id, workspace.root)
        self._publish_persistent_generation(store, expanded)
        return expanded

    async def base_catalog_bundle(
        self,
        workspace: WorkspaceRoot,
        *,
        validate: bool = False,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Return fresh source facts without starting optional catalog enrichers."""
        workspace_id = workspace.workspace_id
        lock = self._catalog_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            return await self._resolve_catalog_bundle(workspace, validate=validate)

    async def _resolve_catalog_bundle(
        self,
        workspace: WorkspaceRoot,
        *,
        validate: bool,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        workspace_id = workspace.workspace_id
        captured = self._captured_bundles.get(workspace_id)
        if captured is not None:
            captured = await self.capture(workspace, validate=validate)
            return self.catalog_for_bundle(captured), captured

        current = self._catalog_generations.get(workspace_id)
        if current is not None and workspace_id in self._catalog_loaded_from_store:
            now = time.monotonic()
            last = self._catalog_last_validated_at.get(workspace_id, 0.0)
            should_validate = validate or now - last >= _RECONCILE_INTERVAL_SECONDS
            if not should_validate or await self._catalog_generation_is_fresh(
                workspace,
                current,
            ):
                self._catalog_last_validated_at[workspace_id] = now
                return current, self._metadata_bundle(current)
            self._catalog_loaded_from_store.discard(workspace_id)

        store = self._catalog_store(workspace_id, workspace.root)
        content: SnapshotBundle | None = None
        if store.mode is CatalogMode.DISK:
            content = await self.capture(workspace, validate=validate)
        try:
            loaded = store.load(
                source_fingerprint=(
                    content.snapshot.source_fingerprint if content is not None else None
                )
            )
        except CatalogStoreError as exc:
            self._handle_catalog_store_error(workspace_id, store, exc)
            loaded = None
        loaded_matches_content = (
            loaded is not None
            and content is not None
            and loaded.snapshot_id == content.snapshot.snapshot_id
            and loaded.source_fingerprint == content.snapshot.source_fingerprint
            and loaded.inventory == self._capture_inventory.get(workspace_id, ())
        )
        loaded_is_fresh = loaded_matches_content or (
            loaded is not None
            and content is None
            and await self._catalog_generation_is_fresh(workspace, loaded)
        )
        if loaded is not None and loaded_is_fresh:
            self._catalog_generations[workspace_id] = loaded
            self._catalog_loaded_from_store.add(workspace_id)
            self._catalog_last_validated_at[workspace_id] = time.monotonic()
            return loaded, content if content is not None else self._metadata_bundle(loaded)

        bundle = content or await self.capture(workspace, validate=validate)
        return self.catalog_for_bundle(bundle), bundle

    def catalog_status(self, workspace_id: str) -> dict[str, object]:
        """Expose non-secret lifecycle state without starting catalog work."""
        generation = self._catalog_generations.get(workspace_id)
        store = self._catalog_stores.get(workspace_id)
        return {
            "generation": generation.number if generation is not None else None,
            "snapshot_id": generation.snapshot_id if generation is not None else None,
            "source_fingerprint": (
                generation.source_fingerprint if generation is not None else None
            ),
            "loaded_from_sqlite": workspace_id in self._catalog_loaded_from_store,
            "mode": (
                store.mode.value
                if store is not None
                else self._config_for(workspace_id).catalog.mode.value
            ),
            "requested_mode": (
                store.requested_mode.value
                if store is not None
                else self._config_for(workspace_id).catalog.mode.value
            ),
            "storage_namespace": (
                store.storage_namespace if store is not None else self._storage_namespace
            ),
            "path": str(store.path) if store is not None and store.path is not None else None,
            "fts_available": store.fts_available if store is not None else None,
            "warning": self._catalog_warnings.get(workspace_id),
            "fallback_reason": store.fallback_reason if store is not None else None,
            "typescript_runtime_started": (
                self._typescript_runtime.started if self._typescript_runtime is not None else False
            ),
            "typescript_enriched_projects": sorted(
                self._typescript_enriched_projects.get(workspace_id, set())
            ),
            "typescript_warning": self._typescript_warning,
        }

    def mark_materialized_generation_restored(self, generation: CatalogGeneration) -> None:
        """Hydrate enrichment state after an exact validated materialized restore."""
        self._structural_enriched_fingerprints[generation.workspace_id] = (
            generation.source_fingerprint
        )
        self._typescript_enriched_projects[generation.workspace_id] = {
            engine.project_id for engine in generation.facts.engines if engine.coverage == "loaded"
        }

    def structural_catalog_enrichment_settled(
        self,
        generation: CatalogGeneration,
    ) -> bool:
        """Whether no structural file remains retryable for this source generation."""
        return (
            self._structural_enriched_fingerprints.get(generation.workspace_id)
            == generation.source_fingerprint
        )

    def _hydrate_incremental_structural_state(
        self,
        store: CatalogStore,
        generation: CatalogGeneration,
    ) -> None:
        """Reuse settled per-file facts only as the base for a changed generation."""
        persisted = store.materialized_publication(generation.workspace_id)
        if (
            persisted is None
            or not persisted.enrichment_settled
            or persisted.generation != generation.number
            or persisted.snapshot_id != generation.snapshot_id
            or persisted.source_fingerprint != generation.source_fingerprint
        ):
            return
        self._structural_extracted[generation.workspace_id] = {
            item.path: item.content_hash
            for item in generation.snapshot.files
            if item.language is not None and item.language.lower() in SUPPORTED_CATALOG_LANGUAGES
        }

    def existing_catalog_store(self, workspace_id: str) -> CatalogStore | None:
        """Return only a lifespan-created store; never create one for a read."""
        return self._catalog_stores.get(workspace_id)

    def ensure_catalog_store(self, workspace: WorkspaceRoot) -> CatalogStore:
        """Create the workspace store for lifecycle-owned indexing."""
        return self._catalog_store(workspace.workspace_id, workspace.root)

    async def enrich_typescript_catalog(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
        bundle: SnapshotBundle,
        *,
        project_ids: frozenset[str],
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Merge dual-engine facts for selected or all TypeScript projects."""
        enriched = self._typescript_enriched_projects.setdefault(
            workspace.workspace_id,
            {
                engine.project_id
                for engine in generation.facts.engines
                if engine.coverage == "loaded"
            },
        )
        requested: frozenset[str] = project_ids.difference(enriched) if project_ids else frozenset()
        if project_ids and not requested:
            return generation, bundle

        if not bundle.contents:
            request_paths = typescript_request_paths(
                generation.facts,
                tuple(item.path for item in generation.snapshot.files),
                project_ids=requested,
            )
            if not request_paths:
                return generation, bundle
            captured = await RepositorySnapshotter(workspace).capture(scope=request_paths)
            expected_hashes = {item.path: item.content_hash for item in generation.snapshot.files}
            captured_hashes = {item.path: item.content_hash for item in captured.snapshot.files}
            drifted = tuple(
                path
                for path in request_paths
                if captured_hashes.get(path) != expected_hashes.get(path)
            )
            if drifted:
                self.mark_dirty(workspace.workspace_id, drifted)
                self._typescript_warning = (
                    "selected TypeScript project changed after the catalog generation"
                )
                return generation, bundle
            bundle = SnapshotBundle(
                snapshot=generation.snapshot,
                contents=captured.contents,
                notes=captured.notes,
            )
        requests = build_typescript_requests(
            generation.facts,
            bundle,
            project_ids=requested,
        )
        if not requests:
            return generation, bundle
        runtime = self._typescript_runtime
        if runtime is None:
            installation = resolve_typescript_installation()
            if installation is None:
                self._typescript_warning = (
                    "exact managed ts-morph/native TypeScript runtime is not installed"
                )
                return generation, bundle
            runtime = TypeScriptNodeRuntime(installation)
            self._typescript_runtime = runtime

        facts = generation.facts
        completed: set[str] = set()
        warnings = list(facts.warnings)
        for request in requests:
            if request.project_id in enriched:
                continue
            try:
                analysis = await runtime.analyze_async(request)
            except TypeScriptRuntimeError as exc:
                warnings.append(f"{request.project_id}: {exc}")
                continue
            if (
                analysis.workspace_id != workspace.workspace_id
                or analysis.project_id != request.project_id
            ):
                warnings.append(f"{request.project_id}: TypeScript worker identity mismatch")
                continue
            facts = merge_typescript_analysis(facts, request, analysis)
            completed.add(request.project_id)
        deduplicated_warnings = tuple(dict.fromkeys(warnings))
        if deduplicated_warnings != facts.warnings:
            facts = facts.model_copy(update={"warnings": deduplicated_warnings})
        if facts == generation.facts:
            return generation, bundle
        updated = catalog_generation_from_facts(
            generation=generation.number + 1,
            snapshot=bundle.snapshot,
            facts=facts,
            inventory=generation.inventory,
            inventory_signatures=generation.inventory_signatures,
        )
        self._catalog_generations[workspace.workspace_id] = updated
        enriched.update(completed)
        store = self._catalog_store(workspace.workspace_id, workspace.root)
        self._publish_persistent_generation(
            store,
            updated,
            previous_fingerprint=generation.source_fingerprint,
            changed_paths=frozenset(),
        )
        return updated, bundle

    async def enrich_structural_catalog(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
        bundle: SnapshotBundle,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Promote per-file declarations, imports, and exports into catalog facts.

        Digest-keyed and self-healing: exactly the captured files whose content
        hash has not been extracted yet are parsed, so incremental generations
        re-extract only changed files and a hydrated restart re-extracts once.
        """
        workspace_id = workspace.workspace_id
        fingerprint = generation.source_fingerprint
        if self._structural_enriched_fingerprints.get(workspace_id) == fingerprint:
            return generation, bundle
        extracted_digests = self._structural_extracted.setdefault(workspace_id, {})
        postgresql_extractions = self._postgresql_extractions.setdefault(workspace_id, {})
        captured_files = {item.path: item for item in generation.snapshot.files}
        postgresql_files = {
            item.path: item for item in generation.snapshot.files if item.language == "PostgreSQL"
        }
        postgresql_projection_changed = False
        for path, cached in tuple(postgresql_extractions.items()):
            captured = postgresql_files.get(path)
            if captured is None or cached.extraction.context.source_digest != captured.content_hash:
                postgresql_extractions.pop(path)
                postgresql_projection_changed = True
        for path in tuple(extracted_digests):
            captured = captured_files.get(path)
            if captured is None or extracted_digests[path] != captured.content_hash:
                extracted_digests.pop(path)
        needed = [
            item
            for item in generation.snapshot.files
            if item.language is not None
            and (
                item.language.lower() in SUPPORTED_CATALOG_LANGUAGES
                or item.language == "PostgreSQL"
            )
            and extracted_digests.get(item.path) != item.content_hash
        ]
        if not needed and not postgresql_projection_changed:
            self._structural_enriched_fingerprints[workspace_id] = fingerprint
            return await self._publish_policy_facts(workspace, generation, bundle, workspace_id)

        warnings: list[str] = []
        truncated = len(needed) > self._structural_catalog_budget.max_files_per_pass
        needed = needed[: self._structural_catalog_budget.max_files_per_pass]

        contents = bundle.contents
        if not contents and needed:
            captured = await RepositorySnapshotter(workspace).capture(
                scope=tuple(item.path for item in needed),
            )
            captured_hashes = {item.path: item.content_hash for item in captured.snapshot.files}
            drifted = frozenset(
                item.path for item in needed if captured_hashes.get(item.path) != item.content_hash
            )
            if drifted:
                self.mark_dirty(workspace_id, sorted(drifted))
                needed = [item for item in needed if item.path not in drifted]
            contents = captured.contents
            if not needed:
                return generation, bundle

        deadline = time.monotonic() + self._structural_catalog_budget.wall_time_seconds
        extracted: dict[str, ExtractedFile] = {}
        postgresql_parsed: dict[str, _CachedPostgreSqlExtraction] = {}
        for item in needed:
            if time.monotonic() >= deadline:
                truncated = True
                break
            content = contents.get(item.path)
            language = item.language
            if content is None or language is None:
                continue
            if len(content) > MAX_CONTENT_BYTES:
                extracted_digests[item.path] = item.content_hash
                if language == "PostgreSQL":
                    warnings.append(
                        postgresql_catalog_failure_warning(
                            item.path,
                            error_type="content_too_large",
                            message=f"content exceeds the {MAX_CONTENT_BYTES} byte cap",
                        )
                    )
                else:
                    warnings.append(
                        f"{item.path}: skipped structural promotion over the content cap"
                    )
                continue
            try:
                if language == "PostgreSQL":
                    result = await self._supervisor.extract(
                        language=language,
                        path=item.path,
                        content=content,
                        projections=(),
                        postgresql_catalog=PostgreSqlCatalogContext(
                            snapshot_id=generation.snapshot_id,
                            path=item.path,
                            source_digest=item.content_hash,
                            source_lane=source_lane_for_path(
                                item.path,
                                lane_roots=self._config_for(workspace_id).postgresql.lane_roots,
                            ),
                        ),
                        workspace_id=workspace_id,
                    )
                else:
                    result = await self._supervisor.extract(
                        language=language,
                        path=item.path,
                        content=content,
                        projections=STRUCTURAL_CATALOG_PROJECTIONS,
                        workspace_id=workspace_id,
                    )
            except (WorkerJobError, WorkerUnavailableError) as exc:
                if language == "PostgreSQL":
                    warnings.append(
                        postgresql_catalog_failure_warning(
                            item.path,
                            error_type=(
                                exc.error_type
                                if isinstance(exc, WorkerJobError)
                                else "worker_unavailable"
                            ),
                            message=str(exc),
                        )
                    )
                else:
                    warnings.append(f"{item.path}: {exc}")
                continue
            if language == "PostgreSQL":
                if result.postgresql_catalog is None:
                    warnings.append(
                        postgresql_catalog_failure_warning(
                            item.path,
                            error_type="missing_result",
                            message="worker returned no PostgreSQL catalog payload",
                        )
                    )
                    continue
                cached = _CachedPostgreSqlExtraction(
                    source=content,
                    extraction=result.postgresql_catalog,
                )
                postgresql_extractions[item.path] = cached
                postgresql_parsed[item.path] = cached
                postgresql_projection_changed = True
                extracted_digests[item.path] = item.content_hash
                continue
            extracted[item.path] = ExtractedFile(
                digest=item.content_hash,
                language=language,
                fragments=result.fragments,
            )
            extracted_digests[item.path] = item.content_hash

        policies = await asyncio.to_thread(
            collect_policy_facts,
            bundle,
            self._config_for(workspace_id).governance,
            workspace_id=workspace_id,
        )
        if (
            not extracted
            and not postgresql_parsed
            and not postgresql_projection_changed
            and not warnings
            and not truncated
            and policies == generation.facts.policies
        ):
            return generation, bundle

        facts = await asyncio.to_thread(
            merge_structural_facts,
            generation.facts,
            workspace_id=workspace_id,
            extracted=extracted,
        )
        if postgresql_projection_changed:
            ordered_postgresql_paths = tuple(sorted(postgresql_extractions))
            postgresql_sources = {
                path: postgresql_extractions[path].source for path in ordered_postgresql_paths
            }
            postgresql_bindings = tuple(
                (
                    postgresql_extractions[path].extraction,
                    PostgreSqlCatalogContext(
                        snapshot_id=generation.snapshot_id,
                        path=path,
                        source_digest=postgresql_files[path].content_hash,
                        source_lane=source_lane_for_path(
                            path,
                            lane_roots=self._config_for(workspace_id).postgresql.lane_roots,
                        ),
                    ),
                )
                for path in ordered_postgresql_paths
            )
            facts = await asyncio.to_thread(
                _merge_postgresql_projection,
                facts,
                workspace_id=workspace_id,
                sources=postgresql_sources,
                bindings=postgresql_bindings,
            )
        facts = facts.model_copy(update={"policies": policies})
        combined = list(facts.warnings)
        combined.extend(warnings)
        if truncated:
            combined.append(
                "structural enrichment truncated after "
                f"{len(extracted) + len(postgresql_parsed)} files"
            )
        facts = facts.model_copy(update={"warnings": tuple(dict.fromkeys(combined))})
        updated = await self._commit_generation(workspace, generation, facts, workspace_id)
        retryable_supported_files_remain = any(
            item.language is not None
            and (
                item.language.lower() in SUPPORTED_CATALOG_LANGUAGES
                or item.language == "PostgreSQL"
            )
            and extracted_digests.get(item.path) != item.content_hash
            for item in generation.snapshot.files
        )
        if not truncated and not retryable_supported_files_remain:
            self._structural_enriched_fingerprints[workspace_id] = fingerprint
        return updated, bundle

    async def _commit_generation(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
        facts: CatalogFacts,
        workspace_id: str,
    ) -> CatalogGeneration:
        """Persist a new generation derived from ``facts`` and publish it."""
        if facts == generation.facts:
            return generation
        updated = await asyncio.to_thread(
            catalog_generation_from_facts,
            generation=generation.number + 1,
            snapshot=generation.snapshot,
            facts=facts,
            inventory=generation.inventory,
            inventory_signatures=generation.inventory_signatures,
        )
        self._catalog_generations[workspace_id] = updated
        store = self._catalog_store(workspace_id, workspace.root)
        self._publish_persistent_generation(
            store,
            updated,
            previous_fingerprint=generation.source_fingerprint,
            changed_paths=frozenset(),
        )
        return updated

    async def _publish_policy_facts(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
        bundle: SnapshotBundle,
        workspace_id: str,
    ) -> tuple[CatalogGeneration, SnapshotBundle]:
        """Publish governance policy facts without structural extraction.

        Policy facts are independent of structural promotion, so a workspace
        with no supported source languages still indexes its configured
        governance sources and the ``search`` tool reaches policy rows.
        """
        policies = await asyncio.to_thread(
            collect_policy_facts,
            bundle,
            self._config_for(workspace_id).governance,
            workspace_id=workspace_id,
        )
        if policies == generation.facts.policies:
            return generation, bundle
        facts = generation.facts.model_copy(update={"policies": policies})
        return (
            await self._commit_generation(workspace, generation, facts, workspace_id),
            bundle,
        )

    def _catalog_store(self, workspace_id: str, root: Path) -> CatalogStore:
        store = self._catalog_stores.get(workspace_id)
        if store is None:
            store = CatalogStore(
                root,
                mode=self._config_for(workspace_id).catalog.mode,
                storage_namespace=self._storage_namespace,
                config_digest=self._config_digest,
                retained_generations=self._config_for(workspace_id).catalog.retained_generations,
                max_disk_size_mb=self._config_for(workspace_id).catalog.max_disk_size_mb,
            )
            self._catalog_stores[workspace_id] = store
        return store

    def _publish_persistent_generation(
        self,
        store: CatalogStore,
        generation: CatalogGeneration,
        *,
        previous_fingerprint: str | None = None,
        changed_paths: frozenset[str] | None = None,
    ) -> None:
        """Persist the restart projection only when persistence is configured."""
        workspace_id = generation.workspace_id
        if store.mode is not CatalogMode.DISK:
            self._catalog_warnings.pop(workspace_id, None)
            return
        try:
            store.publish(
                generation,
                previous_fingerprint=previous_fingerprint,
                changed_paths=changed_paths,
            )
            self._catalog_warnings.pop(workspace_id, None)
        except CatalogStoreError as exc:
            self._handle_catalog_store_error(workspace_id, store, exc)

    def _handle_catalog_store_error(
        self,
        workspace_id: str,
        store: CatalogStore,
        error: CatalogStoreError,
    ) -> None:
        if store.requested_mode is CatalogMode.DISK:
            raise error
        self._catalog_warnings[workspace_id] = str(error)

    async def _catalog_generation_is_fresh(
        self,
        workspace: WorkspaceRoot,
        generation: CatalogGeneration,
    ) -> bool:
        snapshotter = RepositorySnapshotter(workspace)
        inventory = await snapshotter.inventory()
        if inventory != generation.inventory:
            return False
        captured = await snapshotter.capture(
            scope=tuple(item.path for item in generation.snapshot.files)
        )
        expected_hashes = {item.path: item.content_hash for item in generation.snapshot.files}
        captured_hashes = {item.path: item.content_hash for item in captured.snapshot.files}
        return captured_hashes == expected_hashes

    @staticmethod
    def _metadata_bundle(generation: CatalogGeneration) -> SnapshotBundle:
        return SnapshotBundle(
            snapshot=generation.snapshot,
            contents={},
            notes=("catalog generation restored from private SQLite",),
        )

    async def _reconcile_capture(
        self,
        workspace: WorkspaceRoot,
        bundle: SnapshotBundle,
        *,
        force: bool,
    ) -> SnapshotBundle:
        workspace_id = workspace.workspace_id
        now = time.monotonic()
        hinted = self._capture_dirty_hints.get(workspace_id, set())
        last = self._last_reconciled_at.get(workspace_id, 0.0)
        if not force and not hinted and now - last < _RECONCILE_INTERVAL_SECONDS:
            return bundle

        previous_inventory = set(self._capture_inventory.get(workspace_id, ()))
        previous_signatures = self._capture_signatures.get(workspace_id, {})
        if hinted and not force and now - last < _RECONCILE_INTERVAL_SECONDS:
            current_inventory = set(previous_inventory)
            current_signatures = dict(previous_signatures)
            for path in hinted:
                signature = self._path_signature(workspace, path)
                if signature is None:
                    current_inventory.discard(path)
                    current_signatures.pop(path, None)
                else:
                    current_inventory.add(path)
                    current_signatures[path] = signature
            inventory = tuple(sorted(current_inventory))
            dirty = set(hinted)
        else:
            snapshotter = RepositorySnapshotter(workspace)
            inventory = await snapshotter.inventory()
            current_signatures = self._inventory_signatures(workspace, inventory)
            current_inventory = set(inventory)
            dirty = set(hinted)
            dirty.update(previous_inventory.symmetric_difference(current_inventory))
            dirty.update(
                path
                for path in previous_inventory.intersection(current_inventory)
                if previous_signatures.get(path) != current_signatures.get(path)
            )

        self._capture_dirty_hints.pop(workspace_id, None)
        self._capture_inventory[workspace_id] = inventory
        self._capture_signatures[workspace_id] = current_signatures
        self._last_reconciled_at[workspace_id] = now
        if not dirty:
            return bundle

        recaptured_paths = tuple(sorted(dirty.intersection(current_inventory)))
        if recaptured_paths:
            delta = await RepositorySnapshotter(workspace).capture(scope=recaptured_paths)
        else:
            delta = SnapshotBundle(
                snapshot=bundle.snapshot.model_copy(update={"files": ()}),
                contents={},
                notes=(),
            )
        merged = self._merge_capture(bundle, delta, dirty)
        self._captured_bundles[workspace_id] = merged
        return merged

    @staticmethod
    def _path_signature(
        workspace: WorkspaceRoot,
        path: str,
    ) -> _FileSignature | None:
        try:
            admitted = RepositoryPath.admit(workspace, path).absolute(workspace)
            if not admitted.is_file():
                return None
            file_stat = admitted.stat()
        except FileNotFoundError, OSError, ValueError:
            return None
        return (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )

    @staticmethod
    def _inventory_signatures(
        workspace: WorkspaceRoot,
        inventory: Sequence[str],
    ) -> dict[str, _FileSignature]:
        signatures: dict[str, _FileSignature] = {}
        for path in inventory:
            signature = AnalysisFrameBuilder._path_signature(workspace, path)
            if signature is not None:
                signatures[path] = signature
        return signatures

    @staticmethod
    def _merge_capture(
        current: SnapshotBundle,
        delta: SnapshotBundle,
        dirty: set[str],
    ) -> SnapshotBundle:
        contents = {
            path: content for path, content in current.contents.items() if path not in dirty
        }
        contents.update(delta.contents)
        rows_by_path: dict[str, CapturedFile] = {
            row.path: row for row in current.snapshot.files if row.path not in dirty
        }
        rows_by_path.update({row.path: row for row in delta.snapshot.files})

        limits = SnapshotLimits()
        selected_contents: dict[str, bytes] = {}
        selected_rows: list[CapturedFile] = []
        total_bytes = 0
        notes = list(current.notes)
        notes.extend(delta.notes)
        for path in sorted(rows_by_path):
            content = contents[path]
            if len(selected_rows) >= limits.max_files:
                notes.append(f"file count limit {limits.max_files} reached")
                break
            if total_bytes + len(content) > limits.max_bytes:
                notes.append(f"byte limit {limits.max_bytes} reached")
                break
            selected_contents[path] = content
            selected_rows.append(rows_by_path[path])
            total_bytes += len(content)

        fingerprint = snapshot_fingerprint(selected_rows)
        snapshot = current.snapshot.model_copy(
            update={
                "snapshot_id": f"{current.snapshot.workspace_id}:{fingerprint[:16]}",
                "created_at": datetime.now(UTC),
                "files": tuple(selected_rows),
                "source_fingerprint": fingerprint,
                "changed_during_analysis": delta.snapshot.changed_during_analysis,
            }
        )
        return SnapshotBundle(
            snapshot=snapshot,
            contents=selected_contents,
            notes=tuple(dict.fromkeys(notes)),
        )

    async def build(
        self,
        workspace: WorkspaceRoot,
        *,
        include_tables: Sequence[str],
        exclude_tables: Sequence[str],
        semantic_mode: SemanticMode,
        row_limit: int = 1000,
        validate: bool = False,
        seed_keys: Sequence[str] = (),
        producer_scope: frozenset[Producer] | None = None,
        enrich_catalog: bool = True,
        bootstrap_catalog: bool = False,
    ) -> FrameBuild:
        planner = TablePlanner()
        plan = planner.plan(
            include_tables=include_tables,
            exclude_tables=exclude_tables,
        )
        planned_producers = frozenset(plan.producer_tables)
        active_producers = (
            planned_producers
            if producer_scope is None
            else planned_producers.intersection(producer_scope)
        )
        metadata_only = active_producers.issubset({Producer.CATALOG, Producer.SNAPSHOT})
        catalog: CatalogGeneration | None = None
        if metadata_only and Producer.CATALOG in active_producers:
            if bootstrap_catalog:
                catalog, bundle = await self.bootstrap_catalog_bundle(
                    workspace,
                    validate=validate,
                )
            else:
                catalog, bundle = await self.base_catalog_bundle(
                    workspace,
                    validate=validate,
                )
        else:
            bundle = await self.capture(workspace, validate=validate)
        prime_tables: list[str] = []
        if "semantic.imports" in plan.execution_order:
            prime_tables.append("syntax.imports")
        if "semantic.calls" in plan.execution_order:
            prime_tables.append("syntax.call_sites")
        structural: StructuralTableProducer | None = None
        if Producer.STRUCTURAL in active_producers or Producer.SEMANTIC in active_producers:
            structural = StructuralTableProducer(
                self._supervisor,
                prime_tables=prime_tables,
                policy=WorkspaceStandardsAnalyzer(
                    root=workspace.root,
                    config=self._config_for(workspace.workspace_id).structural,
                    engines=self.structural_engines(workspace),
                ),
            )
        if catalog is None and Producer.CATALOG in active_producers:
            catalog = self.catalog_for_bundle(bundle)
        if catalog is not None and not bootstrap_catalog:
            catalog = self._expand_base_catalog(workspace, catalog, bundle)
        if (
            enrich_catalog
            and catalog is not None
            and _TYPESCRIPT_CATALOG_TABLES.intersection(plan.execution_order)
        ):
            project_ids: frozenset[str] = (
                self._project_ids_from_seed_keys(seed_keys) if seed_keys else frozenset()
            )
            catalog, bundle = await self.enrich_typescript_catalog(
                workspace,
                catalog,
                bundle,
                project_ids=project_ids,
            )
        policy_selectors = tuple(
            key.partition(":")[2]
            for key in seed_keys
            if key.startswith("policy:") and key.partition(":")[2]
        )
        producers: dict[Producer, TableProducer] = {}
        if Producer.SNAPSHOT in active_producers:
            producers[Producer.SNAPSHOT] = SnapshotTableProducer()
        if Producer.STRUCTURAL in active_producers and structural is not None:
            producers[Producer.STRUCTURAL] = structural
        if Producer.SEMANTIC in active_producers and structural is not None:
            producers[Producer.SEMANTIC] = SemanticTableProducer(
                structural,
                self.semantic_resolver(workspace),
            )
        if Producer.AUTHORITY in active_producers:
            authority = AuthorityResolver(
                governance=self._config_for(workspace.workspace_id).governance,
                policy_selectors=policy_selectors,
            )
            producers[Producer.AUTHORITY] = (
                _BaseAuthorityTableProducer(
                    authority,
                    governance_paths=frozenset(
                        source.path
                        for source in self._config_for(workspace.workspace_id).governance.sources
                    ),
                )
                if Producer.STRUCTURAL not in active_producers
                else authority
            )
        if Producer.DERIVED in active_producers:
            producers[Producer.DERIVED] = DerivedMaterializer()
        if Producer.IMPORTED in active_producers:
            producers[Producer.IMPORTED] = ImportedTableProducer(
                workspace.root,
                self._config_for(workspace.workspace_id).coverage,
            )
        if Producer.CATALOG in active_producers and catalog is not None:
            producers[Producer.CATALOG] = CatalogTableProducer(catalog)
        frame = await planner.execute(
            plan,
            bundle=bundle,
            semantic_mode=semantic_mode,
            producers=producers,
            row_limit=row_limit,
        )
        return FrameBuild(
            frame=frame,
            bundle=bundle,
            catalog_generation=catalog,
        )

    @staticmethod
    def _project_ids_from_seed_keys(seed_keys: Sequence[str]) -> frozenset[str]:
        project_ids: set[str] = set()
        for key in seed_keys:
            kind, _, value = key.partition(":")
            if kind == "project":
                project_ids.add(value)
            elif kind == "dependency":
                project_id, separator, _package_name = value.rpartition(":")
                if separator:
                    project_ids.add(project_id)
            elif kind in {"symbol", "route", "diagnostic", "import"}:
                project_id, separator, _record_id = value.rpartition(":")
                if separator and project_id != "-":
                    project_ids.add(project_id)
        return frozenset(project_ids)

    def semantic_resolver(self, workspace: WorkspaceRoot) -> SemanticResolver:
        resolver = self._semantic.get(workspace.workspace_id)
        if resolver is None:
            registry = build_provider_registry(
                workspace.root, self._config_for(workspace.workspace_id)
            )
            resolver = SemanticResolver(
                registry,
                diagnostic_timeout_seconds=self._config_for(
                    workspace.workspace_id
                ).lsp.diagnostic_timeout_seconds,
                project_identity_resolver=self._semantic_project_identity,
            )
            self._semantic[workspace.workspace_id] = resolver
        return resolver

    def structural_engines(self, workspace: WorkspaceRoot) -> StructuralEngines:
        engines = self._structural_engines.get(workspace.workspace_id)
        if engines is None:
            engines = StructuralEngines(
                self._supervisor,
                root=workspace.root,
                config=self._config_for(workspace.workspace_id).structural,
            )
            self._structural_engines[workspace.workspace_id] = engines
        return engines

    def _semantic_project_identity(
        self,
        bundle: SnapshotBundle,
        path: str,
        provider: ConfiguredProvider,
        control_paths: tuple[str, ...],
    ) -> SemanticProjectIdentity:
        """Route an LSP request through the same project/config/engine catalog owner."""
        generation = self.catalog_for_bundle(bundle)
        candidates = tuple(
            project
            for project in generation.facts.projects
            if _path_belongs_to_project(path, project.root_path)
        )
        if not candidates:
            return SemanticProjectIdentity.fallback(
                bundle,
                provider_name=provider.provider_name,
                requested_file=path,
                control_paths=control_paths,
            )
        project = max(
            candidates,
            key=lambda item: (item.root_path.count("/"), len(item.root_path)),
        )
        configs = tuple(
            sorted(
                (
                    config
                    for config in generation.facts.configs
                    if config.project_id == project.project_id
                ),
                key=lambda item: item.config_path,
            )
        )
        captured_hashes = {
            captured.path: captured.content_hash for captured in bundle.snapshot.files
        }
        controls = tuple(
            sorted(
                {
                    *control_paths,
                    *(closure_path for config in configs for closure_path in config.closure_paths),
                }
            )
        )
        config_payload = {
            "project_id": project.project_id,
            "project_manifest": (project.manifest_path, project.source_digest),
            "configs": [
                {
                    "path": config.config_path,
                    "source_digest": config.source_digest,
                    "closure": [
                        (closure_path, captured_hashes.get(closure_path))
                        for closure_path in config.closure_paths
                    ],
                }
                for config in configs
            ],
            "controls": [
                (control_path, captured_hashes.get(control_path)) for control_path in controls
            ],
        }
        project_config_digest = content_digest(
            json.dumps(
                config_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        engine_rows = tuple(
            sorted(
                (
                    engine
                    for engine in generation.facts.engines
                    if engine.project_id == project.project_id
                    and engine.role
                    in {
                        EngineRole.LOADED_COMPILER,
                        EngineRole.API,
                        EngineRole.PACKAGE,
                    }
                ),
                key=lambda item: (item.role.value, item.engine_id),
            )
        )
        engine_identities = tuple(_semantic_engine_identity(engine) for engine in engine_rows)
        compiler_identity = "|".join(
            (
                f"provider:{provider.provider_name}@{provider.provider_version}",
                *engine_identities,
            )
        )
        return SemanticProjectIdentity(
            project_id=project.project_id,
            project_root=project.root_path,
            project_config_digest=project_config_digest,
            compiler_identity=compiler_identity,
        )

    def record_semantic_resolution(
        self,
        bundle: SnapshotBundle,
        resolution: CapabilityResolution,
    ) -> CatalogGeneration:
        """Publish verified LSP facts as a metadata-only catalog generation."""
        current = self.catalog_for_bundle(bundle)
        facts = merge_lsp_resolution(current.facts, bundle, resolution)
        if facts == current.facts:
            return current
        published = catalog_generation_from_facts(
            generation=current.number + 1,
            snapshot=current.snapshot,
            facts=facts,
            inventory=current.inventory,
            inventory_signatures=current.inventory_signatures,
        )
        workspace_id = current.workspace_id
        self._catalog_generations[workspace_id] = published
        self._catalog_loaded_from_store.discard(workspace_id)
        store = self._catalog_store(
            workspace_id,
            Path(current.snapshot.root),
        )
        self._publish_persistent_generation(
            store,
            published,
            previous_fingerprint=current.source_fingerprint,
            changed_paths=frozenset(),
        )
        return published

    async def restart_semantic(self, workspace_id: str | None = None) -> int:
        selected = tuple(
            (identifier, resolver)
            for identifier, resolver in self._semantic.items()
            if workspace_id is None or identifier == workspace_id
        )
        for identifier, resolver in selected:
            await resolver.shutdown()
            self._semantic.pop(identifier, None)
        return len(selected)

    async def aclose(self) -> None:
        self._captured_bundles.clear()
        self._capture_inventory.clear()
        self._capture_signatures.clear()
        self._capture_dirty_hints.clear()
        self._last_reconciled_at.clear()
        self._capture_locks.clear()
        self._catalog_generations.clear()
        self._catalog_loaded_from_store.clear()
        self._catalog_base_fingerprints.clear()
        self._catalog_incremental_bases.clear()
        self._catalog_last_validated_at.clear()
        self._catalog_locks.clear()
        stores = tuple(self._catalog_stores.values())
        self._catalog_stores.clear()
        self._catalog_warnings.clear()
        self._structural_extracted.clear()
        self._postgresql_extractions.clear()
        self._structural_enriched_fingerprints.clear()
        for store in stores:
            store.close()
        runtime = self._typescript_runtime
        self._typescript_runtime = None
        self._typescript_enriched_projects.clear()
        if runtime is not None:
            await runtime.aclose()
        resolvers = tuple(self._semantic.values())
        self._semantic.clear()
        for resolver in resolvers:
            await resolver.shutdown()
        structural_engines = tuple(self._structural_engines.values())
        self._structural_engines.clear()
        for engines in structural_engines:
            await engines.aclose()
        await self._supervisor.aclose()


def _path_belongs_to_project(path: str, project_root: str) -> bool:
    return not project_root or path == project_root or path.startswith(f"{project_root}/")


def _semantic_engine_identity(engine: EngineFact) -> str:
    version = engine.runtime_version or engine.package_version or engine.binary_version or "unknown"
    return f"{engine.role.value}:{engine.package_name or engine.engine_id}@{version}"


def frame_for_rows(
    bundle: SnapshotBundle,
    *,
    semantic_mode: SemanticMode,
    table: str,
    rows: Sequence[FactRow],
    status: FrameStatus = FrameStatus.COMPLETE,
    omitted_reasons: tuple[str, ...] = (),
    elapsed_ms: float = 0.0,
) -> AnalysisFrame:
    """Create a canonical frame for non-query service results such as search/context."""
    total_bytes = sum(len(content) for content in bundle.contents.values())
    coverage = Coverage(
        status=status,
        eligible_files=len(bundle.snapshot.files),
        examined_files=len(bundle.snapshot.files),
        parse_failures=0,
        candidate_count=sum(
            row.evidence.resolution_status is ResolutionStatus.CANDIDATE for row in rows
        ),
        resolution_attempts=0,
        resolved_count=sum(
            row.evidence.resolution_status is ResolutionStatus.RESOLVED for row in rows
        ),
        unsupported_count=1 if status is FrameStatus.UNSUPPORTED else 0,
        failed_count=1 if status is FrameStatus.FAILED else 0,
        omitted_reasons=omitted_reasons,
        deadline=datetime.now(UTC) + timedelta(seconds=10),
        row_file_byte_depth_limits=RowFileByteDepthLimits(
            max_rows=max(len(rows), 1),
            max_files=max(len(bundle.snapshot.files), 1),
            max_bytes=max(total_bytes, 1),
            max_depth=1,
        ),
        elapsed_ms=max(elapsed_ms, 0.0),
    )
    return AnalysisFrame(
        snapshot_id=bundle.snapshot.snapshot_id,
        workspace_id=bundle.snapshot.workspace_id,
        semantic_mode=semantic_mode,
        coverage=coverage,
        tables={table: tuple(rows)},
        warnings=(*bundle.notes, *omitted_reasons),
    )


def timed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
