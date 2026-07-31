"""Configured governance sources: discovery, conflicts, and promotion."""

from __future__ import annotations

import pathlib

import pydantic
import pytest

import soleaux.analysis.service
import soleaux.authority.contracts
import soleaux.authority.governance
import soleaux.contracts.config
import soleaux.contracts.requests
import soleaux.contracts.workspace
import soleaux.structural.snapshot

_TABLE = (
    "# Registry\n\n| Concern | Owner |\n| --- | --- |\n| Command decisions | `rules/a.rules` |\n"
)


def _source(**overrides: object) -> soleaux.contracts.config.GovernanceSourceConfig:
    payload: dict[str, object] = {
        "id": "registry",
        "path": "REGISTRY.md",
        "format": "markdown",
        "selector": {"kind": "markdown_table", "heading": "Registry", "occurrence": 1},
        "identity_field": "Concern",
        "relationships": [{"field": "Owner"}],
    }
    payload.update(overrides)
    return soleaux.contracts.config.GovernanceSourceConfig.model_validate(payload)


async def _bundle(root: pathlib.Path) -> soleaux.structural.snapshot.SnapshotBundle:
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("main", str(root))],
        config_digest=soleaux.contracts.config.config_digest(b""),
    ).get(None)
    return await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture()


def _write(root: pathlib.Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def test_no_configured_sources_emit_zero_policies_and_stay_silent(
    tmp_path: pathlib.Path,
) -> None:
    _write(tmp_path, {"REGISTRY.md": _TABLE, "rules/a.rules": "forbid\n"})
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle, {}, governance=soleaux.contracts.config.GovernanceConfig()
    )

    assert claims.policies == ()
    assert claims.bindings == ()
    assert claims.warnings == ()


async def test_marker_comments_are_inert_data(tmp_path: pathlib.Path) -> None:
    marked = f'<!-- {{"soleaux":{{"canonical_records":true}}}} -->\n\n{_TABLE}'
    _write(tmp_path, {"REGISTRY.md": marked, "rules/a.rules": "forbid\n"})
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle, {}, governance=soleaux.contracts.config.GovernanceConfig()
    )

    assert claims.policies == ()


async def test_arbitrary_configured_file_yields_policies_and_bindings(
    tmp_path: pathlib.Path,
) -> None:
    _write(tmp_path, {"docs/anything.md": _TABLE, "rules/a.rules": "forbid\n"})
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(
            sources=(_source(path="docs/anything.md"),)
        ),
    )

    assert [policy.title for policy in claims.policies] == ["Command decisions"]
    assert claims.policies[0].policy_id == "registry:command decisions"
    declared = [binding for binding in claims.bindings if binding.binding_kind.value == "declared"]
    assert [binding.target for binding in declared] == ["rules/a.rules"]


async def test_missing_source_and_missing_selector_degrade_with_typed_warnings(
    tmp_path: pathlib.Path,
) -> None:
    _write(tmp_path, {"REGISTRY.md": "# Other heading\n\nno table\n"})
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(
            sources=(
                _source(id="gone", path="MISSING.md"),
                _source(id="unmatched"),
            )
        ),
    )

    assert claims.policies == ()
    assert any(warning.startswith("governance_source_missing:") for warning in claims.warnings)
    assert any(warning.startswith("governance_selector_not_found:") for warning in claims.warnings)


async def test_two_targets_in_one_cell_are_a_valid_set(tmp_path: pathlib.Path) -> None:
    table = (
        "# Registry\n\n"
        "| Concern | Owner |\n"
        "| --- | --- |\n"
        "| Command decisions | `rules/a.rules`, `rules/b.rules` |\n"
    )
    _write(
        tmp_path,
        {"REGISTRY.md": table, "rules/a.rules": "forbid\n", "rules/b.rules": "forbid\n"},
    )
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(sources=(_source(),)),
    )

    assert claims.conflicts == ()
    declared = [binding for binding in claims.bindings if binding.binding_kind.value == "declared"]
    assert sorted(binding.target for binding in declared) == ["rules/a.rules", "rules/b.rules"]
    assert all(
        binding.state is not soleaux.authority.contracts.GovernanceState.CONFLICTING
        for binding in declared
    )


async def test_identical_declarations_are_redundant_and_differing_conflict(
    tmp_path: pathlib.Path,
) -> None:
    content = (
        "# Registry\n\n"
        "| Concern | Owner |\n"
        "| --- | --- |\n"
        "| Same target | `rules/a.rules` |\n"
        "| Same target | `rules/a.rules` |\n"
        "| Differing target | `rules/a.rules` |\n"
        "| Differing target | `rules/b.rules` |\n"
    )
    _write(
        tmp_path,
        {"REGISTRY.md": content, "rules/a.rules": "forbid\n", "rules/b.rules": "forbid\n"},
    )
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(sources=(_source(),)),
    )

    states = {conflict.state for conflict in claims.conflicts}
    assert soleaux.authority.contracts.GovernanceState.CONFLICTING in states
    assert soleaux.authority.contracts.GovernanceState.SHADOWED in states


async def test_required_relationships_warn_only_when_required(tmp_path: pathlib.Path) -> None:
    table = "# Registry\n\n| Concern | Owner |\n| --- | --- |\n| Command decisions | - |\n"
    _write(tmp_path, {"REGISTRY.md": table})
    bundle = await _bundle(tmp_path)

    optional = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(sources=(_source(),)),
    )
    required = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(
            sources=(_source(relationships=[{"field": "Owner", "required": True}]),)
        ),
    )

    assert not any("governance_relationship_field_missing" in w for w in optional.warnings)
    assert any("governance_relationship_field_missing" in w for w in required.warnings)
    assert required.policies[0].required_roles == ("owner",)


def test_typed_selectors_reject_malformed_shapes() -> None:
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.MarkdownTableSelector.model_validate(
            {"kind": "markdown_table", "heading": ""}
        )
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.StructuredRecordsSelector.model_validate(
            {"kind": "structured_records", "keys": []}
        )
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.GovernanceSourceConfig.model_validate(
            {
                "id": "bad",
                "path": "../outside.md",
                "format": "markdown",
                "selector": {"kind": "markdown_table", "heading": "X"},
                "identity_field": "Concern",
            }
        )
    with pytest.raises(pydantic.ValidationError):
        # A structured selector on a markdown source is a format mismatch.
        soleaux.contracts.config.GovernanceSourceConfig.model_validate(
            {
                "id": "bad",
                "path": "data.yaml",
                "format": "markdown",
                "selector": {"kind": "structured_records", "keys": ["records"]},
                "identity_field": "Concern",
            }
        )
    with pytest.raises(pydantic.ValidationError):
        soleaux.contracts.config.GovernanceConfig.model_validate(
            {"sources": [_source().model_dump(), _source().model_dump()]}
        )


async def test_structured_source_records_resolve_by_key_path(tmp_path: pathlib.Path) -> None:
    _write(
        tmp_path,
        {
            "governance.yaml": (
                "registry:\n"
                "  records:\n"
                "    - Concern: Command decisions\n"
                "      Owner: rules/a.rules\n"
            ),
            "rules/a.rules": "forbid\n",
        },
    )
    bundle = await _bundle(tmp_path)

    claims = soleaux.authority.governance.collect_governance_claims(
        bundle,
        {},
        governance=soleaux.contracts.config.GovernanceConfig(
            sources=(
                _source(
                    path="governance.yaml",
                    format="yaml",
                    selector={
                        "kind": "structured_records",
                        "keys": ["registry", "records"],
                    },
                ),
            )
        ),
    )

    assert [policy.title for policy in claims.policies] == ["Command decisions"]
    declared = [binding for binding in claims.bindings if binding.binding_kind.value == "declared"]
    assert [binding.target for binding in declared] == ["rules/a.rules"]


async def test_policy_facts_promote_into_search_rows(tmp_path: pathlib.Path) -> None:
    _write(
        tmp_path,
        {
            "soleaux.toml": (
                'schema_version = "soleaux.config/v1"\n\n'
                "[[governance.sources]]\n"
                'id = "registry"\n'
                'path = "REGISTRY.md"\n'
                'format = "markdown"\n'
                'selector = { kind = "markdown_table", heading = "Registry", occurrence = 1 }\n'
                'identity_field = "Concern"\n'
                'relationships = [{ field = "Owner" }]\n'
            ),
            "REGISTRY.md": _TABLE,
            "rules/a.rules": "forbid\n",
        },
    )

    async with soleaux.analysis.service.SoleauxService.from_root(tmp_path) as service:
        await service._catalog_indexer.settle()
        response = await service.search(
            soleaux.contracts.requests.SearchRequest(
                query="Command decisions", kinds=[soleaux.contracts.requests.SearchKind.POLICY]
            )
        )

    assert response.status.value == "ok"
    assert response.rows
    row = response.rows[0]
    assert row["kind"] == "policy"
    assert row["key"] == "policy:registry:command decisions"
    assert row["policy_id"] == "registry:command decisions"
    assert row["title"] == "Command decisions"
    assert row["governance_source_id"] == "registry"


async def test_collect_policy_facts_is_deterministic(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, {"REGISTRY.md": _TABLE, "rules/a.rules": "forbid\n"})
    bundle = await _bundle(tmp_path)
    governance = soleaux.contracts.config.GovernanceConfig(sources=(_source(),))

    first = soleaux.authority.governance.collect_policy_facts(
        bundle, governance, workspace_id="main"
    )
    second = soleaux.authority.governance.collect_policy_facts(
        bundle, governance, workspace_id="main"
    )

    assert first == second
    assert [fact.policy_id for fact in first] == ["registry:command decisions"]
    assert first[0].attributes["Owner"] == "rules/a.rules"
