"""Next.js route conventions ported from the framework's own discovery pass.

Upstream derives routes by scanning the filesystem, not by reading build
manifests, so the same answer is reachable from frozen snapshot bytes with no
dev server, no build, and no Node.js. This module mirrors that algorithm:

- `normalizeAppPath` (`dist/shared/lib/router/utils/app-paths.js`)
- `createValidFileMatcher` (`dist/server/lib/find-page-file.js`)
- `getPageFromPath`, `processAppRoutes`, `processPageRoutes`, and the
  `_`-prefixed private-part prune (`dist/build/route-discovery.js`)

Route-convention helpers are total functions of strings. Next config source is
projected into serializable facts inside the supervised ast-grep worker, and
the host-side detector never loads a parser. `SUPPORTED_CONVENTIONS` names what
this port claims; anything outside it is reported through coverage notes rather
than silently omitted.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import functools
import json
import posixpath
import typing

import soleaux.frameworks.contracts
import soleaux.structural.fragments

FRAMEWORK = "nextjs"
NEXT_CONFIG_PROJECTION = "framework.nextjs_config"


def _is_object_dict(value: object) -> typing.TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


# Upstream default from `dist/server/config-shared.js`.
DEFAULT_PAGE_EXTENSIONS: tuple[str, ...] = ("tsx", "ts", "jsx", "js")
# Upstream `CONFIG_FILES` accepts js/mjs/ts, plus mts only when native type
# stripping is enabled; this static port accepts the permissive superset and
# never accepts cjs/cts.
CONFIG_EXTENSIONS: tuple[str, ...] = ("ts", "mts", "js", "mjs")

NEXT_CONVENTIONS_RANGE = ">=16,<17"
NEXT_CONVENTIONS_VERIFIED_VERSION = "16.3.0-preview.6"
_NEXT_CONVENTIONS_MIN_MAJOR = 16
_NEXT_CONVENTIONS_MAX_MAJOR = 17
SUPPORTED_CONVENTIONS = (
    f"Next.js {NEXT_CONVENTIONS_RANGE}, verified against "
    f"next@{NEXT_CONVENTIONS_VERIFIED_VERSION}: app-router "
    "page/route/layout/default, root not-found, pages-router pages and /api "
    "routes, route groups, parallel slots, dynamic and catch-all segments, "
    "intercepting markers, private _ folders, src/ layouts, and literal "
    "pageExtensions"
)

# Route-affecting config this port identifies structurally but never evaluates.
_UNREAD_CONFIG_OPTIONS: tuple[str, ...] = ("basePath", "rewrites", "redirects", "trailingSlash")

# Metadata conventions are outside the baseline: faithful support needs
# upstream's cached metadata table plus a source parse to decide the dynamic
# `/[__metadata_id__]` variant. Stems are enough to report the omission.
_METADATA_STEMS: frozenset[str] = frozenset(
    {
        "apple-icon",
        "favicon",
        "icon",
        "manifest",
        "opengraph-image",
        "robots",
        "sitemap",
        "twitter-image",
    }
)
_ROUTER_DIRS: tuple[tuple[str, soleaux.frameworks.contracts.RouterKind, bool], ...] = (
    ("app", soleaux.frameworks.contracts.RouterKind.APP, False),
    ("src/app", soleaux.frameworks.contracts.RouterKind.APP, True),
    ("pages", soleaux.frameworks.contracts.RouterKind.PAGES, False),
    ("src/pages", soleaux.frameworks.contracts.RouterKind.PAGES, True),
)

# Upstream skips these synthesized app entries (`dist/build/route-discovery.js`).
# `%5F` is decoded before the key is computed, so an encoded underscore lands on
# the same key and needs no separate mapping.
SKIP_PAGE_KEYS: frozenset[str] = frozenset({"/_not-found/page", "/_global-error/page"})

_VERSION_SUFFIX_CHARACTERS = frozenset(
    ".-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def _version_core(value: str) -> str | None:
    core_and_prerelease, plus, build = value.partition("+")
    if plus and (
        not build
        or "+" in build
        or any(character not in _VERSION_SUFFIX_CHARACTERS for character in build)
    ):
        return None
    core, dash, prerelease = core_and_prerelease.partition("-")
    if dash and (
        not prerelease
        or any(character not in _VERSION_SUFFIX_CHARACTERS for character in prerelease)
    ):
        return None
    return core


def _next_major(
    value: str,
    *,
    allow_range_prefix: bool,
    allow_wildcards: bool,
) -> int | None:
    if allow_range_prefix and value.startswith(("^", "~")):
        value = value[1:]
    if value.startswith("v"):
        value = value[1:]
    core = _version_core(value)
    if core is None:
        return None
    parts = core.split(".")
    if not 1 <= len(parts) <= 3 or not parts[0].isdigit():
        return None
    allowed_wildcards = {"*", "x", "X"}
    if any(
        not part or (not part.isdigit() and (not allow_wildcards or part not in allowed_wildcards))
        for part in parts[1:]
    ):
        return None
    return int(parts[0])


def is_supported_next_version(version: str) -> bool:
    """Whether one exact Next.js version is inside the shipped baseline."""
    major = _next_major(
        version.strip(),
        allow_range_prefix=False,
        allow_wildcards=False,
    )
    if major is None:
        return False
    return _NEXT_CONVENTIONS_MIN_MAJOR <= major < _NEXT_CONVENTIONS_MAX_MAJOR


def _declared_next_major(specifier: str) -> int | None:
    """Return a major only when one dependency specifier establishes it."""
    normalized = specifier.strip()
    if normalized.startswith("workspace:"):
        normalized = normalized.removeprefix("workspace:")
    return _next_major(
        normalized,
        allow_range_prefix=True,
        allow_wildcards=True,
    )


def ensure_leading_slash(path: str) -> str:
    """Mirror `dist/shared/lib/page-path/ensure-leading-slash.js`."""
    return path if path.startswith("/") else f"/{path}"


def is_group_segment(segment: str) -> bool:
    """A route group `(name)`. Upstream requires both the prefix and suffix."""
    return segment.startswith("(") and segment.endswith(")")


def is_parallel_route_segment(segment: str) -> bool:
    """A named slot `@name`. `@children` is upstream's reserved implicit slot."""
    return segment.startswith("@") and segment != "@children"


def normalize_app_path(route: str) -> str:
    """Mirror `normalizeAppPath`: drop groups, slots, and a trailing leaf."""
    segments = route.split("/")
    last = len(segments) - 1
    pathname = ""
    for index, segment in enumerate(segments):
        if not segment or is_group_segment(segment):
            continue
        # Upstream tests `segment[0] === '@'`, so `@children` is dropped here
        # even though `isParallelRouteSegment` would keep it.
        if segment.startswith("@"):
            continue
        if segment in {"page", "route"} and index == last:
            continue
        pathname = f"{pathname}/{segment}"
    return ensure_leading_slash(pathname)


def normalize_layout_route(page_key: str) -> str:
    """Mirror `normalizeLayoutRoute`: normalize, then strip the `/layout` leaf."""
    return ensure_leading_slash(normalize_app_path(page_key).removesuffix("/layout"))


@functools.lru_cache(maxsize=32)
def _longest_first(page_extensions: tuple[str, ...]) -> tuple[str, ...]:
    """Upstream tries longer extensions first so `page.js` beats bare `js`.

    Cached because the order is a per-project constant that would otherwise be
    recomputed for every candidate path.
    """
    return tuple(sorted(page_extensions, key=len, reverse=True))


def get_page_from_path(page_path: str, page_extensions: collections.abc.Sequence[str]) -> str:
    """Mirror `getPageFromPath`: strip one page extension, then `/index`."""
    page = page_path
    for extension in _longest_first(tuple(page_extensions)):
        stripped = page.removesuffix(f".{extension}")
        if stripped != page:
            page = stripped
            break
    page = page.removesuffix("/index")
    return "/" if page == "" else page


def is_reserved_page(page: str) -> bool:
    """Mirror `isReservedPage` from `dist/build/utils.js`."""
    return page.startswith(("/_app", "/_error", "/_document")) or (
        page == "/api" or page.startswith("/api/")
    )


def project_label(project_dir: str) -> str:
    """Human-readable project name for coverage notes.

    One owner, because notes about the same project must read identically
    wherever they are produced.
    """
    return project_dir or "<workspace root>"


def is_metadata_stem(rel_path: str) -> bool:
    """True when a filename stem is an app-router metadata convention."""
    name = posixpath.basename(rel_path)
    stem = name.split(".", 1)[0]
    return any(
        (stem.startswith(candidate) and stem.removeprefix(candidate).isdecimal())
        or stem == candidate
        for candidate in _METADATA_STEMS
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _FileMatcher:
    names: frozenset[str] = frozenset()
    extensions: tuple[str, ...] = ()
    root_only: bool = False
    any_name: bool = False

    def search(self, path: str) -> _FileMatcher | None:
        """Return this matcher when one normalized path satisfies it."""
        normalized = path.replace("\\", "/").lstrip("/")
        if self.root_only and "/" in normalized:
            return None
        filename = normalized.rsplit("/", 1)[-1]
        for extension in self.extensions:
            suffix = f".{extension}"
            if not filename.endswith(suffix):
                continue
            stem = filename[: -len(suffix)]
            if self.any_name or stem in self.names:
                return self
        return None

    def match(self, path: str) -> _FileMatcher | None:
        """Match from the normalized path root."""
        return self.search(path)


@dataclasses.dataclass(frozen=True, slots=True)
class ValidFileMatcher:
    """Mirror `createValidFileMatcher` for one project's `pageExtensions`."""

    page: _FileMatcher
    route: _FileMatcher
    layout: _FileMatcher
    default: _FileMatcher
    root_not_found: _FileMatcher
    any_extension: _FileMatcher

    def kind_for(self, rel_path: str) -> soleaux.frameworks.contracts.RegistrationKind | None:
        """Classify one app-dir-relative path in upstream's precedence order."""
        if self.layout.search(rel_path):
            return soleaux.frameworks.contracts.RegistrationKind.LAYOUT
        if self.default.search(rel_path):
            return soleaux.frameworks.contracts.RegistrationKind.DEFAULT
        if self.route.search(rel_path):
            return soleaux.frameworks.contracts.RegistrationKind.ROUTE_HANDLER
        if self.page.search(rel_path):
            return soleaux.frameworks.contracts.RegistrationKind.PAGE
        # Upstream anchors `not-found` to the app root only.
        if self.root_not_found.match(rel_path.lstrip("/")):
            return soleaux.frameworks.contracts.RegistrationKind.NOT_FOUND
        return None


def create_valid_file_matcher(
    page_extensions: collections.abc.Sequence[str] = DEFAULT_PAGE_EXTENSIONS,
) -> ValidFileMatcher:
    """Build the leaf-and-root matcher set for one project."""
    extensions = tuple(page_extensions)

    def leaf(*names: str) -> _FileMatcher:
        return _FileMatcher(
            names=frozenset(names),
            extensions=extensions,
        )

    return ValidFileMatcher(
        page=leaf("page", "route"),
        route=leaf("route"),
        layout=leaf("layout"),
        default=leaf("default"),
        root_not_found=_FileMatcher(
            names=frozenset({"not-found"}),
            extensions=extensions,
            root_only=True,
        ),
        any_extension=_FileMatcher(
            extensions=extensions,
            any_name=True,
        ),
    )


def _dynamic_segment(segment: str) -> tuple[str, soleaux.frameworks.contracts.SegmentKind] | None:
    if segment.startswith("[[...") and segment.endswith("]]"):
        parameter = segment[5:-2]
        kind = soleaux.frameworks.contracts.SegmentKind.OPTIONAL_CATCH_ALL
    elif segment.startswith("[...") and segment.endswith("]"):
        parameter = segment[4:-1]
        kind = soleaux.frameworks.contracts.SegmentKind.CATCH_ALL
    elif segment.startswith("[") and segment.endswith("]"):
        parameter = segment[1:-1]
        kind = soleaux.frameworks.contracts.SegmentKind.REQUIRED
        if "." in parameter:
            return None
    else:
        return None
    if not parameter or "]" in parameter:
        return None
    return parameter, kind


def dynamic_segments(route: str) -> tuple[soleaux.frameworks.contracts.DynamicSegment, ...]:
    """Describe every parameterized segment of a route pattern."""
    found: list[soleaux.frameworks.contracts.DynamicSegment] = []
    for index, segment in enumerate(route.split("/")):
        parsed = _dynamic_segment(segment)
        if parsed is None:
            continue
        param, kind = parsed
        found.append(
            soleaux.frameworks.contracts.DynamicSegment(
                segment=segment, param=param, kind=kind, index=index
            )
        )
    return tuple(found)


def _reduce_visible(segments: collections.abc.Iterable[str]) -> list[str]:
    """Keep only segments `normalizeAppPath` would emit."""
    return [
        segment
        for segment in segments
        if segment and not is_group_segment(segment) and not segment.startswith("@")
    ]


def _interception_marker(segment: str) -> tuple[str, str] | None:
    # Upstream INTERCEPTION_ROUTE_MARKERS; order matters, first match wins.
    for marker in ("(..)(..)", "(.)", "(..)", "(...)"):
        if segment.startswith(marker) and len(segment) > len(marker):
            return marker, segment[len(marker) :]
    return None


def resolve_interception(page_key: str) -> tuple[str | None, str | None, str]:
    """Resolve an intercepting marker to the route it intercepts.

    Upstream `extractInterceptionRouteInformation` splits the path on the
    matched marker and resolves the intercepted route, while
    `normalizeAppPath` retains the raw marker in the intercepting route
    pattern itself. Returns the marker, the resolved target, and a note when
    the target cannot be resolved.
    """
    segments = page_key.split("/")
    for index, segment in enumerate(segments):
        parsed = _interception_marker(segment)
        if parsed is None:
            continue
        marker, rest = parsed
        base = _reduce_visible(segments[:index])
        if marker == "(...)":
            base = []
        elif marker != "(.)":
            levels = marker.count("(..)")
            if levels > len(base):
                return marker, None, f"intercepting marker {marker} rises above the app root"
            base = base[: len(base) - levels]
        tail = _reduce_visible(segments[index + 1 :])
        target = normalize_app_path("/".join(["", *base, rest, *tail]))
        return marker, target, ""
    return None, None, ""


@dataclasses.dataclass(frozen=True, slots=True)
class NextConfigAnalysis:
    """Static facts extracted from one exported Next.js configuration."""

    page_extensions: tuple[str, ...] | None = None
    page_extensions_declared: bool = False
    unread_options: tuple[str, ...] = ()

    @property
    def page_extensions_source(self) -> str:
        if self.page_extensions is not None:
            return "next.config"
        return "unevaluated" if self.page_extensions_declared else "default"

    @classmethod
    def from_fragment(
        cls, fragment: soleaux.structural.fragments.SyntaxFragment
    ) -> NextConfigAnalysis:
        """Validate the serializable worker attributes at the host boundary."""
        raw_extensions: object = fragment.attributes.get("page_extensions")
        page_extensions: tuple[str, ...] | None = None
        if isinstance(raw_extensions, list) and raw_extensions:
            extension_values: list[str] = []
            for value in raw_extensions:
                if not isinstance(value, str):
                    break
                extension_values.append(value)
            else:
                page_extensions = tuple(extension_values)

        raw_unread: object = fragment.attributes.get("unread_options")
        unread_options_list: list[str] = []
        if isinstance(raw_unread, list):
            for value in raw_unread:
                if isinstance(value, str):
                    unread_options_list.append(value)
        return cls(
            page_extensions=page_extensions,
            page_extensions_declared=fragment.attributes.get("page_extensions_declared") is True,
            unread_options=tuple(unread_options_list),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class NextProject:
    """One detected Next.js project, rooted at a workspace-relative directory."""

    project_dir: str
    router_dirs: tuple[tuple[str, soleaux.frameworks.contracts.RouterKind, bool], ...]
    page_extensions: tuple[str, ...]
    proxy_path: str | None

    @property
    def label(self) -> str:
        """Workspace-relative label for coverage notes."""
        return project_label(self.project_dir)


class _AstNode(typing.Protocol):
    """The ast-grep node surface used inside the supervised worker."""

    def kind(self) -> str: ...

    def text(self) -> str: ...

    def is_named(self) -> bool: ...

    def children(self) -> list[_AstNode]: ...

    def field(self, name: str) -> _AstNode | None: ...

    def range(self) -> typing.Any: ...


def _walk_ast(root: _AstNode) -> collections.abc.Iterable[_AstNode]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children()))


def _simple_string(node: _AstNode | None) -> str | None:
    if node is None or node.kind() != "string":
        return None
    raw = node.text()
    if len(raw) < 2 or raw[0] not in {'"', "'"} or raw[-1] != raw[0]:
        return None
    value = raw[1:-1]
    if not value or any(not (character.isalnum() or character == "_") for character in value):
        return None
    return value


def _property_name(node: _AstNode) -> str | None:
    if node.kind() == "pair":
        key = node.field("key")
        if key is None:
            return None
        if key.kind() in {"identifier", "property_identifier"}:
            return key.text()
        return _simple_string(key)
    if node.kind() in {"shorthand_property_identifier", "shorthand_property_identifier_pattern"}:
        return node.text()
    return None


def _literal_extension_array(node: _AstNode | None) -> tuple[str, ...] | None:
    if node is None or node.kind() != "array":
        return None
    values: list[str] = []
    for child in node.children():
        if not child.is_named() or child.kind() == "comment":
            continue
        value = _simple_string(child)
        if value is None:
            return None
        values.append(value)
    return tuple(values) if values else None


def _variable_bindings(root: _AstNode) -> dict[str, _AstNode]:
    bindings: dict[str, _AstNode] = {}
    ambiguous: set[str] = set()
    for node in _walk_ast(root):
        if node.kind() != "variable_declarator":
            continue
        name = node.field("name")
        value = node.field("value")
        if name is None or value is None or name.kind() != "identifier":
            continue
        identifier = name.text()
        if identifier in bindings:
            bindings.pop(identifier)
            ambiguous.add(identifier)
        elif identifier not in ambiguous:
            bindings[identifier] = value
    return bindings


def _unwrap_config_value(
    node: _AstNode,
    bindings: collections.abc.Mapping[str, _AstNode],
) -> _AstNode:
    seen_identifiers: set[str] = set()
    while True:
        if node.kind() == "identifier":
            identifier = node.text()
            if identifier in seen_identifiers or identifier not in bindings:
                return node
            seen_identifiers.add(identifier)
            node = bindings[identifier]
            continue
        if node.kind() not in {
            "as_expression",
            "parenthesized_expression",
            "satisfies_expression",
            "type_assertion",
        }:
            return node
        named = [child for child in node.children() if child.is_named()]
        if not named:
            return node
        node = named[-1] if node.kind() == "type_assertion" else named[0]


def _exported_config_value(root: _AstNode) -> _AstNode | None:
    bindings = _variable_bindings(root)
    for statement in root.children():
        if statement.kind() == "export_statement" and any(
            child.kind() == "default" for child in statement.children()
        ):
            value = statement.field("value")
            if value is not None:
                return _unwrap_config_value(value, bindings)
        if statement.kind() != "expression_statement":
            continue
        for node in _walk_ast(statement):
            if node.kind() != "assignment_expression":
                continue
            left = node.field("left")
            right = node.field("right")
            if left is not None and right is not None and left.text() == "module.exports":
                return _unwrap_config_value(right, bindings)
    return None


def _declared_config_options(root: _AstNode) -> set[str]:
    names: set[str] = set()
    for node in _walk_ast(root):
        name = _property_name(node)
        if name is not None:
            names.add(name)
    return names


def _analyze_config_object(root: _AstNode) -> NextConfigAnalysis:
    page_extensions: tuple[str, ...] | None = None
    page_extensions_declared = False
    page_extensions_uncertain = False
    unread_options: set[str] = set()

    for member in root.children():
        if not member.is_named() or member.kind() == "comment":
            continue
        name = _property_name(member)
        if name in _UNREAD_CONFIG_OPTIONS:
            unread_options.add(name)
        if name == "pageExtensions":
            page_extensions_declared = True
            page_extensions = _literal_extension_array(member.field("value"))
            page_extensions_uncertain = page_extensions is None
            continue
        if page_extensions_declared and (member.kind() == "spread_element" or name is None):
            page_extensions_uncertain = True

    if page_extensions_uncertain:
        page_extensions = None
    return NextConfigAnalysis(
        page_extensions=page_extensions,
        page_extensions_declared=page_extensions_declared,
        unread_options=tuple(sorted(unread_options)),
    )


def extract_next_config(
    root: _AstNode,
    *,
    path: str,
    language: str,
) -> list[soleaux.structural.fragments.SyntaxFragment]:
    """Project one Next config into compact static facts inside the AST worker."""
    if language.lower() not in {"javascript", "typescript"}:
        return []

    config_value = _exported_config_value(root)
    if config_value is None:
        analysis = NextConfigAnalysis()
        anchor = root
    elif config_value.kind() == "object":
        analysis = _analyze_config_object(config_value)
        anchor = config_value
    else:
        declared = _declared_config_options(config_value)
        analysis = NextConfigAnalysis(
            page_extensions_declared="pageExtensions" in declared,
            unread_options=tuple(sorted(declared.intersection(_UNREAD_CONFIG_OPTIONS))),
        )
        anchor = config_value

    node_range = anchor.range()
    return [
        soleaux.structural.fragments.SyntaxFragment(
            projection=NEXT_CONFIG_PROJECTION,
            kind="next_config",
            name=None,
            path=path,
            language=language,
            byte_start=node_range.start.index,
            byte_end=node_range.end.index,
            start_line=node_range.start.line,
            start_column=node_range.start.column,
            end_line=node_range.end.line,
            end_column=node_range.end.column,
            text_preview=anchor.text()[:120],
            attributes={
                "page_extensions": (
                    list(analysis.page_extensions) if analysis.page_extensions is not None else None
                ),
                "page_extensions_declared": analysis.page_extensions_declared,
                "unread_options": list(analysis.unread_options),
            },
        )
    ]


def is_next_config_path(path: str) -> bool:
    """Whether a captured path is a supported Next.js configuration module."""
    name = posixpath.basename(path)
    return name.startswith("next.config.") and name.rsplit(".", 1)[-1] in CONFIG_EXTENSIONS


def _next_dependency(manifest: bytes) -> tuple[bool, str | None]:
    """Whether and how a `package.json` declares `next`.

    Takes bytes: `json.loads` decodes them itself, so the caller never has to
    decode the whole snapshot to reach a handful of manifests.
    """
    try:
        parsed: object = json.loads(manifest)
    except ValueError:
        return False, None
    if not _is_object_dict(parsed):
        return False, None
    sections = parsed
    for field_name in ("dependencies", "devDependencies", "peerDependencies"):
        section = sections.get(field_name)
        if not _is_object_dict(section):
            continue
        dependencies = section
        if "next" not in dependencies:
            continue
        specifier = dependencies["next"]
        return True, specifier if isinstance(specifier, str) else None
    return False, None


def _directory_of(path: str) -> str:
    parent = posixpath.dirname(path)
    return "" if parent == "." else parent


def discover_projects(
    paths: collections.abc.Sequence[str],
    contents: collections.abc.Mapping[str, bytes],
    config_analyses: collections.abc.Mapping[str, NextConfigAnalysis] | None = None,
) -> tuple[tuple[NextProject, ...], tuple[str, ...]]:
    """Find Next.js projects from captured paths and manifest/config text.

    A directory is a project when it carries a Next signal (a `next`
    dependency or a `next.config.*`) and owns a router directory.
    """
    path_set = set(paths)
    analyses = config_analyses or {}
    # Directory -> its `next.config.*` path, if any. Key presence is the Next
    # signal; the value is needed only to report a config that owns no router.
    candidates: dict[str, str | None] = {}
    next_specifiers: dict[str, str] = {}

    for path in paths:
        directory = _directory_of(path)
        if is_next_config_path(path):
            candidates[directory] = path
        elif posixpath.basename(path) == "package.json":
            declares_next, specifier = _next_dependency(contents.get(path, b""))
            if not declares_next:
                continue
            candidates.setdefault(directory, None)
            if specifier is not None:
                next_specifiers[directory] = specifier

    projects: list[NextProject] = []
    notes: list[str] = []
    # One pass collecting every directory that holds a file, so the router-presence
    # test below is a set lookup rather than a scan of the whole read set per
    # candidate directory per router name.
    populated_dirs: set[str] = set()
    for path in paths:
        parent = posixpath.dirname(path)
        while parent:
            populated_dirs.add(parent)
            parent = posixpath.dirname(parent)

    for directory in sorted(candidates):
        config_path = candidates[directory]
        prefix = f"{directory}/" if directory else ""
        present = [
            (router_dir, router, is_src)
            for name, router, is_src in _ROUTER_DIRS
            if (router_dir := f"{prefix}{name}") in populated_dirs
        ]
        label = project_label(directory)
        if not present:
            if config_path is not None:
                notes.append(
                    f"{label} declares a Next.js configuration but owns no "
                    "app/ or pages/ directory; no registrations enumerated"
                )
            continue

        if (specifier := next_specifiers.get(directory)) is not None:
            declared_major = _declared_next_major(specifier)
            if declared_major is not None and not (
                _NEXT_CONVENTIONS_MIN_MAJOR <= declared_major < _NEXT_CONVENTIONS_MAX_MAJOR
            ):
                notes.append(
                    f"{label} declares next@{specifier}, outside the supported "
                    f"{NEXT_CONVENTIONS_RANGE} conventions baseline verified against "
                    f"next@{NEXT_CONVENTIONS_VERIFIED_VERSION}; enumeration may be incomplete"
                )

        analysis = analyses.get(config_path) if config_path is not None else NextConfigAnalysis()
        if analysis is None:
            page_extensions = DEFAULT_PAGE_EXTENSIONS
            source = "unavailable"
            unread = ()
        else:
            page_extensions = analysis.page_extensions or DEFAULT_PAGE_EXTENSIONS
            source = analysis.page_extensions_source
            unread = analysis.unread_options
        if source == "unevaluated":
            notes.append(
                f"{label} sets pageExtensions to a non-literal value; enumeration "
                f"assumed {','.join(DEFAULT_PAGE_EXTENSIONS)}"
            )
        elif source == "unavailable":
            notes.append(
                f"{label} has a Next.js configuration that was not structurally analyzed; "
                f"enumeration assumed {','.join(DEFAULT_PAGE_EXTENSIONS)}"
            )
        if unread:
            notes.append(
                f"{label} declares {', '.join(unread)}; emitted patterns are "
                "filesystem routes and exclude configured rewriting"
            )

        # `app/` wins over `src/app/` when a project somehow carries both.
        selected: list[tuple[str, soleaux.frameworks.contracts.RouterKind, bool]] = []
        for router in (
            soleaux.frameworks.contracts.RouterKind.APP,
            soleaux.frameworks.contracts.RouterKind.PAGES,
        ):
            matching = [entry for entry in present if entry[1] is router]
            if len(matching) > 1:
                kept = min(matching, key=lambda entry: entry[2])
                notes.append(
                    f"{label} contains both {router.value}/ and src/{router.value}/; "
                    f"enumerated {kept[0]} only"
                )
                selected.append(kept)
            elif matching:
                selected.append(matching[0])

        # Upstream resolves `(?:src/)?{proxy,middleware}` on the pageExtensions
        # axis and rejects a project that carries both stems.
        proxy_hits = {
            stem: [
                path
                for base in (prefix, f"{prefix}src/")
                for extension in page_extensions
                if (path := f"{base}{stem}.{extension}") in path_set
            ]
            for stem in ("proxy", "middleware")
        }
        proxy_path: str | None = None
        if proxy_hits["proxy"] and proxy_hits["middleware"]:
            notes.append(
                f"{label} declares both proxy and middleware; upstream rejects "
                "the combination, enumerated proxy only"
            )
            proxy_path = proxy_hits["proxy"][0]
        else:
            proxy_path = next(
                (path for hits in proxy_hits.values() for path in hits),
                None,
            )
        projects.append(
            NextProject(
                project_dir=directory,
                router_dirs=tuple(selected),
                page_extensions=page_extensions,
                proxy_path=proxy_path,
            )
        )
    return tuple(projects), tuple(notes)


def _router_prefixes(
    projects: collections.abc.Sequence[NextProject],
) -> tuple[tuple[str, NextProject, str, soleaux.frameworks.contracts.RouterKind], ...]:
    """Build the prefix table once per pass, innermost project first.

    Ordering by descending project depth makes innermost-wins fall out of the
    first match, so the per-path lookup stops early instead of scanning every
    project and rebuilding a prefix string on each comparison.
    """
    entries = [
        (f"{router_dir}/", project, router_dir, router)
        for project in projects
        for router_dir, router, _is_src in project.router_dirs
    ]
    entries.sort(key=lambda entry: len(entry[1].project_dir), reverse=True)
    return tuple(entries)


def _owning_router(
    path: str,
    prefixes: collections.abc.Sequence[
        tuple[str, NextProject, str, soleaux.frameworks.contracts.RouterKind]
    ],
) -> tuple[NextProject, str, soleaux.frameworks.contracts.RouterKind] | None:
    """Attribute a router file to the innermost project that contains it.

    Returns the router directory it matched so the caller does not repeat the
    prefix scan to recover it.
    """
    for prefix, project, router_dir, router in prefixes:
        if path.startswith(prefix):
            return (project, router_dir, router)
    return None


def _has_private_part(rel_path: str) -> bool:
    """Upstream prunes any path part starting with `_` inside the app dir."""
    return any(part.startswith("_") for part in rel_path.strip("/").split("/"))


def _app_registration(
    project: NextProject,
    matcher: ValidFileMatcher,
    path: str,
    rel_path: str,
) -> soleaux.frameworks.contracts.Registration | None:
    kind = matcher.kind_for(rel_path)
    if kind is None:
        return None
    page_key = get_page_from_path(rel_path.replace("%5F", "_"), project.page_extensions)
    if page_key in SKIP_PAGE_KEYS:
        return None

    if kind is soleaux.frameworks.contracts.RegistrationKind.DEFAULT:
        # `default` is a slot fallback. Upstream uses it only to derive slot
        # metadata and never exposes it as an addressable route.
        route = None
    elif kind is soleaux.frameworks.contracts.RegistrationKind.LAYOUT:
        route = normalize_layout_route(page_key)
    else:
        route = normalize_app_path(page_key)

    segments = page_key.split("/")
    marker, target, note = resolve_interception(page_key)
    return soleaux.frameworks.contracts.Registration(
        framework=FRAMEWORK,
        project_dir=project.project_dir,
        path=path,
        kind=kind,
        route=route,
        router=soleaux.frameworks.contracts.RouterKind.APP,
        dynamic_segments=dynamic_segments(route) if route is not None else (),
        route_groups=tuple(segment for segment in segments if is_group_segment(segment)),
        parallel_slots=tuple(segment for segment in segments if is_parallel_route_segment(segment)),
        intercepting_marker=marker,
        intercepting_target=target,
        note=note
        or (
            "upstream normalizeAppPath retains the intercepting marker in this pattern"
            if marker and route is not None
            else ""
        ),
        confidence=0.6 if marker else 1.0,
    )


def _pages_registration(
    project: NextProject,
    matcher: ValidFileMatcher,
    path: str,
    rel_path: str,
) -> soleaux.frameworks.contracts.Registration | None:
    if not matcher.any_extension.search(rel_path):
        return None
    # Upstream createPagesMapping skips declaration files when TypeScript is a
    # page extension, so `pages/foo.d.ts` never registers as `/foo.d`.
    if "ts" in project.page_extensions and rel_path.endswith(".d.ts"):
        return None
    route = get_page_from_path(rel_path, project.page_extensions)
    is_api = route.startswith("/api/")
    if not is_api and is_reserved_page(route):
        return None
    return soleaux.frameworks.contracts.Registration(
        framework=FRAMEWORK,
        project_dir=project.project_dir,
        path=path,
        kind=soleaux.frameworks.contracts.RegistrationKind.ROUTE_HANDLER
        if is_api
        else soleaux.frameworks.contracts.RegistrationKind.PAGE,
        route=route,
        router=soleaux.frameworks.contracts.RouterKind.PAGES,
        dynamic_segments=dynamic_segments(route),
    )


class NextDetector:
    """Enumerate Next.js registrations from frozen snapshot inputs."""

    def __init__(
        self,
        config_analyses: collections.abc.Mapping[str, NextConfigAnalysis] | None = None,
    ) -> None:
        self._config_analyses = dict(config_analyses or {})

    @property
    def framework(self) -> str:
        return FRAMEWORK

    @property
    def baseline(self) -> str:
        return SUPPORTED_CONVENTIONS

    def enumerate(
        self,
        paths: collections.abc.Sequence[str],
        contents: collections.abc.Mapping[str, bytes],
    ) -> tuple[tuple[soleaux.frameworks.contracts.Registration, ...], tuple[str, ...]]:
        projects, discovery_notes = discover_projects(paths, contents, self._config_analyses)
        notes = list(discovery_notes)
        if not projects:
            return (), tuple(notes)

        matchers = {
            project.project_dir: create_valid_file_matcher(project.page_extensions)
            for project in projects
        }
        registrations: list[soleaux.frameworks.contracts.Registration] = []
        metadata_skipped: dict[str, int] = {}
        prefixes = _router_prefixes(projects)

        for path in paths:
            owned = _owning_router(path, prefixes)
            if owned is None:
                continue
            project, router_dir, router = owned
            rel_path = path[len(router_dir) :]
            matcher = matchers[project.project_dir]

            if router is soleaux.frameworks.contracts.RouterKind.APP:
                if _has_private_part(rel_path):
                    continue
                if is_metadata_stem(rel_path) and matcher.kind_for(rel_path) is None:
                    metadata_skipped[project.label] = metadata_skipped.get(project.label, 0) + 1
                    continue
                registration = _app_registration(project, matcher, path, rel_path)
            else:
                registration = _pages_registration(project, matcher, path, rel_path)
            if registration is not None:
                registrations.append(registration)

        for project in projects:
            if project.proxy_path is not None:
                registrations.append(
                    soleaux.frameworks.contracts.Registration(
                        framework=FRAMEWORK,
                        project_dir=project.project_dir,
                        path=project.proxy_path,
                        kind=soleaux.frameworks.contracts.RegistrationKind.PROXY,
                        note="matcher config is declared in source and not evaluated here",
                        confidence=0.6,
                    )
                )

        for label, count in sorted(metadata_skipped.items()):
            notes.append(
                f"{label} contains {count} metadata route file(s); metadata "
                "conventions are outside this port's supported baseline"
            )
        notes.extend(_duplicate_route_notes(registrations))
        return tuple(sorted(registrations, key=lambda item: item.sort_key)), tuple(notes)


# Only these kinds are directly addressable, so only these can genuinely collide.
# A layout and a page at the same route are the normal case, and upstream also
# documents `page` and `route` at one segment as a conflict.
_ADDRESSABLE_KINDS: frozenset[soleaux.frameworks.contracts.RegistrationKind] = frozenset(
    {
        soleaux.frameworks.contracts.RegistrationKind.PAGE,
        soleaux.frameworks.contracts.RegistrationKind.ROUTE_HANDLER,
    }
)


def _duplicate_route_notes(
    registrations: collections.abc.Sequence[soleaux.frameworks.contracts.Registration],
) -> list[str]:
    """Report an addressable route defined twice *within* one project.

    Two scoping rules keep this from crying wolf: it is per project, because two
    apps in one workspace legitimately both serve `/`, and it covers only
    addressable kinds, because every app has a root layout beside its root page.
    """
    seen: dict[tuple[str, str, str], list[str]] = {}
    for registration in registrations:
        if registration.route is None or registration.router is None:
            continue
        if registration.kind not in _ADDRESSABLE_KINDS:
            continue
        key = (registration.project_dir, registration.router.value, registration.route)
        seen.setdefault(key, []).append(registration.path)
    notes: list[str] = []
    for (project_dir, _router, route), owners in sorted(seen.items()):
        if len(owners) > 1:
            label = project_label(project_dir)
            notes.append(
                f"{label} defines {route} in {len(owners)} files; Next.js resolves exactly one"
            )
    return notes
