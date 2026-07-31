"""Doctor and benchmark contracts backed by measured work-avoidance budgets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import resource
import shutil
import signal
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pydantic

from soleaux.contracts.budget import (
    LspSessionBudget,
    PackagedRuleLimits,
    RequestBudget,
    StructuralWorkerBudget,
)
from soleaux.contracts.config import CONFIG_FILENAME, ResolvedConfig
from soleaux.contracts.tables import TABLE_CATALOG, Producer, SemanticRequirement
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.lsp.contracts import NavigationRequest, SemanticOperation
from soleaux.lsp.providers import (
    ConfiguredProvider,
    ProviderRegistry,
    resolve_provider_executable,
)
from soleaux.lsp.resolvers import SemanticResolver
from soleaux.postgresql.node_runtime import managed_parser_version
from soleaux.postgresql.runtime import build_safe_environment
from soleaux.structural.snapshot import RepositorySnapshotter
from soleaux.structural.supervisor import StructuralWorkerSupervisor
from soleaux.typescript.contracts import NATIVE_TYPESCRIPT_VERSION, TS_MORPH_VERSION
from soleaux.typescript.node_runtime import (
    configured_typescript_prefix,
    resolve_typescript_installation,
)

STARTUP_P95_MS = 750.0
STARTUP_RSS_DELTA_BYTES = 75 * 1024 * 1024
WARM_STRUCTURAL_P95_MS = 75.0
STRUCTURAL_100_FILE_P95_MS = 3000.0
LSP_FIRST_START_MS = 15_000.0
LSP_WARM_P95_MS = 2000.0
CANCELLATION_RETURN_MS = 250
SHUTDOWN_GRACE_MS = 5000


class SamplePolicy(pydantic.BaseModel):
    """Fixed warmup and measured-sample counts for one scenario class."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    warmups: int = pydantic.Field(ge=0)
    samples: int = pydantic.Field(ge=1)


class TimingSummary(pydantic.BaseModel):
    """Deterministic timing summary using nearest-rank p95."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    warmups: int = pydantic.Field(ge=0)
    sample_count: int = pydantic.Field(ge=1)
    median_ms: float = pydantic.Field(ge=0)
    p95_ms: float = pydantic.Field(ge=0)
    samples_ms: tuple[float, ...]


CHEAP_SAMPLE_POLICY = SamplePolicy(warmups=5, samples=30)
REAL_LSP_SAMPLE_POLICY = SamplePolicy(warmups=2, samples=10)
HUNDRED_FILE_SAMPLE_POLICY = SamplePolicy(warmups=1, samples=3)


@dataclass(frozen=True, slots=True)
class _ProviderBenchmarkCase:
    name: str
    files: tuple[tuple[str, str], ...]
    source_files: tuple[str, ...]
    request_path: str
    line: int
    column: int


_PROVIDER_CASES = (
    _ProviderBenchmarkCase(
        name="typescript-language-server",
        files=(
            ("dep.ts", "export const answer = (): number => 42;\n"),
            ("main.ts", 'import { answer } from "./dep.js";\n\nanswer();\n'),
            (
                "tsconfig.json",
                '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext",'
                '"strict":true,"target":"ES2022"},"include":["*.ts"]}\n',
            ),
        ),
        source_files=("dep.ts", "main.ts", "tsconfig.json"),
        request_path="main.ts",
        line=3,
        column=2,
    ),
    _ProviderBenchmarkCase(
        name="pylsp",
        files=(
            ("dep.py", "def answer() -> int:\n    return 42\n"),
            ("main.py", "from dep import answer\n\nanswer()\n"),
            (
                "pyproject.toml",
                '[project]\nname = "soleaux-benchmark"\nversion = "0.0.0"\n'
                'requires-python = ">=3.14"\n',
            ),
        ),
        source_files=("dep.py", "main.py", "pyproject.toml"),
        request_path="main.py",
        line=3,
        column=2,
    ),
    _ProviderBenchmarkCase(
        name="gopls",
        files=(
            ("dep.go", "package fixture\n\nfunc Answer() int {\n\treturn 42\n}\n"),
            ("main.go", "package fixture\n\nfunc Use() int {\n\treturn Answer()\n}\n"),
            ("go.mod", "module example.com/soleaux-benchmark\n\ngo 1.26\n"),
        ),
        source_files=("dep.go", "go.mod", "main.go"),
        request_path="main.go",
        line=4,
        column=12,
    ),
)


def nearest_rank_percentile(samples: Sequence[float], percentile: float) -> float:
    """Return the nearest-rank percentile for a nonempty sample sequence."""
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize_timings(samples: Sequence[float], *, warmups: int) -> TimingSummary:
    """Build a rounded stable timing summary."""
    if not samples:
        raise ValueError("timing summary requires at least one sample")
    rounded = tuple(round(sample, 3) for sample in samples)
    return TimingSummary(
        warmups=warmups,
        sample_count=len(rounded),
        median_ms=round(statistics.median(samples), 3),
        p95_ms=round(nearest_rank_percentile(samples, 0.95), 3),
        samples_ms=rounded,
    )


async def measure_async(
    operation: Callable[[], Awaitable[object]],
    policy: SamplePolicy,
) -> TimingSummary:
    """Measure one async operation after the policy's warmup count."""
    for _ in range(policy.warmups):
        await operation()
    samples: list[float] = []
    for _ in range(policy.samples):
        started = time.perf_counter()
        await operation()
        samples.append((time.perf_counter() - started) * 1000)
    return summarize_timings(samples, warmups=policy.warmups)


async def doctor_report(
    *,
    root: Path,
    workspace_id: str,
    config: ResolvedConfig,
    product_version: str,
    structural_worker_started: bool,
    catalog_status: Mapping[str, object],
    probe: bool,
) -> dict[str, Any]:
    """Build the stable redacted doctor payload without capturing source."""
    from soleaux.analysis.frame import build_provider_registry

    registry = build_provider_registry(root, config)
    typescript_installation = resolve_typescript_installation()
    providers = [
        {
            "name": provider.provider_name,
            "configured_version": provider.provider_version,
            "extensions": list(provider.extensions),
            "executable_available": provider.executable_available(),
            "negotiated_capabilities": None,
            "unsupported_reason": (
                None
                if provider.executable_available()
                else "installed provider executable was not discovered"
            ),
        }
        for provider in registry.providers
    ]
    probe_payload: dict[str, Any] = {
        "requested": probe,
        "completed": not probe,
        "structural_engine_version": _distribution_version("ast-grep-py", "0.44.1"),
        "postgresql_parser_version": managed_parser_version(),
        "typescript_runtime": {
            "available": typescript_installation is not None,
            "prefix": str(
                typescript_installation.prefix
                if typescript_installation is not None
                else configured_typescript_prefix()
            ),
            "ts_morph_version": (
                typescript_installation.ts_morph_version
                if typescript_installation is not None
                else None
            ),
            "native_typescript_version": (
                typescript_installation.native_version
                if typescript_installation is not None
                else None
            ),
            "expected_ts_morph_version": TS_MORPH_VERSION,
            "expected_native_typescript_version": NATIVE_TYPESCRIPT_VERSION,
            "worker_started": False,
        },
        "provider_versions": [],
    }
    if probe:
        probe_payload["provider_versions"] = await _probe_provider_versions(
            registry.providers,
            timeout_seconds=5.0,
        )
        probe_payload["completed"] = True

    unsupported = sorted(
        str(provider["unsupported_reason"])
        for provider in providers
        if provider["unsupported_reason"] is not None
    )
    config_path = root / CONFIG_FILENAME
    any_provider_available = any(bool(provider["executable_available"]) for provider in providers)
    return {
        "schema_version": "soleaux.doctor/v1",
        "workspace": {
            "id": workspace_id,
            "root": str(root),
        },
        "config": {
            "schema_version": config.schema_version,
            "source": str(config_path) if config_path.is_file() else "defaults",
            "workspace_count": len(config.workspaces),
            "provider_names": sorted(config.providers),
        },
        "runtime": {
            "product_version": product_version,
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": f"{platform.system().lower()}-{platform.machine()}",
        },
        "storage": {
            "requested_mode": catalog_status["requested_mode"],
            "effective_mode": catalog_status["mode"],
            "path": catalog_status["path"],
            "fallback_reason": catalog_status["fallback_reason"],
            "repository_local": False,
        },
        "providers": providers,
        "tables": [
            {
                "name": descriptor.name,
                "producer": descriptor.producer.value,
                "available": (
                    descriptor.producer is not Producer.IMPORTED
                    and (
                        descriptor.semantic_requirement is SemanticRequirement.NONE
                        or any_provider_available
                    )
                ),
                "semantic_requirement": descriptor.semantic_requirement.value,
            }
            for descriptor in TABLE_CATALOG
        ],
        "limits": {
            "rules": PackagedRuleLimits().model_dump(mode="json"),
            "request": RequestBudget().model_dump(mode="json"),
            "structural_worker": StructuralWorkerBudget().model_dump(mode="json"),
            "lsp_session": LspSessionBudget().model_dump(mode="json"),
        },
        "unsupported_reasons": unsupported,
        "probe": probe_payload,
        "analysis": {
            "recursive_analysis_performed": False,
            "source_files_opened": 0,
            "structural_worker_started": structural_worker_started,
        },
    }


async def benchmark_report(
    *,
    root: Path,
    startup_probe: Callable[[], Awaitable[object]],
    product_version: str,
) -> dict[str, Any]:
    """Run the reproducible startup, structural, and provider process probes."""
    rss_before = _rss_bytes()
    startup_summary = await measure_async(startup_probe, CHEAP_SAMPLE_POLICY)
    structural = await _structural_benchmarks()
    provider_rows, provider_pids = await _provider_benchmarks(root)
    rss_delta = max(0, _rss_bytes() - rss_before)
    spawned_pids = sorted({*structural["spawned_pids"], *provider_pids})
    warm_structural = structural["warm_structural_2kib"]
    hundred_file = structural["structural_100_file"]
    assert isinstance(warm_structural, TimingSummary)
    assert isinstance(hundred_file, TimingSummary)

    providers_pass = all(
        row["status"] == "unsupported"
        or (
            row["status"] == "available"
            and float(row["first_start_ms"]) <= LSP_FIRST_START_MS
            and float(row["warm_p95_ms"]) <= LSP_WARM_P95_MS
        )
        for row in provider_rows
    )
    passed = (
        startup_summary.p95_ms <= STARTUP_P95_MS
        and rss_delta <= STARTUP_RSS_DELTA_BYTES
        and warm_structural.p95_ms <= WARM_STRUCTURAL_P95_MS
        and hundred_file.p95_ms <= STRUCTURAL_100_FILE_P95_MS
        and providers_pass
    )
    return {
        "schema_version": "soleaux.benchmark/v1",
        "product_version": product_version,
        "sample_policy": {
            "cheap": CHEAP_SAMPLE_POLICY.model_dump(mode="json"),
            "real_language_server": REAL_LSP_SAMPLE_POLICY.model_dump(mode="json"),
        },
        "environment": _environment_metadata(),
        "scenarios": {
            "startup_probe": startup_summary.model_dump(mode="json"),
            "warm_structural_2kib": warm_structural.model_dump(mode="json"),
            "structural_100_file": {
                **hundred_file.model_dump(mode="json"),
                "row_count": structural["row_count"],
                "row_checksum": structural["row_checksum"],
            },
            "language_servers": provider_rows,
        },
        "resource_usage": {
            "rss_delta_bytes": rss_delta,
            "file_count": structural["file_count"],
            "byte_count": structural["byte_count"],
            "producer_invocations": structural["producer_invocations"],
            "spawned_pids": spawned_pids,
        },
        "gates": {
            "mcp_initialize_tools_list_p95_ms": STARTUP_P95_MS,
            "startup_rss_delta_bytes": STARTUP_RSS_DELTA_BYTES,
            "warm_structural_query_2kib_p95_ms": WARM_STRUCTURAL_P95_MS,
            "structural_100_file_p95_ms": STRUCTURAL_100_FILE_P95_MS,
            "lsp_first_start_ms": LSP_FIRST_START_MS,
            "lsp_warm_ms": LSP_WARM_P95_MS,
            "cancellation_return_ms": CANCELLATION_RETURN_MS,
            "shutdown_grace_ms": SHUTDOWN_GRACE_MS,
        },
        "passed": passed,
    }


async def _structural_benchmarks() -> dict[str, Any]:
    supervisor = StructuralWorkerSupervisor()
    small = _small_python_fixture()
    hundred = _hundred_file_fixture()
    spawned_pids: set[int] = set()
    checksums: set[str] = set()
    row_count = 0

    async def small_operation() -> object:
        result = await supervisor.extract(
            language="Python",
            path="small.py",
            content=small,
            projections=("syntax.declarations",),
            workspace_id="benchmark",
        )
        if supervisor.pid is not None:
            spawned_pids.add(supervisor.pid)
        return result

    async def hundred_operation() -> object:
        nonlocal row_count
        rows: list[dict[str, Any]] = []
        for path, content in hundred:
            result = await supervisor.extract(
                language="TypeScript",
                path=path,
                content=content,
                projections=("syntax.declarations", "syntax.imports"),
                workspace_id="benchmark",
            )
            if supervisor.pid is not None:
                spawned_pids.add(supervisor.pid)
            rows.extend(fragment.model_dump(mode="json") for fragment in result.fragments)
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        checksums.add(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        row_count = len(rows)
        return rows

    try:
        warm = await measure_async(small_operation, CHEAP_SAMPLE_POLICY)
        hundred_summary = await measure_async(hundred_operation, HUNDRED_FILE_SAMPLE_POLICY)
        producer_invocations = supervisor.total_completed_jobs
    finally:
        await supervisor.aclose()
    if len(checksums) != 1:
        raise RuntimeError("100-file structural benchmark produced a nondeterministic checksum")
    return {
        "warm_structural_2kib": warm,
        "structural_100_file": hundred_summary,
        "row_count": row_count,
        "row_checksum": next(iter(checksums)),
        "file_count": len(hundred) + 1,
        "byte_count": len(small) + sum(len(content) for _path, content in hundred),
        "producer_invocations": producer_invocations,
        "spawned_pids": sorted(spawned_pids),
    }


async def _provider_benchmarks(root: Path) -> tuple[list[dict[str, Any]], set[int]]:
    template_registry = ProviderRegistry.default(_provider_toolchain_root(root))
    results: list[tuple[dict[str, Any], set[int]]] = []
    for case in _PROVIDER_CASES:
        results.append(await _benchmark_provider(case, template_registry=template_registry))
    rows = [row for row, _pids in results]
    spawned_pids: set[int] = set()
    for _row, pids in results:
        spawned_pids.update(pids)
    return rows, spawned_pids


async def _benchmark_provider(
    case: _ProviderBenchmarkCase,
    *,
    template_registry: ProviderRegistry,
) -> tuple[dict[str, Any], set[int]]:
    spawned_pids: set[int] = set()
    first_start_ms: float | None = None
    summary: TimingSummary | None = None
    failed_reason: str | None = None
    coverage_status: str | None = None
    location_count = 0
    with tempfile.TemporaryDirectory(prefix=f"soleaux-{case.name}-") as directory:
        root = Path(directory)
        for relative, content in case.files:
            (root / relative).write_text(content, encoding="utf-8")
        workspace = AllowedWorkspaceSet.from_launch(
            [("benchmark", str(root))],
            config_digest="soleaux-benchmark",
        ).get("benchmark")
        registry = _relocate_registry(template_registry, root)
        provider = registry.configured_for_path(case.request_path)
        if provider is None or not provider.executable_available():
            return (
                {
                    "name": case.name,
                    "status": "unsupported",
                    "reason": "installed provider executable was not discovered",
                    "first_start_ms": None,
                    "warm_p95_ms": None,
                    "sample_count": 0,
                },
                set(),
            )
        bundle = await RepositorySnapshotter(workspace).capture(scope=case.source_files)
        resolver = SemanticResolver(registry)
        request = NavigationRequest(
            operation=SemanticOperation.DEFINITION,
            path=case.request_path,
            line=case.line,
            column=case.column,
        )
        try:
            started = time.perf_counter()
            first = await asyncio.wait_for(
                resolver.navigate(request, bundle),
                timeout=LSP_FIRST_START_MS / 1000,
            )
            first_start_ms = (time.perf_counter() - started) * 1000
            spawned_pids.update(resolver.active_provider_pids)
            coverage_status = first.status.value
            location_count = len(first.locations)
            if not spawned_pids:
                raise RuntimeError("provider request started no owned process")

            async def warm_request() -> object:
                return await resolver.navigate(request, bundle)

            summary = await measure_async(warm_request, REAL_LSP_SAMPLE_POLICY)
        except Exception as exc:
            failed_reason = f"{type(exc).__name__}: provider benchmark failed"
        finally:
            await resolver.shutdown()
    if failed_reason is not None or summary is None or first_start_ms is None:
        return (
            {
                "name": case.name,
                "status": "failed",
                "reason": failed_reason or "provider benchmark ended before all samples",
                "first_start_ms": first_start_ms,
                "warm_p95_ms": None,
                "sample_count": 0,
                "coverage_status": coverage_status,
                "location_count": location_count,
            },
            spawned_pids,
        )
    return (
        {
            "name": case.name,
            "status": "available",
            "reason": None,
            "first_start_ms": round(first_start_ms, 3),
            "warm_p95_ms": summary.p95_ms,
            "sample_count": summary.sample_count,
            "coverage_status": coverage_status,
            "location_count": location_count,
        },
        spawned_pids,
    )


async def _probe_provider_versions(
    providers: Sequence[ConfiguredProvider],
    *,
    timeout_seconds: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for provider in providers:
        if not provider.executable_available():
            rows.append(
                {
                    "name": provider.provider_name,
                    "status": "unsupported",
                    "version": None,
                }
            )
            continue
        output, error = await _capture_process(
            _version_command(provider),
            cwd=provider.root,
            environment_names=provider.environment_names,
            provider_environment=provider.process_environment(),
            timeout_seconds=timeout_seconds,
        )
        rows.append(
            {
                "name": provider.provider_name,
                "status": "ok" if error is None else "failed",
                "version": output if error is None else None,
            }
        )
    return rows


async def _capture_process(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment_names: Sequence[str],
    provider_environment: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[str, str | None]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=build_safe_environment(
            provider_environment,
            environment_names=environment_names,
        ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        await _terminate_process(process)
        return "", "provider version probe exceeded its deadline"
    if process.returncode != 0:
        return "", f"provider version probe exited with status {process.returncode}"
    first_line = stdout.decode("utf-8", errors="replace").splitlines()
    return (first_line[0][:160] if first_line else "unknown"), None


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        await process.wait()


def _version_command(provider: ConfiguredProvider) -> tuple[str, ...]:
    executable = resolve_provider_executable(provider.argv, provider.root) or provider.argv[0]
    if provider.provider_name == "gopls":
        return (executable, "version")
    if provider.provider_name == "pyright-langserver":
        companion = shutil.which("pyright", path=str(Path(executable).parent))
        return (companion or executable, "--version")
    return (executable, "--version")


def _provider_toolchain_root(root: Path) -> Path:
    candidates = (root, *root.parents, Path.cwd(), *Path.cwd().parents)
    for candidate in candidates:
        if any(
            (candidate / relative).is_file()
            for relative in (
                Path("node_modules/typescript/lib/tsserver.js"),
                Path("node_modules/typescript-lsp/lib/tsserver.js"),
            )
        ):
            return candidate
    return root


def _relocate_registry(registry: ProviderRegistry, root: Path) -> ProviderRegistry:
    canonical_root = root.resolve(strict=True)
    providers = tuple(
        provider.model_copy(
            update={
                "root": canonical_root,
                "config_digest": hashlib.sha256(
                    f"{provider.config_digest}\0{canonical_root}".encode()
                ).hexdigest(),
            }
        )
        for provider in registry.providers
    )
    return ProviderRegistry(providers)


def _small_python_fixture() -> bytes:
    prefix = b"def answer(value: int) -> int:\n    return value + 1\n"
    padding = b"# benchmark padding\n" * 120
    return prefix + padding


def _hundred_file_fixture() -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (
            f"src/mod{index}.ts",
            (
                f"import {{ helper{index} }} from './helper{index}';\n"
                f"export function entry{index}(value: number): number {{\n"
                f"  return helper{index}(value);\n"
                "}\n"
            ).encode(),
        )
        for index in range(100)
    )


def _rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _distribution_version(name: str, fallback: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return fallback


def _environment_metadata() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
        "fastmcp": _distribution_version("fastmcp", "unknown"),
        "ast_grep_py": _distribution_version("ast-grep-py", "0.44.1"),
    }
