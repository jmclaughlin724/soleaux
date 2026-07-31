"""Degradation: honest failures, unsupported coverage, and lazy startup (AC05, AC26)."""

import base64
import importlib
import sys

import pytest

from soleaux.structural.projections import AnalyzerNode, AnalyzerParseError, RootFactory
from soleaux.structural.supervisor import (
    ContentTooLargeError,
    StructuralWorkerSupervisor,
    WorkerJobError,
    WorkerUnavailableError,
)


async def test_no_structural_request_means_no_worker() -> None:
    supervisor = StructuralWorkerSupervisor()
    try:
        assert supervisor.started is False
        assert supervisor.pid is None
    finally:
        await supervisor.aclose()
    assert supervisor.started is False


async def test_unknown_projection_is_a_typed_failure() -> None:
    supervisor = StructuralWorkerSupervisor()
    try:
        with pytest.raises(WorkerJobError) as excinfo:
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=b"x = 1\n",
                projections=("syntax.nope",),
            )
        assert excinfo.value.error_type == "unknown_projection"
    finally:
        await supervisor.aclose()


async def test_unknown_rule_is_a_typed_failure() -> None:
    supervisor = StructuralWorkerSupervisor()
    try:
        with pytest.raises(WorkerJobError) as excinfo:
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=b"x = 1\n",
                projections=(),
                rules=("missing-rule",),
            )
        assert excinfo.value.error_type == "unknown_rule"
    finally:
        await supervisor.aclose()


def test_analyzer_parse_error_is_a_distinct_worker_failure() -> None:
    def fail_to_parse(_source: str, _language: str) -> AnalyzerNode:
        raise AnalyzerParseError("invalid SQL at byte 0")

    root_factories: dict[str, RootFactory] = {"PostgreSQL": fail_to_parse}
    extract_request = importlib.import_module("soleaux.structural.worker")._extract
    response = extract_request(
        {
            "content_b64": base64.b64encode(b"not sql").decode(),
            "language": "PostgreSQL",
            "path": "schema.sql",
            "projections": ["syntax.spans"],
            "rules": [],
        },
        root_factories=root_factories,
        parses=1,
    )

    assert response == {
        "status": "error",
        "error": {
            "type": "parse_error",
            "message": "invalid SQL at byte 0",
            "language": "PostgreSQL",
            "path": "schema.sql",
        },
    }


async def test_unsupported_language_yields_explicit_coverage() -> None:
    supervisor = StructuralWorkerSupervisor()
    try:
        result = await supervisor.extract(
            language="Haskell",
            path="a.hs",
            content=b'main = putStrLn "hi"\n',
            projections=("syntax.declarations",),
        )
        assert result.unsupported == ("syntax.declarations",)
        assert result.fragments == ()
    finally:
        await supervisor.aclose()


async def test_oversized_content_is_rejected_before_ipc() -> None:
    supervisor = StructuralWorkerSupervisor()
    try:
        with pytest.raises(ContentTooLargeError):
            await supervisor.extract(
                language="Python",
                path="big.py",
                content=b"x" * (4 * 1024 * 1024 + 1),
                projections=("syntax.declarations",),
            )
        assert supervisor.started is False
    finally:
        await supervisor.aclose()


async def test_broken_worker_argv_surfaces_unavailable() -> None:
    supervisor = StructuralWorkerSupervisor(
        worker_argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )
    try:
        with pytest.raises(WorkerUnavailableError):
            await supervisor.extract(
                language="Python",
                path="a.py",
                content=b"x = 1\n",
                projections=("syntax.declarations",),
                timeout=5.0,
            )
    finally:
        await supervisor.aclose()
