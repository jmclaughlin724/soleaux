"""LSP broker contracts: specs, capabilities, positions, and session types.

Package-owned contracts that never let provider-library types cross the
boundary. No lsp-client or lsprotocol runtime dependency (D031).
"""

from __future__ import annotations

import enum
import typing

import pydantic

import soleaux.contracts.requests
from soleaux.contracts.requests import SemanticOperation

_OBJECT_MAPPING_ADAPTER = pydantic.TypeAdapter(dict[str, object])


class TextDocumentSyncKind(enum.StrEnum):
    """LSP text document synchronization mode."""

    NONE = "none"
    FULL = "full"
    INCREMENTAL = "incremental"


class NavigationRequest(pydantic.BaseModel):
    """One bounded internal navigation target consumed by the semantic resolver."""

    model_config = pydantic.ConfigDict(extra="forbid")

    operation: SemanticOperation
    workspace_id: str | None = None
    semantic_mode: soleaux.contracts.requests.SemanticMode = (
        soleaux.contracts.requests.SemanticMode.BEST_AVAILABLE
    )
    path: str | None = pydantic.Field(default=None, min_length=1)
    line: int | None = pydantic.Field(default=None, ge=1)
    column: int | None = pydantic.Field(default=None, ge=1)
    symbol_name: str | None = pydantic.Field(default=None, min_length=1, max_length=512)
    symbol_kind: str | None = pydantic.Field(default=None, min_length=1, max_length=64)
    limit: int = pydantic.Field(default=50, ge=1, le=200)

    @pydantic.model_validator(mode="after")
    def _validate_target(self) -> typing.Self:
        if self.symbol_name is not None:
            if self.line is not None or self.column is not None:
                raise ValueError("name navigation accepts no position")
            return self
        if self.symbol_kind is not None:
            raise ValueError("symbol_kind requires symbol_name")
        if self.path is None or self.line is None or self.column is None:
            raise ValueError("position navigation requires path, line, and column")
        return self


class EditorOperation(enum.StrEnum):
    """The four editor preview/apply operations."""

    RENAME = "rename"
    FORMAT_DOCUMENT = "format_document"
    FORMAT_RANGE = "format_range"
    CODE_ACTION = "code_action"


class LspCapability(enum.StrEnum):
    """The 17 CCLSP capabilities mapped to Soleaux operations."""

    DEFINITION = "definition"
    IMPLEMENTATION = "implementation"
    REFERENCES = "references"
    WORKSPACE_SYMBOL = "workspace_symbol"
    FORMAT_DOCUMENT = "format_document"
    FORMAT_RANGE = "format_range"
    CODE_ACTIONS = "code_actions"
    COMPLETION = "completion"
    DIAGNOSTICS = "diagnostics"
    HOVER = "hover"
    INCOMING_CALLS = "incoming_calls"
    OUTGOING_CALLS = "outgoing_calls"
    SIGNATURE_HELP = "signature_help"
    PREPARE_CALL_HIERARCHY = "prepare_call_hierarchy"
    RENAME = "rename"
    RENAME_STRICT = "rename_strict"
    RESTART = "restart"


CAPABILITY_LSP_METHOD: dict[LspCapability, str] = {
    LspCapability.DEFINITION: "textDocument/definition",
    LspCapability.IMPLEMENTATION: "textDocument/implementation",
    LspCapability.REFERENCES: "textDocument/references",
    LspCapability.WORKSPACE_SYMBOL: "workspace/symbol",
    LspCapability.FORMAT_DOCUMENT: "textDocument/formatting",
    LspCapability.FORMAT_RANGE: "textDocument/rangeFormatting",
    LspCapability.CODE_ACTIONS: "textDocument/codeAction",
    LspCapability.COMPLETION: "textDocument/completion",
    LspCapability.DIAGNOSTICS: "textDocument/diagnostic",
    LspCapability.HOVER: "textDocument/hover",
    LspCapability.INCOMING_CALLS: "callHierarchy/incomingCalls",
    LspCapability.OUTGOING_CALLS: "callHierarchy/outgoingCalls",
    LspCapability.SIGNATURE_HELP: "textDocument/signatureHelp",
    LspCapability.PREPARE_CALL_HIERARCHY: "textDocument/prepareCallHierarchy",
    LspCapability.RENAME: "textDocument/rename",
    LspCapability.RENAME_STRICT: "textDocument/rename",
    LspCapability.RESTART: "soleaux/restart",
}


class LspPosition(pydantic.BaseModel):
    """Zero-based LSP line/character position."""

    model_config = pydantic.ConfigDict(extra="forbid")

    line: int = pydantic.Field(ge=0)
    character: int = pydantic.Field(ge=0)


class LspRange(pydantic.BaseModel):
    """Half-open LSP range."""

    model_config = pydantic.ConfigDict(extra="forbid")

    start: LspPosition
    end: LspPosition


class LspLocation(pydantic.BaseModel):
    """LSP location: URI plus range."""

    model_config = pydantic.ConfigDict(extra="forbid")

    uri: str
    range: LspRange


class EditorSessionContext(pydantic.BaseModel):
    """Provider state needed to validate one WorkspaceEdit."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = pydantic.Field(min_length=1)
    provider_name: str = pydantic.Field(min_length=1)
    provider_config_digest: str = pydantic.Field(min_length=1)
    project_id: str = pydantic.Field(min_length=1)
    project_root: str
    project_config_digest: str = pydantic.Field(min_length=64, max_length=64)
    compiler_identity: str = pydantic.Field(min_length=1)
    process_epoch: int = pydantic.Field(ge=0)
    position_encoding: str = pydantic.Field(min_length=1)
    document_versions: dict[str, int]


class RestartStatus(enum.StrEnum):
    """Result of one selected lazy provider restart."""

    RESTARTED = "restarted"
    NOT_RUNNING = "not-running"
    UNAVAILABLE = "unavailable"


class RestartSessionResult(pydantic.BaseModel):
    """One provider's old/new lazy process state."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    provider_name: str = pydantic.Field(min_length=1)
    status: RestartStatus
    old_epoch: int = pydantic.Field(ge=0)
    new_epoch: int = pydantic.Field(ge=0)
    old_pid: int | None = pydantic.Field(default=None, ge=1)
    new_pid: int | None = pydantic.Field(default=None, ge=1)
    reason: str | None = None


class RestartResult(pydantic.BaseModel):
    """Selected restart result without eager provider recreation."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    sessions: tuple[RestartSessionResult, ...]
    restarted_sessions: int = pydantic.Field(ge=0)


class LanguageServerSpec(pydantic.BaseModel):
    """One provider specification: language, argv, init options."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    language: str = pydantic.Field(min_length=1)
    argv: tuple[str, ...] = pydantic.Field(min_length=1)
    initialization_options: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    root_uri: str | None = None
    provider_name: str = pydantic.Field(min_length=1)
    provider_version: str = pydantic.Field(min_length=1)
    environment_names: tuple[str, ...] = ()
    environment: dict[str, pydantic.SecretStr] = pydantic.Field(
        default_factory=dict, exclude=True, repr=False
    )
    logs_retention_days: int = pydantic.Field(default=7, ge=1, le=90)
    temp_retention_hours: int = pydantic.Field(default=24, ge=1, le=168)

    @pydantic.model_validator(mode="after")
    def _environment_is_allowlisted(self) -> typing.Self:
        if len(set(self.environment_names)) != len(self.environment_names):
            raise ValueError("provider environment names must be unique")
        unexpected = set(self.environment).difference(self.environment_names)
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError(f"provider environment names are not allowlisted: {names}")
        return self

    def process_environment(self) -> dict[str, str]:
        """Reveal carried values only at the subprocess boundary."""
        return {name: value.get_secret_value() for name, value in self.environment.items()}


class Registration(pydantic.BaseModel):
    """One LSP dynamic registration."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str
    method: str
    register_options: dict[str, typing.Any] = pydantic.Field(default_factory=dict)


class Unregistration(pydantic.BaseModel):
    """One LSP dynamic unregistration."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str
    method: str


class _TextDocumentSyncOptions(pydantic.BaseModel):
    """Boundary model for the object form of textDocumentSync."""

    model_config = pydantic.ConfigDict(extra="ignore", populate_by_name=True)

    open_close: bool = pydantic.Field(default=False, alias="openClose")
    change: int = 0


class _RawServerCapabilities(pydantic.BaseModel):
    """Untrusted initialize capabilities before normalization."""

    model_config = pydantic.ConfigDict(extra="ignore", populate_by_name=True)

    text_document_sync: object | None = pydantic.Field(default=None, alias="textDocumentSync")
    definition_provider: object = pydantic.Field(default=False, alias="definitionProvider")
    references_provider: object = pydantic.Field(default=False, alias="referencesProvider")
    implementation_provider: object = pydantic.Field(default=False, alias="implementationProvider")
    hover_provider: object = pydantic.Field(default=False, alias="hoverProvider")
    completion_provider: object = pydantic.Field(default=False, alias="completionProvider")
    signature_help_provider: object = pydantic.Field(default=False, alias="signatureHelpProvider")
    code_action_provider: object = pydantic.Field(default=False, alias="codeActionProvider")
    document_formatting_provider: object = pydantic.Field(
        default=False, alias="documentFormattingProvider"
    )
    document_range_formatting_provider: object = pydantic.Field(
        default=False, alias="documentRangeFormattingProvider"
    )
    rename_provider: object = pydantic.Field(default=False, alias="renameProvider")
    call_hierarchy_provider: object = pydantic.Field(default=False, alias="callHierarchyProvider")
    workspace_symbol_provider: object = pydantic.Field(
        default=False, alias="workspaceSymbolProvider"
    )
    diagnostic_provider: object = pydantic.Field(default=False, alias="diagnosticProvider")
    position_encoding: str = pydantic.Field(default="utf-16", alias="positionEncoding")


def _capability_enabled(value: object) -> bool:
    return value is True or isinstance(value, dict)


def _diagnostic_identifier(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    mapping = _OBJECT_MAPPING_ADAPTER.validate_python(value, strict=True)
    identifier = mapping.get("identifier")
    return identifier if isinstance(identifier, str) and identifier else None


def _sync_options(value: object) -> _TextDocumentSyncOptions | None:
    try:
        return _TextDocumentSyncOptions.model_validate(value)
    except pydantic.ValidationError:
        return None


class ServerCapabilities(pydantic.BaseModel):
    """Normalized capabilities from an untrusted initialize response."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    text_document_sync: TextDocumentSyncKind = TextDocumentSyncKind.NONE
    open_close: bool = False
    position_encoding: str = "utf-16"
    definition_provider: bool = False
    references_provider: bool = False
    implementation_provider: bool = False
    hover_provider: bool = False
    completion_provider: bool = False
    signature_help_provider: bool = False
    code_action_provider: bool = False
    document_formatting_provider: bool = False
    document_range_formatting_provider: bool = False
    rename_provider: bool = False
    call_hierarchy_provider: bool = False
    workspace_symbol_provider: bool = False
    diagnostic_provider: bool = False
    diagnostic_identifier: str | None = None

    @classmethod
    def from_lsp(cls, value: object) -> typing.Self:
        """Validate and normalize the wire-format capabilities object."""
        raw = _RawServerCapabilities.model_validate(value)
        sync_options = _sync_options(raw.text_document_sync)
        sync_kind = normalize_text_document_sync(raw.text_document_sync)
        numeric_sync = isinstance(raw.text_document_sync, int) and not isinstance(
            raw.text_document_sync, bool
        )
        return cls(
            text_document_sync=sync_kind,
            open_close=(
                sync_options.open_close
                if sync_options is not None
                else numeric_sync and sync_kind is not TextDocumentSyncKind.NONE
            ),
            position_encoding=raw.position_encoding,
            definition_provider=_capability_enabled(raw.definition_provider),
            references_provider=_capability_enabled(raw.references_provider),
            implementation_provider=_capability_enabled(raw.implementation_provider),
            hover_provider=_capability_enabled(raw.hover_provider),
            completion_provider=_capability_enabled(raw.completion_provider),
            signature_help_provider=_capability_enabled(raw.signature_help_provider),
            code_action_provider=_capability_enabled(raw.code_action_provider),
            document_formatting_provider=_capability_enabled(raw.document_formatting_provider),
            document_range_formatting_provider=_capability_enabled(
                raw.document_range_formatting_provider
            ),
            rename_provider=_capability_enabled(raw.rename_provider),
            call_hierarchy_provider=_capability_enabled(raw.call_hierarchy_provider),
            workspace_symbol_provider=_capability_enabled(raw.workspace_symbol_provider),
            diagnostic_provider=_capability_enabled(raw.diagnostic_provider),
            diagnostic_identifier=_diagnostic_identifier(raw.diagnostic_provider),
        )


class _RawInitializeResult(pydantic.BaseModel):
    """Untrusted initialize response before capability normalization."""

    model_config = pydantic.ConfigDict(extra="ignore", populate_by_name=True)

    capabilities: object
    server_info: object | None = pydantic.Field(default=None, alias="serverInfo")


class _RawServerInfo(pydantic.BaseModel):
    """Untrusted initialize serverInfo before normalization."""

    model_config = pydantic.ConfigDict(extra="ignore")

    name: str = pydantic.Field(min_length=1)
    version: str | None = None


class ServerInfo(pydantic.BaseModel):
    """Runtime-reported provider identity from the initialized process."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    name: str = pydantic.Field(min_length=1)
    version: str | None = None

    @classmethod
    def from_lsp(cls, value: object | None) -> typing.Self | None:
        """Normalize optional serverInfo without inventing an actual version."""
        if value is None:
            return None
        raw = _RawServerInfo.model_validate(value)
        return cls(name=raw.name, version=raw.version)


class ProviderProcessIdentity(pydantic.BaseModel):
    """Configured and runtime-reported identity for one live provider process."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    configured_name: str = pydantic.Field(min_length=1)
    configured_version: str = pydantic.Field(min_length=1)
    server_info: ServerInfo | None = None
    process_id: int = pydantic.Field(ge=1)
    process_epoch: int = pydantic.Field(ge=0)


class InitializeResult(pydantic.BaseModel):
    """Validated subset of the LSP initialize result used by the broker."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    capabilities: ServerCapabilities
    server_info: ServerInfo | None = None

    @classmethod
    def from_lsp(cls, value: object) -> typing.Self:
        """Validate the initialize response at the JSON-RPC boundary."""
        raw = _RawInitializeResult.model_validate(value)
        return cls(
            capabilities=ServerCapabilities.from_lsp(raw.capabilities),
            server_info=ServerInfo.from_lsp(raw.server_info),
        )


class LspError(pydantic.BaseModel):
    """JSON-RPC error object."""

    model_config = pydantic.ConfigDict(extra="forbid")

    code: int
    message: str
    data: typing.Any | None = None


class SessionState(enum.StrEnum):
    """LSP session lifecycle states."""

    IDLE = "idle"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    SHUTTING_DOWN = "shutting_down"
    SHUTDOWN = "shutdown"
    DEAD = "dead"


_SYNC_MAP: dict[int, TextDocumentSyncKind] = {
    0: TextDocumentSyncKind.NONE,
    1: TextDocumentSyncKind.FULL,
    2: TextDocumentSyncKind.INCREMENTAL,
}


def normalize_text_document_sync(value: object) -> TextDocumentSyncKind:
    """Normalize numeric or object textDocumentSync to the enum.

    LSP servers may send either a number (0=None, 1=Full, 2=Incremental)
    or an object with openClose and change fields.
    """
    if value is None:
        return TextDocumentSyncKind.NONE
    if isinstance(value, int) and not isinstance(value, bool):
        return _SYNC_MAP.get(value, TextDocumentSyncKind.NONE)
    sync_options = _sync_options(value)
    if sync_options is not None:
        return _SYNC_MAP.get(sync_options.change, TextDocumentSyncKind.NONE)
    return TextDocumentSyncKind.NONE
