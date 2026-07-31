"""Deterministic stdio LSP server used by broker lifecycle tests."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

from pydantic import TypeAdapter, ValidationError

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])


def _json_object(value: object) -> dict[str, Any]:
    return _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)


def _maybe_json_object(value: object) -> dict[str, Any] | None:
    try:
        return _json_object(value)
    except ValidationError:
        return None


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        decoded = line.decode("ascii").strip()
        if not decoded:
            break
        key, separator, value = decoded.partition(":")
        if separator:
            headers[key.lower()] = value.strip()
    length = int(headers["content-length"])
    payload: object = json.loads(sys.stdin.buffer.read(length))
    return _json_object(payload)


def _write_message(message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def _params(message: dict[str, Any]) -> dict[str, Any]:
    value: object = message.get("params", {})
    try:
        return _json_object(value)
    except ValueError:
        return {}


def _reply(request_id: int | str, result: Any) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _reply_error(request_id: int | str, code: int, message: str) -> None:
    _write_message(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _server_request(request_id: str, method: str, params: dict[str, Any]) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})


def _document_uri(params: dict[str, Any]) -> str:
    raw_text_document: object = params.get("textDocument")
    text_document = _maybe_json_object(raw_text_document)
    if text_document is not None:
        uri = text_document.get("uri")
        if isinstance(uri, str):
            return uri
    raw_item: object = params.get("item")
    item = _maybe_json_object(raw_item)
    if item is not None:
        uri = item.get("uri")
        if isinstance(uri, str):
            return uri
    return "file:///fixture.py"


def _range(line: int) -> dict[str, dict[str, int]]:
    return {
        "start": {"line": line, "character": 0},
        "end": {"line": line, "character": 6},
    }


def _position_line(params: dict[str, Any]) -> int:
    raw_position: object = params.get("position")
    position = _maybe_json_object(raw_position)
    if position is None:
        return 0
    line = position.get("line")
    return line if isinstance(line, int) and not isinstance(line, bool) else 0


def _workspace_symbol(
    *,
    uri: str,
    line: int,
    kind: int,
) -> dict[str, Any]:
    return {
        "name": "target",
        "kind": kind,
        "location": {
            "uri": uri,
            "range": _range(line),
        },
    }


def _workspace_symbols(
    state: dict[str, Any],
    flags: frozenset[str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    if params.get("query") != "target":
        return []
    open_uri = state["open_uri"]
    base_uri, _, filename = open_uri.rpartition("/")
    suffix = f".{filename.rpartition('.')[2]}" if "." in filename else ""
    main_uri = f"{base_uri}/main{suffix}"
    other_uri = f"{base_uri}/other{suffix}"
    if "ambiguous-symbols" in flags:
        return [
            _workspace_symbol(uri=other_uri, line=0, kind=12),
            _workspace_symbol(uri=main_uri, line=0, kind=12),
        ]
    if "mixed-symbol-kinds" in flags:
        return [
            _workspace_symbol(uri=open_uri, line=0, kind=12),
            _workspace_symbol(uri=open_uri, line=1, kind=5),
        ]
    if "many-symbols" in flags:
        return [_workspace_symbol(uri=open_uri, line=line, kind=12) for line in range(25)]
    return [_workspace_symbol(uri=open_uri, line=0, kind=12)]


def _call_item(name: str, uri: str, line: int) -> dict[str, Any]:
    return {
        "name": name,
        "kind": 12,
        "uri": uri,
        "range": _range(line),
        "selectionRange": _range(line),
    }


def _diagnostic(source: str) -> dict[str, Any]:
    return {
        "range": _range(0),
        "severity": 1,
        "source": source,
        "message": f"{source} diagnostic",
    }


def _diagnostic_registration() -> dict[str, Any]:
    return {
        "id": "diagnostic-watch",
        "method": "textDocument/diagnostic",
        "registerOptions": {
            "documentSelector": None,
            "identifier": "fake-diagnostics",
            "interFileDependencies": False,
            "workspaceDiagnostics": False,
        },
    }


def main() -> int:
    initialize_delay = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    flags = frozenset(sys.argv[2:])
    diagnostics_enabled = "no-diagnostics" not in flags
    delayed_dynamic_diagnostics = "delayed-dynamic-diagnostics" in flags
    child = None
    if "spawn-child" in flags:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    state: dict[str, Any] = {
        "initialize_count": 0,
        "notifications": [],
        "cancelled": [],
        "child_pid": child.pid if child is not None else None,
        "requests": [],
        "open_uri": "file:///fixture.py",
        "server_responses": [],
        "late_request_id": None,
        "diagnostic_pull_count": 0,
    }
    while message := _read_message():
        method = message.get("method")
        request_id = message.get("id")
        params = _params(message)
        if method == "initialize" and request_id is not None:
            time.sleep(initialize_delay)
            state["initialize_count"] += 1
            state["initialize_params"] = params
            capabilities: dict[str, Any] = {
                "textDocumentSync": {"openClose": True, "change": 2},
                "positionEncoding": "utf-16",
                "definitionProvider": True,
                "referencesProvider": True,
                "implementationProvider": True,
                "hoverProvider": True,
                "completionProvider": {},
                "signatureHelpProvider": {},
                "codeActionProvider": True,
                "documentFormattingProvider": True,
                "documentRangeFormattingProvider": True,
                "renameProvider": True,
                "callHierarchyProvider": True,
                "workspaceSymbolProvider": True,
            }
            if diagnostics_enabled and not delayed_dynamic_diagnostics:
                capabilities["diagnosticProvider"] = {
                    "identifier": "fake-diagnostics",
                    "interFileDependencies": False,
                    "workspaceDiagnostics": False,
                }
            _reply(
                request_id,
                {
                    "capabilities": capabilities,
                    "serverInfo": {"name": "soleaux-fake-lsp", "version": "1.0.0"},
                },
            )
        elif method is None and request_id is not None:
            state["server_responses"].append(message)
        elif method in {"initialized", "workspace/didChangeConfiguration"}:
            state["notifications"].append({"method": method, "params": params})
            if method == "initialized":
                registrations: list[dict[str, Any]] = [
                    {
                        "id": "definition-watch",
                        "method": "textDocument/definition",
                        "registerOptions": {"documentSelector": None},
                    }
                ]
                if diagnostics_enabled and not delayed_dynamic_diagnostics:
                    registrations.append(_diagnostic_registration())
                _server_request(
                    "register-1",
                    "client/registerCapability",
                    {"registrations": registrations},
                )
        elif method == "textDocument/didOpen":
            state["notifications"].append({"method": method, "params": params})
            uri = _document_uri(params)
            state["open_uri"] = uri
            if delayed_dynamic_diagnostics:
                time.sleep(0.05)
                _server_request(
                    "register-diagnostics-delayed",
                    "client/registerCapability",
                    {"registrations": [_diagnostic_registration()]},
                )
            elif diagnostics_enabled:
                raw_text_document: object = params.get("textDocument")
                text_document = _maybe_json_object(raw_text_document)
                version: int | None = None
                if text_document is not None:
                    raw_version = text_document.get("version")
                    if isinstance(raw_version, int) and not isinstance(raw_version, bool):
                        version = raw_version
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/publishDiagnostics",
                        "params": {
                            "uri": uri,
                            "version": version,
                            "diagnostics": [_diagnostic("push")],
                        },
                    }
                )
        elif method == "test/invalid-registration" and request_id is not None:
            case = params.get("case")
            server_request_id = f"invalid-{case}"
            server_method: str
            server_params: dict[str, Any]
            if case == "duplicate-existing":
                server_method = "client/registerCapability"
                server_params = {
                    "registrations": [
                        {
                            "id": "definition-watch",
                            "method": "textDocument/references",
                            "registerOptions": {},
                        }
                    ]
                }
            elif case == "duplicate-batch":
                server_method = "client/registerCapability"
                server_params = {
                    "registrations": [
                        {
                            "id": "batch-duplicate",
                            "method": "textDocument/definition",
                            "registerOptions": {},
                        },
                        {
                            "id": "batch-duplicate",
                            "method": "textDocument/references",
                            "registerOptions": {},
                        },
                    ]
                }
            elif case == "malformed-options":
                server_method = "client/registerCapability"
                server_params = {
                    "registrations": [
                        {
                            "id": "malformed-options",
                            "method": "textDocument/references",
                            "registerOptions": "not-an-object",
                        }
                    ]
                }
            elif case == "unknown-unregistration":
                server_method = "client/unregisterCapability"
                server_params = {
                    "unregisterations": [
                        {
                            "id": "missing-registration",
                            "method": "textDocument/definition",
                        }
                    ]
                }
            else:
                _reply(request_id, {"unsupported": case})
                continue
            _server_request(server_request_id, server_method, server_params)
            _reply(request_id, None)
        elif method == "test/unregister-wrong" and request_id is not None:
            _server_request(
                "unregister-wrong",
                "client/unregisterCapability",
                {
                    "unregisterations": [
                        {"id": "definition-watch", "method": "textDocument/references"}
                    ]
                },
            )
            _reply(request_id, None)
        elif method == "test/unregister" and request_id is not None:
            _server_request(
                "unregister-1",
                "client/unregisterCapability",
                {
                    "unregisterations": [
                        {"id": "definition-watch", "method": "textDocument/definition"}
                    ]
                },
            )
            _reply(request_id, None)
        elif method == "test/diagnostic-refresh" and request_id is not None:
            _server_request(
                "diagnostic-refresh",
                "workspace/diagnostic/refresh",
                {},
            )
            _reply(request_id, None)
        elif (
            method in {"textDocument/definition", "textDocument/implementation"}
            and request_id is not None
        ):
            state["requests"].append(method)
            _reply(
                request_id,
                {"uri": _document_uri(params), "range": _range(0)},
            )
        elif method == "textDocument/references" and request_id is not None:
            state["requests"].append(method)
            line = _position_line(params) if "echo-reference-position" in flags else 3
            references = (
                []
                if "empty-references" in flags
                else [
                    {"uri": _document_uri(params), "range": _range(reference_line)}
                    for reference_line in range(60)
                ]
                if "many-references" in flags
                else [{"uri": _document_uri(params), "range": _range(line)}]
            )
            _reply(
                request_id,
                references,
            )
        elif method == "textDocument/hover" and request_id is not None:
            state["requests"].append(method)
            _reply(request_id, {"contents": {"kind": "plaintext", "value": "fake hover"}})
        elif method == "textDocument/prepareCallHierarchy" and request_id is not None:
            state["requests"].append(method)
            _reply(request_id, [_call_item("target", _document_uri(params), 0)])
        elif method == "callHierarchy/incomingCalls" and request_id is not None:
            state["requests"].append(method)
            uri = _document_uri(params)
            _reply(
                request_id,
                [{"from": _call_item("caller", uri, 3), "fromRanges": [_range(3)]}],
            )
        elif method == "callHierarchy/outgoingCalls" and request_id is not None:
            state["requests"].append(method)
            uri = _document_uri(params)
            _reply(
                request_id,
                [{"to": _call_item("callee", uri, 0), "fromRanges": [_range(3)]}],
            )
        elif method == "workspace/symbol" and request_id is not None:
            state["requests"].append(method)
            if "slow-symbols" in flags:
                time.sleep(0.05)
            _reply(request_id, _workspace_symbols(state, flags, params))
        elif method == "textDocument/completion" and request_id is not None:
            state["requests"].append(method)
            _reply(
                request_id,
                {
                    "isIncomplete": False,
                    "items": [{"label": "target", "kind": 3}],
                },
            )
        elif method == "textDocument/diagnostic" and request_id is not None:
            state["requests"].append(method)
            state["diagnostic_pull_count"] += 1
            if "pull-error" in flags:
                _reply_error(request_id, -32603, "fake diagnostic pull failure")
            elif state["diagnostic_pull_count"] == 1:
                _reply(
                    request_id,
                    {
                        "kind": "full",
                        "resultId": "diagnostic-result-1",
                        "items": [_diagnostic("pull")],
                    },
                )
            elif params.get("previousResultId") == "diagnostic-result-1":
                _reply(
                    request_id,
                    {
                        "kind": "unchanged",
                        "resultId": "diagnostic-result-2",
                    },
                )
            else:
                _reply(
                    request_id,
                    {
                        "kind": "full",
                        "resultId": "fresh-diagnostic-result",
                        "items": [_diagnostic("fresh-pull")],
                    },
                )
        elif method == "textDocument/signatureHelp" and request_id is not None:
            state["requests"].append(method)
            _reply(
                request_id,
                {
                    "signatures": [{"label": "target()", "parameters": []}],
                    "activeSignature": 0,
                    "activeParameter": 0,
                },
            )
        elif method == "textDocument/codeAction" and request_id is not None:
            state["requests"].append(method)
            _reply(request_id, [])
        elif (
            method in {"textDocument/formatting", "textDocument/rangeFormatting"}
            and request_id is not None
        ):
            state["requests"].append(method)
            _reply(
                request_id,
                [{"range": _range(0), "newText": "def target():\n"}],
            )
        elif method == "textDocument/rename" and request_id is not None:
            state["requests"].append(method)
            _reply(
                request_id,
                {
                    "changes": {
                        _document_uri(params): [
                            {
                                "range": _range(0),
                                "newText": params.get("newName", "renamed"),
                            }
                        ]
                    }
                },
            )
        elif method == "test/state" and request_id is not None:
            _reply(request_id, state)
        elif method == "test/late" and request_id is not None:
            state["late_request_id"] = request_id
        elif method == "test/sleep":
            continue
        elif method == "$/cancelRequest":
            cancelled_id = params.get("id")
            state["cancelled"].append(cancelled_id)
            if state["late_request_id"] == cancelled_id and isinstance(cancelled_id, (int, str)):
                _reply(cancelled_id, {"late": True})
                state["late_request_id"] = None
        elif method == "shutdown" and request_id is not None:
            _reply(request_id, None)
        elif method == "exit":
            return 0
        elif request_id is not None:
            _reply(request_id, {"method": method, "params": params})
        elif method is not None:
            state["notifications"].append({"method": method, "params": params})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
