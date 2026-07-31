"""D017/D029: structural candidates become source-traceable semantic relations."""

from __future__ import annotations

import json
from pathlib import Path

from _assertions import raises_with_message
from pydantic import JsonValue

from soleaux.contracts.coverage import FrameStatus
from soleaux.contracts.evidence import EvidenceKind, ResolutionStatus
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.lsp.broker import SemanticProviderRequiredError
from soleaux.lsp.contracts import (
    LspCapability,
    LspLocation,
    LspPosition,
    LspRange,
    NavigationRequest,
)
from soleaux.lsp.generation import SemanticGeneration
from soleaux.lsp.operations import SemanticResolution
from soleaux.relations.resolver import RelationResolver
from soleaux.structural.fragments import SyntaxFragment
from soleaux.structural.snapshot import RepositorySnapshotter, SnapshotBundle


async def _capture(tmp_path: Path, files: dict[str, str]) -> SnapshotBundle:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="relations-test",
    ).get("workspace")
    return await RepositorySnapshotter(workspace).capture(scope=tuple(files))


def _fragment(
    *,
    projection: str,
    path: str,
    language: str,
    name: str | None,
    column: int = 0,
    attributes: dict[str, JsonValue] | None = None,
) -> SyntaxFragment:
    return SyntaxFragment(
        projection=projection,
        kind="candidate",
        name=name,
        path=path,
        language=language,
        byte_start=column,
        byte_end=column + 1,
        start_line=0,
        start_column=column,
        end_line=0,
        end_column=column + 1,
        text_preview=name or "dynamic",
        attributes=attributes or {},
    )


async def test_module_relations_resolve_local_barrel_alias_package_python_and_go(
    tmp_path: Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "tsconfig.json": json.dumps(
                {
                    "compilerOptions": {
                        "baseUrl": ".",
                        "paths": {"@/*": ["src/*"]},
                    }
                }
            ),
            "src/main.ts": "export const main = true;\n",
            "src/local.ts": "export const local = true;\n",
            "src/lib/index.ts": "export const barrel = true;\n",
            "src/aliased.ts": "export const aliased = true;\n",
            "packages/core/package.json": json.dumps(
                {
                    "name": "@scope/core",
                    "exports": {
                        ".": "./src/index.ts",
                        "./feature": "./src/feature.ts",
                    },
                }
            ),
            "packages/core/src/index.ts": "export const core = true;\n",
            "packages/core/src/feature.ts": "export const feature = true;\n",
            "packages/empty/package.json": json.dumps(
                {
                    "name": "@scope/empty",
                    "exports": {".": {"browser": None}},
                }
            ),
            "pkg/__init__.py": "",
            "pkg/main.py": "from . import dep\n",
            "pkg/dep.py": "VALUE = 1\n",
            "go.mod": "module example.com/fixture\n\ngo 1.25\n",
            "cmd/main.go": "package main\n",
            "internal/dep/dep.go": "package dep\n",
            "script.rb": "require 'thing'\n",
        },
    )
    candidates = (
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="./local",
            attributes={"aliases": ["localAlias"]},
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="./lib",
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="@/aliased",
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="@scope/core/feature",
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="@scope/empty",
        ),
        _fragment(
            projection="syntax.imports",
            path="pkg/main.py",
            language="Python",
            name=".dep",
        ),
        _fragment(
            projection="syntax.imports",
            path="cmd/main.go",
            language="Go",
            name="example.com/fixture/internal/dep",
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name="react",
        ),
        _fragment(
            projection="syntax.imports",
            path="src/main.ts",
            language="TypeScript",
            name=None,
            attributes={"dynamic": True},
        ),
        _fragment(
            projection="syntax.imports",
            path="script.rb",
            language="Ruby",
            name="thing",
            attributes={"generated": True},
        ),
    )

    rows = await RelationResolver(import_candidates=candidates).resolve_imports(
        bundle,
        SemanticMode.BEST_AVAILABLE,
    )

    resolved = {
        row.data["specifier"]: row.data["target_path"]
        for row in rows
        if row.evidence.resolution_status is ResolutionStatus.RESOLVED
    }
    assert resolved == {
        "./local": "src/local.ts",
        "./lib": "src/lib/index.ts",
        "@/aliased": "src/aliased.ts",
        "@scope/core/feature": "packages/core/src/feature.ts",
        ".dep": "pkg/dep.py",
        "example.com/fixture/internal/dep": "internal/dep/dep.go",
    }
    local = next(row for row in rows if row.data["specifier"] == "./local")
    assert local.data["aliases"] == ("localAlias",)
    assert local.evidence.evidence_kind is EvidenceKind.SEMANTIC
    candidates_by_specifier = {
        row.data["specifier"]: row
        for row in rows
        if row.evidence.resolution_status is ResolutionStatus.CANDIDATE
    }
    assert set(candidates_by_specifier) == {"@scope/empty", "react", None, "thing"}
    assert candidates_by_specifier["react"].data["external"] is True
    assert candidates_by_specifier[None].data["dynamic"] is True
    assert candidates_by_specifier["thing"].data["generated"] is True

    with raises_with_message(SemanticProviderRequiredError, "semantic_provider_required"):
        await RelationResolver(import_candidates=(candidates[7],)).resolve_imports(
            bundle,
            SemanticMode.SEMANTIC_REQUIRED,
        )


class _ExactSymbolResolver:
    def __init__(
        self,
        targets: dict[int, tuple[str, ...]],
        *,
        external_uris: dict[int, tuple[str, ...]] | None = None,
    ) -> None:
        self.targets = targets
        self.external_uris = external_uris or {}
        self.requests: list[NavigationRequest] = []

    async def navigate(
        self,
        request: NavigationRequest,
        bundle: SnapshotBundle,
        *,
        dependency_paths: tuple[str, ...] = (),
        control_paths: tuple[str, ...] = (),
    ) -> SemanticResolution:
        self.requests.append(request)
        # This fixture resolves position targets only; the name-target branch of
        # NavigationRequest leaves path/line/column unset.
        assert request.path is not None
        assert request.column is not None
        generation = SemanticGeneration.from_snapshot(
            bundle,
            provider_name="fixture-symbol-resolver",
            provider_config_digest="fixture",
            process_epoch=0,
            requested_file=request.path,
            dependency_paths=dependency_paths,
            control_paths=control_paths,
        )
        workspace_locations = tuple(
            LspLocation(
                uri=(Path(bundle.snapshot.root) / target).as_uri(),
                range=LspRange(
                    start=LspPosition(line=0, character=0),
                    end=LspPosition(line=0, character=3),
                ),
            )
            for target in self.targets[request.column]
        )
        external_locations = tuple(
            LspLocation(
                uri=uri,
                range=LspRange(
                    start=LspPosition(line=0, character=0),
                    end=LspPosition(line=0, character=3),
                ),
            )
            for uri in self.external_uris.get(request.column, ())
        )
        return SemanticResolution(
            operation=request.operation,
            capability=LspCapability.DEFINITION,
            status=FrameStatus.COMPLETE,
            generation=generation,
            locations=workspace_locations + external_locations,
        )


async def test_relation_producer_runs_only_selected_semantic_table(tmp_path: Path) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "src/main.py": "import dep\nrun()\n",
            "dep.py": "VALUE = 1\n",
            "target.py": "def run(): pass\n",
        },
    )
    symbol_resolver = _ExactSymbolResolver({1: ("target.py",)})
    resolver = RelationResolver(
        import_candidates=(
            _fragment(
                projection="syntax.imports",
                path="src/main.py",
                language="Python",
                name="dep",
            ),
        ),
        call_candidates=(
            _fragment(
                projection="syntax.call_sites",
                path="src/main.py",
                language="Python",
                name="run",
            ),
        ),
        symbol_resolver=symbol_resolver,
    )

    output = await resolver.produce(
        ("semantic.imports",),
        bundle,
        SemanticMode.BEST_AVAILABLE,
        {},
    )

    assert tuple(output) == ("semantic.imports",)
    assert symbol_resolver.requests == []


async def test_call_relations_use_exact_positions_for_overloads_and_shadowed_names(
    tmp_path: Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "src/main.py": "run(); run()\n",
            "src/overload_a.py": "def run(): pass\n",
            "src/overload_b.py": "def run(value): pass\n",
            "src/shadow.py": "def run(): pass\n",
        },
    )
    calls = (
        _fragment(
            projection="syntax.call_sites",
            path="src/main.py",
            language="Python",
            name="run",
            column=0,
        ),
        _fragment(
            projection="syntax.call_sites",
            path="src/main.py",
            language="Python",
            name="run",
            column=7,
        ),
        _fragment(
            projection="syntax.call_sites",
            path="src/main.py",
            language="Python",
            name=None,
            column=12,
            attributes={"dynamic": True},
        ),
    )
    symbol_resolver = _ExactSymbolResolver(
        {
            1: ("src/overload_a.py", "src/overload_b.py"),
            8: ("src/shadow.py",),
        }
    )

    rows = await RelationResolver(
        call_candidates=calls,
        symbol_resolver=symbol_resolver,
    ).resolve_calls(bundle, SemanticMode.BEST_AVAILABLE)

    resolved_targets = [
        row.data["target_path"]
        for row in rows
        if row.evidence.resolution_status is ResolutionStatus.RESOLVED
    ]
    assert resolved_targets == [
        "src/overload_a.py",
        "src/overload_b.py",
        "src/shadow.py",
    ]
    assert [(request.line, request.column) for request in symbol_resolver.requests] == [
        (1, 1),
        (1, 8),
    ]
    dynamic = rows[-1]
    assert dynamic.evidence.resolution_status is ResolutionStatus.CANDIDATE
    assert dynamic.data["dynamic"] is True


async def test_call_relations_decode_file_uris_and_keep_non_file_targets_external(
    tmp_path: Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "src/main.py": "run()\n",
            "src/encoded target.py": "def run(): pass\n",
        },
    )
    call = _fragment(
        projection="syntax.call_sites",
        path="src/main.py",
        language="Python",
        name="run",
    )
    external_uri = "https://example.test/run.py"
    symbol_resolver = _ExactSymbolResolver(
        {1: ("src/encoded target.py",)},
        external_uris={1: (external_uri,)},
    )

    rows = await RelationResolver(
        call_candidates=(call,),
        symbol_resolver=symbol_resolver,
    ).resolve_calls(bundle, SemanticMode.BEST_AVAILABLE)

    resolved = next(
        row for row in rows if row.evidence.resolution_status is ResolutionStatus.RESOLVED
    )
    assert resolved.data["target_path"] == "src/encoded target.py"
    assert resolved.data["external"] is False
    external = next(
        row for row in rows if row.evidence.resolution_status is ResolutionStatus.CANDIDATE
    )
    assert external.data["target_path"] is None
    assert external.data["target_uri"] == external_uri
    assert external.data["external"] is True
