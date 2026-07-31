"""LibCST owns Python source facts; ast-grep remains matcher/rewrite-only."""

from __future__ import annotations

from pathlib import Path

import pytest
from libcst import (
    CSTVisitor,
    Import,
    ImportFrom,
    Module,
    Name,
    parse_module,
)

import soleaux.structural.python
from soleaux.structural.fragments import SyntaxFragment
from soleaux.structural.python import PythonParseError, extract_python

PYTHON_SOURCE = """# café
from .base import Base
import yaml, pathlib

__all__ = ["Public", "helper"]

class Public(Base):
    value: int = 1

    def run(self):
        local = 2
        return helper(self.value + local)

def helper(value):
    return value
"""


def test_one_libcst_parse_emits_every_python_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_parse = soleaux.structural.python.parse_module

    def counted_parse(source: str) -> Module:
        nonlocal calls
        calls += 1
        return original_parse(source)

    monkeypatch.setattr(soleaux.structural.python, "parse_module", counted_parse)
    content = PYTHON_SOURCE.encode()
    result = extract_python(
        PYTHON_SOURCE,
        content,
        path="pkg/model.py",
        projections=(
            "syntax.declarations",
            "syntax.imports",
            "syntax.exports",
            "syntax.members",
            "syntax.references",
            "syntax.spans",
            "syntax.visibility",
            "syntax.call_sites",
        ),
    )

    assert calls == 1
    by_projection: dict[str, list[SyntaxFragment]] = {}
    for fragment in result.fragments:
        by_projection.setdefault(fragment.projection, []).append(fragment)

    declarations = {fragment.name for fragment in by_projection["syntax.declarations"]}
    assert declarations >= {"__all__", "Public", "value", "run", "helper"}
    assert "local" not in declarations

    imports = {fragment.name for fragment in by_projection["syntax.imports"]}
    assert imports == {".base", "yaml", "pathlib"}

    export = by_projection["syntax.exports"][0]
    assert export.name == "__all__"
    assert export.attributes["exported_names"] == ["Public", "helper"]

    members = {
        (fragment.name, fragment.attributes["member_of"])
        for fragment in by_projection["syntax.members"]
    }
    assert members == {("value", "Public"), ("run", "Public")}

    helper_call = next(
        fragment for fragment in by_projection["syntax.call_sites"] if fragment.name == "helper"
    )
    assert helper_call.attributes["resolution_status"] == "candidate"
    qualified_names = helper_call.attributes["qualified_names"]
    assert isinstance(qualified_names, list)
    assert "helper" in qualified_names

    references = {fragment.name for fragment in by_projection["syntax.references"]}
    assert references >= {"Base", "helper", "self", "local"}
    assert all(fragment.byte_end >= fragment.byte_start for fragment in result.fragments)


def test_python_stub_uses_the_same_libcst_projection_contract() -> None:
    source = "class Service:\n    def run(self, value: str) -> None: ...\n"
    result = extract_python(
        source,
        source.encode(),
        path="pkg/service.pyi",
        projections=("syntax.declarations", "syntax.members"),
    )

    declarations = {fragment.name for fragment in result.fragments}
    assert declarations == {"Service", "run"}
    method = next(
        fragment for fragment in result.fragments if fragment.projection == "syntax.members"
    )
    assert method.name == "run"
    assert method.attributes["member_of"] == "Service"


def test_python_syntax_error_is_typed() -> None:
    source = "def broken(:\n"
    with pytest.raises(PythonParseError):
        extract_python(
            source,
            source.encode(),
            path="pkg/broken.py",
            projections=("syntax.declarations",),
        )


class _ImportCollector(CSTVisitor):
    """Independent source-contract proof; does not call the production analyzer."""

    def __init__(self) -> None:
        self.modules: list[str] = []

    def visit_Import(self, node: Import) -> None:
        for alias in node.names:
            if isinstance(alias.name, Name):
                self.modules.append(alias.name.value)

    def visit_ImportFrom(self, node: ImportFrom) -> None:
        if isinstance(node.module, Name):
            self.modules.append(node.module.value)


def test_project_catalog_has_no_python_source_parser() -> None:
    source_path = Path(__file__).parents[1] / "src" / "soleaux" / "catalog" / "projects.py"
    collector = _ImportCollector()
    parse_module(source_path.read_text(encoding="utf-8")).visit(collector)

    assert "ast" not in collector.modules
    assert "libcst" not in collector.modules
