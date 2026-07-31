"""Repository-owned private-socket HTTP composition for the local Soleaux service.

The production service listens on a current-user-owned Unix-domain socket, so
filesystem permissions replace bearer credentials. It serves stateless
Streamable HTTP — every request is independent and a restart is invisible to
agents. The ``serve`` entrypoint prebinds the socket in-process — Uvicorn
would create it mode 0666 — and hands the bound listener to FastMCP. Socket
ownership, modes, and occupants are validated here; launchd lifecycle stays
in ``service.mjs``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import socket
import stat
import sys
from typing import Any

from fastmcp import FastMCP

from soleaux.bridge.deployment import DeploymentError, load_deployment_config
from soleaux.contracts.config import ResolvedConfig
from soleaux.server import create_server

_ALLOWED_HOSTS = ["soleaux.local"]


def _workspace_root() -> pathlib.Path:
    deployment = load_deployment_config()
    if deployment.workspace_root is None:
        raise DeploymentError("the Soleaux deployment config must declare workspace_root")
    return deployment.workspace_root


def create_workspace_server() -> FastMCP[dict[str, Any]]:
    """Compose the workspace server; the private socket owns access control."""
    return create_server(_workspace_root(), deployment_transport="http")


def create_development_server() -> FastMCP[dict[str, Any]]:
    """Compose the reload-only development server with zero MCP backends."""
    return create_server(
        _workspace_root(),
        config=ResolvedConfig.default(),
        deployment_transport="http",
    )


def _ensure_socket_directory(directory: pathlib.Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise DeploymentError(f"socket directory {directory} must not be a symlink")
    if info.st_uid != os.getuid():
        raise DeploymentError(f"socket directory {directory} must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        directory.chmod(0o700)


def _prepare_socket_path(path: pathlib.Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
        raise DeploymentError(f"refusing to replace non-socket occupant {path}")
    if info.st_uid != os.getuid():
        raise DeploymentError(f"refusing to replace foreign-owned socket {path}")
    path.unlink()


def _prebind_socket(path: pathlib.Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen()
    except BaseException:
        listener.close()
        raise
    return listener


async def _serve() -> None:
    deployment = load_deployment_config()
    socket_path = deployment.socket_path
    _ensure_socket_directory(socket_path.parent)
    _prepare_socket_path(socket_path)
    listener = _prebind_socket(socket_path)
    server = create_workspace_server()
    try:
        await server.run_http_async(
            transport="http",
            path="/mcp",
            stateless_http=True,
            host_origin_protection=True,
            allowed_hosts=list(_ALLOWED_HOSTS),
            sockets=[listener],
            show_banner=False,
        )
    finally:
        listener.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if args != ["serve"]:
        raise DeploymentError("usage: http_service.py serve")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
