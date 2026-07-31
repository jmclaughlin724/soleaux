"""Snapshot-only module resolution for TypeScript, Python, and Go."""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TypeGuard

from soleaux.lsp.generation import SemanticGeneration
from soleaux.lsp.resolvers import ModuleResolution
from soleaux.structural.snapshot import SnapshotBundle

MODULE_RESOLVER_NAME = "soleaux-module-resolver"
MODULE_RESOLVER_CONFIG_DIGEST = hashlib.sha256(b"soleaux-module-resolver/v1").hexdigest()

_TYPESCRIPT_LANGUAGES = frozenset({"javascript", "typescript", "tsx", "typescriptreact"})
_TYPESCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_CONTROL_NAMES = frozenset(
    {"go.mod", "jsconfig.json", "package.json", "pyproject.toml", "tsconfig.json"}
)


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


@dataclass(frozen=True)
class _TsConfig:
    directory: str
    base_url: str
    paths: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _Package:
    directory: str
    name: str
    exports: object | None
    default_target: str | None


def _object_dict(value: object) -> dict[str, object]:
    if not _is_object_dict(value):
        return {}
    mapping = value
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _directory(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _join_workspace(*parts: str) -> str | None:
    joined = posixpath.normpath(posixpath.join(*parts))
    if joined in {"", "."}:
        return ""
    if joined.startswith("/") or joined == ".." or joined.startswith("../"):
        return None
    return joined.removeprefix("./")


def _control_paths(bundle: SnapshotBundle) -> tuple[str, ...]:
    return tuple(
        path for path in sorted(bundle.contents) if PurePosixPath(path).name in _CONTROL_NAMES
    )


def module_generation(bundle: SnapshotBundle, source_path: str) -> SemanticGeneration:
    """Bind module resolution to every captured content and control input."""
    controls = _control_paths(bundle)
    dependencies = tuple(
        path for path in sorted(bundle.contents) if path != source_path and path not in controls
    )
    return SemanticGeneration.from_snapshot(
        bundle,
        provider_name=MODULE_RESOLVER_NAME,
        provider_config_digest=MODULE_RESOLVER_CONFIG_DIGEST,
        process_epoch=0,
        requested_file=source_path,
        dependency_paths=dependencies,
        control_paths=controls,
    )


class SnapshotModuleResolver:
    """Resolve module specifiers without reading beyond one frozen snapshot."""

    def __init__(self, bundle: SnapshotBundle) -> None:
        self._bundle = bundle
        self._paths = frozenset(bundle.contents)
        self._ts_configs = self._load_ts_configs()
        self._packages = self._load_packages()
        self._go_module = self._load_go_module()

    async def resolve_module(
        self,
        *,
        source_path: str,
        specifier: str,
        generation: SemanticGeneration,
    ) -> ModuleResolution:
        """Resolve one exact specifier against captured manifests and paths."""
        if generation.workspace_id != self._bundle.snapshot.workspace_id:
            return self._unresolved(
                source_path,
                specifier,
                generation,
                "semantic generation belongs to another workspace",
            )
        if generation.requested_file != source_path:
            return self._unresolved(
                source_path,
                specifier,
                generation,
                "semantic generation requested a different source file",
            )

        language = self._language_for(source_path)
        target: str | None
        reason: str
        if language in _TYPESCRIPT_LANGUAGES:
            target, reason = self._resolve_typescript(source_path, specifier)
        elif language == "python":
            target, reason = self._resolve_python(source_path, specifier)
        elif language == "go":
            target, reason = self._resolve_go(source_path, specifier)
        else:
            target = None
            reason = f"unsupported language {language or 'unknown'}"

        if target is None:
            return self._unresolved(source_path, specifier, generation, reason)
        omitted_reasons = () if generation.complete else generation.verification_issues
        return ModuleResolution(
            source_path=source_path,
            specifier=specifier,
            target_path=target,
            generation_fingerprint=generation.fingerprint,
            complete=generation.complete,
            omitted_reasons=omitted_reasons,
        )

    def _language_for(self, source_path: str) -> str:
        captured = next(
            (item for item in self._bundle.snapshot.files if item.path == source_path),
            None,
        )
        return (captured.language or "").lower() if captured is not None else ""

    def _resolve_typescript(self, source_path: str, specifier: str) -> tuple[str | None, str]:
        if specifier.startswith("."):
            candidate = _join_workspace(_directory(source_path), specifier)
            return self._resolved_file(candidate, _TYPESCRIPT_SUFFIXES)

        for config in self._applicable_ts_configs(source_path):
            for pattern, replacements in config.paths:
                wildcard = self._match_pattern(pattern, specifier)
                if wildcard is None:
                    continue
                for replacement in replacements:
                    expanded = replacement.replace("*", wildcard)
                    candidate = _join_workspace(
                        config.directory,
                        config.base_url,
                        expanded,
                    )
                    target, _reason = self._resolved_file(candidate, _TYPESCRIPT_SUFFIXES)
                    if target is not None:
                        return target, ""
            base_candidate = _join_workspace(
                config.directory,
                config.base_url,
                specifier,
            )
            target, _reason = self._resolved_file(base_candidate, _TYPESCRIPT_SUFFIXES)
            if target is not None:
                return target, ""

        package_target = self._resolve_package(specifier)
        if package_target is not None:
            return package_target, ""
        return None, f"external or unresolved package {specifier!r}"

    def _resolve_python(self, source_path: str, specifier: str) -> tuple[str | None, str]:
        level = len(specifier) - len(specifier.lstrip("."))
        module_name = specifier[level:]
        if level:
            base = _directory(source_path)
            for _parent in range(level - 1):
                base = _directory(base)
            module_path = module_name.replace(".", "/")
            candidate = _join_workspace(base, module_path)
        else:
            candidate = _join_workspace(specifier.replace(".", "/"))
        if candidate is None:
            return None, f"module {specifier!r} escapes the workspace"
        target, _reason = self._resolved_file(candidate, (".py",))
        if target is not None:
            return target, ""
        return None, f"external or unresolved Python module {specifier!r}"

    def _resolve_go(self, source_path: str, specifier: str) -> tuple[str | None, str]:
        directory: str | None = None
        if specifier.startswith("."):
            directory = _join_workspace(_directory(source_path), specifier)
        elif self._go_module is not None and (
            specifier == self._go_module or specifier.startswith(f"{self._go_module}/")
        ):
            directory = specifier.removeprefix(self._go_module).removeprefix("/")
        if directory is None:
            return None, f"external or unresolved Go package {specifier!r}"
        prefix = f"{directory}/" if directory else ""
        candidates = sorted(
            path
            for path in self._paths
            if path.startswith(prefix)
            and "/" not in path[len(prefix) :]
            and path.endswith(".go")
            and not path.endswith("_test.go")
        )
        if candidates:
            return candidates[0], ""
        return None, f"Go package {specifier!r} has no captured source file"

    def _resolved_file(
        self,
        candidate: str | None,
        suffixes: tuple[str, ...],
    ) -> tuple[str | None, str]:
        if candidate is None:
            return None, "module path escapes the workspace"
        possibilities = [candidate]
        if not PurePosixPath(candidate).suffix:
            possibilities.extend(f"{candidate}{suffix}" for suffix in suffixes)
        possibilities.extend(f"{candidate}/index{suffix}" for suffix in suffixes)
        for path in possibilities:
            if path in self._paths:
                return path, ""
        return None, f"no captured module matches {candidate!r}"

    def _load_ts_configs(self) -> tuple[_TsConfig, ...]:
        configs: list[_TsConfig] = []
        for path, content in sorted(self._bundle.contents.items()):
            if PurePosixPath(path).name not in {"jsconfig.json", "tsconfig.json"}:
                continue
            try:
                raw: object = json.loads(content)
            except json.JSONDecodeError, UnicodeDecodeError:
                continue
            compiler_options = _object_dict(_object_dict(raw).get("compilerOptions"))
            base_url_value = compiler_options.get("baseUrl", "")
            base_url = base_url_value if isinstance(base_url_value, str) else ""
            paths: list[tuple[str, tuple[str, ...]]] = []
            for pattern, replacements_value in sorted(
                _object_dict(compiler_options.get("paths")).items()
            ):
                if not _is_object_list(replacements_value):
                    continue
                replacements = tuple(
                    replacement
                    for replacement in replacements_value
                    if isinstance(replacement, str)
                )
                if replacements:
                    paths.append((pattern, replacements))
            configs.append(
                _TsConfig(
                    directory=_directory(path),
                    base_url=base_url,
                    paths=tuple(paths),
                )
            )
        return tuple(
            sorted(
                configs,
                key=lambda config: (-len(PurePosixPath(config.directory).parts), config.directory),
            )
        )

    def _applicable_ts_configs(self, source_path: str) -> tuple[_TsConfig, ...]:
        return tuple(
            config
            for config in self._ts_configs
            if not config.directory or source_path.startswith(f"{config.directory}/")
        )

    def _load_packages(self) -> tuple[_Package, ...]:
        packages: list[_Package] = []
        for path, content in sorted(self._bundle.contents.items()):
            if PurePosixPath(path).name != "package.json":
                continue
            try:
                raw: object = json.loads(content)
            except json.JSONDecodeError, UnicodeDecodeError:
                continue
            manifest = _object_dict(raw)
            name = manifest.get("name")
            if not isinstance(name, str) or not name:
                continue
            default_value = manifest.get("module", manifest.get("main"))
            default_target = default_value if isinstance(default_value, str) else None
            packages.append(
                _Package(
                    directory=_directory(path),
                    name=name,
                    exports=manifest.get("exports"),
                    default_target=default_target,
                )
            )
        return tuple(sorted(packages, key=lambda package: (-len(package.name), package.name)))

    def _resolve_package(self, specifier: str) -> str | None:
        for package in self._packages:
            if specifier != package.name and not specifier.startswith(f"{package.name}/"):
                continue
            subpath = (
                "."
                if specifier == package.name
                else f"./{specifier.removeprefix(f'{package.name}/')}"
            )
            export_target = self._export_target(package.exports, subpath)
            if export_target is None and subpath == ".":
                export_target = package.default_target or "index"
            if export_target is None:
                export_target = subpath.removeprefix("./")
            candidate = _join_workspace(package.directory, export_target)
            target, _reason = self._resolved_file(candidate, _TYPESCRIPT_SUFFIXES)
            return target
        return None

    @classmethod
    def _export_target(cls, exports: object | None, subpath: str) -> str | None:
        if isinstance(exports, str):
            return exports if subpath == "." else None
        mapping = _object_dict(exports)
        if not mapping:
            return None
        if any(key.startswith(".") for key in mapping):
            return cls._conditional_target(mapping.get(subpath))
        return cls._conditional_target(exports) if subpath == "." else None

    @classmethod
    def _conditional_target(cls, value: object | None) -> str | None:
        if isinstance(value, str):
            return value
        if _is_object_list(value):
            for item in value:
                target = cls._conditional_target(item)
                if target is not None:
                    return target
            return None
        mapping = _object_dict(value)
        if not mapping:
            return None
        for condition in ("types", "import", "default", "require"):
            target = cls._conditional_target(mapping.get(condition))
            if target is not None:
                return target
        for condition in sorted(mapping):
            target = cls._conditional_target(mapping[condition])
            if target is not None:
                return target
        return None

    @staticmethod
    def _match_pattern(pattern: str, specifier: str) -> str | None:
        if "*" not in pattern:
            return "" if pattern == specifier else None
        prefix, suffix = pattern.split("*", 1)
        if not specifier.startswith(prefix) or not specifier.endswith(suffix):
            return None
        end = len(specifier) - len(suffix) if suffix else len(specifier)
        return specifier[len(prefix) : end]

    def _load_go_module(self) -> str | None:
        content = self._bundle.contents.get("go.mod")
        if content is None:
            return None
        try:
            lines = content.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return None
        for raw_line in lines:
            parts = raw_line.strip().split()
            if len(parts) == 2 and parts[0] == "module":
                return parts[1]
        return None

    @staticmethod
    def _unresolved(
        source_path: str,
        specifier: str,
        generation: SemanticGeneration,
        reason: str,
    ) -> ModuleResolution:
        return ModuleResolution(
            source_path=source_path,
            specifier=specifier,
            target_path=None,
            generation_fingerprint=generation.fingerprint,
            complete=False,
            omitted_reasons=(reason, *generation.verification_issues),
        )
