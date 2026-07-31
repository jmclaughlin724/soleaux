"""Regenerate the D019 zero-MCP contract fixture from the live composition.

The generator and its contract test consume the same public-client projection.
Prints the new sha256 to paste into `ZERO_MCP_SHA256`. Run from the package
directory:

    uv run --locked --package soleaux python -m scripts.generate_zero_mcp_fixture
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib

from scripts.zero_mcp_fixture import canonical_bytes, canonical_projection
from soleaux.server import create_server

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PACKAGE_ROOT / "tests" / "fixtures" / "contracts"
ZERO_MCP_FIXTURE = FIXTURE_ROOT / "d019-zero-mcp.json"


def main() -> None:
    server = create_server(FIXTURE_ROOT)
    projection = asyncio.run(canonical_projection(server))
    payload = canonical_bytes(projection)

    ZERO_MCP_FIXTURE.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    print(f"wrote {ZERO_MCP_FIXTURE.relative_to(PACKAGE_ROOT)}")
    print(f'ZERO_MCP_SHA256 = "{digest}"')


if __name__ == "__main__":
    main()
