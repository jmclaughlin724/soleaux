"""Managed Node runtime for the PostgreSQL 17 parser.

Runtime discovery is read-only. Provisioning is an explicit setup operation,
and parser execution stays in one lazy, supervised JSON-lines child process.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import BinaryIO, Final

from platformdirs import user_data_path
from pydantic import TypeAdapter, ValidationError

from soleaux.postgresql.runtime import build_safe_environment

PARSER_PACKAGE: Final = "@libpg-query/parser"
PARSER_VERSION: Final = "17.6.10"
PARSER_SPEC: Final = f"{PARSER_PACKAGE}@{PARSER_VERSION}"
MANAGED_PREFIX_ENV: Final = "SOLEAUX_POSTGRESQL_PARSER_PREFIX"
MAX_FRAME_BYTES: Final = 8 * 1024 * 1024
MAX_SOURCE_BYTES: Final = 4 * 1024 * 1024
DEFAULT_DEADLINE_SECONDS: Final = 5.0
DEFAULT_SHUTDOWN_GRACE_SECONDS: Final = 1.0
# resources/postgresql/node_worker.cjs calls Error.isError (Node >= 24).
# Language servers (typescript-language-server, @postgres-language-server/cli)
# instead rely on their declared `engines: node >=20`, enforced by the
# installer at provisioning time, so this floor is the only spawn-time gate.
MINIMUM_NODE_MAJOR: Final = 24
_OBJECT_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])


class NodeParserError(RuntimeError):
    """Base failure for the managed PostgreSQL parser runtime."""


class NodeParserUnavailableError(NodeParserError):
    """The pinned parser or Node executable is unavailable."""


class NodeParserProtocolError(NodeParserError):
    """The Node worker violated its bounded JSON-lines contract."""


class NodeParserDeadlineError(NodeParserError):
    """The Node worker missed its synchronous request deadline."""


class NodeParserProvisionError(NodeParserError):
    """Explicit setup could not provision the pinned parser."""


def _require_supported_node(node: str) -> None:
    """Fail fast when the resolved Node cannot run the packaged worker."""
    try:
        completed = subprocess.run(
            (node, "--version"),
            capture_output=True,
            check=False,
            env=build_safe_environment({}, environment_names=()),
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise NodeParserUnavailableError(
            "the discovered Node executable could not be probed"
        ) from error
    version_text = completed.stdout.strip()
    major_text, separator, _ = version_text.lstrip("v").partition(".")
    if completed.returncode != 0 or not separator or not major_text.isdigit():
        raise NodeParserUnavailableError("the discovered Node executable did not report a version")
    if int(major_text) < MINIMUM_NODE_MAJOR:
        raise NodeParserUnavailableError(
            f"the PostgreSQL parser worker requires Node.js >= {MINIMUM_NODE_MAJOR} "
            f"(Error.isError); discovered {version_text or 'an unknown version'} at {node}"
        )


class NodeParserParseError(NodeParserError):
    """The PostgreSQL parser rejected source text."""

    def __init__(self, message: str, *, cursor_position: int | None) -> None:
        super().__init__(message)
        self.cursor_position = cursor_position
        self.cursor_unit = "unicode_code_point"


@dataclass(frozen=True, slots=True)
class ParserInstallation:
    """One validated exact parser installation under a managed prefix."""

    prefix: Path
    package_json: Path
    version: str


@dataclass(frozen=True, slots=True)
class ScanToken:
    """One scanner token whose range is measured in UTF-8 bytes."""

    start: int
    end: int
    text: str
    token_type: int
    token_name: str


@dataclass(frozen=True, slots=True)
class ParserIssue:
    """One scanner-bounded statement that the parser could not recover."""

    message: str
    byte_start: int
    byte_end: int


@dataclass(frozen=True, slots=True)
class EmbeddedQuery:
    """One PL/pgSQL expression with line-only source provenance."""

    line: int
    dynamic: bool
    parse_tree: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class ParserDocument:
    """Validated parser and scanner output for one source document."""

    parse_tree: Mapping[str, object]
    tokens: tuple[ScanToken, ...]
    parser_version: str
    postgresql_version: int
    offset_unit: str = "utf8_byte"
    recovered: bool = False
    issues: tuple[ParserIssue, ...] = ()
    embedded_queries: tuple[EmbeddedQuery, ...] = ()
    plpgsql_error: str | None = None


@dataclass(frozen=True, slots=True)
class _ReaderEof:
    pass


@dataclass(frozen=True, slots=True)
class _ReaderFailure:
    error: OSError


type _FrameRead = bytes | _ReaderEof | _ReaderFailure


def default_managed_prefix() -> Path:
    """Return the canonical per-user parser prefix without creating it."""
    return user_data_path("soleaux", appauthor=False) / "postgresql-parser"


def configured_managed_prefix(
    prefix: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve an explicit override or the canonical managed parser prefix."""
    if prefix is not None:
        return _normalize_prefix(prefix)
    environment = os.environ if environ is None else environ
    override = environment.get(MANAGED_PREFIX_ENV)
    if override:
        return _normalize_prefix(Path(override))
    return _normalize_prefix(default_managed_prefix())


def resolve_parser_installation(
    prefix: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ParserInstallation | None:
    """Discover the exact managed parser without searching or writing."""
    managed_prefix = configured_managed_prefix(prefix, environ=environ)
    root_manifest = managed_prefix / "package.json"
    package_manifest = managed_prefix / "node_modules" / "@libpg-query" / "parser" / "package.json"
    if not root_manifest.is_file() or not package_manifest.is_file():
        return None
    package = _read_json_object(package_manifest)
    name = package.get("name")
    installed_version = package.get("version")
    if name != PARSER_PACKAGE:
        raise NodeParserUnavailableError(
            f"managed parser manifest names {name!r}, expected {PARSER_PACKAGE!r}"
        )
    if installed_version != PARSER_VERSION:
        raise NodeParserUnavailableError(
            f"managed parser version is {installed_version!r}, expected {PARSER_VERSION!r}"
        )
    return ParserInstallation(
        prefix=managed_prefix,
        package_json=package_manifest,
        version=PARSER_VERSION,
    )


def managed_parser_version(
    prefix: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the usable managed parser version for doctor reporting."""
    try:
        installation = resolve_parser_installation(prefix, environ=environ)
    except NodeParserUnavailableError:
        return "unavailable"
    return installation.version if installation is not None else "unavailable"


def provision_parser(
    prefix: Path | None = None,
    *,
    package_manager: str = "npm",
    timeout_seconds: float = 120.0,
    environ: Mapping[str, str] | None = None,
) -> ParserInstallation:
    """Explicitly install the exact parser into its dedicated managed prefix."""
    managed_prefix = configured_managed_prefix(prefix, environ=environ)
    root_manifest = managed_prefix / "package.json"
    if managed_prefix.exists() and not managed_prefix.is_dir():
        raise NodeParserProvisionError(f"managed prefix is not a directory: {managed_prefix}")
    if managed_prefix.is_dir() and not root_manifest.exists():
        existing = next(managed_prefix.iterdir(), None)
        if existing is not None:
            raise NodeParserProvisionError(
                "refusing to provision into a nonempty directory without package.json"
            )
    managed_prefix.mkdir(parents=True, exist_ok=True)
    if not root_manifest.exists():
        root_manifest.write_text(
            '{"name":"soleaux-postgresql-parser-runtime","private":true}\n',
            encoding="utf-8",
        )

    executable = _resolve_executable(package_manager)
    command = (
        executable,
        "install",
        "--prefix",
        str(managed_prefix),
        "--save-exact",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        PARSER_SPEC,
    )
    environment = build_safe_environment(
        {},
        environment_names=(),
        inherited_environment=environ,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=managed_prefix,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NodeParserProvisionError(
            f"parser provisioning timed out after {timeout_seconds:g} seconds"
        ) from exc
    if completed.returncode != 0:
        raise NodeParserProvisionError(
            f"parser provisioning failed with exit {completed.returncode}"
        )
    installation = resolve_parser_installation(managed_prefix)
    if installation is None:
        raise NodeParserProvisionError(
            "package manager succeeded but the exact parser installation was not found"
        )
    return installation


class NodeParserRuntime:
    """Lazy synchronous client for one bounded Node parser worker."""

    def __init__(
        self,
        installation: ParserInstallation,
        *,
        node_executable: str | None = None,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        self._installation = installation
        self._node_executable = node_executable
        self._deadline_seconds = deadline_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._lock = threading.RLock()
        self._request_id = 0
        self._process: subprocess.Popen[bytes] | None = None
        self._frames: queue.Queue[_FrameRead] | None = None
        self._reader: threading.Thread | None = None
        self._resource_context: AbstractContextManager[Path] | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        """Return the live child PID, if the parser has been started."""
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    @property
    def started(self) -> bool:
        """Whether a live Node parser child currently exists."""
        return self.pid is not None

    def analyze(self, source: str) -> ParserDocument:
        """Parse and scan one source exactly once in the managed worker."""
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > MAX_SOURCE_BYTES:
            raise NodeParserProtocolError("source exceeds the 4 MiB parser cap")
        response = self._exchange({"op": "analyze", "source": source})
        return _parse_document(response)

    def close(self) -> None:
        """Request bounded shutdown, then terminate/kill and reap if needed."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            if process is not None and process.poll() is None:
                with contextlib.suppress(NodeParserError):
                    self._exchange(
                        {"op": "shutdown"},
                        timeout_seconds=min(
                            self._deadline_seconds,
                            self._shutdown_grace_seconds,
                        ),
                        allow_closed=True,
                    )
            self._reap_process()

    def __enter__(self) -> NodeParserRuntime:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _exchange(
        self,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
        allow_closed: bool = False,
    ) -> Mapping[str, object]:
        with self._lock:
            if self._closed and not allow_closed:
                raise NodeParserUnavailableError("Node parser runtime is closed")
            self._ensure_process()
            process = self._process
            frames = self._frames
            if process is None or process.stdin is None or frames is None:
                raise NodeParserUnavailableError("Node parser worker did not start")
            self._request_id += 1
            request = {"id": self._request_id, **payload}
            frame = (
                json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            if len(frame) > MAX_FRAME_BYTES:
                raise NodeParserProtocolError("request frame exceeds the 8 MiB cap")
            try:
                process.stdin.write(frame)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._reap_process()
                raise NodeParserUnavailableError("Node parser worker closed its input") from exc

            deadline = self._deadline_seconds if timeout_seconds is None else timeout_seconds
            try:
                read = frames.get(timeout=deadline)
            except queue.Empty:
                self._reap_process()
                raise NodeParserDeadlineError(
                    f"Node parser worker missed its {deadline:g}-second deadline"
                ) from None
            if isinstance(read, _ReaderFailure):
                self._reap_process()
                raise NodeParserUnavailableError(
                    "could not read Node parser output"
                ) from read.error
            if isinstance(read, _ReaderEof):
                self._reap_process()
                raise NodeParserUnavailableError("Node parser worker exited before replying")
            if len(read) > MAX_FRAME_BYTES or not read.endswith(b"\n"):
                self._reap_process()
                raise NodeParserProtocolError("Node parser response exceeded its frame bound")
            try:
                decoded = json.loads(read)
            except json.JSONDecodeError:
                self._reap_process()
                raise NodeParserProtocolError("Node parser returned malformed JSON") from None
            response = _as_object_mapping(decoded, "Node parser response")
            if response.get("id") != self._request_id:
                self._reap_process()
                raise NodeParserProtocolError("Node parser returned a mismatched frame id")
            if response.get("status") != "ok":
                self._raise_worker_error(response)
            return response

    def _ensure_process(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            return
        if self._closed:
            raise NodeParserUnavailableError("Node parser runtime is closed")
        current = resolve_parser_installation(self._installation.prefix)
        if current is None:
            raise NodeParserUnavailableError("managed parser installation disappeared")
        node = self._node_executable or shutil.which("node")
        if node is None:
            raise NodeParserUnavailableError("Node executable was not discovered")
        _require_supported_node(node)

        resource = (
            files("soleaux")
            .joinpath("resources")
            .joinpath("postgresql")
            .joinpath("node_worker.cjs")
        )
        if not resource.is_file():
            raise NodeParserUnavailableError("packaged PostgreSQL Node worker is missing")
        resource_context = as_file(resource)
        worker_path = resource_context.__enter__()
        environment = build_safe_environment({}, environment_names=())
        try:
            process = subprocess.Popen(
                (node, str(worker_path), str(self._installation.prefix)),
                cwd=self._installation.prefix,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError:
            resource_context.__exit__(None, None, None)
            raise
        if process.stdout is None:
            process.terminate()
            process.wait(timeout=self._shutdown_grace_seconds)
            resource_context.__exit__(None, None, None)
            raise NodeParserUnavailableError("Node parser stdout pipe is unavailable")
        frames: queue.Queue[_FrameRead] = queue.Queue()
        reader = threading.Thread(
            target=_read_frames,
            args=(process.stdout, frames),
            name="soleaux-postgresql-parser-output",
            daemon=True,
        )
        self._process = process
        self._frames = frames
        self._reader = reader
        self._resource_context = resource_context
        reader.start()

    def _raise_worker_error(self, response: Mapping[str, object]) -> None:
        raw_error = response.get("error")
        error = _as_object_mapping(raw_error, "Node parser error")
        error_type = str(error.get("type", "parser_failure"))
        message = str(error.get("message", "Node parser failed"))[:280]
        if error_type == "parse_error":
            raw_cursor = error.get("cursor_position")
            cursor = (
                raw_cursor
                if isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool)
                else None
            )
            raise NodeParserParseError(message, cursor_position=cursor)
        if error_type == "deadline":
            raise NodeParserDeadlineError(message)
        raise NodeParserError(f"{error_type}: {message}")

    def _reap_process(self) -> None:
        process = self._process
        reader = self._reader
        resource_context = self._resource_context
        self._process = None
        self._frames = None
        self._reader = None
        self._resource_context = None
        if process is not None:
            if process.stdin is not None:
                with contextlib.suppress(OSError):
                    process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self._shutdown_grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            else:
                process.wait()
            if process.stdout is not None:
                with contextlib.suppress(OSError):
                    process.stdout.close()
        if reader is not None and reader.is_alive():
            reader.join(timeout=self._shutdown_grace_seconds)
        if resource_context is not None:
            resource_context.__exit__(None, None, None)


def _normalize_prefix(prefix: Path) -> Path:
    expanded = prefix.expanduser()
    if not expanded.is_absolute():
        raise ValueError("managed parser prefix must be absolute")
    normalized = expanded.resolve(strict=False)
    if normalized == Path(normalized.anchor):
        raise ValueError("managed parser prefix cannot be a filesystem root")
    if normalized == Path.home().resolve():
        raise ValueError("managed parser prefix cannot be the home directory")
    return normalized


def _read_json_object(path: Path) -> Mapping[str, object]:
    if path.stat().st_size > 64 * 1024:
        raise NodeParserUnavailableError(f"managed manifest is unexpectedly large: {path}")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NodeParserUnavailableError(f"managed manifest is invalid JSON: {path}") from exc
    return _as_object_mapping(decoded, f"managed manifest {path}")


def _resolve_executable(command: str) -> str:
    candidate = Path(command)
    if candidate.parent != Path():
        if candidate.is_file():
            return str(candidate)
        raise NodeParserProvisionError(f"package manager was not found: {command}")
    executable = shutil.which(command)
    if executable is None:
        raise NodeParserProvisionError(f"package manager was not found: {command}")
    return executable


def _read_frames(stream: BinaryIO, frames: queue.Queue[_FrameRead]) -> None:
    try:
        while True:
            frame = stream.readline(MAX_FRAME_BYTES + 1)
            if not frame:
                frames.put(_ReaderEof())
                return
            frames.put(frame)
            if len(frame) > MAX_FRAME_BYTES or not frame.endswith(b"\n"):
                return
    except OSError as exc:
        frames.put(_ReaderFailure(exc))


def _as_object_mapping(value: object, label: str) -> Mapping[str, object]:
    try:
        return _OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise NodeParserProtocolError(f"{label} must be a JSON object") from None


def _as_object_list(value: object, label: str) -> list[object]:
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        raise NodeParserProtocolError(f"{label} must be a list") from None


def _parse_document(response: Mapping[str, object]) -> ParserDocument:
    parser = _as_object_mapping(response.get("parser"), "parser metadata")
    if parser.get("package") != PARSER_PACKAGE or parser.get("version") != PARSER_VERSION:
        raise NodeParserProtocolError("Node worker reported an unexpected parser identity")
    if response.get("offset_unit") != "utf8_byte":
        raise NodeParserProtocolError("Node worker did not declare UTF-8 byte offsets")
    parse_tree = _as_object_mapping(response.get("parse_tree"), "parse tree")
    scan = _as_object_mapping(response.get("scan"), "scanner result")
    raw_version = parse_tree.get("version")
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise NodeParserProtocolError("parse tree version must be an integer")
    raw_tokens = _as_object_list(scan.get("tokens"), "scanner tokens")
    tokens = tuple(_parse_scan_token(value) for value in raw_tokens)
    recovered = response.get("recovered", False)
    if not isinstance(recovered, bool):
        raise NodeParserProtocolError("parser recovery flag must be a boolean")
    raw_issues = _as_object_list(response.get("parse_errors", []), "parser errors")
    issues = tuple(_parse_issue(value) for value in raw_issues)
    raw_embedded_queries = _as_object_list(
        response.get("embedded_queries", []),
        "embedded queries",
    )
    embedded_queries = tuple(_parse_embedded_query(value) for value in raw_embedded_queries)
    raw_plpgsql_error = response.get("plpgsql_error")
    if raw_plpgsql_error is not None and not isinstance(raw_plpgsql_error, str):
        raise NodeParserProtocolError("PL/pgSQL parser error must be text")
    return ParserDocument(
        parse_tree=parse_tree,
        tokens=tokens,
        parser_version=PARSER_VERSION,
        postgresql_version=raw_version,
        recovered=recovered,
        issues=issues,
        embedded_queries=embedded_queries,
        plpgsql_error=raw_plpgsql_error,
    )


def _parse_issue(value: object) -> ParserIssue:
    issue = _as_object_mapping(value, "parser issue")
    message = issue.get("message")
    byte_start = issue.get("byte_start")
    byte_end = issue.get("byte_end")
    if (
        not isinstance(message, str)
        or not message
        or not isinstance(byte_start, int)
        or isinstance(byte_start, bool)
        or not isinstance(byte_end, int)
        or isinstance(byte_end, bool)
        or byte_start < 0
        or byte_end < byte_start
    ):
        raise NodeParserProtocolError("parser issue has an invalid typed range")
    return ParserIssue(
        message=message[:280],
        byte_start=byte_start,
        byte_end=byte_end,
    )


def _parse_embedded_query(value: object) -> EmbeddedQuery:
    query = _as_object_mapping(value, "embedded query")
    line = query.get("line")
    dynamic = query.get("dynamic")
    raw_parse_tree = query.get("parse_tree")
    if (
        not isinstance(line, int)
        or isinstance(line, bool)
        or line < 0
        or not isinstance(dynamic, bool)
    ):
        raise NodeParserProtocolError("embedded query has invalid provenance")
    parse_tree = (
        None
        if raw_parse_tree is None
        else _as_object_mapping(raw_parse_tree, "embedded query parse tree")
    )
    if dynamic and parse_tree is not None:
        raise NodeParserProtocolError("dynamic SQL cannot claim a static parse tree")
    return EmbeddedQuery(line=line, dynamic=dynamic, parse_tree=parse_tree)


def _parse_scan_token(value: object) -> ScanToken:
    token = _as_object_mapping(value, "scanner token")
    start = token.get("start")
    end = token.get("end")
    token_type = token.get("tokenType")
    text = token.get("text")
    token_name = token.get("tokenName")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(token_type, int)
        or isinstance(token_type, bool)
        or not isinstance(text, str)
        or not isinstance(token_name, str)
        or start < 0
        or end < start
    ):
        raise NodeParserProtocolError("scanner token has an invalid typed range")
    return ScanToken(
        start=start,
        end=end,
        text=text,
        token_type=token_type,
        token_name=token_name,
    )
