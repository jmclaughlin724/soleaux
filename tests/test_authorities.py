"""Manifest authority and schema-preserving governance discovery.

Canonical governance sources are declared explicitly through configured
``[[governance.sources]]`` entries (see ``test_governance_sources.py``); this
module covers the manifest authority resolver's entrypoint, owner, history, and
malformed-input behavior.
"""

from __future__ import annotations

import json
import pathlib

import soleaux.authority.contracts
import soleaux.authority.resolver
import soleaux.contracts.workspace
import soleaux.structural.snapshot


async def _capture(
    tmp_path: pathlib.Path, files: dict[str, str]
) -> soleaux.structural.snapshot.SnapshotBundle:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    workspace = soleaux.contracts.workspace.AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="authority-test",
    ).get("workspace")
    return await soleaux.structural.snapshot.RepositorySnapshotter(workspace).capture(
        scope=tuple(files)
    )


async def test_structured_manifests_and_codeowners_emit_typed_authorities(
    tmp_path: pathlib.Path,
) -> None:
    package_manifest = {
        "name": "fixture",
        "scripts": {"build": "tsc", "test": "pytest"},
        "bin": {"fixture": "src/cli.ts"},
        "exports": {".": "./src/index.ts"},
        "soleaux": {
            "entrypoints": [
                {
                    "kind": "route",
                    "name": "health",
                    "target": "src/routes.ts",
                }
            ],
            "registrations": [
                {
                    "kind": "plugin",
                    "name": "auth",
                    "target": "src/plugin.ts",
                    "owners": ["@runtime"],
                }
            ],
            "owners": [
                {
                    "target": "src/generated.ts",
                    "owners": ["@generator"],
                    "kind": "generator",
                },
                {
                    "target": "src/index.ts",
                    "owners": ["@canonical"],
                    "kind": "canonical",
                },
            ],
        },
    }
    pyproject = """\
[project]
name = "fixture-python"

[project.scripts]
fixture-python = "pkg.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]

[[tool.soleaux.entrypoints]]
kind = "job"
name = "cleanup"
target = "pkg/jobs.py"
"""
    yaml_manifest = """\
entrypoints:
  - kind: service
    name: worker
    target: src/worker.ts
owners:
  - target: src/policy.ts
    owners: ["@policy"]
    kind: policy
"""
    bundle = await _capture(
        tmp_path,
        {
            "package.json": json.dumps(package_manifest),
            "pyproject.toml": pyproject,
            "soleaux.yaml": yaml_manifest,
            ".github/CODEOWNERS": "src/index.ts @codeowners\n",
            "src/index.ts": "export const value = 1;\n",
            "src/generated.ts": "export const generated = 1;\n",
            "src/policy.ts": "export const policy = 1;\n",
            "src/plugin.ts": "export const plugin = 1;\n",
            "src/routes.ts": "export const route = 1;\n",
            "src/worker.ts": "export const worker = 1;\n",
            "pkg/cli.py": "def main(): pass\n",
            "pkg/jobs.py": "def cleanup(): pass\n",
            "tests/test_cli.py": "def test_cli(): pass\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    entrypoint_kinds = {row.data["entrypoint_kind"] for row in result.entrypoints}
    assert {
        soleaux.authority.contracts.EntrypointKind.SCRIPT.value,
        soleaux.authority.contracts.EntrypointKind.TEST.value,
        soleaux.authority.contracts.EntrypointKind.EXECUTABLE.value,
        soleaux.authority.contracts.EntrypointKind.PACKAGE.value,
        soleaux.authority.contracts.EntrypointKind.ROUTE.value,
        soleaux.authority.contracts.EntrypointKind.PLUGIN.value,
        soleaux.authority.contracts.EntrypointKind.JOB.value,
        soleaux.authority.contracts.EntrypointKind.SERVICE.value,
    } <= entrypoint_kinds
    assert any(
        row.data["entrypoint_kind"] == soleaux.authority.contracts.EntrypointKind.TEST.value
        and row.data["target"] == "tests"
        and row.data["source_path"] == "pyproject.toml"
        for row in result.entrypoints
    )
    owners = {row.data["target"]: row.data for row in result.owners}
    assert owners["src/index.ts"]["owners"] == ("@codeowners",)
    assert (
        owners["src/index.ts"]["owner_kind"] == soleaux.authority.contracts.OwnerKind.POLICY.value
    )
    assert (
        owners["src/index.ts"]["source_kind"]
        == soleaux.authority.contracts.OwnerSourceKind.EXPLICIT_GOVERNANCE.value
    )
    assert (
        owners["src/generated.ts"]["owner_kind"]
        == soleaux.authority.contracts.OwnerKind.GENERATOR.value
    )
    assert (
        owners["src/plugin.ts"]["owner_kind"]
        == soleaux.authority.contracts.OwnerKind.RUNTIME_REGISTRATION.value
    )
    assert result.warnings == ()


async def test_same_tier_owner_conflict_is_machine_readable_without_a_winner(
    tmp_path: pathlib.Path,
) -> None:
    package_manifest = {
        "name": "fixture",
        "soleaux": {
            "owners": [
                {
                    "target": "src/generated.ts",
                    "owners": ["@first"],
                    "kind": "generator",
                },
                {
                    "target": "src/generated.ts",
                    "owners": ["@second"],
                    "kind": "policy",
                },
            ]
        },
    }
    bundle = await _capture(
        tmp_path,
        {
            "package.json": json.dumps(package_manifest),
            "src/generated.ts": "export const generated = 1;\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    assert result.owners == ()
    assert len(result.conflicts) == 2
    assert {row.data["state"] for row in result.conflicts} == {
        soleaux.authority.contracts.GovernanceState.CONFLICTING.value
    }
    assert len({row.data["conflict_id"] for row in result.conflicts}) == 1
    assert all(row.data["role"] is None for row in result.conflicts)


class _HistoryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def claims(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        *,
        max_paths: int,
        max_commits: int,
    ) -> tuple[soleaux.authority.contracts.AuthorityClaim, ...]:
        del bundle, max_paths, max_commits
        self.calls += 1
        return (
            soleaux.authority.contracts.AuthorityClaim(
                target="src/history.py",
                owners=("@historical",),
                owner_kind=soleaux.authority.contracts.OwnerKind.HISTORICAL,
                source_kind=soleaux.authority.contracts.OwnerSourceKind.GIT_HISTORY,
                source_path="src/history.py",
            ),
        )


async def test_history_is_opt_in_bounded_and_never_overrides_governance(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "CODEOWNERS": "src/history.py @declared\n",
            "src/history.py": "value = 1\n",
        },
    )
    history = _HistoryProvider()
    resolver = soleaux.authority.resolver.AuthorityResolver(history_provider=history)

    without_history = await resolver.resolve(bundle, include_history=False)
    assert history.calls == 0
    assert without_history.owners[0].data["owners"] == ("@declared",)

    with_history = await resolver.resolve(
        bundle,
        include_history=True,
        max_history_paths=1,
        max_history_commits=5,
    )
    assert history.calls == 1
    assert with_history.owners[0].data["owners"] == ("@declared",)
    assert (
        with_history.owners[0].data["owner_kind"]
        == soleaux.authority.contracts.OwnerKind.POLICY.value
    )
    assert {row.data["state"] for row in with_history.conflicts} == {
        soleaux.authority.contracts.GovernanceState.EFFECTIVE.value,
        soleaux.authority.contracts.GovernanceState.SHADOWED.value,
    }


async def test_malformed_structured_manifest_is_reported_without_inference(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "package.json": "{not-json",
            "src/main.ts": "export const value = 1;\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    assert result.entrypoints == ()
    assert result.owners == ()
    assert result.warnings == ("package.json: invalid JSON manifest",)


async def test_repository_conventions_have_no_implicit_governance_semantics(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "AGENTS.md": """\
# Agent notes

## Repository instructions

| Concern | Owner |
| --- | --- |
| Command checks | `.codex/rules/*.rules` |
""",
            ".codex/rules/example.rules": "prefix_rule(pattern=['git'])\n",
            ".claude/rules/example.md": "# Rule\n",
            ".husky/pre-commit": "pnpm check\n",
            ".github/workflows/ci.yml": "jobs: {}\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    assert result.policies == ()
    assert result.bindings == ()
    assert result.conflicts == ()


async def test_markdown_table_cells_and_distant_prose_do_not_claim_canonicality(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "docs/table-cell.md": """\
# Notes

| Topic | Location |
| --- | --- |
| canonical ownership registry | `src/cell.py` |
""",
            "docs/distant-prose.md": """\
# More notes

This table is the authoritative registry.

```text
The intervening block ends the local claim.
```

| Topic | Location |
| --- | --- |
| distant | `src/distant.py` |
""",
            "src/cell.py": "VALUE = 1\n",
            "src/distant.py": "VALUE = 2\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    assert result.policies == ()
    assert result.bindings == ()


async def test_arbitrary_structured_strings_do_not_claim_canonicality(
    tmp_path: pathlib.Path,
) -> None:
    bundle = await _capture(
        tmp_path,
        {
            "records/ordinary.json": json.dumps(
                {
                    "description": "This is a canonical ownership registry.",
                    "entries": [
                        {
                            "Rune": "rune-one",
                            "Artifact": "src/rune.py",
                        }
                    ],
                }
            ),
            "src/rune.py": "VALUE = 1\n",
        },
    )

    result = await soleaux.authority.resolver.AuthorityResolver().resolve(bundle)

    assert result.policies == ()
    assert result.bindings == ()
