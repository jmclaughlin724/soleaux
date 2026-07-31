"""Request-local derived relation materializers; no graph is persisted."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from soleaux.contracts.evidence import Authority, EvidenceKind, ResolutionStatus
from soleaux.contracts.frame import FactRow
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.tables import PRODUCER_SUPPORTED_TABLES, Producer
from soleaux.structural.snapshot import SnapshotBundle
from soleaux.tables.evidence import derived_evidence


class TopologyLimits(BaseModel):
    """Hard bounds for impact traversal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows: int = Field(default=200, ge=1)
    max_depth: int = Field(default=8, ge=1)
    max_bytes: int = Field(default=65536, ge=1)
    timeout_ms: int = Field(default=1000, ge=1)


class MaterializationResult(BaseModel):
    """Bounded derived rows plus explicit truncation reasons."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[FactRow, ...]
    truncated: bool = False
    reasons: tuple[str, ...] = ()


class DerivedMaterializer:
    """Derive response rows from eligible facts without a second graph model."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.DERIVED]

    def __init__(
        self,
        *,
        seeds: Sequence[str] = (),
        limits: TopologyLimits | None = None,
        semantic_complete: bool = True,
        dynamic_inputs: bool = False,
    ) -> None:
        self._seeds = tuple(seeds)
        self._limits = limits
        self._semantic_complete = semantic_complete
        self._dynamic_inputs = dynamic_inputs

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        """Adapt materializers to the shared request-local producer boundary."""
        del bundle
        return self.materialize(
            table_names,
            upstream_tables,
            seeds=self._seeds,
            limits=self._limits,
            semantic_complete=(
                self._semantic_complete and semantic_mode is not SemanticMode.SYNTAX_ONLY
            ),
            dynamic_inputs=self._dynamic_inputs,
        )

    def dependencies(self, rows: Sequence[FactRow]) -> tuple[FactRow, ...]:
        """Build forward dependency rows from resolved semantic/manifest edges."""
        edges = self._eligible_edges(rows, include_manifest=True)
        output: list[FactRow] = []
        for source, target, row in edges:
            data = {
                "source_path": source,
                "target_path": target,
                "edge_kind": row.table,
            }
            output.append(
                FactRow(
                    table="derived.dependencies",
                    data=data,
                    evidence=derived_evidence(
                        row.evidence,
                        table="derived.dependencies",
                        data=data,
                    ),
                )
            )
        return tuple(output)

    def consumers(self, edge_rows: Sequence[FactRow]) -> tuple[FactRow, ...]:
        """Reverse every resolved semantic edge row.

        Takes one sequence rather than one parameter per source table so that a
        new edge producer needs no signature change here.
        """
        edges = self._eligible_edges(edge_rows, include_manifest=False)
        output: list[FactRow] = []
        for source, target, row in edges:
            data = {
                "source_path": target,
                "consumer_path": source,
                "edge_kind": row.table,
            }
            output.append(
                FactRow(
                    table="derived.consumers",
                    data=data,
                    evidence=derived_evidence(
                        row.evidence,
                        table="derived.consumers",
                        data=data,
                    ),
                )
            )
        return tuple(output)

    def impact(
        self,
        dependency_rows: Sequence[FactRow],
        *,
        seeds: Sequence[str],
        limits: TopologyLimits | None = None,
    ) -> MaterializationResult:
        """Traverse reverse dependencies with row/depth/byte/time caps."""
        active_limits = limits or TopologyLimits()
        deadline = time.monotonic() + active_limits.timeout_ms / 1000
        reverse: dict[str, list[tuple[str, FactRow]]] = defaultdict(list)
        for row in dependency_rows:
            edge = self._derived_dependency_edge(row)
            if edge is None:
                continue
            source, target = edge
            reverse[target].append((source, row))
        for target in reverse:
            reverse[target].sort(key=lambda item: (item[0], item[1].evidence.evidence_id))

        queue: deque[tuple[str, int, tuple[str, ...]]] = deque(
            (seed, 0, (seed,)) for seed in sorted(set(seeds))
        )
        visited = set(seeds)
        rows: list[FactRow] = []
        byte_count = 0
        depth_limited = False
        while queue:
            if time.monotonic() >= deadline:
                return MaterializationResult(
                    rows=tuple(rows),
                    truncated=True,
                    reasons=(f"time limit {active_limits.timeout_ms}ms reached",),
                )
            current, depth, path = queue.popleft()
            candidates = reverse.get(current, ())
            if depth >= active_limits.max_depth:
                if any(candidate not in visited for candidate, _row in candidates):
                    depth_limited = True
                continue
            for consumer, source_row in candidates:
                if consumer in visited:
                    continue
                if len(rows) >= active_limits.max_rows:
                    return MaterializationResult(
                        rows=tuple(rows),
                        truncated=True,
                        reasons=(f"row limit {active_limits.max_rows} reached",),
                    )
                next_path = (*path, consumer)
                data = {
                    "seed": path[0],
                    "path": consumer,
                    "depth": depth + 1,
                    "via": next_path,
                }
                row_bytes = sum(len(item.encode("utf-8")) for item in next_path)
                if byte_count + row_bytes > active_limits.max_bytes:
                    return MaterializationResult(
                        rows=tuple(rows),
                        truncated=True,
                        reasons=(f"byte limit {active_limits.max_bytes} reached",),
                    )
                rows.append(
                    FactRow(
                        table="derived.impact",
                        data=data,
                        evidence=derived_evidence(
                            source_row.evidence,
                            table="derived.impact",
                            data=data,
                        ),
                    )
                )
                byte_count += row_bytes
                visited.add(consumer)
                queue.append((consumer, depth + 1, next_path))
        if depth_limited:
            return MaterializationResult(
                rows=tuple(rows),
                truncated=True,
                reasons=(f"depth limit {active_limits.max_depth} reached",),
            )
        return MaterializationResult(rows=tuple(rows))

    def cycles(self, dependency_rows: Sequence[FactRow]) -> tuple[FactRow, ...]:
        """Return deterministic strongly connected components over resolved edges."""
        adjacency: dict[str, set[str]] = defaultdict(set)
        edge_rows: dict[tuple[str, str], FactRow] = {}
        for row in dependency_rows:
            edge = self._derived_dependency_edge(row)
            if edge is None:
                continue
            source, target = edge
            adjacency[source].add(target)
            adjacency.setdefault(target, set())
            edge_rows.setdefault((source, target), row)

        order = self._finish_order(adjacency)
        reverse: dict[str, set[str]] = {node: set() for node in adjacency}
        for source, targets in adjacency.items():
            for target in targets:
                reverse[target].add(source)

        assigned: set[str] = set()
        components: list[tuple[str, ...]] = []
        for start in reversed(order):
            if start in assigned:
                continue
            component: list[str] = []
            stack = [start]
            assigned.add(start)
            while stack:
                node = stack.pop()
                component.append(node)
                for neighbor in sorted(reverse[node], reverse=True):
                    if neighbor not in assigned:
                        assigned.add(neighbor)
                        stack.append(neighbor)
            members = tuple(sorted(component))
            has_self_loop = len(members) == 1 and members[0] in adjacency[members[0]]
            if len(members) > 1 or has_self_loop:
                components.append(members)

        output: list[FactRow] = []
        for members in sorted(components):
            member_set = frozenset(members)
            source_edge = min(
                (
                    (source, target, row)
                    for (source, target), row in edge_rows.items()
                    if source in member_set and target in member_set
                ),
                key=lambda item: (item[0], item[1], item[2].evidence.evidence_id),
            )[2]
            data = {
                "cycle_id": hashlib.sha256("\0".join(members).encode("utf-8")).hexdigest(),
                "members": members,
                "size": len(members),
            }
            output.append(
                FactRow(
                    table="derived.cycles",
                    data=data,
                    evidence=derived_evidence(
                        source_edge.evidence,
                        table="derived.cycles",
                        data=data,
                    ),
                )
            )
        return tuple(output)

    def dead_code_candidates(
        self,
        symbol_rows: Sequence[FactRow],
        *,
        entrypoints: Sequence[FactRow],
        consumers: Sequence[FactRow],
        semantic_complete: bool,
        dynamic_inputs: bool,
    ) -> tuple[FactRow, ...]:
        """Qualify unreachable symbols without claiming certainty."""
        entrypoint_paths = {
            target for row in entrypoints if isinstance((target := row.data.get("target")), str)
        }
        consumed_paths = {
            target for row in consumers if isinstance((target := row.data.get("source_path")), str)
        }
        uncertainty: list[str] = []
        if dynamic_inputs:
            uncertainty.append("dynamic relations present")
        if not semantic_complete:
            uncertainty.append("semantic coverage is partial")

        output: list[FactRow] = []
        for row in sorted(
            symbol_rows,
            key=lambda item: (
                str(item.data.get("path", "")),
                str(item.data.get("symbol_id", "")),
            ),
        ):
            if (
                row.evidence.evidence_kind is not EvidenceKind.SEMANTIC
                or row.evidence.resolution_status is not ResolutionStatus.RESOLVED
            ):
                continue
            path = row.data.get("path")
            symbol_id = row.data.get("symbol_id")
            if not isinstance(path, str) or not isinstance(symbol_id, str):
                continue
            if path in entrypoint_paths or path in consumed_paths:
                continue
            data = {
                "symbol_id": symbol_id,
                "path": path,
                "name": row.data.get("name"),
                "certainty": "uncertain" if uncertainty else "qualified_candidate",
                "uncertainty": tuple(uncertainty),
            }
            output.append(
                FactRow(
                    table="derived.dead_code_candidates",
                    data=data,
                    evidence=derived_evidence(
                        row.evidence,
                        table="derived.dead_code_candidates",
                        data=data,
                        resolution_status=ResolutionStatus.CANDIDATE,
                        confidence=0.35 if uncertainty else 0.65,
                        note="; ".join(uncertainty),
                    ),
                )
            )
        return tuple(output)

    def materialize(
        self,
        table_names: Sequence[str],
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
        *,
        seeds: Sequence[str] = (),
        limits: TopologyLimits | None = None,
        semantic_complete: bool = True,
        dynamic_inputs: bool = False,
    ) -> dict[str, tuple[FactRow, ...]]:
        """Materialize selected tables from request-local prerequisite rows."""
        imports = upstream_tables.get("semantic.imports", ())
        calls = upstream_tables.get("semantic.calls", ())
        manifest = upstream_tables.get("manifest.dependencies", ())
        needs_dependencies = any(
            table_name in {"derived.dependencies", "derived.impact", "derived.cycles"}
            for table_name in table_names
        )
        needs_consumers = any(
            table_name in {"derived.consumers", "derived.dead_code_candidates"}
            for table_name in table_names
        )
        dependencies = self.dependencies((*imports, *manifest)) if needs_dependencies else ()
        consumers = self.consumers((*imports, *calls)) if needs_consumers else ()
        output: dict[str, tuple[FactRow, ...]] = {}
        for table_name in table_names:
            if table_name == "derived.dependencies":
                output[table_name] = dependencies
            elif table_name == "derived.consumers":
                output[table_name] = consumers
            elif table_name == "derived.impact":
                output[table_name] = self.impact(
                    dependencies,
                    seeds=seeds,
                    limits=limits,
                ).rows
            elif table_name == "derived.cycles":
                output[table_name] = self.cycles(dependencies)
            elif table_name == "derived.dead_code_candidates":
                output[table_name] = self.dead_code_candidates(
                    upstream_tables.get("semantic.symbols", ()),
                    entrypoints=upstream_tables.get("authority.entrypoints", ()),
                    consumers=consumers,
                    semantic_complete=semantic_complete,
                    dynamic_inputs=dynamic_inputs,
                )
        return output

    @classmethod
    def _eligible_edges(
        cls,
        rows: Sequence[FactRow],
        *,
        include_manifest: bool,
    ) -> tuple[tuple[str, str, FactRow], ...]:
        selected: dict[tuple[str, str, str], FactRow] = {}
        for row in rows:
            if row.evidence.resolution_status is not ResolutionStatus.RESOLVED:
                continue
            # Eligibility is a property of the row's shape, not its table name:
            # `_data_edge` below already rejects anything without both path
            # keys. Naming tables here would mean adding a case per language,
            # and would exclude a resolved-reference producer that emits the
            # same shape.
            is_semantic = row.evidence.evidence_kind is EvidenceKind.SEMANTIC
            is_manifest = (
                include_manifest
                and row.evidence.evidence_kind is EvidenceKind.METADATA
                and row.evidence.authority is Authority.MANIFEST
            )
            if not is_semantic and not is_manifest:
                continue
            edge = cls._data_edge(row)
            if edge is None:
                continue
            source, target = edge
            selected.setdefault((source, target, row.table), row)
        return tuple(
            (source, target, selected[(source, target, table)])
            for source, target, table in sorted(selected)
        )

    @staticmethod
    def _data_edge(row: FactRow) -> tuple[str, str] | None:
        source = row.data.get("source_path")
        target = row.data.get("target_path")
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        return source, target

    @classmethod
    def _derived_dependency_edge(cls, row: FactRow) -> tuple[str, str] | None:
        if (
            row.table != "derived.dependencies"
            or row.evidence.evidence_kind is EvidenceKind.STRUCTURAL
            or row.evidence.resolution_status is not ResolutionStatus.RESOLVED
        ):
            return None
        return cls._data_edge(row)

    @staticmethod
    def _finish_order(adjacency: Mapping[str, set[str]]) -> tuple[str, ...]:
        visited: set[str] = set()
        order: list[str] = []
        for start in sorted(adjacency):
            if start in visited:
                continue
            visited.add(start)
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, expanded = stack.pop()
                if expanded:
                    order.append(node)
                    continue
                stack.append((node, True))
                for neighbor in sorted(adjacency[node], reverse=True):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append((neighbor, False))
        return tuple(order)
