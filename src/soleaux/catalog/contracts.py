"""Normalized public records for project and build-route catalog facts."""

from __future__ import annotations

import enum
import typing

import pydantic

CATALOG_SCHEMA_VERSION = "soleaux.catalog/v1"


class CatalogRecord(pydantic.BaseModel):
    """Base provenance shared by byte-bound catalog records."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    schema_version: typing.Literal["soleaux.catalog/v1"] = CATALOG_SCHEMA_VERSION
    workspace_id: str = pydantic.Field(min_length=1)
    source_path: str = pydantic.Field(min_length=1)
    source_digest: str = pydantic.Field(min_length=64, max_length=64)
    producer: str = pydantic.Field(min_length=1)
    producer_version: str = pydantic.Field(min_length=1)


class ProjectKind(enum.StrEnum):
    NODE = "node"
    PYTHON = "python"


class DependencyScope(enum.StrEnum):
    RUNTIME = "runtime"
    DEVELOPMENT = "development"
    OPTIONAL = "optional"
    PEER = "peer"
    BUILD = "build"


class DependencyUsage(enum.StrEnum):
    DECLARED = "declared"
    DIRECT_IMPORT = "direct_import"
    DYNAMIC_LOAD = "dynamic_load"
    MANAGED_RUNTIME = "managed_runtime"
    EXTERNAL_PROVIDER = "external_provider"
    TRANSITIVE = "transitive"
    UNRESOLVED = "unresolved"


class EngineRole(enum.StrEnum):
    PACKAGE = "package"
    LOADED_COMPILER = "loaded_compiler"
    API = "api"
    BINARY = "binary"
    LSP = "lsp"
    TYPECHECK = "typecheck"


class ProjectFact(CatalogRecord):
    """One package/project owner discovered from an authoritative manifest."""

    project_id: str = pydantic.Field(min_length=1)
    root_path: str
    manifest_path: str = pydantic.Field(min_length=1)
    kind: ProjectKind
    name: str | None = None
    version: str | None = None
    private: bool | None = None
    framework_ids: tuple[str, ...] = ()


class DependencyFact(CatalogRecord):
    """One dependency claim with declaration and resolved catalog identities."""

    project_id: str = pydantic.Field(min_length=1)
    package_name: str = pydantic.Field(min_length=1)
    declared_specifier: str = pydantic.Field(min_length=1)
    resolved_specifier: str | None = None
    scope: DependencyScope
    usage: DependencyUsage = DependencyUsage.DECLARED
    direct: bool = True


class ScriptFact(CatalogRecord):
    """One package-owned executable route."""

    project_id: str = pydantic.Field(min_length=1)
    name: str = pydantic.Field(min_length=1)
    command: str = pydantic.Field(min_length=1)
    is_typecheck: bool = False
    task_ids: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()


class TaskFact(CatalogRecord):
    """One task-runner task parsed from its authoritative configuration."""

    project_id: str = pydantic.Field(min_length=1)
    runner: str = pydantic.Field(min_length=1)
    task_id: str = pydantic.Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    cache: bool | None = None
    persistent: bool = False
    extends_root: bool = False


class ConfigFact(CatalogRecord):
    """One discovered configuration owner awaiting its authoritative parser."""

    project_id: str = pydantic.Field(min_length=1)
    config_path: str = pydantic.Field(min_length=1)
    config_kind: str = pydantic.Field(min_length=1)
    parser_id: str = pydantic.Field(min_length=1)
    closure_paths: tuple[str, ...]
    complete: bool
    omitted_reasons: tuple[str, ...] = ()


class EngineFact(CatalogRecord):
    """One package, loaded compiler, API, binary, LSP, or command identity."""

    project_id: str = pydantic.Field(min_length=1)
    engine_id: str = pydantic.Field(min_length=1)
    role: EngineRole
    package_name: str | None = None
    package_version: str | None = None
    runtime_version: str | None = None
    api_entrypoint: str | None = None
    binary_version: str | None = None
    protocol_version: str | None = None
    command: str | None = None
    process_id: int | None = pydantic.Field(default=None, ge=1)
    process_epoch: int | None = pydantic.Field(default=None, ge=0)
    reported_name: str | None = None
    capabilities: tuple[str, ...] = ()
    available: bool
    coverage: str = pydantic.Field(min_length=1)
    omitted_reasons: tuple[str, ...] = ()


class TypeScriptRouteFact(CatalogRecord):
    """Effective TypeScript project/compiler/LSP/typecheck routing."""

    project_id: str = pydantic.Field(min_length=1)
    config_path: str | None = None
    root_files: tuple[str, ...] = ()
    config_closure: tuple[str, ...] = ()
    libraries: tuple[str, ...] = ()
    ambient_types: tuple[str, ...] = ()
    module_resolution: str | None = None
    ts_morph_engine_id: str | None = None
    native_engine_id: str | None = None
    lsp_engine_id: str | None = None
    typecheck_engine_id: str | None = None
    typecheck_script: str | None = None
    typecheck_command: str | None = None
    prerequisites: tuple[str, ...] = ()
    parity_status: str = pydantic.Field(min_length=1)
    parity_config_status: str = pydantic.Field(default="not_run", min_length=1)
    parity_roots_status: str = pydantic.Field(default="not_run", min_length=1)
    parity_resolution_status: str = pydantic.Field(default="not_run", min_length=1)
    parity_diagnostics_status: str = pydantic.Field(default="not_run", min_length=1)
    complete: bool
    omitted_reasons: tuple[str, ...] = ()


class ChunkFact(CatalogRecord):
    """One deterministic bounded retrieval unit."""

    chunk_id: str = pydantic.Field(min_length=64, max_length=64)
    path: str = pydantic.Field(min_length=1)
    language_id: str | None = None
    chunk_kind: str = pydantic.Field(min_length=1)
    start_line: int = pydantic.Field(ge=1)
    end_line: int = pydantic.Field(ge=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    text: str


class RouteFact(CatalogRecord):
    """One framework registration discovered from repository conventions."""

    route_id: str = pydantic.Field(min_length=64, max_length=64)
    project_id: str | None = None
    framework: str = pydantic.Field(min_length=1)
    route: str | None = None
    registration_kind: str = pydantic.Field(min_length=1)
    router: str | None = None
    methods: tuple[str, ...] = ()
    runtime: str | None = None
    confidence: float = pydantic.Field(ge=0, le=1)
    complete: bool
    omitted_reasons: tuple[str, ...] = ()


class RuleFact(CatalogRecord):
    """One canonical structural-policy rule identity and applicability contract."""

    rule_id: str = pydantic.Field(min_length=1)
    language: str = pydantic.Field(min_length=1)
    severity: str = pydantic.Field(min_length=1)
    message: str = pydantic.Field(min_length=1)
    note: str = ""
    rule_digest: str = pydantic.Field(min_length=64, max_length=64)
    config_digest: str = pydantic.Field(min_length=64, max_length=64)
    file_globs: tuple[str, ...] = ()
    ignore_globs: tuple[str, ...] = ()


class PolicyFact(CatalogRecord):
    """One configured-source governance record promoted at catalog build time."""

    policy_id: str = pydantic.Field(min_length=1)
    title: str = pydantic.Field(min_length=1)
    governance_source_id: str = pydantic.Field(min_length=1)
    identity_field: str = pydantic.Field(min_length=1)
    source_line: int = pydantic.Field(ge=1)
    attributes: dict[str, str] = pydantic.Field(default_factory=dict)


class SemanticLocation(pydantic.BaseModel):
    """One compiler-resolved repository location without an AST handle."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    kind: str | None = None
    name: str | None = None


class SemanticCallSite(SemanticLocation):
    """One resolved call site related to a symbol."""

    callee: str = pydantic.Field(min_length=1)
    signature_text: str | None = None
    return_type_text: str | None = None


class SymbolFact(CatalogRecord):
    """One logical symbol revision produced by an explicit semantic engine."""

    symbol_id: str = pydantic.Field(min_length=64, max_length=64)
    revision_id: str = pydantic.Field(min_length=64, max_length=64)
    project_id: str
    path: str = pydantic.Field(min_length=1)
    name: str = pydantic.Field(min_length=1)
    symbol_kind: str = pydantic.Field(min_length=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    exported: bool = False
    type_text: str | None = None
    documentation: str | None = None
    signatures: tuple[str, ...] = ()
    declarations: tuple[SemanticLocation, ...] = ()
    definitions: tuple[SemanticLocation, ...] = ()
    implementations: tuple[SemanticLocation, ...] = ()
    references: tuple[SemanticLocation, ...] = ()
    calls: tuple[SemanticCallSite, ...] = ()
    assignable_to_self: bool | None = None
    engine_id: str = pydantic.Field(min_length=1)
    coverage: str = pydantic.Field(min_length=1)


class ImportFact(CatalogRecord):
    """One source import or dynamic-load edge."""

    import_id: str = pydantic.Field(min_length=64, max_length=64)
    project_id: str
    path: str = pydantic.Field(min_length=1)
    specifier: str = pydantic.Field(min_length=1)
    resolved_path: str | None = None
    usage: DependencyUsage
    is_type_only: bool = False
    engine_id: str = pydantic.Field(min_length=1)


class DiagnosticFact(CatalogRecord):
    """One generation-bound compiler or language-server diagnostic."""

    diagnostic_id: str = pydantic.Field(min_length=64, max_length=64)
    project_id: str
    path: str = pydantic.Field(min_length=1)
    engine_id: str = pydantic.Field(min_length=1)
    category: str = pydantic.Field(min_length=1)
    code: str | None = None
    message: str = pydantic.Field(min_length=1)
    byte_start: int = pydantic.Field(ge=0)
    byte_end: int = pydantic.Field(ge=0)
    coverage: str = pydantic.Field(min_length=1)


class ChangeFact(CatalogRecord):
    """One added, changed, or deleted path in the latest bounded generation delta."""

    change_id: str = pydantic.Field(min_length=64, max_length=64)
    generation: int = pydantic.Field(ge=1)
    path: str = pydantic.Field(min_length=1)
    operation: typing.Literal["added", "changed", "deleted"]
    previous_digest: str | None = pydantic.Field(default=None, min_length=64, max_length=64)
    current_digest: str | None = pydantic.Field(default=None, min_length=64, max_length=64)


class CatalogFacts(pydantic.BaseModel):
    """Immutable normalized facts emitted outside catalog transactions."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    projects: tuple[ProjectFact, ...] = ()
    dependencies: tuple[DependencyFact, ...] = ()
    scripts: tuple[ScriptFact, ...] = ()
    tasks: tuple[TaskFact, ...] = ()
    configs: tuple[ConfigFact, ...] = ()
    engines: tuple[EngineFact, ...] = ()
    typescript_routes: tuple[TypeScriptRouteFact, ...] = ()
    routes: tuple[RouteFact, ...] = ()
    rules: tuple[RuleFact, ...] = ()
    policies: tuple[PolicyFact, ...] = ()
    symbols: tuple[SymbolFact, ...] = ()
    imports: tuple[ImportFact, ...] = ()
    diagnostics: tuple[DiagnosticFact, ...] = ()
    changes: tuple[ChangeFact, ...] = ()
    chunks: tuple[ChunkFact, ...] = ()
    warnings: tuple[str, ...] = ()
