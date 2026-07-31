"""AllowedWorkspaceSet (D022): launcher/client-authorized roots frozen at init.

Request workspace selectors can only choose from this frozen set; root
containment alone is not authorization. Trust digests bind the exact launch
root and config content so cross-root trust reuse fails.
"""

from __future__ import annotations

import hashlib
import os
import pathlib

import soleaux.contracts.repository


class WorkspaceError(Exception):
    """Base for workspace authorization failures."""


class UnauthorizedRootError(WorkspaceError):
    """The selector or launch root is not in the authorized set."""


class RootEscapeError(WorkspaceError):
    """A candidate path escapes its authorized root after resolution."""


class TrustDigestMismatchError(WorkspaceError):
    """A presented config-content digest does not bind to this launch root."""


def workspace_trust_digest(root: pathlib.Path, config_digest: str) -> str:
    """Bind the exact launch root and config content digest."""
    hasher = hashlib.sha256()
    hasher.update(str(root).encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(config_digest.encode("utf-8"))
    return hasher.hexdigest()


def _resolve_launch_root(raw: str) -> pathlib.Path:
    if "\x00" in raw:
        msg = "NUL bytes are not admitted in launch roots"
        raise UnauthorizedRootError(msg)
    if raw.lower().startswith("file://"):
        msg = f"file URIs are not launch roots: {raw!r}"
        raise UnauthorizedRootError(msg)
    if raw == "/dev" or raw.startswith("/dev/"):
        msg = f"device paths are not authorized roots: {raw!r}"
        raise UnauthorizedRootError(msg)
    try:
        resolved = pathlib.Path(raw).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        msg = f"launch root does not exist: {raw!r}"
        raise UnauthorizedRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"launch root is not a directory: {raw!r}"
        raise UnauthorizedRootError(msg)
    return resolved


class WorkspaceRoot:
    """One frozen authorized root."""

    __slots__ = ("root", "trust_digest", "workspace_id")

    def __init__(self, workspace_id: str, root: pathlib.Path, trust_digest: str) -> None:
        self.workspace_id = workspace_id
        self.root = root
        self.trust_digest = trust_digest

    def __repr__(self) -> str:
        return f"WorkspaceRoot({self.workspace_id!r}, {self.root})"


class AllowedWorkspaceSet:
    """The frozen set of authorized roots for one server process."""

    def __init__(self, roots: tuple[WorkspaceRoot, ...]) -> None:
        if not roots:
            msg = "AllowedWorkspaceSet requires at least one authorized root"
            raise ValueError(msg)
        identifiers = [root.workspace_id for root in roots]
        if len(set(identifiers)) != len(identifiers):
            msg = "duplicate workspace_id in AllowedWorkspaceSet"
            raise ValueError(msg)
        self._roots = {root.workspace_id: root for root in roots}

    @classmethod
    def from_launch(
        cls,
        roots: list[tuple[str, str]],
        *,
        config_digest: str,
    ) -> AllowedWorkspaceSet:
        """Freeze authorized (workspace_id, raw_root) pairs at launcher/client init."""
        resolved: list[WorkspaceRoot] = []
        for workspace_id, raw in roots:
            if not workspace_id or "\x00" in workspace_id:
                msg = "workspace_id must be nonempty and NUL-free"
                raise UnauthorizedRootError(msg)
            root = _resolve_launch_root(raw)
            resolved.append(
                WorkspaceRoot(
                    workspace_id=workspace_id,
                    root=root,
                    trust_digest=workspace_trust_digest(root, config_digest),
                )
            )
        return cls(tuple(resolved))

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        """Authorized identifiers in insertion order."""
        return tuple(self._roots.keys())

    def get(self, workspace_id: str | None) -> WorkspaceRoot:
        """Select an authorized root; None selects only when exactly one exists."""
        if workspace_id is None:
            if len(self._roots) == 1:
                return next(iter(self._roots.values()))
            msg = "workspace_id is required when multiple roots are authorized"
            raise UnauthorizedRootError(msg)
        try:
            return self._roots[workspace_id]
        except KeyError:
            msg = f"root {workspace_id!r} is not in the AllowedWorkspaceSet"
            raise UnauthorizedRootError(msg) from None

    def verify_trust(self, workspace_id: str, presented_digest: str) -> WorkspaceRoot:
        """Bind a presented config-content digest to the exact launch root."""
        root = self.get(workspace_id)
        if root.trust_digest != presented_digest:
            msg = "config-content digest does not bind to this launch root"
            raise TrustDigestMismatchError(msg)
        return root

    def admit(self, root: WorkspaceRoot, candidate: str | os.PathLike[str]) -> pathlib.Path:
        """Normalize a candidate inside the authorized root or reject it.

        Traversal, NUL bytes, and symlinks escaping the root are rejected after
        resolution; the empty readable root itself is admissible.
        """
        text = os.fspath(candidate)
        if text in {"", "."}:
            return root.root
        try:
            repository_path = soleaux.contracts.repository.RepositoryPath.admit(root, text)
            return repository_path.absolute(root).resolve(strict=False)
        except soleaux.contracts.repository.RepositoryIdentityError as exc:
            raise RootEscapeError(str(exc)) from exc
