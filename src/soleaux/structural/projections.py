"""Soleaux-owned non-Python projections by analyzer-node traversal (D011).

The host imports registry metadata (projection IDs and digest) only; the
supervised worker executes the extractors. Python projections are owned by
``structural.python`` and ast-grep is reserved for configured matchers and
rewrites. Normalized rows are Soleaux's contract, not analyzer outline parity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from soleaux.frameworks.nextjs import NEXT_CONFIG_PROJECTION, extract_next_config
from soleaux.structural.fragments import EXTRACTOR_VERSION, SyntaxFragment


class Position(Protocol):
    """One endpoint of a source range.

    `index` is the analyzer's native source offset. `line` and `column` are
    zero-based Unicode-code-point coordinates; the worker converts them to
    canonical UTF-8 byte offsets before emitting rows.
    """

    @property
    def index(self) -> int: ...

    @property
    def line(self) -> int: ...

    @property
    def column(self) -> int: ...


class Range(Protocol):
    """A half-open source range with both endpoints resolved."""

    @property
    def start(self) -> Position: ...

    @property
    def end(self) -> Position: ...


class AnalyzerNode(Protocol):
    """The syntax-node surface the projections rely on.

    Structural, not nominal: an implementation satisfies this by shape alone,
    so no analyzer needs an adapter class. ast-grep's `SgNode` already conforms.
    """

    def kind(self) -> str: ...

    def text(self) -> str: ...

    def is_named(self) -> bool: ...

    def children(self) -> Sequence[AnalyzerNode]: ...

    def field(self, name: str) -> AnalyzerNode | None: ...

    def range(self) -> Range: ...


RootFactory = Callable[[str, str], AnalyzerNode]
"""Builds a root node from (source_text, language)."""


class UnsupportedLanguageError(ValueError):
    """Raised when a projection set does not cover a language."""


class AnalyzerParseError(ValueError):
    """Raised when an analyzer cannot parse a source file at all.

    ast-grep reports a syntax error as an ERROR node and still returns a tree,
    but a parser that raises instead needs a typed error so the worker can
    report a parse failure rather than a generic crash.
    """


def _walk(root: AnalyzerNode) -> Iterator[AnalyzerNode]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children()))


def _fragment(
    node: AnalyzerNode,
    *,
    projection: str,
    kind: str,
    name: str | None,
    path: str,
    language: str,
    attributes: dict[str, JsonValue] | None = None,
) -> SyntaxFragment:
    rng = node.range()
    return SyntaxFragment(
        projection=projection,
        kind=kind,
        name=name,
        path=path,
        language=language,
        byte_start=rng.start.index,
        byte_end=rng.end.index,
        start_line=rng.start.line,
        start_column=rng.start.column,
        end_line=rng.end.line,
        end_column=rng.end.column,
        text_preview=node.text()[:120],
        attributes=attributes or {},
    )


def _name_of(node: AnalyzerNode) -> str | None:
    for field_name in ("name", "left"):
        field = node.field(field_name)
        if field is not None:
            return field.text()[:200]
    for child in node.children():
        if child.kind() in {
            "identifier",
            "property_identifier",
            "field_identifier",
            "type_identifier",
        }:
            return child.text()[:200]
    return None


_DECLARATION_KINDS: dict[str, dict[str, str]] = {
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "method_definition": "method",
        "variable_declarator": "variable",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "method_definition": "method",
        "variable_declarator": "variable",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "variable_declarator": "variable",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
        "const_declaration": "constant",
        "var_declaration": "variable",
    },
}

_IMPORT_KINDS: dict[str, dict[str, str]] = {
    "typescript": {"import_statement": "import"},
    "tsx": {"import_statement": "import"},
    "javascript": {"import_statement": "import"},
    "go": {"import_declaration": "import"},
}

_EXPORT_STATEMENT_KINDS: dict[str, dict[str, str]] = {
    "typescript": {"export_statement": "export"},
    "tsx": {"export_statement": "export"},
    "javascript": {"export_statement": "export"},
}

_CALL_KINDS: dict[str, dict[str, str]] = {
    "typescript": {"call_expression": "call_candidate"},
    "tsx": {"call_expression": "call_candidate"},
    "javascript": {"call_expression": "call_candidate"},
    "go": {"call_expression": "call_candidate"},
}

_CLASS_KINDS: dict[str, dict[str, str]] = {
    "typescript": {"class_declaration": "class", "abstract_class_declaration": "class"},
    "tsx": {"class_declaration": "class", "abstract_class_declaration": "class"},
    "javascript": {"class_declaration": "class"},
    "go": {"type_declaration": "type"},
}

_MEMBER_KINDS: dict[str, dict[str, str]] = {
    "typescript": {
        "method_definition": "method",
        "property_declaration": "property",
        "field_definition": "property",
    },
    "tsx": {
        "method_definition": "method",
        "property_declaration": "property",
        "field_definition": "property",
    },
    "javascript": {"method_definition": "method", "field_definition": "property"},
    "go": {"field_declaration": "field", "method_declaration": "method"},
}


#: Languages whose declaration/import/export projections feed the persisted
#: catalog at generation time. Derived from the kind tables so support has one
#: owner; matching is against the snapshot's `structural_language`, lowercased.
SUPPORTED_CATALOG_LANGUAGES: frozenset[str] = (
    frozenset(_DECLARATION_KINDS)
    | frozenset(_IMPORT_KINDS)
    | frozenset(_EXPORT_STATEMENT_KINDS)
    | {"python"}
)


def _language_table(table: dict[str, dict[str, str]], language: str) -> dict[str, str]:
    key = language.lower()
    if key == "typescriptreact":
        key = "tsx"
    try:
        return table[key]
    except KeyError:
        msg = f"no projection support for language {language!r}"
        raise UnsupportedLanguageError(msg) from None


def extract_declarations(
    root: AnalyzerNode,
    *,
    path: str,
    language: str,
    name_query: str | None = None,
    max_results: int | None = None,
) -> list[SyntaxFragment]:
    kinds = _language_table(_DECLARATION_KINDS, language)
    normalized_query = name_query.casefold() if name_query is not None else None
    rows: list[SyntaxFragment] = []
    for node in _walk(root):
        kind = kinds.get(node.kind())
        if kind is None:
            continue
        name = _name_of(node)
        if normalized_query is not None and (
            name is None or normalized_query not in name.casefold()
        ):
            continue
        rows.append(
            _fragment(
                node,
                projection="syntax.declarations",
                kind=kind,
                name=name,
                path=path,
                language=language,
            )
        )
        if max_results is not None and len(rows) >= max_results:
            break
    return rows


def extract_imports(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    kinds = _language_table(_IMPORT_KINDS, language)
    rows: list[SyntaxFragment] = []
    for node in _walk(root):
        kind = kinds.get(node.kind())
        if kind is None:
            continue
        module: str | None = None
        for field_name in ("module_name", "source", "path"):
            field = node.field(field_name)
            if field is not None:
                module = field.text().strip("\"'")[:200]
                break
        if module is None:
            for child in node.children():
                if child.kind() == "dotted_name":
                    module = child.text()[:200]
                    break
        rows.append(
            _fragment(
                node,
                projection="syntax.imports",
                kind=kind,
                name=module,
                path=path,
                language=language,
                attributes={"resolution_status": "candidate"},
            )
        )
    return rows


_EXPORTED_DECLARATION_KINDS = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "function_signature",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }
)


def _export_details(node: AnalyzerNode) -> tuple[str | None, dict[str, JsonValue]]:
    """Resolve the exported identifier and bounded initializer of one export."""
    attributes: dict[str, JsonValue] = {}
    for child in node.children():
        kind = child.kind()
        if kind in {"lexical_declaration", "variable_declaration"}:
            for declarator in child.children():
                if declarator.kind() != "variable_declarator":
                    continue
                value = declarator.field("value")
                if value is not None:
                    attributes["initializer_text"] = value.text()[:200]
                return _name_of(declarator), attributes
            return None, attributes
        if kind in _EXPORTED_DECLARATION_KINDS:
            return _name_of(child), attributes
        if kind == "export_clause":
            names = [
                name
                for specifier in child.children()
                if specifier.kind() == "export_specifier"
                and (name := _name_of(specifier)) is not None
            ]
            if names:
                exported_names: list[JsonValue] = [name for name in names]
                attributes["exported_names"] = exported_names
            return None, attributes
    return None, attributes


def extract_exports(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    rows: list[SyntaxFragment] = []
    export_kinds = _EXPORT_STATEMENT_KINDS.get(language.lower(), {})
    for node in _walk(root):
        kind = export_kinds.get(node.kind())
        if kind is not None:
            name, attributes = _export_details(node)
            rows.append(
                _fragment(
                    node,
                    projection="syntax.exports",
                    kind=kind,
                    name=name,
                    path=path,
                    language=language,
                    attributes=attributes,
                )
            )
            continue
    return rows


def extract_members(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    class_kinds = _language_table(_CLASS_KINDS, language)
    member_kinds = _language_table(_MEMBER_KINDS, language)
    rows: list[SyntaxFragment] = []
    for node in _walk(root):
        class_kind = class_kinds.get(node.kind())
        if class_kind is None:
            continue
        owner = _name_of(node)
        members = list(reversed(node.children()))
        while members:
            member = members.pop()
            member_kind = member_kinds.get(member.kind())
            if member_kind is None:
                members.extend(reversed(member.children()))
                continue
            rows.append(
                _fragment(
                    member,
                    projection="syntax.members",
                    kind=member_kind,
                    name=_name_of(member),
                    path=path,
                    language=language,
                    attributes={"member_of": owner, "owner_kind": class_kind},
                )
            )
    return rows


def extract_spans(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    rows = [
        _fragment(
            root,
            projection="syntax.spans",
            kind="file",
            name=None,
            path=path,
            language=language,
        )
    ]
    for child in root.children():
        if child.is_named():
            rows.append(
                _fragment(
                    child,
                    projection="syntax.spans",
                    kind=child.kind(),
                    name=None,
                    path=path,
                    language=language,
                )
            )
    return rows


def extract_visibility(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    declarations = _language_table(_DECLARATION_KINDS, language)
    rows: list[SyntaxFragment] = []
    lowered = language.lower()
    for node in _walk(root):
        if node.kind() not in declarations:
            continue
        name = _name_of(node)
        if not name:
            continue
        if lowered == "go":
            visibility = "exported" if name[0].isupper() else "unexported"
        else:
            modifier = None
            for child in node.children():
                if child.kind() == "accessibility_modifier":
                    modifier = child.text()
                    break
            visibility = modifier or "unspecified"
        rows.append(
            _fragment(
                node,
                projection="syntax.visibility",
                kind=visibility,
                name=name,
                path=path,
                language=language,
            )
        )
    return rows


def extract_call_sites(root: AnalyzerNode, *, path: str, language: str) -> list[SyntaxFragment]:
    kinds = _language_table(_CALL_KINDS, language)
    rows: list[SyntaxFragment] = []
    for node in _walk(root):
        kind = kinds.get(node.kind())
        if kind is None:
            continue
        callee = node.field("function")
        callee_text = callee.text()[:200] if callee is not None else None
        rows.append(
            _fragment(
                node,
                projection="syntax.call_sites",
                kind=kind,
                name=callee_text,
                path=path,
                language=language,
                attributes={"resolution_status": "candidate"},
            )
        )
    return rows


ProjectionFn = Callable[..., list[SyntaxFragment]]


@dataclass(frozen=True)
class ProjectionEntry:
    """One registered projection."""

    id: str
    extract: ProjectionFn | None
    supported_languages: frozenset[str] | None = None

    def supports_language(self, language: str) -> bool:
        """Whether this projection can produce facts for one analyzer language."""
        if self.supported_languages is None:
            return True
        normalized = language.casefold()
        if normalized == "typescriptreact":
            normalized = "tsx"
        return normalized in self.supported_languages


PROJECTIONS: dict[str, ProjectionEntry] = {
    entry.id: entry
    for entry in (
        ProjectionEntry(
            "syntax.declarations",
            extract_declarations,
            frozenset((*_DECLARATION_KINDS, "python")),
        ),
        ProjectionEntry(
            "syntax.imports",
            extract_imports,
            frozenset((*_IMPORT_KINDS, "python")),
        ),
        ProjectionEntry(
            "syntax.exports",
            extract_exports,
            frozenset((*_EXPORT_STATEMENT_KINDS, "python")),
        ),
        ProjectionEntry(
            "syntax.members",
            extract_members,
            frozenset((*_CLASS_KINDS, "python")),
        ),
        ProjectionEntry("syntax.spans", extract_spans),
        ProjectionEntry(
            "syntax.visibility",
            extract_visibility,
            frozenset((*_DECLARATION_KINDS, "python")),
        ),
        ProjectionEntry(
            "syntax.call_sites",
            extract_call_sites,
            frozenset((*_CALL_KINDS, "python")),
        ),
        ProjectionEntry(
            "syntax.references",
            None,
            frozenset({"python"}),
        ),
        ProjectionEntry(
            NEXT_CONFIG_PROJECTION,
            extract_next_config,
            frozenset({"javascript", "typescript"}),
        ),
    )
}

PROJECTION_IDS: tuple[str, ...] = tuple(PROJECTIONS)


def projection_registry_digest() -> str:
    """Digest over the registered projection IDs and extractor version."""
    payload = json.dumps(
        {"ids": sorted(PROJECTION_IDS), "extractor_version": EXTRACTOR_VERSION},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
