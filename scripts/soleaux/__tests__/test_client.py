from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

import httpx2
import pytest
from fastmcp import Client as FastMCPClient
from fastmcp import Context, FastMCP
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient
from mcp_types import Root, TextContent
from pydantic import FileUrl

from soleaux.contracts.context import (
    ContextGap,
    ContextSection,
    TaskContextItem,
    TaskContextPacket,
)
from soleaux.contracts.results import ResultStatus, TaskContextEnvelope

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_PATH = _REPOSITORY_ROOT / "scripts" / "soleaux" / "client.py"


def _load_client() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "soleaux_local_client",
        _CLIENT_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load the local Soleaux client")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


client = _load_client()


def _task_item(
    section: ContextSection,
    identity: str,
    *,
    summary: str = "Required fact.",
) -> TaskContextItem:
    return TaskContextItem(
        table=f"authority.{identity}",
        section=section,
        identity=identity,
        summary=summary,
        data={"identity": identity},
        evidence_id=f"evidence-{identity}",
        path=f"owners/{identity}.md",
        start_line=1,
        end_line=1,
        relation_distance=0,
    )


def _task_packet(
    *,
    canonical_owners: tuple[TaskContextItem, ...] = (),
    consumers: tuple[TaskContextItem, ...] = (),
    conflicts: tuple[TaskContextItem, ...] = (),
    validation_routes: tuple[TaskContextItem, ...] = (),
    gaps: tuple[ContextGap, ...] = (),
) -> TaskContextPacket:
    return TaskContextPacket(
        objective="Find the owner",
        retrieval_engine="sqlite-fts5",
        canonical_owners=canonical_owners,
        consumers=consumers,
        conflicts=conflicts,
        validation_routes=validation_routes,
        gaps=gaps,
        ranked_candidate_count=(
            len(canonical_owners) + len(consumers) + len(conflicts) + len(validation_routes)
        ),
        related_fact_count=0,
        returned_item_count=(
            len(canonical_owners) + len(consumers) + len(conflicts) + len(validation_routes)
        ),
        coverage_complete=not gaps,
    )


def _context_result(packet: TaskContextPacket, text: str) -> CallToolResult:
    envelope = TaskContextEnvelope(
        product_version="0.1.0",
        request_id="request-1",
        status=ResultStatus.OK,
        data=packet,
    )
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=envelope.model_dump(mode="json"),
        meta=None,
    )


def _complete_server_text(packet: TaskContextPacket) -> str:
    lines = ["# Soleaux task context", "", "## Section index"]
    for title, items in client._required_sections(packet):
        lines.append(f"- {title}: {len(items)}")
    for title, items in client._required_sections(packet):
        if items:
            lines.extend(("", f"## {title} ({len(items)})"))
    if packet.gaps:
        lines.extend(("", f"## Coverage gaps ({len(packet.gaps)})"))
        lines.extend(f"- `{gap.code}`" for gap in packet.gaps)
    return "\n".join(lines)


def test_deployment_config_owns_the_private_socket_endpoint() -> None:
    deployment = client.load_deployment_config()

    assert deployment.endpoint == "http://soleaux.local/mcp"
    assert deployment.service_label == "dev.soleaux.soleaux"
    assert deployment.socket_relative_path == "Library/Caches/Soleaux/soleaux.sock"
    assert deployment.socket_path == Path.home() / deployment.socket_relative_path
    assert len(str(deployment.socket_path)) <= 100
    assert deployment.workspace_root is None


def _write_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    config_path = tmp_path / "deployment.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("SOLEAUX_DEPLOYMENT_CONFIG", str(config_path))
    client.load_deployment_config.cache_clear()


def _valid_deployment_payload() -> dict[str, object]:
    return {
        "endpoint": "http://soleaux.local/mcp",
        "schema_version": "soleaux.local-deployment/v2",
        "service_label": "dev.soleaux.test-workspace",
        "socket_relative_path": "Library/Caches/Soleaux/test-workspace.sock",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            {"schema_version": "soleaux.local-deployment/v1"},
            "unsupported schema",
        ),
        (
            {"endpoint": "http://127.0.0.1:8765/mcp"},
            "soleaux.local",
        ),
        (
            {"endpoint": "http://user@soleaux.local/mcp"},
            "credential-free",
        ),
        (
            {"socket_relative_path": "/tmp/soleaux.sock"},
            "relative",
        ),
        (
            {"socket_relative_path": "Library/../escape.sock"},
            "relative",
        ),
    ),
)
def test_deployment_config_rejects_unsafe_socket_deployments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
    message: str,
) -> None:
    payload = _valid_deployment_payload() | mutation
    _write_deployment(tmp_path, monkeypatch, payload)

    with pytest.raises(client.DeploymentError) as excinfo:
        client.load_deployment_config()
    assert message in str(excinfo.value)


def test_context_uses_one_legacy_fastmcp_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object], float]] = []
    constructions: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, transport: object, **options: object) -> None:
            constructions.append({"transport": transport, **options})

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            _exception_type: type[BaseException] | None,
            _exception: BaseException | None,
            _traceback: object | None,
        ) -> None:
            return None

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object],
            *,
            timeout: float,
        ):
            calls.append((name, arguments, timeout))
            packet = _task_packet()
            return _context_result(packet, _complete_server_text(packet))

    monkeypatch.setattr(client, "Client", FakeClient)

    result = asyncio.run(client.request_context("Find the owner", "codex"))

    assert result == _complete_server_text(_task_packet())
    assert len(constructions) == 1
    assert constructions[0]["mode"] == "legacy"
    assert constructions[0]["name"] == "soleaux-codex-context"
    transport = constructions[0]["transport"]
    assert isinstance(transport, StreamableHttpTransport)
    assert str(transport.url) == client.load_deployment_config().endpoint
    assert transport.auth is None

    deployment = client.load_deployment_config()
    upstream_http = transport.httpx_client_factory(
        headers={
            "Authorization": "Bearer ambient-secret",
            "Cookie": "ambient=credential",
            "X-Request-Id": "request-id",
        },
        follow_redirects=True,
        trust_env=True,
    )

    async def close_client() -> None:
        await upstream_http.aclose()

    asyncio.run(close_client())

    assert isinstance(upstream_http._transport, httpx2.AsyncHTTPTransport)
    assert upstream_http.follow_redirects is False
    assert upstream_http._trust_env is False
    assert "authorization" not in upstream_http.headers
    assert "cookie" not in upstream_http.headers
    assert "x-request-id" not in upstream_http.headers
    closure = transport.httpx_client_factory.__closure__
    assert closure is not None
    assert any(cell.cell_contents == deployment.socket_path for cell in closure)
    assert calls == [
        (
            "context",
            {
                "request": {
                    "limit": 120,
                    "max_bytes": 65_535,
                    "objective": "Find the owner",
                }
            },
            60,
        )
    ]


def test_context_rebuilds_oversized_text_with_required_sections_and_explicit_gap() -> None:
    packet = _task_packet(
        canonical_owners=(_task_item(ContextSection.CANONICAL_OWNER, "canonical-owner"),),
        consumers=(_task_item(ContextSection.CONSUMER, "direct-consumer"),),
        conflicts=(_task_item(ContextSection.CONFLICT, "conflicting-claim"),),
        validation_routes=(_task_item(ContextSection.VALIDATION_ROUTE, "validation-route"),),
        gaps=(
            ContextGap(
                code="repository_gap",
                message="Repository evidence is incomplete.",
            ),
        ),
    )
    result = _context_result(packet, "é" * client._MAX_CONTEXT_BYTES)

    context = client._human_context(result)

    assert len(context.encode()) <= client._MAX_CONTEXT_PAYLOAD_BYTES
    assert "## Canonical owners (1)" in context
    assert '"identity":"canonical-owner"' in context
    assert "## Consumers (1)" in context
    assert '"identity":"direct-consumer"' in context
    assert "## Conflicts (1)" in context
    assert '"identity":"conflicting-claim"' in context
    assert "## Validation routes (1)" in context
    assert '"identity":"validation-route"' in context
    assert '"code":"repository_gap"' in context
    assert f'"code":"{client._HOST_CONTEXT_LIMIT_GAP}"' in context
    assert "é" not in context


def test_context_uses_a_minimal_explicit_gap_when_required_detail_exceeds_the_limit() -> None:
    items = tuple(
        _task_item(
            ContextSection.CANONICAL_OWNER,
            f"owner-{index}",
            summary="é" * 512,
        )
        for index in range(120)
    )
    packet = _task_packet(canonical_owners=items)

    context = client._human_context(_context_result(packet, "é" * client._MAX_CONTEXT_BYTES))

    assert len(context.encode()) <= client._MAX_CONTEXT_PAYLOAD_BYTES
    assert "## Canonical owners (120)" in context
    assert "Required identities preserved; summaries omitted at the host boundary." in context
    assert f'"code":"{client._HOST_CONTEXT_LIMIT_GAP}"' in context
    assert '"identity":"owner-0"' in context
    assert '"identity":"owner-119"' in context
    assert '"summary"' not in context


def test_context_fails_with_an_explicit_gap_if_required_sections_cannot_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "_MAX_CONTEXT_PAYLOAD_BYTES", 64)

    with pytest.raises(client.DeploymentError) as excinfo:
        client._human_context(_context_result(_task_packet(), "context"))
    assert "[host_context_limit]" in str(excinfo.value)


def test_context_command_writes_terminated_response_within_host_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_request_context(_prompt: str, _client: str) -> str:
        return "x" * client._MAX_CONTEXT_PAYLOAD_BYTES

    monkeypatch.setattr(client, "request_context", fake_request_context)
    monkeypatch.setattr(client.sys, "stdin", io.StringIO("Find the owner"))

    result = client.main(["context", "opencode"])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.endswith(client._OUTPUT_TERMINATOR)
    assert len(captured.out.encode()) <= client._MAX_CONTEXT_BYTES


@pytest.mark.parametrize("host", ("claude", "codex", "opencode"))
def test_bridge_uses_public_stateful_proxy_with_private_socket_transport(
    host: str,
) -> None:
    deployment = client.load_deployment_config()
    proxy = client._create_bridge_proxy(deployment, host)

    assert isinstance(proxy, FastMCPProxy)
    assert proxy.provider_error_strategy == "raise"
    factory_owner = proxy.client_factory.__self__
    assert isinstance(factory_owner, StatefulProxyClient)
    assert factory_owner.mode == "legacy"
    assert factory_owner.name == f"soleaux-{host}-bridge"

    transport = factory_owner.transport
    assert isinstance(transport, StreamableHttpTransport)
    assert str(transport.url) == deployment.endpoint
    assert transport.auth is None
    assert transport.httpx_client_factory is not None

    upstream_http = transport.httpx_client_factory(
        headers={
            "Authorization": "Bearer front-connection-secret",
            "Cookie": "front-session=credential",
        },
        follow_redirects=True,
        trust_env=True,
    )

    async def close_client() -> None:
        await upstream_http.aclose()

    asyncio.run(close_client())

    assert isinstance(upstream_http._transport, httpx2.AsyncHTTPTransport)
    assert upstream_http.follow_redirects is False
    assert upstream_http._trust_env is False
    assert "authorization" not in upstream_http.headers
    assert "cookie" not in upstream_http.headers


def test_stateful_proxy_isolates_callbacks_and_session_lifecycle() -> None:
    lifecycle = {"starts": 0, "stops": 0}

    @asynccontextmanager
    async def lifespan(_server: object) -> AsyncGenerator[None]:
        lifecycle["starts"] += 1
        try:
            yield
        finally:
            lifecycle["stops"] += 1

    upstream = FastMCP("stateful-upstream", lifespan=lifespan)

    @upstream.tool
    def identity(context: Context) -> str:
        return context.session_id

    @upstream.tool
    def client_capabilities(context: Context) -> dict[str, bool]:
        capabilities = context.session.client_params.capabilities
        return {
            "elicitation": capabilities.elicitation is not None,
            "roots": capabilities.roots is not None,
            "sampling": capabilities.sampling is not None,
        }

    owner = StatefulProxyClient(
        upstream,
        mode="legacy",
        roots=None,
        sampling_handler=None,
        elicitation_handler=None,
        log_handler=client._discard_upstream_log,
        progress_handler=client._discard_upstream_progress,
    )
    proxy = FastMCPProxy(
        client_factory=owner.new_stateful,
        name="stateful-proxy",
    )

    async def reject_forwarded_callback(
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise AssertionError("upstream callback reached the downstream host")

    async def exercise_connections() -> tuple[str, str]:
        session_ids: list[str] = []
        for expected_lifecycle in (
            {"starts": 1, "stops": 0},
            {"starts": 2, "stops": 1},
        ):
            async with FastMCPClient(
                proxy,
                mode="legacy",
                roots=[Root(uri=FileUrl("file:///repository"), name="repository")],
                sampling_handler=reject_forwarded_callback,
                elicitation_handler=reject_forwarded_callback,
            ) as downstream:
                first_identity = await downstream.call_tool("identity", {})
                second_identity = await downstream.call_tool("identity", {})
                assert first_identity.data == second_identity.data
                assert isinstance(first_identity.data, str)
                session_ids.append(first_identity.data)

                capabilities = await downstream.call_tool("client_capabilities", {})
                assert capabilities.data == {
                    "elicitation": False,
                    "roots": False,
                    "sampling": False,
                }
                assert lifecycle == expected_lifecycle

            assert lifecycle["starts"] == lifecycle["stops"]

        return session_ids[0], session_ids[1]

    first_session, second_session = asyncio.run(exercise_connections())

    assert first_session != second_session
    assert lifecycle == {"starts": 2, "stops": 2}


def test_bridge_runs_stdio_without_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[dict[str, object]] = []

    class FakeProxy:
        def run(self, **options: object) -> None:
            runs.append(options)

    monkeypatch.setattr(
        client,
        "_create_bridge_proxy",
        lambda _config, _client: FakeProxy(),
    )

    client.run_bridge("claude")

    assert runs == [{"show_banner": False, "transport": "stdio"}]


def test_stateless_upstream_serves_context_and_bridge_clients(
    short_socket_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib
    import socket as unix_socket
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "path", [str(_REPOSITORY_ROOT), *sys.path])
    from scripts.soleaux import http_service as composition

    socket_path = short_socket_dir / "s.sock"
    listener = composition._prebind_socket(socket_path)
    monkeypatch.setattr(
        composition,
        "load_deployment_config",
        lambda: SimpleNamespace(workspace_root=_REPOSITORY_ROOT),
    )
    server = composition.create_workspace_server()
    deployment = SimpleNamespace(
        endpoint="http://soleaux.local/mcp",
        socket_path=socket_path,
    )
    monkeypatch.setattr(client, "load_deployment_config", lambda: deployment)

    async def exercise() -> tuple[str, bool]:
        serve_task = asyncio.create_task(
            server.run_http_async(
                transport="http",
                path="/mcp",
                stateless_http=True,
                host_origin_protection=True,
                allowed_hosts=["soleaux.local"],
                sockets=[listener],
                show_banner=False,
            )
        )
        for _ in range(120):
            try:
                probe = unix_socket.socket(unix_socket.AF_UNIX, unix_socket.SOCK_STREAM)
                probe.connect(str(socket_path))
                probe.close()
                break
            except OSError:
                await asyncio.sleep(0.5)
        else:
            serve_task.cancel()
            raise AssertionError("the stateless test server did not start")
        try:
            rendered = await client.request_context(
                "Find the Soleaux context owner",
                "codex",
            )
            proxy = client._create_bridge_proxy(deployment, "codex")
            async with FastMCPClient(
                proxy,
                name="test-downstream",
                timeout=60,
                mode="legacy",
            ) as downstream:
                result = await downstream.call_tool("describe", {"request": {}})
            return rendered, result.is_error is False
        finally:
            serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serve_task

    try:
        rendered, bridge_ok = asyncio.run(exercise())
    finally:
        listener.close()

    assert bridge_ok is True
    assert rendered.startswith("# Soleaux task context")
    assert "## Section index" in rendered
    assert len(rendered.encode("utf-8")) <= client._MAX_CONTEXT_PAYLOAD_BYTES


def test_context_survives_high_cardinality_generation_gaps_within_host_envelope() -> None:
    gaps = tuple(
        ContextGap(
            code="coverage_omission",
            message=(
                (
                    f"path-{index}.tsx: structural projection 'syntax.references' "
                    "is unsupported for Tsx"
                )
                + " detail" * 128
            )[:1024],
        )
        for index in range(64)
    )
    packet = _task_packet(
        canonical_owners=(_task_item(ContextSection.CANONICAL_OWNER, "canonical-owner"),),
        consumers=(_task_item(ContextSection.CONSUMER, "direct-consumer"),),
        conflicts=(_task_item(ContextSection.CONFLICT, "conflicting-claim"),),
        validation_routes=(_task_item(ContextSection.VALIDATION_ROUTE, "validation-route"),),
        gaps=gaps,
    )
    result = _context_result(packet, "é" * client._MAX_CONTEXT_BYTES)

    context = client._human_context(result)

    assert len(context.encode()) <= client._MAX_CONTEXT_PAYLOAD_BYTES
    assert "## Canonical owners (1)" in context
    assert "## Consumers (1)" in context
    assert "## Conflicts (1)" in context
    assert "## Validation routes (1)" in context
    assert "## Coverage gaps (65)" in context
    assert context.count('"code":"coverage_omission"') == 1
    assert f'"code":"{client._HOST_CONTEXT_LIMIT_GAP}"' in context


def test_context_admits_item_detail_within_budget_with_explicit_omissions() -> None:
    items = tuple(
        _task_item(
            ContextSection.CANONICAL_OWNER,
            f"owner-{index}-{'x' * 80}",
            summary="é" * 1024,
        )
        for index in range(600)
    )
    packet = _task_packet(canonical_owners=items)
    result = _context_result(packet, "é" * client._MAX_CONTEXT_BYTES)

    context = client._human_context(result)

    assert len(context.encode()) <= client._MAX_CONTEXT_PAYLOAD_BYTES
    assert "## Canonical owners (600)" in context
    assert "## Consumers (0)" in context
    assert "## Conflicts (0)" in context
    assert "## Validation routes (0)" in context
    assert '"identity":"owner-0-' in context
    assert "canonical owners item(s) omitted at the host boundary" in context
    assert f'"code":"{client._HOST_CONTEXT_LIMIT_GAP}"' in context
