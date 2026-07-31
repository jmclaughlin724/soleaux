"""Soleaux private-socket bridge client and stdio host bridge."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx2
from fastmcp import Client
from fastmcp.client.logging import LogMessage
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.providers.proxy import FastMCPProxy, StatefulProxyClient

from soleaux.bridge.deployment import (
    DeploymentConfig,
    DeploymentError,
    load_deployment_config,
)
from soleaux.bridge.rendering import (
    _MAX_CONTEXT_PAYLOAD_BYTES,
    _OUTPUT_TERMINATOR,
    _bounded_objective,
    _human_context,
)


async def request_context(prompt: str, client: str) -> str:
    config = load_deployment_config()
    transport = StreamableHttpTransport(
        config.endpoint,
        httpx_client_factory=_uds_http_client_factory(config.socket_path),
    )
    async with Client(
        transport,
        name=f"soleaux-{client}-context",
        timeout=60,
        mode="legacy",
    ) as soleaux:
        result = await soleaux.call_tool(
            "context",
            {
                "request": {
                    "limit": 120,
                    "max_bytes": _MAX_CONTEXT_PAYLOAD_BYTES,
                    "objective": _bounded_objective(prompt),
                }
            },
            timeout=60,
        )
    return _human_context(result)


async def _discard_upstream_log(_message: LogMessage) -> None:
    return None


async def _discard_upstream_progress(
    _progress: float,
    _total: float | None,
    _message: str | None,
) -> None:
    return None


def _uds_http_client_factory(
    socket_path: Path,
) -> Callable[..., httpx2.AsyncClient]:
    """Build the credential-free Unix-socket client factory for one deployment."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        *,
        follow_redirects: bool = True,
        **options: Any,
    ) -> httpx2.AsyncClient:
        """Keep ambient proxy variables and redirects out of the socket session."""

        _ = headers, auth, follow_redirects
        options.pop("trust_env", None)
        options.pop("transport", None)
        return httpx2.AsyncClient(
            transport=httpx2.AsyncHTTPTransport(uds=str(socket_path)),
            timeout=timeout or httpx2.Timeout(30.0, read=300.0),
            follow_redirects=False,
            trust_env=False,
            **options,
        )

    return factory


def _create_bridge_proxy(
    config: DeploymentConfig,
    client: str,
) -> FastMCPProxy:
    transport = StreamableHttpTransport(
        config.endpoint,
        httpx_client_factory=_uds_http_client_factory(config.socket_path),
    )
    # new_stateful caches one connected client per front connection; a shared
    # client is re-entered per operation and pays an upstream handshake churn
    # even against the stateless upstream (measured: 9 connects vs 2 for one
    # two-connection workload).
    owner: StatefulProxyClient[Any] = StatefulProxyClient(
        transport,
        name=f"soleaux-{client}-bridge",
        roots=None,
        sampling_handler=None,
        elicitation_handler=None,
        log_handler=_discard_upstream_log,
        progress_handler=_discard_upstream_progress,
        timeout=60,
        mode="legacy",
    )
    return FastMCPProxy(
        client_factory=owner.new_stateful,
        name=f"Soleaux {client} bridge",
        provider_error_strategy="raise",
    )


def run_bridge(client: str) -> None:
    config = load_deployment_config()
    proxy = _create_bridge_proxy(config, client)
    proxy.run(transport="stdio", show_banner=False)


def run_context(
    client: str,
    *,
    stdin: Any = None,
    stdout: Any = None,
) -> int:
    """Emit one host context payload for the objective on stdin."""
    source = sys.stdin if stdin is None else stdin
    output = sys.stdout if stdout is None else stdout
    prompt = source.read()
    if not prompt:
        raise DeploymentError("a nonempty task objective is required on stdin")
    output.write(f"{asyncio.run(request_context(prompt, client))}{_OUTPUT_TERMINATOR}")
    return 0


_SUCCESS = 0
_HOSTS = ("claude", "codex", "opencode")


def main(arguments: Sequence[str] | None = None) -> int:
    """Standalone entrypoint retained for the legacy script shim."""
    import argparse

    parser = argparse.ArgumentParser(prog="soleaux-client")
    subcommands = parser.add_subparsers(dest="command", required=True)
    bridge = subcommands.add_parser("bridge")
    bridge.add_argument("client", choices=_HOSTS)
    context = subcommands.add_parser("context")
    context.add_argument("client", choices=_HOSTS)
    options = parser.parse_args(arguments)
    try:
        if options.command == "bridge":
            run_bridge(options.client)
        else:
            return run_context(options.client)
    except DeploymentError as error:
        sys.stderr.write(f"soleaux-client: {error}\n")
        return 2
    except Exception:
        sys.stderr.write(
            "soleaux-client: the Soleaux request failed; run `soleaux service status` and retry.\n"
        )
        return 2
    return _SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
