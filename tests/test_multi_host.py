"""Independent stdio hosts own isolated process epochs and exit cleanly."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import _processes
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StdioTransport


def _payload(result: CallToolResult) -> dict[str, object]:
    assert result.structured_content is not None
    return result.structured_content


def _children() -> tuple[int, ...]:
    result = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return tuple(int(line) for line in result.stdout.splitlines() if line.strip())


async def _host_snapshot(root: Path) -> str:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "soleaux"],
        env=_processes.minimum_environment(),
        cwd=str(root),
        keep_alive=False,
    )
    async with Client(transport) as client:
        payload = _payload(await client.call_tool("search", {"request": {"query": "value"}}))
        assert payload["status"] == "ok"
        snapshot_id = payload["snapshot_id"]
        assert isinstance(snapshot_id, str)
        return snapshot_id


async def test_three_stdio_hosts_are_isolated_and_leave_no_descendants(tmp_path: Path) -> None:
    roots = tuple(tmp_path / f"host-{index}" for index in range(3))
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "main.py").write_text(f"value = {index}\n", encoding="utf-8")

    snapshots = await asyncio.gather(*(_host_snapshot(root) for root in roots))

    assert len(set(snapshots)) == 3
    # Cold CI runners can take several seconds to reap stdio children.
    for _ in range(100):
        if _children() == ():
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError(f"stdio hosts survived the ten-second shutdown grace: {_children()}")
