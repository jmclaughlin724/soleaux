from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    # AF_UNIX sun_path is limited to 104 bytes on macOS; pytest's tmp_path exceeds it.
    directory = Path(tempfile.mkdtemp(dir="/tmp", prefix="slx-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
