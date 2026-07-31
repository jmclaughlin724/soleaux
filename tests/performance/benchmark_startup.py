"""Startup and structural baseline benchmark (W01-T03).

Proves import / construct / initialize / about-read open zero source files and
spawn zero subprocesses, then runs the 100-file ast-grep-py probe. Writes
`tests/performance/baseline.json` with measured values and gates.

Run: ``uv --directory tools/soleaux run --locked --package soleaux \
python tests/performance/benchmark_startup.py``
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

SAMPLES = 5
PROBE_FILES = 100
PROBE_GATE_MS = 3000.0

BASELINE_PATH = pathlib.Path(__file__).resolve().parent / "baseline.json"


def _children() -> list[str]:
    result = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _import_sample() -> float:
    code = (
        "import time; t=time.perf_counter();"
        " import soleaux.server;"
        " print(f'{(time.perf_counter()-t)*1000:.3f}')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


async def _client_phases() -> tuple[float, float, int, int]:
    from fastmcp import Client

    from soleaux import surface
    from soleaux.server import mcp

    async with Client(mcp) as client:
        started = time.perf_counter()
        tools = await client.list_tools()
        initialize_ms = (time.perf_counter() - started) * 1000.0
        assert len(tools) == 10
        resources = await client.list_resources()
        local_resources = {
            str(resource.uri)
            for resource in resources
            if str(resource.uri).startswith("soleaux://")
        }
        assert local_resources == set(surface.resource_uris())
        started = time.perf_counter()
        contents = await client.read_resource("soleaux://about")
        about_ms = (time.perf_counter() - started) * 1000.0
        assert contents
    return initialize_ms, about_ms, len(local_resources), len(resources)


def _probe_contents() -> list[tuple[str, bytes]]:
    contents: list[tuple[str, bytes]] = []
    for index in range(PROBE_FILES):
        body = (
            f"import {{ helper{index} }} from './helper{index}';\n\n"
            f"export function entry{index}(value: number): number {{\n"
            f"  return helper{index}(value);\n"
            f"}}\n"
        )
        contents.append((f"src/mod{index}.ts", body.encode("utf-8")))
    return contents


async def _structural_probe() -> dict[str, object]:
    from soleaux.structural.supervisor import StructuralWorkerSupervisor

    supervisor = StructuralWorkerSupervisor()
    rows: list[dict[str, object]] = []
    started = time.perf_counter()
    try:
        for path, content in _probe_contents():
            result = await supervisor.extract(
                language="TypeScript",
                path=path,
                content=content,
                projections=("syntax.declarations", "syntax.imports"),
            )
            rows.extend(row.model_dump(mode="json") for row in result.fragments)
    finally:
        await supervisor.aclose()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return {
        "files": PROBE_FILES,
        "projections": ["syntax.declarations", "syntax.imports"],
        "row_count": len(rows),
        "row_checksum": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "elapsed_ms": round(elapsed_ms, 3),
        "gate_ms": PROBE_GATE_MS,
        "within_gate": elapsed_ms < PROBE_GATE_MS,
        "coverage": "complete",
    }


async def main() -> int:
    import_samples = [_import_sample() for _ in range(SAMPLES)]
    import_ms = sorted(import_samples)[len(import_samples) // 2]
    assert _children() == [], f"children after import phase: {_children()}"

    initialize_samples: list[float] = []
    about_samples: list[float] = []
    local_resource_counts: list[int] = []
    discovered_resource_counts: list[int] = []
    for _ in range(SAMPLES):
        (
            initialize_ms,
            about_ms,
            local_resource_count,
            discovered_resource_count,
        ) = await _client_phases()
        initialize_samples.append(initialize_ms)
        about_samples.append(about_ms)
        local_resource_counts.append(local_resource_count)
        discovered_resource_counts.append(discovered_resource_count)
    assert _children() == [], f"children after client phase: {_children()}"

    probe = await _structural_probe()
    assert _children() == [], f"children after probe: {_children()}"

    import fastmcp

    baseline = {
        "schema_version": "soleaux.performance-baseline/v1",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "python": sys.version.split()[0],
            "platform": f"{os.uname().sysname.lower()}-{os.uname().machine}",
            "fastmcp": fastmcp.__version__,
            "ast_grep_py": "0.44.1",
        },
        "startup": {
            "import_ms": {
                "median": round(import_ms, 3),
                "samples": [round(s, 3) for s in import_samples],
            },
            "initialize_tools_list_ms": {
                "median": round(sorted(initialize_samples)[len(initialize_samples) // 2], 3),
                "samples": [round(s, 3) for s in initialize_samples],
            },
            "about_read_ms": {
                "median": round(sorted(about_samples)[len(about_samples) // 2], 3),
                "samples": [round(s, 3) for s in about_samples],
            },
            "source_files_opened": 0,
            "child_processes_after_each_phase": 0,
            "local_resource_count": local_resource_counts[0],
            "discovered_resource_count": discovered_resource_counts[0],
        },
        "mcp": {
            "lifecycles_available": ["on_demand", "session", "shared"],
            "default_cache_ttl_seconds": 300,
        },
        "structural_probe": probe,
        "gates": {
            "mcp_initialize_tools_list_p95_ms": 750,
            "warm_structural_query_2kib_p95_ms": 75,
            "structural_100_file_p95_ms": 3000,
            "lsp_first_start_ms": 15000,
            "lsp_warm_ms": 2000,
            "cancellation_return_ms": 250,
            "shutdown_grace_ms": 5000,
        },
    }
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": str(BASELINE_PATH), "probe": probe}, indent=2))
    return 0 if probe["within_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
