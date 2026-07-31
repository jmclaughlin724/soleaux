"""Versioned contracts for the managed ts-morph/native TypeScript worker."""

from __future__ import annotations

import typing

import pydantic

import soleaux.contracts.repository

TYPESCRIPT_PROTOCOL_VERSION = "soleaux.typescript/v1"
TS_MORPH_VERSION = "28.0.0"
TS_MORPH_TYPESCRIPT_VERSION = "6.0.2"
NATIVE_TYPESCRIPT_VERSION = "7.0.2"


class TypeScriptSource(pydantic.BaseModel):
    """One repository source supplied from the canonical capture buffer."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    digest: str = pydantic.Field(min_length=64, max_length=64)
    text: str

    @pydantic.model_validator(mode="after")
    def _digest_matches_text(self) -> typing.Self:
        if soleaux.contracts.repository.content_digest(self.text.encode("utf-8")) != self.digest:
            raise ValueError(f"source digest does not match text for {self.path!r}")
        return self


class TypeScriptPreviewRequest(pydantic.BaseModel):
    """Optional edits applied only to the request's disposable ts-morph project."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    organize_imports: bool = False
    format: bool = False
    rename_path: str | None = None
    rename_position: int | None = pydantic.Field(default=None, ge=0)
    new_name: str | None = None

    @pydantic.model_validator(mode="after")
    def _rename_is_atomic(self) -> typing.Self:
        rename_values = (self.rename_path, self.rename_position, self.new_name)
        if any(value is not None for value in rename_values) and not all(
            value is not None for value in rename_values
        ):
            raise ValueError("rename_path, rename_position, and new_name are required together")
        return self


class TypeScriptAnalysisRequest(pydantic.BaseModel):
    """A complete generation-bound project analysis request."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    protocol_version: typing.Literal["soleaux.typescript/v1"] = TYPESCRIPT_PROTOCOL_VERSION
    workspace_id: str = pydantic.Field(min_length=1)
    project_id: str = pydantic.Field(min_length=1)
    config_path: str | None = None
    root_files: tuple[str, ...] = ()
    package_roots: dict[str, str] = pydantic.Field(default_factory=dict[str, str])
    sources: tuple[TypeScriptSource, ...] = pydantic.Field(min_length=1)
    include_references: bool = False
    include_emit: bool = False
    preview: TypeScriptPreviewRequest | None = None
    max_facts: int = pydantic.Field(default=5000, ge=1, le=50_000)

    @pydantic.model_validator(mode="after")
    def _paths_are_unique_and_present(self) -> typing.Self:
        paths = [source.path for source in self.sources]
        if len(paths) != len(set(paths)):
            raise ValueError("TypeScript source paths must be unique")
        available = set(paths)
        if self.config_path is not None and self.config_path not in available:
            raise ValueError("config_path must identify a supplied source")
        missing_roots = set(self.root_files).difference(available)
        if missing_roots:
            raise ValueError(f"root_files are not supplied: {', '.join(sorted(missing_roots))}")
        for package_name, root_path in self.package_roots.items():
            if (
                not package_name
                or package_name.startswith((".", "/"))
                or "\\" in package_name
                or ".." in package_name.split("/")
                or root_path.startswith("/")
                or "\\" in root_path
                or ".." in root_path.split("/")
            ):
                raise ValueError("package_roots must map package names to repository paths")
        return self


class EngineIdentity(pydantic.BaseModel):
    """Separate package, loaded runtime, binary, API, and protocol identities."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    engine: str = pydantic.Field(min_length=1)
    package_name: str = pydantic.Field(min_length=1)
    package_version: str = pydantic.Field(min_length=1)
    runtime_version: str = pydantic.Field(min_length=1)
    api_entrypoint: str = pydantic.Field(min_length=1)
    binary_version: str | None = None
    protocol_version: typing.Literal["soleaux.typescript/v1"] = TYPESCRIPT_PROTOCOL_VERSION


class TypeScriptDiagnostic(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    engine: str = pydantic.Field(min_length=1)
    category: str = pydantic.Field(min_length=1)
    code: int | None = None
    message: str = pydantic.Field(min_length=1)
    path: str | None = None
    start: int | None = pydantic.Field(default=None, ge=0)
    length: int | None = pydantic.Field(default=None, ge=0)
    byte_start: int | None = pydantic.Field(default=None, ge=0)
    byte_end: int | None = pydantic.Field(default=None, ge=0)


class TypeScriptImport(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    specifier: str = pydantic.Field(min_length=1)
    is_type_only: bool = False
    usage: typing.Literal["direct_import", "dynamic_load"] = "direct_import"
    resolved_path: str | None = None


class TypeScriptLocation(pydantic.BaseModel):
    """One bounded, serializable semantic location."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    start: int = pydantic.Field(ge=0)
    end: int = pydantic.Field(ge=0)
    byte_start: int | None = pydantic.Field(default=None, ge=0)
    byte_end: int | None = pydantic.Field(default=None, ge=0)
    kind: str | None = None
    name: str | None = None


class TypeScriptCall(pydantic.BaseModel):
    """One call site resolved by a compiler engine."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    callee: str = pydantic.Field(min_length=1)
    start: int = pydantic.Field(ge=0)
    end: int = pydantic.Field(ge=0)
    byte_start: int | None = pydantic.Field(default=None, ge=0)
    byte_end: int | None = pydantic.Field(default=None, ge=0)
    signature_text: str | None = None
    return_type_text: str | None = None


class TypeScriptSymbol(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    name: str = pydantic.Field(min_length=1)
    kind: str = pydantic.Field(min_length=1)
    start: int = pydantic.Field(ge=0)
    end: int = pydantic.Field(ge=0)
    byte_start: int | None = pydantic.Field(default=None, ge=0)
    byte_end: int | None = pydantic.Field(default=None, ge=0)
    exported: bool = False
    type_text: str | None = None
    value_text: str | None = None
    documentation: str | None = None
    signatures: tuple[str, ...] = ()
    declarations: tuple[TypeScriptLocation, ...] = ()
    definitions: tuple[TypeScriptLocation, ...] = ()
    implementations: tuple[TypeScriptLocation, ...] = ()
    references: tuple[TypeScriptLocation, ...] = ()
    assignable_to_self: bool | None = None


class EmittedFile(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    text: str


class PreviewedFile(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    path: str = pydantic.Field(min_length=1)
    preimage_digest: str = pydantic.Field(min_length=64, max_length=64)
    postimage_digest: str = pydantic.Field(min_length=64, max_length=64)
    text: str


class EngineAnalysis(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    identity: EngineIdentity
    config_path: str | None = None
    root_files: tuple[str, ...] = ()
    compiler_options: dict[str, object] = pydantic.Field(default_factory=dict[str, object])
    imports: tuple[TypeScriptImport, ...] = ()
    symbols: tuple[TypeScriptSymbol, ...] = ()
    calls: tuple[TypeScriptCall, ...] = ()
    diagnostics: tuple[TypeScriptDiagnostic, ...] = ()
    emitted_files: tuple[EmittedFile, ...] = ()
    previewed_files: tuple[PreviewedFile, ...] = ()
    timing: dict[str, object] = pydantic.Field(default_factory=dict[str, object])
    cache: dict[str, object] = pydantic.Field(default_factory=dict[str, object])
    capability_evidence: dict[str, object] = pydantic.Field(default_factory=dict[str, object])
    coverage: tuple[str, ...] = ()


class TypeScriptParityDimension(pydantic.BaseModel):
    """One explicit TS6-versus-native comparison over normalized facts."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    status: typing.Literal["equal", "different"]
    ts_morph_digest: str = pydantic.Field(min_length=64, max_length=64)
    native_digest: str = pydantic.Field(min_length=64, max_length=64)
    ts_morph_count: int = pydantic.Field(ge=0)
    native_count: int = pydantic.Field(ge=0)


class TypeScriptParity(pydantic.BaseModel):
    """Config, roots, resolution, and diagnostics parity evidence."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    status: typing.Literal["equal", "different"]
    config: TypeScriptParityDimension
    roots: TypeScriptParityDimension
    resolution: TypeScriptParityDimension
    diagnostics: TypeScriptParityDimension


class TypeScriptAnalysis(pydantic.BaseModel):
    """Validated, transport-neutral result from both compiler engines."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    protocol_version: typing.Literal["soleaux.typescript/v1"] = TYPESCRIPT_PROTOCOL_VERSION
    workspace_id: str = pydantic.Field(min_length=1)
    project_id: str = pydantic.Field(min_length=1)
    ts_morph: EngineAnalysis
    native: EngineAnalysis
    capabilities: dict[str, bool]
    parity: TypeScriptParity
    warnings: tuple[str, ...] = ()
