"""Hatch build hook that stamps a wheel-time build identity into the package.

The generated artifact is included only in non-editable wheel builds. Editable
installs fall back to runtime git resolution in `soleaux._identity`.
"""

from __future__ import annotations

import json
import subprocess
import typing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_GIT_SHA_EXCEPTIONS: tuple[type[BaseException], ...] = (
    subprocess.CalledProcessError,
    FileNotFoundError,
    subprocess.TimeoutExpired,
)


class CustomBuildHook(BuildHookInterface[Any]):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return

        existing = self._existing_identity()
        git_sha = self._git_sha() or typing.cast(str | None, existing.get("git_sha"))
        build_time_utc = self._build_time_utc(git_sha, existing)

        identity = {
            "version": "0.1.0",
            "git_sha": git_sha,
            "build_time_utc": build_time_utc,
            "source": "wheel",
        }

        staging_path = Path(self.directory) / "build_identity.json"
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path.write_text(
            json.dumps(identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        build_data["force_include"][str(staging_path)] = "soleaux/resources/build_identity.json"

    def _build_time_utc(self, git_sha: str | None, existing: dict[str, Any]) -> str:
        """Return a deterministic build timestamp when possible.

        Uses ``SOURCE_DATE_EPOCH`` if set, otherwise reuses the timestamp from a
        pre-existing artifact or the committer date of the resolved git SHA.
        Falls back to the current time only when no git metadata is available.
        """
        source_date_epoch = self._source_date_epoch()
        if source_date_epoch is not None:
            return source_date_epoch

        existing_time = existing.get("build_time_utc")
        if isinstance(existing_time, str) and existing_time:
            return existing_time

        if git_sha is not None:
            commit_time = self._git_committer_date(git_sha)
            if commit_time is not None:
                return commit_time

        return datetime.now(UTC).isoformat()

    def _source_date_epoch(self) -> str | None:
        raw = typing.cast(str | None, __import__("os").environ.get("SOURCE_DATE_EPOCH"))
        if raw is None:
            return None
        try:
            return datetime.fromtimestamp(int(raw), tz=UTC).isoformat()
        except Exception:
            return None

    def _git_committer_date(self, sha: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%cI", sha],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except _GIT_SHA_EXCEPTIONS:
            return None
        iso = result.stdout.strip()
        return iso if iso else None

    def _git_sha(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except _GIT_SHA_EXCEPTIONS:
            return None
        sha = result.stdout.strip()
        return sha if sha else None

    def _existing_identity(self) -> dict[str, Any]:
        """Reuse the identity stamped into a pre-existing build artifact.

        This preserves git SHA and build time when a wheel is built from an
        sdist that was itself produced from a git checkout.
        """
        try:
            path = Path(self.root) / "soleaux" / "resources" / "build_identity.json"
            if not path.is_file():
                return {}
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return typing.cast(dict[str, Any], payload)
        except Exception:
            return {}
