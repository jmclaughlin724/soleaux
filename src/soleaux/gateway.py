"""Config-driven MCP proxy with FastMCP-owned connection lifecycles (D034).

Each enabled ``[mcp.<name>]`` entry becomes one namespaced ``ProxyProvider``.
``on_demand`` creates a fresh client per provider operation, ``session`` delegates
front-connection ownership to ``StatefulProxyClient``, and ``shared`` reuses one
client only for an explicitly stateless HTTP backend. Tool catalogs use the
configured bounded TTL so protocol-mandated ``tools/list`` calls do not repeatedly
start command-backed providers.

D035 extends D034 to OAuth-protected backends: tokens persist in a user-private
py-key-value-aio store (0600 disk files by default, keyring opt-in), login is
CLI-mediated via ``soleaux mcp login <name>`` rather than daemon-launched
browsers, and auth failures direct the user to that login command.
"""

from __future__ import annotations

import collections.abc
import os
import pathlib
import ssl
import typing

import fastmcp
import fastmcp.client.auth.oauth
import fastmcp.client.logging
import fastmcp.client.transports
import fastmcp.server.providers.proxy
import httpx2

import soleaux.contracts.config
import soleaux.credentials
import soleaux.postgresql.runtime

type McpTransport = (
    fastmcp.client.transports.StdioTransport | fastmcp.client.transports.StreamableHttpTransport
)
type _TransportFactory = collections.abc.Callable[[], McpTransport]


async def _discard_backend_log(_message: fastmcp.client.logging.LogMessage) -> None:
    return None


async def _discard_backend_progress(
    _progress: float,
    _total: float | None,
    _message: str | None,
) -> None:
    return None


def _client_options(backend: soleaux.contracts.config.McpBackendConfig) -> dict[str, typing.Any]:
    return {
        "roots": None,
        "sampling_handler": None,
        "elicitation_handler": None,
        "log_handler": _discard_backend_log,
        "progress_handler": _discard_backend_progress,
        "timeout": backend.request_timeout_seconds,
        "init_timeout": backend.init_timeout_seconds,
    }


class _FreshTransportStatefulProxyClient(
    fastmcp.server.providers.proxy.StatefulProxyClient[McpTransport]
):
    """Let FastMCP cache by front connection without sharing a stdio process."""

    def __init__(
        self,
        transport_factory: _TransportFactory,
        client_options: dict[str, typing.Any],
    ) -> None:
        self._soleaux_transport_factory = transport_factory
        self._soleaux_client_options = dict(client_options)
        super().__init__(
            transport_factory(),
            mode="legacy",
            **self._soleaux_client_options,
        )

    def new(self) -> _FreshTransportStatefulProxyClient:
        return _FreshTransportStatefulProxyClient(
            self._soleaux_transport_factory,
            self._soleaux_client_options,
        )


def _required_environment_value(name: str, *, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"MCP {purpose} environment variable is missing or empty: {name}")
    return value


def _resolved_cwd(root: pathlib.Path, configured: str | None) -> str:
    resolved_root = root.resolve(strict=True)
    candidate = (
        resolved_root if configured is None else (resolved_root / configured).resolve(strict=True)
    )
    if not candidate.is_dir() or not candidate.is_relative_to(resolved_root):
        raise ValueError(f"MCP cwd escapes the workspace or is not a directory: {configured!r}")
    return str(candidate)


def _isolated_http_client_factory(
    verify: bool | ssl.SSLContext,
) -> collections.abc.Callable[..., httpx2.AsyncClient]:
    """Build HTTP clients without relaying the front connection's credentials."""

    def create_client(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        *,
        follow_redirects: bool = True,
        **kwargs: typing.Any,
    ) -> httpx2.AsyncClient:
        _ = follow_redirects
        isolated_headers = {
            name: value
            for name, value in (headers or {}).items()
            if name.casefold() != "authorization"
        }
        kwargs.pop("trust_env", None)
        kwargs.pop("verify", None)
        return httpx2.AsyncClient(
            headers=isolated_headers,
            timeout=timeout or httpx2.Timeout(30.0, read=300.0),
            auth=auth,
            follow_redirects=False,
            verify=verify,
            trust_env=False,
            **kwargs,
        )

    return create_client


class McpBackendAuthRequiredError(RuntimeError):
    """A backend needs interactive login; the message names the exact command (D035)."""


class _PinnedTokenStorage:
    """TokenStorage delegate that pins token_endpoint_auth_method (D035).

    Some authorization servers (Supabase) issue a client secret in their DCR
    response but omit token_endpoint_auth_method; the SDK then sends no
    credentials at token exchange and the AS rejects it. Pinning the declared
    method at the storage boundary corrects the record at write and at read.
    """

    def __init__(self, inner: typing.Any, auth_method: str) -> None:
        self._inner = inner
        self._auth_method = auth_method

    async def get_tokens(self) -> typing.Any:
        return await self._inner.get_tokens()

    async def set_tokens(self, tokens: typing.Any) -> None:
        await self._inner.set_tokens(tokens)

    async def get_client_info(self) -> typing.Any:
        info = await self._inner.get_client_info()
        if info is not None and info.token_endpoint_auth_method != self._auth_method:
            return info.model_copy(update={"token_endpoint_auth_method": self._auth_method})
        return info

    async def set_client_info(self, client_info: typing.Any) -> None:
        await self._inner.set_client_info(
            client_info.model_copy(update={"token_endpoint_auth_method": self._auth_method})
        )


class _NonInteractiveOAuth(fastmcp.client.auth.oauth.OAuth):
    """Daemon-side OAuth: never launch a browser; fail with the login command."""

    def __init__(self, *args: typing.Any, backend_name: str, **kwargs: typing.Any) -> None:
        self._soleaux_backend_name = backend_name
        super().__init__(*args, **kwargs)

    async def redirect_handler(self, authorization_url: str) -> None:
        raise McpBackendAuthRequiredError(
            f"MCP backend {self._soleaux_backend_name!r} is not authenticated; "
            f"run `soleaux mcp login {self._soleaux_backend_name}`"
        )


def _oauth_auth(
    backend: soleaux.contracts.config.McpBackendConfig,
    *,
    backend_name: str,
    url: str,
    verify: bool | ssl.SSLContext,
    interactive: bool = False,
) -> fastmcp.client.auth.oauth.OAuth:
    client_id = (
        _required_environment_value(backend.client_id_env, purpose="OAuth client id")
        if backend.client_id_env is not None
        else None
    )
    client_secret = (
        _required_environment_value(backend.client_secret_env, purpose="OAuth client secret")
        if backend.client_secret_env is not None
        else None
    )
    options: dict[str, typing.Any] = {
        "mcp_url": url,
        "scopes": list(backend.oauth_scopes) or None,
        "client_name": backend.oauth_client_name,
        "token_storage": soleaux.credentials.build_token_store(backend, backend_name=backend_name),
        "client_metadata_url": backend.oauth_client_metadata_url,
        "client_id": client_id,
        "client_secret": client_secret,
        "httpx_client_factory": _isolated_http_client_factory(verify),
    }
    if interactive:
        oauth = fastmcp.client.auth.oauth.OAuth(**options)
    else:
        oauth = _NonInteractiveOAuth(**options, backend_name=backend_name)
    if backend.oauth_token_endpoint_auth_method is not None:
        oauth.context.storage = _PinnedTokenStorage(
            oauth.context.storage, backend.oauth_token_endpoint_auth_method
        )
    return oauth


def _transport_factory(
    backend: soleaux.contracts.config.McpBackendConfig,
    root: pathlib.Path,
    *,
    backend_name: str,
    interactive_oauth: bool = False,
) -> _TransportFactory:
    if backend.command is not None:
        program, *arguments = backend.command
        environment = soleaux.postgresql.runtime.build_safe_environment(
            backend.env,
            environment_names=tuple(backend.env),
        )
        cwd = _resolved_cwd(root, backend.cwd)

        def stdio_transport() -> McpTransport:
            return fastmcp.client.transports.StdioTransport(
                command=program,
                args=list(arguments),
                env=environment,
                cwd=cwd,
                keep_alive=False,
            )

        return stdio_transport

    url = backend.url
    if url is None:
        raise ValueError("MCP backend has neither command nor url")

    def http_transport() -> McpTransport:
        # Secrets resolve lazily at client creation so a missing variable
        # degrades one backend instead of failing server construction (D034).
        headers: dict[str, str] = {}
        for header, env_name in backend.headers_from_env.items():
            value = _required_environment_value(env_name, purpose=f"header {header!r}")
            if "\r" in value or "\n" in value:
                raise ValueError(f"MCP header environment variable contains a newline: {env_name}")
            headers[header] = value

        verify: bool | ssl.SSLContext = backend.tls_verify
        if backend.tls_ca_file_env is not None:
            ca_value = _required_environment_value(backend.tls_ca_file_env, purpose="TLS CA file")
            ca_path = pathlib.Path(ca_value)
            if not ca_path.is_absolute():
                raise ValueError("MCP TLS CA file must be an absolute path")
            resolved_ca = ca_path.resolve(strict=True)
            if not resolved_ca.is_file():
                raise ValueError("MCP TLS CA file must resolve to a regular file")
            verify = ssl.create_default_context(cafile=str(resolved_ca))

        auth: str | fastmcp.client.auth.oauth.OAuth | None = None
        if backend.auth == "oauth":
            auth = _oauth_auth(
                backend,
                backend_name=backend_name,
                url=url,
                verify=verify,
                interactive=interactive_oauth,
            )
        elif backend.auth_token_env is not None:
            auth = _required_environment_value(backend.auth_token_env, purpose="auth token")
            if any(character.isspace() for character in auth):
                raise ValueError("MCP bearer token must not contain whitespace")

        return fastmcp.client.transports.StreamableHttpTransport(
            url,
            headers=headers or None,
            auth=auth,
            httpx_client_factory=_isolated_http_client_factory(verify),
        )

    return http_transport


def _client_factory(
    backend: soleaux.contracts.config.McpBackendConfig,
    root: pathlib.Path,
    *,
    backend_name: str,
) -> collections.abc.Callable[[], fastmcp.Client[typing.Any]]:
    transport_factory = _transport_factory(backend, root, backend_name=backend_name)
    if backend.url is not None:
        if backend.lifecycle == "shared":
            shared_client: fastmcp.Client[typing.Any] | None = None

            def shared_http_client() -> fastmcp.Client[typing.Any]:
                # Construct on first use so a missing secret degrades this one
                # backend instead of failing server construction (D034).
                nonlocal shared_client
                if shared_client is None:
                    shared_client = fastmcp.server.providers.proxy.ProxyClient(
                        transport_factory(),
                        mode="auto",
                        **_client_options(backend),
                    )
                return shared_client

            return shared_http_client

        def create_http_client() -> fastmcp.Client[typing.Any]:
            return fastmcp.server.providers.proxy.ProxyClient(
                transport_factory(),
                mode="auto",
                **_client_options(backend),
            )

        return create_http_client

    if backend.lifecycle == "session":
        owner = _FreshTransportStatefulProxyClient(
            transport_factory,
            _client_options(backend),
        )
        return owner.new_stateful

    def create_client() -> fastmcp.Client[typing.Any]:
        return fastmcp.server.providers.proxy.ProxyClient(
            transport_factory(),
            mode="auto",
            **_client_options(backend),
        )

    return create_client


def attach_mcp_proxies[LifespanT](
    server: fastmcp.FastMCP[LifespanT],
    config: soleaux.contracts.config.ResolvedConfig,
    root: pathlib.Path,
) -> int:
    """Attach one namespaced proxy provider per enabled backend.

    Backend subprocesses run from ``root`` (the workspace root); a backend's
    ``cwd`` resolves relative to it.
    """
    attached = 0
    for name, backend in sorted(config.mcp.items()):
        if not backend.enabled:
            continue
        provider = fastmcp.server.providers.proxy.ProxyProvider(  # pyright: ignore[reportUnknownMemberType]
            _client_factory(backend, root, backend_name=name),
            cache_ttl=backend.cache_ttl_seconds,
        )
        server.add_provider(provider, namespace=name)
        attached += 1
    return attached
