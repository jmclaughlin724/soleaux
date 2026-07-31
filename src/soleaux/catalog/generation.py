"""Immutable in-memory generations and deterministic retrieval indexes."""

from __future__ import annotations

import collections
import collections.abc
import dataclasses
import datetime
import importlib.metadata
import pathlib
import types

import soleaux.catalog.contracts
import soleaux.catalog.postgresql
import soleaux.catalog.projects
import soleaux.catalog.structural
import soleaux.contracts.repository
import soleaux.contracts.snapshot
import soleaux.structural.snapshot

CATALOG_GENERATION_PRODUCER = "soleaux-catalog-generation"
CATALOG_GENERATION_VERSION = "1"
DEFAULT_CHUNK_BYTES = 8 * 1024
DEFAULT_CHUNK_LINES = 120
_STRUCTURAL_FACT_PRODUCERS = frozenset(
    {
        soleaux.catalog.postgresql.POSTGRESQL_CATALOG_PRODUCER,
        soleaux.catalog.structural.STRUCTURAL_PRODUCER,
    }
)

_EXCLUDED_NAMES = frozenset(
    {
        "bun.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "uv.lock",
        "yarn.lock",
    }
)
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".next",
        ".turbo",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "vendor",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class CatalogGeneration:
    """One immutable hot view published atomically."""

    number: int
    workspace_id: str
    snapshot_id: str
    source_fingerprint: str
    created_at: datetime.datetime
    snapshot: soleaux.contracts.snapshot.RepositorySnapshot
    inventory: tuple[str, ...]
    inventory_signatures: collections.abc.Mapping[str, tuple[int, int, int, int, int]]
    facts: soleaux.catalog.contracts.CatalogFacts
    projects_by_id: collections.abc.Mapping[str, soleaux.catalog.contracts.ProjectFact]
    tasks_by_key: collections.abc.Mapping[tuple[str, str, str], soleaux.catalog.contracts.TaskFact]
    dependencies_by_package: collections.abc.Mapping[
        str, tuple[soleaux.catalog.contracts.DependencyFact, ...]
    ]
    dependencies_by_project: collections.abc.Mapping[
        str, tuple[soleaux.catalog.contracts.DependencyFact, ...]
    ]
    routes_by_project: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.RouteFact, ...]]
    rules_by_id: collections.abc.Mapping[str, soleaux.catalog.contracts.RuleFact]
    policies_by_id: collections.abc.Mapping[str, soleaux.catalog.contracts.PolicyFact]
    symbols_by_id: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.SymbolFact, ...]]
    symbols_by_name: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.SymbolFact, ...]]
    imports_by_path: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.ImportFact, ...]]
    diagnostics_by_path: collections.abc.Mapping[
        str, tuple[soleaux.catalog.contracts.DiagnosticFact, ...]
    ]
    changes_by_path: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.ChangeFact, ...]]
    chunks_by_id: collections.abc.Mapping[str, soleaux.catalog.contracts.ChunkFact]
    chunks_by_path: collections.abc.Mapping[str, tuple[soleaux.catalog.contracts.ChunkFact, ...]]


def _chunk_kind(path: str) -> str:
    name = pathlib.PurePosixPath(path).name
    if name in {"AGENTS.md", "SKILL.md"}:
        return "instruction"
    if name in {"package.json", "pyproject.toml", "pnpm-workspace.yaml"}:
        return "manifest"
    if name.startswith(("tsconfig", "jsconfig")) or name.endswith(
        (".json", ".jsonc", ".toml", ".yaml", ".yml")
    ):
        return "configuration"
    if name.endswith((".md", ".mdx")):
        return "section"
    return "source"


def _eligible_for_chunks(path: str, content: bytes) -> bool:
    pure_path = pathlib.PurePosixPath(path)
    if pure_path.name in _EXCLUDED_NAMES:
        return False
    if any(part in _EXCLUDED_PARTS for part in pure_path.parts):
        return False
    if pure_path.name.endswith((".min.js", ".min.css", ".map")):
        return False
    return len(content) <= 4 * 1024 * 1024


def _split_large_line(encoded: bytes) -> tuple[bytes, ...]:
    """Split one UTF-8 line without cutting a code point."""
    segments: list[bytes] = []
    start = 0
    while start < len(encoded):
        end = min(start + DEFAULT_CHUNK_BYTES, len(encoded))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        segments.append(encoded[start:end])
        start = end
    return tuple(segments)


def _chunk_segments(content: bytes) -> tuple[tuple[int, int, int, int, str], ...]:
    """Return byte- and line-bound UTF-8 segments for one captured file."""
    lines = content.decode("utf-8").splitlines(keepends=True)
    if not lines:
        lines = [""]

    segments: list[tuple[int, int, int, int, str]] = []
    pending: list[bytes] = []
    pending_bytes = 0
    pending_start_line = 1
    pending_byte_start = 0
    byte_cursor = 0

    def flush(end_line: int) -> None:
        nonlocal pending, pending_bytes
        if not pending:
            return
        encoded = b"".join(pending)
        segments.append(
            (
                pending_byte_start,
                pending_byte_start + len(encoded),
                pending_start_line,
                end_line,
                encoded.decode("utf-8"),
            )
        )
        pending = []
        pending_bytes = 0

    for line_number, line in enumerate(lines, start=1):
        encoded = line.encode("utf-8")
        would_exceed_bytes = pending_bytes + len(encoded) > DEFAULT_CHUNK_BYTES
        would_exceed_lines = len(pending) >= DEFAULT_CHUNK_LINES
        if pending and (would_exceed_bytes or would_exceed_lines):
            flush(line_number - 1)
            pending_byte_start = byte_cursor

        if len(encoded) > DEFAULT_CHUNK_BYTES:
            for part in _split_large_line(encoded):
                segments.append(
                    (
                        byte_cursor,
                        byte_cursor + len(part),
                        line_number,
                        line_number,
                        part.decode("utf-8"),
                    )
                )
                byte_cursor += len(part)
            pending_byte_start = byte_cursor
            pending_start_line = line_number + 1
            continue

        if not pending:
            pending_start_line = line_number
            pending_byte_start = byte_cursor
        pending.append(encoded)
        pending_bytes += len(encoded)
        byte_cursor += len(encoded)

    flush(len(lines))
    return tuple(segments)


def _chunks(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
) -> tuple[soleaux.catalog.contracts.ChunkFact, ...]:
    chunks: list[soleaux.catalog.contracts.ChunkFact] = []
    captured_by_path = {item.path: item for item in bundle.snapshot.files}
    for path, content in sorted(bundle.contents.items()):
        if not _eligible_for_chunks(path, content):
            continue
        captured = captured_by_path[path]
        for byte_start, byte_end, start_line, end_line, text in _chunk_segments(content):
            identity = (
                f"{bundle.snapshot.workspace_id}\0{path}\0{captured.content_hash}\0"
                f"{byte_start}\0{byte_end}"
            ).encode()
            chunks.append(
                soleaux.catalog.contracts.ChunkFact(
                    workspace_id=bundle.snapshot.workspace_id,
                    source_path=path,
                    source_digest=captured.content_hash,
                    producer=CATALOG_GENERATION_PRODUCER,
                    producer_version=CATALOG_GENERATION_VERSION,
                    chunk_id=soleaux.contracts.repository.content_digest(identity),
                    path=path,
                    language_id=captured.language_id,
                    chunk_kind=_chunk_kind(path),
                    start_line=start_line,
                    end_line=end_line,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    text=text,
                )
            )
    return tuple(chunks)


class CatalogGenerationBuilder:
    """Build complete normalized facts and indexes outside persistence transactions."""

    def build_base(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        generation: int,
        inventory: collections.abc.Sequence[str] = (),
        inventory_signatures: collections.abc.Mapping[str, tuple[int, int, int, int, int]]
        | None = None,
    ) -> CatalogGeneration:
        """Build only deterministic retrieval chunks for bounded startup."""
        return catalog_generation_from_facts(
            generation=generation,
            snapshot=bundle.snapshot,
            facts=soleaux.catalog.contracts.CatalogFacts(chunks=_chunks(bundle)),
            inventory=tuple(inventory),
            inventory_signatures=inventory_signatures or {},
        )

    def build(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        generation: int,
        inventory: collections.abc.Sequence[str] = (),
        inventory_signatures: collections.abc.Mapping[str, tuple[int, int, int, int, int]]
        | None = None,
    ) -> CatalogGeneration:
        manifest_facts = soleaux.catalog.projects.ProjectCatalogExtractor().extract(bundle)
        facts = manifest_facts.model_copy(update={"chunks": _chunks(bundle)})
        return catalog_generation_from_facts(
            generation=generation,
            snapshot=bundle.snapshot,
            facts=facts,
            inventory=tuple(inventory),
            inventory_signatures=inventory_signatures or {},
        )

    def update(
        self,
        current: CatalogGeneration,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        generation: int,
        changed_paths: frozenset[str],
        inventory: collections.abc.Sequence[str] = (),
        inventory_signatures: collections.abc.Mapping[str, tuple[int, int, int, int, int]]
        | None = None,
    ) -> CatalogGeneration:
        """Rebuild cheap project facts and only the changed retrieval chunks."""
        manifest_facts = soleaux.catalog.projects.ProjectCatalogExtractor().extract(bundle)
        retained_chunks = tuple(
            chunk
            for chunk in current.facts.chunks
            if chunk.path not in changed_paths and chunk.producer not in _STRUCTURAL_FACT_PRODUCERS
        )
        changed_bundle = soleaux.structural.snapshot.SnapshotBundle(
            snapshot=bundle.snapshot,
            contents={
                path: content for path, content in bundle.contents.items() if path in changed_paths
            },
            notes=bundle.notes,
        )
        changed_chunks = _chunks(changed_bundle)
        changes = _change_facts(
            current,
            bundle.snapshot,
            generation=generation,
            changed_paths=changed_paths,
        )
        facts = manifest_facts.model_copy(
            update={
                "chunks": tuple(
                    sorted(
                        (*retained_chunks, *changed_chunks),
                        key=lambda chunk: (chunk.path, chunk.byte_start, chunk.chunk_id),
                    )
                ),
                "changes": changes,
            }
        )
        unchanged_paths = frozenset(
            item.path for item in bundle.snapshot.files if item.path not in changed_paths
        )
        facts = _retain_unchanged_structural_facts(
            current.facts,
            facts,
            paths=unchanged_paths,
            path_languages={item.path: item.language for item in bundle.snapshot.files},
        )
        return catalog_generation_from_facts(
            generation=generation,
            snapshot=bundle.snapshot,
            facts=facts,
            inventory=tuple(inventory),
            inventory_signatures=inventory_signatures or {},
        )


def _retain_unchanged_structural_facts(
    previous: soleaux.catalog.contracts.CatalogFacts,
    current: soleaux.catalog.contracts.CatalogFacts,
    *,
    paths: frozenset[str],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.CatalogFacts:
    """Carry byte-bound facts after rebinding them to current project facts."""
    if not paths:
        return current

    retained_dependencies = tuple(
        _rebind_dependency(dependency, current.projects, path_languages)
        for dependency in _retained_structural_rows(previous.dependencies, paths=paths)
        if dependency.usage is not soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT
    )
    engines = tuple(
        _rebind_engine(engine, current.projects, path_languages)
        for engine in _retained_structural_rows(previous.engines, paths=paths)
    )
    symbols = tuple(
        _rebind_symbol(symbol, current.projects, path_languages)
        for symbol in _retained_structural_rows(previous.symbols, paths=paths)
    )
    imports = tuple(
        _rebind_import(imported, current.projects, path_languages)
        for imported in _retained_structural_rows(previous.imports, paths=paths)
    )
    diagnostics = tuple(
        _rebind_diagnostic(diagnostic, current.projects, path_languages)
        for diagnostic in _retained_structural_rows(previous.diagnostics, paths=paths)
    )
    chunks = _retained_structural_rows(previous.chunks, paths=paths)
    dependencies = _rebuild_direct_import_dependencies(
        (*current.dependencies, *retained_dependencies),
        imports,
        path_languages=path_languages,
    )
    routes = _preserve_route_enrichment(
        previous.routes,
        current.routes,
        paths=paths,
    )
    warnings = tuple(
        warning
        for warning in previous.warnings
        if any(warning.startswith(f"{path}:") for path in paths)
    )
    return current.model_copy(
        update={
            "dependencies": tuple(
                sorted(
                    dependencies,
                    key=lambda item: (
                        item.project_id,
                        item.scope,
                        item.package_name,
                        item.usage,
                        item.source_path,
                    ),
                )
            ),
            "engines": tuple(
                sorted(
                    (*current.engines, *engines),
                    key=lambda item: (item.project_id, item.engine_id),
                )
            ),
            "symbols": tuple(
                sorted(
                    (*current.symbols, *symbols),
                    key=lambda item: (item.path, item.byte_start, item.symbol_id),
                )
            ),
            "imports": tuple(
                sorted(
                    (*current.imports, *imports),
                    key=lambda item: (item.path, item.import_id),
                )
            ),
            "diagnostics": tuple(
                sorted(
                    (*current.diagnostics, *diagnostics),
                    key=lambda item: (item.path, item.byte_start, item.diagnostic_id),
                )
            ),
            "chunks": tuple(
                sorted(
                    (*current.chunks, *chunks),
                    key=lambda item: (item.path, item.byte_start, item.chunk_id),
                )
            ),
            "routes": routes,
            "warnings": tuple(dict.fromkeys((*current.warnings, *warnings))),
        }
    )


def _canonical_project_id(
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    *,
    path: str,
    path_languages: collections.abc.Mapping[str, str | None],
    producer: str,
    workspace_id: str,
) -> str:
    language = path_languages.get(path)
    kind = (
        soleaux.catalog.contracts.ProjectKind.PYTHON
        if language is not None and language.casefold() == "python"
        else None
    )
    project_id = soleaux.catalog.structural.project_id_for_path(projects, path, kind=kind)
    if not project_id and producer == soleaux.catalog.postgresql.POSTGRESQL_CATALOG_PRODUCER:
        return f"{workspace_id}:postgresql:."
    return project_id


def _rebind_dependency(
    dependency: soleaux.catalog.contracts.DependencyFact,
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.DependencyFact:
    project_id = _canonical_project_id(
        projects,
        path=dependency.source_path,
        path_languages=path_languages,
        producer=dependency.producer,
        workspace_id=dependency.workspace_id,
    )
    if project_id == dependency.project_id:
        return dependency
    return dependency.model_copy(update={"project_id": project_id})


def _rebind_engine(
    engine: soleaux.catalog.contracts.EngineFact,
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.EngineFact:
    project_id = _canonical_project_id(
        projects,
        path=engine.source_path,
        path_languages=path_languages,
        producer=engine.producer,
        workspace_id=engine.workspace_id,
    )
    if project_id == engine.project_id:
        return engine
    return engine.model_copy(update={"project_id": project_id})


def _rebind_symbol(
    symbol: soleaux.catalog.contracts.SymbolFact,
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.SymbolFact:
    project_id = _canonical_project_id(
        projects,
        path=symbol.path,
        path_languages=path_languages,
        producer=symbol.producer,
        workspace_id=symbol.workspace_id,
    )
    if project_id == symbol.project_id:
        return symbol
    updates: dict[str, str] = {"project_id": project_id}
    if symbol.producer == soleaux.catalog.structural.STRUCTURAL_PRODUCER:
        symbol_id = soleaux.contracts.repository.content_digest(
            (
                f"{symbol.workspace_id}\0{project_id}\0{symbol.path}\0"
                f"{symbol.name}\0{symbol.byte_start}\0{symbol.byte_end}"
            ).encode()
        )
        updates["symbol_id"] = symbol_id
        updates["revision_id"] = soleaux.contracts.repository.content_digest(
            (
                f"{symbol_id}\0{symbol.source_digest}\0{symbol.engine_id}\0{symbol.symbol_kind}\0"
            ).encode()
        )
    return symbol.model_copy(update=updates)


def _rebind_import(
    imported: soleaux.catalog.contracts.ImportFact,
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.ImportFact:
    project_id = _canonical_project_id(
        projects,
        path=imported.path,
        path_languages=path_languages,
        producer=imported.producer,
        workspace_id=imported.workspace_id,
    )
    if project_id == imported.project_id:
        return imported
    return imported.model_copy(update={"project_id": project_id})


def _rebind_diagnostic(
    diagnostic: soleaux.catalog.contracts.DiagnosticFact,
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path_languages: collections.abc.Mapping[str, str | None],
) -> soleaux.catalog.contracts.DiagnosticFact:
    project_id = _canonical_project_id(
        projects,
        path=diagnostic.path,
        path_languages=path_languages,
        producer=diagnostic.producer,
        workspace_id=diagnostic.workspace_id,
    )
    if project_id == diagnostic.project_id:
        return diagnostic
    return diagnostic.model_copy(update={"project_id": project_id})


def _normalized_distribution(value: str) -> str:
    return value.casefold().replace("-", "").replace("_", "").replace(".", "")


def _rebuild_direct_import_dependencies(
    dependencies: collections.abc.Sequence[soleaux.catalog.contracts.DependencyFact],
    imports: collections.abc.Sequence[soleaux.catalog.contracts.ImportFact],
    *,
    path_languages: collections.abc.Mapping[str, str | None],
) -> tuple[soleaux.catalog.contracts.DependencyFact, ...]:
    declared_by_project: dict[str, dict[str, soleaux.catalog.contracts.DependencyFact]] = {}
    for dependency in dependencies:
        if dependency.usage is not soleaux.catalog.contracts.DependencyUsage.DECLARED:
            continue
        declared_by_project.setdefault(dependency.project_id, {})[
            _normalized_distribution(dependency.package_name)
        ] = dependency

    distributions = importlib.metadata.packages_distributions()
    direct: list[soleaux.catalog.contracts.DependencyFact] = []
    emitted: set[tuple[str, str]] = set()
    for imported in imports:
        language = path_languages.get(imported.path)
        if (
            imported.producer != soleaux.catalog.structural.STRUCTURAL_PRODUCER
            or language is None
            or language.casefold() != "python"
            or imported.specifier.startswith(".")
        ):
            continue
        module = imported.specifier.partition(".")[0]
        declared = declared_by_project.get(imported.project_id, {})
        dependency = next(
            (
                declared.get(_normalized_distribution(candidate))
                for candidate in distributions.get(module, [module])
                if _normalized_distribution(candidate) in declared
            ),
            None,
        )
        if dependency is None:
            continue
        key = (imported.path, dependency.package_name)
        if key in emitted:
            continue
        emitted.add(key)
        direct.append(
            soleaux.catalog.contracts.DependencyFact(
                workspace_id=imported.workspace_id,
                source_path=imported.source_path,
                source_digest=imported.source_digest,
                producer=soleaux.catalog.structural.STRUCTURAL_PRODUCER,
                producer_version=imported.producer_version,
                project_id=imported.project_id,
                package_name=dependency.package_name,
                declared_specifier=dependency.declared_specifier,
                resolved_specifier=dependency.resolved_specifier,
                scope=dependency.scope,
                usage=soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT,
            )
        )
    return (*dependencies, *direct)


def _route_key(
    route: soleaux.catalog.contracts.RouteFact,
) -> tuple[str, str, str | None, str, str | None]:
    return (
        route.source_path,
        route.framework,
        route.route,
        route.registration_kind,
        route.router,
    )


def _preserve_route_enrichment(
    previous: collections.abc.Sequence[soleaux.catalog.contracts.RouteFact],
    current: collections.abc.Sequence[soleaux.catalog.contracts.RouteFact],
    *,
    paths: frozenset[str],
) -> tuple[soleaux.catalog.contracts.RouteFact, ...]:
    previous_by_key = {
        _route_key(route): route
        for route in previous
        if route.source_path in paths and (route.methods or route.runtime is not None)
    }
    routes: list[soleaux.catalog.contracts.RouteFact] = []
    for route in current:
        enriched = previous_by_key.get(_route_key(route))
        if enriched is None or enriched.source_digest != route.source_digest:
            routes.append(route)
            continue
        routes.append(
            route.model_copy(
                update={
                    "methods": enriched.methods,
                    "runtime": enriched.runtime,
                    "complete": route.complete and enriched.complete,
                }
            )
        )
    return tuple(routes)


def _retained_structural_rows[RecordT: soleaux.catalog.contracts.CatalogRecord](
    rows: collections.abc.Sequence[RecordT],
    *,
    paths: frozenset[str],
) -> tuple[RecordT, ...]:
    return tuple(
        row
        for row in rows
        if row.producer in _STRUCTURAL_FACT_PRODUCERS and row.source_path in paths
    )


def changed_snapshot_paths(
    current: soleaux.contracts.snapshot.RepositorySnapshot,
    updated: soleaux.contracts.snapshot.RepositorySnapshot,
) -> frozenset[str]:
    """Return added, removed, and digest-changed repository identities."""
    current_hashes = {item.path: item.content_hash for item in current.files}
    updated_hashes = {item.path: item.content_hash for item in updated.files}
    return frozenset(
        path
        for path in current_hashes.keys() | updated_hashes.keys()
        if current_hashes.get(path) != updated_hashes.get(path)
    )


def _change_facts(
    current: CatalogGeneration,
    updated: soleaux.contracts.snapshot.RepositorySnapshot,
    *,
    generation: int,
    changed_paths: frozenset[str],
) -> tuple[soleaux.catalog.contracts.ChangeFact, ...]:
    current_hashes = {item.path: item.content_hash for item in current.snapshot.files}
    updated_hashes = {item.path: item.content_hash for item in updated.files}
    facts: list[soleaux.catalog.contracts.ChangeFact] = []
    for path in sorted(changed_paths):
        previous_digest = current_hashes.get(path)
        current_digest = updated_hashes.get(path)
        if previous_digest is None:
            operation = "added"
        elif current_digest is None:
            operation = "deleted"
        else:
            operation = "changed"
        identity = (
            f"{updated.workspace_id}\0{generation}\0{path}\0{operation}\0"
            f"{previous_digest or ''}\0{current_digest or ''}"
        ).encode()
        facts.append(
            soleaux.catalog.contracts.ChangeFact(
                workspace_id=updated.workspace_id,
                source_path=path,
                source_digest=current_digest
                or previous_digest
                or soleaux.contracts.repository.content_digest(b""),
                producer=CATALOG_GENERATION_PRODUCER,
                producer_version=CATALOG_GENERATION_VERSION,
                change_id=soleaux.contracts.repository.content_digest(identity),
                generation=generation,
                path=path,
                operation=operation,
                previous_digest=previous_digest,
                current_digest=current_digest,
            )
        )
    return tuple(facts)


def catalog_generation_from_facts(
    *,
    generation: int,
    snapshot: soleaux.contracts.snapshot.RepositorySnapshot,
    facts: soleaux.catalog.contracts.CatalogFacts,
    created_at: datetime.datetime | None = None,
    inventory: tuple[str, ...] = (),
    inventory_signatures: collections.abc.Mapping[str, tuple[int, int, int, int, int]]
    | None = None,
) -> CatalogGeneration:
    """Build every immutable reverse index from validated normalized facts."""
    dependencies_by_package: collections.defaultdict[
        str, list[soleaux.catalog.contracts.DependencyFact]
    ] = collections.defaultdict(list)
    dependencies_by_project: collections.defaultdict[
        str, list[soleaux.catalog.contracts.DependencyFact]
    ] = collections.defaultdict(list)
    routes_by_project: collections.defaultdict[str, list[soleaux.catalog.contracts.RouteFact]] = (
        collections.defaultdict(list)
    )
    symbols_by_id: collections.defaultdict[str, list[soleaux.catalog.contracts.SymbolFact]] = (
        collections.defaultdict(list)
    )
    symbols_by_name: collections.defaultdict[str, list[soleaux.catalog.contracts.SymbolFact]] = (
        collections.defaultdict(list)
    )
    imports_by_path: collections.defaultdict[str, list[soleaux.catalog.contracts.ImportFact]] = (
        collections.defaultdict(list)
    )
    diagnostics_by_path: collections.defaultdict[
        str, list[soleaux.catalog.contracts.DiagnosticFact]
    ] = collections.defaultdict(list)
    changes_by_path: collections.defaultdict[str, list[soleaux.catalog.contracts.ChangeFact]] = (
        collections.defaultdict(list)
    )
    chunks_by_path: collections.defaultdict[str, list[soleaux.catalog.contracts.ChunkFact]] = (
        collections.defaultdict(list)
    )
    for dependency in facts.dependencies:
        dependencies_by_package[dependency.package_name.casefold()].append(dependency)
        dependencies_by_project[dependency.project_id].append(dependency)
    for chunk in facts.chunks:
        chunks_by_path[chunk.path].append(chunk)
    for route in facts.routes:
        routes_by_project[route.project_id or ""].append(route)
    for symbol in facts.symbols:
        symbols_by_id[symbol.symbol_id].append(symbol)
        symbols_by_name[symbol.name.casefold()].append(symbol)
    for imported in facts.imports:
        imports_by_path[imported.path].append(imported)
    for diagnostic in facts.diagnostics:
        diagnostics_by_path[diagnostic.path].append(diagnostic)
    for change in facts.changes:
        changes_by_path[change.path].append(change)
    return CatalogGeneration(
        number=generation,
        workspace_id=snapshot.workspace_id,
        snapshot_id=snapshot.snapshot_id,
        source_fingerprint=snapshot.source_fingerprint,
        created_at=created_at or datetime.datetime.now(datetime.UTC),
        snapshot=snapshot,
        inventory=inventory,
        inventory_signatures=types.MappingProxyType(dict(inventory_signatures or {})),
        facts=facts,
        projects_by_id=types.MappingProxyType(
            {project.project_id: project for project in facts.projects}
        ),
        tasks_by_key=types.MappingProxyType(
            {(task.project_id, task.runner, task.task_id): task for task in facts.tasks}
        ),
        dependencies_by_package=types.MappingProxyType(
            {package: tuple(rows) for package, rows in dependencies_by_package.items()}
        ),
        dependencies_by_project=types.MappingProxyType(
            {project: tuple(rows) for project, rows in dependencies_by_project.items()}
        ),
        routes_by_project=types.MappingProxyType(
            {project: tuple(rows) for project, rows in routes_by_project.items()}
        ),
        rules_by_id=types.MappingProxyType({rule.rule_id: rule for rule in facts.rules}),
        policies_by_id=types.MappingProxyType(
            {policy.policy_id: policy for policy in facts.policies}
        ),
        symbols_by_id=types.MappingProxyType(
            {symbol_id: tuple(rows) for symbol_id, rows in symbols_by_id.items()}
        ),
        symbols_by_name=types.MappingProxyType(
            {name: tuple(rows) for name, rows in symbols_by_name.items()}
        ),
        imports_by_path=types.MappingProxyType(
            {path: tuple(rows) for path, rows in imports_by_path.items()}
        ),
        diagnostics_by_path=types.MappingProxyType(
            {path: tuple(rows) for path, rows in diagnostics_by_path.items()}
        ),
        changes_by_path=types.MappingProxyType(
            {path: tuple(rows) for path, rows in changes_by_path.items()}
        ),
        chunks_by_id=types.MappingProxyType({chunk.chunk_id: chunk for chunk in facts.chunks}),
        chunks_by_path=types.MappingProxyType(
            {path: tuple(rows) for path, rows in chunks_by_path.items()}
        ),
    )
