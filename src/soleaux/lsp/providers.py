"""Lazy language-server provider configuration (D006, D023, D031)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from soleaux.contracts.repository import LANGUAGE_REGISTRY
from soleaux.lsp.contracts import LanguageServerSpec
from soleaux.postgresql.runtime import (
    GO_TOOLCHAIN_ENVIRONMENT_NAMES,
    POSTGRESQL_ENVIRONMENT_NAMES,
    capture_inherited_environment,
    environment_names_for_provider,
)

_LANGUAGE_BY_EXTENSION = dict(LANGUAGE_REGISTRY.lsp_by_extension())


@dataclass(frozen=True)
class BuiltinProvider:
    """One catalog entry for a known language-server provider."""

    name: str
    display_name: str
    argv: tuple[str, ...]
    extensions: tuple[str, ...]
    version: str = "unprobed"
    install_hint: str = ""
    install_command: tuple[str, ...] | None = None
    detect_files: tuple[str, ...] = ()
    detect_deps: tuple[str, ...] = ()
    needs_dynamic_init: bool = False
    partition_key: str | None = None
    environment_names: tuple[str, ...] = ()


BUILTIN_PROVIDERS: tuple[BuiltinProvider, ...] = (
    BuiltinProvider(
        name="typescript-language-server",
        display_name="TypeScript / JavaScript",
        argv=("typescript-language-server", "--stdio"),
        extensions=("ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts"),
        version="5.3.0",
        # typescript@7 is the native compiler (bin:{tsc} only, no
        # lib/tsserver.js); typescript-language-server requires the 6.x layout.
        install_hint="npm install -g typescript-language-server typescript@6",
        install_command=("npm", "install", "-g", "typescript-language-server", "typescript@6"),
        detect_files=("tsconfig.json", "next.config.ts", "next.config.mjs"),
        detect_deps=("typescript", "next"),
        needs_dynamic_init=True,
        partition_key="ts",
    ),
    BuiltinProvider(
        name="pyright",
        display_name="Python (Pyright)",
        argv=("pyright-langserver", "--stdio"),
        extensions=("py", "pyi"),
        version="1.1.411",
        install_hint="pip install pyright || npm install -g pyright",
        install_command=("pip", "install", "pyright"),
        detect_files=("pyproject.toml",),
        detect_deps=("fastmcp",),
    ),
    BuiltinProvider(
        name="gopls",
        display_name="Go",
        argv=("gopls",),
        extensions=("go",),
        version="0.23.0",
        install_hint="go install golang.org/x/tools/gopls@latest",
        detect_files=("go.mod",),
        environment_names=GO_TOOLCHAIN_ENVIRONMENT_NAMES,
    ),
    BuiltinProvider(
        name="rust-analyzer",
        display_name="Rust",
        argv=("rust-analyzer",),
        extensions=("rs",),
        version="0.3.2256",
        install_hint="rustup component add rust-analyzer",
        detect_files=("Cargo.toml",),
    ),
    BuiltinProvider(
        name="bash-language-server",
        display_name="Shell / Bash",
        argv=("bash-language-server", "start"),
        extensions=("sh", "bash", "zsh"),
        version="5.6.0",
        install_hint="npm install -g bash-language-server",
        install_command=("npm", "install", "-g", "bash-language-server"),
        detect_files=(".bashrc", ".zshrc"),
    ),
    BuiltinProvider(
        name="deno",
        display_name="Deno",
        argv=("deno", "lsp"),
        extensions=("ts", "tsx", "js", "jsx", "mjs"),
        version="2.9.4",
        install_hint="curl -fsSL https://deno.land/install.sh | sh",
        detect_files=("deno.json", "deno.jsonc"),
        partition_key="ts",
    ),
    BuiltinProvider(
        name="astro-ls",
        display_name="Astro",
        argv=("astro-ls", "--stdio"),
        extensions=("astro",),
        version="2.16.13",
        install_hint="npm install -g @astrojs/language-server",
        install_command=("npm", "install", "-g", "@astrojs/language-server"),
        detect_deps=("astro",),
    ),
    BuiltinProvider(
        name="prisma-language-server",
        display_name="Prisma",
        argv=("prisma-language-server",),
        extensions=("prisma",),
        install_hint="npm install -g @prisma/language-server",
        detect_files=("prisma/schema.prisma",),
        detect_deps=("@prisma/client", "prisma"),
    ),
    BuiltinProvider(
        name="yaml-language-server",
        display_name="YAML",
        argv=("yaml-language-server", "--stdio"),
        extensions=("yaml", "yml"),
        version="1.23.0",
        install_hint="npm install -g yaml-language-server",
        install_command=("npm", "install", "-g", "yaml-language-server"),
        detect_files=("docker-compose.yml", "docker-compose.yaml"),
    ),
    BuiltinProvider(
        name="postgres-language-server",
        display_name="PostgreSQL",
        argv=("postgres-language-server", "lsp-proxy"),
        extensions=("sql",),
        version="0.25.4",
        install_hint="npm install -g @postgres-language-server/cli",
        install_command=("npm", "install", "-g", "@postgres-language-server/cli"),
        # `postgrestools.jsonc` is the project's former name and remains a live
        # fallback that 0.25.4 still reads with a deprecation warning.
        detect_files=(
            "postgres-language-server.jsonc",
            "postgrestools.jsonc",
            "supabase/config.toml",
        ),
        # The CLI is still dual-published under its previous package name.
        detect_deps=("@postgres-language-server/cli", "@postgrestools/postgrestools"),
        environment_names=POSTGRESQL_ENVIRONMENT_NAMES,
    ),
)

_KNOWN_PROVIDER_VERSIONS: dict[str, str] = {
    Path(provider.argv[0]).name: provider.version
    for provider in BUILTIN_PROVIDERS
    if provider.version != "unprobed"
}


class ProviderManifestError(ValueError):
    """The explicitly trusted provider manifest is malformed or escapes its root."""


class _ManifestServer(BaseModel):
    """One server row in the trusted CCLSP migration-format manifest."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    command: tuple[str, ...] = Field(min_length=1)
    extensions: tuple[str, ...] = Field(min_length=1)
    initialization_options: dict[str, Any] = Field(
        default_factory=dict,
        alias="initializationOptions",
    )
    root_dir: str = Field(default=".", alias="rootDir")

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not token or "\x00" in token for token in value):
            msg = "provider command tokens must be nonempty and NUL-free"
            raise ValueError(msg)
        return value

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(extension.removeprefix(".").lower() for extension in value)
        if any(
            not extension or "\x00" in extension or "/" in extension or "\\" in extension
            for extension in normalized
        ):
            msg = "provider extensions must be simple, nonempty suffixes"
            raise ValueError(msg)
        if len(set(normalized)) != len(normalized):
            msg = "provider extensions must be unique within a server row"
            raise ValueError(msg)
        return normalized

    @field_validator("root_dir")
    @classmethod
    def _validate_root_dir(cls, value: str) -> str:
        if not value or "\x00" in value:
            msg = "provider rootDir must be nonempty and NUL-free"
            raise ValueError(msg)
        return value


class _Manifest(BaseModel):
    """Validated CCLSP migration-format manifest."""

    model_config = ConfigDict(extra="forbid")

    servers: tuple[_ManifestServer, ...] = Field(min_length=1)


def resolve_provider_executable(argv: tuple[str, ...], root: Path) -> str | None:
    """Resolve the executable a configured provider would launch without invoking it."""
    launcher = Path(argv[0]).name
    provider_executable = _provider_name(argv)
    launcher_executable = shutil.which(argv[0])

    if launcher == "pnpm":
        managed_executable = _resolve_workspace_executable(
            provider_executable,
            root,
            relative_bin=Path("node_modules/.bin"),
        )
        return launcher_executable if managed_executable is not None else None

    if launcher == "uv":
        managed_executable = _resolve_workspace_executable(
            provider_executable,
            root,
            relative_bin=Path(".venv") / ("Scripts" if os.name == "nt" else "bin"),
        )
        return launcher_executable if managed_executable is not None else None

    workspace_executable = _resolve_workspace_executable(
        provider_executable,
        root,
        relative_bin=Path("node_modules/.bin"),
    )
    if workspace_executable is not None:
        return workspace_executable
    environment_executable = _resolve_workspace_executable(
        provider_executable,
        root,
        relative_bin=Path(".venv") / ("Scripts" if os.name == "nt" else "bin"),
    )
    return environment_executable or launcher_executable


def _resolve_workspace_executable(
    executable: str,
    root: Path,
    *,
    relative_bin: Path,
) -> str | None:
    """Prefer the nearest workspace or monorepo-owned toolchain executable."""
    for candidate in (root, *root.parents):
        resolved = shutil.which(executable, path=str(candidate / relative_bin))
        if resolved is not None:
            return resolved
    return None


class ConfiguredProvider(BaseModel):
    """One inert provider configuration; constructing it starts no process."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    extensions: tuple[str, ...] = Field(min_length=1)
    initialization_options: dict[str, Any] = Field(default_factory=dict)
    root: Path
    config_digest: str = Field(min_length=1)
    environment_names: tuple[str, ...] = ()
    environment: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)
    logs_retention_days: int = Field(default=7, ge=1, le=90)
    temp_retention_hours: int = Field(default=24, ge=1, le=168)

    @model_validator(mode="after")
    def _environment_is_allowlisted(self) -> Self:
        if len(set(self.environment_names)) != len(self.environment_names):
            raise ValueError("provider environment names must be unique")
        unexpected = set(self.environment).difference(self.environment_names)
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError(f"provider environment names are not allowlisted: {names}")
        return self

    def executable_available(self) -> bool:
        """Probe an already-installed executable without invoking or downloading it."""
        return resolve_provider_executable(self.argv, self.root) is not None

    def process_environment(self) -> dict[str, str]:
        """Reveal carried values only while constructing a process spec."""
        return {name: value.get_secret_value() for name, value in self.environment.items()}

    def to_spec(
        self,
        extension: str,
        *,
        project_root: Path | None = None,
    ) -> LanguageServerSpec:
        """Create the package-owned launch contract for one supported extension."""
        normalized = extension.removeprefix(".").lower()
        if normalized not in self.extensions:
            msg = f"{self.provider_name!r} does not support extension {extension!r}"
            raise ValueError(msg)
        selected_root = self.root if project_root is None else project_root.resolve(strict=True)
        _require_within_workspace(selected_root, self.root, label="semantic project root")
        resolved_executable = resolve_provider_executable(self.argv, self.root)
        argv = self.argv if resolved_executable is None else (resolved_executable, *self.argv[1:])
        return LanguageServerSpec(
            language=_LANGUAGE_BY_EXTENSION.get(normalized, normalized),
            argv=argv,
            initialization_options=copy.deepcopy(self.initialization_options),
            root_uri=selected_root.as_uri(),
            provider_name=self.provider_name,
            provider_version=self.provider_version,
            environment_names=self.environment_names,
            environment=dict(self.environment),
            logs_retention_days=self.logs_retention_days,
            temp_retention_hours=self.temp_retention_hours,
        )


class ProviderRegistry:
    """Configured providers indexed by extension, with lazy availability probes."""

    def __init__(self, providers: tuple[ConfiguredProvider, ...]) -> None:
        if not providers:
            msg = "ProviderRegistry requires at least one provider"
            raise ValueError(msg)
        by_extension: dict[str, ConfiguredProvider] = {}
        for provider in providers:
            for extension in provider.extensions:
                if extension in by_extension:
                    msg = f"multiple providers configured for extension {extension!r}"
                    raise ValueError(msg)
                by_extension[extension] = provider
        self._providers = providers
        self._by_extension = by_extension

    @property
    def providers(self) -> tuple[ConfiguredProvider, ...]:
        """Return inert configurations in manifest order."""
        return self._providers

    @classmethod
    def from_cclsp(
        cls,
        *,
        manifest_path: Path,
        workspace_root: Path,
    ) -> Self:
        """Parse an explicitly trusted CCLSP migration manifest through Pydantic."""
        root = workspace_root.resolve(strict=True)
        manifest = manifest_path.resolve(strict=True)
        _require_within_workspace(manifest, root, label="provider manifest")
        try:
            parsed = _Manifest.model_validate_json(manifest.read_bytes())
        except (OSError, ValidationError) as exc:
            msg = f"invalid provider manifest {manifest}"
            raise ProviderManifestError(msg) from exc

        providers = tuple(
            _configured_provider(
                row,
                workspace_root=root,
                manifest_directory=manifest.parent,
            )
            for row in parsed.servers
        )
        try:
            return cls(providers)
        except ValueError as exc:
            raise ProviderManifestError(str(exc)) from exc

    @classmethod
    def default(
        cls,
        workspace_root: Path,
        *,
        logs_retention_days: int = 7,
        temp_retention_hours: int = 24,
    ) -> Self:
        """Build the registry from BUILTIN_PROVIDERS with detection-based partition."""
        root = workspace_root.resolve(strict=True)
        deno_active = (root / "deno.json").is_file() or (root / "deno.jsonc").is_file()

        rows: list[_ManifestServer] = []
        for builtin in BUILTIN_PROVIDERS:
            if builtin.partition_key == "ts":
                if builtin.name == "deno" and not deno_active:
                    continue
                if builtin.name == "typescript-language-server" and deno_active:
                    extensions = ("cjs", "mts", "cts")
                else:
                    extensions = builtin.extensions
            else:
                extensions = builtin.extensions

            init_options: dict[str, Any] = {}
            if builtin.needs_dynamic_init:
                init_options = _typescript_initialization_options(root)

            rows.append(
                _ManifestServer(
                    command=builtin.argv,
                    extensions=extensions,
                    initializationOptions=init_options,
                )
            )

        return cls(
            tuple(
                _configured_provider(
                    row,
                    workspace_root=root,
                    manifest_directory=root,
                    logs_retention_days=logs_retention_days,
                    temp_retention_hours=temp_retention_hours,
                )
                for row in rows
            )
        )

    def configured_for_path(self, path: str | Path) -> ConfiguredProvider | None:
        """Return a matching inert configuration without probing or starting it."""
        extension = Path(path).suffix.removeprefix(".").lower()
        return self._by_extension.get(extension)

    def available_spec_for_path(self, path: str | Path) -> LanguageServerSpec | None:
        """Return a launch spec only when the configured executable is already installed."""
        extension = Path(path).suffix.removeprefix(".").lower()
        provider = self._by_extension.get(extension)
        if provider is None or not provider.executable_available():
            return None
        return provider.to_spec(extension)


def _configured_provider(
    row: _ManifestServer,
    *,
    workspace_root: Path,
    manifest_directory: Path,
    logs_retention_days: int = 7,
    temp_retention_hours: int = 24,
) -> ConfiguredProvider:
    raw_root = Path(row.root_dir)
    provider_root = (
        raw_root.resolve(strict=True)
        if raw_root.is_absolute()
        else (manifest_directory / raw_root).resolve(strict=True)
    )
    _require_within_workspace(provider_root, workspace_root, label="provider rootDir")
    provider_name = _provider_name(row.command)
    environment_names = environment_names_for_provider(provider_name)
    environment = capture_inherited_environment(environment_names)
    digest_payload = {
        "argv": row.command,
        "environment_names": environment_names,
        "extensions": row.extensions,
        "initialization_options": row.initialization_options,
        "root": str(provider_root),
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ConfiguredProvider(
        provider_name=provider_name,
        provider_version=_KNOWN_PROVIDER_VERSIONS.get(provider_name, "unprobed"),
        argv=row.command,
        extensions=row.extensions,
        initialization_options=copy.deepcopy(row.initialization_options),
        root=provider_root,
        config_digest=digest,
        environment_names=environment_names,
        environment={name: SecretStr(value) for name, value in environment.items()},
        logs_retention_days=logs_retention_days,
        temp_retention_hours=temp_retention_hours,
    )


def _provider_name(argv: tuple[str, ...]) -> str:
    launcher = Path(argv[0]).name
    if launcher == "pnpm":
        try:
            executable_index = argv.index("exec") + 1
            return Path(argv[executable_index]).name
        except (ValueError, IndexError) as exc:
            msg = "pnpm provider command must include an executable after 'exec'"
            raise ProviderManifestError(msg) from exc
    if launcher == "uv":
        try:
            run_index = argv.index("run")
        except ValueError as exc:
            msg = "uv provider command must include 'run'"
            raise ProviderManifestError(msg) from exc
        candidates = [token for token in argv[run_index + 1 :] if not token.startswith("-")]
        if not candidates:
            msg = "uv provider command must name an executable"
            raise ProviderManifestError(msg)
        return Path(candidates[-1]).name
    return launcher


def _typescript_initialization_options(workspace_root: Path) -> dict[str, Any]:
    search_roots = (workspace_root, *workspace_root.parents)
    for root in search_roots:
        for relative in (
            Path("node_modules/typescript-lsp/lib/tsserver.js"),
            Path("node_modules/typescript/lib/tsserver.js"),
        ):
            candidate = root / relative
            if candidate.is_file():
                return {
                    "tsserver": {
                        "path": str(candidate.resolve()),
                        "useSyntaxServer": "never",
                    }
                }
    executable = shutil.which("tsserver")
    if executable is not None:
        resolved = Path(executable).resolve()
        candidate = resolved.parent.parent / "lib" / "tsserver.js"
        if candidate.is_file():
            return {
                "tsserver": {
                    "path": str(candidate),
                    "useSyntaxServer": "never",
                }
            }
    return {"tsserver": {"useSyntaxServer": "never"}}


def _require_within_workspace(path: Path, workspace_root: Path, *, label: str) -> None:
    if path != workspace_root and workspace_root not in path.parents:
        msg = f"{label} escapes the configured workspace: {path}"
        raise ProviderManifestError(msg)
