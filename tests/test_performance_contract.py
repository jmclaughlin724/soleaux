"""Measured budgets and keyed work sharing back the performance contract."""

from __future__ import annotations

import asyncio
import json
import pathlib

import _assertions
import pytest

import soleaux.analysis.budgets
import soleaux.analysis.task_registry
import soleaux.postgresql.runtime


def _mapping(value: object) -> dict[str, object]:
    return _assertions.object_mapping(value)


def test_nearest_rank_p95_and_declared_sample_policies() -> None:
    assert (
        soleaux.analysis.budgets.nearest_rank_percentile(
            tuple(float(value) for value in range(1, 21)), 0.95
        )
        == 19
    )
    assert (
        soleaux.analysis.budgets.CHEAP_SAMPLE_POLICY.warmups,
        soleaux.analysis.budgets.CHEAP_SAMPLE_POLICY.samples,
    ) == (5, 30)
    assert (
        soleaux.analysis.budgets.REAL_LSP_SAMPLE_POLICY.warmups,
        soleaux.analysis.budgets.REAL_LSP_SAMPLE_POLICY.samples,
    ) == (2, 10)


async def test_provider_version_probe_uses_minimum_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setenv("SOLEAUX_TEST_UNLISTED_SECRET", "must-not-propagate")

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"provider 1.0\n", b""

    async def create_process(*argv: str, **options: object) -> Process:
        observed["argv"] = argv
        observed.update(options)
        return Process()

    monkeypatch.setattr(
        soleaux.analysis.budgets.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    output, error = await soleaux.analysis.budgets._capture_process(
        ("provider", "--version"),
        cwd=tmp_path,
        environment_names=("GOCACHE",),
        provider_environment={"GOCACHE": str(tmp_path / "go-cache")},
        timeout_seconds=1,
    )

    assert (output, error) == ("provider 1.0", None)
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment == soleaux.postgresql.runtime.build_safe_environment(
        {"GOCACHE": str(tmp_path / "go-cache")},
        environment_names=("GOCACHE",),
    )
    assert "SOLEAUX_TEST_UNLISTED_SECRET" not in environment


async def test_task_registry_shares_results_errors_and_cancels_final_waiter() -> None:
    registry = soleaux.analysis.task_registry.TaskRegistry()
    calls = 0
    release = asyncio.Event()

    async def work() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "shared"

    first = asyncio.create_task(registry.share(("epoch", "same"), work))
    second = asyncio.create_task(registry.share(("epoch", "same"), work))
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(first, second) == ["shared", "shared"]
    assert calls == 1

    async def fail() -> None:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError):
        await registry.share("failed", fail)
    with pytest.raises(RuntimeError):
        await registry.share("failed", fail)

    cancelled = asyncio.Event()

    async def wait_forever() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    waiter = asyncio.create_task(registry.share("cancel", wait_forever))
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.wait_for(cancelled.wait(), timeout=0.25)
    assert (asyncio.get_running_loop().time() - started) * 1000 <= 250
    assert registry.in_flight == 0


async def test_benchmark_reports_real_samples_resources_and_gates(tmp_path: pathlib.Path) -> None:
    (tmp_path / "main.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    from fastmcp import Client

    from soleaux.analysis.budgets import benchmark_report
    from soleaux.analysis.service import product_version
    from soleaux.server import create_server

    async with Client(create_server(tmp_path)) as client:

        async def read_about() -> object:
            return await client.read_resource("soleaux://about")

        report = _mapping(
            await benchmark_report(
                root=tmp_path,
                startup_probe=read_about,
                product_version=product_version(),
            )
        )

    assert report["schema_version"] == "soleaux.benchmark/v1"
    policies = _mapping(report["sample_policy"])
    assert _mapping(policies["cheap"]) == {"warmups": 5, "samples": 30}
    assert _mapping(policies["real_language_server"]) == {"warmups": 2, "samples": 10}
    scenarios = _mapping(report["scenarios"])
    startup_probe = _mapping(scenarios["startup_probe"])
    structural = _mapping(scenarios["warm_structural_2kib"])
    assert startup_probe["sample_count"] == 30
    assert structural["sample_count"] == 30
    resources = _mapping(report["resource_usage"])
    assert isinstance(resources["rss_delta_bytes"], int)
    assert resources["file_count"] == 101
    assert isinstance(resources["producer_invocations"], int)
    assert isinstance(resources["spawned_pids"], list)
    assert report["passed"] is True, json.dumps(
        {
            "scenarios": report["scenarios"],
            "resource_usage": report["resource_usage"],
        },
        sort_keys=True,
    )
