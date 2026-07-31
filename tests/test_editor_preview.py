"""D009/D027: editor previews normalize LSP edits without writing."""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Mapping

import _assertions
import pydantic
import pytest

import soleaux.analysis.frame
import soleaux.analysis.service
import soleaux.contracts.coverage
import soleaux.contracts.requests
import soleaux.contracts.results
import soleaux.contracts.workspace
import soleaux.editor.preview
import soleaux.lsp.contracts
import soleaux.lsp.generation
import soleaux.lsp.operations
import soleaux.lsp.resolvers
import soleaux.structural.snapshot


class _EditorResolver(soleaux.lsp.resolvers.SemanticResolver):
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.calls: list[soleaux.lsp.contracts.LspCapability] = []

    async def execute_capability(
        self,
        capability: soleaux.lsp.contracts.LspCapability,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        path: str,
        arguments: Mapping[str, object] | None = None,
        dependency_paths: tuple[str, ...] = (),
        **_kwargs: object,
    ) -> soleaux.lsp.operations.CapabilityResolution:
        self.calls.append(capability)
        generation = soleaux.lsp.generation.SemanticGeneration.from_snapshot(
            bundle,
            provider_name="fixture-lsp",
            provider_config_digest="fixture-config",
            process_epoch=0,
            requested_file=path,
            dependency_paths=dependency_paths,
        )
        uri = (self.root / path).as_uri()
        request_arguments = arguments or {}
        if capability is soleaux.lsp.contracts.LspCapability.WORKSPACE_SYMBOL:
            payload: object = [
                {
                    "name": "target",
                    "kind": 12,
                    "location": {
                        "uri": uri,
                        "range": _range(0, 4, 0, 10),
                    },
                }
            ]
        elif capability in {
            soleaux.lsp.contracts.LspCapability.RENAME,
            soleaux.lsp.contracts.LspCapability.RENAME_STRICT,
        }:
            new_name = request_arguments["newName"]
            assert isinstance(new_name, str)
            payload = {
                "changes": {
                    uri: [
                        {
                            "range": _range(0, 4, 0, 10),
                            "newText": new_name,
                        }
                    ]
                }
            }
        elif capability in {
            soleaux.lsp.contracts.LspCapability.FORMAT_DOCUMENT,
            soleaux.lsp.contracts.LspCapability.FORMAT_RANGE,
        }:
            payload = [
                {
                    "range": _range(0, 0, 0, 13),
                    "newText": "def target() -> int:",
                }
            ]
        elif capability is soleaux.lsp.contracts.LspCapability.CODE_ACTIONS:
            payload = [
                {
                    "title": "Use two",
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": _range(1, 11, 1, 12),
                                    "newText": "2",
                                }
                            ]
                        }
                    },
                }
            ]
        else:
            raise AssertionError(f"unexpected capability {capability}")
        return soleaux.lsp.operations.CapabilityResolution(
            capability=capability,
            status=soleaux.contracts.coverage.FrameStatus.COMPLETE,
            generation=generation,
            payload=soleaux.lsp.operations.normalize_json_payload(payload),
        )

    def editor_session_context(
        self,
        generation: soleaux.lsp.generation.SemanticGeneration,
    ) -> soleaux.lsp.contracts.EditorSessionContext:
        return soleaux.lsp.contracts.EditorSessionContext(
            workspace_id=generation.workspace_id,
            provider_name=generation.provider_name,
            provider_config_digest=generation.provider_config_digest,
            project_id=generation.project_id,
            project_root=generation.project_root,
            project_config_digest=generation.project_config_digest,
            compiler_identity=generation.compiler_identity,
            process_epoch=generation.process_epoch,
            position_encoding="utf-16",
            document_versions={(self.root / generation.requested_file).as_uri(): 1},
        )

    def process_epoch(
        self,
        *,
        workspace_id: str,
        provider_name: str,
        provider_config_digest: str,
        project_id: str | None = None,
        project_root: str | None = None,
        project_config_digest: str | None = None,
        compiler_identity: str | None = None,
    ) -> int:
        assert workspace_id == "workspace"
        assert provider_name == "fixture-lsp"
        assert provider_config_digest == "fixture-config"
        assert project_id is not None
        assert project_root is not None
        assert project_config_digest is not None
        assert compiler_identity is not None
        return 0


class _EditorFrameBuilder(soleaux.analysis.frame.AnalysisFrameBuilder):
    def __init__(self, resolver: _EditorResolver) -> None:
        super().__init__()
        self._fixture_resolver = resolver

    def semantic_resolver(
        self, workspace: soleaux.contracts.workspace.WorkspaceRoot
    ) -> soleaux.lsp.resolvers.SemanticResolver:
        del workspace
        return self._fixture_resolver


async def make_editor_service(
    root: pathlib.Path,
    *,
    preview_ttl_seconds: float = 300,
) -> tuple[soleaux.analysis.service.SoleauxService, _EditorResolver]:
    workspaces = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(root))],
        config_digest="editor-test",
    )
    resolver = _EditorResolver(root)
    return (
        soleaux.analysis.service.SoleauxService(
            workspaces,
            frame_builder=_EditorFrameBuilder(resolver),
            preview_ttl_seconds=preview_ttl_seconds,
        ),
        resolver,
    )


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "operation": "rename",
                "path": "main.py",
                "line": 1,
                "column": 1,
                "new_name": "renamed",
                "end_line": 1,
                "end_column": 2,
            },
            "range end",
        ),
        (
            {
                "operation": "rename",
                "path": "main.py",
                "target": "name",
                "symbol_name": "target",
                "line": 1,
                "column": 1,
                "new_name": "renamed",
            },
            "accepts no position",
        ),
        (
            {
                "operation": "format_document",
                "path": "main.py",
                "line": 1,
                "column": 1,
            },
            "accepts no range",
        ),
        (
            {
                "operation": "code_action",
                "path": "main.py",
                "line": 1,
                "column": 1,
                "end_line": 2,
                "action_index": 0,
            },
            "requires both",
        ),
    ],
)
def test_editor_request_contract_rejects_ambiguous_arguments(
    payload: dict[str, object],
    message: str,
) -> None:
    with _assertions.raises_with_message(pydantic.ValidationError, message):
        soleaux.contracts.requests.PreviewEditRequest.model_validate(payload)


@pytest.mark.parametrize(
    "preview_request, expected_capabilities",
    [
        (
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.RENAME,
                path="main.py",
                target=soleaux.contracts.requests.RenameTarget.NAME,
                symbol_name="target",
                new_name="renamed",
            ),
            (
                soleaux.lsp.contracts.LspCapability.WORKSPACE_SYMBOL,
                soleaux.lsp.contracts.LspCapability.RENAME,
            ),
        ),
        (
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.RENAME,
                path="main.py",
                target=soleaux.contracts.requests.RenameTarget.POSITION,
                line=1,
                column=5,
                new_name="renamed",
                strict=True,
            ),
            (soleaux.lsp.contracts.LspCapability.RENAME_STRICT,),
        ),
        (
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.FORMAT_DOCUMENT,
                path="main.py",
            ),
            (soleaux.lsp.contracts.LspCapability.FORMAT_DOCUMENT,),
        ),
        (
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.FORMAT_RANGE,
                path="main.py",
                line=1,
                column=1,
                end_line=1,
                end_column=14,
            ),
            (soleaux.lsp.contracts.LspCapability.FORMAT_RANGE,),
        ),
        (
            soleaux.contracts.requests.PreviewEditRequest(
                operation=soleaux.contracts.requests.PreviewOperation.CODE_ACTION,
                path="main.py",
                line=2,
                column=12,
                action_index=0,
            ),
            (soleaux.lsp.contracts.LspCapability.CODE_ACTIONS,),
        ),
    ],
)
async def test_all_editor_kinds_return_bound_previews_without_writing(
    tmp_path: pathlib.Path,
    preview_request: soleaux.contracts.requests.PreviewEditRequest,
    expected_capabilities: tuple[soleaux.lsp.contracts.LspCapability, ...],
) -> None:
    source = tmp_path / "main.py"
    source.write_text("def target():\n    return 1\n", encoding="utf-8")
    before = source.read_bytes()
    service, resolver = await make_editor_service(tmp_path)
    try:
        response = await service.preview(preview_request)
    finally:
        await service.aclose()

    assert response.status is soleaux.contracts.results.ResultStatus.OK
    assert response.data is not None
    assert response.data["schema_version"] == "soleaux.preview/v1"
    assert response.data["origin"] == "lsp"
    assert response.data["affected_paths"] == ["main.py"]
    assert response.data["patches"]
    assert len(response.data["digest"]) == 64
    if preview_request.operation is soleaux.contracts.requests.PreviewOperation.RENAME:
        expected_target = preview_request.target or (
            soleaux.contracts.requests.RenameTarget.NAME
            if preview_request.symbol_name is not None and not preview_request.strict
            else soleaux.contracts.requests.RenameTarget.POSITION
        )
        assert response.data["target"]["target"] == expected_target
    assert tuple(resolver.calls) == expected_capabilities
    assert source.read_bytes() == before


async def test_workspace_edit_variants_reject_unsafe_or_ambiguous_changes(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "main.py"
    source.write_text("hello\n", encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="editor-normalizer",
    ).get("workspace")
    bundle = await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=("main.py",)
    )
    uri = source.as_uri()
    valid_edit = {"range": _range(0, 0, 0, 5), "newText": "world"}

    cases: list[tuple[object, str]] = [
        (
            {
                "changes": {uri: [valid_edit]},
                "documentChanges": [],
            },
            "cannot mix",
        ),
        (
            {
                "documentChanges": [
                    {
                        "kind": "create",
                        "uri": (tmp_path / "new.py").as_uri(),
                    }
                ]
            },
            "resource operations",
        ),
        (
            {
                "documentChanges": [
                    {
                        "textDocument": {"uri": uri, "version": 2},
                        "edits": [valid_edit],
                    }
                ]
            },
            "stale",
        ),
        (
            {
                "changes": {
                    uri: [
                        valid_edit,
                        {"range": _range(0, 2, 0, 4), "newText": "x"},
                    ]
                }
            },
            "overlapping",
        ),
        (
            {
                "changes": {
                    uri: [
                        {
                            **valid_edit,
                            "annotationId": "approval",
                        }
                    ]
                }
            },
            "annotated",
        ),
        (
            {"changes": {(tmp_path.parent / "outside.py").as_uri(): [valid_edit]}},
            "escapes workspace",
        ),
    ]
    before = _sha256(source.read_bytes())
    for raw_edit, message in cases:
        with _assertions.raises_with_message(soleaux.editor.preview.EditorPreviewError, message):
            soleaux.editor.preview.normalize_workspace_edit(
                raw_edit,
                root=tmp_path,
                bundle=bundle,
                position_encoding="utf-16",
                document_versions={uri: 1},
            )
        assert _sha256(source.read_bytes()) == before


async def test_symlink_edit_path_is_rejected_without_touching_target(
    tmp_path: pathlib.Path,
) -> None:
    source = tmp_path / "main.py"
    target = tmp_path / "target.py"
    target.write_text("secret\n", encoding="utf-8")
    source.symlink_to(target)
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="editor-symlink",
    ).get("workspace")
    bundle = soleaux.structural.snapshot.SnapshotBundle(
        snapshot=(
            await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
                scope=("target.py",)
            )
        ).snapshot,
        contents={"main.py": b"secret\n"},
        notes=(),
    )

    with _assertions.raises_with_message(soleaux.editor.preview.EditorPreviewError, "symlink"):
        soleaux.editor.preview.normalize_workspace_edit(
            {"changes": {source.as_uri(): [{"range": _range(0, 0, 0, 6), "newText": "changed"}]}},
            root=tmp_path,
            bundle=bundle,
            position_encoding="utf-16",
            document_versions={},
        )
    assert target.read_text(encoding="utf-8") == "secret\n"


def _range(
    start_line: int,
    start_character: int,
    end_line: int,
    end_character: int,
) -> dict[str, dict[str, int]]:
    return {
        "start": {"line": start_line, "character": start_character},
        "end": {"line": end_line, "character": end_character},
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
