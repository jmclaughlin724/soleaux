"""Gateway transport, provider, cache, and session lifecycle contracts."""

from __future__ import annotations

import ast
import asyncio
import inspect
import io
import json
import logging
import os
import ssl
import sys
import textwrap
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Protocol, runtime_checkable

import anyio
import httpx2
import pytest
from _assertions import object_mapping, raises_with_message
from fastmcp import Client, FastMCP
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.providers import LocalProvider
from fastmcp.server.providers.proxy import (
    ProxyClient,
    ProxyProvider,
    StatefulProxyClient,
)
from fastmcp.utilities.inspect import inspect_fastmcp
from mcp_types import Root
from mcp_types import Tool as McpTool
from pydantic import FileUrl, ValidationError

from soleaux import cli as cli_module
from soleaux import gateway as gateway_module
from soleaux.contracts.config import (
    McpBackendConfig,
    ResolvedConfig,
    load_config,
)
from soleaux.gateway import attach_mcp_proxies
from soleaux.postgresql.runtime import build_safe_environment


class _CatalogClient(Client[StdioTransport]):
    def __init__(self, tools: Sequence[McpTool]) -> None:
        self.tools = list(tools)
        self.list_calls = 0

    async def __aenter__(self) -> _CatalogClient:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def list_tools(self, max_pages: int = 250) -> list[McpTool]:
        _ = max_pages
        self.list_calls += 1
        return list(self.tools)


@runtime_checkable
class _StatefulOwner(Protocol):
    mode: str
    transport: object

    def new(self) -> _StatefulOwner: ...


class _FailingCatalogClient(_CatalogClient):
    def __init__(self, failure: BaseException) -> None:
        super().__init__([])
        self.failure = failure

    async def list_tools(self, max_pages: int = 250) -> list[McpTool]:
        _ = max_pages
        await asyncio.sleep(0)
        raise self.failure


class _BlockingCatalogClient(_CatalogClient):
    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked_once = False

    async def list_tools(self, max_pages: int = 250) -> list[McpTool]:
        _ = max_pages
        if self._blocked_once:
            return []
        self._blocked_once = True
        self.started.set()
        await self.release.wait()
        return []


def _mcp_tool(name: str) -> McpTool:
    return McpTool(
        name=name,
        input_schema={"type": "object", "properties": {}},
    )


def _session_owner(
    factory: Callable[[], Client[Any]],
) -> _StatefulOwner:
    owner: object = getattr(factory, "__self__", None)
    assert isinstance(owner, _StatefulOwner)
    stateful_owner = owner
    assert isinstance(owner, StatefulProxyClient)
    return stateful_owner


def _attach(
    config: ResolvedConfig,
    root: Path,
) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(name="t")
    assert attach_mcp_proxies(server, config, root) == 1
    return server


def _factory(
    backend: McpBackendConfig,
    root: Path,
) -> Callable[[], Client[Any]]:
    return gateway_module._client_factory(backend, root)


def _catalog_factory(client: _CatalogClient) -> Callable[[], Client[StdioTransport]]:
    """Present a structural catalog double as the client factory ProxyProvider expects."""
    return lambda: client


def _server_with_mcp_client(client: _CatalogClient) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(name="failure-test")

    def local_ready() -> str:
        return "ready"

    server.tool(name="local_ready")(local_ready)
    server.add_provider(ProxyProvider(_catalog_factory(client), cache_ttl=0), namespace="bad")
    return server


def _provider_failure(kind: Literal["slow", "malformed", "disconnected", "auth"]) -> BaseException:
    if kind == "slow":
        return TimeoutError("slow backend timed out")
    if kind == "malformed":
        return ValueError("malformed backend catalog")
    if kind == "disconnected":
        return anyio.EndOfStream()
    request = httpx2.Request("GET", "https://backend.invalid/mcp")
    response = httpx2.Response(401, request=request)
    return httpx2.HTTPStatusError(
        "backend authentication failed",
        request=request,
        response=response,
    )


def test_zero_config_default_declares_no_gateway() -> None:
    assert ResolvedConfig.default().mcp == {}


def test_mcp_backend_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        McpBackendConfig()
    with pytest.raises(ValidationError):
        McpBackendConfig(command=["echo"], url="http://localhost/mcp")
    with pytest.raises(ValidationError):
        McpBackendConfig(command=["echo"], unknown="x")  # type: ignore[call-arg]


def test_load_config_parses_mcp_section(tmp_path: Path) -> None:
    (tmp_path / "soleaux.toml").write_text(
        textwrap.dedent(
            """
            [mcp.fake]
            command = ["echo", "hi"]

            [mcp.stateful]
            command = ["echo", "ho"]
            lifecycle = "session"
            enabled = false
            """
        ),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.mcp["fake"].lifecycle == "on_demand"
    assert config.mcp["fake"].enabled is True
    assert config.mcp["stateful"].lifecycle == "session"
    assert config.mcp["stateful"].enabled is False


def test_attach_mcp_proxies_without_backends_keeps_local_provider_only(tmp_path: Path) -> None:
    server: FastMCP[None] = FastMCP(name="t")
    assert attach_mcp_proxies(server, ResolvedConfig.default(), tmp_path) == 0
    assert len(server.providers) == 1
    assert isinstance(server.providers[0], LocalProvider)


def _fake_backend(tmp_path: Path) -> tuple[list[str], dict[str, str], Path]:
    marker = tmp_path / "starts.log"
    script = tmp_path / "fake_backend.py"
    script.write_text(
        textwrap.dedent(
            """
            import os

            from fastmcp import Context, FastMCP

            with open(os.environ["SOLEAUX_TEST_MARKER"], "a", encoding="utf-8") as fh:
                fh.write("start\\n")

            mcp = FastMCP(name="fake")


            @mcp.tool(description="Echo backend text.")
            def echo(text: str) -> str:
                return text


            @mcp.tool
            def identity(context: Context) -> dict[str, int | str]:
                return {"pid": os.getpid(), "session_id": context.session_id}


            @mcp.tool
            def client_capabilities(context: Context) -> dict[str, bool]:
                capabilities = context.session.client_params.capabilities
                return {
                    "roots": capabilities.roots is not None,
                    "sampling": capabilities.sampling is not None,
                    "elicitation": capabilities.elicitation is not None,
                }


            @mcp.resource(
                "data://fixed",
                name="backend_fixed",
                description="Fixed backend resource.",
                mime_type="text/plain",
            )
            def fixed_resource() -> str:
                return "fixed"


            @mcp.resource(
                "data://items/{item_id}",
                name="backend_item",
                description="Parameterized backend resource.",
                mime_type="application/json",
            )
            def item_resource(item_id: str) -> str:
                return '{"item_id": "' + item_id + '"}'


            @mcp.prompt(name="summarize", description="Summarize a backend topic.")
            def summarize(topic: str) -> str:
                return "Summarize " + topic


            mcp.run()
            """
        ),
        encoding="utf-8",
    )
    command = [sys.executable, str(script)]
    env = {"SOLEAUX_TEST_MARKER": str(marker)}
    return command, env, marker


def _starts(marker: Path) -> int:
    if not marker.exists():
        return 0
    return len(marker.read_text(encoding="utf-8").splitlines())


def _constructor_parameters(class_: type[object]) -> Mapping[str, inspect.Parameter]:
    constructor: object = vars(class_).get("__init__")
    assert callable(constructor)
    return inspect.signature(constructor).parameters


async def _identity(client: Client[Any]) -> tuple[int, str]:
    result = await client.call_tool("fake_identity", {})
    data = object_mapping(result.data)
    pid = data.get("pid")
    session_id = data.get("session_id")
    assert isinstance(pid, int)
    assert isinstance(session_id, str)
    return pid, session_id


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_pid_exit(pid: int) -> None:
    for _ in range(100):
        if not _pid_exists(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"MCP backend process {pid} survived teardown")


def test_client_capabilities_use_explicit_discard_handlers() -> None:
    backend = McpBackendConfig(command=["unused"])
    options = gateway_module._client_options(backend)

    assert options["roots"] is None
    assert options["sampling_handler"] is None
    assert options["elicitation_handler"] is None
    assert options["log_handler"] is gateway_module._discard_backend_log
    assert options["progress_handler"] is gateway_module._discard_backend_progress
    assert options["timeout"] == 300
    assert options["init_timeout"] == 30


async def test_discard_handlers_do_not_emit_backend_payloads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = McpBackendConfig(command=["unused"])
    options = gateway_module._client_options(backend)
    with caplog.at_level(logging.DEBUG):
        await options["log_handler"](
            LogMessage(
                level="error",
                logger="backend",
                data={"secret": "backend-log-payload"},
            )
        )
        await options["progress_handler"](1, 2, "backend-progress-payload")

    assert "backend-log-payload" not in caplog.text
    assert "backend-progress-payload" not in caplog.text


@pytest.mark.parametrize("lifecycle", ["on_demand", "session"])
async def test_stdio_transport_fields_are_exact_for_each_lifecycle(
    tmp_path: Path,
    lifecycle: Literal["on_demand", "session"],
) -> None:
    backend_cwd = tmp_path / "backend"
    backend_cwd.mkdir()
    backend = McpBackendConfig(
        command=["unused-program", "--flag", "value"],
        env={"OWNED": "yes"},
        cwd="backend",
        lifecycle=lifecycle,
        request_timeout_seconds=17,
        init_timeout_seconds=9,
    )
    factory = _factory(backend, tmp_path)
    options = gateway_module._client_options(backend)
    assert options["timeout"] == 17
    assert options["init_timeout"] == 9

    if lifecycle == "session":
        owner = _session_owner(factory)
        assert owner.mode == "legacy"
        first = owner.new()
        second = owner.new()
        assert len({id(owner.transport), id(first.transport), id(second.transport)}) == 3
        clients = (owner, first, second)
    else:
        first = factory()
        second = factory()
        assert isinstance(first, ProxyClient)
        assert isinstance(second, ProxyClient)
        assert first.mode == "auto"
        assert second.mode == "auto"
        assert first.transport is not second.transport
        clients = (first, second)

    for client in clients:
        transport = client.transport
        assert isinstance(transport, StdioTransport)
        assert transport.command == "unused-program"
        assert transport.args == ["--flag", "value"]
        assert transport.env == build_safe_environment(
            {"OWNED": "yes"},
            environment_names=("OWNED",),
        )
        assert transport.cwd == str(backend_cwd.resolve())
        assert transport.keep_alive is False
        await transport.close()


async def test_http_transport_resolves_external_policy_and_owns_fresh_transports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test CA", encoding="utf-8")
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def default_context(*, cafile: str) -> ssl.SSLContext:
        assert cafile == str(ca_file.resolve())
        return tls_context

    monkeypatch.setattr(gateway_module.ssl, "create_default_context", default_context)
    monkeypatch.setenv("SOLEAUX_TEST_TOKEN", "secret-token")
    monkeypatch.setenv("SOLEAUX_TEST_HEADER", "tenant-a")
    monkeypatch.setenv("SOLEAUX_TEST_CA", str(ca_file))
    backend = McpBackendConfig(
        url="https://example.invalid/mcp",
        auth_token_env="SOLEAUX_TEST_TOKEN",
        headers_from_env={"X-Tenant": "SOLEAUX_TEST_HEADER"},
        tls_ca_file_env="SOLEAUX_TEST_CA",
        request_timeout_seconds=23,
        init_timeout_seconds=11,
    )
    factory = _factory(backend, tmp_path)
    options = gateway_module._client_options(backend)
    assert options["timeout"] == 23
    assert options["init_timeout"] == 11

    first = factory()
    second = factory()
    assert isinstance(first, ProxyClient)
    assert isinstance(second, ProxyClient)
    assert first.mode == "auto"
    assert second.mode == "auto"
    assert first.transport is not second.transport

    for client in (first, second):
        transport = client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == "https://example.invalid/mcp"
        assert transport.headers == {"X-Tenant": "tenant-a"}
        assert isinstance(transport.auth, BearerAuth)
        assert transport.auth.token.get_secret_value() == "secret-token"
        assert transport.verify is None
        assert transport.httpx_client_factory is not None
        http_client = transport.httpx_client_factory(
            headers={
                "Authorization": "Bearer front-client-token",
                "X-Tenant": "tenant-a",
            },
            auth=transport.auth,
        )
        assert "authorization" not in http_client.headers
        assert http_client.headers["X-Tenant"] == "tenant-a"
        await http_client.aclose()
        await transport.close()


def test_http_client_factory_ignores_environment_and_caller_tls_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://fallback-proxy.invalid:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/untrusted/environment/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/untrusted/environment/certs")
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    captured: dict[str, object] = {}
    created_client = object()

    def recording_async_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return created_client

    monkeypatch.setattr(gateway_module.httpx2, "AsyncClient", recording_async_client)
    factory = gateway_module._isolated_http_client_factory(tls_context)

    result = factory(
        headers={"X-Tenant": "tenant-a"},
        trust_env=True,
        verify=False,
    )

    assert result is created_client
    assert captured["trust_env"] is False
    assert captured["verify"] is tls_context
    assert captured["headers"] == {"X-Tenant": "tenant-a"}
    assert captured["follow_redirects"] is False


async def test_http_client_factory_rejects_cross_origin_redirects_with_secret_headers() -> None:
    requests: list[httpx2.Request] = []

    async def redirect(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            307,
            headers={"Location": "https://untrusted.example/capture"},
            request=request,
        )

    client = gateway_module._isolated_http_client_factory(True)(
        headers={"X-Api-Key": "configured-secret"},
        follow_redirects=True,
        transport=httpx2.MockTransport(redirect),
    )
    try:
        response = await client.post(
            "https://trusted.example/mcp",
            content=b"sensitive-tool-input",
        )
    finally:
        await client.aclose()

    assert response.status_code == 307
    assert len(requests) == 1
    assert requests[0].url.host == "trusted.example"


async def test_shared_http_factory_reuses_one_proxy_client(tmp_path: Path) -> None:
    backend = McpBackendConfig(
        url="https://example.invalid/mcp",
        lifecycle="shared",
        stateless=True,
    )
    factory = _factory(backend, tmp_path)

    first = factory()
    second = factory()

    assert first is second
    assert isinstance(first, ProxyClient)
    assert first.mode == "auto"
    shared_transport = first.transport
    assert isinstance(shared_transport, StreamableHttpTransport)
    await shared_transport.close()


async def test_cli_probe_uses_the_gateway_transport_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "soleaux.toml").write_text(
        ('schema_version = "soleaux.config/v1"\n[mcp.fixture]\ncommand = ["unused-program"]\n'),
        encoding="utf-8",
    )
    backend_server = FastMCP("probe")
    seen: list[tuple[tuple[str, ...] | None, Path]] = []

    def transport_factory(
        backend: McpBackendConfig,
        root: Path,
    ) -> Callable[[], FastMCP]:
        seen.append(
            (
                tuple(backend.command) if backend.command is not None else None,
                root,
            )
        )
        return lambda: backend_server

    monkeypatch.setattr(gateway_module, "_transport_factory", transport_factory)
    output = io.StringIO()
    result = await cli_module._probe_mcp_backends(
        tmp_path,
        json_output=True,
        stdout=output,
    )

    assert result == 0
    assert seen == [(("unused-program",), tmp_path)]
    assert json.loads(output.getvalue()) == [
        {
            "name": "fixture",
            "alive": True,
            "tool_count": 0,
            "tool_names": [],
            "elapsed_ms": pytest.approx(0, abs=100),
        }
    ]


def test_gateway_does_not_import_fastmcp_internal_context_or_transport_options() -> None:
    gateway_path = Path(gateway_module.__file__)
    tree = ast.parse(gateway_path.read_text(encoding="utf-8"), filename=str(gateway_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "fastmcp.client.transports.base" not in imported_modules
    assert "fastmcp.server.dependencies" not in imported_modules


def test_every_one_shot_stdio_transport_explicitly_disables_persistence() -> None:
    package_root = Path(__file__).parents[1]
    violations: list[str] = []
    for root_name in ("scripts", "src", "tests"):
        for path in sorted((package_root / root_name).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            trees = [(tree, str(path))]
            for constant in ast.walk(tree):
                if not isinstance(constant, ast.Constant) or not isinstance(constant.value, str):
                    continue
                try:
                    embedded = ast.parse(
                        textwrap.dedent(constant.value),
                        filename=f"{path}:{constant.lineno}",
                    )
                except SyntaxError:
                    continue
                trees.append((embedded, f"{path}:{constant.lineno}"))
            for candidate, owner in trees:
                for node in ast.walk(candidate):
                    if not isinstance(node, ast.Call):
                        continue
                    call_name = (
                        node.func.id
                        if isinstance(node.func, ast.Name)
                        else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
                    )
                    if call_name != "StdioTransport":
                        continue
                    keep_alive = next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "keep_alive"),
                        None,
                    )
                    if not (isinstance(keep_alive, ast.Constant) and keep_alive.value is False):
                        violations.append(f"{owner}:{node.lineno}")

    assert violations == []


def test_fastmcp_beta_gateway_adapter_matches_exact_public_contract() -> None:
    assert version("fastmcp") == "4.0.0b1"
    assert version("fastmcp-slim") == "4.0.0b1"
    assert version("mcp") == "2.0.0"
    assert version("mcp-types") == "2.0.0"

    proxy_client_parameters = _constructor_parameters(ProxyClient)
    proxy_provider_parameters = _constructor_parameters(ProxyProvider)
    stateful_parameters = _constructor_parameters(StatefulProxyClient)
    transport_parameters = inspect.signature(StreamableHttpTransport.__init__).parameters
    stdio_parameters = inspect.signature(StdioTransport.__init__).parameters

    assert tuple(proxy_client_parameters) == ("self", "transport", "kwargs")
    assert tuple(proxy_provider_parameters) == ("self", "client_factory", "cache_ttl")
    assert tuple(stateful_parameters) == ("self", "args", "kwargs")
    assert tuple(transport_parameters) == (
        "self",
        "url",
        "headers",
        "auth",
        "httpx_client_factory",
        "verify",
    )
    assert "sse_read_timeout" not in transport_parameters
    assert tuple(stdio_parameters) == (
        "self",
        "command",
        "args",
        "env",
        "cwd",
        "keep_alive",
        "log_file",
    )
    assert stdio_parameters["keep_alive"].default is None


def test_configured_cache_ttl_reaches_gateway_tool_catalog_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_ttls: list[float | None] = []

    def recording_proxy_provider(
        client_factory: Callable[[], Client[Any]],
        cache_ttl: float | None = None,
    ) -> ProxyProvider:
        cache_ttls.append(cache_ttl)
        return ProxyProvider(client_factory, cache_ttl=cache_ttl)

    monkeypatch.setattr(
        "fastmcp.server.providers.proxy.ProxyProvider",
        recording_proxy_provider,
    )
    backend = McpBackendConfig(command=["unused"], cache_ttl_seconds=19)
    config = ResolvedConfig(mcp={"fake": backend})
    _server = _attach(config, tmp_path)

    assert cache_ttls == [19]


async def test_gateway_reuses_tool_catalog_for_protocol_mandated_lists() -> None:
    catalog_client = _CatalogClient([_mcp_tool("remote")])
    provider = ProxyProvider(_catalog_factory(catalog_client), cache_ttl=60)
    server: FastMCP[None] = FastMCP(name="catalog-cache-test")

    @server.tool
    def local_ready() -> str:
        return "ready"

    assert local_ready() == "ready"
    server.add_provider(provider, namespace="remote")

    async with Client(server) as downstream:
        first = await downstream.call_tool("local_ready", {})
        second = await downstream.call_tool("local_ready", {})

    assert first.data == "ready"
    assert second.data == "ready"
    assert catalog_client.list_calls == 1


async def test_proxy_cache_refreshes_explicitly_and_at_zero_ttl() -> None:
    explicit_client = _CatalogClient([_mcp_tool("first")])
    explicit_provider = ProxyProvider(_catalog_factory(explicit_client), cache_ttl=60)

    first = await explicit_provider.list_tools()
    explicit_client.tools = [_mcp_tool("second")]
    second = await explicit_provider.list_tools()

    assert [tool.name for tool in first] == ["first"]
    assert [tool.name for tool in second] == ["second"]
    assert explicit_client.list_calls == 2

    zero_client = _CatalogClient([_mcp_tool("dynamic")])
    zero_provider = ProxyProvider(_catalog_factory(zero_client), cache_ttl=0)
    assert (await zero_provider.get_tool("dynamic")) is not None
    assert (await zero_provider.get_tool("dynamic")) is not None
    assert zero_client.list_calls == 2


async def test_namespaced_backend_catalog_preserves_identities_and_metadata(tmp_path: Path) -> None:
    command, env, _marker = _fake_backend(tmp_path)
    config = ResolvedConfig(mcp={"fake": McpBackendConfig(command=command, env=env)})
    server = _attach(config, tmp_path)

    async with Client(server) as downstream:
        tools, resources, templates, prompts = await asyncio.gather(
            downstream.list_tools(),
            downstream.list_resources(),
            downstream.list_resource_templates(),
            downstream.list_prompts(),
        )

    tool_names = [tool.name for tool in tools]
    resource_uris = [str(resource.uri) for resource in resources]
    template_uris = [template.uri_template for template in templates]
    prompt_names = [prompt.name for prompt in prompts]
    assert set(tool_names) == {"fake_client_capabilities", "fake_echo", "fake_identity"}
    assert resource_uris == ["data://fake/fixed"]
    assert template_uris == ["data://fake/items/{item_id}"]
    assert prompt_names == ["fake_summarize"]
    for identities in (tool_names, resource_uris, template_uris, prompt_names):
        assert len(identities) == len(set(identities))

    echo = next(tool for tool in tools if tool.name == "fake_echo")
    assert echo.description == "Echo backend text."
    assert resources[0].name == "backend_fixed"
    assert resources[0].description == "Fixed backend resource."
    assert resources[0].mime_type == "text/plain"
    assert templates[0].name == "backend_item"
    assert templates[0].description == "Parameterized backend resource."
    assert templates[0].mime_type == "application/json"
    assert prompts[0].description == "Summarize a backend topic."


async def test_on_demand_backend_catalog_is_available_to_fastmcp_inspect(tmp_path: Path) -> None:
    command, env, _marker = _fake_backend(tmp_path)
    config = ResolvedConfig(mcp={"fake": McpBackendConfig(command=command, env=env)})
    server = _attach(config, tmp_path)

    info = await inspect_fastmcp(server)
    assert {tool.name for tool in info.tools} == {
        "fake_client_capabilities",
        "fake_echo",
        "fake_identity",
    }
    assert {resource.uri for resource in info.resources} == {"data://fake/fixed"}
    assert {template.uri_template for template in info.templates} == {"data://fake/items/{item_id}"}
    assert {prompt.name for prompt in info.prompts} == {"fake_summarize"}


async def test_proxy_does_not_advertise_front_client_capabilities(tmp_path: Path) -> None:
    command, env, _marker = _fake_backend(tmp_path)
    server = _attach(
        ResolvedConfig(mcp={"fake": McpBackendConfig(command=command, env=env)}),
        tmp_path,
    )

    async def reject_forwarded_callback(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("backend callback reached the front client")

    async with Client(
        server,
        roots=[Root(uri=FileUrl("file:///workspace"), name="workspace")],
        sampling_handler=reject_forwarded_callback,
        elicitation_handler=reject_forwarded_callback,
    ) as downstream:
        result = await downstream.call_tool("fake_client_capabilities", {})

    assert result.data == {
        "roots": False,
        "sampling": False,
        "elicitation": False,
    }


@pytest.mark.parametrize("kind", ["slow", "malformed", "disconnected", "auth"])
async def test_provider_failures_leave_local_catalog_and_calls_available(
    kind: Literal["slow", "malformed", "disconnected", "auth"],
) -> None:
    server = _server_with_mcp_client(_FailingCatalogClient(_provider_failure(kind)))

    async with Client(server) as downstream:
        tool_names = {tool.name for tool in await downstream.list_tools()}
        assert tool_names == {"local_ready"}
        result = await downstream.call_tool("local_ready", {})
        assert result.data == "ready"


async def test_cancelled_provider_listing_propagates_and_later_local_call_succeeds() -> None:
    backend = _BlockingCatalogClient()
    server = _server_with_mcp_client(backend)

    async with Client(server) as downstream:
        listing = asyncio.create_task(downstream.list_tools())
        await asyncio.wait_for(backend.started.wait(), timeout=1)
        listing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listing
        backend.release.set()

        result = await downstream.call_tool("local_ready", {})
        assert result.data == "ready"


async def test_on_demand_backend_is_lazy_and_exits_per_operation(tmp_path: Path) -> None:
    command, env, marker = _fake_backend(tmp_path)
    config = ResolvedConfig(mcp={"fake": McpBackendConfig(command=command, env=env)})
    server = _attach(config, tmp_path)

    async with Client(server) as downstream:
        assert _starts(marker) == 0
        tool_names = {tool.name for tool in await downstream.list_tools()}
        assert "fake_echo" in tool_names
        assert _starts(marker) == 1
        first = await downstream.call_tool("fake_echo", {"text": "a"})
        assert first.content
        assert _starts(marker) == 2
        second = await downstream.call_tool("fake_echo", {"text": "b"})
        assert second.content
        assert _starts(marker) == 3


async def test_session_backend_reuses_one_process_and_session(tmp_path: Path) -> None:
    command, env, marker = _fake_backend(tmp_path)
    config = ResolvedConfig(
        mcp={"fake": McpBackendConfig(command=command, env=env, lifecycle="session")}
    )
    server = _attach(config, tmp_path)
    downstream = Client(server, mode="legacy")

    async with downstream:
        first = await _identity(downstream)
        second = await _identity(downstream)
        assert first == second
        assert _starts(marker) == 1
    await _wait_for_pid_exit(first[0])


async def test_session_lifecycle_rejects_a_modern_front_connection(tmp_path: Path) -> None:
    command, env, _marker = _fake_backend(tmp_path)
    server = _attach(
        ResolvedConfig(
            mcp={
                "fake": McpBackendConfig(
                    command=command,
                    env=env,
                    lifecycle="session",
                )
            }
        ),
        tmp_path,
    )

    async with Client(server) as downstream:
        with raises_with_message(ToolError, "handshake protocol era"):
            await _identity(downstream)


async def test_concurrent_downstream_sessions_isolate_processes_and_session_ids(
    tmp_path: Path,
) -> None:
    command, env, marker = _fake_backend(tmp_path)
    config = ResolvedConfig(
        mcp={"fake": McpBackendConfig(command=command, env=env, lifecycle="session")}
    )
    server = _attach(config, tmp_path)
    app = server.http_app(path="/mcp", transport="streamable-http")
    connected = asyncio.Barrier(2)

    def httpx_client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kwargs: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    async def inspect_session() -> tuple[tuple[int, str], str]:
        transport = StreamableHttpTransport(
            "http://soleaux.test/mcp",
            httpx_client_factory=httpx_client_factory,
        )
        async with Client(transport, mode="legacy") as downstream:
            await connected.wait()
            front_session_id = transport.get_session_id()
            assert front_session_id is not None
            first = await _identity(downstream)
            assert await _identity(downstream) == first
            assert _pid_exists(first[0])
            return first, front_session_id

    async with app.router.lifespan_context(app):
        (first, first_front), (second, second_front) = await asyncio.gather(
            inspect_session(),
            inspect_session(),
        )
    assert first_front != second_front
    assert first[0] != second[0]
    assert first[1] != second[1]
    assert _starts(marker) == 2

    await asyncio.gather(_wait_for_pid_exit(first[0]), _wait_for_pid_exit(second[0]))


async def test_unavailable_mcp_fails_open_while_local_tool_remains(tmp_path: Path) -> None:
    server: FastMCP[None] = FastMCP(name="t")

    @server.tool
    def local_ready() -> str:
        return "ready"

    assert local_ready() == "ready"
    config = ResolvedConfig(
        mcp={"missing": McpBackendConfig(command=[str(tmp_path / "does-not-exist")])}
    )
    assert attach_mcp_proxies(server, config, tmp_path) == 1

    async with Client(server) as downstream:
        tool_names = {tool.name for tool in await downstream.list_tools()}
        assert "local_ready" in tool_names
        assert not any(name.startswith("missing_") for name in tool_names)
        result = await downstream.call_tool("local_ready", {})
        assert result.data == "ready"
