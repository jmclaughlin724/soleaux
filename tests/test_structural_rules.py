"""AC25: packaged capability rules load from the installed package and evaluate."""

import pytest

import soleaux.structural.rules
import soleaux.structural.supervisor

EXPECTED_RULE_IDS = {
    "decorator-ts",
    "decorator-py",
    "jsx-element",
    "directive-prologue-ts",
    "fastmcp-tool-registration",
    "call-ts",
    "call-py",
}


def test_packaged_rules_load_with_digests() -> None:
    rules = soleaux.structural.rules.packaged_rules()
    assert set(rules) == EXPECTED_RULE_IDS
    for rule_id, rule in rules.items():
        assert rule.id == rule_id
        assert len(rule.digest) == 64
        assert rule.digest == soleaux.structural.rules.packaged_rule_digest(rule_id)
        assert rule.rule


def test_unknown_rule_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        soleaux.structural.rules.load_packaged_rule("missing-rule")


@pytest.fixture
async def supervisor():
    instance = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_decorator_py_evaluates_through_the_worker(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    content = b"@mcp.tool()\ndef handler() -> None:\n    return None\n"
    result = await supervisor.extract(
        language="Python",
        path="server.py",
        content=content,
        projections=(),
        rules=("decorator-py", "fastmcp-tool-registration"),
    )
    projections = {row.projection for row in result.fragments}
    assert "rules.decorator-py" in projections
    assert "rules.fastmcp-tool-registration" in projections


async def test_jsx_and_directive_rules(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    content = b'"use client";\n\nexport const View = () => <Button label="go" />;\n'
    result = await supervisor.extract(
        language="Tsx",
        path="view.tsx",
        content=content,
        projections=(),
        rules=("jsx-element", "directive-prologue-ts"),
    )
    projections = {row.projection for row in result.fragments}
    assert "rules.jsx-element" in projections
    assert "rules.directive-prologue-ts" in projections


async def test_rule_language_mismatch_is_explicit(
    supervisor: soleaux.structural.supervisor.StructuralWorkerSupervisor,
) -> None:
    result = await supervisor.extract(
        language="TypeScript",
        path="a.ts",
        content=b"const x = 1;\n",
        projections=(),
        rules=("decorator-py",),
    )
    assert result.unsupported == ("decorator-py",)
