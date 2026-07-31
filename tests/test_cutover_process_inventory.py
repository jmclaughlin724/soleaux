"""Real Soleaux launch surfaces isolate protocol output and reap owned processes."""

from __future__ import annotations

import asyncio
import collections.abc
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
import typing

import _assertions
import _host_root
import _processes
import fastmcp
import fastmcp.client.client
import fastmcp.client.transports
import mcp_types
import pydantic

import soleaux.surface

type Catalog = dict[str, tuple[dict[str, object], ...]]
type ConfigKind = typing.Literal["missing", "empty", "configured"]
type FailureMode = typing.Literal["stdout_noise", "hang"]
type LaunchSurface = typing.Literal["console", "fastmcp_json"]

REPOSITORY_ROOT = _host_root.require_host_root()
SOLEAUX_ROOT = REPOSITORY_ROOT / "tools" / "soleaux"
FASTMCP_MANIFEST = SOLEAUX_ROOT / "fastmcp.json"
FAKE_MCP = pathlib.Path(__file__).parent / "fixtures" / "mcp" / "fake_mcp.py"
CONSOLE_COMMAND = pathlib.Path(sys.executable).with_name("soleaux")
FASTMCP_COMMAND = REPOSITORY_ROOT / ".venv" / "bin" / "fastmcp"
PROCESS_EXIT_TIMEOUT_SECONDS = 5.0
PROTOCOL_TIMEOUT_SECONDS = 15.0
FAILURE_BOUND_SECONDS = 15.0
FAILURE_INIT_TIMEOUT_SECONDS = 5.0
MAX_STDERR_BYTES = 64 * 1024


@dataclasses.dataclass(frozen=True)
class FixtureProcess:
    pid: int
    ppid: int
    mode: str


@dataclasses.dataclass(frozen=True)
class LaunchResult:
    catalog: Catalog
    fixture_processes: tuple[FixtureProcess, ...]
    outer_pids: tuple[int, ...]
    stderr: str


def _direct_child_pids() -> tuple[int, ...]:
    result = subprocess.run(
        ("pgrep", "-P", str(os.getpid())),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return tuple(int(pid) for pid in result.stdout.splitlines())


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_exit(pids: collections.abc.Sequence[int]) -> None:
    deadline = time.monotonic() + PROCESS_EXIT_TIMEOUT_SECONDS
    remaining = tuple(pid for pid in pids if _pid_exists(pid))
    while remaining and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
        remaining = tuple(pid for pid in remaining if _pid_exists(pid))
    assert remaining == (), f"owned MCP processes survived teardown: {remaining}"


def _fixture_processes(path: pathlib.Path) -> tuple[FixtureProcess, ...]:
    if not path.exists():
        return ()
    records: list[FixtureProcess] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = _assertions.object_mapping(json.loads(line))
        assert payload.get("event") == "start"
        pid = payload.get("pid")
        ppid = payload.get("ppid")
        mode = payload.get("mode")
        assert isinstance(pid, int)
        assert isinstance(ppid, int)
        assert isinstance(mode, str)
        records.append(FixtureProcess(pid=pid, ppid=ppid, mode=mode))
    return tuple(records)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _write_mcp_config(root: pathlib.Path, mode: str, pid_log: pathlib.Path) -> None:
    request_timeout_seconds = 10.0 if mode == "normal" else 2.0
    init_timeout_seconds = 10.0 if mode == "normal" else FAILURE_INIT_TIMEOUT_SECONDS
    root.joinpath("soleaux.toml").write_text(
        "\n".join(
            (
                "[mcp.fixture]",
                f"command = [{_toml_string(sys.executable)}, {_toml_string(str(FAKE_MCP))}]",
                'lifecycle = "session"',
                "cache_ttl_seconds = 300.0",
                f"request_timeout_seconds = {request_timeout_seconds}",
                f"init_timeout_seconds = {init_timeout_seconds}",
                "",
                "[mcp.fixture.env]",
                f"SOLEAUX_TEST_MCP_MODE = {_toml_string(mode)}",
                f"SOLEAUX_TEST_MCP_PID_LOG = {_toml_string(str(pid_log))}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _workspace_root(base: pathlib.Path, kind: ConfigKind, *, mode: str = "normal") -> pathlib.Path:
    root = base / kind
    root.mkdir(parents=True)
    root.joinpath(".git").mkdir()
    root.joinpath("main.py").write_text("value = 42\n", encoding="utf-8")
    if kind == "empty":
        root.joinpath("soleaux.toml").write_text("\n", encoding="utf-8")
    elif kind == "configured":
        _write_mcp_config(root, mode, root / "fixture-processes.jsonl")
    return root


def _write_declarative_manifest(root: pathlib.Path) -> pathlib.Path:
    original = _assertions.object_mapping(json.loads(FASTMCP_MANIFEST.read_text(encoding="utf-8")))
    source_value = original.get("source")
    deployment_value = original.get("deployment")
    source = _assertions.object_mapping(source_value)
    deployment = _assertions.object_mapping(deployment_value)
    source_path = source.get("path")
    assert isinstance(source_path, str)
    source["path"] = str((FASTMCP_MANIFEST.parent / source_path).resolve())
    deployment["cwd"] = str(root.resolve())
    original["source"] = source
    original["deployment"] = deployment

    manifest = root / "fastmcp.json"
    manifest.write_text(f"{json.dumps(original, indent=2, sort_keys=True)}\n", encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8")) == original
    return manifest


def _test_environment(blocker: pathlib.Path, uv_marker: pathlib.Path) -> dict[str, str]:
    blocker.mkdir()
    blocker.joinpath("uv").write_text(
        '#!/bin/sh\n: > "${SOLEAUX_TEST_UV_MARKER:?}"\nexit 97\n',
        encoding="utf-8",
    )
    blocker.joinpath("uv").chmod(0o755)
    environment = _processes.minimum_environment()
    environment.update(
        {
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "FASTMCP_LOG_LEVEL": "CRITICAL",
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "PATH": f"{blocker}{os.pathsep}{environment.get('PATH', '')}",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOLEAUX_TEST_UV_MARKER": str(uv_marker),
        }
    )
    return environment


def _component_payloads(
    components: collections.abc.Sequence[pydantic.BaseModel],
) -> tuple[dict[str, object], ...]:
    payloads = tuple(component.model_dump(mode="json", by_alias=True) for component in components)
    return tuple(
        sorted(
            payloads,
            key=lambda payload: json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
    )


async def _complete_catalog(
    client: fastmcp.Client[fastmcp.client.transports.StdioTransport],
) -> Catalog:
    return {
        "tools": _component_payloads(await client.list_tools()),
        "resources": _component_payloads(await client.list_resources()),
        "resource_templates": _component_payloads(await client.list_resource_templates()),
        "prompts": _component_payloads(await client.list_prompts()),
    }


async def _tool_catalog(
    client: fastmcp.Client[fastmcp.client.transports.StdioTransport],
) -> Catalog:
    return {
        "tools": _component_payloads(await client.list_tools()),
        "resources": (),
        "resource_templates": (),
        "prompts": (),
    }


def _tool_data(result: fastmcp.client.client.CallToolResult) -> dict[str, object]:
    return _assertions.object_mapping(result.data)


async def _exercise_configured_mcp(
    client: fastmcp.Client[fastmcp.client.transports.StdioTransport],
) -> int:
    first_state = _tool_data(await client.call_tool("fixture_state", {}))
    second_state = _tool_data(await client.call_tool("fixture_state", {}))
    assert first_state["count"] == 1
    assert second_state["count"] == 2
    assert first_state["pid"] == second_state["pid"]
    assert first_state["session_id"] == second_state["session_id"]
    backend_pid = first_state["pid"]
    assert isinstance(backend_pid, int)

    callback_status = _tool_data(await client.call_tool("fixture_callback_probe", {}))
    assert set(callback_status) == {"elicitation", "roots", "sampling"}
    assert all(status != "forwarded" for status in callback_status.values())

    fixed = await client.read_resource("data://fixture/fixed")
    templated = await client.read_resource("data://fixture/items/alpha")
    assert isinstance(fixed[0], mcp_types.TextResourceContents)
    assert isinstance(templated[0], mcp_types.TextResourceContents)
    assert fixed[0].text == "fixed"
    assert json.loads(templated[0].text) == {"item_id": "alpha"}

    prompt = await client.get_prompt("fixture_summarize", {"topic": "catalog"})
    assert len(prompt.messages) == 1
    assert isinstance(prompt.messages[0].content, mcp_types.TextContent)
    assert prompt.messages[0].content.text == "Summarize catalog"
    return backend_pid


def _launch_command(surface: LaunchSurface, root: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    if surface == "console":
        return CONSOLE_COMMAND, ["--root", str(root)]
    manifest = _write_declarative_manifest(root)
    return FASTMCP_COMMAND, [
        "run",
        str(manifest),
        "--skip-env",
        "--log-level",
        "CRITICAL",
        "--no-banner",
    ]


async def _launch(
    surface: LaunchSurface,
    root: pathlib.Path,
    *,
    configured: bool,
    mode: str,
    complete_catalog: bool,
) -> LaunchResult:
    command, arguments = _launch_command(surface, root)
    assert command.is_file()
    assert os.access(command, os.X_OK)

    pid_log = root / "fixture-processes.jsonl"
    before_processes = _fixture_processes(pid_log)
    uv_marker = root / f"{surface}-uv-invoked"
    stderr_path = root / f"{surface}-{mode}.stderr.log"
    environment = _test_environment(root / f"{surface}-blocked-bin", uv_marker)
    baseline_children = set(_direct_child_pids())
    transport = fastmcp.client.transports.StdioTransport(
        command=str(command),
        args=arguments,
        env=environment,
        cwd=str(root),
        keep_alive=False,
        log_file=stderr_path,
    )

    outer_pids: tuple[int, ...] = ()
    backend_pid: int | None = None
    catalog: Catalog = {}
    try:
        async with asyncio.timeout(PROTOCOL_TIMEOUT_SECONDS):
            async with fastmcp.Client(
                transport,
                mode="legacy",
                timeout=PROTOCOL_TIMEOUT_SECONDS,
                init_timeout=PROTOCOL_TIMEOUT_SECONDS,
            ) as client:
                outer_pids = tuple(sorted(set(_direct_child_pids()) - baseline_children))
                assert len(outer_pids) == 1, (
                    f"{surface} must own one direct stdio process, found {outer_pids}"
                )
                catalog = (
                    await _complete_catalog(client)
                    if complete_catalog
                    else await _tool_catalog(client)
                )
                if configured and mode == "normal":
                    backend_pid = await _exercise_configured_mcp(client)
    finally:
        all_processes = _fixture_processes(pid_log)
        assert all_processes[: len(before_processes)] == before_processes
        new_processes = all_processes[len(before_processes) :]
        await _wait_for_pid_exit(
            (*outer_pids, *(process.pid for process in new_processes)),
        )

    all_processes = _fixture_processes(pid_log)
    new_processes = all_processes[len(before_processes) :]
    if configured:
        assert new_processes
        assert {process.mode for process in new_processes} == {mode}
        assert all(process.ppid in outer_pids for process in new_processes)
    else:
        assert new_processes == ()
    if backend_pid is not None:
        assert backend_pid in {process.pid for process in new_processes}

    assert not uv_marker.exists(), f"{surface} unexpectedly invoked uv"
    stderr = stderr_path.read_text(encoding="utf-8")
    assert len(stderr.encode()) <= MAX_STDERR_BYTES
    sentinel = f"fixture-stderr mode={mode}"
    if configured:
        assert sentinel in stderr
    else:
        assert sentinel not in stderr
    return LaunchResult(
        catalog=catalog,
        fixture_processes=new_processes,
        outer_pids=outer_pids,
        stderr=stderr,
    )


def _catalog_values(catalog: Catalog, kind: str, field: str) -> set[str]:
    values: set[str] = set()
    for component in catalog[kind]:
        value = component[field]
        assert isinstance(value, str)
        values.add(value)
    return values


def test_retired_launchers_have_no_repository_owner() -> None:
    owner_paths = (
        ".codex/config.toml",
        "opencode.json",
        "package.json",
        "pnpm-workspace.yaml",
        "pnpm-lock.yaml",
    )
    combined = "\n".join(
        REPOSITORY_ROOT.joinpath(path).read_text(encoding="utf-8") for path in owner_paths
    )
    for retired in (
        "codeatlas-mcp",
        "@codeatlas/mcp",
        '"cclsp"',
        "mcp-server.mjs",
        "test-mcp.py",
    ):
        assert retired not in combined
    assert not (REPOSITORY_ROOT / ".codeatlas").exists()
    assert not (REPOSITORY_ROOT / ".codeatlas-sa").exists()


async def test_console_and_fastmcp_json_match_the_complete_root_matrix(
    tmp_path: pathlib.Path,
) -> None:
    catalogs: dict[ConfigKind, Catalog] = {}
    for kind in ("missing", "empty", "configured"):
        root = _workspace_root(tmp_path / "matrix", kind)
        launches = {
            surface: await _launch(
                surface,
                root,
                configured=kind == "configured",
                mode="normal",
                complete_catalog=True,
            )
            for surface in ("console", "fastmcp_json")
        }
        assert launches["console"].catalog == launches["fastmcp_json"].catalog
        catalogs[kind] = launches["console"].catalog

    assert catalogs["missing"] == catalogs["empty"]
    expected_local_tools = set(soleaux.surface.tool_names())
    expected_local_resources = set(soleaux.surface.resource_uris())
    for kind in ("missing", "empty"):
        assert _catalog_values(catalogs[kind], "tools", "name") == expected_local_tools
        assert _catalog_values(catalogs[kind], "resources", "uri") == expected_local_resources
        assert catalogs[kind]["resource_templates"] == ()
        assert catalogs[kind]["prompts"] == ()

    configured = catalogs["configured"]
    assert _catalog_values(configured, "tools", "name") == expected_local_tools | {
        "fixture_callback_probe",
        "fixture_echo",
        "fixture_state",
    }
    assert _catalog_values(configured, "resources", "uri") == expected_local_resources | {
        "data://fixture/fixed"
    }
    assert _catalog_values(configured, "resource_templates", "uriTemplate") == {
        "data://fixture/items/{item_id}"
    }
    assert _catalog_values(configured, "prompts", "name") == {"fixture_summarize"}


async def test_mcp_failures_are_bounded_and_outer_stdout_stays_protocol_only(
    tmp_path: pathlib.Path,
) -> None:
    expected_local_tools = set(soleaux.surface.tool_names())
    for mode in ("stdout_noise", "hang"):
        mode_catalogs: dict[LaunchSurface, Catalog] = {}
        for surface in ("console", "fastmcp_json"):
            root = _workspace_root(
                tmp_path / "failures" / mode / surface,
                "configured",
                mode=mode,
            )
            started = time.monotonic()
            result = await _launch(
                surface,
                root,
                configured=True,
                mode=mode,
                complete_catalog=False,
            )
            assert time.monotonic() - started < FAILURE_BOUND_SECONDS
            assert _catalog_values(result.catalog, "tools", "name") == expected_local_tools
            mode_catalogs[surface] = result.catalog
        assert mode_catalogs["console"] == mode_catalogs["fastmcp_json"]
