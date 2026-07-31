"""Engine selection, fail-closed guidance, and cross-engine parity."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import unittest.mock
from typing import Literal

import _assertions
import _host_root
import pytest

import soleaux.contracts.config
import soleaux.contracts.structural
import soleaux.structural.engines
import soleaux.structural.fragments
import soleaux.structural.supervisor
import soleaux.structural.worker

REPOSITORY_ROOT = _host_root.require_host_root()
RUST_BINARY = soleaux.structural.engines.managed_rust_binary_path()
NAPI_PACKAGE = REPOSITORY_ROOT / "node_modules" / "@ast-grep" / "napi"
NAPI_JSON_PACKAGE_VERSION = "0.0.7"

_SOURCE = "// π marker\nconsole.log(alpha);\nconsole.log(beta);\n"
_MATCHER = soleaux.contracts.structural.InlinePattern(
    language="TypeScript",
    pattern="console.log($ARG)",
    fix="debug.trace($ARG)",
)


def _protocol_worker(*, identity: dict[str, object] | None = None) -> tuple[str, ...]:
    handshake_identity = (
        {
            "engine": "rust",
            "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
            "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
        }
        if identity is None
        else identity
    )
    script = (
        "import json\n"
        "import os\n"
        "import sys\n"
        f"identity = {handshake_identity!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    reply = {**identity, 'id': request.get('id'), 'ok': True}\n"
        "    if request.get('op') != 'ping':\n"
        "        reply['environment_clean'] = "
        "'SOLEAUX_TEST_UNLISTED_SECRET' not in os.environ\n"
        "    print(json.dumps(reply), flush=True)\n"
    )
    return (sys.executable, "-I", "-c", script)


def _write_repository_node_package(
    root: pathlib.Path,
    *,
    package_name: str,
    version: str,
    marker: pathlib.Path,
) -> None:
    package = root / "node_modules"
    for segment in package_name.split("/"):
        package /= segment
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": package_name,
                "version": version,
                "main": "index.js",
            }
        ),
        encoding="utf-8",
    )
    (package / "index.js").write_text(
        "\n".join(
            (
                'const { writeFileSync } = require("node:fs");',
                f'writeFileSync({json.dumps(str(marker))}, "executed");',
                "module.exports = {};",
                "",
            )
        ),
        encoding="utf-8",
    )


async def _run(
    backend_config: soleaux.contracts.config.StructuralConfig, root: pathlib.Path
) -> soleaux.structural.engines.StructuralOutcome:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    engines = soleaux.structural.engines.StructuralEngines(
        supervisor, root=root, config=backend_config
    )
    try:
        resolved = engines.resolve(_MATCHER)
        return await engines.run(
            resolved,
            files=(("src/a.ts", _SOURCE.encode("utf-8")),),
            want=("findings", "edits"),
        )
    finally:
        await engines.aclose()
        await supervisor.aclose()


async def test_rust_backend_fails_closed_with_install_guidance(tmp_path: pathlib.Path) -> None:
    engines = soleaux.structural.engines.StructuralEngines(
        soleaux.structural.supervisor.StructuralWorkerSupervisor(),
        root=tmp_path,
        config=soleaux.contracts.config.StructuralConfig(backend="rust"),
    )
    resolved = engines.resolve(_MATCHER)
    with pytest.raises(soleaux.structural.engines.StructuralEngineError) as excinfo:
        await engines.run(resolved, files=(("a.ts", b"console.log(1);\n"),))
    await engines.aclose()
    assert excinfo.value.error_type == "engine_unavailable"
    assert soleaux.structural.engines.RUST_INSTALL_COMMAND in excinfo.value.message


def test_repository_controlled_napi_installation_is_rejected_before_execution(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "napi-executed"
    _write_repository_node_package(
        tmp_path,
        package_name="@ast-grep/napi",
        version=soleaux.structural.fragments.AST_GREP_VERSION,
        marker=marker,
    )
    (tmp_path / "soleaux.toml").write_text(
        "\n".join(("[structural]", 'backend = "napi"', 'installation = "node_modules"', "")),
        encoding="utf-8",
    )

    with _assertions.raises_with_message(
        soleaux.contracts.config.ConfigError,
        "installation",
    ):
        soleaux.contracts.config.load_config(tmp_path)

    assert not marker.exists()


def test_repository_controlled_rust_installation_is_rejected_before_execution(
    tmp_path: pathlib.Path,
) -> None:
    marker = tmp_path / "rust-executed"
    worker = tmp_path / soleaux.structural.engines.RUST_WORKER_NAME
    identity = {
        "engine": "rust",
        "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
        "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
    }
    worker.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "import json",
                "import pathlib",
                "import sys",
                f"marker = pathlib.Path({str(marker)!r})",
                'marker.write_text("executed", encoding="utf-8")',
                f"identity = {identity!r}",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    print(",
                "        json.dumps({**identity, 'id': request.get('id'), 'ok': True}),",
                "        flush=True,",
                "    )",
                "",
            )
        ),
        encoding="utf-8",
    )
    worker.chmod(0o755)
    (tmp_path / "soleaux.toml").write_text(
        "\n".join(("[structural]", 'backend = "rust"', 'installation = "."', "")),
        encoding="utf-8",
    )

    with _assertions.raises_with_message(
        soleaux.contracts.config.ConfigError,
        "installation",
    ):
        soleaux.contracts.config.load_config(tmp_path)

    assert not marker.exists()


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param(
            {
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="missing-engine",
        ),
        pytest.param(
            {
                "engine": "napi",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="wrong-engine",
        ),
        pytest.param(
            {
                "engine": "rust",
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="missing-version",
        ),
        pytest.param(
            {
                "engine": "rust",
                "engine_version": "unexpected",
                "capabilities": list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES),
            },
            id="wrong-version",
        ),
        pytest.param(
            {
                "engine": "rust",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
            },
            id="missing-capabilities",
        ),
        pytest.param(
            {
                "engine": "rust",
                "engine_version": soleaux.structural.fragments.AST_GREP_VERSION,
                "capabilities": ["unexpected"],
            },
            id="wrong-capabilities",
        ),
    ],
)
async def test_external_engine_identity_mismatch_fails_closed(
    identity: dict[str, object],
) -> None:
    client = soleaux.structural.engines._JsonlWorkerClient(
        _protocol_worker(identity=identity),
        engine="rust",
        engine_version=soleaux.structural.fragments.AST_GREP_VERSION,
    )
    try:
        with _assertions.raises_with_message(
            soleaux.structural.engines.StructuralEngineError,
            "did not prove the expected 0.44.1 engine/version/capability identity",
        ) as excinfo:
            await client.request(
                {"op": "structural"},
                timeout=soleaux.structural.supervisor.JOB_TIMEOUT_SECONDS,
            )
        assert excinfo.value.error_type == "engine_identity"
    finally:
        await client.aclose()


async def test_external_engine_excludes_unlisted_host_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-reach-engine")
    client = soleaux.structural.engines._JsonlWorkerClient(
        _protocol_worker(),
        engine="rust",
        engine_version=soleaux.structural.fragments.AST_GREP_VERSION,
    )
    try:
        response = await client.request(
            {"op": "structural"},
            timeout=soleaux.structural.supervisor.JOB_TIMEOUT_SECONDS,
        )
        assert response["environment_clean"] is True
    finally:
        await client.aclose()


@pytest.mark.parametrize("backend", ["napi", "rust"])
async def test_configured_external_engines_remain_lazy(
    backend: Literal["napi", "rust"],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = unittest.mock.AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    engines = soleaux.structural.engines.StructuralEngines(
        supervisor,
        root=tmp_path,
        config=soleaux.contracts.config.StructuralConfig(backend=backend),
    )
    try:
        spawn.assert_not_awaited()
    finally:
        await engines.aclose()
        await supervisor.aclose()


@pytest.mark.skipif(not RUST_BINARY.is_file(), reason="rust worker is not built")
async def test_rust_and_python_engines_agree_on_findings_and_edits(tmp_path: pathlib.Path) -> None:
    python_outcome = await _run(
        soleaux.contracts.config.StructuralConfig(backend="python"), tmp_path
    )
    rust_outcome = await _run(
        soleaux.contracts.config.StructuralConfig(backend="rust"),
        tmp_path,
    )

    def _projection(
        outcome: soleaux.structural.engines.StructuralOutcome,
    ) -> list[tuple[object, ...]]:
        findings = outcome.findings
        return [
            (
                finding.path,
                finding.byte_start,
                finding.byte_end,
                finding.start_line,
                finding.start_column,
                tuple((capture.name, capture.text) for capture in finding.captures),
            )
            for finding in findings
        ]

    assert python_outcome.engine is soleaux.contracts.structural.StructuralBackend.PYTHON
    assert rust_outcome.engine is soleaux.contracts.structural.StructuralBackend.RUST
    assert python_outcome.engine_version == soleaux.structural.fragments.AST_GREP_VERSION
    assert rust_outcome.engine_version == soleaux.structural.fragments.AST_GREP_VERSION
    assert _projection(python_outcome) == _projection(rust_outcome)
    python_edits = [
        (edit.path, edit.byte_start, edit.byte_end, edit.inserted_text)
        for edit in python_outcome.edits
    ]
    rust_edits = [
        (edit.path, edit.byte_start, edit.byte_end, edit.inserted_text)
        for edit in rust_outcome.edits
    ]
    assert python_edits == rust_edits
    assert python_edits[0][3] == "debug.trace(alpha)"


@pytest.mark.skipif(not NAPI_PACKAGE.is_dir(), reason="@ast-grep/napi is not installed")
async def test_napi_and_python_engines_agree_on_findings_and_edits(tmp_path: pathlib.Path) -> None:
    python_outcome = await _run(
        soleaux.contracts.config.StructuralConfig(backend="python"), tmp_path
    )
    napi_outcome = await _run(
        soleaux.contracts.config.StructuralConfig(backend="napi"),
        tmp_path,
    )

    def _projection(
        outcome: soleaux.structural.engines.StructuralOutcome,
    ) -> list[tuple[object, ...]]:
        return [
            (
                finding.path,
                finding.byte_start,
                finding.byte_end,
                finding.start_line,
                finding.start_column,
                tuple((capture.name, capture.text) for capture in finding.captures),
            )
            for finding in outcome.findings
        ]

    assert python_outcome.engine is soleaux.contracts.structural.StructuralBackend.PYTHON
    assert napi_outcome.engine is soleaux.contracts.structural.StructuralBackend.NAPI
    assert python_outcome.engine_version == soleaux.structural.fragments.AST_GREP_VERSION
    assert napi_outcome.engine_version == soleaux.structural.fragments.AST_GREP_VERSION
    assert _projection(python_outcome) == _projection(napi_outcome)
    python_edits = [
        (edit.path, edit.byte_start, edit.byte_end, edit.inserted_text)
        for edit in python_outcome.edits
    ]
    napi_edits = [
        (edit.path, edit.byte_start, edit.byte_end, edit.inserted_text)
        for edit in napi_outcome.edits
    ]
    assert python_edits == napi_edits
    assert python_edits[0][3] == "debug.trace(alpha)"


@pytest.mark.skipif(not NAPI_PACKAGE.is_dir(), reason="@ast-grep/napi is not installed")
async def test_napi_ignores_same_version_repository_package_and_process_cwd(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_marker = tmp_path / "napi-executed"
    language_marker = tmp_path / "language-executed"
    _write_repository_node_package(
        tmp_path,
        package_name="@ast-grep/napi",
        version=soleaux.structural.fragments.AST_GREP_VERSION,
        marker=engine_marker,
    )
    _write_repository_node_package(
        tmp_path,
        package_name="@ast-grep/lang-json",
        version=NAPI_JSON_PACKAGE_VERSION,
        marker=language_marker,
    )
    monkeypatch.chdir(tmp_path)

    outcome = await _run(
        soleaux.contracts.config.StructuralConfig(
            backend="napi",
            languages={"Json": "@ast-grep/lang-json"},
        ),
        tmp_path,
    )

    assert outcome.engine is soleaux.contracts.structural.StructuralBackend.NAPI
    assert outcome.engine_version == soleaux.structural.fragments.AST_GREP_VERSION
    assert not engine_marker.exists()
    assert not language_marker.exists()


def test_python_engine_covers_all_seven_upstream_convert_cases() -> None:
    convert = soleaux.structural.worker._convert_text

    assert convert("helloWorld again", "lowerCase") == "helloworld again"
    assert convert("helloWorld", "upperCase") == "HELLOWORLD"
    assert convert("helloWorld", "capitalize") == "HelloWorld"
    assert convert("hello world again", "camelCase") == "helloWorldAgain"
    assert convert("hello world again", "pascalCase") == "HelloWorldAgain"
    assert convert("hello world again", "snakeCase") == "hello_world_again"
    assert convert("hello world again", "kebabCase") == "hello-world-again"
    assert (
        frozenset(
            {
                "lowerCase",
                "upperCase",
                "capitalize",
                "camelCase",
                "pascalCase",
                "snakeCase",
                "kebabCase",
            }
        )
        == soleaux.structural.worker._CONVERT_CASES
    )


def test_napi_error_guidance_names_only_capable_engines() -> None:
    source = (
        REPOSITORY_ROOT / "tools/soleaux/src/soleaux/resources/structural/napi_worker.mjs"
    ).read_text(encoding="utf-8")

    assert "rust or python engine" not in source
    assert source.count("requires the rust engine") >= 4
