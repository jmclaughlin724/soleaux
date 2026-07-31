"""Wheel and sdist contents are bounded, complete, and reproducible."""

from __future__ import annotations

import dataclasses
import email.parser
import email.policy
import pathlib
import tarfile
import zipfile

import _processes

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
WHEEL_LIMIT_BYTES = 5 * 1024 * 1024
SDIST_LIMIT_BYTES = 10 * 1024 * 1024
PY_TYPED_WHEEL_PATH = "soleaux/py.typed"
PY_TYPED_SDIST_PATH = f"src/{PY_TYPED_WHEEL_PATH}"
EXPECTED_GATEWAY_WHEEL_OWNERS = {
    "soleaux/contracts/config.py",
    "soleaux/gateway.py",
    "soleaux/server.py",
}
EXPECTED_GATEWAY_SDIST_OWNERS = {f"src/{path}" for path in EXPECTED_GATEWAY_WHEEL_OWNERS}
GENERATED_DOCS_SDIST_PREFIXES = (
    "docs/.blume/",
    "docs/.blume-verify/",
    "docs/.turbo/",
    "docs/dist/",
    "docs/node_modules/",
)

EXPECTED_PACKAGED_GUIDANCE = {
    "soleaux/resources/docs/agent-workflow.md",
    "soleaux/resources/docs/editor-safety.md",
    "soleaux/resources/docs/evidence-and-coverage.md",
    "soleaux/resources/docs/provider-configuration.md",
    "soleaux/resources/docs/quickstart.md",
    "soleaux/resources/docs/server-instructions.md",
    "soleaux/resources/docs/tool-catalog.md",
    "soleaux/resources/docs/troubleshooting.md",
    "soleaux/resources/docs/adopt-guide.md",
    "soleaux/resources/skills/soleaux/SKILL.md",
}
FORBIDDEN_POLICY_RESOURCE_PREFIX = "soleaux/resources/policy/"
FORBIDDEN_POLICY_RESOURCE_SDIST_PREFIX = f"src/{FORBIDDEN_POLICY_RESOURCE_PREFIX}"
FORBIDDEN_POLICY_RESOURCE_SOURCE = PACKAGE_ROOT / "src" / FORBIDDEN_POLICY_RESOURCE_PREFIX
EXECUTABLE_RESOURCE_PREFIXES = (
    "soleaux/resources/postgresql/",
    "soleaux/resources/rules/",
    "soleaux/resources/structural/",
    "soleaux/resources/typescript/",
)
RUST_WORKER_WHEEL_ROOT = "soleaux/resources/structural/rust/"
RUST_WORKER_BUILD_INPUTS = {
    "Cargo.lock",
    "Cargo.toml",
    "soleaux-ast-grep-worker/Cargo.toml",
    "soleaux-ast-grep-worker/src/lib.rs",
    "soleaux-ast-grep-worker/src/main.rs",
}
_BANNED_EXECUTABLE_RESOURCE_STRINGS: tuple[bytes, ...] = (
    b"sgconfig",
    b"tools/ast-grep",
    b"pnpm ",
    b".codex",
    b".claude",
)

# Strings that would couple the wheel to this specific repository. The wheel
# must be portable to any external workspace, so none of these may appear in
# any source file or rendered doc. (Generic tool references like "ast-grep" or
# generic TOML directives like "workspace = true" are NOT banned — only
# strings that identify THIS repo, its absolute paths, or its dev launch.)
_BANNED_WHEEL_STRINGS: tuple[bytes, ...] = (
    b"anilize-temp",
    b"/Users/johnmclaughlin",
    b"/private/tmp/anilize-",
    b"pnpm soleaux:dev",
    b"--workspace-root",
    b"@eslint/mcp@0.3.9",
    b"next-devtools-mcp@0.4.0",
    b"@playwright/mcp@0.0.78",
    b"shadcn@4.14.1",
    b"SOLEAUX_ANILIZE_TEMP_TOKEN",
)
_WORKSPACE_ACTIVATION_STRINGS: tuple[bytes, ...] = (
    b"anilize-temp",
    b"scripts/soleaux",
    b"anilize_temp_http.py",
    b"pnpm soleaux:",
    b"Keychain",
    b"LaunchAgents",
    b"launchd",
)


@dataclasses.dataclass(frozen=True)
class BuiltArtifacts:
    direct_wheel: pathlib.Path
    sdist: pathlib.Path
    sdist_wheel: pathlib.Path


def _environment() -> dict[str, str]:
    return _processes.minimum_environment(
        {
            "UV_NO_SYNC": "1",
            "UV_OFFLINE": "1",
        }
    )


def _only(directory: pathlib.Path, pattern: str) -> pathlib.Path:
    matches = tuple(directory.glob(pattern))
    assert len(matches) == 1
    return matches[0]


def _build_artifacts(tmp_path: pathlib.Path) -> BuiltArtifacts:
    uv = _processes.required_executable("uv")
    environment = _environment()
    direct = tmp_path / "direct"
    rebuilt = tmp_path / "rebuilt"
    direct.mkdir()
    rebuilt.mkdir()

    _processes.run_checked(
        (
            uv,
            "build",
            "--no-sources",
            "--out-dir",
            str(direct),
            "--no-create-gitignore",
        ),
        cwd=REPOSITORY_ROOT,
        environment=environment,
    )
    sdist = _only(direct, "soleaux-*.tar.gz")
    _processes.run_checked(
        (
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(rebuilt),
            "--no-create-gitignore",
            str(sdist),
        ),
        cwd=tmp_path,
        environment=environment,
    )
    return BuiltArtifacts(
        direct_wheel=_only(direct, "soleaux-*.whl"),
        sdist=sdist,
        sdist_wheel=_only(rebuilt, "soleaux-*.whl"),
    )


def _wheel_contents(path: pathlib.Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if not info.is_dir()
        }


def _sdist_contents(path: pathlib.Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        contents: dict[str, bytes] = {}
        for member in archive.getmembers():
            if not member.isfile() or "/" not in member.name:
                continue
            payload = archive.extractfile(member)
            assert payload is not None
            contents[member.name.split("/", maxsplit=1)[1]] = payload.read()
        return contents


def _assert_no_private_or_generated_payloads(paths: set[str]) -> None:
    for path in paths:
        parts = path.split("/")
        assert not path.startswith(("plans/", "tests/"))
        assert "__pycache__" not in parts
        assert parts[-1] not in {".env", ".env.local"}
        assert not path.endswith((".pyc", ".pyo"))


def _assert_executable_resources_are_repository_neutral(
    wheel_contents: dict[str, bytes],
) -> None:
    for path, payload in wheel_contents.items():
        if not path.startswith(EXECUTABLE_RESOURCE_PREFIXES):
            continue
        for banned in _BANNED_EXECUTABLE_RESOURCE_STRINGS:
            assert banned not in payload, (
                f"executable resource {path!r} contains repository-specific string {banned!r}"
            )


def _assert_no_workspace_activation_payload(path: str, payload: bytes) -> None:
    for banned in _WORKSPACE_ACTIVATION_STRINGS:
        assert banned not in payload, (
            f"distribution payload {path!r} contains workspace activation string {banned!r}"
        )


def _metadata_description(payload: bytes) -> bytes:
    metadata = email.parser.BytesParser(policy=email.policy.default).parsebytes(payload)
    description = metadata.get_payload()
    assert isinstance(description, str)
    return description.encode("utf-8")


def test_distribution_contents_and_limits(tmp_path: pathlib.Path) -> None:
    assert not any(path.is_file() for path in FORBIDDEN_POLICY_RESOURCE_SOURCE.rglob("*"))
    artifacts = _build_artifacts(tmp_path)
    direct_contents = _wheel_contents(artifacts.direct_wheel)
    rebuilt_contents = _wheel_contents(artifacts.sdist_wheel)
    sdist_contents = _sdist_contents(artifacts.sdist)
    sdist_paths = set(sdist_contents)

    assert artifacts.direct_wheel.stat().st_size <= WHEEL_LIMIT_BYTES
    assert artifacts.sdist_wheel.stat().st_size <= WHEEL_LIMIT_BYTES
    assert artifacts.sdist.stat().st_size <= SDIST_LIMIT_BYTES
    assert direct_contents == rebuilt_contents
    assert set(direct_contents) >= EXPECTED_PACKAGED_GUIDANCE
    assert not any(path.startswith(FORBIDDEN_POLICY_RESOURCE_PREFIX) for path in direct_contents)
    assert not any(path.startswith(FORBIDDEN_POLICY_RESOURCE_SDIST_PREFIX) for path in sdist_paths)
    assert set(direct_contents) >= EXPECTED_GATEWAY_WHEEL_OWNERS
    assert sdist_paths >= EXPECTED_GATEWAY_SDIST_OWNERS
    assert {
        path.removeprefix(RUST_WORKER_WHEEL_ROOT)
        for path in direct_contents
        if path.startswith(RUST_WORKER_WHEEL_ROOT)
    } == RUST_WORKER_BUILD_INPUTS
    assert {f"rust/{path}" for path in RUST_WORKER_BUILD_INPUTS} <= sdist_paths
    for relative_path in RUST_WORKER_BUILD_INPUTS:
        assert (
            direct_contents[f"{RUST_WORKER_WHEEL_ROOT}{relative_path}"]
            == sdist_contents[f"rust/{relative_path}"]
        )
    assert not any("/target/" in f"/{path}/" for path in sdist_paths)
    _assert_no_private_or_generated_payloads(set(direct_contents))
    _assert_no_private_or_generated_payloads(sdist_paths)
    assert not any(path.endswith("soleaux.toml") for path in direct_contents)
    assert not any(path.endswith("soleaux.toml") for path in sdist_paths)
    assert direct_contents.get(PY_TYPED_WHEEL_PATH) == b""
    assert PY_TYPED_SDIST_PATH in sdist_paths
    assert not any(path.startswith(GENERATED_DOCS_SDIST_PREFIXES) for path in sdist_paths)
    assert {
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "src/soleaux/resources/skills/soleaux/SKILL.md",
    } <= sdist_paths

    metadata_name = next(name for name in direct_contents if name.endswith(".dist-info/METADATA"))
    metadata_bytes = direct_contents[metadata_name]
    parsed_metadata = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        metadata_bytes
    )
    assert parsed_metadata.get_all("Requires-Python") == [">=3.14"]
    metadata = metadata_bytes.decode("utf-8")
    assert "License-Expression: MIT" in metadata
    assert "License-File: LICENSE" in metadata
    assert "Project-URL: Source" in metadata
    assert "Author: John McLaughlin" in metadata
    assert "Requires-Dist: fastmcp==4.0.0b1" in metadata
    assert "Requires-Dist: fastmcp-slim==4.0.0b1" in metadata
    assert "Requires-Dist: mcp==2.0.0" in metadata
    assert "Requires-Dist: mcp-types==2.0.0" in metadata
    assert "Requires-Dist: platformdirs==4.10.1" in metadata
    assert "Requires-Dist: psutil>=6.0; extra == 'adopt'" in metadata
    assert PACKAGE_ROOT.joinpath("README.md").is_file()

    _assert_no_repo_coupling(direct_contents)
    _assert_no_workspace_activation_payload(
        "wheel metadata description", _metadata_description(metadata_bytes)
    )
    _assert_no_workspace_activation_payload("sdist README.md", sdist_contents["README.md"])
    _assert_no_workspace_activation_payload(
        "sdist metadata description", _metadata_description(sdist_contents["PKG-INFO"])
    )
    _assert_executable_resources_are_repository_neutral(direct_contents)


def _assert_no_repo_coupling(wheel_contents: dict[str, bytes]) -> None:
    """Reject any byte pattern that would leak this repo's identity or layout."""
    for path, payload in wheel_contents.items():
        # Skip the dist-info metadata; project.urls point back at the source
        # repo intentionally (Homepage/Source/Issues/Changelog URLs).
        if path.endswith(".dist-info/METADATA") or path.endswith(".dist-info/RECORD"):
            continue
        for banned in _BANNED_WHEEL_STRINGS:
            assert banned not in payload, (
                f"wheel payload {path!r} contains banned string {banned!r} — "
                f"this couples the wheel to this repository"
            )
