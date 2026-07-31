"""D023: immutable semantic generations and deterministic reconciliation."""

from __future__ import annotations

import asyncio
import datetime
import typing

import pytest

import soleaux.contracts.repository
import soleaux.contracts.snapshot
import soleaux.lsp.broker
import soleaux.lsp.contracts
import soleaux.lsp.generation
import soleaux.lsp.sessions
import soleaux.structural.snapshot


def _content_hash(content: bytes) -> str:
    return soleaux.contracts.repository.content_digest(content)


def _bundle(
    contents: dict[str, bytes],
    *,
    changed_during_analysis: bool = False,
) -> soleaux.structural.snapshot.SnapshotBundle:
    files = tuple(
        soleaux.contracts.snapshot.CapturedFile(
            workspace_id="workspace",
            path=path,
            content_hash=_content_hash(content),
            byte_start=0,
            byte_end=len(content),
            start_line=0,
            start_column=0,
            end_line=content.count(b"\n"),
            end_column=0,
            encoding="utf-8",
            newline="lf",
            language="Python",
            producer_id="test",
            producer_version="1",
            producer_config_digest="test-config",
            claim_basis=soleaux.contracts.snapshot.ClaimBasis.SYNTAX,
        )
        for path, content in sorted(contents.items())
    )
    snapshot = soleaux.contracts.snapshot.RepositorySnapshot(
        snapshot_id="workspace:test",
        workspace_id="workspace",
        root="/workspace",
        created_at=datetime.datetime.now(datetime.UTC),
        files=files,
        source_fingerprint="test-snapshot",
        changed_during_analysis=changed_during_analysis,
    )
    return soleaux.structural.snapshot.SnapshotBundle(
        snapshot=snapshot, contents=dict(contents), notes=()
    )


def _generation(
    bundle: soleaux.structural.snapshot.SnapshotBundle,
    *,
    dependency_paths: tuple[str, ...] = ("src/dependency.py",),
    control_paths: tuple[str, ...] = ("pyproject.toml",),
) -> soleaux.lsp.generation.SemanticGeneration:
    return soleaux.lsp.generation.SemanticGeneration.from_snapshot(
        bundle,
        provider_name="pylsp",
        provider_config_digest="provider-config",
        process_epoch=7,
        requested_file="src/main.py",
        dependency_paths=dependency_paths,
        control_paths=control_paths,
    )


class _CountingBroker(soleaux.lsp.broker.LspBroker):
    def __init__(self) -> None:
        super().__init__(
            soleaux.lsp.contracts.LanguageServerSpec(
                language="Python",
                argv=("unused-provider",),
                provider_name="counting",
                provider_version="1",
            )
        )
        self.calls = 0
        self.release = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, typing.Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> typing.Any:
        self.calls += 1
        await self.release.wait()
        return {"method": method, "params": params, "timeout": timeout}


def test_semantic_generation_captures_only_frozen_snapshot_hashes() -> None:
    bundle = _bundle(
        {
            "pyproject.toml": b"[project]\nname = 'fixture'\n",
            "src/dependency.py": b"VALUE = 1\n",
            "src/main.py": b"from .dependency import VALUE\n",
        }
    )

    generation = _generation(bundle)

    assert generation.status is soleaux.lsp.generation.SemanticGenerationStatus.VERIFIED
    assert generation.complete is True
    assert generation.requested_hash == _content_hash(bundle.contents["src/main.py"])
    assert generation.dependencies[0].path == "src/dependency.py"
    assert generation.controls[0].path == "pyproject.toml"
    assert generation.missing_inputs == ()


def test_semantic_generation_marks_missing_or_drifting_inputs_unverified() -> None:
    bundle = _bundle(
        {"src/main.py": b"VALUE = 1\n"},
        changed_during_analysis=True,
    )

    generation = _generation(bundle)

    assert (
        generation.status
        is soleaux.lsp.generation.SemanticGenerationStatus.UNVERIFIED_WORKSPACE_INPUTS
    )
    assert generation.complete is False
    assert generation.missing_inputs == ("pyproject.toml", "src/dependency.py")
    assert generation.snapshot_changed_during_analysis is True


def test_open_document_change_reconciles_with_did_change() -> None:
    before = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 1\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )
    after = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 2\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )

    plan = soleaux.lsp.generation.SemanticGenerationBarrier.plan_reconciliation(
        before,
        after,
        open_documents=frozenset({"src/dependency.py"}),
        watched_files_supported=True,
    )

    assert [action.kind for action in plan.actions] == [
        soleaux.lsp.generation.ReconciliationActionKind.DID_CHANGE
    ]
    assert plan.actions[0].paths == ("src/dependency.py",)


@pytest.mark.parametrize(
    ("watched_files_supported", "expected"),
    [
        (True, soleaux.lsp.generation.ReconciliationActionKind.DID_CHANGE_WATCHED_FILES),
        (False, soleaux.lsp.generation.ReconciliationActionKind.RESTART),
    ],
)
def test_unopened_change_uses_watched_files_or_restart(
    watched_files_supported: bool,
    expected: soleaux.lsp.generation.ReconciliationActionKind,
) -> None:
    before = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 1\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )
    after = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 2\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )

    plan = soleaux.lsp.generation.SemanticGenerationBarrier.plan_reconciliation(
        before,
        after,
        open_documents=frozenset(),
        watched_files_supported=watched_files_supported,
    )

    assert [action.kind for action in plan.actions] == [expected]
    assert plan.actions[0].paths == ("src/dependency.py",)


def test_control_change_always_requires_restart() -> None:
    before = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\nname = 'before'\n",
                "src/dependency.py": b"VALUE = 1\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )
    after = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\nname = 'after'\n",
                "src/dependency.py": b"VALUE = 1\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )

    plan = soleaux.lsp.generation.SemanticGenerationBarrier.plan_reconciliation(
        before,
        after,
        open_documents=frozenset({"pyproject.toml"}),
        watched_files_supported=True,
    )

    assert [action.kind for action in plan.actions] == [
        soleaux.lsp.generation.ReconciliationActionKind.RESTART
    ]
    assert plan.actions[0].paths == ("pyproject.toml",)


def test_project_compiler_identity_partitions_semantic_generations() -> None:
    bundle = _bundle({"src/main.py": b"VALUE = 1\n"})
    first_identity = soleaux.lsp.generation.SemanticProjectIdentity(
        project_id="workspace:package-a",
        project_root="package-a",
        project_config_digest="a" * 64,
        compiler_identity="typescript@6.0.2",
    )
    second_identity = first_identity.model_copy(
        update={
            "project_id": "workspace:package-b",
            "project_root": "package-b",
            "project_config_digest": "b" * 64,
            "compiler_identity": "typescript@7.0.2",
        }
    )

    first = soleaux.lsp.generation.SemanticGeneration.from_snapshot(
        bundle,
        provider_name="typescript-language-server",
        provider_config_digest="provider-config",
        process_epoch=0,
        requested_file="src/main.py",
        project_identity=first_identity,
    )
    second = soleaux.lsp.generation.SemanticGeneration.from_snapshot(
        bundle,
        provider_name="typescript-language-server",
        provider_config_digest="provider-config",
        process_epoch=0,
        requested_file="src/main.py",
        project_identity=second_identity,
    )

    assert first.fingerprint != second.fingerprint
    assert soleaux.lsp.sessions.LspSessionManager.key_for_generation(
        first
    ) != soleaux.lsp.sessions.LspSessionManager.key_for_generation(second)


async def test_equivalent_requests_from_different_generations_do_not_coalesce() -> None:
    before = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 1\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )
    after = _generation(
        _bundle(
            {
                "pyproject.toml": b"[project]\n",
                "src/dependency.py": b"VALUE = 2\n",
                "src/main.py": b"from .dependency import VALUE\n",
            }
        )
    )
    assert before.fingerprint != after.fingerprint

    sessions = soleaux.lsp.sessions.LspSessionManager()
    broker = _CountingBroker()
    first = asyncio.create_task(
        sessions.request(
            broker=broker,
            generation=before,
            method="textDocument/definition",
            params={"same": True},
            response_schema="definition-v1",
        )
    )
    second = asyncio.create_task(
        sessions.request(
            broker=broker,
            generation=after,
            method="textDocument/definition",
            params={"same": True},
            response_schema="definition-v1",
        )
    )
    try:
        for _ in range(50):
            if broker.calls == 2:
                break
            await asyncio.sleep(0.01)
        assert broker.calls == 2
        broker.release.set()
        await asyncio.gather(first, second)
    finally:
        broker.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await sessions.shutdown()
