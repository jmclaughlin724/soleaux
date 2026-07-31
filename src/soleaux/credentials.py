"""OAuth token storage for proxied MCP backends (D035).

FastMCP's ``OAuth`` keys tokens by server URL inside whatever ``AsyncKeyValue``
it is handed, so per-backend isolation is owned here: each backend gets its own
store. Disk stores live one directory per backend under the platformdirs user
data dir; the directory is created mode 0700 and is the access guard, matching
the user-private posture of the deployment Unix socket. Keyring stores isolate
each backend under its own service name (``soleaux-<backend>``), so two named
backends pointing at the same server URL never share one keyring entry. Tokens
never live under the worktree and never appear in logs.
"""

from __future__ import annotations

import pathlib

import platformdirs
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.disk import DiskStore
from key_value.aio.stores.keyring.store import (
    KeyringStore,
    KeyringV1CollectionSanitizationStrategy,
    KeyringV1KeySanitizationStrategy,
)

import soleaux.contracts.config

_KEYRING_SERVICE_PREFIX = "soleaux"
_TOKEN_ROOT_MODE = 0o700


def keyring_service_name(backend_name: str) -> str:
    """The one keyring service name isolating one backend's tokens."""
    return f"{_KEYRING_SERVICE_PREFIX}-{backend_name}"


def token_store_root() -> pathlib.Path:
    """Root directory for all backend token stores."""
    return platformdirs.user_data_path("soleaux") / "mcp-tokens"


def token_store_directory(backend_name: str) -> pathlib.Path:
    """The one disk store directory for one backend."""
    return token_store_root() / backend_name


def _ensure_private_directory(directory: pathlib.Path) -> None:
    directory.mkdir(mode=_TOKEN_ROOT_MODE, parents=True, exist_ok=True)
    # Tighten pre-existing directories; files inside inherit the directory guard.
    directory.chmod(_TOKEN_ROOT_MODE)


def build_token_store(
    backend: soleaux.contracts.config.McpBackendConfig,
    *,
    backend_name: str,
) -> AsyncKeyValue:
    """Build the configured per-backend token store for one OAuth backend."""
    if backend.token_store == "keyring":
        return KeyringStore(
            service_name=keyring_service_name(backend_name),
            key_sanitization_strategy=KeyringV1KeySanitizationStrategy(),
            collection_sanitization_strategy=KeyringV1CollectionSanitizationStrategy(),
        )
    directory = token_store_directory(backend_name)
    _ensure_private_directory(directory)
    return DiskStore(directory=directory)


def clear_token_store(
    backend: soleaux.contracts.config.McpBackendConfig,
    *,
    backend_name: str,
) -> bool:
    """Remove one backend's disk token store. Returns True when one existed."""
    directory = token_store_directory(backend_name)
    if backend.token_store == "keyring" or not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_file():
            entry.unlink()
    return True
