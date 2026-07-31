"""Canonical path, digest, and language identity contracts."""

import pathlib

import pytest

import soleaux.contracts.repository
import soleaux.contracts.workspace


def _workspace(root: pathlib.Path):
    return soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(root))], config_digest="d" * 64
    ).get(None)


def test_repository_path_admits_relative_absolute_and_percent_encoded_uri(
    tmp_path: pathlib.Path,
) -> None:
    directory = tmp_path / "space dir"
    directory.mkdir()
    target = directory / "Example.ts"
    target.write_text("export const value = 1;\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    relative = soleaux.contracts.repository.RepositoryPath.admit(workspace, "space dir/Example.ts")
    absolute = soleaux.contracts.repository.RepositoryPath.admit(workspace, target)
    uri = soleaux.contracts.repository.RepositoryPath.admit(workspace, target.as_uri())

    assert relative == absolute == uri
    assert relative.value == "space dir/Example.ts"
    assert relative.file_uri(workspace) == target.as_uri()
    localhost = soleaux.contracts.repository.RepositoryPath.admit(
        workspace,
        target.as_uri().replace("file://", "file://LOCALHOST"),
    )
    assert localhost == relative


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.ts",
        "nested/../../outside.ts",
        "bad\x00name.ts",
        r"windows\separator.ts",
        "nested//file.ts",
        "nested/./file.ts",
        "https://example.com/file.ts",
        "file://remote.example/workspace/file.ts",
        "file:relative.ts",
        "file:///workspace/bad%ZZ.ts",
    ],
)
def test_repository_path_rejects_noncanonical_or_unsafe_inputs(
    tmp_path: pathlib.Path,
    candidate: str,
) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(
        (
            soleaux.contracts.repository.InvalidRepositoryPathError,
            soleaux.contracts.repository.RepositoryPathEscapeError,
        )
    ):
        soleaux.contracts.repository.RepositoryPath.admit(workspace, candidate)


def test_repository_path_rejects_symlink_escape(tmp_path: pathlib.Path) -> None:
    outside = tmp_path.parent / "soleaux-outside.ts"
    outside.write_text("export {};\n", encoding="utf-8")
    (tmp_path / "escape.ts").symlink_to(outside)

    with pytest.raises(soleaux.contracts.repository.RepositoryPathEscapeError):
        soleaux.contracts.repository.RepositoryPath.admit(_workspace(tmp_path), "escape.ts")


def test_digest_and_language_registry_have_one_stable_identity(tmp_path: pathlib.Path) -> None:
    repository_path = soleaux.contracts.repository.RepositoryPath.admit(
        _workspace(tmp_path), "src/example.tsx"
    )
    language = soleaux.contracts.repository.LANGUAGE_REGISTRY.detect(repository_path)

    assert soleaux.contracts.repository.content_digest(b"same bytes") == (
        "58100dc8fc06562ce3e578231dc948e083520ee49c4b4ee5a5a28bb4b4003feb"
    )
    assert language is not None
    assert language.language_id == "typescriptreact"
    assert language.structural_language == "Tsx"
    assert language.parser_id == "ts-morph:typescript"
