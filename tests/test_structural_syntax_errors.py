"""AC21: syntax errors return partial structural facts plus diagnostics."""

import pytest

import soleaux.structural.supervisor

BROKEN_TS = b"""export function good(): number {
  return 1;
}

export function broken( {
  const x = ;
}
"""


@pytest.fixture
async def supervisor():
    instance = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_syntax_error_returns_survivor_facts_and_diagnostics(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="TypeScript",
        path="broken.ts",
        content=BROKEN_TS,
        projections=("syntax.declarations", "syntax.call_sites"),
    )
    names = [row.name for row in result.fragments if row.projection == "syntax.declarations"]
    assert "good" in names
    assert result.diagnostics, "expected structural diagnostics for the broken region"
    diagnostic = result.diagnostics[0]
    assert diagnostic.severity == "error"
    assert "syntax error" in diagnostic.message
    assert diagnostic.path == "broken.ts"
