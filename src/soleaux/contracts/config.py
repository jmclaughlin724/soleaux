"""`soleaux.toml` resolution (D021).

Absent or empty config resolves to the same complete typed default. Unknown
keys fail clearly. There is no init command and no generated runtime.
"""

from __future__ import annotations

import contextlib
import enum
import ipaddress
import json
import pathlib
import tomllib
import typing
import urllib.parse

import pydantic

import soleaux.contracts.governance
import soleaux.contracts.repository
import soleaux.postgresql.contracts

CONFIG_SCHEMA_VERSION = "soleaux.config/v1"
CONFIG_FILENAME = "soleaux.toml"

_HTTP_TOKEN_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_NPM_PACKAGE_CHARACTERS = frozenset(
    "-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_FORBIDDEN_MCP_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "cookie",
        "keep-alive",
        "mcp-protocol-version",
        "mcp-session-id",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _empty_postgresql_lane_roots() -> dict[
    soleaux.postgresql.contracts.SourceLane, tuple[str, ...]
]:
    return {}


def _is_ascii_letter(character: str) -> bool:
    return "a" <= character <= "z" or "A" <= character <= "Z"


def _is_ascii_alphanumeric(character: str) -> bool:
    return _is_ascii_letter(character) or "0" <= character <= "9"


def _is_environment_variable_name(value: str) -> bool:
    return (
        bool(value)
        and (value[0] == "_" or _is_ascii_letter(value[0]))
        and all(character == "_" or _is_ascii_alphanumeric(character) for character in value[1:])
    )


def _is_mcp_namespace(value: str) -> bool:
    if not value or not "a" <= value[0] <= "z":
        return False
    previous_was_separator = False
    for character in value[1:]:
        if character in {"-", "_"}:
            if previous_was_separator:
                return False
            previous_was_separator = True
            continue
        if not ("a" <= character <= "z" or "0" <= character <= "9"):
            return False
        previous_was_separator = False
    return not previous_was_separator


def _is_http_token(value: str) -> bool:
    return bool(value) and all(character in _HTTP_TOKEN_CHARACTERS for character in value)


def _is_npm_package_segment(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith(".")
        and all(character in _NPM_PACKAGE_CHARACTERS for character in value)
    )


def _is_npm_package_name(value: str) -> bool:
    parts = value.split("/")
    if value.startswith("@"):
        return (
            len(parts) == 2
            and _is_npm_package_segment(parts[0][1:])
            and _is_npm_package_segment(parts[1])
        )
    return len(parts) == 1 and _is_npm_package_segment(parts[0])


class ConfigError(Exception):
    """Typed config failure naming the offending file or key."""


class CatalogMode(enum.StrEnum):
    """SQLite catalog persistence mode."""

    AUTO = "auto"
    DISK = "disk"
    MEMORY = "memory"
    OFF = "off"


class CatalogConfig(pydantic.BaseModel):
    """Lifecycle-owned SQLite catalog configuration."""

    model_config = pydantic.ConfigDict(extra="forbid")

    mode: CatalogMode = CatalogMode.MEMORY
    retained_generations: int = pydantic.Field(default=2, ge=1, le=32)
    max_disk_size_mb: int = pydantic.Field(default=512, ge=16, le=16384)


class PostgreSqlConfig(pydantic.BaseModel):
    """Explicit repository evidence for PostgreSQL provenance lanes."""

    model_config = pydantic.ConfigDict(extra="forbid")

    lane_roots: dict[soleaux.postgresql.contracts.SourceLane, tuple[str, ...]] = pydantic.Field(
        default_factory=_empty_postgresql_lane_roots
    )

    @pydantic.model_validator(mode="after")
    def _validate_lane_roots(self) -> PostgreSqlConfig:
        if soleaux.postgresql.contracts.SourceLane.UNCLASSIFIED in self.lane_roots:
            raise ValueError("unclassified is a fallback, not a configured source lane")
        seen: set[str] = set()
        for roots in self.lane_roots.values():
            for root in roots:
                candidate = pathlib.Path(root)
                if not root or candidate.is_absolute() or ".." in candidate.parts or "\x00" in root:
                    raise ValueError(
                        "PostgreSQL lane roots must be contained repository-relative paths"
                    )
                normalized = candidate.as_posix().removeprefix("./").rstrip("/")
                if normalized in seen:
                    raise ValueError("PostgreSQL lane roots must be globally unique")
                seen.add(normalized)
        return self


class ProviderConfig(pydantic.BaseModel):
    """One explicitly trusted language-server provider definition."""

    model_config = pydantic.ConfigDict(extra="forbid")

    command: list[str] | None = None
    extensions: tuple[str, ...] = ()
    initialization_options: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    root_dir: str = "."
    enabled: bool = True


class McpBackendConfig(pydantic.BaseModel):
    """One explicitly trusted proxied MCP backend (D034, D035)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = pydantic.Field(default_factory=dict)
    cwd: str | None = None
    lifecycle: typing.Literal["on_demand", "session", "shared"] = "on_demand"
    stateless: bool = False
    enabled: bool = True
    cache_ttl_seconds: float = pydantic.Field(default=300.0, ge=0, le=300, allow_inf_nan=False)
    request_timeout_seconds: float = pydantic.Field(
        default=300.0, gt=0, le=300, allow_inf_nan=False
    )
    init_timeout_seconds: float = pydantic.Field(default=30.0, gt=0, le=60, allow_inf_nan=False)
    auth: typing.Literal["none", "bearer_env", "oauth"] = "none"
    auth_token_env: str | None = None
    headers_from_env: dict[str, str] = pydantic.Field(default_factory=dict)
    tls_verify: bool = True
    tls_ca_file_env: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    oauth_client_name: str = "Soleaux"
    oauth_client_metadata_url: str | None = None
    oauth_token_endpoint_auth_method: (
        typing.Literal["client_secret_basic", "client_secret_post", "none"] | None
    ) = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    token_store: typing.Literal["disk", "keyring"] = "disk"
    forward_incoming_headers: typing.Literal[False] = False
    forward_roots: typing.Literal[False] = False
    forward_sampling: typing.Literal[False] = False
    forward_elicitation: typing.Literal[False] = False
    forward_logs: typing.Literal[False] = False
    forward_progress: typing.Literal[False] = False
    fail_open: typing.Literal[True] = True

    @pydantic.model_validator(mode="after")
    def _validate_backend(self) -> McpBackendConfig:
        has_command = self.command is not None
        has_url = self.url is not None
        if has_command == has_url:
            msg = "MCP backend requires exactly one of command or url"
            raise ValueError(msg)
        if self.lifecycle == "shared" and (not has_url or not self.stateless):
            raise ValueError(
                "MCP lifecycle 'shared' requires a stateless HTTP backend "
                "declared with stateless = true"
            )
        if self.lifecycle == "session" and has_url:
            raise ValueError("MCP lifecycle 'session' requires a command backend")
        self._validate_auth_fields(has_url=has_url)

        if has_command:
            assert self.command is not None
            if not self.command or any(not part or "\x00" in part for part in self.command):
                raise ValueError("MCP command requires nonempty, NUL-free elements")
            self._validate_command_fields()
        else:
            assert self.url is not None
            self._validate_url_fields()
        return self

    def _validate_auth_fields(self, *, has_url: bool) -> None:
        if self.auth == "oauth" and self.auth_token_env is not None:
            raise ValueError("MCP auth 'oauth' is mutually exclusive with auth_token_env")
        if self.auth == "bearer_env":
            if self.auth_token_env is None:
                raise ValueError("MCP auth 'bearer_env' requires auth_token_env")
        elif self.auth_token_env is not None:
            raise ValueError('MCP auth_token_env requires auth = "bearer_env"')
        if self.auth == "oauth":
            if not has_url:
                raise ValueError("MCP auth 'oauth' requires a URL backend")
            self._validate_oauth_fields()
            return
        oauth_fields_set = (
            bool(self.oauth_scopes)
            or self.oauth_client_metadata_url is not None
            or self.oauth_token_endpoint_auth_method is not None
            or self.client_id_env is not None
            or self.client_secret_env is not None
            or self.token_store != "disk"
        )
        if oauth_fields_set:
            raise ValueError('MCP OAuth fields require auth = "oauth"')

    def _validate_oauth_fields(self) -> None:
        for scope in self.oauth_scopes:
            if not scope or any(character.isspace() for character in scope) or "\x00" in scope:
                raise ValueError("MCP oauth_scopes require nonempty, whitespace-free elements")
        if not self.oauth_client_name or "\x00" in self.oauth_client_name:
            raise ValueError("MCP oauth_client_name must be a nonempty, NUL-free string")
        if self.oauth_client_metadata_url is not None:
            try:
                parsed = urllib.parse.urlsplit(self.oauth_client_metadata_url)
            except ValueError as exc:
                raise ValueError("MCP oauth_client_metadata_url is invalid") from exc
            if (
                parsed.scheme != "https"
                or parsed.hostname is None
                or parsed.path in {"", "/"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(
                    "MCP oauth_client_metadata_url must be an HTTPS URL with a non-root path"
                )
        for env_name in (self.client_id_env, self.client_secret_env):
            if env_name is not None and not _is_environment_variable_name(env_name):
                raise ValueError("MCP OAuth client references must be environment variable names")
        if self.client_secret_env is not None and self.client_id_env is None:
            raise ValueError("MCP client_secret_env requires client_id_env")

    def _validate_command_fields(self) -> None:
        for name, value in self.env.items():
            if not _is_environment_variable_name(name) or "\x00" in value:
                raise ValueError("MCP env requires valid names and NUL-free values")
        if self.cwd is not None:
            cwd = pathlib.Path(self.cwd)
            if not self.cwd or "\x00" in self.cwd or cwd.is_absolute() or ".." in cwd.parts:
                raise ValueError("MCP cwd must be a contained relative path")
        if (
            self.auth_token_env is not None
            or self.headers_from_env
            or self.tls_ca_file_env is not None
            or not self.tls_verify
        ):
            raise ValueError("HTTP-only fields require a URL backend")

    def _validate_url_fields(self) -> None:
        assert self.url is not None
        if self.env or self.cwd is not None:
            raise ValueError("MCP env and cwd require a command backend")
        try:
            parsed = urllib.parse.urlsplit(self.url)
            hostname = parsed.hostname
        except ValueError as exc:
            raise ValueError("MCP URL is invalid") from exc
        if parsed.scheme not in {"http", "https"} or hostname is None:
            raise ValueError("MCP URL must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("MCP URL must not contain userinfo or a fragment")

        is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            with contextlib.suppress(ValueError):
                is_loopback = ipaddress.ip_address(hostname).is_loopback
        if parsed.scheme == "http" and not is_loopback:
            raise ValueError("non-loopback MCP URLs require HTTPS")
        if not self.tls_verify and not is_loopback:
            raise ValueError("TLS verification may be disabled only for loopback URLs")
        if self.tls_ca_file_env is not None and not self.tls_verify:
            raise ValueError("a TLS CA file requires verification")

        for env_name in (self.auth_token_env, self.tls_ca_file_env):
            if env_name is not None and not _is_environment_variable_name(env_name):
                raise ValueError("MCP secret references must be environment variable names")

        normalized_headers: set[str] = set()
        for header, env_name in self.headers_from_env.items():
            normalized = header.lower()
            if (
                not _is_http_token(header)
                or normalized in _FORBIDDEN_MCP_HEADERS
                or normalized in normalized_headers
            ):
                raise ValueError("MCP header names must be unique, safe HTTP tokens")
            if not _is_environment_variable_name(env_name):
                raise ValueError("MCP header values must reference environment variables")
            normalized_headers.add(normalized)


class PolicyEffect(enum.StrEnum):
    """Canonical host-neutral MCP tool-policy effect."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyBackendConfig(pydantic.BaseModel):
    """One backend's default effect and per-tool overrides.

    Tool keys are unprefixed backend tool names as the backend exposes them.
    Live backend membership is unknowable at config-load time, so only shape
    is validated. Wildcards are not supported: ``default`` is the only
    per-backend fallback.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    default: PolicyEffect = PolicyEffect.ASK
    tools: dict[str, PolicyEffect] = pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="after")
    def _validate_tool_names(self) -> PolicyBackendConfig:
        for name in self.tools:
            if not name or "\x00" in name or "*" in name:
                raise ValueError("policy tool names must be nonempty, NUL-free, and non-wildcard")
        return self


class PolicyConfig(pydantic.BaseModel):
    """Canonical per-backend MCP tool policy (``[policy]``) (D036).

    ``soleaux.toml`` owns policy effects; host approval surfaces are rendered
    output. Policy backends must be declared under ``[mcp]``; an absent
    section resolves to an empty policy.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    backends: dict[str, PolicyBackendConfig] = pydantic.Field(default_factory=dict)


class HealthConfig(pydantic.BaseModel):
    """Retention thresholds for workspace and host health checks."""

    model_config = pydantic.ConfigDict(extra="forbid")

    logs_retention_days: int = pydantic.Field(default=7, ge=1, le=90)
    temp_retention_hours: int = pydantic.Field(default=24, ge=1, le=168)
    archived_sessions_retention_days: int = pydantic.Field(default=14, ge=1, le=90)
    max_logs_db_size_mb: int = pydantic.Field(default=500, ge=100, le=10000)


class LspConfig(pydantic.BaseModel):
    """Language-server request deadlines owned by the protocol adapter."""

    model_config = pydantic.ConfigDict(extra="forbid")

    diagnostic_timeout_seconds: float = pydantic.Field(
        default=5.0,
        gt=0,
        le=60,
        allow_inf_nan=False,
    )


class SkillsConfig(pydantic.BaseModel):
    """Explicit workspace skill discovery and exposure (``[skills]``).

    The product default is disabled and has no roots, so repositories choose
    their own skill layout and activation policy. Resolved root paths are
    de-duplicated and per-name first-wins handling is delegated to the upstream
    ``SkillsDirectoryProvider``.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    enabled: bool = False
    roots: tuple[str, ...] = ()
    reload: bool = False
    main_file_name: str = "SKILL.md"
    supporting_files: typing.Literal["resources", "template"] = "template"

    @pydantic.model_validator(mode="after")
    def _validate_skills_fields(self) -> SkillsConfig:
        for configured in self.roots:
            if not configured or "\x00" in configured:
                raise ValueError("skills roots require nonempty, NUL-free elements")
            candidate = pathlib.Path(configured)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"skills root must be a contained relative path: {configured!r}")
        name = self.main_file_name
        if not name or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("skills main_file_name must be a bare filename")
        return self


class TelemetryConfig(pydantic.BaseModel):
    """Optional read-only telemetry daemon exposure (``[telemetry]``).

    Disabled by default. When enabled, ``telemetry_*`` tools proxy the local
    telemetry daemon's HTTP API. ``daemon_url`` is the bare origin; the client
    owns the ``/api/v1`` prefix, matching the telemetry workspace convention.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    enabled: bool = False
    daemon_url: str = "http://127.0.0.1:43120"
    timeout_seconds: float = 5.0

    @pydantic.model_validator(mode="after")
    def _validate_telemetry_fields(self) -> TelemetryConfig:
        parsed = urllib.parse.urlsplit(self.daemon_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"telemetry daemon_url must be an http(s) origin: {self.daemon_url!r}")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError(
                "telemetry daemon_url must be a bare origin; the client appends /api/v1"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("telemetry timeout_seconds must be positive")
        return self


class StructuralConfig(pydantic.BaseModel):
    """Structural engine selection and workspace rule loading (``[structural]``).

    The product default is the zero-config Python engine. Selecting ``napi`` or
    ``rust`` requires the package-owned or managed engine to exist at its exact
    pinned version; a missing or mismatched engine fails closed with install
    guidance and never falls back to another backend. Repository configuration
    cannot select an executable or package installation path.
    ``project_config`` names one contained ast-grep project configuration whose
    rule directories serve lint and rule references; absent means no workspace
    rules. ``languages`` lists trusted dynamic-language registrations for the
    NAPI engine, resolved relative to the packaged worker only — requests may
    select registered languages but never supply parser packages.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    backend: typing.Literal["python", "napi", "rust"] = "python"
    project_config: str | None = None
    languages: dict[str, str] = pydantic.Field(default_factory=dict)

    @pydantic.model_validator(mode="after")
    def _validate_structural_fields(self) -> StructuralConfig:
        if self.project_config is not None:
            configured = self.project_config
            if not configured or "\x00" in configured:
                raise ValueError("structural paths require nonempty, NUL-free values")
            candidate = pathlib.Path(configured)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(
                    f"structural path must be a contained relative path: {configured!r}"
                )
        if self.languages and self.backend != "napi":
            raise ValueError("dynamic language registrations require the napi backend")
        for language, package in self.languages.items():
            if not language or not language.isidentifier():
                raise ValueError(f"invalid dynamic language name: {language!r}")
            if not _is_npm_package_name(package):
                raise ValueError(f"invalid dynamic language package: {package!r}")
        return self


class CoverageArtifactConfig(pydantic.BaseModel):
    """One explicitly trusted local coverage artifact."""

    model_config = pydantic.ConfigDict(extra="forbid")

    path: str = pydantic.Field(min_length=1)
    format: typing.Literal["soleaux_json"] = "soleaux_json"

    @pydantic.model_validator(mode="after")
    def _validate_path(self) -> CoverageArtifactConfig:
        candidate = pathlib.Path(self.path)
        if candidate.is_absolute() or ".." in candidate.parts or "\x00" in self.path:
            raise ValueError(f"coverage artifact must stay in the workspace: {self.path!r}")
        return self


class CoverageImportConfig(pydantic.BaseModel):
    """Trusted local continuous-integration artifacts; empty disables import."""

    model_config = pydantic.ConfigDict(extra="forbid")

    artifacts: tuple[CoverageArtifactConfig, ...] = pydantic.Field(default=(), max_length=16)

    @pydantic.model_validator(mode="after")
    def _validate_unique_paths(self) -> CoverageImportConfig:
        paths = [artifact.path for artifact in self.artifacts]
        if len(set(paths)) != len(paths):
            raise ValueError("coverage artifacts declare duplicate paths")
        return self


class MarkdownTableSelector(pydantic.BaseModel):
    """Select one markdown table by its nearest heading text and occurrence."""

    model_config = pydantic.ConfigDict(extra="forbid")

    kind: typing.Literal["markdown_table"] = "markdown_table"
    heading: str = pydantic.Field(min_length=1)
    occurrence: int = pydantic.Field(default=1, ge=1)


class StructuredRecordsSelector(pydantic.BaseModel):
    """Address one list of record mappings by a typed key path."""

    model_config = pydantic.ConfigDict(extra="forbid")

    kind: typing.Literal["structured_records"] = "structured_records"
    keys: tuple[str, ...] = pydantic.Field(min_length=1)

    @pydantic.model_validator(mode="after")
    def _validate_keys(self) -> StructuredRecordsSelector:
        if any(not key for key in self.keys):
            raise ValueError("structured record keys must be nonempty")
        return self


class GovernanceRelationshipConfig(pydantic.BaseModel):
    """One declared relationship column of a configured governance source."""

    model_config = pydantic.ConfigDict(extra="forbid")

    field: str = pydantic.Field(min_length=1)
    target_kind: soleaux.contracts.governance.GovernanceTargetKind = (
        soleaux.contracts.governance.GovernanceTargetKind.AUTO
    )
    role: str | None = pydantic.Field(default=None, min_length=1)
    required: bool = False


class GovernanceSourceConfig(pydantic.BaseModel):
    """One explicitly configured canonical governance source."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str = pydantic.Field(min_length=1)
    path: str = pydantic.Field(min_length=1)
    format: typing.Literal["markdown", "json", "yaml", "toml"]
    selector: MarkdownTableSelector | StructuredRecordsSelector = pydantic.Field(
        discriminator="kind"
    )
    identity_field: str = pydantic.Field(min_length=1)
    relationships: tuple[GovernanceRelationshipConfig, ...] = ()

    @pydantic.model_validator(mode="after")
    def _validate_source(self) -> GovernanceSourceConfig:
        candidate = pathlib.Path(self.path)
        if candidate.is_absolute() or ".." in candidate.parts or "\x00" in self.path:
            raise ValueError(f"governance source path must stay in the workspace: {self.path!r}")
        markdown_selector = isinstance(self.selector, MarkdownTableSelector)
        if markdown_selector != (self.format == "markdown"):
            raise ValueError(
                f"governance source {self.id!r} selector kind does not match format {self.format!r}"
            )
        fields = [relationship.field for relationship in self.relationships]
        if len(set(fields)) != len(fields):
            raise ValueError(
                f"governance source {self.id!r} declares duplicate relationship fields"
            )
        if self.identity_field in fields:
            raise ValueError(
                f"governance source {self.id!r} identity field collides with a relationship field"
            )
        return self


class GovernanceConfig(pydantic.BaseModel):
    """Configured governance sources; empty means the feature is disabled."""

    model_config = pydantic.ConfigDict(extra="forbid")

    sources: tuple[GovernanceSourceConfig, ...] = ()

    @pydantic.model_validator(mode="after")
    def _validate_unique_ids(self) -> GovernanceConfig:
        identifiers = [source.id for source in self.sources]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("governance sources declare duplicate ids")
        return self


class WorkspaceConfig(pydantic.BaseModel):
    """One named workspace root from config."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str = pydantic.Field(min_length=1)
    root: str = pydantic.Field(min_length=1)


class ResolvedConfig(pydantic.BaseModel):
    """The complete typed default; every field resolves without user input."""

    model_config = pydantic.ConfigDict(extra="forbid")

    schema_version: typing.Literal["soleaux.config/v1"] = CONFIG_SCHEMA_VERSION
    workspaces: tuple[WorkspaceConfig, ...] = ()
    catalog: CatalogConfig = pydantic.Field(default_factory=CatalogConfig)
    postgresql: PostgreSqlConfig = pydantic.Field(default_factory=PostgreSqlConfig)
    providers: dict[str, ProviderConfig] = pydantic.Field(default_factory=dict)
    lsp: LspConfig = pydantic.Field(default_factory=LspConfig)
    structural: StructuralConfig = pydantic.Field(default_factory=StructuralConfig)
    coverage: CoverageImportConfig = pydantic.Field(default_factory=CoverageImportConfig)
    governance: GovernanceConfig = pydantic.Field(default_factory=GovernanceConfig)
    health: HealthConfig = pydantic.Field(default_factory=HealthConfig)
    skills: SkillsConfig = pydantic.Field(default_factory=SkillsConfig)
    telemetry: TelemetryConfig = pydantic.Field(default_factory=TelemetryConfig)
    mcp: dict[str, McpBackendConfig] = pydantic.Field(default_factory=dict)
    policy: PolicyConfig = pydantic.Field(default_factory=PolicyConfig)

    @pydantic.model_validator(mode="after")
    def _validate_mcp_namespaces(self) -> ResolvedConfig:
        names = sorted(self.mcp)
        for name in names:
            if name == "soleaux" or not _is_mcp_namespace(name):
                raise ValueError(f"invalid or reserved MCP namespace: {name!r}")
        for index, name in enumerate(names):
            for other in names[index + 1 :]:
                if other.startswith(f"{name}_"):
                    raise ValueError(f"prefix-ambiguous MCP namespaces: {name!r} and {other!r}")
        return self

    @pydantic.model_validator(mode="after")
    def _validate_policy_backends(self) -> ResolvedConfig:
        for name in self.policy.backends:
            if name not in self.mcp:
                raise ValueError(f"policy references an undeclared MCP backend: {name!r}")
        return self

    @classmethod
    def default(cls) -> ResolvedConfig:
        """The zero-config default."""
        return cls()

    def public_payload(self) -> dict[str, object]:
        """Serialize public config without empty additive extensions."""
        payload: dict[str, object] = self.model_dump(mode="json")
        if not self.mcp:
            payload.pop("mcp")
        if not self.coverage.artifacts:
            payload.pop("coverage")
        if not self.telemetry.enabled:
            payload.pop("telemetry")
        if not self.policy.backends:
            payload.pop("policy")
        return payload


def load_config_snapshot(
    root: pathlib.Path,
    filename: str = CONFIG_FILENAME,
) -> tuple[ResolvedConfig, bytes]:
    """Load one parsed config and its exact trust-binding source bytes."""
    path = root / filename
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = b""
    if not raw.strip():
        return ResolvedConfig.default(), raw
    try:
        text = raw.decode("utf-8")
        parsed = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        msg = f"invalid TOML in {path}: {exc}"
        raise ConfigError(msg) from exc
    try:
        return ResolvedConfig.model_validate(parsed), raw
    except pydantic.ValidationError as exc:
        msg = f"invalid {filename}: {exc}"
        raise ConfigError(msg) from exc


def load_config(root: pathlib.Path, filename: str = CONFIG_FILENAME) -> ResolvedConfig:
    """Load `<root>/soleaux.toml`; absent or empty yields the default."""
    config, _content = load_config_snapshot(root, filename)
    return config


def config_digest(content: bytes) -> str:
    """Digest over exact config content bytes (trust-binding input)."""
    return soleaux.contracts.repository.content_digest(content)


def resolved_config_bytes(config: ResolvedConfig) -> bytes:
    """Serialize an in-memory configuration for stable identity binding."""
    return json.dumps(
        config.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
