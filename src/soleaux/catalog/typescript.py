"""Merge managed TypeScript source usage into normalized catalog facts."""

from __future__ import annotations

import typing

import soleaux.catalog.contracts
import soleaux.contracts.repository
import soleaux.structural.snapshot
import soleaux.typescript.contracts

_TYPESCRIPT_SOURCE_SUFFIXES = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)
_MAX_PROJECT_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_PROJECT_SOURCES = 2048


def _is_object_list(value: object) -> typing.TypeGuard[list[object]]:
    return isinstance(value, list)


def build_typescript_requests(
    facts: soleaux.catalog.contracts.CatalogFacts,
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    project_ids: frozenset[str] = frozenset(),
) -> tuple[soleaux.typescript.contracts.TypeScriptAnalysisRequest, ...]:
    """Build bounded project-specific requests from captured catalog bytes."""
    node_projects = tuple(
        project
        for project in facts.projects
        if project.kind is soleaux.catalog.contracts.ProjectKind.NODE
    )
    selected = tuple(
        project for project in node_projects if not project_ids or project.project_id in project_ids
    )
    projects_by_name = {
        project.name: project for project in node_projects if project.name is not None
    }
    captured_by_path = {item.path: item for item in bundle.snapshot.files}
    requests: list[soleaux.typescript.contracts.TypeScriptAnalysisRequest] = []
    for project in selected:
        owned_roots = _dependency_project_closure(
            project,
            facts,
            projects_by_name,
        )
        own_paths, required_paths = _request_paths_for_project(
            project,
            owned_roots,
            facts,
            node_projects,
            tuple(bundle.contents),
        )
        if not own_paths:
            continue
        config = next(
            (
                row
                for row in facts.configs
                if row.project_id == project.project_id and row.config_kind == "typescript"
            ),
            None,
        )
        sources: list[soleaux.typescript.contracts.TypeScriptSource] = []
        total_bytes = 0
        for path in sorted(required_paths):
            content = bundle.contents.get(path)
            captured = captured_by_path.get(path)
            if content is None or captured is None:
                continue
            if (
                len(sources) >= _MAX_PROJECT_SOURCES
                or total_bytes + len(content) > _MAX_PROJECT_SOURCE_BYTES
            ):
                break
            sources.append(
                soleaux.typescript.contracts.TypeScriptSource(
                    path=path,
                    digest=captured.content_hash,
                    text=content.decode("utf-8"),
                )
            )
            total_bytes += len(content)
        supplied = {source.path for source in sources}
        root_files = tuple(path for path in own_paths if path in supplied)
        config_path = (
            config.config_path if config is not None and config.config_path in supplied else None
        )
        if not sources or not root_files:
            continue
        requests.append(
            soleaux.typescript.contracts.TypeScriptAnalysisRequest(
                workspace_id=bundle.snapshot.workspace_id,
                project_id=project.project_id,
                config_path=config_path,
                root_files=root_files,
                package_roots={
                    dependency.name: dependency.root_path
                    for dependency in owned_roots
                    if dependency.name is not None and dependency != project
                },
                sources=tuple(sources),
            )
        )
    return tuple(requests)


def typescript_request_paths(
    facts: soleaux.catalog.contracts.CatalogFacts,
    paths: tuple[str, ...],
    *,
    project_ids: frozenset[str],
) -> tuple[str, ...]:
    """Return the bounded file closure needed by selected TypeScript projects."""
    node_projects = tuple(
        project
        for project in facts.projects
        if project.kind is soleaux.catalog.contracts.ProjectKind.NODE
    )
    projects_by_name = {
        project.name: project for project in node_projects if project.name is not None
    }
    selected: set[str] = set()
    for project in node_projects:
        if project_ids and project.project_id not in project_ids:
            continue
        owned_roots = _dependency_project_closure(project, facts, projects_by_name)
        own_paths, required_paths = _request_paths_for_project(
            project,
            owned_roots,
            facts,
            node_projects,
            paths,
        )
        if own_paths:
            selected.update(required_paths)
    return tuple(sorted(selected))


def _request_paths_for_project(
    project: soleaux.catalog.contracts.ProjectFact,
    owned_roots: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    facts: soleaux.catalog.contracts.CatalogFacts,
    node_projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    paths: tuple[str, ...],
) -> tuple[tuple[str, ...], set[str]]:
    owned_project_ids = {item.project_id for item in owned_roots}
    own_paths = tuple(
        path
        for path in sorted(paths)
        if _project_for_path(node_projects, path) == project
        and path.endswith(_TYPESCRIPT_SOURCE_SUFFIXES)
    )
    required_paths = set(own_paths)
    config = next(
        (
            row
            for row in facts.configs
            if row.project_id == project.project_id and row.config_kind == "typescript"
        ),
        None,
    )
    if config is not None:
        required_paths.update(config.closure_paths)
    for path in sorted(paths):
        owner = _project_for_path(node_projects, path)
        if owner is None or owner.project_id not in owned_project_ids:
            continue
        if path.endswith((*_TYPESCRIPT_SOURCE_SUFFIXES, ".json")):
            required_paths.add(path)
    return own_paths, required_paths


def _dependency_project_closure(
    project: soleaux.catalog.contracts.ProjectFact,
    facts: soleaux.catalog.contracts.CatalogFacts,
    projects_by_name: dict[str, soleaux.catalog.contracts.ProjectFact],
) -> tuple[soleaux.catalog.contracts.ProjectFact, ...]:
    selected: dict[str, soleaux.catalog.contracts.ProjectFact] = {project.project_id: project}
    pending = [project]
    while pending and len(selected) < 64:
        current = pending.pop()
        for dependency in facts.dependencies:
            if (
                dependency.project_id != current.project_id
                or dependency.usage is not soleaux.catalog.contracts.DependencyUsage.DECLARED
            ):
                continue
            resolved = projects_by_name.get(dependency.package_name)
            if resolved is None or resolved.project_id in selected:
                continue
            selected[resolved.project_id] = resolved
            pending.append(resolved)
    return tuple(sorted(selected.values(), key=lambda item: item.project_id))


def _project_for_path(
    projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    path: str,
) -> soleaux.catalog.contracts.ProjectFact | None:
    candidates = [
        project
        for project in projects
        if not project.root_path or path.startswith(f"{project.root_path.rstrip('/')}/")
    ]
    return max(candidates, key=lambda item: len(item.root_path), default=None)


def merge_typescript_dependencies(
    facts: soleaux.catalog.contracts.CatalogFacts,
    request: soleaux.typescript.contracts.TypeScriptAnalysisRequest,
    analysis: soleaux.typescript.contracts.TypeScriptAnalysis,
) -> soleaux.catalog.contracts.CatalogFacts:
    """Add byte-bound direct import/dynamic-load facts from ts-morph results."""
    declared = {
        dependency.package_name: dependency
        for dependency in facts.dependencies
        if dependency.project_id == request.project_id
        and dependency.usage is soleaux.catalog.contracts.DependencyUsage.DECLARED
    }
    source_digests = {source.path: source.digest for source in request.sources}
    additions: list[soleaux.catalog.contracts.DependencyFact] = []
    seen = {
        (dependency.source_path, dependency.package_name, dependency.usage)
        for dependency in facts.dependencies
        if dependency.project_id == request.project_id
        and dependency.usage
        in {
            soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT,
            soleaux.catalog.contracts.DependencyUsage.DYNAMIC_LOAD,
        }
    }
    for imported in analysis.ts_morph.imports:
        if imported.path not in source_digests:
            continue
        package_name = _package_name(imported.specifier)
        if package_name is None:
            continue
        declaration = declared.get(package_name)
        usage = (
            soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT
            if imported.usage == "direct_import"
            else soleaux.catalog.contracts.DependencyUsage.DYNAMIC_LOAD
        )
        identity = (imported.path, package_name, usage)
        if identity in seen:
            continue
        seen.add(identity)
        additions.append(
            soleaux.catalog.contracts.DependencyFact(
                workspace_id=request.workspace_id,
                source_path=imported.path,
                source_digest=source_digests[imported.path],
                producer="ts-morph",
                producer_version=analysis.ts_morph.identity.package_version,
                project_id=request.project_id,
                package_name=package_name,
                declared_specifier=(
                    declaration.declared_specifier
                    if declaration is not None
                    else imported.specifier
                ),
                resolved_specifier=(
                    declaration.resolved_specifier if declaration is not None else None
                ),
                scope=(
                    declaration.scope
                    if declaration is not None
                    else soleaux.catalog.contracts.DependencyScope.RUNTIME
                ),
                usage=usage,
                direct=True,
            )
        )
    return facts.model_copy(
        update={
            "dependencies": tuple(
                sorted(
                    (*facts.dependencies, *additions),
                    key=lambda item: (
                        item.project_id,
                        item.package_name,
                        item.usage,
                        item.source_path,
                    ),
                )
            )
        }
    )


def merge_typescript_analysis(
    facts: soleaux.catalog.contracts.CatalogFacts,
    request: soleaux.typescript.contracts.TypeScriptAnalysisRequest,
    analysis: soleaux.typescript.contracts.TypeScriptAnalysis,
) -> soleaux.catalog.contracts.CatalogFacts:
    """Merge one dual-engine result and its explicit project route."""
    merged = merge_typescript_dependencies(facts, request, analysis)
    merged = _merge_typescript_semantic_facts(merged, request, analysis)
    source = next(
        (item for item in request.sources if item.path == request.config_path),
        request.sources[0],
    )
    engines = tuple(
        engine
        for engine in merged.engines
        if not (engine.project_id == request.project_id and engine.coverage == "loaded")
    )
    loaded_engines = (
        *_engine_facts(request, source, analysis.ts_morph),
        *_engine_facts(request, source, analysis.native),
    )
    previous_route = next(
        (route for route in merged.typescript_routes if route.project_id == request.project_id),
        None,
    )
    ts_options = analysis.ts_morph.compiler_options
    route = soleaux.catalog.contracts.TypeScriptRouteFact(
        workspace_id=request.workspace_id,
        source_path=source.path,
        source_digest=source.digest,
        producer="soleaux-typescript-worker",
        producer_version=analysis.protocol_version,
        project_id=request.project_id,
        config_path=request.config_path,
        root_files=analysis.ts_morph.root_files,
        config_closure=(previous_route.config_closure if previous_route is not None else ()),
        libraries=_string_tuple(ts_options.get("lib")),
        ambient_types=(
            _string_tuple(ts_options.get("types"))
            or (previous_route.ambient_types if previous_route is not None else ())
        ),
        module_resolution=_optional_text(ts_options.get("moduleResolution")),
        ts_morph_engine_id=f"loaded:{request.project_id}:ts-morph:api",
        native_engine_id=f"loaded:{request.project_id}:typescript-native:api",
        lsp_engine_id=(previous_route.lsp_engine_id if previous_route is not None else None),
        typecheck_engine_id=(
            previous_route.typecheck_engine_id if previous_route is not None else None
        ),
        typecheck_script=(previous_route.typecheck_script if previous_route is not None else None),
        typecheck_command=(
            previous_route.typecheck_command if previous_route is not None else None
        ),
        prerequisites=(previous_route.prerequisites if previous_route is not None else ()),
        parity_status=analysis.parity.status,
        parity_config_status=analysis.parity.config.status,
        parity_roots_status=analysis.parity.roots.status,
        parity_resolution_status=analysis.parity.resolution.status,
        parity_diagnostics_status=analysis.parity.diagnostics.status,
        complete=not analysis.warnings,
        omitted_reasons=analysis.warnings,
    )
    return merged.model_copy(
        update={
            "engines": tuple(
                sorted(
                    (*engines, *loaded_engines),
                    key=lambda item: (item.project_id, item.engine_id),
                )
            ),
            "typescript_routes": tuple(
                sorted(
                    (
                        *(
                            existing
                            for existing in merged.typescript_routes
                            if existing.project_id != request.project_id
                        ),
                        route,
                    ),
                    key=lambda item: item.project_id,
                )
            ),
        }
    )


def _merge_typescript_semantic_facts(
    facts: soleaux.catalog.contracts.CatalogFacts,
    request: soleaux.typescript.contracts.TypeScriptAnalysisRequest,
    analysis: soleaux.typescript.contracts.TypeScriptAnalysis,
) -> soleaux.catalog.contracts.CatalogFacts:
    source_by_path = {source.path: source for source in request.sources}
    retained_symbols = tuple(
        symbol for symbol in facts.symbols if symbol.project_id != request.project_id
    )
    retained_imports = tuple(
        imported for imported in facts.imports if imported.project_id != request.project_id
    )
    retained_diagnostics = tuple(
        diagnostic
        for diagnostic in facts.diagnostics
        if diagnostic.project_id != request.project_id
    )
    symbols: list[soleaux.catalog.contracts.SymbolFact] = []
    diagnostics: list[soleaux.catalog.contracts.DiagnosticFact] = []
    for engine in (analysis.ts_morph, analysis.native):
        engine_id = f"loaded:{request.project_id}:{engine.identity.engine}:api"
        for symbol in engine.symbols:
            source = source_by_path.get(symbol.path)
            if source is None:
                continue
            byte_start, byte_end = _byte_bounds(
                source.text,
                symbol.start,
                symbol.end,
                byte_start=symbol.byte_start,
                byte_end=symbol.byte_end,
            )
            logical_identity = (
                f"{request.workspace_id}\0{request.project_id}\0{symbol.path}\0"
                f"{symbol.name}\0{byte_start}\0{byte_end}"
            ).encode()
            symbol_id = soleaux.contracts.repository.content_digest(logical_identity)
            revision_id = soleaux.contracts.repository.content_digest(
                (
                    f"{symbol_id}\0{source.digest}\0{engine_id}\0"
                    f"{symbol.kind}\0{symbol.type_text or ''}"
                ).encode()
            )
            symbols.append(
                soleaux.catalog.contracts.SymbolFact(
                    workspace_id=request.workspace_id,
                    source_path=symbol.path,
                    source_digest=source.digest,
                    producer=engine.identity.engine,
                    producer_version=engine.identity.runtime_version,
                    symbol_id=symbol_id,
                    revision_id=revision_id,
                    project_id=request.project_id,
                    path=symbol.path,
                    name=symbol.name,
                    symbol_kind=symbol.kind,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    exported=symbol.exported,
                    type_text=symbol.type_text,
                    documentation=symbol.documentation,
                    signatures=symbol.signatures,
                    declarations=_semantic_locations(
                        symbol.declarations,
                        source_by_path,
                    ),
                    definitions=_semantic_locations(
                        symbol.definitions,
                        source_by_path,
                    ),
                    implementations=_semantic_locations(
                        symbol.implementations,
                        source_by_path,
                    ),
                    references=_semantic_locations(
                        symbol.references,
                        source_by_path,
                    ),
                    calls=_semantic_calls(
                        tuple(
                            call
                            for call in engine.calls
                            if _callee_name(call.callee) == symbol.name
                        ),
                        source_by_path,
                    ),
                    assignable_to_self=symbol.assignable_to_self,
                    engine_id=engine_id,
                    coverage="semantic",
                )
            )
        for diagnostic in engine.diagnostics:
            path = diagnostic.path or request.config_path or request.sources[0].path
            source = source_by_path.get(path)
            if source is None:
                continue
            start = diagnostic.start or 0
            end = start + (diagnostic.length or 0)
            byte_start, byte_end = _byte_bounds(
                source.text,
                start,
                end,
                byte_start=diagnostic.byte_start,
                byte_end=diagnostic.byte_end,
            )
            identity = (
                f"{request.workspace_id}\0{request.project_id}\0{engine_id}\0{path}\0"
                f"{diagnostic.category}\0{diagnostic.code}\0{byte_start}\0{byte_end}\0"
                f"{diagnostic.message}"
            ).encode()
            diagnostics.append(
                soleaux.catalog.contracts.DiagnosticFact(
                    workspace_id=request.workspace_id,
                    source_path=path,
                    source_digest=source.digest,
                    producer=engine.identity.engine,
                    producer_version=engine.identity.runtime_version,
                    diagnostic_id=soleaux.contracts.repository.content_digest(identity),
                    project_id=request.project_id,
                    path=path,
                    engine_id=engine_id,
                    category=diagnostic.category,
                    code=str(diagnostic.code) if diagnostic.code is not None else None,
                    message=diagnostic.message,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    coverage="semantic",
                )
            )

    imports: list[soleaux.catalog.contracts.ImportFact] = []
    for imported in analysis.ts_morph.imports:
        source = source_by_path.get(imported.path)
        if source is None:
            continue
        usage = (
            soleaux.catalog.contracts.DependencyUsage.DIRECT_IMPORT
            if imported.usage == "direct_import"
            else soleaux.catalog.contracts.DependencyUsage.DYNAMIC_LOAD
        )
        identity = (
            f"{request.workspace_id}\0{request.project_id}\0{imported.path}\0"
            f"{imported.specifier}\0{imported.resolved_path or ''}\0{usage.value}"
        ).encode()
        imports.append(
            soleaux.catalog.contracts.ImportFact(
                workspace_id=request.workspace_id,
                source_path=imported.path,
                source_digest=source.digest,
                producer="ts-morph",
                producer_version=analysis.ts_morph.identity.runtime_version,
                import_id=soleaux.contracts.repository.content_digest(identity),
                project_id=request.project_id,
                path=imported.path,
                specifier=imported.specifier,
                resolved_path=imported.resolved_path,
                usage=usage,
                is_type_only=imported.is_type_only,
                engine_id=f"loaded:{request.project_id}:ts-morph:api",
            )
        )
    return facts.model_copy(
        update={
            "symbols": tuple(
                sorted(
                    (*retained_symbols, *symbols),
                    key=lambda item: (
                        item.project_id,
                        item.path,
                        item.byte_start,
                        item.engine_id,
                    ),
                )
            ),
            "imports": tuple(
                sorted(
                    (*retained_imports, *imports),
                    key=lambda item: (
                        item.project_id,
                        item.path,
                        item.specifier,
                        item.usage,
                    ),
                )
            ),
            "diagnostics": tuple(
                sorted(
                    (*retained_diagnostics, *diagnostics),
                    key=lambda item: (
                        item.project_id,
                        item.path,
                        item.byte_start,
                        item.engine_id,
                        item.diagnostic_id,
                    ),
                )
            ),
        }
    )


def _semantic_locations(
    locations: tuple[soleaux.typescript.contracts.TypeScriptLocation, ...],
    source_by_path: dict[str, soleaux.typescript.contracts.TypeScriptSource],
) -> tuple[soleaux.catalog.contracts.SemanticLocation, ...]:
    normalized: list[soleaux.catalog.contracts.SemanticLocation] = []
    for location in locations:
        source = source_by_path.get(location.path)
        if source is None:
            continue
        byte_start, byte_end = _byte_bounds(
            source.text,
            location.start,
            location.end,
            byte_start=location.byte_start,
            byte_end=location.byte_end,
        )
        normalized.append(
            soleaux.catalog.contracts.SemanticLocation(
                path=location.path,
                byte_start=byte_start,
                byte_end=byte_end,
                kind=location.kind,
                name=location.name,
            )
        )
    return tuple(normalized)


def _semantic_calls(
    calls: tuple[soleaux.typescript.contracts.TypeScriptCall, ...],
    source_by_path: dict[str, soleaux.typescript.contracts.TypeScriptSource],
) -> tuple[soleaux.catalog.contracts.SemanticCallSite, ...]:
    normalized: list[soleaux.catalog.contracts.SemanticCallSite] = []
    for call in calls:
        source = source_by_path.get(call.path)
        if source is None:
            continue
        byte_start, byte_end = _byte_bounds(
            source.text,
            call.start,
            call.end,
            byte_start=call.byte_start,
            byte_end=call.byte_end,
        )
        normalized.append(
            soleaux.catalog.contracts.SemanticCallSite(
                path=call.path,
                byte_start=byte_start,
                byte_end=byte_end,
                callee=call.callee,
                signature_text=call.signature_text,
                return_type_text=call.return_type_text,
            )
        )
    return tuple(normalized)


def _callee_name(callee: str) -> str:
    return callee.rsplit(".", maxsplit=1)[-1]


def _byte_bounds(
    text: str,
    start: int,
    end: int,
    *,
    byte_start: int | None,
    byte_end: int | None,
) -> tuple[int, int]:
    if byte_start is not None and byte_end is not None:
        return byte_start, byte_end
    encoded = text.encode("utf-16-le")
    start_text = encoded[: start * 2].decode("utf-16-le", errors="ignore")
    end_text = encoded[: end * 2].decode("utf-16-le", errors="ignore")
    return len(start_text.encode()), len(end_text.encode())


def _engine_facts(
    request: soleaux.typescript.contracts.TypeScriptAnalysisRequest,
    source: soleaux.typescript.contracts.TypeScriptSource,
    engine: soleaux.typescript.contracts.EngineAnalysis,
) -> tuple[soleaux.catalog.contracts.EngineFact, ...]:
    identity = engine.identity
    capabilities = tuple(sorted(engine.coverage))

    def engine_fact(
        role: soleaux.catalog.contracts.EngineRole, suffix: str
    ) -> soleaux.catalog.contracts.EngineFact:
        return soleaux.catalog.contracts.EngineFact(
            workspace_id=request.workspace_id,
            source_path=source.path,
            source_digest=source.digest,
            producer="soleaux-typescript-worker",
            producer_version=identity.protocol_version,
            project_id=request.project_id,
            engine_id=f"loaded:{request.project_id}:{identity.engine}:{suffix}",
            role=role,
            package_name=identity.package_name,
            package_version=identity.package_version,
            runtime_version=identity.runtime_version,
            api_entrypoint=identity.api_entrypoint,
            binary_version=identity.binary_version,
            protocol_version=identity.protocol_version,
            capabilities=capabilities,
            available=True,
            coverage="loaded",
        )

    return (
        engine_fact(soleaux.catalog.contracts.EngineRole.PACKAGE, "package"),
        engine_fact(soleaux.catalog.contracts.EngineRole.LOADED_COMPILER, "compiler"),
        engine_fact(soleaux.catalog.contracts.EngineRole.API, "api"),
        *(
            (engine_fact(soleaux.catalog.contracts.EngineRole.BINARY, "binary"),)
            if identity.binary_version is not None
            else ()
        ),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not _is_object_list(value):
        return ()
    return tuple(str(item) for item in value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _package_name(specifier: str) -> str | None:
    if specifier.startswith((".", "/", "#")):
        return None
    parts = specifier.split("/")
    if specifier.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else None
