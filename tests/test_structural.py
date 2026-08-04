"""Structural extraction through the supervised worker."""

import base64
import importlib

import pytest

import soleaux.structural.cache
import soleaux.structural.narrow
import soleaux.structural.supervisor
import soleaux.structural.worker

TS_FIXTURE = b"""import { helper } from "./helper";

export function greet(name: string): string {
  return helper(name);
}

export class Greeter {
  greet(name: string): string {
    return greet(name);
  }
}
"""

PY_FIXTURE = b"""import os
from collections import OrderedDict


def top_level():
    return os.name


class Holder:
    def method(self):
        return top_level()
"""

PY_MEMBER_FIXTURE = b"""class Outer:
    annotated: int = 1

    class Inner:
        nested_annotated: str = "value"

        def inner_method(self):
            inner_local = 1
            return inner_local

    def outer_method(self):
        outer_local = 1
        return outer_local
"""


@pytest.fixture
async def supervisor():
    instance = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_typescript_projections(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="TypeScript",
        path="src/greet.ts",
        content=TS_FIXTURE,
        projections=(
            "syntax.declarations",
            "syntax.imports",
            "syntax.exports",
            "syntax.call_sites",
        ),
    )
    by_projection: dict[str, list[str]] = {}
    for row in result.fragments:
        by_projection.setdefault(row.projection, []).append(row.name or row.kind)
    assert "greet" in by_projection["syntax.declarations"]
    assert "Greeter" in by_projection["syntax.declarations"]
    assert "./helper" in by_projection["syntax.imports"]
    assert by_projection["syntax.exports"]
    assert "helper" in by_projection["syntax.call_sites"]
    for row in result.fragments:
        assert row.schema_version == "soleaux.projection/v1"
        assert row.byte_end >= row.byte_start


async def test_python_projections(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="Python",
        path="pkg/holder.py",
        content=PY_FIXTURE,
        projections=(
            "syntax.declarations",
            "syntax.imports",
            "syntax.members",
            "syntax.visibility",
        ),
    )
    by_projection: dict[str, list[str]] = {}
    for row in result.fragments:
        by_projection.setdefault(row.projection, []).append(row.name or row.kind)
    assert "top_level" in by_projection["syntax.declarations"]
    assert "Holder" in by_projection["syntax.declarations"]
    assert "os" in by_projection["syntax.imports"]
    assert "collections" in by_projection["syntax.imports"]
    assert "method" in by_projection["syntax.members"]
    assert by_projection["syntax.visibility"]


async def test_positions_are_canonical_utf8_byte_offsets(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    content = "# é\nvalue = 1\n".encode()
    result = await supervisor.extract(
        language="Python",
        path="pkg/positions.py",
        content=content,
        projections=("syntax.declarations",),
    )

    fragment = next(row for row in result.fragments if row.name == "value")
    assert (fragment.byte_start, fragment.byte_end) == (5, 14)
    assert (
        fragment.start_line,
        fragment.start_column,
        fragment.end_line,
        fragment.end_column,
    ) == (1, 0, 1, 9)


async def test_python_members_use_nearest_class_without_duplicates(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="Python",
        path="pkg/members.py",
        content=PY_MEMBER_FIXTURE,
        projections=("syntax.members",),
    )

    members = [
        (
            fragment.name,
            fragment.kind,
            fragment.attributes.get("member_of"),
        )
        for fragment in result.fragments
    ]
    assert set(members) == {
        ("annotated", "attribute", "Outer"),
        ("Inner", "nested_class", "Outer"),
        ("outer_method", "method", "Outer"),
        ("nested_annotated", "attribute", "Inner"),
        ("inner_method", "method", "Inner"),
    }
    assert len(members) == len(set(members))
    assert all(name not in {"inner_local", "outer_local"} for name, _kind, _owner in members)


def test_narrow_candidates_is_literal_and_bounded() -> None:
    contents = {
        "a.py": b"alpha beta",
        "b.py": b"gamma",
        "c.py": b"alpha",
    }
    assert soleaux.structural.narrow.narrow_candidates(contents, b"alpha") == ("a.py", "c.py")
    assert soleaux.structural.narrow.narrow_candidates(contents, b"") == ()
    assert soleaux.structural.narrow.narrow_candidates(contents, b"alpha", max_candidates=1) == (
        "a.py",
    )


def test_memory_cache_weighted_lru() -> None:
    cache = soleaux.structural.cache.MemoryCache(max_entries=2, max_bytes=64)
    first = soleaux.structural.cache.StructuralCacheKey(
        workspace_id="w",
        source_fingerprint="f1",
        projection_name="syntax.declarations",
        language="Python",
        sgconfig_hash="s",
        rule_digest="r",
        analyzer_version="0.45.0",
    )
    second = soleaux.structural.cache.StructuralCacheKey(
        workspace_id="w",
        source_fingerprint="f2",
        projection_name="syntax.declarations",
        language="Python",
        sgconfig_hash="s",
        rule_digest="r",
        analyzer_version="0.45.0",
    )
    cache.put(first, b"one")
    cache.put(second, b"two")
    assert cache.get(first) == b"one"
    third = soleaux.structural.cache.StructuralCacheKey(
        workspace_id="w",
        source_fingerprint="f3",
        projection_name="syntax.declarations",
        language="Python",
        sgconfig_hash="s",
        rule_digest="r",
        analyzer_version="0.45.0",
    )
    cache.put(third, b"three")
    assert cache.get(second) is None
    assert cache.size[0] == 2
    cache.clear()
    assert cache.size == (0, 0)


def test_off_cache_never_stores() -> None:
    cache = soleaux.structural.cache.OffCache()
    key = soleaux.structural.cache.StructuralCacheKey(
        workspace_id="w",
        source_fingerprint="f",
        projection_name="syntax.declarations",
        language="Python",
        sgconfig_hash="s",
        rule_digest="r",
        analyzer_version="0.45.0",
    )
    cache.put(key, b"bytes")
    assert cache.get(key) is None
    cache.clear()


def test_sql_reports_unavailable_analyzer_with_an_unregistered_factory() -> None:
    root_factories = {
        language: factory
        for language, factory in soleaux.structural.worker.ROOT_FACTORIES.items()
        if language != "PostgreSQL"
    }
    extract_request = importlib.import_module("soleaux.structural.worker")._extract
    response = extract_request(
        {
            "content_b64": base64.b64encode(b"CREATE TABLE t (id int);").decode(),
            "language": "PostgreSQL",
            "path": "supabase/schemas/00_bootstrap.sql",
            "projections": ["syntax.declarations"],
            "rules": [],
        },
        root_factories=root_factories,
        parses=0,
    )

    assert response["fragments"] == []
    assert response["diagnostics"] == []
    stats = response["stats"]
    assert isinstance(stats, dict)
    assert stats["unsupported"] == ["syntax.declarations"]


def test_sql_is_classified_before_its_analyzer_exists() -> None:
    """The root-factory registry is the analyzer capability owner."""
    from soleaux.structural.snapshot import LANGUAGE_BY_EXTENSION

    assert LANGUAGE_BY_EXTENSION[".sql"] == "PostgreSQL"
    classified = set(LANGUAGE_BY_EXTENSION.values())
    assert set(soleaux.structural.worker.ROOT_FACTORIES) == classified


def test_cache_key_binds_rows_to_the_analyzer_that_made_them() -> None:
    """Two analyzers over one language must not share a cache entry."""
    from libcst import LIBCST_VERSION

    from soleaux.structural.fragments import analyzer_version_for

    def key(analyzer_version: str) -> soleaux.structural.cache.StructuralCacheKey:
        return soleaux.structural.cache.StructuralCacheKey(
            workspace_id="w",
            source_fingerprint="f",
            projection_name="syntax.declarations",
            language="PostgreSQL",
            sgconfig_hash="s",
            rule_digest="r",
            analyzer_version=analyzer_version,
        )

    cache = soleaux.structural.cache.MemoryCache(max_entries=4, max_bytes=1024)
    cache.put(key("0.45.0"), b"from-ast-grep")
    assert cache.get(key("7.18")) is None
    assert cache.get(key("0.45.0")) == b"from-ast-grep"
    assert analyzer_version_for("Python") == LIBCST_VERSION
