"""Project, dependency, script, and config facts from captured manifests."""

from datetime import UTC, datetime
from pathlib import Path

import _host_root

from soleaux.analysis.service import SoleauxService
from soleaux.catalog.contracts import (
    DependencyFact,
    DependencyScope,
    DependencyUsage,
    EngineRole,
    ProjectKind,
)
from soleaux.catalog.projects import ProjectCatalogExtractor
from soleaux.catalog.structural import ExtractedFile, merge_structural_facts
from soleaux.catalog.typescript import build_typescript_requests, typescript_request_paths
from soleaux.contracts.repository import content_digest
from soleaux.contracts.requests import SearchKind, SearchRequest
from soleaux.contracts.snapshot import CapturedFile, ClaimBasis, RepositorySnapshot
from soleaux.structural.fragments import LIBCST_ANALYZER_ID
from soleaux.structural.python import extract_python
from soleaux.structural.snapshot import SnapshotBundle


def _bundle(contents: dict[str, bytes], *, root: Path = Path("/workspace")) -> SnapshotBundle:
    files = tuple(
        CapturedFile(
            workspace_id="main",
            path=path,
            content_hash=content_digest(content),
            byte_start=0,
            byte_end=len(content),
            start_line=0,
            start_column=0,
            end_line=content.count(b"\n"),
            end_column=0,
            producer_id="test",
            producer_version="1",
            producer_config_digest="d" * 64,
            claim_basis=ClaimBasis.MANIFEST,
        )
        for path, content in sorted(contents.items())
    )
    return SnapshotBundle(
        snapshot=RepositorySnapshot(
            snapshot_id="main:test",
            workspace_id="main",
            root=str(root),
            created_at=datetime.now(UTC),
            files=files,
            source_fingerprint="f" * 64,
        ),
        contents=contents,
        notes=(),
    )


def test_project_extractor_resolves_pnpm_catalogs_and_config_closures() -> None:
    bundle = _bundle(
        {
            "package.json": b"""{
              "name": "root",
              "private": true,
              "devDependencies": {
                "@typescript/native": "catalog:",
                "ts-morph": "catalog:",
                "typescript": "catalog:typescript-api"
              },
              "scripts": {"typecheck": "next typegen && turbo run typecheck"}
            }""",
            "packages/app/package.json": b"""{
              "name": "@fixture/app",
              "dependencies": {"next": "catalog:", "react": "catalog:"},
              "devDependencies": {"typescript": "catalog:typescript-api"}
            }""",
            "packages/app/tsconfig.json": b'{"extends":"../../tsconfig.json"}',
            "pnpm-workspace.yaml": b"""
catalog:
  "@typescript/native": "npm:typescript@7.0.2"
  next: "16.0.0"
  react: "19.0.0"
  ts-morph: "28.0.0"
catalogs:
  typescript-api:
    typescript: "npm:@typescript/typescript6@6.0.2"
""",
            "tsconfig.json": b'{"compilerOptions":{"strict":true}}',
        }
    )

    facts = ProjectCatalogExtractor().extract(bundle)

    assert [(project.project_id, project.kind) for project in facts.projects] == [
        ("main:node:.", ProjectKind.NODE),
        ("main:node:packages/app", ProjectKind.NODE),
    ]
    dependencies = {(item.project_id, item.package_name): item for item in facts.dependencies}
    assert dependencies[("main:node:.", "@typescript/native")].resolved_specifier == (
        "npm:typescript@7.0.2"
    )
    assert dependencies[("main:node:.", "ts-morph")].resolved_specifier == "28.0.0"
    assert dependencies[("main:node:.", "typescript")].resolved_specifier == (
        "npm:@typescript/typescript6@6.0.2"
    )
    assert dependencies[("main:node:packages/app", "next")].scope is DependencyScope.RUNTIME
    assert facts.projects[1].framework_ids == ("nextjs", "react")
    assert facts.scripts[0].is_typecheck is True
    assert facts.scripts[0].prerequisites == ("next typegen",)
    assert facts.scripts[0].task_ids == ()
    assert facts.tasks == ()
    app_config = next(
        config for config in facts.configs if config.config_path == "packages/app/tsconfig.json"
    )
    assert app_config.closure_paths == (
        "packages/app/tsconfig.json",
        "tsconfig.json",
    )
    root_route = next(
        route for route in facts.typescript_routes if route.project_id == "main:node:."
    )
    assert root_route.typecheck_command == "next typegen && turbo run typecheck"
    assert root_route.prerequisites == ("next typegen",)
    assert root_route.parity_status == "not_run"
    assert any(
        engine.role is EngineRole.TYPECHECK and engine.project_id == "main:node:."
        for engine in facts.engines
    )


def test_turbo_task_graph_extraction_and_script_task_ids() -> None:
    bundle = _bundle(
        {
            "package.json": b"""{
              "name": "root",
              "private": true,
              "scripts": {
                "build": "turbo run build",
                "check": "pnpm exec turbo typecheck --filter=@fixture/app",
                "dev": "turbo",
                "latest": "npx turbo@latest build",
                "scoped": "turbo run @fixture/app#build"
              }
            }""",
            "turbo.json": b"""{
              "tasks": {
                "build": {"dependsOn": ["^build"], "outputs": [".next/**"]},
                "typecheck": {"dependsOn": ["build"]},
                "dev": {"cache": false, "persistent": true}
              }
            }""",
            "packages/app/package.json": b"""{
              "name": "@fixture/app",
              "scripts": {"build": "turbo run compile"}
            }""",
            "packages/app/turbo.json": b"""{
              "extends": ["//"],
              "tasks": {
                "compile": {"inputs": ["src/**"]}
              }
            }""",
            "plans/turbo.json": b"""{
              "tasks": {"orphan": {}}
            }""",
        }
    )

    facts = ProjectCatalogExtractor().extract(bundle)

    tasks = {(task.project_id, task.task_id): task for task in facts.tasks}
    root_build = tasks[("main:node:.", "build")]
    assert root_build.runner == "turbo"
    assert root_build.depends_on == ("^build",)
    assert root_build.outputs == (".next/**",)
    assert root_build.extends_root is False
    assert tasks[("main:node:.", "dev")].cache is False
    assert tasks[("main:node:.", "dev")].persistent is True
    app_compile = tasks[("main:node:packages/app", "compile")]
    assert app_compile.inputs == ("src/**",)
    assert app_compile.extends_root is True
    assert ("main:node:.", "orphan") not in tasks
    assert any("plans/turbo.json: no owning package manifest" in note for note in facts.warnings)

    scripts = {(script.project_id, script.name): script for script in facts.scripts}
    assert scripts[("main:node:.", "build")].task_ids == ("build",)
    assert scripts[("main:node:.", "check")].task_ids == ("typecheck",)
    assert scripts[("main:node:.", "dev")].task_ids == ()
    assert scripts[("main:node:.", "latest")].task_ids == ()
    assert scripts[("main:node:.", "scoped")].task_ids == ("@fixture/app#build",)
    assert scripts[("main:node:packages/app", "build")].task_ids == ("compile",)


def test_typescript_request_uses_project_roots_and_workspace_package_aliases() -> None:
    bundle = _bundle(
        {
            "package.json": b"""{
              "name": "root",
              "dependencies": {"@fixture/config": "workspace:*"}
            }""",
            "src/main.ts": b'import type {Config} from "@fixture/config";\n',
            "tsconfig.json": b'{"extends":"@fixture/config/base.json"}',
            "packages/config/package.json": b'{"name":"@fixture/config"}',
            "packages/config/base.json": b'{"compilerOptions":{"strict":true}}',
            "packages/config/index.ts": b"export interface Config { value: string }\n",
        }
    )
    facts = ProjectCatalogExtractor().extract(bundle)

    (request,) = build_typescript_requests(
        facts,
        bundle,
        project_ids=frozenset({"main:node:."}),
    )

    assert request.config_path == "tsconfig.json"
    assert request.root_files == ("src/main.ts",)
    assert request.package_roots == {"@fixture/config": "packages/config"}
    assert {source.path for source in request.sources} >= {
        "src/main.ts",
        "tsconfig.json",
        "packages/config/package.json",
        "packages/config/base.json",
        "packages/config/index.ts",
    }
    assert typescript_request_paths(
        facts,
        tuple(bundle.contents),
        project_ids=frozenset({"main:node:."}),
    ) == tuple(sorted(source.path for source in request.sources))


def test_python_libcst_distinguishes_declared_pyyaml_from_direct_yaml_import() -> None:
    bundle = _bundle(
        {
            "tools/soleaux/pyproject.toml": b"""
[project]
name = "soleaux"
dependencies = ["pyyaml>=6.0.3"]
""",
            "tools/soleaux/src/soleaux/config.py": b"import yaml\n",
        }
    )

    manifest_facts = ProjectCatalogExtractor().extract(bundle)
    path = "tools/soleaux/src/soleaux/config.py"
    content = bundle.contents[path]
    extraction = extract_python(
        content.decode(),
        content,
        path=path,
        projections=("syntax.imports",),
    )
    facts = merge_structural_facts(
        manifest_facts,
        workspace_id="main",
        extracted={
            path: ExtractedFile(
                digest=content_digest(content),
                language="Python",
                fragments=extraction.fragments,
            )
        },
    )

    pyyaml = [
        dependency
        for dependency in facts.dependencies
        if dependency.package_name.casefold() == "pyyaml"
    ]
    assert {dependency.usage for dependency in pyyaml} == {
        DependencyUsage.DECLARED,
        DependencyUsage.DIRECT_IMPORT,
    }
    direct = next(
        dependency for dependency in pyyaml if dependency.usage is DependencyUsage.DIRECT_IMPORT
    )
    assert direct.source_path == "tools/soleaux/src/soleaux/config.py"
    assert direct.producer == "soleaux-structural-catalog"
    assert facts.imports[0].engine_id == LIBCST_ANALYZER_ID


def test_dogfood_manifests_prove_original_dependency_and_compiler_facts() -> None:
    repository_root = _host_root.require_host_root()
    selected_paths = (
        "package.json",
        "packages/auth/package.json",
        "packages/next-config/package.json",
        "pnpm-workspace.yaml",
        "tools/soleaux/pyproject.toml",
    )
    bundle = _bundle(
        {path: (repository_root / path).read_bytes() for path in selected_paths},
        root=repository_root,
    )

    facts = ProjectCatalogExtractor().extract(bundle)
    by_package: dict[str, list[DependencyFact]] = {}
    for dependency in facts.dependencies:
        by_package.setdefault(dependency.package_name.casefold(), []).append(dependency)

    assert any(item.project_id == "main:python:tools/soleaux" for item in by_package["pyyaml"])
    assert any(item.project_id == "main:node:." for item in by_package["yaml"])
    assert any(item.project_id == "main:node:." for item in by_package["@libpg-query/parser"])
    assert any(
        item.project_id == "main:node:." and item.resolved_specifier == "28.0.0"
        for item in by_package["ts-morph"]
    )
    assert {item.project_id for item in by_package["@typescript/native"]} >= {
        "main:node:.",
        "main:node:packages/auth",
        "main:node:packages/next-config",
    }
    assert {
        item.resolved_specifier
        for item in by_package["typescript"]
        if item.project_id
        in {
            "main:node:.",
            "main:node:packages/auth",
            "main:node:packages/next-config",
        }
    } == {"npm:@typescript/typescript6@6.0.2"}


async def test_search_returns_hydrated_project_dependency_and_script_facts(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"fixture","dependencies":{"yaml":"2.8.0"},"scripts":{"typecheck":"tsc --noEmit"}}',
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text("export const value = 1;\n", encoding="utf-8")

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        workspace_id = service.workspace_ids[0]
        project_id = f"{workspace_id}:node:."
        dependencies = await service.search(
            SearchRequest(query="yaml", kinds=[SearchKind.DEPENDENCY])
        )
        projects = await service.search(SearchRequest(query="fixture", kinds=[SearchKind.PROJECT]))
        scripts = await service.search(SearchRequest(query="typecheck", kinds=[SearchKind.SCRIPT]))

    assert dependencies.rows
    dependency = dependencies.rows[0]
    assert dependency["key"] == f"dependency:{project_id}:yaml"
    assert dependency["package_name"] == "yaml"
    assert dependency["declared_specifier"] == "2.8.0"
    assert dependency["project_id"] == project_id
    assert projects.rows
    assert projects.rows[0]["key"] == f"project:{project_id}"
    assert projects.rows[0]["project_id"] == project_id
    assert scripts.rows
    assert scripts.rows[0]["key"] == f"script:{project_id}:typecheck"
    assert scripts.rows[0]["command"] == "tsc --noEmit"
    assert scripts.rows[0]["project_id"] == project_id


async def test_route_and_canonical_rule_metadata_are_searchable_and_queryable(
    tmp_path: Path,
) -> None:
    (tmp_path / "app" / "api" / "health").mkdir(parents=True)
    (tmp_path / "tools" / "ast-grep" / "rules").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{"name":"fixture","dependencies":{"next":"16.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "app" / "api" / "health" / "route.ts").write_text(
        "export function GET() { return new Response('ok'); }\n",
        encoding="utf-8",
    )
    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - tools/ast-grep/rules\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "ast-grep" / "rules" / "rtest.yml").write_text(
        (
            "id: rtest\n"
            "language: TypeScript\n"
            "severity: error\n"
            "message: RTEST canonical rule\n"
            "rule:\n"
            "  pattern: console.log($A)\n"
        ),
        encoding="utf-8",
    )

    async with SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        searched = await service.search(SearchRequest(query="RTEST"))
        routed = await service.search(SearchRequest(query="health", kinds=[SearchKind.ROUTE]))

    assert searched.error is None
    assert searched.rows is not None
    rule = next(row for row in searched.rows if row["key"] == "rule:rtest")
    assert rule["rule_id"] == "rtest"
    assert rule["packaged"] is False
    assert routed.rows
    assert routed.rows[0]["route"] == "/api/health"

    facts = ProjectCatalogExtractor().extract(
        _bundle(
            {
                path.relative_to(tmp_path).as_posix(): path.read_bytes()
                for path in tmp_path.rglob("*")
                if path.is_file()
            },
            root=tmp_path,
        )
    )
    (route,) = facts.routes
    assert route.route == "/api/health"
    assert route.registration_kind == "route_handler"
