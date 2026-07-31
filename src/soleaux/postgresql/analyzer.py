"""AnalyzerNode adapter for @libpg-query/parser PostgreSQL 17 output."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import TypeAdapter, ValidationError

from soleaux.contracts.positions import PositionCodec
from soleaux.postgresql.node_runtime import (
    NodeParserParseError,
    NodeParserRuntime,
    ParserDocument,
    ScanToken,
)
from soleaux.structural.projections import AnalyzerNode, AnalyzerParseError

SEMICOLON_TOKEN_TYPE = 59
_MISSING = object()
_OBJECT_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])


class PostgreSqlLocationState(StrEnum):
    """Whether a protobuf node carried a usable native location field."""

    ABSENT = "absent"
    KNOWN = "known"
    UNKNOWN_SENTINEL = "unknown_sentinel"


@dataclass(frozen=True, slots=True)
class PostgreSqlPosition:
    """One PostgreSQL byte offset mapped to code-point and UTF-16 columns."""

    index: int
    line: int
    column: int
    utf16_column: int


@dataclass(frozen=True, slots=True)
class PostgreSqlRange:
    """A half-open source range for the generic analyzer contract."""

    start: PostgreSqlPosition
    end: PostgreSqlPosition


@dataclass(frozen=True, slots=True)
class PostgreSqlAnalyzerNode:
    """A real adaptation from protobuf-shaped JSON into AnalyzerNode."""

    _kind: str
    _text: str
    _range: PostgreSqlRange
    _children: tuple[PostgreSqlAnalyzerNode, ...] = ()
    _fields: tuple[tuple[str, PostgreSqlAnalyzerNode], ...] = ()
    _named: bool = True
    location_state: PostgreSqlLocationState = PostgreSqlLocationState.ABSENT
    native_byte_offset: int | None = None

    def kind(self) -> str:
        return self._kind

    def text(self) -> str:
        return self._text

    def is_named(self) -> bool:
        return self._named

    def children(self) -> Sequence[PostgreSqlAnalyzerNode]:
        return self._children

    def field(self, name: str) -> PostgreSqlAnalyzerNode | None:
        return next((node for field_name, node in self._fields if field_name == name), None)

    def range(self) -> PostgreSqlRange:
        return self._range


@dataclass(frozen=True, slots=True)
class PostgreSqlAnalysis:
    """One parser document and its generic node adaptation from a single parse."""

    root: PostgreSqlAnalyzerNode
    document: ParserDocument


def postgresql_root(
    source: str,
    language: str,
    runtime: NodeParserRuntime,
) -> AnalyzerNode:
    """Parse PostgreSQL source and return the generic analyzer root."""
    return analyze_postgresql(source, language, runtime).root


def analyze_postgresql(
    source: str,
    language: str,
    runtime: NodeParserRuntime,
) -> PostgreSqlAnalysis:
    """Parse once and retain both the typed document and generic node root."""
    if language != "PostgreSQL":
        raise ValueError(f"PostgreSQL analyzer cannot parse {language!r}")
    try:
        document = runtime.analyze(source)
    except NodeParserParseError as exc:
        message = str(exc)
        if exc.cursor_position is not None:
            position = codepoint_cursor_to_position(source, exc.cursor_position)
            message = (
                f"{message} at line {position.line + 1}, "
                f"column {position.column + 1} (byte {position.index})"
            )
        raise AnalyzerParseError(message) from None
    return PostgreSqlAnalysis(
        root=build_postgresql_root(source, document),
        document=document,
    )


def codepoint_cursor_to_position(source: str, cursor: int) -> PostgreSqlPosition:
    """Map a parser error's Unicode-code-point cursor, not a byte offset."""
    if cursor < 0 or cursor > len(source):
        raise AnalyzerParseError(
            f"parser error cursor {cursor} is outside 0..{len(source)} code points"
        )
    byte_offset = len(source[:cursor].encode("utf-8"))
    return _position(PositionCodec(source.encode("utf-8")), byte_offset)


def build_postgresql_root(source: str, document: ParserDocument) -> PostgreSqlAnalyzerNode:
    """Adapt one validated parse/scan document without reparsing source."""
    content = source.encode("utf-8")
    codec = PositionCodec(content)
    raw_statements = _optional_object_list(document.parse_tree.get("stmts"))
    if raw_statements is None:
        raise ValueError("PostgreSQL parse tree does not contain a statements list")
    scanner_ranges = _statement_ranges(document.tokens, len(content))
    statements: list[PostgreSqlAnalyzerNode] = []
    previous_end = 0
    for index, raw_statement in enumerate(raw_statements):
        statement = _mapping(raw_statement, "raw statement")
        start, end = _statement_range(
            statement,
            index=index,
            scanner_ranges=scanner_ranges,
            previous_end=previous_end,
            content_length=len(content),
        )
        previous_end = end
        raw_node = statement.get("stmt")
        node = _build_value(
            raw_node,
            field_name="stmt",
            content=content,
            codec=codec,
            enclosing=(start, end),
            forced_range=(start, end),
        )
        statements.append(node)

    root_range = _range(codec, 0, len(content))
    statement_tuple = tuple(statements)
    statement_container = PostgreSqlAnalyzerNode(
        _kind="stmts",
        _text=source,
        _range=root_range,
        _children=statement_tuple,
        _named=False,
    )
    return PostgreSqlAnalyzerNode(
        _kind="PostgreSQL",
        _text=source,
        _range=root_range,
        _children=statement_tuple,
        _fields=(("stmts", statement_container),),
    )


def _build_value(
    value: object,
    *,
    field_name: str,
    content: bytes,
    codec: PositionCodec,
    enclosing: tuple[int, int],
    forced_range: tuple[int, int] | None = None,
) -> PostgreSqlAnalyzerNode:
    mapping = _optional_mapping(value)
    if mapping is not None:
        if len(mapping) == 1:
            kind, wrapped = next(iter(mapping.items()))
            wrapped_mapping = _optional_mapping(wrapped)
            if kind[:1].isupper() and wrapped_mapping is not None:
                return _build_mapping_node(
                    kind,
                    wrapped_mapping,
                    content=content,
                    codec=codec,
                    enclosing=enclosing,
                    forced_range=forced_range,
                )
        return _build_mapping_node(
            field_name,
            mapping,
            content=content,
            codec=codec,
            enclosing=enclosing,
            forced_range=forced_range,
        )
    items = _optional_object_list(value)
    if items is not None:
        children = tuple(
            _build_value(
                item,
                field_name=field_name,
                content=content,
                codec=codec,
                enclosing=enclosing,
            )
            for item in items
        )
        return PostgreSqlAnalyzerNode(
            _kind=field_name,
            _text="",
            _range=_zero_range(codec, enclosing[0]),
            _children=children,
            _named=False,
        )
    scalar_text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return PostgreSqlAnalyzerNode(
        _kind=field_name,
        _text=scalar_text,
        _range=_zero_range(codec, enclosing[0]),
        _named=False,
    )


def _build_mapping_node(
    kind: str,
    payload: Mapping[str, object],
    *,
    content: bytes,
    codec: PositionCodec,
    enclosing: tuple[int, int],
    forced_range: tuple[int, int] | None,
) -> PostgreSqlAnalyzerNode:
    location = payload.get("location", _MISSING)
    state, native_offset = _location(location, len(content))
    if forced_range is not None:
        node_range = _range(codec, *forced_range)
    elif native_offset is not None:
        node_range = _zero_range(codec, native_offset)
    else:
        node_range = _zero_range(codec, enclosing[0])

    fields: list[tuple[str, PostgreSqlAnalyzerNode]] = []
    for name, value in payload.items():
        if name in {"location", "stmt_location", "stmt_len"}:
            continue
        fields.append(
            (
                name,
                _build_value(
                    value,
                    field_name=name,
                    content=content,
                    codec=codec,
                    enclosing=enclosing,
                ),
            )
        )
    start = node_range.start.index
    end = node_range.end.index
    text = content[start:end].decode("utf-8")
    children = tuple(node for _, node in fields)
    return PostgreSqlAnalyzerNode(
        _kind=kind,
        _text=text,
        _range=node_range,
        _children=children,
        _fields=tuple(fields),
        location_state=state,
        native_byte_offset=native_offset,
    )


def _location(
    value: object,
    content_length: int,
) -> tuple[PostgreSqlLocationState, int | None]:
    if value is _MISSING:
        return PostgreSqlLocationState.ABSENT, None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("PostgreSQL location must be an integer when present")
    if value == -1:
        return PostgreSqlLocationState.UNKNOWN_SENTINEL, None
    if value < 0 or value > content_length:
        raise ValueError(f"PostgreSQL location {value} is outside captured source")
    return PostgreSqlLocationState.KNOWN, value


def _statement_ranges(
    tokens: Sequence[ScanToken],
    content_length: int,
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    last_end: int | None = None
    for token in tokens:
        if token.end > content_length:
            raise ValueError("scanner token range exceeds captured source")
        if token.token_type == SEMICOLON_TOKEN_TYPE:
            if start is not None:
                ranges.append((start, token.end))
                start = None
                last_end = None
            continue
        if start is None:
            start = token.start
        last_end = token.end
    if start is not None and last_end is not None:
        ranges.append((start, last_end))
    return tuple(ranges)


def _statement_range(
    statement: Mapping[str, object],
    *,
    index: int,
    scanner_ranges: Sequence[tuple[int, int]],
    previous_end: int,
    content_length: int,
) -> tuple[int, int]:
    if index < len(scanner_ranges):
        return scanner_ranges[index]
    raw_start = statement.get("stmt_location", _MISSING)
    if raw_start is _MISSING:
        start = 0 if index == 0 else previous_end
    elif isinstance(raw_start, int) and not isinstance(raw_start, bool) and raw_start >= 0:
        start = raw_start
    else:
        raise ValueError("statement location must be a nonnegative byte offset")
    raw_length = statement.get("stmt_len", _MISSING)
    if raw_length is _MISSING:
        end = content_length
    elif isinstance(raw_length, int) and not isinstance(raw_length, bool) and raw_length >= 0:
        end = start + raw_length
    else:
        raise ValueError("statement length must be a nonnegative byte count")
    if start > end or end > content_length:
        raise ValueError("statement range exceeds captured source")
    return start, end


def _position(codec: PositionCodec, byte_offset: int) -> PostgreSqlPosition:
    point = codec.byte_to_point(byte_offset)
    return PostgreSqlPosition(
        index=point.byte,
        line=point.line,
        column=point.column,
        utf16_column=point.utf16_column,
    )


def _range(codec: PositionCodec, start: int, end: int) -> PostgreSqlRange:
    return PostgreSqlRange(start=_position(codec, start), end=_position(codec, end))


def _zero_range(codec: PositionCodec, offset: int) -> PostgreSqlRange:
    position = _position(codec, offset)
    return PostgreSqlRange(start=position, end=position)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    mapping = _optional_mapping(value)
    if mapping is None:
        raise ValueError(f"{label} must be an object")
    return mapping


def _optional_mapping(value: object) -> dict[str, object] | None:
    try:
        return _OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        return None


def _optional_object_list(value: object) -> list[object] | None:
    try:
        return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        return None
