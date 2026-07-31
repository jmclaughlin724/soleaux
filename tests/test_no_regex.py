"""Executable AST/structured-parser-only invariant for maintained Soleaux files."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

import yaml

PACKAGE_ROOT = Path(__file__).parents[1]
PYTHON_ROOTS = ("scripts", "src", "tests")
ECMASCRIPT_ROOTS = ("docs", "scripts", "src", "tests")
ECMASCRIPT_LANGUAGES = {
    ".cjs": "javascript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}
BANNED_MATCHING_MODULES = frozenset({"fnmatch", "re", "regex"})
BANNED_TRAVERSAL_CALLS = frozenset({"glob", "rglob"})
EXCLUDED_PARTS = frozenset(
    {
        ".blume",
        ".blume-verify",
        ".turbo",
        ".vercel",
        "__pycache__",
        "dist",
        "node_modules",
    }
)


def _maintained_files(roots: Sequence[str], suffixes: frozenset[str]) -> Iterable[Path]:
    for root_name in roots:
        root = PACKAGE_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix in suffixes
                and not EXCLUDED_PARTS.intersection(path.relative_to(PACKAGE_ROOT).parts)
            ):
                yield path


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _python_violations(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    # Glob traversal is banned in the shipped package only. The test harness
    # walks its own tree to find the files it checks, which is not product code.
    product_source = path.is_relative_to(PACKAGE_ROOT / "src")
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BANNED_MATCHING_MODULES:
                    violations.append(f"line {node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module in BANNED_MATCHING_MODULES:
            violations.append(f"line {node.lineno}: imports from {node.module}")
        elif isinstance(node, ast.Call):
            call_name = _call_name(node)
            if product_source and call_name in BANNED_TRAVERSAL_CALLS:
                violations.append(
                    f"line {node.lineno}: {call_name} traverses by glob; "
                    "use os.walk with RepositoryPattern"
                )
            for keyword in node.keywords:
                if keyword.arg == "pattern" and call_name in {
                    "Field",
                    "StringConstraints",
                    "constr",
                }:
                    violations.append(f"line {node.lineno}: {call_name} uses a pattern validator")
                if keyword.arg == "match" and call_name == "raises":
                    violations.append(
                        f"line {node.lineno}: pytest.raises uses a regex-backed match"
                    )
    return tuple(violations)


def _ecmascript_violations(path: Path) -> tuple[str, ...]:
    from ast_grep_py import SgRoot

    language = ECMASCRIPT_LANGUAGES[path.suffix]
    root = SgRoot(path.read_text(encoding="utf-8"), language).root()
    violations = [
        f"line {node.range().start.line + 1}: regular-expression literal"
        for node in root.find_all(kind="regex")
    ]
    for kind, field_name in (
        ("call_expression", "function"),
        ("new_expression", "constructor"),
    ):
        for node in root.find_all(kind=kind):
            owner = node.field(field_name)
            if owner is not None and owner.text() == "RegExp":
                violations.append(f"line {node.range().start.line + 1}: constructs RegExp")
    return tuple(violations)


def _mapping_violations(value: object, *, path: tuple[str, ...] = ()) -> tuple[str, ...]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        entries = cast("Mapping[object, object]", value)
        for raw_key, child in entries.items():
            key = str(raw_key)
            child_path = (*path, key)
            if key == "regex":
                violations.append(".".join(child_path))
            violations.extend(_mapping_violations(child, path=child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        children = cast("Sequence[object]", value)
        for index, child in enumerate(children):
            violations.extend(_mapping_violations(child, path=(*path, str(index))))
    return tuple(violations)


def test_maintained_soleaux_surfaces_never_use_regular_expressions() -> None:
    violations: list[str] = []
    for path in _maintained_files(PYTHON_ROOTS, frozenset({".py"})):
        violations.extend(
            f"{path.relative_to(PACKAGE_ROOT)}: {detail}" for detail in _python_violations(path)
        )
    for path in _maintained_files(
        ECMASCRIPT_ROOTS,
        frozenset(ECMASCRIPT_LANGUAGES),
    ):
        violations.extend(
            f"{path.relative_to(PACKAGE_ROOT)}: {detail}" for detail in _ecmascript_violations(path)
        )
    for path in _maintained_files(
        ("src", "tests"),
        frozenset({".yaml", ".yml"}),
    ):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            violations.extend(
                f"{path.relative_to(PACKAGE_ROOT)}: ast-grep constraint {detail}"
                for detail in _mapping_violations(document)
            )
    assert violations == []
