"""Engine-neutral structural execution behind exactly one configured backend.

The Python engine is the zero-config default and runs inside the supervised
structural worker. The NAPI and Rust engines are long-lived JSONL workers
loaded from package-owned or installer-owned locations; repository
configuration cannot select an executable or package path. A missing or
mismatched engine fails closed with exact install guidance and never falls
back. Callers hand this module `soleaux.structural/v1` matchers and receive typed
findings and edits — engine-native AST objects never cross the boundary.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import importlib.resources
import json
import pathlib
import shutil
import sys
import typing

import platformdirs

import soleaux.contracts.config
import soleaux.contracts.structural
import soleaux.postgresql.runtime
import soleaux.structural.fragments
import soleaux.structural.rules
import soleaux.structural.supervisor
import soleaux.structural.workspace_rules

RUST_WORKER_NAME = "soleaux-ast-grep-worker"
RUST_INSTALL_COMMAND = "soleaux install ast-grep-rust"


class StructuralEngineError(Exception):
    """Fail-closed engine selection or execution failure with guidance."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type
        self.message = message


@dataclasses.dataclass(frozen=True)
class ResolvedMatcher:
    """One matcher normalized to the engine wire form plus rule metadata."""

    language: str
    matcher: dict[str, typing.Any]
    fix: str | dict[str, typing.Any] | None
    transforms: dict[str, typing.Any] | None
    rule_id: str | None = None
    severity: str | None = None
    message: str | None = None


@dataclasses.dataclass(frozen=True)
class StructuralOutcome:
    """Typed findings and edits from one bounded engine job."""

    findings: tuple[soleaux.contracts.structural.StructuralFinding, ...]
    edits: tuple[soleaux.contracts.structural.StructuralEdit, ...]
    truncated: bool
    errors: tuple[str, ...]
    engine: soleaux.contracts.structural.StructuralBackend
    engine_version: str


def managed_rust_binary_path() -> pathlib.Path:
    """The managed build location owned by `soleaux install ast-grep-rust`."""
    return (
        platformdirs.user_cache_path("soleaux")
        / "ast-grep-rust"
        / soleaux.structural.fragments.AST_GREP_VERSION
        / RUST_WORKER_NAME
    )


def _wire_fix(
    fix: str | soleaux.contracts.structural.FixConfig | None,
) -> str | dict[str, typing.Any] | None:
    if isinstance(fix, soleaux.contracts.structural.FixConfig):
        return {
            "template": fix.template,
            "expand_start": fix.expand_start,
            "expand_end": fix.expand_end,
        }
    return fix


def resolve_matcher(
    matcher: soleaux.contracts.structural.InlinePattern
    | soleaux.contracts.structural.InlineRule
    | soleaux.contracts.structural.RuleReference,
    *,
    root: pathlib.Path,
    config: soleaux.contracts.config.StructuralConfig,
) -> ResolvedMatcher:
    """Normalize one v1 matcher; rule references never accept caller overrides."""
    if isinstance(matcher, soleaux.contracts.structural.InlinePattern):
        return ResolvedMatcher(
            language=matcher.language,
            matcher={"kind": "pattern", "pattern": matcher.pattern},
            fix=_wire_fix(matcher.fix),
            transforms={
                name: item.model_dump(mode="json") for name, item in matcher.transforms.items()
            }
            or None,
        )
    if isinstance(matcher, soleaux.contracts.structural.InlineRule):
        soleaux.contracts.structural.validate_rule_fields(matcher.rule)
        return ResolvedMatcher(
            language=matcher.language,
            matcher={
                "kind": "rule",
                "rule": matcher.rule,
                "constraints": matcher.constraints,
                "utils": matcher.utils,
            },
            fix=_wire_fix(matcher.fix),
            transforms={
                name: item.model_dump(mode="json") for name, item in matcher.transforms.items()
            }
            or None,
        )
    packaged = soleaux.structural.rules.packaged_rules().get(matcher.rule_id)
    if packaged is not None:
        return ResolvedMatcher(
            language=packaged.language,
            matcher={"kind": "rule", "rule": dict(packaged.rule)},
            fix=None,
            transforms=None,
            rule_id=packaged.id,
            severity=packaged.severity,
            message=packaged.message,
        )
    if config.project_config is None:
        raise StructuralEngineError(
            "unknown_rule",
            f"{matcher.rule_id!r} is not packaged and no [structural].project_config is set",
        )
    workspace, _warnings = soleaux.structural.workspace_rules.load_workspace_rules(
        root, config.project_config
    )
    selected: soleaux.structural.workspace_rules.WorkspaceRule | None = workspace.get(
        matcher.rule_id
    )
    if selected is None:
        raise StructuralEngineError(
            "unknown_rule",
            f"{matcher.rule_id!r} is not a packaged or configured workspace rule",
        )
    return ResolvedMatcher(
        language=selected.language,
        matcher={
            "kind": "rule",
            "rule": selected.rule,
            "constraints": selected.constraints,
            "utils": selected.utils,
        },
        fix=selected.fix,
        transforms=selected.transforms,
        rule_id=selected.rule_id,
        severity=selected.severity,
        message=selected.message,
    )


class _JsonlWorkerClient:
    """One long-lived JSONL worker: single-flight requests, typed errors."""

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        engine: str,
        engine_version: str,
    ) -> None:
        self._argv = argv
        self._engine = engine
        self._engine_version = engine_version
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0

    async def _ensure(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=soleaux.structural.supervisor.MAX_FRAME_BYTES + 2,
                env=soleaux.postgresql.runtime.build_safe_environment(
                    {},
                    environment_names=(),
                ),
            )
        except OSError:
            self._process = None
            raise StructuralEngineError(
                "engine_unavailable",
                f"{self._engine} worker could not start",
            ) from None
        try:
            ping = await self._roundtrip(
                {"op": "ping"},
                timeout=soleaux.structural.supervisor.JOB_TIMEOUT_SECONDS,
                handshake=True,
            )
        except BaseException:
            await self.aclose()
            raise
        if (
            ping.get("ok") is not True
            or ping.get("engine") != self._engine
            or ping.get("engine_version") != self._engine_version
            or ping.get("capabilities")
            != list(soleaux.structural.fragments.STRUCTURAL_WORKER_CAPABILITIES)
        ):
            await self.aclose()
            raise StructuralEngineError(
                "engine_identity",
                f"{self._engine} worker did not prove the expected "
                f"{self._engine_version} engine/version/capability identity",
            )
        return self._process

    async def _read_frame(self, process: asyncio.subprocess.Process) -> dict[str, typing.Any]:
        assert process.stdout is not None
        line = await process.stdout.readline()
        if not line:
            raise StructuralEngineError("engine_unavailable", f"{self._engine} worker exited")
        if len(line) > soleaux.structural.supervisor.MAX_FRAME_BYTES:
            raise StructuralEngineError("protocol", f"{self._engine} frame exceeds the byte cap")
        parsed: object = json.loads(line)
        if not isinstance(parsed, dict):
            raise StructuralEngineError("protocol", f"{self._engine} returned a non-object frame")
        return typing.cast("dict[str, typing.Any]", parsed)

    async def _roundtrip(
        self,
        payload: dict[str, typing.Any],
        *,
        timeout: float,
        handshake: bool = False,
    ) -> dict[str, typing.Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise StructuralEngineError("engine_unavailable", f"{self._engine} worker not running")
        self._request_id += 1
        request = {**payload, "id": self._request_id}
        frame = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(frame) > soleaux.structural.supervisor.MAX_FRAME_BYTES:
            raise StructuralEngineError("protocol", "request frame exceeds the byte cap")
        try:
            process.stdin.write(frame)
            await process.stdin.drain()
            async with asyncio.timeout(timeout):
                while True:
                    response = await self._read_frame(process)
                    if handshake and "ready" in response:
                        continue
                    if response.get("id") == self._request_id:
                        return response
        except TimeoutError:
            await self.aclose()
            raise StructuralEngineError(
                "engine_unavailable", f"{self._engine} worker missed its deadline"
            ) from None
        except (BrokenPipeError, ConnectionResetError, ValueError) as exc:
            await self.aclose()
            raise StructuralEngineError("protocol", f"{self._engine}: {exc}") from exc

    async def request(
        self, payload: dict[str, typing.Any], *, timeout: float
    ) -> dict[str, typing.Any]:
        async with self._lock:
            await self._ensure()
            response = await self._roundtrip(payload, timeout=timeout)
            error = response.get("error")
            if isinstance(error, dict):
                typed = typing.cast("dict[str, typing.Any]", error)
                raise StructuralEngineError(
                    str(typed.get("type", "engine_failure")),
                    str(typed.get("message", "")),
                )
            return response

    async def aclose(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(5.0):
                await process.wait()


class StructuralEngines:
    """Run bounded structural jobs on the one configured backend."""

    def __init__(
        self,
        supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
        *,
        root: pathlib.Path,
        config: soleaux.contracts.config.StructuralConfig,
    ) -> None:
        self._supervisor = supervisor
        self._root = root
        self._config = config
        self._napi: _JsonlWorkerClient | None = None
        self._rust: _JsonlWorkerClient | None = None
        self._resources = contextlib.ExitStack()

    def _engine_resolve_root(self) -> str | None:
        """Nearest ancestor with the napi engine package, if any.

        Engine and language packages resolve from the soleaux runtime
        environment, never from the analyzed root: the interpreter
        environment first, then the package location, so host-venv and
        standalone checkouts both resolve even when the worker is consumed
        through a link whose realpath has no node_modules.
        """
        anchors = [
            pathlib.Path(sys.prefix).parent,
            *pathlib.Path(sys.prefix).parents,
            pathlib.Path(__file__).parent,
            *pathlib.Path(__file__).parents,
        ]
        for anchor in anchors:
            if (anchor / "node_modules" / "@ast-grep" / "napi").is_dir():
                return str(anchor)
        return None

    @property
    def backend(self) -> soleaux.contracts.structural.StructuralBackend:
        return soleaux.contracts.structural.StructuralBackend(self._config.backend)

    def resolve(
        self,
        matcher: soleaux.contracts.structural.InlinePattern
        | soleaux.contracts.structural.InlineRule
        | soleaux.contracts.structural.RuleReference,
    ) -> ResolvedMatcher:
        return resolve_matcher(matcher, root=self._root, config=self._config)

    async def run(
        self,
        resolved: ResolvedMatcher,
        *,
        files: tuple[tuple[str, bytes], ...],
        want: tuple[str, ...] = ("findings",),
        limits: dict[str, int] | None = None,
        timeout: float = soleaux.structural.supervisor.JOB_TIMEOUT_SECONDS,
    ) -> StructuralOutcome:
        backend = self.backend
        wire_fix: dict[str, typing.Any] | None = (
            {"text": resolved.fix} if isinstance(resolved.fix, str) else resolved.fix
        )
        if backend is soleaux.contracts.structural.StructuralBackend.PYTHON:
            try:
                response = await self._supervisor.structural(
                    language=resolved.language,
                    matcher=resolved.matcher,
                    files=files,
                    fix=wire_fix,
                    transforms=resolved.transforms,
                    want=want,
                    limits=limits,
                    timeout=timeout,
                )
            except soleaux.structural.supervisor.WorkerJobError as exc:
                raise StructuralEngineError(exc.error_type, str(exc)) from exc
            except soleaux.structural.supervisor.WorkerUnavailableError as exc:
                raise StructuralEngineError("engine_unavailable", str(exc)) from exc
        else:
            client = await self._client(backend)
            response = await client.request(
                {
                    "op": "structural",
                    "language": resolved.language,
                    "matcher": resolved.matcher,
                    "fix": wire_fix,
                    "transforms": resolved.transforms,
                    "want": list(want),
                    "limits": limits or {},
                    "files": [
                        {
                            "path": path,
                            "content_b64": base64.b64encode(content).decode("ascii"),
                        }
                        for path, content in files
                    ],
                    "mirror_root": None,
                    "glob_paths": None,
                },
                timeout=timeout,
            )
        return self._decode(backend, resolved, response)

    async def _client(
        self, backend: soleaux.contracts.structural.StructuralBackend
    ) -> _JsonlWorkerClient:
        if backend is soleaux.contracts.structural.StructuralBackend.NAPI:
            if self._napi is None:
                node = shutil.which("node")
                if node is None:
                    raise StructuralEngineError("engine_unavailable", "node is not on PATH")
                worker = self._resources.enter_context(
                    importlib.resources.as_file(
                        importlib.resources.files("soleaux.resources").joinpath(
                            "structural/napi_worker.mjs"
                        )
                    )
                )
                launch_config: dict[str, typing.Any] = {
                    "languages": [
                        {"name": name, "package": package}
                        for name, package in sorted(self._config.languages.items())
                    ],
                }
                resolve_root = self._engine_resolve_root()
                if resolve_root is not None:
                    launch_config["resolve_root"] = resolve_root
                self._napi = _JsonlWorkerClient(
                    (node, str(worker), json.dumps(launch_config, separators=(",", ":"))),
                    engine="napi",
                    engine_version=soleaux.structural.fragments.AST_GREP_VERSION,
                )
            return self._napi
        if self._rust is None:
            binary = managed_rust_binary_path()
            if not binary.is_file():
                raise StructuralEngineError(
                    "engine_unavailable",
                    f"rust worker {binary} is missing; run `{RUST_INSTALL_COMMAND}` "
                    "to build the pinned "
                    f"{soleaux.structural.fragments.AST_GREP_VERSION} worker",
                )
            self._rust = _JsonlWorkerClient(
                (str(binary),),
                engine="rust",
                engine_version=soleaux.structural.fragments.AST_GREP_VERSION,
            )
        return self._rust

    def _decode(
        self,
        backend: soleaux.contracts.structural.StructuralBackend,
        resolved: ResolvedMatcher,
        response: dict[str, typing.Any],
    ) -> StructuralOutcome:
        engine_version = response.get("engine_version")
        if (
            response.get("engine") != backend.value
            or engine_version != soleaux.structural.fragments.AST_GREP_VERSION
        ):
            raise StructuralEngineError(
                "engine_identity",
                f"{backend.value} response did not prove the expected "
                f"{soleaux.structural.fragments.AST_GREP_VERSION} engine identity",
            )
        findings: list[soleaux.contracts.structural.StructuralFinding] = []
        raw_findings = response.get("findings")
        finding_rows = (
            typing.cast("list[object]", raw_findings) if isinstance(raw_findings, list) else []
        )
        for row in finding_rows:
            record = typing.cast("dict[str, typing.Any]", row) if isinstance(row, dict) else {}
            raw_captures = record.get("captures")
            capture_rows = (
                typing.cast("list[object]", raw_captures) if isinstance(raw_captures, list) else []
            )
            captures = tuple(
                soleaux.contracts.structural.StructuralCapture.model_validate(capture)
                for capture in capture_rows
            )
            findings.append(
                soleaux.contracts.structural.StructuralFinding(
                    path=str(record.get("path", "")),
                    rule_id=resolved.rule_id,
                    engine=backend,
                    engine_version=engine_version,
                    language=resolved.language,
                    severity=resolved.severity,
                    message=resolved.message,
                    byte_start=int(record.get("byte_start", 0)),
                    byte_end=int(record.get("byte_end", 0)),
                    start_line=int(record.get("start_line", 0)),
                    start_column=int(record.get("start_column", 0)),
                    end_line=int(record.get("end_line", 0)),
                    end_column=int(record.get("end_column", 0)),
                    text_preview=str(record.get("text_preview", "")),
                    captures=captures,
                )
            )
        raw_edits = response.get("edits")
        edit_rows = typing.cast("list[object]", raw_edits) if isinstance(raw_edits, list) else []
        edits = tuple(
            soleaux.contracts.structural.StructuralEdit.model_validate(row) for row in edit_rows
        )
        raw_errors = response.get("errors")
        error_rows = typing.cast("list[object]", raw_errors) if isinstance(raw_errors, list) else []
        errors: list[str] = []
        for row in error_rows:
            if isinstance(row, dict):
                entry = typing.cast("dict[str, typing.Any]", row)
                errors.append(f"{entry.get('path', '')}: {entry.get('message', '')}")
            else:
                errors.append(str(row))
        return StructuralOutcome(
            findings=tuple(findings),
            edits=edits,
            truncated=bool(response.get("truncated", False)),
            errors=tuple(errors),
            engine=backend,
            engine_version=engine_version,
        )

    async def aclose(self) -> None:
        for client in (self._napi, self._rust):
            if client is not None:
                await client.aclose()
        self._napi = None
        self._rust = None
        self._resources.close()


__all__ = [
    "RUST_INSTALL_COMMAND",
    "ResolvedMatcher",
    "StructuralEngineError",
    "StructuralEngines",
    "StructuralOutcome",
    "managed_rust_binary_path",
    "resolve_matcher",
]
