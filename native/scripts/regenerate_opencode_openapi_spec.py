#!/usr/bin/env python3
"""Re-fetch the vendored OpenCode OpenAPI spec from its pinned upstream commit.

The build never touches the network: `soleaux-adapter-opencode` embeds the
digest below and loads the vendored bytes from its `contracts/` directory.
This script exists so the vendored file is re-fetchable and auditable:

- default mode re-downloads `packages/sdk/openapi.json` at the exact commit
  of the `v1.18.14` release tag and requires the bytes to hash to the pinned
  digest before writing;
- `--refresh-from-server` captures `GET /doc` from a locally running
  `npx opencode-ai@<version> serve` instead, for a reviewed version bump.

A digest change is a client-version change: it requires the five-step matrix
protocol in docs/testing/CLIENT-CAPABILITY-MATRIX.md and a matching update to
`OPENCODE_OPENAPI_SHA256` in the adapter crate.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDORED_SPEC = (
    ROOT
    / "native"
    / "daemon"
    / "adapters"
    / "opencode"
    / "contracts"
    / "opencode-openapi-1.18.14.json"
)
PINNED_COMMIT = "65cf14df16c191f3e9684f0d9a8bae69103ced6d"
PINNED_SHA256 = "5bbd6493a1a488ef4294889341c896e420f814ecea95822100aaa9f3f95ab2d1"
PINNED_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/"
    f"{PINNED_COMMIT}/packages/sdk/openapi.json"
)
MAX_SPEC_BYTES = 16 * 1024 * 1024


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "soleaux-opencode-spec/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(MAX_SPEC_BYTES + 1)
    if len(payload) > MAX_SPEC_BYTES:
        raise SystemExit(f"spec exceeds {MAX_SPEC_BYTES} bytes")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-from-server",
        metavar="BASE_URL",
        help="capture GET <BASE_URL>/doc from a running opencode server instead "
        "of the pinned commit (reviewed version bumps only)",
    )
    arguments = parser.parse_args()

    if arguments.refresh_from_server:
        url = arguments.refresh_from_server.rstrip("/") + "/doc"
        payload = fetch(url)
        digest = hashlib.sha256(payload).hexdigest()
        VENDORED_SPEC.write_bytes(payload)
        print(f"wrote {VENDORED_SPEC} ({len(payload)} bytes) from {url}")
        print(f"sha256 {digest}")
        if digest != PINNED_SHA256:
            print(
                "digest differs from the pinned vendored spec: this is a client-version "
                "change; follow the capability-matrix update protocol and update "
                "OPENCODE_OPENAPI_SHA256 in soleaux-adapter-opencode"
            )
        return

    payload = fetch(PINNED_URL)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != PINNED_SHA256:
        raise SystemExit(
            f"upstream bytes at the pinned commit hash to {digest}, expected {PINNED_SHA256}; "
            "refusing to write"
        )
    VENDORED_SPEC.write_bytes(payload)
    print(f"wrote {VENDORED_SPEC} ({len(payload)} bytes)")
    print(f"sha256 {digest} (matches the pinned digest)")


if __name__ == "__main__":
    main()
