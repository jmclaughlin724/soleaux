"""Canonical LibCST extraction for Python source projections.

One ``parse_module`` call produces every requested Python projection. LibCST
owns Python concrete syntax and metadata; ast-grep remains the explicit
matcher/rewrite engine and is not used to build Python catalog facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from libcst import (
    AnnAssign,
    Assign,
    BaseAssignTargetExpression,
    BaseExpression,
    Call,
    ClassDef,
    CSTNode,
    CSTVisitor,
    Element,
    FunctionDef,
    Import,
    ImportFrom,
    ImportStar,
    List,
    MetadataWrapper,
    Module,
    Name,
    ParserSyntaxError,
    SimpleString,
    Tuple,
    TypeAlias,
    parse_module,
)
from libcst.metadata import (
    ClassScope,
    CodeRange,
    ExpressionContext,
    ExpressionContextProvider,
    GlobalScope,
    ParentNodeProvider,
    PositionProvider,
    QualifiedNameProvider,
    ScopeProvider,
)
from pydantic import JsonValue

from soleaux.contracts.positions import PositionCodec
from soleaux.structural.fragments import MAX_TEXT_PREVIEW, SyntaxFragment

PYTHON_PROJECTIONS = frozenset(
    {
        "syntax.declarations",
        "syntax.imports",
        "syntax.exports",
        "syntax.members",
        "syntax.references",
        "syntax.spans",
        "syntax.visibility",
        "syntax.call_sites",
    }
)


class PythonParseError(ValueError):
    """LibCST could not build a complete module for captured Python source."""


@dataclass(frozen=True)
class PythonExtraction:
    """All selected fragments produced from one parsed Python module."""

    fragments: tuple[SyntaxFragment, ...]
    truncated: bool


def _assigned_names(target: BaseAssignTargetExpression) -> tuple[Name, ...]:
    if isinstance(target, Name):
        return (target,)
    if isinstance(target, (Tuple, List)):
        names: list[Name] = []
        for element in target.elements:
            if isinstance(element, Element) and isinstance(element.value, (Name, Tuple, List)):
                names.extend(_assigned_names(element.value))
        return tuple(names)
    return ()


def _assign_targets(node: Assign | AnnAssign) -> tuple[Name, ...]:
    if isinstance(node, AnnAssign):
        return _assigned_names(node.target)
    names: list[Name] = []
    for target in node.targets:
        names.extend(_assigned_names(target.target))
    return tuple(names)


class _PythonProjectionVisitor(CSTVisitor):
    METADATA_DEPENDENCIES = (
        PositionProvider,
        ParentNodeProvider,
        ExpressionContextProvider,
        ScopeProvider,
        QualifiedNameProvider,
    )

    def __init__(
        self,
        *,
        module: Module,
        content: bytes,
        positions: Mapping[CSTNode, CodeRange],
        path: str,
        projections: frozenset[str],
        name_query: str | None,
        max_declarations: int | None,
    ) -> None:
        self._module = module
        self._codec = PositionCodec(content)
        self._positions = positions
        self._path = path
        self._projections = projections
        self._name_query = name_query.casefold() if name_query is not None else None
        self._max_declarations = max_declarations
        self._rows: dict[str, list[SyntaxFragment]] = {projection: [] for projection in projections}
        self.declarations_truncated = False

    def rows(self, projection_order: Sequence[str]) -> tuple[SyntaxFragment, ...]:
        return tuple(
            fragment
            for projection in projection_order
            for fragment in self._rows.get(projection, ())
        )

    def _qualified_names(self, node: CSTNode) -> list[JsonValue]:
        return [
            name
            for name in sorted(
                qualified.name
                for qualified in self.get_metadata(QualifiedNameProvider, node, set())
            )
        ]

    def _nearest_class(self, node: CSTNode) -> ClassDef | None:
        current = node
        while True:
            parent = self.get_metadata(ParentNodeProvider, current, None)
            if parent is None:
                return None
            if isinstance(parent, ClassDef):
                return parent
            if isinstance(parent, FunctionDef):
                return None
            current = parent

    def _scope_allows_declaration(self, node: CSTNode) -> bool:
        scope = self.get_metadata(ScopeProvider, node)
        return isinstance(scope, (GlobalScope, ClassScope))

    def _fragment(
        self,
        node: CSTNode,
        *,
        projection: str,
        kind: str,
        name: str | None,
        attributes: dict[str, JsonValue] | None = None,
    ) -> SyntaxFragment:
        position = self._positions[node]
        start_line = position.start.line - 1
        end_line = position.end.line - 1
        byte_start = self._codec.point_to_byte(start_line, position.start.column)
        byte_end = self._codec.point_to_byte(end_line, position.end.column)
        return SyntaxFragment(
            projection=projection,
            kind=kind,
            name=name,
            path=self._path,
            language="Python",
            byte_start=byte_start,
            byte_end=byte_end,
            start_line=start_line,
            start_column=position.start.column,
            end_line=end_line,
            end_column=position.end.column,
            text_preview=self._module.code_for_node(node)[:MAX_TEXT_PREVIEW],
            attributes=attributes or {},
        )

    def _append_declaration(
        self,
        node: CSTNode,
        *,
        kind: str,
        name: str,
        qualified_node: CSTNode,
    ) -> None:
        if "syntax.declarations" not in self._projections:
            return
        if self._name_query is not None and self._name_query not in name.casefold():
            return
        rows = self._rows["syntax.declarations"]
        if self._max_declarations is not None and len(rows) >= self._max_declarations:
            self.declarations_truncated = True
            return
        rows.append(
            self._fragment(
                node,
                projection="syntax.declarations",
                kind=kind,
                name=name,
                attributes={"qualified_names": self._qualified_names(qualified_node)},
            )
        )

    def _append_visibility(self, node: CSTNode, *, name: str) -> None:
        if "syntax.visibility" not in self._projections:
            return
        visibility = (
            "mangled"
            if name.startswith("__") and not name.endswith("__")
            else "private"
            if name.startswith("_")
            else "public"
        )
        self._rows["syntax.visibility"].append(
            self._fragment(
                node,
                projection="syntax.visibility",
                kind=visibility,
                name=name,
            )
        )

    def _append_member(self, node: CSTNode, *, kind: str, name: str) -> None:
        if "syntax.members" not in self._projections:
            return
        owner = self._nearest_class(node)
        if owner is None:
            return
        self._rows["syntax.members"].append(
            self._fragment(
                node,
                projection="syntax.members",
                kind=kind,
                name=name,
                attributes={"member_of": owner.name.value, "owner_kind": "class"},
            )
        )

    def visit_Module(self, node: Module) -> None:
        if "syntax.spans" not in self._projections:
            return
        rows = self._rows["syntax.spans"]
        rows.append(
            self._fragment(
                node,
                projection="syntax.spans",
                kind="file",
                name=None,
            )
        )
        for statement in node.body:
            rows.append(
                self._fragment(
                    statement,
                    projection="syntax.spans",
                    kind="statement",
                    name=None,
                )
            )

    def visit_FunctionDef(self, node: FunctionDef) -> None:
        if not self._scope_allows_declaration(node):
            return
        name = node.name.value
        self._append_declaration(
            node,
            kind="function",
            name=name,
            qualified_node=node,
        )
        self._append_visibility(node, name=name)
        self._append_member(node, kind="method", name=name)

    def visit_ClassDef(self, node: ClassDef) -> None:
        if not self._scope_allows_declaration(node):
            return
        name = node.name.value
        self._append_declaration(
            node,
            kind="class",
            name=name,
            qualified_node=node,
        )
        self._append_visibility(node, name=name)
        self._append_member(node, kind="nested_class", name=name)

    def visit_TypeAlias(self, node: TypeAlias) -> None:
        if not self._scope_allows_declaration(node):
            return
        name = node.name.value
        self._append_declaration(
            node,
            kind="type",
            name=name,
            qualified_node=node.name,
        )
        self._append_visibility(node, name=name)
        self._append_member(node, kind="type", name=name)

    def _visit_assignment(self, node: Assign | AnnAssign) -> None:
        if not self._scope_allows_declaration(node):
            return
        names = _assign_targets(node)
        for name_node in names:
            name = name_node.value
            self._append_declaration(
                node,
                kind="variable",
                name=name,
                qualified_node=name_node,
            )
            self._append_visibility(node, name=name)
            self._append_member(node, kind="attribute", name=name)
        if any(name.value == "__all__" for name in names):
            self._append_exports(node)

    def visit_Assign(self, node: Assign) -> None:
        self._visit_assignment(node)

    def visit_AnnAssign(self, node: AnnAssign) -> None:
        self._visit_assignment(node)

    def _append_exports(self, node: Assign | AnnAssign) -> None:
        if "syntax.exports" not in self._projections:
            return
        value = node.value
        exported_names: list[JsonValue] = (
            [name for name in _literal_string_values(value)] if value is not None else []
        )
        self._rows["syntax.exports"].append(
            self._fragment(
                node,
                projection="syntax.exports",
                kind="export",
                name="__all__",
                attributes={"exported_names": exported_names},
            )
        )

    def visit_Import(self, node: Import) -> None:
        if "syntax.imports" not in self._projections:
            return
        imported_names = [self._module.code_for_node(alias.name) for alias in node.names]
        imported_names_json: list[JsonValue] = [name for name in imported_names]
        for module_name in imported_names:
            self._rows["syntax.imports"].append(
                self._fragment(
                    node,
                    projection="syntax.imports",
                    kind="import",
                    name=module_name,
                    attributes={
                        "imported_names": imported_names_json,
                        "resolution_status": "candidate",
                    },
                )
            )

    def visit_ImportFrom(self, node: ImportFrom) -> None:
        if "syntax.imports" not in self._projections:
            return
        relative = "." * len(node.relative)
        module_name = (
            relative + self._module.code_for_node(node.module)
            if node.module is not None
            else relative
        )
        names: list[str]
        if isinstance(node.names, ImportStar):
            names = ["*"]
        else:
            names = [self._module.code_for_node(alias.name) for alias in node.names]
        names_json: list[JsonValue] = [name for name in names]
        self._rows["syntax.imports"].append(
            self._fragment(
                node,
                projection="syntax.imports",
                kind="from_import",
                name=module_name,
                attributes={
                    "imported_names": names_json,
                    "resolution_status": "candidate",
                },
            )
        )

    def visit_Call(self, node: Call) -> None:
        if "syntax.call_sites" not in self._projections:
            return
        self._rows["syntax.call_sites"].append(
            self._fragment(
                node,
                projection="syntax.call_sites",
                kind="call_candidate",
                name=self._module.code_for_node(node.func)[:200],
                attributes={
                    "qualified_names": self._qualified_names(node.func),
                    "resolution_status": "candidate",
                },
            )
        )

    def visit_Name(self, node: Name) -> None:
        if "syntax.references" not in self._projections:
            return
        if self.get_metadata(ExpressionContextProvider, node, None) is not ExpressionContext.LOAD:
            return
        self._rows["syntax.references"].append(
            self._fragment(
                node,
                projection="syntax.references",
                kind="identifier_reference",
                name=node.value,
                attributes={
                    "qualified_names": self._qualified_names(node),
                    "resolution_status": "candidate",
                },
            )
        )


def _literal_string_values(value: BaseExpression) -> Iterable[str]:
    if isinstance(value, SimpleString):
        evaluated = value.evaluated_value
        if isinstance(evaluated, str):
            yield evaluated
        return
    if not isinstance(value, (Tuple, List)):
        return
    for element in value.elements:
        if not isinstance(element, Element):
            continue
        item = element.value
        if not isinstance(item, SimpleString):
            continue
        evaluated = item.evaluated_value
        if isinstance(evaluated, str):
            yield evaluated


def extract_python(
    source: str,
    content: bytes,
    *,
    path: str,
    projections: Sequence[str],
    name_query: str | None = None,
    max_results: int | None = None,
) -> PythonExtraction:
    """Parse once and emit all requested Python projections."""
    unsupported = tuple(
        projection for projection in projections if projection not in PYTHON_PROJECTIONS
    )
    if unsupported:
        message = f"unsupported Python projections: {', '.join(unsupported)}"
        raise ValueError(message)
    try:
        module = parse_module(source)
    except ParserSyntaxError as exc:
        raise PythonParseError(str(exc)) from exc
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    visitor = _PythonProjectionVisitor(
        module=module,
        content=content,
        positions=positions,
        path=path,
        projections=frozenset(projections),
        name_query=name_query,
        max_declarations=max_results,
    )
    wrapper.visit(visitor)
    return PythonExtraction(
        fragments=visitor.rows(projections),
        truncated=visitor.declarations_truncated,
    )
