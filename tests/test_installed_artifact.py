"""Every artifact path runs outside the monorepo from site-packages."""

from __future__ import annotations

import dataclasses
import email.parser
import email.policy
import importlib.metadata
import json
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap
import time
import zipfile

import _assertions
import _processes
import pytest
from packaging.requirements import Requirement

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src"
EXPECTED_DOCS = {
    "agent-workflow.md",
    "adopt-guide.md",
    "editor-safety.md",
    "evidence-and-coverage.md",
    "mcp-gateway.md",
    "postgresql-security.md",
    "provider-configuration.md",
    "quickstart.md",
    "server-instructions.md",
    "tool-catalog.md",
    "troubleshooting.md",
}


@dataclasses.dataclass(frozen=True)
class ArtifactSet:
    direct_wheel: pathlib.Path
    sdist: pathlib.Path
    sdist_wheel: pathlib.Path
    offline_dependency_wheels: tuple[pathlib.Path, ...]

    def select(self, name: str) -> pathlib.Path:
        if name == "direct_wheel":
            return self.direct_wheel
        if name == "sdist":
            return self.sdist
        if name == "sdist_wheel":
            return self.sdist_wheel
        raise AssertionError(f"unknown artifact fixture: {name}")


def _base_environment() -> dict[str, str]:
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


def _runtime_dependency_names() -> tuple[str, ...]:
    """Installed distributions in soleaux's runtime dependency closure."""
    seen: dict[str, importlib.metadata.Distribution] = {}
    extras_by_name: dict[str, frozenset[str]] = {}
    pending: list[tuple[importlib.metadata.Distribution, frozenset[str]]] = [
        (importlib.metadata.distribution("soleaux"), frozenset())
    ]
    while pending:
        distribution, requested_extras = pending.pop()
        for requirement_text in distribution.requires or []:
            requirement = Requirement(requirement_text)
            name = requirement.name.lower().replace("_", "-")
            if name == "soleaux":
                continue
            try:
                dependency = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                continue
            if requirement.marker is not None:
                contexts = requested_extras or frozenset({""})
                if not any(requirement.marker.evaluate({"extra": extra}) for extra in contexts):
                    continue
            new_extras = frozenset(requirement.extras) - extras_by_name.get(name, frozenset())
            if name in seen and not new_extras:
                continue
            extras_by_name[name] = extras_by_name.get(name, frozenset()) | frozenset(
                requirement.extras
            )
            if name not in seen:
                seen[name] = dependency
            pending.append((dependency, extras_by_name[name]))
    return tuple(sorted(seen))


def _compressed_wheel_tag(tags: list[str]) -> str:
    interpreters: list[str] = []
    abis: list[str] = []
    platforms: list[str] = []
    for tag in tags:
        interpreter, abi, platform = tag.split("-", 2)
        if interpreter not in interpreters:
            interpreters.append(interpreter)
        if abi not in abis:
            abis.append(abi)
        if platform not in platforms:
            platforms.append(platform)
    return ".".join(interpreters) + "-" + ".".join(abis) + "-" + ".".join(platforms)


def _repack_installed_wheel(
    distribution_name: str,
    output_directory: pathlib.Path,
) -> pathlib.Path:
    """Materialize one installed dependency as a local wheel for offline resolution."""
    distribution = importlib.metadata.distribution(distribution_name)
    wheel_text = distribution.read_text("WHEEL")
    assert wheel_text is not None
    wheel_metadata = email.parser.Parser(policy=email.policy.default).parsestr(wheel_text)
    wheel_tags = _assertions.string_list(wheel_metadata.get_all("Tag"))
    assert len(wheel_tags) >= 1
    wheel_tag = _compressed_wheel_tag(wheel_tags)
    assert wheel_tag and all(character.isalnum() or character in "._-" for character in wheel_tag)
    assert distribution.version and all(
        character.isalnum() or character in ".+_" for character in distribution.version
    )

    files = distribution.files
    assert files is not None
    distribution_root = pathlib.Path(str(distribution.locate_file(""))).resolve()
    # PEP 503 wheel-name escaping without `re` (no-regex maintained surface).
    normalized_name = "".join(
        character if character == "_" or character == "." or character.isalnum() else "_"
        for character in distribution_name
    )
    wheel_path = output_directory / f"{normalized_name}-{distribution.version}-{wheel_tag}.whl"
    with zipfile.ZipFile(wheel_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in sorted(files, key=lambda path: path.as_posix()):
            source_path = pathlib.Path(str(distribution.locate_file(relative_path))).resolve()
            if not source_path.is_relative_to(distribution_root):
                continue
            assert source_path.is_file()
            archive.write(source_path, arcname=relative_path.as_posix())
    return wheel_path


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> ArtifactSet:
    root = tmp_path_factory.mktemp("soleaux-artifacts")
    direct = root / "direct"
    rebuilt = root / "rebuilt"
    direct.mkdir()
    rebuilt.mkdir()
    uv = _processes.required_executable("uv")
    environment = _base_environment()

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
        cwd=root,
        environment=environment,
    )
    dependency_directory = root / "dependencies"
    dependency_directory.mkdir()
    return ArtifactSet(
        direct_wheel=_only(direct, "soleaux-*.whl"),
        sdist=sdist,
        sdist_wheel=_only(rebuilt, "soleaux-*.whl"),
        offline_dependency_wheels=tuple(
            _repack_installed_wheel(name, dependency_directory)
            for name in _runtime_dependency_names()
        ),
    )


def _python(environment_root: pathlib.Path) -> pathlib.Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return environment_root / directory / executable


def _command(environment_root: pathlib.Path) -> pathlib.Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "soleaux.exe" if os.name == "nt" else "soleaux"
    return environment_root / directory / executable


def _seed_local_postgres_provider(root: pathlib.Path) -> pathlib.Path:
    local_provider_bin = root / "node_modules" / ".bin"
    local_provider_bin.mkdir(parents=True)
    provider_name = (
        "postgres-language-server.exe" if os.name == "nt" else "postgres-language-server"
    )
    local_postgres_provider = local_provider_bin / provider_name
    pylsp = _processes.required_executable("pylsp")
    shutil.copy2(pylsp, local_postgres_provider)
    return local_postgres_provider


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _runtime_environment(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    audit_directory = tmp_path / "audit"
    audit_directory.mkdir()
    audit_log = tmp_path / "network-attempts.log"
    (audit_directory / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import json
            import os
            import shutil
            import socket
            import subprocess
            from pathlib import Path

            _original_connect = socket.socket.connect
            _original_run = subprocess.run
            _original_which = shutil.which

            def _audited_connect(instance, address):
                log = os.environ.get("SOLEAUX_NETWORK_AUDIT_LOG")
                if log is not None:
                    with Path(log).open("a", encoding="utf-8") as stream:
                        stream.write(repr(address) + "\\n")
                raise RuntimeError(f"unexpected network connection: {address!r}")

            def _which(command, *args, **kwargs):
                if command == "cargo" and os.environ.get("SOLEAUX_FAKE_CARGO_LOG"):
                    return command
                return _original_which(command, *args, **kwargs)

            def _run(command, *args, **kwargs):
                log = os.environ.get("SOLEAUX_FAKE_CARGO_LOG")
                if not log or not command or command[0] != "cargo":
                    return _original_run(command, *args, **kwargs)
                arguments = list(command)
                manifest = Path(arguments[arguments.index("--manifest-path") + 1])
                target = Path(arguments[arguments.index("--target-dir") + 1])
                worker = target / "release" / "soleaux-ast-grep-worker"
                worker.parent.mkdir(parents=True, exist_ok=True)
                worker.write_text("artifact rust worker\\n", encoding="utf-8")
                worker.chmod(0o755)
                Path(log).write_text(
                    json.dumps({
                        "argv": arguments,
                        "manifest": str(manifest),
                        "target": str(target),
                    }),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(arguments, 0, "", "")

            socket.socket.connect = _audited_connect
            shutil.which = _which
            subprocess.run = _run
            """
        ).lstrip(),
        encoding="utf-8",
    )
    environment = _base_environment()
    pylsp = _processes.required_executable("pylsp")
    git = _processes.required_executable("git")
    providerless_bin = tmp_path / "providerless-bin"
    providerless_bin.mkdir()
    providerless_bin.joinpath("git").symlink_to(git)
    environment["FASTMCP_CHECK_FOR_UPDATES"] = "off"
    environment["PYTHONPATH"] = str(audit_directory)
    environment["SOLEAUX_NETWORK_AUDIT_LOG"] = str(audit_log)
    environment["SOLEAUX_TEST_PROVIDERLESS_BIN"] = str(providerless_bin)
    environment["SOLEAUX_TEST_PROVIDER_BIN"] = str(pathlib.Path(pylsp).parent)
    return environment, audit_log


def _install(
    artifact: pathlib.Path,
    dependency_wheels: tuple[pathlib.Path, ...],
    environment_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    uv = _processes.required_executable("uv")
    environment = _base_environment()
    _processes.run_checked(
        (uv, "venv", "--python", "3.14", str(environment_root)),
        cwd=tmp_path,
        environment=environment,
    )
    _processes.run_checked(
        (
            uv,
            "pip",
            "install",
            "--python",
            str(_python(environment_root)),
            "--link-mode",
            "copy",
            str(artifact),
            *(str(path) for path in dependency_wheels),
        ),
        cwd=tmp_path,
        environment=environment,
    )


def _write_mcp_fixture(
    root: pathlib.Path,
    *,
    python: pathlib.Path,
    pid_log: pathlib.Path,
    audit_pythonpath: str,
) -> None:
    root.mkdir()
    root.joinpath("main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    backend = root / "artifact_mcp.py"
    backend.write_text(
        textwrap.dedent(
            """
            import os
            from pathlib import Path

            from fastmcp import FastMCP

            with Path(os.environ["SOLEAUX_ARTIFACT_PID_LOG"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"{os.getpid()}\\n")

            server = FastMCP("soleaux-artifact-fixture")

            @server.tool
            def echo(value: str) -> dict[str, str]:
                return {"echo": value}

            if __name__ == "__main__":
                server.run()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    root.joinpath("soleaux.toml").write_text(
        "\n".join(
            (
                'schema_version = "soleaux.config/v1"',
                "",
                "[mcp.artifact]",
                f"command = [{json.dumps(str(python))}, {json.dumps(str(backend))}]",
                "cache_ttl_seconds = 0",
                "env = { "
                f'PYTHONDONTWRITEBYTECODE = "1", PYTHONPATH = {json.dumps(audit_pythonpath)}, '
                f"SOLEAUX_ARTIFACT_PID_LOG = {json.dumps(str(pid_log))} }}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _wait_for_reaped_fixture_pids(pid_log: pathlib.Path) -> None:
    assert pid_log.is_file()
    pids = {int(value) for value in pid_log.read_text(encoding="utf-8").splitlines()}
    assert pids
    running: set[int] = set()
    for _ in range(100):
        running = set()
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                running.add(pid)
            else:
                running.add(pid)
        if not running:
            return
        time.sleep(0.02)
    assert not running


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return _assertions.object_mapping(json.loads(result.stdout))


@pytest.mark.parametrize(
    "artifact_name",
    ("direct_wheel", "sdist_wheel"),
)
def test_installed_artifact_postgres_provider_portability(
    artifact_name: str,
    artifacts: ArtifactSet,
    tmp_path: pathlib.Path,
) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    with zipfile.ZipFile(artifacts.select(artifact_name)) as wheel:
        wheel.extractall(site_packages)

    consumer = tmp_path / "consumer"
    consumer.mkdir()
    local_postgres_provider = _seed_local_postgres_provider(consumer)
    environment = _base_environment()
    environment["PATH"] = ""
    environment["PYTHONPATH"] = str(site_packages)

    probe = _json_output(
        _processes.run_checked(
            (
                sys.executable,
                "-c",
                textwrap.dedent(
                    """
                    import json
                    import soleaux
                    import sys
                    from pathlib import Path

                    from soleaux.lsp.providers import BUILTIN_PROVIDERS, ProviderRegistry

                    root = Path(sys.argv[1])
                    catalog = next(
                        provider
                        for provider in BUILTIN_PROVIDERS
                        if provider.name == "postgres-language-server"
                    )
                    registry = ProviderRegistry.default(root)
                    configured = registry.configured_for_path("schema.sql")
                    spec = registry.available_spec_for_path("schema.sql")
                    assert configured is not None
                    assert spec is not None
                    print(json.dumps({
                        "module_file": soleaux.__file__,
                        "catalog_argv": list(catalog.argv),
                        "catalog_version": catalog.version,
                        "provider_name": configured.provider_name,
                        "runtime_argv": list(spec.argv),
                    }))
                    """
                ),
                str(consumer),
            ),
            cwd=consumer,
            environment=environment,
        )
    )

    module_file_value = probe["module_file"]
    assert isinstance(module_file_value, str)
    module_file = pathlib.Path(module_file_value).resolve()
    assert module_file.is_relative_to(site_packages.resolve())
    assert probe["catalog_argv"] == ["postgres-language-server", "lsp-proxy"]
    assert probe["catalog_version"] == "0.25.4"
    assert probe["provider_name"] == "postgres-language-server"
    runtime_argv = _assertions.string_list(probe["runtime_argv"])
    assert pathlib.Path(runtime_argv[0]).resolve() == local_postgres_provider.resolve()
    assert runtime_argv[1:] == ["lsp-proxy"]


@pytest.mark.parametrize(
    "artifact_name",
    ("direct_wheel", "sdist", "sdist_wheel"),
)
def test_installed_artifact_acceptance(
    artifact_name: str,
    artifacts: ArtifactSet,
    tmp_path: pathlib.Path,
) -> None:
    artifact = artifacts.select(artifact_name)
    environment_root = tmp_path / "environment"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    local_postgres_provider = _seed_local_postgres_provider(consumer)
    _install(artifact, artifacts.offline_dependency_wheels, environment_root, tmp_path)
    runtime_environment, audit_log = _runtime_environment(tmp_path)
    providerless_environment = dict(runtime_environment)
    providerless_environment["PATH"] = providerless_environment["SOLEAUX_TEST_PROVIDERLESS_BIN"]
    python = _python(environment_root)
    executable = _command(environment_root)
    configured_consumer = tmp_path / "configured-consumer"
    fixture_pid_log = tmp_path / "artifact-mcp-pids.log"
    _write_mcp_fixture(
        configured_consumer,
        python=python,
        pid_log=fixture_pid_log,
        audit_pythonpath=runtime_environment["PYTHONPATH"],
    )
    before = _snapshot(consumer)
    configured_before = _snapshot(configured_consumer)

    module_probe = _processes.run_checked(
        (
            str(python),
            "-c",
            textwrap.dedent(
                """
                import json
                import sys
                from importlib.metadata import requires, version
                from importlib.resources import files
                from pathlib import Path

                import libcst
                import soleaux
                from soleaux.lsp.providers import BUILTIN_PROVIDERS, ProviderRegistry
                from soleaux.structural.rust_runtime import rust_workspace_manifest

                docs = files("soleaux.resources").joinpath("docs")
                skill = files("soleaux.resources").joinpath("skills/soleaux/SKILL.md")
                rust_manifest = rust_workspace_manifest()
                assert rust_manifest is not None
                root = Path(sys.argv[1])
                postgres_catalog = next(
                    provider
                    for provider in BUILTIN_PROVIDERS
                    if provider.name == "postgres-language-server"
                )
                registry = ProviderRegistry.default(root)
                postgres_provider = registry.configured_for_path("schema.sql")
                postgres_spec = registry.available_spec_for_path("schema.sql")
                assert postgres_provider is not None
                assert postgres_spec is not None
                print(json.dumps({
                    "module_file": soleaux.__file__,
                    "version": version("soleaux"),
                    "requirements": requires("soleaux"),
                    "libcst_file": libcst.__file__,
                    "libcst_version": version("libcst"),
                    "python": list(sys.version_info[:2]),
                    "docs": sorted(
                        item.name
                        for item in docs.iterdir()
                        if item.suffix in {".md", ".mdx"}
                    ),
                    "skill": skill.is_file(),
                    "rust_manifest": str(rust_manifest),
                    "postgres_catalog": {
                        "name": postgres_catalog.name,
                        "argv": list(postgres_catalog.argv),
                        "version": postgres_catalog.version,
                    },
                    "postgres_runtime": {
                        "provider": postgres_provider.provider_name,
                        "argv": list(postgres_spec.argv),
                    },
                }))
                """
            ),
            str(consumer),
        ),
        cwd=consumer,
        environment=providerless_environment,
    )
    module = _json_output(module_probe)
    module_file_value = module["module_file"]
    assert isinstance(module_file_value, str)
    module_file = pathlib.Path(module_file_value).resolve()
    assert module_file.is_relative_to(environment_root.resolve())
    assert not module_file.is_relative_to(SOURCE_ROOT.resolve())
    assert module["version"] == "0.1.0"
    requirements = _assertions.string_list(module["requirements"])
    assert "libcst>=1.8.6" in requirements
    libcst_file_value = module["libcst_file"]
    assert isinstance(libcst_file_value, str)
    libcst_file = pathlib.Path(libcst_file_value).resolve()
    assert libcst_file.is_relative_to(environment_root.resolve())
    assert not libcst_file.is_relative_to(SOURCE_ROOT.resolve())
    assert module["libcst_version"] == importlib.metadata.version("libcst")
    assert module["python"] == [3, 14]
    assert set(_assertions.string_list(module["docs"])) == EXPECTED_DOCS
    assert module["skill"] is True
    rust_manifest_value = module["rust_manifest"]
    assert isinstance(rust_manifest_value, str)
    rust_manifest = pathlib.Path(rust_manifest_value).resolve()
    assert rust_manifest.is_relative_to(environment_root.resolve())
    assert not rust_manifest.is_relative_to(SOURCE_ROOT.resolve())
    assert (rust_manifest.parent / "Cargo.lock").is_file()
    assert (rust_manifest.parent / "soleaux-ast-grep-worker" / "Cargo.toml").is_file()
    assert (rust_manifest.parent / "soleaux-ast-grep-worker" / "src" / "lib.rs").is_file()
    assert module["postgres_catalog"] == {
        "name": "postgres-language-server",
        "argv": ["postgres-language-server", "lsp-proxy"],
        "version": "0.25.4",
    }
    postgres_runtime = _assertions.object_mapping(module["postgres_runtime"])
    assert postgres_runtime["provider"] == "postgres-language-server"
    postgres_argv = _assertions.string_list(postgres_runtime["argv"])
    assert pathlib.Path(postgres_argv[0]).resolve() == local_postgres_provider.resolve()
    assert postgres_argv[1:] == ["lsp-proxy"]

    rust_home = tmp_path / "rust-home"
    rust_home.mkdir()
    cargo_log = tmp_path / "cargo-build.json"
    rust_environment = dict(runtime_environment)
    rust_environment.update(
        {
            "APPDATA": str(rust_home),
            "HOME": str(rust_home),
            "LOCALAPPDATA": str(rust_home),
            "SOLEAUX_FAKE_CARGO_LOG": str(cargo_log),
            "XDG_CACHE_HOME": str(rust_home),
        }
    )
    rust_install = _processes.run_checked(
        (
            str(executable),
            "--root",
            str(consumer),
            "install",
            "ast-grep-rust",
        ),
        cwd=consumer,
        environment=rust_environment,
    )
    rust_prefix = "[OK] ast-grep-rust: 0.44.1 at "
    assert rust_install.stdout.startswith(rust_prefix)
    rust_binary = pathlib.Path(rust_install.stdout.removeprefix(rust_prefix).strip()).resolve()
    assert rust_binary.is_relative_to(rust_home.resolve())
    assert rust_binary.read_text(encoding="utf-8") == "artifact rust worker\n"
    cargo_build = _assertions.object_mapping(json.loads(cargo_log.read_text(encoding="utf-8")))
    cargo_manifest_value = cargo_build["manifest"]
    cargo_target_value = cargo_build["target"]
    assert isinstance(cargo_manifest_value, str)
    assert isinstance(cargo_target_value, str)
    cargo_manifest = pathlib.Path(cargo_manifest_value).resolve()
    cargo_target = pathlib.Path(cargo_target_value).resolve()
    assert cargo_manifest == rust_manifest
    assert cargo_target == rust_binary.parent / "build"
    assert _assertions.string_list(cargo_build["argv"]) == [
        "cargo",
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        str(rust_manifest),
        "--target-dir",
        str(cargo_target),
    ]

    version = _processes.run_checked(
        (str(executable), "--version"),
        cwd=consumer,
        environment=runtime_environment,
    )
    assert version.stdout.strip() == "soleaux 0.1.0"

    doctor = _json_output(
        _processes.run_checked(
            (str(executable), "--root", str(consumer), "doctor", "--json"),
            cwd=consumer,
            environment=runtime_environment,
        )
    )
    assert doctor["status"] == "ok"

    lint_probe = _processes.run_checked(
        (str(executable), "--root", str(consumer), "lint"),
        cwd=consumer,
        environment=runtime_environment,
        expected_returncode=2,
    )
    lint_payload = _json_output(lint_probe)
    assert lint_payload["status"] == "error"
    lint_error = _assertions.object_mapping(lint_payload["error"])
    assert lint_error["error_type"] == "lint_unconfigured"

    providerless_probe = _json_output(
        _processes.run_checked(
            (
                str(python),
                "-c",
                textwrap.dedent(
                    """
                    import asyncio
                    import json
                    import sys
                    from pathlib import Path

                    from soleaux.analysis.service import SoleauxService
                    from soleaux.contracts.requests import SearchKind, SearchRequest, SemanticMode

                    async def main():
                        async with SoleauxService.from_root(Path(sys.argv[1])) as service:
                            response = await service.search(
                                SearchRequest(
                                    query="answer",
                                    kinds=[SearchKind.SYMBOL],
                                    semantic_mode=SemanticMode.SEMANTIC_REQUIRED,
                                )
                            )
                            active_language_servers = service.active_language_server_count
                        error = response.error
                        print(json.dumps({
                            "status": response.status.value,
                            "error_type": error.error_type if error else None,
                            "active_language_servers": active_language_servers,
                        }))

                    asyncio.run(main())
                    """
                ),
                str(consumer),
            ),
            cwd=consumer,
            environment=providerless_environment,
            timeout=30,
        )
    )
    # semantic_required fails closed from published coverage without starting a provider.
    assert providerless_probe == {
        "status": "error",
        "error_type": "semantic_unavailable",
        "active_language_servers": 0,
    }

    stdio_probe = _processes.run_checked(
        (
            str(python),
            "-c",
            textwrap.dedent(
                """
                import asyncio
                import json
                import os
                import subprocess
                import sys

                from fastmcp import Client
                from fastmcp.client.transports import StdioTransport

                async def probe(server_environment, root, *, mcp):
                    transport = StdioTransport(
                        command=sys.argv[1],
                        args=["--root", root],
                        env=server_environment,
                        cwd=root,
                        keep_alive=False,
                    )
                    async with Client(transport) as client:
                        tools = await client.list_tools()
                        resources = await client.list_resources()
                        guide = await client.read_resource("soleaux://guide")
                        if mcp:
                            mcp_result = await client.call_tool(
                                "artifact_echo", {"value": "installed"}
                            )
                            mcp_payload = mcp_result.structured_content
                            search_rows = None
                        else:
                            search_payload = None
                            for _ in range(100):
                                searched = await client.call_tool(
                                    "search",
                                    {
                                        "request": {
                                            "query": "answer",
                                            "kinds": ["symbol"],
                                            "semantic_mode": "syntax_only",
                                        }
                                    },
                                )
                                search_payload = searched.structured_content
                                assert search_payload is not None
                                assert search_payload["status"] == "ok"
                                if search_payload["rows"]:
                                    break
                                await asyncio.sleep(0.05)
                            assert search_payload is not None
                            mcp_payload = None
                            search_rows = [
                                row["name"] for row in search_payload["rows"]
                            ]
                    return {
                        "tools": [tool.name for tool in tools],
                        "resources": len(resources),
                        "guide": "`search`" in guide[0].text,
                        "mcp_payload": mcp_payload,
                        "search_rows": search_rows,
                    }

                async def wait_for_children():
                    children = []
                    for _ in range(100):
                        children = subprocess.run(
                            ["pgrep", "-P", str(os.getpid())],
                            capture_output=True,
                            text=True,
                            check=False,
                        ).stdout.splitlines()
                        if not children:
                            return []
                        await asyncio.sleep(0.02)
                    return children

                async def main():
                    from soleaux.postgresql.runtime import (
                        SAFE_BASELINE_ENVIRONMENT_NAMES,
                        capture_inherited_environment,
                    )

                    selected_environment = capture_inherited_environment(
                        SAFE_BASELINE_ENVIRONMENT_NAMES
                    )
                    selected_environment["FASTMCP_MCP_CAMELCASE_COMPAT"] = "false"
                    selected_environment["PYTHONDONTWRITEBYTECODE"] = "1"
                    selected_environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
                    selected_environment["SOLEAUX_NETWORK_AUDIT_LOG"] = os.environ[
                        "SOLEAUX_NETWORK_AUDIT_LOG"
                    ]
                    selected_environment["PATH"] = os.pathsep.join((
                        os.environ["SOLEAUX_TEST_PROVIDER_BIN"],
                        selected_environment["PATH"],
                    ))
                    zero = await probe(selected_environment, sys.argv[2], mcp=False)
                    configured = await probe(
                        selected_environment, sys.argv[3], mcp=True
                    )
                    selected_children = await wait_for_children()
                    print(json.dumps({
                        "zero_tools": len(zero["tools"]),
                        "zero_resources": zero["resources"],
                        "zero_guide": zero["guide"],
                        "zero_search_rows": zero["search_rows"],
                        "configured_tools": len(configured["tools"]),
                        "mcp_tools": [
                            name for name in configured["tools"] if name.startswith("artifact_")
                        ],
                        "configured_resources": configured["resources"],
                        "configured_guide": configured["guide"],
                        "mcp_payload": configured["mcp_payload"],
                        "children": selected_children,
                    }))

                asyncio.run(main())
                """
            ),
            str(executable),
            str(consumer),
            str(configured_consumer),
        ),
        cwd=consumer,
        environment=runtime_environment,
        timeout=30,
    )
    stdio = _json_output(stdio_probe)
    assert stdio == {
        "zero_tools": 10,
        "zero_resources": 8,
        "zero_guide": True,
        "zero_search_rows": ["answer"],
        "configured_tools": 11,
        "mcp_tools": ["artifact_echo"],
        "configured_resources": 8,
        "configured_guide": True,
        "mcp_payload": {"echo": "installed"},
        "children": [],
    }
    _wait_for_reaped_fixture_pids(fixture_pid_log)

    assert _snapshot(consumer) == before
    assert _snapshot(configured_consumer) == configured_before
    assert not (consumer / ".soleaux").exists()
    assert not (configured_consumer / ".soleaux").exists()
    assert not audit_log.exists()
