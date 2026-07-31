"""StructuralWorkerSupervisor: one lazy supervised ast-grep worker (D011, D018).

Startup, initialize, tools/list, and describe start no child. The first
structural request lazily starts exactly one supervised Python worker. The
parent owns bounded IPC, deadlines, output caps, cancellation,
terminate/kill escalation, job and RSS accounting, and replacement after 64
completed jobs, 256 MiB RSS, hard cancellation, or protocol failure.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import json
import os
import signal
import sys
import time
import typing

import soleaux.catalog.postgresql
import soleaux.contracts.budget
import soleaux.postgresql.runtime
import soleaux.structural.fragments
from soleaux.analysis.task_registry import TaskRegistry
from soleaux.contracts.repository import content_digest
from soleaux.structural.cache import MemoryCache, StructuralCacheKey

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_CONTENT_BYTES = 4 * 1024 * 1024
JOB_TIMEOUT_SECONDS = 15.0
PROCESS_GROUP_POLL_SECONDS = 0.01


class WorkerUnavailableError(Exception):
    """The worker failed or missed its deadline; the provider is named."""


class ContentTooLargeError(ValueError):
    """The requested content exceeds the bounded IPC cap."""


class WorkerJobError(Exception):
    """The worker returned a typed job failure."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclasses.dataclass(frozen=True)
class _OwnedProcessTree:
    root_pid: int


@dataclasses.dataclass(frozen=True)
class _WorkerResponse:
    payload: dict[str, typing.Any]
    frame: bytes


@dataclasses.dataclass(frozen=True)
class ExtractResult:
    """One bounded extraction response."""

    fragments: tuple[soleaux.structural.fragments.SyntaxFragment, ...]
    diagnostics: tuple[soleaux.structural.fragments.FragmentDiagnostic, ...]
    parses: int
    parse_ms: float
    truncated: bool
    unsupported: tuple[str, ...]
    postgresql_catalog: soleaux.catalog.postgresql.PostgreSqlCatalogExtraction | None = None


@dataclasses.dataclass(frozen=True)
class _CompletedExtraction:
    result: ExtractResult
    cache_frame: bytes


class StructuralWorkerSupervisor:
    """Owns the one lazy supervised worker and its lifecycle accounting."""

    def __init__(
        self,
        budget: soleaux.contracts.budget.StructuralWorkerBudget | None = None,
        *,
        worker_argv: list[str] | None = None,
    ) -> None:
        self._budget = budget or soleaux.contracts.budget.StructuralWorkerBudget()
        self._argv = worker_argv or [
            sys.executable,
            "-I",
            "-m",
            "soleaux.structural.worker",
        ]
        self._proc: asyncio.subprocess.Process | None = None
        self._process_tree: _OwnedProcessTree | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0
        self._completed_jobs = 0
        self._total_completed_jobs = 0
        self._last_rss_bytes: int | None = None
        self._last_replace_reason: str | None = None
        self._worker_epoch = 0
        self._tasks = TaskRegistry()
        self._cache = MemoryCache(
            max_entries=self._budget.lru_entries,
            max_bytes=self._budget.lru_bytes,
        )
        self._closed = False

    @property
    def started(self) -> bool:
        """Whether a worker process currently exists."""
        return self._proc is not None

    @property
    def pid(self) -> int | None:
        """The current worker pid, when started."""
        return self._proc.pid if self._proc is not None else None

    @property
    def completed_jobs(self) -> int:
        """Jobs completed by the current worker epoch."""
        return self._completed_jobs

    @property
    def total_completed_jobs(self) -> int:
        """Worker jobs completed across recycled worker epochs."""
        return self._total_completed_jobs

    async def extract(
        self,
        *,
        language: str,
        path: str,
        content: bytes,
        projections: tuple[str, ...],
        rules: tuple[str, ...] = (),
        symbol_query: str | None = None,
        symbol_max_results: int | None = None,
        postgresql_catalog: (soleaux.catalog.postgresql.PostgreSqlCatalogContext | None) = None,
        timeout: float = JOB_TIMEOUT_SECONDS,
        workspace_id: str = "standalone",
    ) -> ExtractResult:
        """Run one bounded extraction; one bounded retry after worker failure."""
        if len(content) > MAX_CONTENT_BYTES:
            msg = f"content of {len(content)} bytes exceeds the {MAX_CONTENT_BYTES} cap"
            raise ContentTooLargeError(msg)
        self._require_open()
        if (symbol_query is None) != (symbol_max_results is None):
            raise ValueError("symbol_query and symbol_max_results must be provided together")
        if symbol_query is not None:
            if not symbol_query or symbol_max_results is None or symbol_max_results < 1:
                raise ValueError(
                    "symbol search requires a non-empty query and positive result limit"
                )
            if projections != ("syntax.declarations",) or rules:
                raise ValueError("symbol search supports only the syntax.declarations projection")
        if postgresql_catalog is not None:
            if language != "PostgreSQL":
                raise ValueError("PostgreSQL catalog context requires PostgreSQL source")
            if postgresql_catalog.path != path:
                raise ValueError("PostgreSQL catalog path must match the extraction path")
        content_hash = content_digest(content)
        postgresql_catalog_payload = (
            postgresql_catalog.model_dump(mode="json") if postgresql_catalog is not None else None
        )
        cache_key = StructuralCacheKey(
            workspace_id=workspace_id,
            source_fingerprint=content_digest(f"{workspace_id}\0{path}\0{content_hash}".encode()),
            projection_name=json.dumps(
                {
                    "projections": projections,
                    "rules": rules,
                    "symbol_query": symbol_query,
                    "symbol_max_results": symbol_max_results,
                    "postgresql_catalog": postgresql_catalog_payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            language=language,
            sgconfig_hash=hashlib.sha256(
                json.dumps(
                    {"argv": self._argv, "language": language},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            rule_digest=hashlib.sha256(
                json.dumps(rules, separators=(",", ":")).encode()
            ).hexdigest(),
            analyzer_version=soleaux.structural.fragments.analyzer_version_for(language),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return _decode_result(cached)
        request: dict[str, typing.Any] = {
            "op": "extract",
            "language": language,
            "path": path,
            "content_b64": base64.b64encode(content).decode("ascii"),
            "projections": list(projections),
            "rules": list(rules),
        }
        if symbol_query is not None and symbol_max_results is not None:
            request["symbol_query"] = symbol_query
            request["symbol_max_results"] = symbol_max_results
        if postgresql_catalog_payload is not None:
            request["postgresql_catalog"] = postgresql_catalog_payload
        task_key = (
            self._worker_epoch,
            language,
            path,
            content_hash,
            projections,
            rules,
            symbol_query,
            symbol_max_results,
            json.dumps(
                postgresql_catalog_payload,
                sort_keys=True,
                separators=(",", ":"),
            ),
            timeout,
            soleaux.structural.fragments.PROJECTION_SCHEMA_VERSION,
        )
        completed = await self._tasks.share(
            task_key,
            lambda: self._extract_once(request, timeout),
        )
        self._cache.put(cache_key, completed.cache_frame)
        return completed.result

    async def _extract_once(
        self,
        request: dict[str, typing.Any],
        timeout: float,
    ) -> _CompletedExtraction:
        async with self._lock:
            self._require_open()
            try:
                return await self._run_job(request, timeout)
            except WorkerUnavailableError:
                self._require_open()
                return await self._run_job(request, timeout)

    async def structural(
        self,
        *,
        language: str,
        matcher: dict[str, typing.Any],
        files: tuple[tuple[str, bytes], ...],
        fix: dict[str, typing.Any] | str | None = None,
        transforms: dict[str, typing.Any] | None = None,
        want: tuple[str, ...] = ("findings",),
        limits: dict[str, int] | None = None,
        timeout: float = JOB_TIMEOUT_SECONDS,
    ) -> dict[str, typing.Any]:
        """Run one bounded structural matcher job; return the raw wire response."""
        self._require_open()
        for _path, content in files:
            if len(content) > MAX_CONTENT_BYTES:
                msg = f"content of {len(content)} bytes exceeds the {MAX_CONTENT_BYTES} cap"
                raise ContentTooLargeError(msg)
        request: dict[str, typing.Any] = {
            "op": "structural",
            "language": language,
            "matcher": matcher,
            "fix": fix,
            "transforms": transforms,
            "want": list(want),
            "limits": limits or {},
            "files": [
                {"path": path, "content_b64": base64.b64encode(content).decode("ascii")}
                for path, content in files
            ],
        }
        return await self._tasks.share(
            ("structural", object()),
            lambda: self._structural_once(request, timeout),
        )

    async def _structural_once(
        self,
        request: dict[str, typing.Any],
        timeout: float,
    ) -> dict[str, typing.Any]:
        async with self._lock:
            self._require_open()
            try:
                return await self._run_structural_job(request, timeout)
            except WorkerUnavailableError:
                self._require_open()
                return await self._run_structural_job(request, timeout)

    async def _run_structural_job(
        self,
        request: dict[str, typing.Any],
        timeout: float,
    ) -> dict[str, typing.Any]:
        await self._ensure_worker()
        await self._recycle_if_needed()
        response = await self._roundtrip(request, timeout)
        self._completed_jobs += 1
        self._total_completed_jobs += 1
        return response.payload

    async def _run_job(
        self,
        request: dict[str, typing.Any],
        timeout: float,
    ) -> _CompletedExtraction:
        await self._ensure_worker()
        await self._recycle_if_needed()
        worker_response = await self._roundtrip(request, timeout)
        response = worker_response.payload
        self._completed_jobs += 1
        self._total_completed_jobs += 1
        rss = response.get("stats", {}).get("max_rss_bytes")
        if isinstance(rss, int):
            self._last_rss_bytes = rss
            if rss > self._budget.max_rss_bytes:
                await self._replace("rss-limit", provision=True)
        return _CompletedExtraction(
            result=_result_from_response(response),
            cache_frame=worker_response.frame,
        )

    async def _ensure_worker(self) -> None:
        self._require_open()
        if self._proc is not None:
            if self._proc.returncode is None:
                return
            await self._replace("worker-exited")
        await self._spawn_and_handshake()

    async def _spawn_and_handshake(self) -> None:
        self._require_open()
        _require_process_tree_isolation()
        self._process_tree = None
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=soleaux.postgresql.runtime.build_safe_environment(
                    {},
                    environment_names=(),
                ),
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            try:
                process = await spawn_task
            except OSError:
                pass
            else:
                await self._terminate_owned_process(
                    process,
                    _OwnedProcessTree(root_pid=process.pid),
                    self._budget.shutdown_grace_seconds,
                )
            raise
        except OSError:
            self._proc = None
            raise WorkerUnavailableError("structural worker could not start") from None
        self._proc = process
        self._process_tree = _OwnedProcessTree(root_pid=process.pid)
        self._worker_epoch += 1
        self._completed_jobs = 0
        response = (await self._roundtrip({"op": "ping"}, timeout=5.0)).payload
        if (
            response.get("status") != "ok"
            or response.get("op") != "pong"
            or response.get("engine") != "python"
            or response.get("engine_version") != soleaux.structural.fragments.AST_GREP_VERSION
            or response.get("capabilities")
            != list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES)
        ):
            await self._replace("identity-mismatch")
            raise WorkerUnavailableError(
                "structural worker did not prove the expected "
                "python engine/version/capability identity"
            )

    async def _roundtrip(self, request: dict[str, typing.Any], timeout: float) -> _WorkerResponse:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise WorkerUnavailableError("structural worker is not running")
        self._request_id += 1
        request = {**request, "id": self._request_id}
        frame = json.dumps(request).encode("utf-8") + b"\n"
        if len(frame) > MAX_FRAME_BYTES:
            raise ContentTooLargeError(
                f"request frame of {len(frame)} bytes exceeds the {MAX_FRAME_BYTES} cap"
            )
        try:
            self._proc.stdin.write(frame)
            await self._proc.stdin.drain()
            line = await asyncio.wait_for(self._read_frame(), timeout=timeout)
        except (
            TimeoutError,
            asyncio.IncompleteReadError,
            BrokenPipeError,
            ConnectionResetError,
        ):
            await self._replace("deadline-or-eof")
            raise WorkerUnavailableError("structural worker missed its deadline") from None
        except asyncio.CancelledError:
            await self._replace("hard-cancellation")
            raise
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            await self._replace("protocol-failure")
            raise WorkerUnavailableError("structural worker returned a malformed frame") from None
        if not isinstance(parsed, dict):
            await self._replace("protocol-failure")
            raise WorkerUnavailableError("structural worker returned a non-object frame")
        response = typing.cast("dict[str, typing.Any]", parsed)
        if response.get("id") != request["id"]:
            await self._replace("protocol-failure")
            raise WorkerUnavailableError("structural worker returned a mismatched frame id")
        if response.get("status") == "error":
            error = response.get("error", {})
            raise WorkerJobError(str(error.get("type", "unknown")), str(error.get("message", "")))
        return _WorkerResponse(payload=response, frame=line)

    async def _read_frame(self) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        buffer = bytearray()
        while True:
            chunk = await self._proc.stdout.read(65536)
            if not chunk:
                raise asyncio.IncompleteReadError(bytes(buffer), None)
            buffer.extend(chunk)
            if len(buffer) > MAX_FRAME_BYTES:
                raise asyncio.IncompleteReadError(bytes(buffer[:0]), None)
            newline = buffer.find(b"\n")
            if newline >= 0:
                return bytes(buffer[:newline])

    async def _recycle_if_needed(self) -> None:
        if self._completed_jobs >= self._budget.max_completed_jobs:
            await self._replace("job-limit", provision=True)

    async def _replace(self, reason: str, *, provision: bool = False) -> None:
        self._last_replace_reason = reason
        proc = self._proc
        process_tree = self._process_tree
        self._proc = None
        self._process_tree = None
        self._completed_jobs = 0
        self._last_rss_bytes = None
        if proc is not None:
            await self._terminate_owned_process(
                proc,
                process_tree,
                self._budget.shutdown_grace_seconds,
            )
        if provision and not self._closed:
            with contextlib.suppress(WorkerUnavailableError):
                await self._spawn_and_handshake()

    @staticmethod
    async def _terminate_then_kill(
        proc: asyncio.subprocess.Process,
        process_tree: _OwnedProcessTree | None,
        grace_seconds: float,
    ) -> None:
        if process_tree is None:
            await _terminate_direct_process(proc, grace_seconds)
            return

        StructuralWorkerSupervisor._signal_process_tree(
            proc,
            process_tree.root_pid,
            signal.SIGTERM,
        )
        if proc.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        if await StructuralWorkerSupervisor._wait_for_process_group_exit(
            process_tree.root_pid,
            grace_seconds,
        ):
            return
        StructuralWorkerSupervisor._signal_process_tree(
            proc,
            process_tree.root_pid,
            signal.SIGKILL,
        )
        if proc.returncode is None:
            await proc.wait()
        await StructuralWorkerSupervisor._wait_for_process_group_exit(
            process_tree.root_pid,
            grace_seconds,
        )

    @staticmethod
    async def _terminate_owned_process(
        proc: asyncio.subprocess.Process,
        process_tree: _OwnedProcessTree | None,
        grace_seconds: float,
    ) -> None:
        cleanup = asyncio.create_task(
            StructuralWorkerSupervisor._terminate_then_kill(
                proc,
                process_tree,
                grace_seconds,
            )
        )
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    @staticmethod
    def _signal_process_tree(
        proc: asyncio.subprocess.Process,
        process_group_id: int | None,
        signal_number: signal.Signals,
    ) -> None:
        if os.name == "posix" and process_group_id is not None:
            try:
                os.killpg(process_group_id, signal_number)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            else:
                return
        if proc.returncode is not None:
            return
        try:
            if signal_number == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            return

    @staticmethod
    async def _wait_for_process_group_exit(
        process_group_id: int,
        timeout_seconds: float,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(PROCESS_GROUP_POLL_SECONDS, remaining))

    async def aclose(self) -> None:
        """Cancel-and-drain-then-release: bounded shutdown, then terminate/kill."""
        self._closed = True
        await self._tasks.cancel_all()
        self._cache.clear()
        async with self._lock:
            proc = self._proc
            process_tree = self._process_tree
            self._proc = None
            self._process_tree = None
            if proc is None:
                return
            try:
                if proc.returncode is None and proc.stdin is not None:
                    self._request_id += 1
                    proc.stdin.write(
                        json.dumps({"op": "shutdown", "id": self._request_id}).encode() + b"\n"
                    )
                    await proc.stdin.drain()
            except BrokenPipeError, ConnectionResetError:
                pass
            await self._terminate_owned_process(
                proc,
                process_tree,
                self._budget.shutdown_grace_seconds,
            )

    def _require_open(self) -> None:
        if self._closed:
            raise WorkerUnavailableError("structural worker supervisor is closed")


def _require_process_tree_isolation() -> None:
    if os.name == "posix":
        return
    raise WorkerUnavailableError(
        f"structural worker process-tree isolation is unsupported on platform {os.name!r}"
    )


async def _terminate_direct_process(
    proc: asyncio.subprocess.Process,
    grace_seconds: float,
) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        await proc.wait()
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()


def _decode_result(payload: bytes) -> ExtractResult:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise WorkerUnavailableError("cached structural result is not an object")
    return _result_from_response(typing.cast("dict[str, typing.Any]", decoded))


def _result_from_response(response: dict[str, typing.Any]) -> ExtractResult:
    return ExtractResult(
        fragments=tuple(
            soleaux.structural.fragments.SyntaxFragment.model_validate(fragment)
            for fragment in response["fragments"]
        ),
        diagnostics=tuple(
            soleaux.structural.fragments.FragmentDiagnostic.model_validate(diagnostic)
            for diagnostic in response["diagnostics"]
        ),
        parses=int(response["stats"]["parses"]),
        parse_ms=float(response["stats"]["parse_ms"]),
        truncated=bool(response["stats"]["truncated"]),
        unsupported=tuple(str(value) for value in response["stats"]["unsupported"]),
        postgresql_catalog=(
            soleaux.catalog.postgresql.PostgreSqlCatalogExtraction.model_validate(
                response["postgresql_catalog"]
            )
            if response.get("postgresql_catalog") is not None
            else None
        ),
    )
