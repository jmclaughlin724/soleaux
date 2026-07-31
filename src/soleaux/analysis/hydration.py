"""Hydration of ranked search hits back to their canonical typed records.

The FTS projection only ranks; every hit is a `fact_key` pointer. This module
resolves those pointers against the hot generation, flattens a bounded summary
per kind, attaches stored relationships for symbols, and slices bounded source
excerpts from the digest-bound chunk text. Facts stay authoritative — nothing
here re-derives or re-parses source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from soleaux.catalog.contracts import ChunkFact, SymbolFact
from soleaux.contracts.evidence import EvidenceKind

if TYPE_CHECKING:
    from soleaux.catalog.generation import CatalogGeneration
    from soleaux.catalog.search import RankedHit
    from soleaux.contracts.frame import FactRow

MAX_EXCERPT_CHARS = 2048
MAX_RELATION_LOCATIONS = 10
MAX_RELATION_DIAGNOSTICS = 5


@dataclass(frozen=True)
class HydratedRecord:
    """One resolved fact flattened for a search row."""

    summary: dict[str, Any]
    relations: dict[str, Any] | None
    evidence_kind: EvidenceKind
    evidence_path: str
    byte_start: int | None
    byte_end: int | None


class SearchHydrator:
    """Resolve fact keys for one generation; lookup tables built once."""

    def __init__(self, generation: CatalogGeneration, *, query: str) -> None:
        self._generation = generation
        self._query = query.casefold()
        facts = generation.facts
        self._files = {item.path for item in generation.snapshot.files}
        self._dependencies = {
            f"{item.project_id}:{item.package_name}": item for item in facts.dependencies
        }
        self._scripts = {f"{item.project_id}:{item.name}": item for item in facts.scripts}
        self._configs = {f"{item.project_id}:{item.config_path}": item for item in facts.configs}
        self._routes = {item.route_id: item for item in facts.routes}
        self._imports = {item.import_id: item for item in facts.imports}
        self._diagnostics = {item.diagnostic_id: item for item in facts.diagnostics}
        self._changes = {item.change_id: item for item in facts.changes}

    def record(self, hit: RankedHit) -> HydratedRecord | None:
        kind, _, remainder = hit.fact_key.partition(":")
        resolver = getattr(self, f"_{kind}_record", None)
        if resolver is None:
            return None
        return resolver(remainder, hit)

    def excerpt(
        self,
        hit: RankedHit,
        record: HydratedRecord,
        *,
        context_lines: int,
    ) -> str | None:
        if hit.kind == "chunk":
            chunk = self._generation.chunks_by_id.get(hit.fact_key.partition(":")[2])
            if chunk is None:
                return None
            return self._sliced(chunk, self._query_line(chunk), context_lines)
        if record.byte_start is None:
            return None
        chunk = self._chunk_at(record.evidence_path, record.byte_start)
        if chunk is None:
            return None
        offset_lines = chunk.text[: record.byte_start - chunk.byte_start].count("\n")
        return self._sliced(chunk, offset_lines, context_lines)

    def _query_line(self, chunk: ChunkFact) -> int:
        for index, line in enumerate(chunk.text.splitlines()):
            if self._query in line.casefold():
                return index
        return 0

    @staticmethod
    def _sliced(chunk: ChunkFact, line_index: int, context_lines: int) -> str:
        lines = chunk.text.splitlines()
        start = max(0, line_index - context_lines)
        end = min(len(lines), line_index + context_lines + 1)
        return "\n".join(lines[start:end])[:MAX_EXCERPT_CHARS]

    def _chunk_at(self, path: str, byte_offset: int) -> ChunkFact | None:
        for chunk in self._generation.chunks_by_path.get(path, ()):
            if chunk.byte_start <= byte_offset < max(chunk.byte_end, chunk.byte_start + 1):
                return chunk
        return None

    def _position(self, path: str, byte_offset: int) -> tuple[int, int] | None:
        chunk = self._chunk_at(path, byte_offset)
        if chunk is None:
            return None
        prefix = chunk.text[: byte_offset - chunk.byte_start]
        line = chunk.start_line + prefix.count("\n")
        last_newline = prefix.rfind("\n")
        return line, len(prefix[last_newline + 1 :]) + 1

    def _chunk_record(self, chunk_id: str, hit: RankedHit) -> HydratedRecord | None:
        chunk = self._generation.chunks_by_id.get(chunk_id)
        if chunk is None:
            return None
        return HydratedRecord(
            summary={
                "chunk_kind": chunk.chunk_kind,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
            },
            relations=None,
            evidence_kind=EvidenceKind.STRUCTURAL,
            evidence_path=chunk.path,
            byte_start=chunk.byte_start,
            byte_end=chunk.byte_end,
        )

    def _path_record(self, path: str, hit: RankedHit) -> HydratedRecord | None:
        if path not in self._files:
            return None
        return HydratedRecord(
            summary={},
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=path,
            byte_start=None,
            byte_end=None,
        )

    def _project_record(self, project_id: str, hit: RankedHit) -> HydratedRecord | None:
        project = self._generation.projects_by_id.get(project_id)
        if project is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": project.project_id,
                "name": project.name,
                "project_kind": project.kind.value,
                "root_path": project.root_path,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=project.manifest_path,
            byte_start=None,
            byte_end=None,
        )

    def _dependency_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        dependency = self._dependencies.get(remainder)
        if dependency is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": dependency.project_id,
                "package_name": dependency.package_name,
                "declared_specifier": dependency.declared_specifier,
                "resolved_specifier": dependency.resolved_specifier,
                "scope": dependency.scope.value,
                "usage": dependency.usage.value,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=dependency.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _script_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        script = self._scripts.get(remainder)
        if script is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": script.project_id,
                "name": script.name,
                "command": script.command,
                "task_ids": list(script.task_ids),
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=script.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _task_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        project_id, _, task_id = remainder.rpartition(":")
        task = self._generation.tasks_by_key.get((project_id, "turbo", task_id))
        if task is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": task.project_id,
                "task_id": task.task_id,
                "runner": task.runner,
                "depends_on": list(task.depends_on),
                "outputs": list(task.outputs),
                "cache": task.cache,
                "persistent": task.persistent,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=task.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _config_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        config = self._configs.get(remainder)
        if config is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": config.project_id,
                "config_kind": config.config_kind,
                "parser_id": config.parser_id,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=config.config_path,
            byte_start=None,
            byte_end=None,
        )

    def _route_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        route = self._routes.get(remainder.rpartition(":")[2])
        if route is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": route.project_id,
                "route": route.route,
                "framework": route.framework,
                "registration_kind": route.registration_kind,
                "methods": list(route.methods),
                "runtime": route.runtime,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=route.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _rule_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        if remainder.startswith("packaged:"):
            from soleaux.structural.rules import packaged_rules

            packaged = packaged_rules().get(remainder.partition(":")[2])
            if packaged is None:
                return None
            return HydratedRecord(
                summary={
                    "rule_id": packaged.id,
                    "language": packaged.language,
                    "severity": packaged.severity,
                    "message": packaged.message,
                    "packaged": True,
                },
                relations=None,
                evidence_kind=EvidenceKind.METADATA,
                evidence_path="",
                byte_start=None,
                byte_end=None,
            )
        rule = self._generation.rules_by_id.get(remainder)
        if rule is None:
            return None
        return HydratedRecord(
            summary={
                "rule_id": rule.rule_id,
                "language": rule.language,
                "severity": rule.severity,
                "message": rule.message,
                "packaged": False,
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=rule.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _symbol_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        symbol_id = remainder.rpartition(":")[2]
        candidates = self._generation.symbols_by_id.get(symbol_id, ())
        symbol: SymbolFact | None = candidates[0] if candidates else None
        if symbol is None:
            return None
        position = self._position(symbol.path, symbol.byte_start)
        summary: dict[str, Any] = {
            "project_id": symbol.project_id,
            "name": symbol.name,
            "symbol_kind": symbol.symbol_kind,
            "exported": symbol.exported,
            "coverage": symbol.coverage,
            "engine_id": symbol.engine_id,
        }
        if position is not None:
            summary["line"], summary["column"] = position
        relations: dict[str, Any] = {}
        if symbol.definitions:
            relations["definitions"] = [
                {"path": item.path, "byte_start": item.byte_start, "byte_end": item.byte_end}
                for item in symbol.definitions[:MAX_RELATION_LOCATIONS]
            ]
        if symbol.references:
            relations["references_count"] = len(symbol.references)
        if symbol.calls:
            relations["calls_count"] = len(symbol.calls)
        diagnostics = self._generation.diagnostics_by_path.get(symbol.path, ())
        if diagnostics:
            relations["diagnostics"] = [
                item.message for item in diagnostics[:MAX_RELATION_DIAGNOSTICS]
            ]
        return HydratedRecord(
            summary=summary,
            relations=relations or None,
            evidence_kind=(
                EvidenceKind.SEMANTIC if symbol.coverage == "semantic" else EvidenceKind.STRUCTURAL
            ),
            evidence_path=symbol.path,
            byte_start=symbol.byte_start,
            byte_end=symbol.byte_end,
        )

    def _import_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        imported = self._imports.get(remainder.rpartition(":")[2])
        if imported is None:
            return None
        return HydratedRecord(
            summary={
                "project_id": imported.project_id,
                "specifier": imported.specifier,
                "resolved_path": imported.resolved_path,
                "usage": imported.usage.value,
            },
            relations=None,
            evidence_kind=EvidenceKind.STRUCTURAL,
            evidence_path=imported.path,
            byte_start=None,
            byte_end=None,
        )

    def _diagnostic_record(self, remainder: str, hit: RankedHit) -> HydratedRecord | None:
        diagnostic = self._diagnostics.get(remainder.rpartition(":")[2])
        if diagnostic is None:
            return None
        position = self._position(diagnostic.path, diagnostic.byte_start)
        summary: dict[str, Any] = {
            "project_id": diagnostic.project_id,
            "category": diagnostic.category,
            "code": diagnostic.code,
            "message": diagnostic.message,
            "engine_id": diagnostic.engine_id,
        }
        if position is not None:
            summary["line"], summary["column"] = position
        return HydratedRecord(
            summary=summary,
            relations=None,
            evidence_kind=EvidenceKind.SEMANTIC,
            evidence_path=diagnostic.path,
            byte_start=diagnostic.byte_start,
            byte_end=diagnostic.byte_end,
        )

    def _policy_record(self, policy_id: str, hit: RankedHit) -> HydratedRecord | None:
        policy = self._generation.policies_by_id.get(policy_id)
        if policy is None:
            return None
        return HydratedRecord(
            summary={
                "policy_id": policy.policy_id,
                "title": policy.title,
                "governance_source_id": policy.governance_source_id,
                "source_line": policy.source_line,
                "attributes": dict(policy.attributes),
            },
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=policy.source_path,
            byte_start=None,
            byte_end=None,
        )

    def _change_record(self, change_id: str, hit: RankedHit) -> HydratedRecord | None:
        change = self._changes.get(change_id)
        if change is None:
            return None
        return HydratedRecord(
            summary={"operation": change.operation, "change_generation": change.generation},
            relations=None,
            evidence_kind=EvidenceKind.METADATA,
            evidence_path=change.path,
            byte_start=None,
            byte_end=None,
        )


def materialized_summary(row: FactRow, *, kind: str) -> dict[str, Any]:
    """Preserve hydrated search fields using only a published SQLite row."""
    summary = dict(row.data)
    if kind == "rule":
        summary.setdefault("packaged", False)
    return summary
