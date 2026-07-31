"""Explicitly provisioned, lazy Node runtime for ts-morph and native TypeScript."""

from __future__ import annotations

import asyncio
import collections.abc
import dataclasses
import importlib.resources
import json
import os
import pathlib
import selectors
import shutil
import subprocess
import threading
import typing

import platformdirs
import pydantic

import soleaux.postgresql.runtime
import soleaux.typescript.contracts

MAX_FRAME_BYTES: typing.Final = 32 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: typing.Final = 30.0
_OBJECT_MAPPING_ADAPTER = pydantic.TypeAdapter(dict[str, object])


class TypeScriptRuntimeError(Exception):
    """Base failure for the managed dual-engine runtime."""


class TypeScriptRuntimeUnavailableError(TypeScriptRuntimeError):
    """The exact managed packages or Node executable are unavailable."""


class TypeScriptRuntimeProtocolError(TypeScriptRuntimeError):
    """The worker violated its versioned JSON-lines protocol."""


@dataclasses.dataclass(frozen=True, slots=True)
class TypeScriptRuntimeInstallation:
    """One exact validated TypeScript runtime prefix."""

    prefix: pathlib.Path
    node_executable: str
    ts_morph_version: str
    native_version: str


def managed_typescript_prefix() -> pathlib.Path:
    """Return the canonical per-user prefix without creating it."""
    return platformdirs.user_data_path("soleaux", appauthor=False) / "typescript-runtime"


def configured_typescript_prefix(
    prefix: pathlib.Path | None = None,
    *,
    environ: collections.abc.Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Resolve an explicit or environment-owned prefix without creating it."""
    environment = os.environ if environ is None else environ
    if prefix is not None:
        return prefix.expanduser().resolve(strict=False)
    configured = environment.get("SOLEAUX_TYPESCRIPT_RUNTIME")
    selected = pathlib.Path(configured).expanduser() if configured else managed_typescript_prefix()
    return selected.resolve(strict=False)


def _manifest_version(path: pathlib.Path, *, expected_name: str) -> str:
    try:
        raw: object = json.loads(path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise TypeScriptRuntimeUnavailableError(f"invalid runtime manifest {path}") from exc
    if not isinstance(raw, dict):
        raise TypeScriptRuntimeUnavailableError(f"runtime manifest is not an object: {path}")
    manifest = _OBJECT_MAPPING_ADAPTER.validate_python(raw, strict=True)
    name = manifest.get("name")
    version = manifest.get("version")
    if name != expected_name or not isinstance(version, str):
        raise TypeScriptRuntimeUnavailableError(
            f"runtime manifest {path} does not identify {expected_name}"
        )
    return version


def resolve_typescript_installation(
    prefix: pathlib.Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> TypeScriptRuntimeInstallation | None:
    """Discover the exact managed runtime without searching or writing."""
    environment = os.environ if environ is None else environ
    runtime_prefix = configured_typescript_prefix(prefix, environ=environment)
    node = shutil.which("node", path=environment.get("PATH"))
    if node is None:
        return None
    ts_morph_manifest = runtime_prefix / "node_modules" / "ts-morph" / "package.json"
    native_manifest = runtime_prefix / "node_modules" / "@typescript" / "native" / "package.json"
    try:
        ts_morph_version = _manifest_version(ts_morph_manifest, expected_name="ts-morph")
        native_version = _manifest_version(native_manifest, expected_name="typescript")
    except TypeScriptRuntimeUnavailableError:
        return None
    if (
        ts_morph_version != soleaux.typescript.contracts.TS_MORPH_VERSION
        or native_version != soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION
    ):
        return None
    return TypeScriptRuntimeInstallation(
        prefix=runtime_prefix,
        node_executable=node,
        ts_morph_version=ts_morph_version,
        native_version=native_version,
    )


def provision_typescript_runtime(
    prefix: pathlib.Path | None = None,
    *,
    npm_executable: str = "npm",
    timeout_seconds: float = 180.0,
) -> TypeScriptRuntimeInstallation:
    """Explicitly install both exact packages under a dedicated prefix."""
    runtime_prefix = configured_typescript_prefix(prefix)
    package_json = runtime_prefix / "package.json"
    if runtime_prefix.exists() and not runtime_prefix.is_dir():
        raise TypeScriptRuntimeUnavailableError(
            f"TypeScript runtime prefix is not a directory: {runtime_prefix}"
        )
    if runtime_prefix.is_dir() and not package_json.exists():
        existing = next(runtime_prefix.iterdir(), None)
        if existing is not None:
            raise TypeScriptRuntimeUnavailableError(
                "refusing to provision into a nonempty directory without package.json"
            )
    runtime_prefix.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not package_json.exists():
        package_json.write_text(
            '{"name":"soleaux-typescript-runtime","private":true}\n',
            encoding="utf-8",
        )
    command = [
        npm_executable,
        "install",
        "--prefix",
        str(runtime_prefix),
        "--no-package-lock",
        "--no-save",
        "--no-audit",
        "--no-fund",
        f"ts-morph@{soleaux.typescript.contracts.TS_MORPH_VERSION}",
        f"@typescript/native@npm:typescript@{soleaux.typescript.contracts.NATIVE_TYPESCRIPT_VERSION}",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=soleaux.postgresql.runtime.build_safe_environment({}, environment_names=()),
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise TypeScriptRuntimeUnavailableError("TypeScript runtime provisioning failed") from exc
    if completed.returncode != 0:
        raise TypeScriptRuntimeUnavailableError(
            f"TypeScript runtime provisioning failed with exit {completed.returncode}"
        )
    installation = resolve_typescript_installation(runtime_prefix)
    if installation is None:
        raise TypeScriptRuntimeUnavailableError(
            "package manager completed without the exact TypeScript runtime"
        )
    return installation


class TypeScriptNodeRuntime:
    """One bounded lazy worker shared across project requests."""

    def __init__(
        self,
        installation: TypeScriptRuntimeInstallation,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._installation = installation
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False

    def __enter__(self) -> typing.Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object | None,
    ) -> None:
        self.close()

    @property
    def started(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self.started and self._process is not None else None

    def analyze(
        self, request: soleaux.typescript.contracts.TypeScriptAnalysisRequest
    ) -> soleaux.typescript.contracts.TypeScriptAnalysis:
        """Analyze one immutable project through both exact compiler engines."""
        response = self._request(
            {
                "operation": "analyze",
                "request": request.model_dump(mode="json"),
            }
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise TypeScriptRuntimeProtocolError("worker result is not an object")
        return soleaux.typescript.contracts.TypeScriptAnalysis.model_validate(result)

    async def analyze_async(
        self, request: soleaux.typescript.contracts.TypeScriptAnalysisRequest
    ) -> soleaux.typescript.contracts.TypeScriptAnalysis:
        return await asyncio.to_thread(self.analyze, request)

    def capabilities(self) -> dict[str, object]:
        response = self._request({"operation": "capabilities"})
        result = response.get("result")
        if not isinstance(result, dict):
            raise TypeScriptRuntimeProtocolError("worker capabilities are not an object")
        return _OBJECT_MAPPING_ADAPTER.validate_python(result, strict=True)

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise TypeScriptRuntimeUnavailableError("TypeScript runtime is closed")
            process = self._ensure_process()
            self._sequence += 1
            frame_id = self._sequence
            frame = json.dumps(
                {"id": frame_id, **payload},
                separators=(",", ":"),
            ).encode("utf-8")
            if len(frame) > MAX_FRAME_BYTES:
                raise TypeScriptRuntimeProtocolError("TypeScript request exceeds frame bound")
            if process.stdin is None or process.stdout is None:
                raise TypeScriptRuntimeUnavailableError("TypeScript worker pipes are unavailable")
            try:
                process.stdin.write(frame + b"\n")
                process.stdin.flush()
            except BrokenPipeError as exc:
                self._terminate()
                raise TypeScriptRuntimeUnavailableError("TypeScript worker input closed") from exc

            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            try:
                if not selector.select(self._timeout_seconds):
                    self._terminate()
                    raise TypeScriptRuntimeUnavailableError(
                        f"TypeScript worker missed its {self._timeout_seconds:g}-second deadline"
                    )
                response_frame = process.stdout.readline(MAX_FRAME_BYTES + 1)
            finally:
                selector.close()
            if not response_frame:
                self._terminate()
                raise TypeScriptRuntimeUnavailableError("TypeScript worker exited before replying")
            if len(response_frame) > MAX_FRAME_BYTES:
                self._terminate()
                raise TypeScriptRuntimeProtocolError("TypeScript response exceeds frame bound")
            try:
                decoded: object = json.loads(response_frame)
            except json.JSONDecodeError as exc:
                self._terminate()
                raise TypeScriptRuntimeProtocolError(
                    "TypeScript worker returned invalid JSON"
                ) from exc
            if not isinstance(decoded, dict):
                raise TypeScriptRuntimeProtocolError("TypeScript response is not an object")
            response = _OBJECT_MAPPING_ADAPTER.validate_python(decoded, strict=True)
            if response.get("id") != frame_id:
                raise TypeScriptRuntimeProtocolError("TypeScript worker frame id mismatch")
            if response.get("status") != "ok":
                error = response.get("error")
                raise TypeScriptRuntimeError(str(error))
            return response

    def _ensure_process(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        worker = importlib.resources.files("soleaux.resources.typescript").joinpath(
            "node_worker.cjs"
        )
        environment = soleaux.postgresql.runtime.build_safe_environment(
            {"SOLEAUX_TYPESCRIPT_RUNTIME": str(self._installation.prefix)},
            environment_names=("SOLEAUX_TYPESCRIPT_RUNTIME",),
        )
        with importlib.resources.as_file(worker) as worker_path:
            self._process = subprocess.Popen(
                [self._installation.node_executable, str(worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        return self._process

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._terminate()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
