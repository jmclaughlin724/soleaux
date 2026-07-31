"""Direct lazy LSP broker owning JSON-RPC framing and lifecycle (D023, D031).

One minimal stdio broker per provider: framing, request IDs, pending
responses, server-initiated requests, dynamic registration, document
synchronization, cancellation, bounded late-response tombstones, shutdown,
and process-tree cleanup. No lsp-client or lsprotocol dependency.
"""

from __future__ import annotations

import asyncio
import collections
import collections.abc
import dataclasses
import json
import logging
import os
import pathlib
import signal
import subprocess
import tempfile
import time
import typing

from pydantic import TypeAdapter

import soleaux.contracts.budget
import soleaux.lsp.contracts
import soleaux.lsp.diagnostics
import soleaux.postgresql.runtime

_logger = logging.getLogger(__name__)
WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# JSON-RPC error codes
PARSED_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR_START = -32099
SERVER_NOT_INITIALIZED = -32002
REQUEST_CANCELLED = -32800
CONTENT_REDHERRING = -32801
LATE_RESPONSE_TTL_SECONDS = 60.0
MAX_LATE_RESPONSE_TOMBSTONES = 256
MAX_RETAINED_NOTIFICATIONS_PER_METHOD = 64
_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])


class LspBrokerError(Exception):
    """Base broker error."""


class ProviderUnavailableError(LspBrokerError):
    """No provider available for the requested language/operation."""


class SemanticProviderRequiredError(LspBrokerError):
    """semantic_required mode could not satisfy a prerequisite."""


class OperationTimeoutError(LspBrokerError):
    """The operation exceeded its deadline."""


@dataclasses.dataclass
class _PendingRequest:
    """One outstanding JSON-RPC request awaiting a response."""

    future: asyncio.Future[typing.Any]
    method: str
    params: typing.Any


@dataclasses.dataclass
class _DocumentState:
    """Tracked open document with content and monotonic version."""

    uri: str
    language_id: str
    version: int
    content: str


@dataclasses.dataclass
class RegistrationEntry:
    """One retained dynamic registration."""

    id: str
    method: str
    register_options: dict[str, typing.Any]


class LspBroker:
    """One lazy stdio JSON-RPC/LSP broker per provider spec."""

    def __init__(
        self,
        spec: soleaux.lsp.contracts.LanguageServerSpec,
        budget: soleaux.contracts.budget.LspSessionBudget | None = None,
        *,
        process_epoch: int = 0,
        workspace_root: str | None = None,
    ) -> None:
        self._spec = spec
        self._budget = budget or soleaux.contracts.budget.LspSessionBudget()
        self._workspace_root = workspace_root
        self._process_epoch = process_epoch
        self._proc: asyncio.subprocess.Process | None = None
        self._postgresql_runtime: soleaux.postgresql.runtime.PostgreSqlSessionRuntime | None = None
        self._go_build_cache: tempfile.TemporaryDirectory[str] | None = None
        self._secret_values: tuple[str, ...] = ()
        self._process_group_id: int | None = None
        self._state = soleaux.lsp.contracts.SessionState.IDLE
        self._request_id = 0
        self._pending: dict[int, _PendingRequest] = {}
        self._registrations: dict[str, RegistrationEntry] = {}
        self._documents: dict[str, _DocumentState] = {}
        self._document_bytes: int = 0
        self._doc_lru: list[str] = []
        self._capabilities: soleaux.lsp.contracts.ServerCapabilities | None = None
        self._provider_identity: soleaux.lsp.contracts.ProviderProcessIdentity | None = None
        self._sync_kind: soleaux.lsp.contracts.TextDocumentSyncKind = (
            soleaux.lsp.contracts.TextDocumentSyncKind.NONE
        )
        self._open_close: bool = False
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._start_task: asyncio.Task[None] | None = None
        self._dispatch_tasks: set[asyncio.Task[None]] = set()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False
        self._init_event = asyncio.Event()
        self._late_response_tombstones: collections.OrderedDict[int, float] = (
            collections.OrderedDict()
        )
        self._late_response_count = 0
        self._notifications: dict[str, list[dict[str, typing.Any]]] = {}
        self._notification_events: dict[str, asyncio.Event] = {}
        self._diagnostics = soleaux.lsp.diagnostics.DiagnosticStateStore()
        self._server_requests: dict[
            str,
            collections.abc.Callable[
                [dict[str, typing.Any]], collections.abc.Awaitable[typing.Any]
            ],
        ] = {
            "client/registerCapability": self._handle_register,
            "client/unregisterCapability": self._handle_unregister,
            "workspace/configuration": self._handle_configuration,
            "workspace/diagnostic/refresh": self._handle_diagnostic_refresh,
            "workspace/workspaceFolders": self._handle_workspace_folders,
        }

    @property
    def state(self) -> soleaux.lsp.contracts.SessionState:
        """Current session state."""
        return self._state

    @property
    def capabilities(self) -> soleaux.lsp.contracts.ServerCapabilities | None:
        """Negotiated server capabilities after initialize."""
        return self._capabilities

    @property
    def provider_identity(self) -> soleaux.lsp.contracts.ProviderProcessIdentity | None:
        """Configured and initialize-reported identity for the live process."""
        return self._provider_identity

    @property
    def started(self) -> bool:
        """Whether a provider process exists."""
        return self._proc is not None

    @property
    def pid(self) -> int | None:
        """Provider PID when started."""
        return self._proc.pid if self._proc is not None else None

    @property
    def open_document_count(self) -> int:
        """Number of tracked open documents."""
        return len(self._documents)

    @property
    def open_document_uris(self) -> frozenset[str]:
        """Exact live document set after broker-owned LRU eviction."""
        return frozenset(self._documents)

    @property
    def document_versions(self) -> dict[str, int]:
        """Snapshot the exact client versions for currently open documents."""
        return {uri: document.version for uri, document in self._documents.items()}

    @property
    def pending_request_count(self) -> int:
        """Number of response slots currently owned by the broker."""
        return len(self._pending)

    @property
    def late_response_count(self) -> int:
        """Number of safely discarded responses to abandoned requests."""
        return self._late_response_count

    async def start(self) -> None:
        """Start once and make every concurrent caller await initialization."""
        async with self._start_lock:
            if self._state is soleaux.lsp.contracts.SessionState.INITIALIZED:
                return
            if self._start_task is None:
                if self._state is not soleaux.lsp.contracts.SessionState.IDLE:
                    raise LspBrokerError(f"broker cannot start from state {self._state}")
                self._start_task = asyncio.create_task(self._start())
            start_task = self._start_task
        await asyncio.shield(start_task)

    async def _start(self) -> None:
        """Spawn and initialize the provider for the shared start task."""
        self._state = soleaux.lsp.contracts.SessionState.INITIALIZING
        await self._spawn()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            initialize_result = soleaux.lsp.contracts.InitializeResult.from_lsp(
                await self._request("initialize", self._init_params(), timeout=15.0)
            )
            self._capabilities = initialize_result.capabilities
            process_id = self.pid
            if process_id is None:
                raise LspBrokerError("initialized provider process has no process identity")
            self._provider_identity = soleaux.lsp.contracts.ProviderProcessIdentity(
                configured_name=self._spec.provider_name,
                configured_version=self._spec.provider_version,
                server_info=initialize_result.server_info,
                process_id=process_id,
                process_epoch=self._process_epoch,
            )
            self._open_close = initialize_result.capabilities.open_close
            self._sync_kind = initialize_result.capabilities.text_document_sync
            await self._notify("initialized", {})
            await self._notify("workspace/didChangeConfiguration", {"settings": {}})
            self._state = soleaux.lsp.contracts.SessionState.INITIALIZED
            self._init_event.set()
        except Exception:
            self._state = soleaux.lsp.contracts.SessionState.DEAD
            await self._kill_proc()
            raise

    def _init_params(self) -> dict[str, typing.Any]:
        root_uri = self._spec.root_uri
        if root_uri is None and self._workspace_root is not None:
            root_uri = self._workspace_root
        return {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {
                    "workspaceEdit": {"documentChanges": True},
                    "didChangeConfiguration": {"dynamicRegistration": False},
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                    "symbol": {"dynamicRegistration": False},
                    "configuration": True,
                    "workspaceFolders": True,
                    "diagnostics": {"refreshSupport": True},
                    "registerCapability": True,
                    "unregisterCapability": True,
                },
                "window": {"workDoneProgress": False},
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                        "didSave": True,
                    },
                    "completion": {"dynamicRegistration": True},
                    "hover": {"dynamicRegistration": True},
                    "signatureHelp": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "definition": {"dynamicRegistration": True},
                    "implementation": {"dynamicRegistration": True},
                    "rename": {"dynamicRegistration": True, "prepareSupport": False},
                    "codeAction": {"dynamicRegistration": True},
                    "publishDiagnostics": {"versionSupport": True},
                    "diagnostic": {"dynamicRegistration": True},
                    "formatting": {"dynamicRegistration": True},
                    "rangeFormatting": {"dynamicRegistration": True},
                    "callHierarchy": {"dynamicRegistration": True},
                },
            },
            "initializationOptions": self._spec.initialization_options,
            "workspaceFolders": (
                [{"uri": root_uri, "name": root_uri.rstrip("/").rsplit("/", 1)[-1]}]
                if root_uri is not None
                else None
            ),
        }

    async def _spawn(self) -> None:
        argv = self._spec.argv
        provider_environment = self._spec.process_environment()
        self._secret_values = soleaux.postgresql.runtime.secret_values(provider_environment)
        try:
            environment = soleaux.postgresql.runtime.build_safe_environment(
                provider_environment,
                environment_names=self._spec.environment_names,
            )
            if (
                self._spec.provider_name == "gopls"
                and "GOCACHE" not in environment
                and "HOME" not in environment
            ):
                self._go_build_cache = tempfile.TemporaryDirectory(prefix="soleaux-gocache-")
                pathlib.Path(self._go_build_cache.name).chmod(0o700)
                environment["GOCACHE"] = self._go_build_cache.name
            if soleaux.postgresql.runtime.uses_postgresql_runtime(self._spec.environment_names):
                if self._spec.root_uri is not None:
                    workspace_root = soleaux.postgresql.runtime.workspace_path_from_uri(
                        self._spec.root_uri
                    )
                elif self._workspace_root is not None:
                    workspace_root = pathlib.Path(self._workspace_root).resolve(strict=True)
                else:
                    raise soleaux.postgresql.runtime.PostgreSqlRuntimeError(
                        "PostgreSQL provider requires a local workspace root"
                    )
                runtime = soleaux.postgresql.runtime.create_postgresql_session_runtime(
                    argv=argv,
                    workspace_root=workspace_root,
                    provider_environment=provider_environment,
                    logs_retention_days=self._spec.logs_retention_days,
                    temp_retention_hours=self._spec.temp_retention_hours,
                )
                self._postgresql_runtime = runtime
                argv = runtime.argv
                environment = runtime.environment
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=os.name == "posix",
                creationflags=WINDOWS_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except (OSError, soleaux.postgresql.runtime.PostgreSqlRuntimeError, ValueError) as exc:
            if self._postgresql_runtime is not None:
                self._postgresql_runtime.cleanup()
                self._postgresql_runtime = None
            if self._go_build_cache is not None:
                self._go_build_cache.cleanup()
                self._go_build_cache = None
            detail = soleaux.postgresql.runtime.redact_text(str(exc), self._secret_values)
            raise ProviderUnavailableError(
                f"provider {self._spec.provider_name!r} failed to start: {detail}"
            ) from None
        if os.name == "posix":
            self._process_group_id = self._proc.pid

    async def _read_loop(self) -> None:
        """Read and dispatch JSON-RPC messages from the provider stdout."""
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        try:
            while not self._closed:
                msg = await self._read_message(stream)
                if msg is None:
                    break
                dispatch_task = asyncio.create_task(self._dispatch(msg))
                self._dispatch_tasks.add(dispatch_task)
                dispatch_task.add_done_callback(self._dispatch_tasks.discard)
        except asyncio.IncompleteReadError, ConnectionResetError, OSError:
            pass
        # Wake up any pending requests with error
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(ProviderUnavailableError("provider stream closed"))

    async def _drain_stderr(self) -> None:
        """Drain stderr without blocking."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while not self._closed:
                chunk = await self._proc.stderr.read(4096)
                if not chunk:
                    break
        except OSError, ConnectionResetError:
            pass

    async def _read_message(self, stream: asyncio.StreamReader) -> dict[str, typing.Any] | None:
        """Parse one Content-Length framed JSON-RPC message."""
        headers: dict[str, str] = {}
        while True:
            line = await stream.readline()
            if not line:
                return None
            line_str = line.decode("ascii", errors="replace").strip()
            if not line_str:
                break
            if ":" in line_str:
                key, _, value = line_str.partition(":")
                headers[key.strip().lower()] = value.strip()
        length_str = headers.get("content-length")
        if not length_str:
            return None
        length = int(length_str)
        body = await stream.readexactly(length)
        return json.loads(body)

    async def _dispatch(self, msg: dict[str, typing.Any]) -> None:
        """Route one decoded message to its handler."""
        msg = _OBJECT_MAPPING_ADAPTER.validate_python(
            soleaux.postgresql.runtime.redact_value(msg, self._secret_values),
            strict=True,
        )
        if "id" in msg and "method" in msg:
            # Server-initiated request
            handler = self._server_requests.get(msg["method"])
            if handler is None:
                method_name = str(msg["method"])
                await self._respond_error(
                    msg["id"], METHOD_NOT_FOUND, f"unknown server request: {method_name}"
                )
                return
            try:
                raw_params = msg.get("params", {})
                params = (
                    _OBJECT_MAPPING_ADAPTER.validate_python(raw_params, strict=True)
                    if isinstance(raw_params, dict)
                    else {}
                )
                result = await handler(params)
                await self._send_response(msg["id"], result)
            except Exception as exc:
                await self._respond_error(msg["id"], INTERNAL_ERROR, str(exc))
        elif "id" in msg and "result" in msg:
            self._resolve_response(msg["id"], msg["result"])
        elif "id" in msg and "error" in msg:
            self._resolve_error(msg["id"], msg["error"])
        elif "method" in msg:
            method = str(msg["method"])
            raw_params = msg.get("params", {})
            params = (
                _OBJECT_MAPPING_ADAPTER.validate_python(raw_params, strict=True)
                if isinstance(raw_params, dict)
                else {}
            )
            self._record_notification(method, params)

    def _resolve_response(self, req_id: int | str, result: typing.Any) -> None:
        try:
            key = int(req_id)
        except TypeError, ValueError:
            return  # late response with unparseable id
        pending = self._pending.pop(key, None)
        if pending is None:
            self._consume_tombstone(key)
            return
        if not pending.future.done():
            pending.future.set_result(result)

    def _resolve_error(self, req_id: int | str, error: dict[str, typing.Any]) -> None:
        try:
            key = int(req_id)
        except TypeError, ValueError:
            return
        pending = self._pending.pop(key, None)
        if pending is None:
            self._consume_tombstone(key)
            return
        if not pending.future.done():
            if error.get("code") == REQUEST_CANCELLED:
                pending.future.set_exception(
                    OperationTimeoutError(error.get("message", "cancelled"))
                )
            else:
                err_code = error.get("code", "?")
                err_msg = error.get("message", "")
                pending.future.set_exception(LspBrokerError(f"LSP error {err_code}: {err_msg}"))

    async def _handle_register(self, params: dict[str, typing.Any]) -> None:
        """Apply client/registerCapability registrations."""
        registrations = _OBJECT_LIST_ADAPTER.validate_python(
            params.get("registrations", []),
            strict=True,
        )
        parsed: list[soleaux.lsp.contracts.Registration] = []
        for reg_raw in registrations:
            registration = _OBJECT_MAPPING_ADAPTER.validate_python(reg_raw, strict=True)
            register_options = _OBJECT_MAPPING_ADAPTER.validate_python(
                registration.get("registerOptions", {}),
                strict=True,
            )
            parsed.append(
                soleaux.lsp.contracts.Registration(
                    id=str(registration["id"]),
                    method=str(registration["method"]),
                    register_options=register_options,
                )
            )
        incoming_ids = {registration.id for registration in parsed}
        if len(incoming_ids) != len(parsed):
            raise LspBrokerError("duplicate registration id in one request")
        for reg in parsed:
            if reg.id in self._registrations:
                raise LspBrokerError(f"duplicate registration id: {reg.id}")
        for reg in parsed:
            self._registrations[reg.id] = RegistrationEntry(
                id=reg.id, method=reg.method, register_options=reg.register_options
            )

    async def _handle_configuration(self, params: dict[str, typing.Any]) -> list[None]:
        """Return default settings for each advertised workspace configuration item."""
        items: object = params.get("items", [])
        if not isinstance(items, list):
            raise LspBrokerError("workspace/configuration items must be an array")
        return [None] * len(_OBJECT_LIST_ADAPTER.validate_python(items, strict=True))

    async def _handle_diagnostic_refresh(self, _params: dict[str, typing.Any]) -> None:
        """Invalidate retained diagnostic results for a server-requested refresh."""
        self._diagnostics.invalidate()

    async def _handle_workspace_folders(
        self, _params: dict[str, typing.Any]
    ) -> list[dict[str, str]]:
        """Return the initialized root for servers that request workspace folders."""
        root_uri = self._spec.root_uri
        if root_uri is None:
            return []
        return [{"uri": root_uri, "name": root_uri.rstrip("/").rsplit("/", 1)[-1]}]

    async def _handle_unregister(self, params: dict[str, typing.Any]) -> None:
        """Apply client/unregisterCapability unregistrations."""
        unregs = params.get("unregisterations", [])
        parsed: list[soleaux.lsp.contracts.Unregistration] = []
        for unreg_raw in unregs:
            parsed.append(
                soleaux.lsp.contracts.Unregistration(
                    id=str(unreg_raw["id"]), method=str(unreg_raw["method"])
                )
            )
        for unreg in parsed:
            entry = self._registrations.get(unreg.id)
            if entry is None:
                raise LspBrokerError(f"unknown unregistration id: {unreg.id}")
            if entry.method != unreg.method:
                raise LspBrokerError(
                    f"unregistration method mismatch for {unreg.id}: "
                    f"expected {entry.method}, received {unreg.method}"
                )
        for unreg in parsed:
            self._registrations.pop(unreg.id)

    async def open_document(self, uri: str, language_id: str, content: str) -> None:
        """Open a document, respecting openClose and sync kind."""
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > self._budget.max_open_bytes:
            raise LspBrokerError("document exceeds the session byte budget")
        async with self._lock:
            if uri in self._documents:
                return
            evicted = self._evict_documents_locked(content_bytes)
            doc = _DocumentState(uri=uri, language_id=language_id, version=1, content=content)
            self._documents[uri] = doc
            self._document_bytes += content_bytes
            self._doc_lru.append(uri)
        if self._open_close:
            for closed in evicted:
                await self._notify("textDocument/didClose", {"textDocument": {"uri": closed.uri}})
            await self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": doc.version,
                        "text": content,
                    }
                },
            )

    async def update_document(self, uri: str, content: str) -> None:
        """Full-content document update (Full sync) or notification (Incremental)."""
        async with self._lock:
            doc = self._documents.get(uri)
            if doc is None:
                previous_content = None
                version = 0
            else:
                previous_content = doc.content
                old_bytes = len(previous_content.encode("utf-8"))
                doc.version += 1
                doc.content = content
                version = doc.version
                new_bytes = len(content.encode("utf-8"))
                self._document_bytes += new_bytes - old_bytes
                if self._document_bytes > self._budget.max_open_bytes:
                    doc.version -= 1
                    doc.content = previous_content
                    self._document_bytes -= new_bytes - old_bytes
                    raise LspBrokerError("document update exceeds the session byte budget")
                if uri in self._doc_lru:
                    self._doc_lru.remove(uri)
                self._doc_lru.append(uri)
        if previous_content is None:
            await self.open_document(uri, "", content)
            return
        if self._sync_kind is soleaux.lsp.contracts.TextDocumentSyncKind.NONE:
            return
        if self._sync_kind is soleaux.lsp.contracts.TextDocumentSyncKind.FULL:
            changes = [{"text": content}]
        else:
            changes = [self._full_replacement_change(previous_content, content)]
        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": changes,
            },
        )

    def _full_replacement_change(self, previous: str, content: str) -> dict[str, typing.Any]:
        lines = previous.split("\n")
        last_line = lines[-1]
        if last_line.endswith("\r"):
            last_line = last_line[:-1]
        encoding = self._capabilities.position_encoding if self._capabilities else "utf-16"
        if encoding == "utf-8":
            end_character = len(last_line.encode("utf-8"))
        elif encoding == "utf-32":
            end_character = len(last_line)
        else:
            end_character = len(last_line.encode("utf-16-le")) // 2
        return {
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": len(lines) - 1, "character": end_character},
            },
            "text": content,
        }

    def _remove_document_locked(self, uri: str) -> _DocumentState | None:
        doc = self._documents.pop(uri, None)
        if doc is None:
            return None
        self._document_bytes -= len(doc.content.encode("utf-8"))
        if uri in self._doc_lru:
            self._doc_lru.remove(uri)
        return doc

    def _evict_documents_locked(self, incoming_bytes: int) -> list[_DocumentState]:
        evicted: list[_DocumentState] = []
        while self._doc_lru and (
            len(self._documents) >= self._budget.max_open_documents
            or self._document_bytes + incoming_bytes > self._budget.max_open_bytes
        ):
            removed = self._remove_document_locked(self._doc_lru[0])
            if removed is not None:
                evicted.append(removed)
        return evicted

    async def close_document(self, uri: str) -> None:
        """Close a document and send didClose if openClose is active."""
        async with self._lock:
            doc = self._remove_document_locked(uri)
        if doc is not None and self._open_close:
            await self._notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    async def request(
        self,
        method: str,
        params: dict[str, typing.Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> typing.Any:
        """Send a JSON-RPC request and await its response."""
        if self._state is not soleaux.lsp.contracts.SessionState.INITIALIZED:
            raise LspBrokerError(f"broker not initialized (state={self._state})")
        return await self._request(method, params or {}, timeout=timeout)

    async def send_notification(
        self, method: str, params: dict[str, typing.Any] | None = None
    ) -> None:
        """Send a JSON-RPC notification."""
        await self._notify(method, params or {})

    async def cancel(self, request_id: int) -> None:
        """Send $/cancelRequest for a pending request."""
        await self._notify("$/cancelRequest", {"id": request_id})

    async def _request(
        self,
        method: str,
        params: dict[str, typing.Any],
        *,
        timeout: float,
    ) -> typing.Any:
        self._request_id += 1
        req_id = self._request_id
        future: asyncio.Future[typing.Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = _PendingRequest(future=future, method=method, params=params)
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        try:
            await self._write_message(msg)
            async with asyncio.timeout(timeout):
                return await asyncio.shield(future)
        except TimeoutError:
            await self._abandon_request(req_id)
            msg_timeout = f"LSP request {method} timed out after {timeout}s"
            raise OperationTimeoutError(msg_timeout) from None
        except asyncio.CancelledError:
            await self._abandon_request(req_id)
            raise
        except BaseException:
            pending = self._pending.pop(req_id, None)
            if pending is not None:
                pending.future.cancel()
            raise

    async def _abandon_request(self, request_id: int) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        pending.future.cancel()
        self._record_tombstone(request_id)
        await self.cancel(request_id)

    def _record_tombstone(self, request_id: int) -> None:
        now = time.monotonic()
        while self._late_response_tombstones:
            _, created_at = next(iter(self._late_response_tombstones.items()))
            if now - created_at <= LATE_RESPONSE_TTL_SECONDS:
                break
            self._late_response_tombstones.popitem(last=False)
        self._late_response_tombstones[request_id] = now
        while len(self._late_response_tombstones) > MAX_LATE_RESPONSE_TOMBSTONES:
            self._late_response_tombstones.popitem(last=False)

    def _consume_tombstone(self, request_id: int) -> bool:
        if self._late_response_tombstones.pop(request_id, None) is None:
            return False
        self._late_response_count += 1
        return True

    async def _notify(self, method: str, params: dict[str, typing.Any]) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write_message(msg)

    async def _send_response(self, req_id: int | str, result: typing.Any) -> None:
        msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
        await self._write_message(msg)

    async def _respond_error(self, req_id: int | str, code: int, message: str) -> None:
        safe_message = soleaux.postgresql.runtime.redact_text(message, self._secret_values)
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": safe_message},
        }
        await self._write_message(msg)

    async def _write_message(self, msg: dict[str, typing.Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ProviderUnavailableError("provider is not running")
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        await self._proc.stdin.drain()

    def get_registration(self, reg_id: str) -> RegistrationEntry | None:
        """Look up one dynamic registration by ID."""
        return self._registrations.get(reg_id)

    def registrations_by_method(self, method: str) -> list[RegistrationEntry]:
        """All active registrations for one method."""
        return [r for r in self._registrations.values() if r.method == method]

    def bind_diagnostic_generation(
        self,
        uri: str,
        *,
        document_version: int,
        generation_fingerprint: str,
    ) -> None:
        """Bind diagnostic state to this broker epoch and one document generation."""
        self._diagnostics.bind(
            uri,
            document_version=document_version,
            provider_epoch=self._process_epoch,
            generation_fingerprint=generation_fingerprint,
        )

    def diagnostic_previous_result_id(
        self,
        uri: str,
        *,
        document_version: int,
        generation_fingerprint: str,
    ) -> str | None:
        """Return a pull result ID only when it matches the current generation."""
        return self._diagnostics.previous_result_id(
            uri,
            document_version=document_version,
            provider_epoch=self._process_epoch,
            generation_fingerprint=generation_fingerprint,
        )

    def apply_diagnostic_pull_report(
        self,
        uri: str,
        report: object,
    ) -> soleaux.lsp.diagnostics.DiagnosticState:
        """Normalize one pull report through the broker's diagnostic state owner."""
        return self._diagnostics.apply_pull_report(uri, report)

    async def wait_for_diagnostics(
        self,
        uri: str,
        *,
        document_version: int,
        generation_fingerprint: str,
        timeout: float,
    ) -> soleaux.lsp.diagnostics.DiagnosticState | None:
        """Wait for push state compatible with one URI and semantic generation."""
        return await self._diagnostics.wait(
            uri,
            document_version=document_version,
            provider_epoch=self._process_epoch,
            generation_fingerprint=generation_fingerprint,
            timeout=timeout,
        )

    def notifications_by_method(self, method: str) -> tuple[dict[str, typing.Any], ...]:
        """Return bounded retained params for one server notification method."""
        return tuple(self._notifications.get(method, ()))

    async def wait_for_notification(
        self,
        method: str,
        *,
        timeout: float,
    ) -> tuple[dict[str, typing.Any], ...]:
        """Wait boundedly for the first retained notification of one method."""
        event = self._notification_events.setdefault(method, asyncio.Event())
        retained = self.notifications_by_method(method)
        if retained:
            return retained
        event.clear()
        try:
            async with asyncio.timeout(timeout):
                await event.wait()
        except TimeoutError:
            return ()
        return self.notifications_by_method(method)

    def _record_notification(self, method: str, params: dict[str, typing.Any]) -> None:
        if method == "textDocument/publishDiagnostics":
            self._diagnostics.publish(params)
            return
        retained = self._notifications.setdefault(method, [])
        retained.append(params)
        if len(retained) > MAX_RETAINED_NOTIFICATIONS_PER_METHOD:
            del retained[: len(retained) - MAX_RETAINED_NOTIFICATIONS_PER_METHOD]
        self._notification_events.setdefault(method, asyncio.Event()).set()

    async def shutdown(self) -> None:
        """Graceful shutdown: send shutdown, exit, terminate, then kill."""
        if self._closed:
            return
        was_initialized = self._state is soleaux.lsp.contracts.SessionState.INITIALIZED
        self._state = soleaux.lsp.contracts.SessionState.SHUTTING_DOWN
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
        if was_initialized and self._proc is not None and self._proc.returncode is None:
            try:
                await self._request("shutdown", {}, timeout=3.0)
                await self._notify("exit", {})
            except Exception:
                pass
        self._closed = True
        self._state = soleaux.lsp.contracts.SessionState.SHUTDOWN
        await self._kill_proc()
        background_tasks = [
            task
            for task in (self._reader_task, self._stderr_task, *self._dispatch_tasks)
            if task is not None
        ]
        for task in background_tasks:
            if not task.done():
                task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        self._dispatch_tasks.clear()
        self._registrations.clear()
        self._documents.clear()
        self._doc_lru.clear()
        self._document_bytes = 0
        self._provider_identity = None
        self._late_response_tombstones.clear()
        self._notifications.clear()
        self._diagnostics.clear()
        for event in self._notification_events.values():
            event.set()
        self._notification_events.clear()
        self._state = soleaux.lsp.contracts.SessionState.DEAD

    async def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        runtime = self._postgresql_runtime
        self._postgresql_runtime = None
        go_build_cache = self._go_build_cache
        self._go_build_cache = None
        process_group_id = self._process_group_id
        self._process_group_id = None
        if proc is None:
            if runtime is not None:
                runtime.cleanup()
            if go_build_cache is not None:
                go_build_cache.cleanup()
            return
        try:
            self._signal_process_tree(proc, process_group_id, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._budget.shutdown_grace_seconds)
            except TimeoutError:
                self._signal_process_tree(proc, process_group_id, signal.SIGKILL)
                await proc.wait()
            if process_group_id is not None:
                await self._reap_process_group(process_group_id)
        finally:
            if runtime is not None:
                runtime.cleanup()
            if go_build_cache is not None:
                go_build_cache.cleanup()

    @staticmethod
    def _signal_process_tree(
        proc: asyncio.subprocess.Process, process_group_id: int | None, signal_number: int
    ) -> None:
        if os.name == "posix" and process_group_id is not None:
            try:
                os.killpg(process_group_id, signal_number)
            except ProcessLookupError:
                return
            return
        if proc.returncode is None:
            try:
                proc.send_signal(signal_number)
            except ProcessLookupError:
                return

    async def _reap_process_group(self, process_group_id: int) -> None:
        deadline = time.monotonic() + self._budget.shutdown_grace_seconds
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group_id, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.01)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            return
