#!/usr/bin/env python3
"""Build a deterministic, explicitly unsigned Soleaux development-alpha archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, cast

VERSION = "0.4.0-dev.5"
PRODUCT_VERSION = f"Soleaux {VERSION}"
PUBLIC_TOOL_CEILING = 12
PRODUCTION_CLAIM_ALLOWED = False


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_version(binary: pathlib.Path) -> str:
    output = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if VERSION not in output:
        raise SystemExit(f"unexpected version from {binary}: {output}")
    return output


def normalized_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower().replace("amd64", "x86_64")
    return f"{system}-{machine}"


def copy_file(source: pathlib.Path, destination: pathlib.Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def write_text(path: pathlib.Path, value: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    path.chmod(mode)


def cargo_package_key(package: dict[str, Any]) -> str:
    source = package.get("source") or "workspace"
    return f"{package['name']}@{package['version']}#{source}"


def normalized_cargo_sbom(metadata: dict[str, Any]) -> dict[str, Any]:
    packages_value = metadata.get("packages")
    if not isinstance(packages_value, list):
        raise SystemExit("cargo metadata omitted packages")
    packages = cast(list[dict[str, Any]], packages_value)
    id_to_key: dict[str, str] = {
        str(package["id"]): cargo_package_key(package) for package in packages
    }
    normalized_packages: list[dict[str, Any]] = []
    for package in packages:
        dependencies: list[dict[str, Any]] = []
        dependency_values = cast(
            list[dict[str, Any]], package.get("dependencies", [])
        )
        for dependency in dependency_values:
            dependencies.append(
                {
                    "name": dependency["name"],
                    "rename": dependency.get("rename"),
                    "source": dependency.get("source"),
                    "requirement": dependency.get("req"),
                    "kind": dependency.get("kind"),
                    "optional": bool(dependency.get("optional", False)),
                    "usesDefaultFeatures": bool(
                        dependency.get("uses_default_features", True)
                    ),
                    "features": sorted(
                        cast(list[str], dependency.get("features", []))
                    ),
                    "target": dependency.get("target"),
                }
            )
        dependencies.sort(
            key=lambda item: (
                str(item["name"]),
                str(item["rename"] or ""),
                str(item["source"] or ""),
                str(item["kind"] or ""),
                str(item["target"] or ""),
                str(item["requirement"] or ""),
            )
        )
        targets: list[dict[str, Any]] = []
        target_values = cast(list[dict[str, Any]], package.get("targets", []))
        for target in target_values:
            targets.append(
                {
                    "name": target["name"],
                    "kind": sorted(cast(list[str], target.get("kind", []))),
                    "crateTypes": sorted(
                        cast(list[str], target.get("crate_types", []))
                    ),
                    "requiredFeatures": sorted(
                        cast(list[str], target.get("required-features", []))
                    ),
                    "edition": target.get("edition"),
                    "doctest": bool(target.get("doctest", False)),
                    "test": bool(target.get("test", False)),
                    "doc": bool(target.get("doc", False)),
                }
            )
        targets.sort(
            key=lambda item: (
                str(item["name"]),
                tuple(cast(list[str], item["kind"])),
                tuple(cast(list[str], item["crateTypes"])),
            )
        )
        feature_values = cast(
            dict[str, list[str]], package.get("features", {})
        )
        features: dict[str, list[str]] = {
            name: sorted(values) for name, values in sorted(feature_values.items())
        }
        normalized_packages.append(
            {
                "key": cargo_package_key(package),
                "name": package["name"],
                "version": package["version"],
                "source": package.get("source"),
                "license": package.get("license"),
                "repository": package.get("repository"),
                "edition": package.get("edition"),
                "rustVersion": package.get("rust_version"),
                "links": package.get("links"),
                "features": features,
                "dependencies": dependencies,
                "targets": targets,
            }
        )
    normalized_packages.sort(key=lambda item: str(item["key"]))

    resolve_value = metadata.get("resolve")
    resolve = (
        cast(dict[str, Any], resolve_value)
        if isinstance(resolve_value, dict)
        else {}
    )
    nodes: list[dict[str, Any]] = []
    node_values = cast(list[dict[str, Any]], resolve.get("nodes", []))
    for node in node_values:
        dependencies = sorted(
            id_to_key[str(value)]
            for value in cast(list[Any], node.get("dependencies", []))
        )
        dependency_edges: list[dict[str, Any]] = []
        edge_values = cast(list[dict[str, Any]], node.get("deps", []))
        for edge in edge_values:
            dep_kind_values = cast(
                list[dict[str, Any]], edge.get("dep_kinds", [])
            )
            dependency_edges.append(
                {
                    "name": edge["name"],
                    "package": id_to_key[str(edge["pkg"])],
                    "kinds": sorted(
                        (
                            {
                                "kind": kind.get("kind"),
                                "target": kind.get("target"),
                            }
                            for kind in dep_kind_values
                        ),
                        key=lambda item: (
                            str(item["kind"] or ""),
                            str(item["target"] or ""),
                        ),
                    ),
                }
            )
        dependency_edges.sort(
            key=lambda item: (str(item["name"]), str(item["package"]))
        )
        nodes.append(
            {
                "package": id_to_key[str(node["id"])],
                "dependencies": dependencies,
                "dependencyEdges": dependency_edges,
                "features": sorted(cast(list[str], node.get("features", []))),
            }
        )
    nodes.sort(key=lambda item: str(item["package"]))
    root = resolve.get("root")
    workspace_members = cast(list[Any], metadata.get("workspace_members", []))
    workspace_default_members = cast(
        list[Any], metadata.get("workspace_default_members", [])
    )
    return {
        "schemaVersion": "soleaux.cargo-sbom/v1",
        "cargoMetadataFormatVersion": metadata.get("version"),
        "workspaceMembers": sorted(id_to_key[str(value)] for value in workspace_members),
        "workspaceDefaultMembers": sorted(
            id_to_key[str(value)] for value in workspace_default_members
        ),
        "resolve": {
            "root": id_to_key.get(str(root)) if root is not None else None,
            "nodes": nodes,
        },
        "packages": normalized_packages,
    }


def package_manifest(root: pathlib.Path, source_commit: str, epoch: int) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "MANIFEST.json":
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "mode": oct(stat.S_IMODE(path.stat().st_mode)),
            }
        )
    return {
        "schemaVersion": "soleaux.unsigned-alpha-manifest/v1",
        "product": "Soleaux",
        "version": VERSION,
        "sourceCommit": source_commit,
        "platform": normalized_platform(),
        "sourceDateEpoch": epoch,
        "unsigned": True,
        "productionClaimAllowed": PRODUCTION_CLAIM_ALLOWED,
        "publicToolCeiling": PUBLIC_TOOL_CEILING,
        "files": files,
    }


def tar_filter(epoch: int):
    def normalize(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"
        member.mtime = epoch
        member.pax_headers = {}
        executable_suffixes = (
            "/bin/soleaux",
            "/bin/soleauxd",
            "/install.sh",
            "/uninstall.sh",
        )
        if member.isdir() or member.name.endswith(executable_suffixes):
            member.mode = 0o755
        else:
            member.mode = 0o644
        return member

    return normalize


def add_sorted(
    archive: tarfile.TarFile,
    root: pathlib.Path,
    package_name: str,
    epoch: int,
) -> None:
    archive.add(root, arcname=package_name, recursive=False, filter=tar_filter(epoch))
    paths = sorted(
        root.rglob("*"),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    )
    for path in paths:
        archive.add(
            path,
            arcname=f"{package_name}/{path.relative_to(root).as_posix()}",
            recursive=False,
            filter=tar_filter(epoch),
        )


def build(args: argparse.Namespace) -> dict[str, Any]:
    repository = args.repository.resolve()
    binaries = args.binaries.resolve()
    output = args.output.resolve()
    metadata = args.cargo_metadata.resolve()
    epoch = int(args.source_date_epoch)
    source_commit = args.source_commit.strip()
    invalid_commit = len(source_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_commit
    )
    if invalid_commit:
        raise SystemExit("source commit must be a full lowercase Git SHA-1")
    soleaux = binaries / ("soleaux.exe" if os.name == "nt" else "soleaux")
    soleauxd = binaries / ("soleauxd.exe" if os.name == "nt" else "soleauxd")
    if not soleaux.is_file() or not soleauxd.is_file():
        raise SystemExit("compiled soleaux and soleauxd binaries are required")
    versions = {"soleaux": run_version(soleaux), "soleauxd": run_version(soleauxd)}
    loaded_metadata = cast(object, json.loads(metadata.read_text(encoding="utf-8")))
    if not isinstance(loaded_metadata, dict):
        raise SystemExit("cargo metadata root must be an object")
    cargo_metadata = cast(dict[str, Any], loaded_metadata)
    cargo_sbom = normalized_cargo_sbom(cargo_metadata)

    package_name = f"soleaux-{VERSION}-{normalized_platform()}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soleaux-alpha-package-") as temporary:
        root = pathlib.Path(temporary) / package_name
        copy_file(soleaux, root / "bin" / soleaux.name, 0o755)
        copy_file(soleauxd, root / "bin" / soleauxd.name, 0o755)
        for relative in (
            "LICENSE",
            "README.md",
            "UNIFIED-MCP-PROFILE.md",
            "CONTEXT-PACKET-V2.md",
            "contracts/phase0-identity.json",
            "native/Cargo.lock",
            "native/contracts/unified-mcp-profile-v2.json",
            "native/contracts/context-packet-v2.schema.json",
        ):
            source = repository / relative
            if not source.is_file():
                raise SystemExit(f"required package source is missing: {relative}")
            copy_file(source, root / "share" / relative, 0o644)
        write_text(
            root / "INSTALL.md",
            f"""# Soleaux {VERSION} unsigned development alpha

This archive is unsigned and is not a production release. `productionClaimAllowed` remains false.

Run `./install.sh` to copy `soleaux` and `soleauxd` into
`${{SOLEAUX_INSTALL_BIN:-$HOME/.local/bin}}` and write a per-user service manifest.
The installer does not mutate Claude, Codex, OpenCode, Cursor, or other
vendor-native stores.

Run `soleaux service start` after reviewing the generated per-user service
manifest. Use `soleaux doctor`, `soleaux service status`, `soleaux backup`,
`soleaux export`, and `soleaux repair` for operational checks.

Run `./uninstall.sh` to call `soleaux uninstall`. State removal requires an
explicit separate decision.
""",
        )
        write_text(
            root / "install.sh",
            """#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN=${SOLEAUX_INSTALL_BIN:-"$HOME/.local/bin"}
mkdir -p "$BIN"
install -m 0755 "$HERE/bin/soleaux" "$BIN/soleaux"
install -m 0755 "$HERE/bin/soleauxd" "$BIN/soleauxd"
"$BIN/soleaux" install --cli "$BIN/soleaux" --daemon "$BIN/soleauxd" --no-start
""",
            0o755,
        )
        write_text(
            root / "uninstall.sh",
            """#!/bin/sh
set -eu
BIN=${SOLEAUX_INSTALL_BIN:-"$HOME/.local/bin"}
if [ -x "$BIN/soleaux" ]; then
  "$BIN/soleaux" uninstall
fi
""",
            0o755,
        )
        write_text(
            root / "SBOM.cargo.json",
            json.dumps(cargo_sbom, indent=2, sort_keys=True) + "\n",
        )
        write_text(
            root / "BUILD-IDENTITY.json",
            json.dumps(
                {
                    "schemaVersion": "soleaux.unsigned-alpha-build/v1",
                    "product": "Soleaux",
                    "version": VERSION,
                    "sourceCommit": source_commit,
                    "sourceDateEpoch": epoch,
                    "platform": normalized_platform(),
                    "versions": versions,
                    "unsigned": True,
                    "productionClaimAllowed": PRODUCTION_CLAIM_ALLOWED,
                    "publicToolCeiling": PUBLIC_TOOL_CEILING,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        manifest = package_manifest(root, source_commit, epoch)
        write_text(root / "MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        with tarfile.open(output, mode="w:xz", format=tarfile.PAX_FORMAT, preset=9) as archive:
            add_sorted(archive, root, package_name, epoch)

    manifest_files = cast(list[dict[str, Any]], manifest["files"])
    result = {
        "schemaVersion": "soleaux.unsigned-alpha-package/v1",
        "product": PRODUCT_VERSION,
        "sourceCommit": source_commit,
        "platform": normalized_platform(),
        "archive": str(output),
        "archiveBytes": output.stat().st_size,
        "archiveSha256": sha256(output),
        "fileCount": len(manifest_files) + 1,
        "unsigned": True,
        "productionClaimAllowed": PRODUCTION_CLAIM_ALLOWED,
        "publicToolCeiling": PUBLIC_TOOL_CEILING,
        "status": "pass",
    }
    if args.report:
        write_text(args.report.resolve(), json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=pathlib.Path, required=True)
    parser.add_argument("--binaries", type=pathlib.Path, required=True)
    parser.add_argument("--cargo-metadata", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--source-date-epoch",
        default=os.environ.get("SOURCE_DATE_EPOCH", "0"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
