"""Manifest-only project, dependency, script, and configuration extraction."""

from __future__ import annotations

import collections.abc
import json
import pathlib
import posixpath
import tomllib
import typing

import pydantic
import yaml

import soleaux.catalog.contracts
import soleaux.contracts.repository
import soleaux.frameworks.nextjs
import soleaux.structural.snapshot
from soleaux.catalog.contracts import DependencyScope

PROJECT_EXTRACTOR_ID = "soleaux-project-catalog"
PROJECT_EXTRACTOR_VERSION = "1"

_NODE_DEPENDENCY_SECTIONS: tuple[tuple[str, DependencyScope], ...] = (
    ("dependencies", DependencyScope.RUNTIME),
    ("devDependencies", DependencyScope.DEVELOPMENT),
    ("optionalDependencies", DependencyScope.OPTIONAL),
    ("peerDependencies", DependencyScope.PEER),
)
_CONFIG_NAMES = frozenset(
    {
        "jsconfig.json",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "tsconfig.json",
    }
)
_OBJECT_ADAPTER = pydantic.TypeAdapter(dict[str, object])
_OBJECT_SEQUENCE_ADAPTER = pydantic.TypeAdapter(list[object])


def _object(value: object) -> dict[str, object]:
    try:
        return _OBJECT_ADAPTER.validate_python(value)
    except pydantic.ValidationError:
        return {}


def _object_sequence(value: object) -> list[object]:
    try:
        return _OBJECT_SEQUENCE_ADAPTER.validate_python(value)
    except pydantic.ValidationError:
        return []


def _string_mapping(value: object) -> dict[str, str]:
    return {key: item for key, item in _object(value).items() if isinstance(item, str) and item}


def _directory(path: str) -> str:
    parent = pathlib.PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _project_id(
    workspace_id: str, kind: soleaux.catalog.contracts.ProjectKind, root_path: str
) -> str:
    return f"{workspace_id}:{kind.value}:{root_path or '.'}"


class _CatalogEvidence(typing.TypedDict):
    workspace_id: str
    source_path: str
    source_digest: str
    producer: str
    producer_version: str


def _evidence(bundle: soleaux.structural.snapshot.SnapshotBundle, path: str) -> _CatalogEvidence:
    captured = next(item for item in bundle.snapshot.files if item.path == path)
    return {
        "workspace_id": bundle.snapshot.workspace_id,
        "source_path": path,
        "source_digest": captured.content_hash,
        "producer": PROJECT_EXTRACTOR_ID,
        "producer_version": PROJECT_EXTRACTOR_VERSION,
    }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


class CatalogResolution:
    """Exact pnpm catalog name-to-specifier projection."""

    def __init__(
        self,
        default: collections.abc.Mapping[str, str],
        named: collections.abc.Mapping[str, collections.abc.Mapping[str, str]],
    ) -> None:
        self._default = dict(default)
        self._named = {name: dict(values) for name, values in named.items()}

    @classmethod
    def from_bundle(cls, bundle: soleaux.structural.snapshot.SnapshotBundle) -> CatalogResolution:
        content = bundle.contents.get("pnpm-workspace.yaml")
        if content is None:
            return cls({}, {})
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            return cls({}, {})
        root = _object(parsed)
        default = _string_mapping(root.get("catalog"))
        named = {
            name: _string_mapping(values) for name, values in _object(root.get("catalogs")).items()
        }
        return cls(default, named)

    def resolve(self, package_name: str, specifier: str) -> str | None:
        if specifier == "catalog:":
            return self._default.get(package_name)
        if specifier.startswith("catalog:"):
            catalog_name = specifier.removeprefix("catalog:")
            return self._named.get(catalog_name, {}).get(package_name)
        return specifier


class ProjectCatalogExtractor:
    """Extract cheap project facts without reading beyond one snapshot bundle."""

    def extract(
        self, bundle: soleaux.structural.snapshot.SnapshotBundle
    ) -> soleaux.catalog.contracts.CatalogFacts:
        catalog = CatalogResolution.from_bundle(bundle)
        projects: list[soleaux.catalog.contracts.ProjectFact] = []
        dependencies: list[soleaux.catalog.contracts.DependencyFact] = []
        scripts: list[soleaux.catalog.contracts.ScriptFact] = []
        configs: list[soleaux.catalog.contracts.ConfigFact] = []
        warnings: list[str] = []

        for path in sorted(bundle.contents):
            name = pathlib.PurePosixPath(path).name
            if name == "package.json":
                try:
                    project, project_dependencies, project_scripts = self._node_project(
                        bundle,
                        path,
                        catalog,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    warnings.append(f"{path}: {exc}")
                    continue
                projects.append(project)
                dependencies.extend(project_dependencies)
                scripts.extend(project_scripts)
            elif name == "pyproject.toml":
                try:
                    project, project_dependencies = self._python_project(bundle, path)
                except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
                    warnings.append(f"{path}: {exc}")
                    continue
                projects.append(project)
                dependencies.extend(project_dependencies)

        tasks, task_warnings = self._turbo_tasks(bundle, tuple(projects))
        warnings.extend(task_warnings)
        known_task_ids = self._task_ids_by_project(tuple(tasks))
        scripts = [
            script.model_copy(update={"task_ids": self._turbo_task_ids(script, known_task_ids)})
            for script in scripts
        ]

        all_paths = frozenset(bundle.contents)
        for project in projects:
            configs.extend(self._project_configs(bundle, project, all_paths))
        engines, typescript_routes = self._typescript_routes(
            bundle,
            tuple(projects),
            tuple(dependencies),
            tuple(scripts),
            tuple(configs),
        )
        routes, route_warnings = self._framework_routes(bundle, tuple(projects))
        rules, rule_warnings = self._structural_rules(bundle)
        warnings.extend(route_warnings)
        warnings.extend(rule_warnings)

        return soleaux.catalog.contracts.CatalogFacts(
            projects=tuple(sorted(projects, key=lambda item: item.project_id)),
            dependencies=tuple(
                sorted(
                    dependencies,
                    key=lambda item: (item.project_id, item.scope, item.package_name),
                )
            ),
            scripts=tuple(sorted(scripts, key=lambda item: (item.project_id, item.name))),
            tasks=tuple(
                sorted(tasks, key=lambda item: (item.project_id, item.runner, item.task_id))
            ),
            configs=tuple(sorted(configs, key=lambda item: (item.project_id, item.config_path))),
            engines=engines,
            typescript_routes=typescript_routes,
            routes=routes,
            rules=rules,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _framework_routes(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    ) -> tuple[tuple[soleaux.catalog.contracts.RouteFact, ...], tuple[str, ...]]:
        registrations, notes = soleaux.frameworks.nextjs.NextDetector().enumerate(
            tuple(item.path for item in bundle.snapshot.files),
            bundle.contents,
        )
        projects_by_root = {
            project.root_path: project
            for project in projects
            if project.kind is soleaux.catalog.contracts.ProjectKind.NODE
        }
        routes: list[soleaux.catalog.contracts.RouteFact] = []
        for registration in registrations:
            captured = next(
                (item for item in bundle.snapshot.files if item.path == registration.path),
                None,
            )
            if captured is None:
                continue
            project = projects_by_root.get(registration.project_dir)
            identity = json.dumps(
                {
                    "workspace_id": bundle.snapshot.workspace_id,
                    "project_id": project.project_id if project is not None else None,
                    "path": registration.path,
                    "kind": registration.kind.value,
                    "route": registration.route,
                    "router": (
                        registration.router.value if registration.router is not None else None
                    ),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            routes.append(
                soleaux.catalog.contracts.RouteFact(
                    workspace_id=bundle.snapshot.workspace_id,
                    source_path=registration.path,
                    source_digest=captured.content_hash,
                    producer="soleaux-frameworks-nextjs",
                    producer_version="1",
                    route_id=soleaux.contracts.repository.content_digest(identity),
                    project_id=project.project_id if project is not None else None,
                    framework=registration.framework,
                    route=registration.route,
                    registration_kind=registration.kind.value,
                    router=(registration.router.value if registration.router is not None else None),
                    confidence=registration.confidence,
                    complete=not registration.note,
                    omitted_reasons=(registration.note,) if registration.note else (),
                )
            )
        return (
            tuple(
                sorted(
                    routes,
                    key=lambda route: (
                        route.project_id or "",
                        route.route or "",
                        route.source_path,
                    ),
                )
            ),
            tuple(f"framework.registrations: {note}" for note in notes),
        )

    @staticmethod
    def _structural_rules(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
    ) -> tuple[tuple[soleaux.catalog.contracts.RuleFact, ...], tuple[str, ...]]:
        config_path = "sgconfig.yml"
        config_content = bundle.contents.get(config_path)
        if config_content is None:
            return (), ()
        try:
            config = _object(yaml.safe_load(config_content))
        except yaml.YAMLError as exc:
            return (), (f"{config_path}: {exc}",)
        raw_directories = config.get("ruleDirs")
        directory_values = _object_sequence(raw_directories)
        if not isinstance(raw_directories, list):
            return (), (f"{config_path}: ruleDirs is not a list",)
        directories = tuple(
            str(value).rstrip("/")
            for value in directory_values
            if isinstance(value, str)
            and value
            and not value.startswith(("/", "."))
            and ".." not in pathlib.PurePosixPath(value).parts
        )
        config_hash = soleaux.contracts.repository.content_digest(config_content)
        rules: list[soleaux.catalog.contracts.RuleFact] = []
        warnings: list[str] = []
        for path, content in sorted(bundle.contents.items()):
            if not path.endswith((".yml", ".yaml")) or not any(
                path.startswith(f"{directory}/") for directory in directories
            ):
                continue
            try:
                raw_rule = _object(yaml.safe_load(content))
            except yaml.YAMLError as exc:
                warnings.append(f"{path}: {exc}")
                continue
            rule_id = raw_rule.get("id")
            language = raw_rule.get("language")
            if not isinstance(rule_id, str) or not isinstance(language, str):
                warnings.append(f"{path}: structural rule is missing id or language")
                continue
            rules.append(
                soleaux.catalog.contracts.RuleFact(
                    **_evidence(bundle, path),
                    rule_id=rule_id,
                    language=language,
                    severity=str(raw_rule.get("severity", "hint")),
                    message=str(raw_rule.get("message", rule_id)),
                    note=str(raw_rule.get("note", "")),
                    rule_digest=soleaux.contracts.repository.content_digest(content),
                    config_digest=config_hash,
                    file_globs=_string_sequence(raw_rule.get("files")),
                    ignore_globs=_string_sequence(raw_rule.get("ignores")),
                )
            )
        return tuple(sorted(rules, key=lambda rule: rule.rule_id)), tuple(warnings)

    def _typescript_routes(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
        dependencies: tuple[soleaux.catalog.contracts.DependencyFact, ...],
        scripts: tuple[soleaux.catalog.contracts.ScriptFact, ...],
        configs: tuple[soleaux.catalog.contracts.ConfigFact, ...],
    ) -> tuple[
        tuple[soleaux.catalog.contracts.EngineFact, ...],
        tuple[soleaux.catalog.contracts.TypeScriptRouteFact, ...],
    ]:
        """Record declared routes without claiming that any engine was loaded."""
        engines: list[soleaux.catalog.contracts.EngineFact] = []
        routes: list[soleaux.catalog.contracts.TypeScriptRouteFact] = []
        for project in projects:
            if project.kind is not soleaux.catalog.contracts.ProjectKind.NODE:
                continue
            project_dependencies = tuple(
                dependency
                for dependency in dependencies
                if dependency.project_id == project.project_id
                and dependency.usage is soleaux.catalog.contracts.DependencyUsage.DECLARED
            )
            project_configs = tuple(
                config
                for config in configs
                if config.project_id == project.project_id and config.config_kind == "typescript"
            )
            has_typescript_source = any(
                self._project_for_path(projects, path, soleaux.catalog.contracts.ProjectKind.NODE)
                == project
                and path.endswith((".ts", ".tsx", ".mts", ".cts", ".js", ".jsx"))
                for path in bundle.contents
            )
            if not project_configs and not has_typescript_source:
                continue

            evidence = _evidence(bundle, project.manifest_path)
            engine_ids: dict[str, str] = {}
            for dependency in project_dependencies:
                if dependency.package_name not in {
                    "ts-morph",
                    "typescript",
                    "@typescript/native",
                    "typescript-language-server",
                }:
                    continue
                role = (
                    soleaux.catalog.contracts.EngineRole.LSP
                    if dependency.package_name == "typescript-language-server"
                    else soleaux.catalog.contracts.EngineRole.PACKAGE
                )
                engine_id = f"{role.value}:{project.project_id}:{dependency.package_name}"
                engine_ids[dependency.package_name] = engine_id
                engines.append(
                    soleaux.catalog.contracts.EngineFact(
                        **evidence,
                        project_id=project.project_id,
                        engine_id=engine_id,
                        role=role,
                        package_name=dependency.package_name,
                        package_version=(
                            dependency.resolved_specifier or dependency.declared_specifier
                        ),
                        available=False,
                        coverage="declared",
                        omitted_reasons=("runtime identity not loaded",),
                    )
                )

            typecheck = next(
                (
                    script
                    for script in scripts
                    if script.project_id == project.project_id and script.is_typecheck
                ),
                None,
            )
            typecheck_engine_id: str | None = None
            if typecheck is not None:
                typecheck_engine_id = f"typecheck:{project.project_id}:{typecheck.name}"
                engines.append(
                    soleaux.catalog.contracts.EngineFact(
                        **evidence,
                        project_id=project.project_id,
                        engine_id=typecheck_engine_id,
                        role=soleaux.catalog.contracts.EngineRole.TYPECHECK,
                        command=typecheck.command,
                        available=True,
                        coverage="declared_command",
                    )
                )

            config = project_configs[0] if project_configs else None
            route_evidence = _evidence(
                bundle,
                config.config_path if config is not None else project.manifest_path,
            )
            routes.append(
                soleaux.catalog.contracts.TypeScriptRouteFact(
                    **route_evidence,
                    project_id=project.project_id,
                    config_path=config.config_path if config is not None else None,
                    config_closure=config.closure_paths if config is not None else (),
                    ambient_types=(
                        ("node",)
                        if any(
                            dependency.package_name == "@types/node"
                            for dependency in project_dependencies
                        )
                        else ()
                    ),
                    ts_morph_engine_id=engine_ids.get("ts-morph"),
                    native_engine_id=engine_ids.get("@typescript/native"),
                    lsp_engine_id=engine_ids.get("typescript-language-server"),
                    typecheck_engine_id=typecheck_engine_id,
                    typecheck_script=typecheck.name if typecheck is not None else None,
                    typecheck_command=typecheck.command if typecheck is not None else None,
                    prerequisites=typecheck.prerequisites if typecheck is not None else (),
                    parity_status="not_run",
                    complete=False,
                    omitted_reasons=("managed TypeScript engines not loaded",),
                )
            )
        return (
            tuple(sorted(engines, key=lambda item: (item.project_id, item.engine_id))),
            tuple(sorted(routes, key=lambda item: item.project_id)),
        )

    @staticmethod
    def _project_for_path(
        projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
        path: str,
        kind: soleaux.catalog.contracts.ProjectKind,
    ) -> soleaux.catalog.contracts.ProjectFact | None:
        candidates = [
            project
            for project in projects
            if project.kind is kind
            and (not project.root_path or path.startswith(f"{project.root_path.rstrip('/')}/"))
        ]
        return max(candidates, key=lambda project: len(project.root_path), default=None)

    def _node_project(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        path: str,
        catalog: CatalogResolution,
    ) -> tuple[
        soleaux.catalog.contracts.ProjectFact,
        list[soleaux.catalog.contracts.DependencyFact],
        list[soleaux.catalog.contracts.ScriptFact],
    ]:
        raw: object = json.loads(bundle.contents[path])
        manifest = _object(raw)
        root_path = _directory(path)
        project_id = _project_id(
            bundle.snapshot.workspace_id,
            soleaux.catalog.contracts.ProjectKind.NODE,
            root_path,
        )
        dependency_maps = {
            section: _string_mapping(manifest.get(section))
            for section, _scope in _NODE_DEPENDENCY_SECTIONS
        }
        package_names = {
            package_name for values in dependency_maps.values() for package_name in values
        }
        frameworks: list[str] = []
        if "next" in package_names:
            frameworks.append("nextjs")
        if "react" in package_names:
            frameworks.append("react")
        evidence = _evidence(bundle, path)
        project = soleaux.catalog.contracts.ProjectFact(
            **evidence,
            project_id=project_id,
            root_path=root_path,
            manifest_path=path,
            kind=soleaux.catalog.contracts.ProjectKind.NODE,
            name=_optional_string(manifest.get("name")),
            version=_optional_string(manifest.get("version")),
            private=_optional_bool(manifest.get("private")),
            framework_ids=tuple(frameworks),
        )
        dependencies = [
            soleaux.catalog.contracts.DependencyFact(
                **evidence,
                project_id=project_id,
                package_name=package_name,
                declared_specifier=specifier,
                resolved_specifier=catalog.resolve(package_name, specifier),
                scope=scope,
            )
            for section, scope in _NODE_DEPENDENCY_SECTIONS
            for package_name, specifier in dependency_maps[section].items()
        ]
        scripts = [
            soleaux.catalog.contracts.ScriptFact(
                **evidence,
                project_id=project_id,
                name=name,
                command=command,
                is_typecheck=name in {"typecheck", "type-check", "check:types"},
                prerequisites=self._script_prerequisites(command),
            )
            for name, command in _string_mapping(manifest.get("scripts")).items()
        ]
        return project, dependencies, scripts

    def _python_project(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        path: str,
    ) -> tuple[
        soleaux.catalog.contracts.ProjectFact, list[soleaux.catalog.contracts.DependencyFact]
    ]:
        manifest = tomllib.loads(bundle.contents[path].decode("utf-8"))
        project_table = _object(manifest.get("project"))
        root_path = _directory(path)
        project_id = _project_id(
            bundle.snapshot.workspace_id,
            soleaux.catalog.contracts.ProjectKind.PYTHON,
            root_path,
        )
        evidence = _evidence(bundle, path)
        project = soleaux.catalog.contracts.ProjectFact(
            **evidence,
            project_id=project_id,
            root_path=root_path,
            manifest_path=path,
            kind=soleaux.catalog.contracts.ProjectKind.PYTHON,
            name=_optional_string(project_table.get("name")),
            version=_optional_string(project_table.get("version")),
        )
        dependencies: list[soleaux.catalog.contracts.DependencyFact] = []
        raw_dependencies = project_table.get("dependencies")
        dependency_values = _object_sequence(raw_dependencies)
        if isinstance(raw_dependencies, list):
            for dependency in dependency_values:
                if not isinstance(dependency, str) or not dependency:
                    continue
                package_name = dependency.split("[", 1)[0]
                for marker in ("<", ">", "=", "!", "~", ";", " "):
                    package_name = package_name.split(marker, 1)[0]
                dependencies.append(
                    soleaux.catalog.contracts.DependencyFact(
                        **evidence,
                        project_id=project_id,
                        package_name=package_name,
                        declared_specifier=dependency,
                        resolved_specifier=dependency,
                        scope=soleaux.catalog.contracts.DependencyScope.RUNTIME,
                    )
                )
        return project, dependencies

    @staticmethod
    def _script_prerequisites(command: str) -> tuple[str, ...]:
        prerequisites: list[str] = []
        if "next typegen" in command:
            prerequisites.append("next typegen")
        return tuple(prerequisites)

    @staticmethod
    def _turbo_tasks(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        projects: tuple[soleaux.catalog.contracts.ProjectFact, ...],
    ) -> tuple[list[soleaux.catalog.contracts.TaskFact], list[str]]:
        node_projects_by_root = {
            project.root_path: project
            for project in projects
            if project.kind is soleaux.catalog.contracts.ProjectKind.NODE
        }
        tasks: list[soleaux.catalog.contracts.TaskFact] = []
        warnings: list[str] = []
        for path in sorted(bundle.contents):
            if pathlib.PurePosixPath(path).name != "turbo.json":
                continue
            owner = node_projects_by_root.get(_directory(path))
            if owner is None:
                warnings.append(f"{path}: no owning package manifest")
                continue
            try:
                parsed: object = json.loads(bundle.contents[path])
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                warnings.append(f"{path}: {exc}")
                continue
            configuration = _object(parsed)
            declared = _object(configuration.get("tasks")) or _object(configuration.get("pipeline"))
            evidence = _evidence(bundle, path)
            for task_id, raw_specification in declared.items():
                specification = _object(raw_specification)
                tasks.append(
                    soleaux.catalog.contracts.TaskFact(
                        **evidence,
                        project_id=owner.project_id,
                        runner="turbo",
                        task_id=task_id,
                        depends_on=_string_sequence(specification.get("dependsOn")),
                        outputs=_string_sequence(specification.get("outputs")),
                        inputs=_string_sequence(specification.get("inputs")),
                        cache=_optional_bool(specification.get("cache")),
                        persistent=specification.get("persistent") is True,
                        extends_root=_directory(path) != "",
                    )
                )
        return tasks, warnings

    @staticmethod
    def _task_ids_by_project(
        tasks: tuple[soleaux.catalog.contracts.TaskFact, ...],
    ) -> dict[str, frozenset[str]]:
        root_ids = frozenset(
            task.task_id for task in tasks if task.runner == "turbo" and not task.extends_root
        )
        by_project: dict[str, set[str]] = {}
        for task in tasks:
            if task.runner != "turbo":
                continue
            by_project.setdefault(task.project_id, set()).add(task.task_id)
        return {
            project_id: frozenset(task_ids | root_ids)
            for project_id, task_ids in by_project.items()
        } | {"": root_ids}

    @staticmethod
    def _turbo_task_ids(
        script: soleaux.catalog.contracts.ScriptFact,
        known_task_ids: dict[str, frozenset[str]],
    ) -> tuple[str, ...]:
        known = known_task_ids.get(script.project_id, known_task_ids.get("", frozenset()))
        if not known:
            return ()
        matched: set[str] = set()
        tokens = script.command.split()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token != "turbo" and not token.endswith("/turbo"):
                continue
            if index < len(tokens) and tokens[index] == "run":
                index += 1
            while index < len(tokens):
                candidate = tokens[index]
                if candidate.startswith("-") or candidate in {"&&", "||", ";", "|"}:
                    break
                index += 1
                bare = candidate.rpartition("#")[2]
                if candidate in known or bare in known:
                    matched.add(candidate)
        return tuple(sorted(matched))

    def _project_configs(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        project: soleaux.catalog.contracts.ProjectFact,
        all_paths: frozenset[str],
    ) -> list[soleaux.catalog.contracts.ConfigFact]:
        prefix = f"{project.root_path}/" if project.root_path else ""
        config_paths = tuple(
            path
            for path in sorted(all_paths)
            if path.startswith(prefix)
            and "/" not in path[len(prefix) :]
            and pathlib.PurePosixPath(path).name in _CONFIG_NAMES
        )
        configs: list[soleaux.catalog.contracts.ConfigFact] = []
        for path in config_paths:
            name = pathlib.PurePosixPath(path).name
            if name in {"tsconfig.json", "jsconfig.json"}:
                parser_id = "typescript:config"
                closure, complete, reasons = self._typescript_config_closure(
                    bundle,
                    path,
                    all_paths,
                )
                kind = "typescript"
            else:
                parser_id = "ts-morph:typescript"
                closure, complete, reasons = (path,), True, ()
                kind = "nextjs"
            configs.append(
                soleaux.catalog.contracts.ConfigFact(
                    **_evidence(bundle, path),
                    project_id=project.project_id,
                    config_path=path,
                    config_kind=kind,
                    parser_id=parser_id,
                    closure_paths=closure,
                    complete=complete,
                    omitted_reasons=reasons,
                )
            )
        return configs

    def _typescript_config_closure(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        path: str,
        all_paths: frozenset[str],
    ) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
        """Build a conservative JSON-only closure; the TS worker replaces it authoritatively."""
        try:
            parsed = _object(json.loads(bundle.contents[path]))
        except UnicodeDecodeError, json.JSONDecodeError:
            return (path,), False, ("requires TypeScript JSONC config parser",)
        closure = [path]
        reasons: list[str] = []
        extends = parsed.get("extends")
        if isinstance(extends, str):
            resolved = self._resolve_config_reference(path, extends, all_paths)
            if resolved is None:
                reasons.append(f"unresolved extends {extends!r}")
            else:
                closure.append(resolved)
        references = parsed.get("references")
        reference_values = _object_sequence(references)
        if isinstance(references, list):
            for reference in reference_values:
                reference_path = _object(reference).get("path")
                if not isinstance(reference_path, str):
                    continue
                resolved = self._resolve_config_reference(path, reference_path, all_paths)
                if resolved is None:
                    reasons.append(f"unresolved project reference {reference_path!r}")
                else:
                    closure.append(resolved)
        return tuple(dict.fromkeys(closure)), not reasons, tuple(reasons)

    @staticmethod
    def _resolve_config_reference(
        config_path: str,
        reference: str,
        all_paths: frozenset[str],
    ) -> str | None:
        if not reference.startswith("."):
            return None
        candidate = posixpath.normpath(posixpath.join(_directory(config_path), reference))
        possibilities = (candidate, f"{candidate}.json", f"{candidate}/tsconfig.json")
        return next((path for path in possibilities if path in all_paths), None)


def _string_sequence(value: object) -> tuple[str, ...]:
    return tuple(item for item in _object_sequence(value) if isinstance(item, str))
