"""Construction is analyzer-free; lifespan-owned analysis is published and reaped."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import _processes
import pytest
from fastmcp import Client

from soleaux.analysis.service import SoleauxService
from soleaux.contracts.config import McpBackendConfig, ResolvedConfig
from soleaux.contracts.requests import SearchRequest, SemanticMode
from soleaux.server import create_server


def _environment() -> dict[str, str]:
    return _processes.minimum_environment()


def _mcp_process_fixture(tmp_path: Path) -> tuple[ResolvedConfig, Path, Path]:
    marker = tmp_path / "gateway-pids.txt"
    backend = tmp_path / "gateway_backend.py"
    backend.write_text(
        textwrap.dedent(
            """
            import os
            import subprocess
            import sys
            import time

            from fastmcp import FastMCP

            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            with open(os.environ["SOLEAUX_GATEWAY_PID_MARKER"], "w", encoding="utf-8") as output:
                output.write(f"{os.getpid()} {child.pid}\\n")
                output.flush()

            server = FastMCP("gateway-process-tree")

            @server.tool
            def identity() -> int:
                return os.getpid()

            server.run(show_banner=False)
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    config = ResolvedConfig(
        mcp={
            "tree": McpBackendConfig(
                command=[sys.executable, str(backend)],
                env={"SOLEAUX_GATEWAY_PID_MARKER": str(marker)},
                lifecycle="session",
                request_timeout_seconds=10,
                init_timeout_seconds=10,
            )
        }
    )
    return config, marker, backend


async def _mcp_pids(marker: Path) -> tuple[int, int]:
    for _ in range(100):
        if marker.exists():
            parent, child = marker.read_text(encoding="utf-8").split()
            return int(parent), int(child)
        await asyncio.sleep(0.01)
    raise AssertionError("MCP backend did not record its process tree")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _assert_processes_exit(*pids: int) -> None:
    # Session teardown serializes stdin-close grace, SIGTERM, and tree-kill
    # (~2s each in mcp stdio); under full-suite load 4s is not enough.
    for _ in range(750):
        if not any(_process_exists(pid) for pid in pids):
            return
        await asyncio.sleep(0.02)
    surviving = [pid for pid in pids if _process_exists(pid)]
    raise AssertionError(f"gateway process tree survived shutdown: {surviving}")


def _assert_owned_process_tree(parent: int, child: int, backend: Path) -> None:
    command = subprocess.run(
        ["ps", "-p", str(parent), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    descendants = subprocess.run(
        ["pgrep", "-P", str(parent)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert str(backend) in command
    assert str(child) in descendants


def test_import_is_analyzer_free_and_lifespan_publishes_once(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("value = 42\n", encoding="utf-8")
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import pathlib
        import subprocess
        import sys

        source_reads = []
        original_read_bytes = pathlib.Path.read_bytes

        def tracked_read_bytes(path):
            if path.suffix in {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go"}:
                source_reads.append(str(path))
            return original_read_bytes(path)

        pathlib.Path.read_bytes = tracked_read_bytes

        def children():
            return subprocess.run(
                ["pgrep", "-P", str(__import__("os").getpid())],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.splitlines()

        async def run():
            import soleaux.server
            from fastmcp import Client

            reads_after_import = list(source_reads)
            children_after_import = children()
            ast_grep_after_import = "ast_grep" in sys.modules or "ast_grep_py" in sys.modules
            async with Client(soleaux.server.mcp) as client:
                reads_after_start = list(source_reads)
                tools = await client.list_tools()
                about = await client.read_resource("soleaux://about")
                context = await client.call_tool(
                    "context",
                    {"request": {"objective": "explain value", "paths": ["main.py"]}},
                )
                reads_after_requests = list(source_reads)
            print(json.dumps({
                "tools": len(tools),
                "about_reads": len(about),
                "context_status": context.structured_content["status"],
                "reads_after_import": reads_after_import,
                "reads_after_start": reads_after_start,
                "reads_after_requests": reads_after_requests,
                "ast_grep_after_import": ast_grep_after_import,
                "ast_grep_imported": "ast_grep" in sys.modules or "ast_grep_py" in sys.modules,
                "children_after_import": children_after_import,
                "children_after_close": children(),
            }))

        asyncio.run(run())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    published_reads = [str(tmp_path / "main.py")]
    assert payload["tools"] == 10
    assert payload["about_reads"] == 1
    assert payload["context_status"] == "ok"
    assert payload["reads_after_import"] == []
    assert payload["reads_after_start"] == published_reads
    assert payload["reads_after_requests"] == published_reads
    assert payload["ast_grep_after_import"] is False
    assert payload["ast_grep_imported"] is False
    assert payload["children_after_import"] == []
    assert payload["children_after_close"] == []


async def test_catalog_reconciler_reuses_one_worker_and_shutdown_reaps_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    request = SearchRequest(query="answer", semantic_mode=SemanticMode.SYNTAX_ONLY)

    async with SoleauxService.from_root(tmp_path) as service:
        first, second = await asyncio.gather(service.search(request), service.search(request))
        assert first.status.value == "ok"
        assert second.status.value == "ok"
        await service._catalog_indexer.settle()
        worker_pid = service.structural_worker_pid
        assert worker_pid is not None
        jobs_after_reconciliation = service.structural_completed_jobs
        third = await service.search(request)
        assert third.status.value == "ok"
        assert service.structural_completed_jobs == jobs_after_reconciliation

    for _ in range(50):
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(f"structural worker {worker_pid} survived service shutdown")


async def test_failed_catalog_reconciler_does_not_block_worker_shutdown(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "def answer() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    service = SoleauxService.from_root(tmp_path)
    worker_pid: int | None = None
    try:
        await service.start()
        await service._catalog_indexer.settle()
        worker_pid = service.structural_worker_pid
        assert worker_pid is not None

        reconciler = service._catalog_indexer._task
        assert reconciler is not None
        reconciler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reconciler

        async def injected_failure() -> None:
            raise RuntimeError("injected reconciler failure")

        failed = asyncio.create_task(injected_failure())
        await asyncio.sleep(0)
        assert isinstance(failed.exception(), RuntimeError)
        service._catalog_indexer._task = failed

        await service.aclose()
    finally:
        await service.aclose()

    assert worker_pid is not None
    for _ in range(50):
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(f"structural worker {worker_pid} survived failed reconciler shutdown")


async def test_session_mcp_reaps_backend_and_descendant_at_session_exit(
    tmp_path: Path,
) -> None:
    config, marker, backend = _mcp_process_fixture(tmp_path)

    async with Client(create_server(tmp_path, config=config), mode="legacy") as client:
        tools = {tool.name for tool in await client.list_tools()}
        assert "tree_identity" in tools
        parent, child = await _mcp_pids(marker)
        assert _process_exists(parent)
        assert _process_exists(child)
        _assert_owned_process_tree(parent, child, backend)

    await _assert_processes_exit(parent, child)


async def test_cancelled_downstream_session_reaps_mcp_process_tree(tmp_path: Path) -> None:
    config, marker, backend = _mcp_process_fixture(tmp_path)
    server = create_server(tmp_path, config=config)
    ready = asyncio.Event()
    pids: list[int] = []

    async def connected() -> None:
        async with Client(server, mode="legacy") as client:
            tools = {tool.name for tool in await client.list_tools()}
            assert "tree_identity" in tools
            parent, child = await _mcp_pids(marker)
            pids.extend((parent, child))
            _assert_owned_process_tree(parent, child, backend)
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(connected())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(pids) == 2
    await _assert_processes_exit(*pids)
