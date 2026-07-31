"""Canonical repository path, content digest, and language identities.

These contracts are deliberately independent of snapshots, parsers, LSPs, and
storage. Every producer admits a path once, hashes captured bytes once, and
records the language/parser identity selected by this registry.
"""

from __future__ import annotations

import collections.abc
import hashlib
import os
import pathlib
import types
import typing
import urllib.parse

import pydantic

CONTENT_DIGEST_ALGORITHM = "sha256"


class RepositoryIdentityError(ValueError):
    """Base failure for repository identity admission."""


class InvalidRepositoryPathError(RepositoryIdentityError):
    """A path is malformed or cannot have one portable repository identity."""


class RepositoryPathEscapeError(RepositoryIdentityError):
    """A path resolves outside its declared workspace."""


class WorkspaceIdentity(typing.Protocol):
    """The minimal workspace boundary required for path admission."""

    workspace_id: str
    root: pathlib.Path


def content_digest(content: bytes) -> str:
    """Return the one canonical digest for captured and prospective bytes."""
    return hashlib.sha256(content).hexdigest()


def file_uri_to_local_path(raw: str) -> str:
    """Decode one local file URI while rejecting remote authorities."""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.casefold() != "file":
        raise InvalidRepositoryPathError(f"not a file URI: {raw!r}")
    if parsed.query or parsed.fragment:
        raise InvalidRepositoryPathError("file URIs cannot contain a query or fragment")
    if parsed.netloc.casefold() not in {"", "localhost"}:
        raise InvalidRepositoryPathError(
            f"file URI authority must be empty or localhost: {parsed.netloc!r}"
        )
    for index, character in enumerate(parsed.path):
        if character == "%" and (
            index + 2 >= len(parsed.path)
            or any(
                digit not in "0123456789abcdefABCDEF"
                for digit in parsed.path[index + 1 : index + 3]
            )
        ):
            raise InvalidRepositoryPathError("file URI contains malformed percent encoding")
    try:
        decoded = urllib.parse.unquote(parsed.path, errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidRepositoryPathError("file URI path is not valid UTF-8") from exc
    if "\x00" in decoded:
        raise InvalidRepositoryPathError("NUL bytes are not admitted in repository paths")
    if os.name == "nt" and len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
        return decoded[1:]
    return decoded


def _relative_identity(raw: str) -> str:
    if "\\" in raw:
        raise InvalidRepositoryPathError("repository paths use POSIX separators")
    path = pathlib.PurePosixPath(raw)
    if path.is_absolute():
        raise InvalidRepositoryPathError("repository identity must be workspace-relative")
    normalized = path.as_posix()
    if not path.parts or normalized in {"", "."}:
        raise InvalidRepositoryPathError("repository path must identify a file or directory")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidRepositoryPathError(f"repository path is not normalized: {raw!r}")
    if normalized != raw:
        raise InvalidRepositoryPathError(f"repository path is not normalized: {raw!r}")
    return normalized


class RepositoryPath(pydantic.BaseModel):
    """One normalized POSIX path bound to an authorized workspace.

    The value preserves repository case and Unicode spelling. Containment uses
    the resolved filesystem target so symlink escapes cannot acquire an
    in-workspace identity.
    """

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = pydantic.Field(min_length=1)
    value: str = pydantic.Field(min_length=1)

    @classmethod
    def admit(
        cls,
        workspace: WorkspaceIdentity,
        candidate: str | os.PathLike[str],
    ) -> typing.Self:
        """Admit a relative path, absolute path, or local file URI."""
        raw = os.fspath(candidate)
        if "\x00" in raw:
            raise InvalidRepositoryPathError("NUL bytes are not admitted in repository paths")
        native_path = pathlib.Path(raw)
        parsed = urllib.parse.urlsplit(raw)
        if not native_path.is_absolute() and parsed.scheme and parsed.scheme.casefold() != "file":
            raise InvalidRepositoryPathError(f"unsupported repository path URI: {raw!r}")

        is_file_uri = raw.lower().startswith("file:")
        decoded = file_uri_to_local_path(raw) if is_file_uri else raw
        candidate_path = pathlib.Path(decoded)
        if is_file_uri and not candidate_path.is_absolute():
            raise InvalidRepositoryPathError("file URI path must be absolute")
        identity: str | None = None
        if candidate_path.is_absolute():
            lexical_path = candidate_path
        else:
            identity = _relative_identity(decoded)
            lexical_path = workspace.root / pathlib.Path(*pathlib.PurePosixPath(identity).parts)

        resolved_root = workspace.root.resolve(strict=True)
        resolved = lexical_path.resolve(strict=False)
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise RepositoryPathEscapeError(f"path escapes workspace: {raw!r}")

        if identity is None:
            try:
                relative = lexical_path.relative_to(workspace.root)
            except ValueError:
                relative = resolved.relative_to(resolved_root)
            identity = _relative_identity(relative.as_posix())

        return cls(workspace_id=workspace.workspace_id, value=identity)

    def absolute(self, workspace: WorkspaceIdentity) -> pathlib.Path:
        """Resolve this identity inside the workspace and revalidate containment."""
        if workspace.workspace_id != self.workspace_id:
            raise RepositoryIdentityError(
                f"path belongs to workspace {self.workspace_id!r}, not {workspace.workspace_id!r}"
            )
        admitted = type(self).admit(workspace, self.value)
        return workspace.root.joinpath(*pathlib.PurePosixPath(admitted.value).parts)

    def file_uri(self, workspace: WorkspaceIdentity) -> str:
        """Return the canonical percent-encoded local file URI."""
        return self.absolute(workspace).resolve(strict=False).as_uri()

    @property
    def suffix(self) -> str:
        return pathlib.PurePosixPath(self.value).suffix.casefold()

    @property
    def name(self) -> str:
        return pathlib.PurePosixPath(self.value).name

    def __str__(self) -> str:
        return self.value


class LanguageSpec(pydantic.BaseModel):
    """Stable language identity and its authoritative parser route."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    language_id: str = pydantic.Field(min_length=1)
    display_name: str = pydantic.Field(min_length=1)
    parser_id: str = pydantic.Field(min_length=1)
    lsp_language_id: str = pydantic.Field(min_length=1)
    extensions: tuple[str, ...] = ()
    filenames: tuple[str, ...] = ()
    structural_language: str | None = None


class LanguageRegistry:
    """Immutable filename/extension registry shared by every catalog consumer."""

    def __init__(self, languages: tuple[LanguageSpec, ...]) -> None:
        by_id: dict[str, LanguageSpec] = {}
        by_extension: dict[str, LanguageSpec] = {}
        by_filename: dict[str, LanguageSpec] = {}
        for language in languages:
            if language.language_id in by_id:
                raise ValueError(f"duplicate language id {language.language_id!r}")
            by_id[language.language_id] = language
            for extension in language.extensions:
                normalized = extension.casefold()
                if normalized in by_extension:
                    raise ValueError(f"duplicate language extension {extension!r}")
                by_extension[normalized] = language
            for filename in language.filenames:
                normalized = filename.casefold()
                if normalized in by_filename:
                    raise ValueError(f"duplicate language filename {filename!r}")
                by_filename[normalized] = language
        self._languages = languages
        self._by_id: collections.abc.Mapping[str, LanguageSpec] = types.MappingProxyType(by_id)
        self._by_extension: collections.abc.Mapping[str, LanguageSpec] = types.MappingProxyType(
            by_extension
        )
        self._by_filename: collections.abc.Mapping[str, LanguageSpec] = types.MappingProxyType(
            by_filename
        )

    @property
    def languages(self) -> tuple[LanguageSpec, ...]:
        return self._languages

    @property
    def by_id(self) -> collections.abc.Mapping[str, LanguageSpec]:
        return self._by_id

    def detect(self, path: RepositoryPath | str) -> LanguageSpec | None:
        value = path.value if isinstance(path, RepositoryPath) else path
        pure_path = pathlib.PurePosixPath(value)
        named = self._by_filename.get(pure_path.name.casefold())
        if named is not None:
            return named
        return self._by_extension.get(pure_path.suffix.casefold())

    def structural_by_extension(self) -> collections.abc.Mapping[str, str]:
        return types.MappingProxyType(
            {
                extension: language.structural_language
                for extension, language in self._by_extension.items()
                if language.structural_language is not None
            }
        )

    def lsp_by_extension(self) -> collections.abc.Mapping[str, str]:
        return types.MappingProxyType(
            {
                extension.removeprefix("."): language.lsp_language_id
                for extension, language in self._by_extension.items()
            }
        )


LANGUAGE_REGISTRY = LanguageRegistry(
    (
        LanguageSpec(
            language_id="python",
            display_name="Python",
            parser_id="ast-grep:Python",
            lsp_language_id="python",
            extensions=(".py", ".pyi"),
            structural_language="Python",
        ),
        LanguageSpec(
            language_id="typescript",
            display_name="TypeScript",
            parser_id="ts-morph:typescript",
            lsp_language_id="typescript",
            extensions=(".ts", ".mts", ".cts"),
            structural_language="TypeScript",
        ),
        LanguageSpec(
            language_id="typescriptreact",
            display_name="TypeScript JSX",
            parser_id="ts-morph:typescript",
            lsp_language_id="typescriptreact",
            extensions=(".tsx",),
            structural_language="Tsx",
        ),
        LanguageSpec(
            language_id="javascript",
            display_name="JavaScript",
            parser_id="ts-morph:typescript",
            lsp_language_id="javascript",
            extensions=(".js", ".mjs", ".cjs"),
            structural_language="JavaScript",
        ),
        LanguageSpec(
            language_id="javascriptreact",
            display_name="JavaScript JSX",
            parser_id="ts-morph:typescript",
            lsp_language_id="javascriptreact",
            extensions=(".jsx",),
            structural_language="Tsx",
        ),
        LanguageSpec(
            language_id="go",
            display_name="Go",
            parser_id="ast-grep:Go",
            lsp_language_id="go",
            extensions=(".go",),
            structural_language="Go",
        ),
        LanguageSpec(
            language_id="rust",
            display_name="Rust",
            parser_id="language-server:rust-analyzer",
            lsp_language_id="rust",
            extensions=(".rs",),
        ),
        LanguageSpec(
            language_id="shell",
            display_name="Shell",
            parser_id="language-server:bash-language-server",
            lsp_language_id="shellscript",
            extensions=(".sh", ".bash", ".zsh"),
        ),
        LanguageSpec(
            language_id="postgresql",
            display_name="PostgreSQL",
            parser_id="@libpg-query/parser",
            lsp_language_id="sql",
            extensions=(".sql",),
            structural_language="PostgreSQL",
        ),
        LanguageSpec(
            language_id="yaml",
            display_name="YAML",
            parser_id="PyYAML",
            lsp_language_id="yaml",
            extensions=(".yaml", ".yml"),
        ),
        LanguageSpec(
            language_id="json",
            display_name="JSON",
            parser_id="stdlib:json",
            lsp_language_id="json",
            extensions=(".json",),
        ),
        LanguageSpec(
            language_id="jsonc",
            display_name="JSON with comments",
            parser_id="typescript:jsonc",
            lsp_language_id="jsonc",
            extensions=(".jsonc",),
        ),
        LanguageSpec(
            language_id="toml",
            display_name="TOML",
            parser_id="stdlib:tomllib",
            lsp_language_id="toml",
            extensions=(".toml",),
        ),
        LanguageSpec(
            language_id="css",
            display_name="CSS",
            parser_id="language-server:vscode-css-language-server",
            lsp_language_id="css",
            extensions=(".css", ".scss", ".sass", ".less"),
        ),
        LanguageSpec(
            language_id="graphql",
            display_name="GraphQL",
            parser_id="language-server:graphql",
            lsp_language_id="graphql",
            extensions=(".graphql", ".gql"),
        ),
        LanguageSpec(
            language_id="html",
            display_name="HTML",
            parser_id="language-server:vscode-html-language-server",
            lsp_language_id="html",
            extensions=(".html", ".htm"),
        ),
        LanguageSpec(
            language_id="markdown",
            display_name="Markdown",
            parser_id="markdown:section",
            lsp_language_id="markdown",
            extensions=(".md",),
            filenames=("AGENTS.md", "SKILL.md"),
        ),
        LanguageSpec(
            language_id="mdx",
            display_name="MDX",
            parser_id="markdown:section",
            lsp_language_id="mdx",
            extensions=(".mdx",),
        ),
        LanguageSpec(
            language_id="astro",
            display_name="Astro",
            parser_id="language-server:astro-ls",
            lsp_language_id="astro",
            extensions=(".astro",),
        ),
        LanguageSpec(
            language_id="prisma",
            display_name="Prisma",
            parser_id="language-server:prisma-language-server",
            lsp_language_id="prisma",
            extensions=(".prisma",),
        ),
    )
)
