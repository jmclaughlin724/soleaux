"""Managed ts-morph 28 / native TypeScript 7 worker contracts."""

import pathlib
import subprocess
from typing import cast

import _assertions
import _host_root
import pytest

import soleaux.analysis.frame
import soleaux.analysis.service
import soleaux.catalog.contracts
import soleaux.catalog.indexer
import soleaux.catalog.store
import soleaux.catalog.typescript
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.workspace
import soleaux.postgresql.runtime
import soleaux.typescript.contracts
import soleaux.typescript.node_runtime


def _source(path: str, text: str) -> soleaux.typescript.contracts.TypeScriptSource:
    return soleaux.typescript.contracts.TypeScriptSource(
        path=path,
        text=text,
        digest=soleaux.contracts.repository.content_digest(text.encode("utf-8")),
    )


def _metadata_object(value: object) -> dict[str, object]:
    return _assertions.object_mapping(value)


def _metadata_int(metadata: dict[str, object], key: str) -> int:
    value = metadata[key]
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _development_installation():
    repository_root = _host_root.require_host_root()
    installation = soleaux.typescript.node_runtime.resolve_typescript_installation(repository_root)
    assert installation is not None
    return installation


def _runtime_manifest(path: pathlib.Path, *, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"name":"{name}","version":"{version}"}}\n',
        encoding="utf-8",
    )


def test_runtime_discovery_is_exact_read_only_and_mismatch_safe(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "runtime"
    monkeypatch.setenv("SOLEAUX_TYPESCRIPT_RUNTIME", str(prefix))
    assert soleaux.typescript.node_runtime.resolve_typescript_installation() is None
    assert not prefix.exists()

    _runtime_manifest(
        prefix / "node_modules" / "ts-morph" / "package.json",
        name="ts-morph",
        version=soleaux.typescript.contracts.TS_MORPH_VERSION,
    )
    _runtime_manifest(
        prefix / "node_modules" / "@typescript" / "native" / "package.json",
        name="typescript",
        version="7.0.1",
    )
    assert soleaux.typescript.node_runtime.resolve_typescript_installation() is None


def test_runtime_provisioning_installs_only_the_two_exact_packages(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "runtime"
    observed: list[list[str]] = []
    observed_options: list[dict[str, object]] = []
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-propagate")

    def fake_run(
        command: list[str],
        **options: object,
    ) -> subprocess.CompletedProcess[str]:
        observed.append(command)
        observed_options.append(options)
        _runtime_manifest(
            prefix / "node_modules" / "ts-morph" / "package.json",
            name="ts-morph",
            version=soleaux.typescript.contracts.TS_MORPH_VERSION,
        )
        _runtime_manifest(
            prefix / "node_modules" / "@typescript" / "native" / "package.json",
            name="typescript",
            version=soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION,
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    installation = soleaux.typescript.node_runtime.provision_typescript_runtime(
        prefix, npm_executable="npm"
    )

    assert installation.prefix == prefix
    assert observed == [
        [
            "npm",
            "install",
            "--prefix",
            str(prefix),
            "--no-package-lock",
            "--no-save",
            "--no-audit",
            "--no-fund",
            f"ts-morph@{soleaux.typescript.contracts.TS_MORPH_VERSION}",
            f"@typescript/native@npm:typescript@{soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION}",
        ]
    ]
    environment = observed_options[0]["env"]
    assert isinstance(environment, dict)
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {},
        environment_names=(),
    )
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment


def test_runtime_provisioning_refuses_unowned_directory(tmp_path: pathlib.Path) -> None:
    prefix = tmp_path / "runtime"
    prefix.mkdir()
    (prefix / "user-content.txt").write_text("protected\n", encoding="utf-8")

    with _assertions.raises_with_message(
        soleaux.typescript.node_runtime.TypeScriptRuntimeUnavailableError,
        r"nonempty directory without package.json",
    ):
        soleaux.typescript.node_runtime.provision_typescript_runtime(prefix)

    assert (prefix / "user-content.txt").read_text(encoding="utf-8") == "protected\n"


def test_runtime_provisioning_reports_offline_failure_without_echoing_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "runtime"
    secret_output = "registry token=do-not-echo"

    def offline(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", secret_output)

    monkeypatch.setattr(subprocess, "run", offline)

    with pytest.raises(
        soleaux.typescript.node_runtime.TypeScriptRuntimeUnavailableError
    ) as excinfo:
        soleaux.typescript.node_runtime.provision_typescript_runtime(prefix)

    assert "exit 1" in str(excinfo.value)
    assert secret_output not in str(excinfo.value)


def test_runtime_capabilities_report_separate_exact_identities() -> None:
    with soleaux.typescript.node_runtime.TypeScriptNodeRuntime(
        _development_installation()
    ) as runtime:
        assert runtime.started is False
        capabilities = runtime.capabilities()
        assert runtime.started is True

    typed_identities = _assertions.object_mapping(capabilities["identities"])
    typed_ts_morph = _assertions.object_mapping(typed_identities["ts_morph"])
    typed_native = _assertions.object_mapping(typed_identities["native"])
    assert typed_ts_morph["package_version"] == soleaux.typescript.contracts.TS_MORPH_VERSION
    assert (
        typed_ts_morph["runtime_version"]
        == soleaux.typescript.contracts.TS_MORPH_TYPESCRIPT_VERSION
    )
    assert typed_native["package_version"] == soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION
    assert typed_native["api_entrypoint"] == "@typescript/native/unstable/async"
    capability_evidence = _assertions.object_mapping(capabilities["capability_evidence"])
    assert _metadata_int(capability_evidence, "native_scanner_tokens") > 0
    assert _metadata_int(capability_evidence, "native_visitor_nodes") == 1
    assert capability_evidence["native_factory_identifier"] == "soleauxCapabilityProbe"
    assert (
        capability_evidence["ts_morph_compiler_version"]
        == soleaux.typescript.contracts.TS_MORPH_TYPESCRIPT_VERSION
    )


def test_dual_engine_analysis_uses_virtual_sources_and_disposable_preview() -> None:
    main_text = 'import {value} from "./dep";\nexport const result:string=value;\n'
    request = soleaux.typescript.contracts.TypeScriptAnalysisRequest(
        workspace_id="main",
        project_id="main:fixture",
        config_path="tsconfig.json",
        root_files=("main.ts", "dep.ts", "runtime.cjs"),
        sources=(
            _source(
                "tsconfig.json",
                '{"compilerOptions":{"strict":true,"module":"nodenext","allowJs":true},'
                '"files":["main.ts","dep.ts","runtime.cjs"]}',
            ),
            _source("dep.ts", "export const value = 1;\n"),
            _source("main.ts", main_text),
            _source(
                "runtime.cjs",
                'const parser = require("@libpg-query/parser");\nmodule.exports = parser;\n',
            ),
        ),
        include_references=True,
        include_emit=True,
        preview=soleaux.typescript.contracts.TypeScriptPreviewRequest(format=True),
    )

    with soleaux.typescript.node_runtime.TypeScriptNodeRuntime(
        _development_installation()
    ) as runtime:
        result = runtime.analyze(request)

    assert result.ts_morph.identity.package_version == soleaux.typescript.contracts.TS_MORPH_VERSION
    assert (
        result.ts_morph.identity.runtime_version
        == soleaux.typescript.contracts.TS_MORPH_TYPESCRIPT_VERSION
    )
    assert (
        result.native.identity.package_version
        == soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION
    )
    assert set(result.ts_morph.root_files) == {"main.ts", "dep.ts", "runtime.cjs"}
    assert set(result.native.root_files) == {"main.ts", "dep.ts", "runtime.cjs"}
    assert any(item.specifier == "./dep" for item in result.ts_morph.imports)
    assert any(
        item.specifier == "@libpg-query/parser" and item.usage == "dynamic_load"
        for item in result.ts_morph.imports
    )
    assert any(item.name == "result" for item in result.ts_morph.symbols)
    assert all(
        item.byte_start is not None and item.byte_end is not None
        for item in result.ts_morph.symbols
    )
    assert result.ts_morph.emitted_files
    assert result.ts_morph.previewed_files
    assert result.ts_morph.cache["disposable"] is True
    assert result.ts_morph.cache["entries"] == 0
    assert result.native.timing.get("enabled") is True
    assert "diagnostics" in result.native.coverage
    assert result.native.cache["disposable"] is True
    assert result.native.cache["entries"] == 0
    assert result.native.imports
    assert result.native.emitted_files
    scanner = _metadata_object(result.native.capability_evidence["scanner"])
    visitor = _metadata_object(result.native.capability_evidence["visitor"])
    factory = _metadata_object(result.native.capability_evidence["factory"])
    clone = _metadata_object(result.native.capability_evidence["clone"])
    assert _metadata_int(scanner, "token_count") > 0
    assert _metadata_int(visitor, "node_count") > 0
    assert factory["identifier_text"] == "soleauxNativeProbe"
    assert clone["deep_clone_digest"]
    assert result.parity.roots.status == "equal"
    assert result.parity.roots.ts_morph_count == 3
    assert result.parity.roots.native_count == 3
    assert result.parity.config.ts_morph_digest
    assert result.parity.resolution.native_digest
    assert result.parity.diagnostics.status in {"equal", "different"}

    facts = soleaux.catalog.contracts.CatalogFacts(
        dependencies=(
            soleaux.catalog.contracts.DependencyFact(
                workspace_id="main",
                source_path="package.json",
                source_digest=soleaux.contracts.repository.content_digest(b"manifest"),
                producer="fixture",
                producer_version="1",
                project_id="main:fixture",
                package_name="@libpg-query/parser",
                declared_specifier="17.6.10",
                resolved_specifier="17.6.10",
                scope=soleaux.catalog.contracts.DependencyScope.DEVELOPMENT,
            ),
        )
    )
    merged = soleaux.catalog.typescript.merge_typescript_dependencies(facts, request, result)
    libpg = [
        dependency
        for dependency in merged.dependencies
        if dependency.package_name == "@libpg-query/parser"
    ]
    assert {dependency.usage for dependency in libpg} == {
        soleaux.catalog.contracts.DependencyUsage.DECLARED,
        soleaux.catalog.contracts.DependencyUsage.DYNAMIC_LOAD,
    }
    enriched = soleaux.catalog.typescript.merge_typescript_analysis(facts, request, result)
    assert {
        engine.role for engine in enriched.engines if engine.project_id == request.project_id
    } >= {
        soleaux.catalog.contracts.EngineRole.PACKAGE,
        soleaux.catalog.contracts.EngineRole.LOADED_COMPILER,
        soleaux.catalog.contracts.EngineRole.API,
        soleaux.catalog.contracts.EngineRole.BINARY,
    }
    (route,) = enriched.typescript_routes
    assert route.root_files == result.ts_morph.root_files
    assert route.ts_morph_engine_id == "loaded:main:fixture:ts-morph:api"
    assert route.native_engine_id == "loaded:main:fixture:typescript-native:api"
    assert route.parity_status == result.parity.status
    assert route.parity_config_status == result.parity.config.status
    assert route.parity_roots_status == "equal"
    assert route.parity_resolution_status == result.parity.resolution.status
    assert route.parity_diagnostics_status == result.parity.diagnostics.status
    assert any(symbol.name == "result" for symbol in enriched.symbols)
    assert any(imported.specifier == "./dep" for imported in enriched.imports)
    assert enriched.diagnostics


def test_dual_engine_caches_are_bounded_incremental_and_revision_keyed() -> None:
    config = _source(
        "tsconfig.json",
        '{"compilerOptions":{"strict":true},"files":["main.ts"]}',
    )
    main = _source(
        "main.ts",
        "/** Greets a caller. */\n"
        "export function greet(name: string): string { return name; }\n"
        'export const result = greet("Soleaux");\n',
    )
    request = soleaux.typescript.contracts.TypeScriptAnalysisRequest(
        workspace_id="main",
        project_id="main:cache",
        config_path="tsconfig.json",
        root_files=("main.ts",),
        sources=(config, main),
        include_references=True,
        include_emit=True,
    )

    with soleaux.typescript.node_runtime.TypeScriptNodeRuntime(
        _development_installation()
    ) as runtime:
        first = runtime.analyze(request)
        second = runtime.analyze(request)
        changed_text = main.text.replace('"Soleaux"', '"fast"')
        changed = runtime.analyze(
            request.model_copy(
                update={
                    "sources": (
                        config,
                        _source("main.ts", changed_text),
                    )
                }
            )
        )
        unchanged_again = runtime.analyze(
            request.model_copy(
                update={
                    "sources": (
                        config,
                        _source("main.ts", changed_text),
                    )
                }
            )
        )

    assert first.ts_morph.cache["hit"] is False
    assert first.native.cache["hit"] is False
    assert second.ts_morph.cache["hit"] is True
    assert second.native.cache["hit"] is True
    assert second.native.cache["snapshot_id"] == first.native.cache["snapshot_id"]
    assert changed.ts_morph.cache["hit"] is False
    assert changed.native.cache["hit"] is False
    assert changed.native.cache["snapshot_id"] != first.native.cache["snapshot_id"]
    assert unchanged_again.ts_morph.cache["hit"] is True
    assert unchanged_again.native.cache["hit"] is True
    assert unchanged_again.native.cache["snapshot_id"] == changed.native.cache["snapshot_id"]
    assert _metadata_int(first.ts_morph.cache, "entries") <= _metadata_int(
        first.ts_morph.cache, "limit"
    )
    assert _metadata_int(first.native.cache, "entries") <= _metadata_int(
        first.native.cache, "limit"
    )

    greet = next(symbol for symbol in first.ts_morph.symbols if symbol.name == "greet")
    native_greet = next(symbol for symbol in first.native.symbols if symbol.name == "greet")
    assert greet.documentation == "Greets a caller."
    assert greet.signatures == ("(name: string): string",)
    assert greet.definitions
    assert greet.implementations
    assert greet.references
    assert greet.assignable_to_self is True
    assert first.ts_morph.calls[0].callee == "greet"
    assert first.ts_morph.calls[0].return_type_text == "string"
    assert native_greet.documentation == "Greets a caller."
    assert native_greet.signatures
    assert native_greet.definitions
    assert native_greet.references
    assert native_greet.assignable_to_self is True
    assert first.native.calls[0].callee == "greet"
    assert first.native.calls[0].return_type_text == "string"

    merged = soleaux.catalog.typescript.merge_typescript_analysis(
        soleaux.catalog.contracts.CatalogFacts(), request, first
    )
    catalog_greet = next(
        symbol
        for symbol in merged.symbols
        if symbol.name == "greet" and symbol.producer == "ts-morph"
    )
    assert catalog_greet.documentation == "Greets a caller."
    assert catalog_greet.signatures == ("(name: string): string",)
    assert catalog_greet.definitions
    assert catalog_greet.implementations
    assert catalog_greet.references
    assert catalog_greet.calls[0].callee == "greet"
    assert catalog_greet.calls[0].return_type_text == "string"


def test_dual_engine_cache_eviction_is_measured_and_releases_oldest_projects() -> None:
    config = _source(
        "tsconfig.json",
        '{"compilerOptions":{"strict":true},"files":["main.ts"]}',
    )
    main = _source("main.ts", "export const value: number = 1;\n")
    requests = tuple(
        soleaux.typescript.contracts.TypeScriptAnalysisRequest(
            workspace_id="main",
            project_id=f"main:eviction:{index}",
            config_path="tsconfig.json",
            root_files=("main.ts",),
            sources=(config, main),
        )
        for index in range(5)
    )

    with soleaux.typescript.node_runtime.TypeScriptNodeRuntime(
        _development_installation()
    ) as runtime:
        results = tuple(runtime.analyze(request) for request in requests)
        first_again = runtime.analyze(requests[0])

    last = results[-1]
    assert _metadata_int(last.ts_morph.cache, "entries") == 4
    assert _metadata_int(last.ts_morph.cache, "limit") == 4
    assert _metadata_int(last.native.cache, "entries") == 2
    assert _metadata_int(last.native.cache, "limit") == 2
    assert _metadata_int(last.ts_morph.cache, "evictions") >= 1
    assert _metadata_int(last.native.cache, "evictions") >= 3
    assert first_again.ts_morph.cache["hit"] is False
    assert first_again.native.cache["hit"] is False
    assert _metadata_int(first_again.ts_morph.cache, "evictions") >= 2
    assert _metadata_int(first_again.native.cache, "evictions") >= 4
    assert _metadata_int(first_again.ts_morph.cache, "rss_bytes") > 0
    assert _metadata_int(first_again.native.cache, "rss_bytes") > 0


async def test_seeded_query_reads_lifecycle_published_dual_engine_project_route(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _host_root.require_host_root()
    monkeypatch.setenv("SOLEAUX_TYPESCRIPT_RUNTIME", str(repository_root))
    (tmp_path / "package.json").write_text(
        '{"name":"fixture","devDependencies":{"typescript":"6.0.2"},'
        '"scripts":{"typecheck":"tsc --noEmit"}}',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":true},"files":["main.ts"]}',
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text(
        "export const value: string = 'ok';\n",
        encoding="utf-8",
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service.ensure_full_catalog()
        publication_generations: list[int] = []
        original_publish = service._catalog_indexer._publish_build

        async def tracked_publish(
            workspace: soleaux.contracts.workspace.WorkspaceRoot,
            store: soleaux.catalog.store.CatalogStore,
            built: soleaux.analysis.frame.FrameBuild,
            *,
            plan: soleaux.catalog.indexer._PublicationPlan,
            enrichment_complete: bool,
        ) -> soleaux.catalog.indexer.CatalogPublication:
            publication = await original_publish(
                workspace,
                store,
                built,
                plan=plan,
                enrichment_complete=enrichment_complete,
            )
            publication_generations.append(publication.generation)
            return publication

        monkeypatch.setattr(service._catalog_indexer, "_publish_build", tracked_publish)
        project_id = f"{service.workspace_ids[0]}:node:."
        cursor: str | None = None
        rows: list[dict[str, object]] = []
        generations: set[int] = set()
        while True:
            response = await service.query(
                soleaux.contracts.requests.QueryRequest(
                    include_tables=[
                        "repository.engines",
                        "repository.typescript_routes",
                    ],
                    seed_keys=[f"project:{project_id}"],
                    semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                    limit=1,
                    cursor=cursor,
                )
            )
            assert response.status is soleaux.contracts.results.ResultStatus.OK
            assert response.error is None
            assert response.rows is not None
            assert response.data is not None
            rows.extend(response.rows)
            generations.add(int(response.data["generation"]))
            cursor = response.next_cursor
            if cursor is None:
                break

        repeated = await service.query(
            soleaux.contracts.requests.QueryRequest(
                include_tables=[
                    "repository.engines",
                    "repository.typescript_routes",
                ],
                seed_keys=[f"project:{project_id}"],
                semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
                limit=1,
            )
        )

    assert repeated.status is soleaux.contracts.results.ResultStatus.OK
    assert len(generations) == 1
    assert publication_generations == []
    loaded_engines = [
        row for row in rows if row["table"] == "repository.engines" and row["coverage"] == "loaded"
    ]
    assert {str(row["package_name"]) for row in loaded_engines} == {
        "@typescript/native",
        "ts-morph",
    }
    assert {str(row["role"]) for row in loaded_engines} >= {
        "api",
        "loaded_compiler",
        "package",
    }
    route = next(row for row in rows if row["table"] == "repository.typescript_routes")
    assert route["ts_morph_engine_id"] == f"loaded:{project_id}:ts-morph:api"
    assert route["native_engine_id"] == f"loaded:{project_id}:typescript-native:api"
    assert route["parity_roots_status"] == "equal"
    assert route["parity_status"] != "not_run"


async def test_repeated_typescript_runtime_warning_does_not_advance_generation(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"fixture","devDependencies":{"typescript":"6.0.2"}}',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":true},"files":["main.ts"]}',
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text(
        "export const value: string = 'ok';\n",
        encoding="utf-8",
    )
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(tmp_path))],
        config_digest="d" * 64,
    ).get(None)

    class FailingRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def analyze_async(
            self,
            _request: soleaux.typescript.contracts.TypeScriptAnalysisRequest,
        ) -> soleaux.typescript.contracts.TypeScriptAnalysis:
            self.calls += 1
            raise soleaux.typescript.node_runtime.TypeScriptRuntimeError("transient worker failure")

        async def aclose(self) -> None:
            return None

    frames = soleaux.analysis.frame.AnalysisFrameBuilder()
    runtime = FailingRuntime()
    frames._typescript_runtime = cast(
        soleaux.typescript.node_runtime.TypeScriptNodeRuntime,
        runtime,
    )
    try:
        generation, bundle = await frames.base_catalog_bundle(workspace, validate=True)
        project_id = f"{workspace.workspace_id}:node:."

        warned, warned_bundle = await frames.enrich_typescript_catalog(
            workspace,
            generation,
            bundle,
            project_ids=frozenset({project_id}),
        )
        retried, _ = await frames.enrich_typescript_catalog(
            workspace,
            warned,
            warned_bundle,
            project_ids=frozenset({project_id}),
        )
        retried_again, _ = await frames.enrich_typescript_catalog(
            workspace,
            retried,
            warned_bundle,
            project_ids=frozenset({project_id}),
        )

        assert warned.number == generation.number + 1
        assert retried.number == warned.number
        assert retried_again.number == warned.number
        assert runtime.calls == 3
        assert warned.facts.warnings == (f"{project_id}: transient worker failure",)
    finally:
        await frames.aclose()
