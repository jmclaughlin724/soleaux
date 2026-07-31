"""Deployment discovery and validation for the Soleaux bridge and service.

Discovery order (first hit wins):

1. ``SOLEAUX_DEPLOYMENT`` environment variable (``SOLEAUX_DEPLOYMENT_CONFIG``
   remains as the legacy alias).
2. The enclosing repository's per-repo v2 document:
   ``<repo>/scripts/soleaux/deployment.json`` or ``<repo>/soleaux.deployment.json``.
3. The machine-level registry document at
   ``~/Library/Application Support/Soleaux/deployment.json``.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from soleaux.contracts.deployment import (
    ATTACH_REPAIR_COMMAND,
    SUPPORTED_DEPLOYMENT_SCHEMAS,
)

_SOCKET_HOSTNAME = "soleaux.local"
# macOS limits AF_UNIX sun_path to 104 bytes; keep a margin for resolution.
_MAX_SOCKET_PATH_CHARACTERS = 100

_LEGACY_REPO_RELATIVE = Path("scripts") / "soleaux" / "deployment.json"
_REPO_ROOT_FILENAME = "soleaux.deployment.json"
_MACHINE_REGISTRY_RELATIVE = Path("Library") / "Application Support" / "Soleaux" / "deployment.json"


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentConfig:
    endpoint: str
    service_label: str
    socket_relative_path: str
    workspace_root: Path | None = None

    @property
    def socket_path(self) -> Path:
        return Path.home() / self.socket_relative_path


class DeploymentError(RuntimeError):
    """A bounded local deployment failure safe to expose to host adapters."""


def _repository_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def deployment_config_path(*, start: Path | None = None) -> Path:
    """Resolve the deployment document following the canonical discovery order."""
    override = os.environ.get("SOLEAUX_DEPLOYMENT") or os.environ.get("SOLEAUX_DEPLOYMENT_CONFIG")
    if override:
        return Path(override)

    root = _repository_root(start or Path.cwd())
    if root is not None:
        legacy = root / _LEGACY_REPO_RELATIVE
        if legacy.is_file():
            return legacy
        per_repo = root / _REPO_ROOT_FILENAME
        if per_repo.is_file():
            return per_repo

    return Path.home() / _MACHINE_REGISTRY_RELATIVE


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} must be a nonempty string")
    return value


def load_deployment_config(*, start: Path | None = None) -> DeploymentConfig:
    config_path = deployment_config_path(start=start)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(
            f"the Soleaux deployment config could not be loaded from {config_path}"
        ) from error
    if not isinstance(payload, dict):
        raise DeploymentError("the Soleaux deployment config must be an object")
    record = cast("dict[str, object]", payload)
    schema_version = record.get("schema_version")
    if schema_version not in SUPPORTED_DEPLOYMENT_SCHEMAS:
        raise DeploymentError(
            "the Soleaux deployment config has an unsupported schema "
            f"({schema_version!r}); re-register the deployment with `{ATTACH_REPAIR_COMMAND}`"
        )

    endpoint = _required_string(record.get("endpoint"), "endpoint")
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "http"
        or parsed_endpoint.hostname != _SOCKET_HOSTNAME
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
    ):
        raise DeploymentError(f"endpoint must be a credential-free http://{_SOCKET_HOSTNAME} URL")

    socket_relative_path = _required_string(
        record.get("socket_relative_path"),
        "socket_relative_path",
    )
    relative = Path(socket_relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeploymentError("socket_relative_path must stay relative to the home directory")
    workspace_root_value = record.get("workspace_root")
    workspace_root: Path | None = None
    if workspace_root_value is not None:
        candidate = Path(_required_string(workspace_root_value, "workspace_root"))
        if not candidate.is_absolute():
            candidate = config_path.resolve().parent / candidate
        workspace_root = candidate.resolve()
    config = DeploymentConfig(
        endpoint=endpoint,
        service_label=_required_string(record.get("service_label"), "service_label"),
        socket_relative_path=socket_relative_path,
        workspace_root=workspace_root,
    )
    if len(str(config.socket_path)) > _MAX_SOCKET_PATH_CHARACTERS:
        raise DeploymentError("the resolved Soleaux socket path exceeds the AF_UNIX limit")
    return config
