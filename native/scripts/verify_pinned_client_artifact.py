#!/usr/bin/env python3
"""Download and verify one immutable client artifact from the locked matrix."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX = ROOT / "native" / "contracts" / "client-capability-matrix-v1.json"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


def fail(message: str) -> None:
    raise SystemExit(message)


def download(url: str, destination: Path) -> tuple[int, str, str, str]:
    sha1 = hashlib.sha1(usedforsecurity=False)
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": "soleaux-artifact-verifier/1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                fail(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")
            sha1.update(chunk)
            sha256.update(chunk)
            sha512.update(chunk)
            output.write(chunk)
    return total, sha1.hexdigest(), sha256.hexdigest(), base64.b64encode(sha512.digest()).decode()


def load_version(matrix: Path, platform: str) -> dict[str, Any]:
    data = json.loads(matrix.read_text(encoding="utf-8"))
    record = next((item for item in data["platforms"] if item["id"] == platform), None)
    if record is None or len(record.get("versions", [])) != 1:
        fail(f"platform does not have one locked version: {platform}")
    return record["versions"][0]


def verify(args: argparse.Namespace) -> dict[str, Any]:
    version = load_version(args.matrix, args.platform)
    args.download_dir.mkdir(parents=True, exist_ok=True)
    if args.platform in {"claude_code", "codex"}:
        artifact = version.get("packageArtifact")
        if not isinstance(artifact, dict):
            fail("matrix omitted packageArtifact")
        destination = args.download_dir / f"{args.platform}-{artifact['version']}.tgz"
        size, sha1, sha256, sha512 = download(str(artifact["tarball"]), destination)
        expected_sri = str(artifact["integrity"])
        actual_sri = f"sha512-{sha512}"
        if sha1 != artifact["shasum"] or actual_sri != expected_sri:
            destination.unlink(missing_ok=True)
            fail(f"{args.platform} package digest mismatch")
        identity = {
            "package": artifact["package"],
            "version": artifact["version"],
            "url": artifact["tarball"],
            "sha1": sha1,
            "sha256": sha256,
            "integrity": actual_sri,
        }
    elif args.platform == "opencode":
        asset = version.get("linuxX64Asset")
        if not isinstance(asset, dict):
            fail("matrix omitted linuxX64Asset")
        destination = args.download_dir / "opencode-linux-x64.tar.gz"
        size, _sha1, sha256, _sha512 = download(str(asset["url"]), destination)
        if sha256 != asset["sha256"]:
            destination.unlink(missing_ok=True)
            fail("OpenCode artifact SHA-256 mismatch")
        identity = {"version": version["version"], "url": asset["url"], "sha256": sha256}
    else:
        fail("only claude_code, codex, and opencode have pinned distributable artifacts")

    result = {
        "schemaVersion": "soleaux.pinned-client-artifact-verification/v1",
        "platform": args.platform,
        "artifactPath": str(destination.resolve()),
        "artifactBytes": size,
        "identity": identity,
        "productionClaimAllowed": False,
        "status": "pass",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["claude_code", "codex", "opencode"], required=True)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--download-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.matrix = arguments.matrix.resolve()
    arguments.download_dir = (
        arguments.download_dir.resolve()
        if arguments.download_dir
        else Path(tempfile.mkdtemp(prefix="soleaux-client-artifact-"))
    )
    verify(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
