"""Supervised structural worker entrypoint (D011).

Runs as `python -I -m soleaux.structural.worker` inside the one lazy supervised
process. Receives captured bytes, language, and registered projection/rule IDs
over bounded JSON-lines IPC and returns only serializable rows. ast-grep-py is
imported lazily by its registered root factory, so importing this module is free
and the host process never loads a parser at startup.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import pathlib
import resource
import sys
import time
from collections.abc import Mapping
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from soleaux.catalog.postgresql import (
    PostgreSqlCatalogContext,
    extract_postgresql_catalog,
)
from soleaux.contracts.positions import PositionCodec
from soleaux.postgresql.analyzer import PostgreSqlAnalysis, analyze_postgresql, postgresql_root
from soleaux.postgresql.node_runtime import (
    NodeParserError,
    NodeParserRuntime,
    NodeParserUnavailableError,
    resolve_parser_installation,
)
from soleaux.structural.fragments import (
    STRUCTURAL_WORKER_CAPABILITIES,
    FragmentDiagnostic,
    SyntaxFragment,
)
from soleaux.structural.projections import (
    PROJECTIONS,
    AnalyzerNode,
    AnalyzerParseError,
    RootFactory,
    UnsupportedLanguageError,
)
from soleaux.structural.python import PythonParseError, extract_python

if TYPE_CHECKING:
    from ast_grep_py import Config, SgNode

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_CONTENT_B64 = 6 * 1024 * 1024
MAX_FRAGMENTS = 1000
AST_GREP_RUNTIME_VERSION = version("ast-grep-py")


def _ast_grep_root(source: str, language: str) -> SgNode:
    from ast_grep_py import SgRoot

    return SgRoot(source, language).root()


_postgresql_runtime: NodeParserRuntime | None = None


def _postgresql_analysis(source: str, language: str) -> PostgreSqlAnalysis:
    return analyze_postgresql(source, language, _postgresql_runtime_for_request())


def _postgresql_root(source: str, language: str) -> AnalyzerNode:
    return postgresql_root(source, language, _postgresql_runtime_for_request())


def _postgresql_runtime_for_request() -> NodeParserRuntime:
    global _postgresql_runtime
    if _postgresql_runtime is None:
        installation = resolve_parser_installation()
        if installation is None:
            raise NodeParserUnavailableError(
                "managed @libpg-query/parser@17.6.10 is not provisioned"
            )
        _postgresql_runtime = NodeParserRuntime(installation)
    return _postgresql_runtime


def _close_postgresql_runtime() -> None:
    global _postgresql_runtime
    runtime = _postgresql_runtime
    _postgresql_runtime = None
    if runtime is not None:
        runtime.close()


ROOT_FACTORIES: dict[str, RootFactory] = {
    **{
        language: _ast_grep_root for language in ("Go", "JavaScript", "Python", "Tsx", "TypeScript")
    },
    "PostgreSQL": _postgresql_root,
}


class FrameTooLargeError(Exception):
    """An IPC frame exceeded the byte cap."""


def _object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    mapping = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast("list[object]", value)


def _wire_integer(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str | int | float):
        return int(value)
    raise TypeError(f"expected a JSON number or numeric string, got {type(value).__name__}")


def _read_frame(stream: BinaryIO) -> bytes | None:
    line = stream.readline(MAX_FRAME_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_FRAME_BYTES:
        raise FrameTooLargeError("frame exceeds the 8 MiB cap")
    return line


def _write_frame(stream: BinaryIO, payload: dict[str, Any]) -> None:
    frame = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    stream.write(frame)
    stream.flush()


def _max_rss_bytes() -> int:
    """Peak resident set size of this worker, normalized to bytes.

    On Linux with THP in always mode, ru_maxrss counts whole 2 MiB pages for
    sparsely touched regions and overstates residency by an order of magnitude;
    smaps_rollup reports true per-page residency instead.
    """
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError, ValueError, IndexError):
            for line in (
                pathlib.Path("/proc/self/smaps_rollup").read_text(encoding="utf-8").splitlines()
            ):
                if line.startswith("Rss:"):
                    return int(line.split()[1]) * 1024
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _serialize_with_byte_offsets(
    row: SyntaxFragment | FragmentDiagnostic,
    position_codec: PositionCodec,
) -> dict[str, Any]:
    payload = row.model_dump(mode="json")
    payload["byte_start"] = position_codec.point_to_byte(row.start_line, row.start_column)
    payload["byte_end"] = position_codec.point_to_byte(row.end_line, row.end_column)
    return payload


def _syntax_diagnostics(
    root: AnalyzerNode,
    *,
    path: str,
    language: str,
) -> list[FragmentDiagnostic]:
    rows: list[FragmentDiagnostic] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind() == "ERROR":
            rng = node.range()
            rows.append(
                FragmentDiagnostic(
                    path=path,
                    language=language,
                    severity="error",
                    message="syntax error; partial structural facts returned",
                    byte_start=rng.start.index,
                    byte_end=rng.end.index,
                    start_line=rng.start.line,
                    start_column=rng.start.column,
                    end_line=rng.end.line,
                    end_column=rng.end.column,
                )
            )
        stack.extend(node.children())
    return rows


def _extract(
    request: dict[str, Any],
    *,
    root_factories: Mapping[str, RootFactory],
    parses: int,
) -> dict[str, Any]:
    from soleaux.structural.rules import evaluate_packaged_rule, load_packaged_rule

    raw_symbol_query: object = request.get("symbol_query")
    raw_symbol_max_results: object = request.get("symbol_max_results")
    if (raw_symbol_query is None) != (raw_symbol_max_results is None):
        return {
            "status": "error",
            "error": {
                "type": "bad_symbol_search",
                "message": "symbol_query and symbol_max_results must be provided together",
            },
        }
    symbol_search: tuple[str, int] | None = None
    if raw_symbol_query is not None:
        if (
            not isinstance(raw_symbol_query, str)
            or not raw_symbol_query
            or not isinstance(raw_symbol_max_results, int)
            or isinstance(raw_symbol_max_results, bool)
            or not 1 <= raw_symbol_max_results <= MAX_FRAGMENTS
        ):
            return {
                "status": "error",
                "error": {
                    "type": "bad_symbol_search",
                    "message": "symbol search requires a query and a result limit from 1 to 1000",
                },
            }
        symbol_search = (raw_symbol_query, raw_symbol_max_results)
        if request.get("projections") != ["syntax.declarations"] or request.get("rules"):
            return {
                "status": "error",
                "error": {
                    "type": "bad_symbol_search",
                    "message": "symbol search supports only the syntax.declarations projection",
                },
            }

    content_b64 = request["content_b64"]
    if len(content_b64) > MAX_CONTENT_B64:
        return {
            "status": "error",
            "error": {"type": "content_too_large", "message": "content exceeds the 4 MiB cap"},
        }
    try:
        content = base64.b64decode(content_b64, validate=True)
    except binascii.Error, ValueError:
        return {
            "status": "error",
            "error": {"type": "bad_content", "message": "content_b64 is not valid base64"},
        }
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "status": "error",
            "error": {"type": "bad_encoding", "message": "content is not valid UTF-8"},
        }
    language = str(request["language"])
    path = str(request["path"])
    raw_postgresql_catalog = request.get("postgresql_catalog")
    postgresql_catalog: PostgreSqlCatalogContext | None = None
    if raw_postgresql_catalog is not None:
        if language != "PostgreSQL":
            return {
                "status": "error",
                "error": {
                    "type": "bad_postgresql_catalog",
                    "message": "PostgreSQL catalog context requires PostgreSQL source",
                },
            }
        try:
            postgresql_catalog = PostgreSqlCatalogContext.model_validate(raw_postgresql_catalog)
        except ValueError:
            return {
                "status": "error",
                "error": {
                    "type": "bad_postgresql_catalog",
                    "message": "PostgreSQL catalog context is invalid",
                },
            }
        if postgresql_catalog.path != path:
            return {
                "status": "error",
                "error": {
                    "type": "bad_postgresql_catalog",
                    "message": "PostgreSQL catalog path does not match the request",
                },
            }
    projection_ids = tuple(str(value) for value in request.get("projections", []))
    unknown_projection = next(
        (projection_id for projection_id in projection_ids if projection_id not in PROJECTIONS),
        None,
    )
    if unknown_projection is not None:
        return {
            "status": "error",
            "error": {
                "type": "unknown_projection",
                "message": f"unregistered projection {unknown_projection!r}",
            },
        }
    unsupported_projections = tuple(
        projection_id
        for projection_id in projection_ids
        if not PROJECTIONS[projection_id].supports_language(language)
    )
    if (
        projection_ids
        and len(unsupported_projections) == len(projection_ids)
        and not request.get("rules")
        and postgresql_catalog is None
    ):
        return {
            "status": "ok",
            "fragments": [],
            "diagnostics": [],
            "stats": {
                "parses": parses,
                "parse_ms": 0.0,
                "fragment_count": 0,
                "truncated": False,
                "unsupported": list(unsupported_projections),
                "max_rss_bytes": _max_rss_bytes(),
            },
        }
    root_factory = root_factories.get(language)
    if root_factory is None:
        unsupported = [
            str(identifier)
            for key in ("projections", "rules")
            for identifier in request.get(key, [])
        ]
        return {
            "status": "ok",
            "fragments": [],
            "diagnostics": [],
            "stats": {
                "parses": parses,
                "parse_ms": 0.0,
                "fragment_count": 0,
                "truncated": False,
                "unsupported": unsupported,
                "max_rss_bytes": _max_rss_bytes(),
            },
        }
    started = time.perf_counter()
    postgresql_analysis: PostgreSqlAnalysis | None = None
    root: AnalyzerNode | None = None
    python_fragments: tuple[SyntaxFragment, ...] = ()
    symbol_truncated = False
    try:
        if language == "Python" and projection_ids:
            python_extraction = extract_python(
                text,
                content,
                path=path,
                projections=projection_ids,
                name_query=symbol_search[0] if symbol_search is not None else None,
                max_results=symbol_search[1] if symbol_search is not None else None,
            )
            python_fragments = python_extraction.fragments
            symbol_truncated = python_extraction.truncated
            if request.get("rules"):
                root = _ast_grep_root(text, language)
        elif postgresql_catalog is not None:
            postgresql_analysis = _postgresql_analysis(text, language)
            root = postgresql_analysis.root
        else:
            root = root_factory(text, language)
    except (AnalyzerParseError, PythonParseError) as exc:
        return {
            "status": "error",
            "error": {
                "type": "parse_error",
                "message": str(exc),
                "language": language,
                "path": path,
            },
        }
    except NodeParserError as exc:
        return {
            "status": "error",
            "error": {
                "type": "parser_unavailable",
                "message": str(exc),
                "language": language,
                "path": path,
            },
        }
    parse_ms = (time.perf_counter() - started) * 1000.0
    position_codec = PositionCodec(content)
    diagnostics = (
        _syntax_diagnostics(root, path=path, language=language) if root is not None else []
    )
    fragments: list[dict[str, Any]] = [
        _serialize_with_byte_offsets(row, position_codec) for row in python_fragments
    ]
    unsupported: list[str] = list(unsupported_projections)
    for projection_id in () if language == "Python" else projection_ids:
        if projection_id in unsupported_projections:
            continue
        entry = PROJECTIONS[projection_id]
        extractor = entry.extract
        if extractor is None:
            unsupported.append(projection_id)
            continue
        if root is None:
            return {
                "status": "error",
                "error": {
                    "type": "worker_contract",
                    "message": "projection analyzer root is unavailable",
                    "language": language,
                    "path": path,
                },
            }
        try:
            if symbol_search is not None:
                symbol_query, symbol_max_results = symbol_search
                extracted = extractor(
                    root,
                    path=path,
                    language=language,
                    name_query=symbol_query,
                    max_results=symbol_max_results + 1,
                )
                symbol_truncated = len(extracted) > symbol_max_results
                extracted = extracted[:symbol_max_results]
            else:
                extracted = extractor(root, path=path, language=language)
            fragments.extend(_serialize_with_byte_offsets(row, position_codec) for row in extracted)
        except UnsupportedLanguageError:
            unsupported.append(projection_id)
    for rule_id in request.get("rules", []):
        if root is None:
            root = _ast_grep_root(text, language)
        try:
            rule = load_packaged_rule(rule_id)
        except KeyError:
            return {
                "status": "error",
                "error": {
                    "type": "unknown_rule",
                    "message": f"unregistered packaged rule {rule_id!r}",
                },
            }
        try:
            fragments.extend(
                _serialize_with_byte_offsets(row, position_codec)
                for row in evaluate_packaged_rule(root, rule, path=path, language=language)
            )
        except UnsupportedLanguageError:
            unsupported.append(rule_id)
    truncated = symbol_truncated or len(fragments) > MAX_FRAGMENTS
    postgresql_catalog_payload: dict[str, Any] | None = None
    if postgresql_catalog is not None:
        if postgresql_analysis is None:
            return {
                "status": "error",
                "error": {
                    "type": "worker_contract",
                    "message": "PostgreSQL analysis is unavailable",
                    "language": language,
                    "path": path,
                },
            }
        try:
            extraction = extract_postgresql_catalog(
                text,
                postgresql_analysis.document,
                postgresql_catalog,
                root=postgresql_analysis.root,
            )
        except ValueError as exc:
            return {
                "status": "error",
                "error": {
                    "type": "postgresql_catalog_contract",
                    "message": str(exc),
                    "language": language,
                    "path": path,
                },
            }
        postgresql_catalog_payload = extraction.model_dump(mode="json")
    return {
        "status": "ok",
        "fragments": fragments[:MAX_FRAGMENTS],
        "diagnostics": [
            _serialize_with_byte_offsets(diagnostic, position_codec) for diagnostic in diagnostics
        ],
        "postgresql_catalog": postgresql_catalog_payload,
        "stats": {
            "parses": parses,
            "parse_ms": parse_ms,
            "fragment_count": len(fragments),
            "truncated": truncated,
            "unsupported": unsupported,
            "max_rss_bytes": _max_rss_bytes(),
        },
    }


_STRUCTURAL_AST_GREP_LANGUAGES = frozenset(
    language for language, factory in ROOT_FACTORIES.items() if factory is _ast_grep_root
)
_CONVERT_CASES = frozenset(
    {
        "lowerCase",
        "upperCase",
        "capitalize",
        "camelCase",
        "pascalCase",
        "snakeCase",
        "kebabCase",
    }
)


def _metavariable_names(payload: str) -> tuple[str, ...]:
    """Scan `$VAR`/`$$$VAR` tokens with explicit character iteration (no regex)."""
    names: set[str] = set()
    index = 0
    while index < len(payload):
        if payload[index] != "$":
            index += 1
            continue
        while index < len(payload) and payload[index] == "$":
            index += 1
        start = index
        while index < len(payload) and (payload[index].isalnum() or payload[index] == "_"):
            index += 1
        if index > start:
            candidate = payload[start:index]
            if candidate == candidate.upper():
                names.add(candidate)
    return tuple(sorted(names, key=len, reverse=True))


def _convert_text(text: str, to_case: str) -> str:
    words: list[str] = []
    current: list[str] = []
    for character in text:
        if character.isalnum():
            if current and character.isupper() and current[-1].islower():
                words.append("".join(current))
                current = []
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    lowered = [word.lower() for word in words]
    if to_case == "lowerCase":
        return text.lower()
    if to_case == "upperCase":
        return text.upper()
    if to_case == "capitalize":
        return text[:1].upper() + text[1:]
    if to_case == "camelCase":
        return lowered[0] + "".join(word.capitalize() for word in lowered[1:]) if lowered else ""
    if to_case == "pascalCase":
        return "".join(word.capitalize() for word in lowered)
    if to_case == "kebabCase":
        return "-".join(lowered)
    return "_".join(lowered)


def _transformed_captures(
    captures: dict[str, str],
    transforms: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Apply stable substring/convert transforms; reject anything else."""
    derived = dict(captures)
    for name, raw_specification in transforms.items():
        specification = _object_mapping(raw_specification)
        if specification is None:
            return derived, {"type": "bad_transform", "message": str(name)}
        kind = specification.get("kind")
        source = str(specification.get("source", ""))
        source_value = derived.get(source.lstrip("$"), "")
        if kind == "substring":
            start = specification.get("start_char")
            end = specification.get("end_char")
            derived[name] = source_value[
                start if isinstance(start, int) else None : end if isinstance(end, int) else None
            ]
            continue
        if kind == "convert":
            to_case = str(specification.get("to_case", "lowerCase"))
            if to_case not in _CONVERT_CASES:
                return derived, {"type": "bad_transform", "message": to_case}
            derived[name] = _convert_text(source_value, to_case)
            continue
        return derived, {
            "type": "unsupported_capability",
            "message": f"transform {kind!r} requires the rust engine",
        }
    return derived, None


def _render_template(template: str, captures: dict[str, str]) -> str:
    rendered = template
    for name in sorted(captures, key=len, reverse=True):
        value = captures[name]
        rendered = rendered.replace(f"$$${name}", value).replace(f"${name}", value)
    return rendered


def _structural(request: dict[str, Any]) -> dict[str, Any]:
    language = str(request.get("language", ""))
    if language not in _STRUCTURAL_AST_GREP_LANGUAGES:
        return {
            "status": "error",
            "error": {"type": "unsupported_language", "message": language},
        }
    matcher = _object_mapping(request.get("matcher"))
    if matcher is None:
        return {
            "status": "error",
            "error": {"type": "bad_matcher", "message": "matcher object required"},
        }
    matcher_kind = matcher.get("kind")
    if matcher_kind == "pattern":
        pattern = matcher.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return {
                "status": "error",
                "error": {"type": "bad_matcher", "message": "pattern required"},
            }
        config: dict[str, object] = {"rule": {"pattern": pattern}}
    elif matcher_kind == "rule":
        rule = _object_mapping(matcher.get("rule"))
        if not rule:
            return {
                "status": "error",
                "error": {"type": "bad_matcher", "message": "rule mapping required"},
            }
        config = {"rule": rule}
        for optional in ("constraints", "utils"):
            value = _object_mapping(matcher.get(optional))
            if value:
                config[optional] = value
    else:
        return {
            "status": "error",
            "error": {"type": "bad_matcher", "message": f"unknown matcher kind {matcher_kind!r}"},
        }

    fix: object = request.get("fix")
    transforms = _object_mapping(request.get("transforms"))
    raw_want: object = request.get("want")
    want_values = _object_list(raw_want)
    want = (
        frozenset(str(item) for item in want_values)
        if want_values is not None
        else frozenset({"findings"})
    )
    limits = _object_mapping(request.get("limits")) or {}
    max_findings = _wire_integer(limits.get("max_findings"), 200)
    max_capture_chars = _wire_integer(limits.get("max_capture_chars"), 200)
    max_preview_chars = _wire_integer(limits.get("max_preview_chars"), 200)
    capture_names = _metavariable_names(json.dumps(config, separators=(",", ":")))
    template: str | None = None
    fix_config = _object_mapping(fix)
    if fix_config is not None:
        raw_template = fix_config.get("template", fix_config.get("text"))
        if fix_config.get("expand_start") is not None or fix_config.get("expand_end") is not None:
            return {
                "status": "error",
                "error": {
                    "type": "unsupported_capability",
                    "message": "fix expansion requires the rust engine",
                },
            }
        template = raw_template if isinstance(raw_template, str) else None
    elif isinstance(fix, str):
        template = fix

    findings: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    truncated = False
    file_entries = _object_list(request.get("files")) or []
    for raw_entry in file_entries:
        if truncated:
            break
        entry = _object_mapping(raw_entry)
        if entry is None:
            continue
        path = str(entry.get("path", ""))
        try:
            raw = base64.b64decode(str(entry.get("content_b64", "")), validate=True)
            text = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            errors.append({"path": path, "message": f"undecodable content: {exc}"})
            continue
        codec = PositionCodec(raw)
        try:
            root = _ast_grep_root(text, language)
        except Exception as exc:  # parse failure is a per-file error, not process death
            errors.append({"path": path, "message": f"{type(exc).__name__}: {exc}"[:280]})
            continue
        matches = root.find_all(cast("Config", config))
        spans: list[tuple[int, int, Any]] = []
        for node in matches:
            node_range = node.range()
            spans.append((node_range.start.index, node_range.end.index, node))
        spans.sort(key=lambda item: (item[0], -item[1]))
        outermost: list[tuple[int, int, Any]] = []
        for start, end, node in spans:
            if any(
                kept_start <= start and end <= kept_end and (kept_start, kept_end) != (start, end)
                for kept_start, kept_end, _kept in outermost
            ):
                continue
            outermost.append((start, end, node))
        previous_end = -1
        for start, end, _node in outermost:
            if start < previous_end:
                return {
                    "status": "error",
                    "error": {
                        "type": "overlapping_edits",
                        "message": f"{path}: partially overlapping matches",
                    },
                }
            previous_end = end
        for _start, _end, node in outermost:
            if len(findings) >= max_findings:
                truncated = True
                break
            node_range = node.range()
            byte_start = codec.point_to_byte(node_range.start.line, node_range.start.column)
            byte_end = codec.point_to_byte(node_range.end.line, node_range.end.column)
            captures: dict[str, str] = {}
            capture_rows: list[dict[str, Any]] = []
            for name in capture_names:
                single = node.get_match(name)
                if single is not None:
                    single_range = single.range()
                    value = single.text()
                    captures[name] = value
                    capture_rows.append(
                        {
                            "name": name,
                            "text": value[:max_capture_chars],
                            "byte_start": codec.point_to_byte(
                                single_range.start.line, single_range.start.column
                            ),
                            "byte_end": codec.point_to_byte(
                                single_range.end.line, single_range.end.column
                            ),
                        }
                    )
                    continue
                multiple = node.get_multiple_matches(name)
                if multiple:
                    value = " ".join(item.text() for item in multiple)
                    captures[name] = value
            findings.append(
                {
                    "path": path,
                    "byte_start": byte_start,
                    "byte_end": byte_end,
                    "start_line": node_range.start.line,
                    "start_column": node_range.start.column,
                    "end_line": node_range.end.line,
                    "end_column": node_range.end.column,
                    "text_preview": node.text()[:max_preview_chars],
                    "captures": capture_rows[: _wire_integer(limits.get("max_captures"), 16)],
                }
            )
            if "edits" in want and template is not None:
                derived = captures
                if transforms:
                    derived, transform_error = _transformed_captures(captures, transforms)
                    if transform_error is not None:
                        return {"status": "error", "error": transform_error}
                edits.append(
                    {
                        "path": path,
                        "byte_start": byte_start,
                        "byte_end": byte_end,
                        "inserted_text": _render_template(template, derived),
                    }
                )
    return {
        "status": "ok",
        "engine": "python",
        "engine_version": AST_GREP_RUNTIME_VERSION,
        "findings": findings,
        "edits": edits,
        "truncated": truncated,
        "errors": errors,
    }


def main() -> int:
    """Serve bounded JSON-lines extraction requests until EOF or shutdown."""
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    parses = 0
    try:
        while True:
            try:
                frame = _read_frame(stdin)
            except FrameTooLargeError:
                return 2
            if frame is None:
                return 0
            try:
                request = json.loads(frame)
            except json.JSONDecodeError:
                _write_frame(
                    stdout,
                    {
                        "id": None,
                        "status": "error",
                        "error": {"type": "bad_frame", "message": "invalid JSON"},
                    },
                )
                continue
            request_id = request.get("id")
            op = request.get("op")
            if op == "ping":
                _write_frame(
                    stdout,
                    {
                        "id": request_id,
                        "status": "ok",
                        "op": "pong",
                        "engine": "python",
                        "engine_version": AST_GREP_RUNTIME_VERSION,
                        "capabilities": list(STRUCTURAL_WORKER_CAPABILITIES),
                        "pid": os.getpid(),
                        "max_rss_bytes": _max_rss_bytes(),
                    },
                )
                continue
            if op == "shutdown":
                _write_frame(stdout, {"id": request_id, "status": "ok", "op": "shutdown"})
                return 0
            if op == "structural":
                try:
                    response = _structural(request)
                except Exception as exc:  # worker stays alive; failure is typed
                    response = {
                        "status": "error",
                        "error": {
                            "type": "worker_failure",
                            "message": f"{type(exc).__name__}: {exc}"[:280],
                        },
                    }
                response["id"] = request_id
                _write_frame(stdout, response)
                continue
            if op == "extract":
                if str(request.get("language", "")) in ROOT_FACTORIES:
                    parses += 1
                try:
                    response = _extract(
                        request,
                        root_factories=ROOT_FACTORIES,
                        parses=parses,
                    )
                except Exception as exc:  # worker stays alive; failure is typed
                    response = {
                        "status": "error",
                        "error": {
                            "type": "worker_failure",
                            "message": f"{type(exc).__name__}: {exc}"[:280],
                        },
                    }
                response["id"] = request_id
                _write_frame(stdout, response)
                continue
            _write_frame(
                stdout,
                {
                    "id": request_id,
                    "status": "error",
                    "error": {"type": "unknown_op", "message": str(op)},
                },
            )
    finally:
        _close_postgresql_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
