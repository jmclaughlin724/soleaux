"""Generation-time promotion of structural fragments into persisted catalog facts.

One merge owner per producer, sibling to `catalog/lsp.py` and
`catalog/typescript.py`: declaration fragments become `SymbolFact` rows with
`coverage="syntactic"`, import fragments become unresolved `ImportFact` rows,
and export fragments carry HTTP method and runtime evidence onto `RouteFact`s.
Semantic engines stay authoritative — a structural symbol never replaces or
shadows an overlapping semantic one.
"""

from __future__ import annotations

import collections.abc
import importlib.metadata

import soleaux.catalog.contracts
import soleaux.contracts.repository
import soleaux.structural.fragments

STRUCTURAL_ANALYZER_IDS = frozenset(
    {
        soleaux.structural.fragments.AST_GREP_ANALYZER_ID,
        soleaux.structural.fragments.LIBCST_ANALYZER_ID,
    }
)
STRUCTURAL_PRODUCER = "soleaux-structural-catalog"
_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

#: Projections one generation-time extraction pass requests per file.
STRUCTURAL_CATALOG_PROJECTIONS: tuple[str, ...] = (
    "syntax.declarations",
    "syntax.imports",
    "syntax.exports",
)


class ExtractedFile:
    """Fragments extracted from one captured file at one digest."""

    __slots__ = ("digest", "fragments", "language")

    def __init__(
        self,
        *,
        digest: str,
        language: str,
        fragments: tuple[soleaux.structural.fragments.SyntaxFragment, ...],
    ):
        self.digest = digest
        self.language = language
        self.fragments = fragments


def project_id_for_path(
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path: str,
    *,
    kind: soleaux.catalog.contracts.ProjectKind | None = None,
) -> str:
    """The owning project by longest root-path prefix; '' when unowned."""
    best = ""
    best_length = -1
    for project in projects:
        if kind is not None and project.kind is not kind:
            continue
        root = project.root_path
        if root == "" and best_length < 0:
            best, best_length = project.project_id, 0
            continue
        if root and (path == root or path.startswith(f"{root}/")) and len(root) > best_length:
            best, best_length = project.project_id, len(root)
    return best


def _exported_names(
    exports: tuple[soleaux.structural.fragments.SyntaxFragment, ...],
) -> frozenset[str]:
    names: set[str] = set()
    for fragment in exports:
        if fragment.name is not None:
            names.add(fragment.name)
        listed = fragment.attributes.get("exported_names")
        if isinstance(listed, list):
            names.update(name for name in listed if isinstance(name, str))
    return frozenset(names)


def _encloses(
    export: soleaux.structural.fragments.SyntaxFragment,
    declaration: soleaux.structural.fragments.SyntaxFragment,
) -> bool:
    return export.byte_start <= declaration.byte_start and declaration.byte_end <= export.byte_end


def _runtime_export(exports: tuple[soleaux.structural.fragments.SyntaxFragment, ...]) -> str | None:
    for fragment in exports:
        if fragment.name != "runtime":
            continue
        initializer = fragment.attributes.get("initializer_text")
        if isinstance(initializer, str) and initializer:
            return initializer.strip("\"'")
    return None


def merge_structural_facts(
    facts: soleaux.catalog.contracts.CatalogFacts,
    *,
    workspace_id: str,
    extracted: collections.abc.Mapping[str, ExtractedFile],
) -> soleaux.catalog.contracts.CatalogFacts:
    """Replace the structural facts of exactly the re-extracted paths."""
    if not extracted:
        return facts

    projects = facts.projects
    retained_symbols = tuple(
        symbol
        for symbol in facts.symbols
        if not (symbol.engine_id in STRUCTURAL_ANALYZER_IDS and symbol.path in extracted)
    )
    retained_imports = tuple(
        imported
        for imported in facts.imports
        if not (imported.engine_id in STRUCTURAL_ANALYZER_IDS and imported.path in extracted)
    )
    python_paths = frozenset(
        path for path, extraction in extracted.items() if extraction.language.casefold() == "python"
    )
    retained_dependencies = tuple(
        dependency
        for dependency in facts.dependencies
        if not (
            dependency.source_path in python_paths
            and dependency.usage is soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT
            and dependency.producer in {STRUCTURAL_PRODUCER, "stdlib:ast"}
        )
    )
    semantic_spans: dict[str, list[tuple[str, int, int]]] = {}
    for symbol in retained_symbols:
        if symbol.path in extracted:
            semantic_spans.setdefault(symbol.path, []).append(
                (symbol.name, symbol.byte_start, symbol.byte_end)
            )

    symbols: list[soleaux.catalog.contracts.SymbolFact] = []
    imports: list[soleaux.catalog.contracts.ImportFact] = []
    direct_dependencies: list[soleaux.catalog.contracts.DependencyFact] = []
    exports_by_path: dict[str, tuple[soleaux.structural.fragments.SyntaxFragment, ...]] = {}
    package_distributions = importlib.metadata.packages_distributions()
    declared_by_project: dict[str, dict[str, soleaux.catalog.contracts.DependencyFact]] = {}
    for dependency in retained_dependencies:
        if dependency.usage is not soleaux.catalog.contracts.DependencyUsage.DECLARED:
            continue
        declared_by_project.setdefault(dependency.project_id, {})[
            _normalized_distribution(dependency.package_name)
        ] = dependency
    for path, extraction in extracted.items():
        declarations = tuple(
            fragment
            for fragment in extraction.fragments
            if fragment.projection == "syntax.declarations"
        )
        import_fragments = tuple(
            fragment for fragment in extraction.fragments if fragment.projection == "syntax.imports"
        )
        exports = tuple(
            fragment for fragment in extraction.fragments if fragment.projection == "syntax.exports"
        )
        exports_by_path[path] = exports
        exported_names = _exported_names(exports)
        is_python = extraction.language.casefold() == "python"
        project_id = project_id_for_path(
            projects,
            path,
            kind=soleaux.catalog.contracts.ProjectKind.PYTHON if is_python else None,
        )
        producer_version = soleaux.structural.fragments.analyzer_version_for(extraction.language)
        analyzer_id = soleaux.structural.fragments.analyzer_id_for(extraction.language)
        occupied = semantic_spans.get(path, [])

        for declaration in declarations:
            name = declaration.name
            if name is None:
                continue
            if any(
                occupied_name == name
                and occupied_start < declaration.byte_end
                and declaration.byte_start < occupied_end
                for occupied_name, occupied_start, occupied_end in occupied
            ):
                continue
            symbol_id = soleaux.contracts.repository.content_digest(
                (
                    f"{workspace_id}\0{project_id}\0{path}\0"
                    f"{name}\0{declaration.byte_start}\0{declaration.byte_end}"
                ).encode()
            )
            revision_id = soleaux.contracts.repository.content_digest(
                (f"{symbol_id}\0{extraction.digest}\0{analyzer_id}\0{declaration.kind}\0").encode()
            )
            symbols.append(
                soleaux.catalog.contracts.SymbolFact(
                    workspace_id=workspace_id,
                    source_path=path,
                    source_digest=extraction.digest,
                    producer=STRUCTURAL_PRODUCER,
                    producer_version=producer_version,
                    symbol_id=symbol_id,
                    revision_id=revision_id,
                    project_id=project_id,
                    path=path,
                    name=name,
                    symbol_kind=declaration.kind,
                    byte_start=declaration.byte_start,
                    byte_end=declaration.byte_end,
                    exported=name in exported_names
                    or any(_encloses(export, declaration) for export in exports),
                    engine_id=analyzer_id,
                    coverage="syntactic",
                )
            )

        for fragment in import_fragments:
            specifier = fragment.name
            if specifier is None:
                continue
            import_id = soleaux.contracts.repository.content_digest(
                (
                    f"{workspace_id}\0{project_id}\0{path}\0{specifier}\0{fragment.byte_start}"
                ).encode()
            )
            imports.append(
                soleaux.catalog.contracts.ImportFact(
                    workspace_id=workspace_id,
                    source_path=path,
                    source_digest=extraction.digest,
                    producer=STRUCTURAL_PRODUCER,
                    producer_version=producer_version,
                    import_id=import_id,
                    project_id=project_id,
                    path=path,
                    specifier=specifier,
                    resolved_path=None,
                    usage=soleaux.catalog.contracts.DependencyUsage.UNRESOLVED,
                    engine_id=analyzer_id,
                )
            )
        if is_python:
            declared = declared_by_project.get(project_id, {})
            imported_modules = {
                fragment.name.partition(".")[0]
                for fragment in import_fragments
                if fragment.name is not None and not fragment.name.startswith(".")
            }
            emitted_packages: set[str] = set()
            for module in sorted(imported_modules):
                candidates = package_distributions.get(module, [module])
                dependency = next(
                    (
                        declared.get(_normalized_distribution(candidate))
                        for candidate in candidates
                        if _normalized_distribution(candidate) in declared
                    ),
                    None,
                )
                if dependency is None or dependency.package_name in emitted_packages:
                    continue
                emitted_packages.add(dependency.package_name)
                direct_dependencies.append(
                    soleaux.catalog.contracts.DependencyFact(
                        workspace_id=workspace_id,
                        source_path=path,
                        source_digest=extraction.digest,
                        producer=STRUCTURAL_PRODUCER,
                        producer_version=producer_version,
                        project_id=project_id,
                        package_name=dependency.package_name,
                        declared_specifier=dependency.declared_specifier,
                        resolved_specifier=dependency.resolved_specifier,
                        scope=dependency.scope,
                        usage=soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT,
                    )
                )

    routes = tuple(
        _route_with_export_evidence(route, exports_by_path[route.source_path])
        if route.source_path in exports_by_path
        else route
        for route in facts.routes
    )

    return facts.model_copy(
        update={
            "symbols": tuple(
                sorted(
                    (*retained_symbols, *symbols),
                    key=lambda item: (item.path, item.byte_start, item.symbol_id),
                )
            ),
            "imports": tuple(
                sorted(
                    (*retained_imports, *imports),
                    key=lambda item: (item.path, item.import_id),
                )
            ),
            "dependencies": tuple(
                sorted(
                    (*retained_dependencies, *direct_dependencies),
                    key=lambda item: (
                        item.project_id,
                        item.scope,
                        item.package_name,
                        item.usage,
                        item.source_path,
                    ),
                )
            ),
            "routes": routes,
        }
    )


def _normalized_distribution(value: str) -> str:
    return value.casefold().replace("-", "").replace("_", "").replace(".", "")


def _route_with_export_evidence(
    route: soleaux.catalog.contracts.RouteFact,
    exports: tuple[soleaux.structural.fragments.SyntaxFragment, ...],
) -> soleaux.catalog.contracts.RouteFact:
    methods = tuple(sorted(_exported_names(exports) & _HTTP_METHODS))
    runtime = _runtime_export(exports) or route.runtime
    return route.model_copy(
        update={
            "methods": methods,
            "runtime": runtime,
            "complete": route.complete and bool(methods or route.route is not None),
        }
    )
