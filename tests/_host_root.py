"""Host-checkout resolution for dogfood tests.

Soleaux is its own project. Tests that read a consuming repository's
manifests (migrations, installed engines, agent surfaces) run only when
``SOLEAUX_HOST_ROOT`` names that checkout; otherwise they skip explicitly.
"""

from __future__ import annotations

import os
import pathlib

import pytest

SOLEAUX_ROOT = pathlib.Path(__file__).resolve().parents[1]


def require_host_root() -> pathlib.Path:
    configured = os.environ.get("SOLEAUX_HOST_ROOT", "")
    root = pathlib.Path(configured).expanduser() if configured else None
    if root is None or not (root / "package.json").is_file():
        pytest.skip(
            "host repository unavailable; set SOLEAUX_HOST_ROOT",
            allow_module_level=True,
        )
    return root
