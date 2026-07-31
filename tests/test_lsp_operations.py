"""D023/D029/D031: semantic modes, operation mapping, and session reuse."""

import sys
from pathlib import Path

from _assertions import object_list, object_mapping, raises_with_message

from soleaux.contracts.coverage import FrameStatus
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.workspace import AllowedWorkspaceSet
from soleaux.lsp.broker import SemanticProviderRequiredError
from soleaux.lsp.contracts import LspCapability, NavigationRequest, SemanticOperation
from soleaux.lsp.operations import CapabilityResolution
from soleaux.lsp.providers import ConfiguredProvider, ProviderRegistry
from soleaux.lsp.resolvers import SemanticResolver, resolve_named_symbols
from soleaux.structural.snapshot import RepositorySnapshotter, SnapshotBundle

FAKE_SERVER = Path(__file__).parent / "fixtures" / "repositories" / "lsp-fake" / "fake_server.py"


def test_resolve_named_symbols_filters_exact_name_kind_and_path() -> None:
    selected_uri = "file:///workspace/main.py"
    resolution = CapabilityResolution(
        capability=LspCapability.WORKSPACE_SYMBOL,
        status=FrameStatus.COMPLETE,
        generation=None,
        payload=[
            {
                "name": "target",
                "kind": 5,
                "location": {
                    "uri": selected_uri,
                    "range": {
                        "start": {"line": 1, "character": 2},
                        "end": {"line": 1, "character": 8},
                    },
                },
            },
            {
                "name": "target",
                "kind": 12,
                "location": {
                    "uri": selected_uri,
                    "range": {
                        "start": {"line": 3, "character": 4},
                        "end": {"line": 3, "character": 10},
                    },
                },
            },
            {
                "name": "target",
                "kind": 5,
                "location": {
                    "uri": "file:///workspace/other.py",
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 6},
                    },
                },
            },
            {
                "name": "target_helper",
                "kind": 5,
                "location": {
                    "uri": selected_uri,
                    "range": {
                        "start": {"line": 5, "character": 0},
                        "end": {"line": 5, "character": 13},
                    },
                },
            },
        ],
    )

    matches = resolve_named_symbols(
        resolution,
        name="target",
        kind="class",
        path=selected_uri,
    )

    assert matches.truncated is False
    assert len(matches.candidates) == 1
    assert matches.candidates[0].kind == 5
    assert matches.candidates[0].location.range.start.line == 1


def _payload_items(payload: object) -> list[dict[str, object]]:
    return [object_mapping(item) for item in object_list(payload)]


async def _resolver_fixture(
    tmp_path: Path,
    *,
    server_flags: tuple[str, ...] = (),
    diagnostic_timeout_seconds: float = 5.0,
    include_other: bool = False,
    name_navigation_timeout_seconds: float = 10.0,
) -> tuple[SemanticResolver, SnapshotBundle]:
    source = tmp_path / "main.py"
    source.write_text("def target():\n    return 1\n\ntarget()\n", encoding="utf-8")
    scope = ["main.py"]
    if include_other:
        (tmp_path / "other.py").write_text(
            "def target():\n    return 2\n\ntarget()\n",
            encoding="utf-8",
        )
        scope.append("other.py")
    workspace = AllowedWorkspaceSet.from_launch(
        [("workspace", str(tmp_path))],
        config_digest="test",
    ).get("workspace")
    bundle = await RepositorySnapshotter(workspace).capture(scope=tuple(scope))
    provider = ConfiguredProvider(
        provider_name="fake-lsp",
        provider_version="1",
        argv=(sys.executable, str(FAKE_SERVER), "0", *server_flags),
        extensions=("py",),
        root=tmp_path,
        config_digest="fake-config",
    )
    return (
        SemanticResolver(
            ProviderRegistry((provider,)),
            diagnostic_timeout_seconds=diagnostic_timeout_seconds,
            name_navigation_timeout_seconds=name_navigation_timeout_seconds,
        ),
        bundle,
    )


async def test_definition_and_incoming_calls_reuse_one_session(tmp_path: Path) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    try:
        definition = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.DEFINITION,
                path="main.py",
                line=4,
                column=2,
            ),
            bundle,
        )
        incoming = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.INCOMING_CALLS,
                path="main.py",
                line=1,
                column=5,
            ),
            bundle,
        )

        assert definition.status is FrameStatus.COMPLETE
        assert definition.locations[0].uri == (tmp_path / "main.py").as_uri()
        assert incoming.status is FrameStatus.COMPLETE
        assert incoming.symbols[0].name == "caller"
        assert resolver.active_session_count == 1
    finally:
        await resolver.shutdown()


async def test_name_navigation_resolves_unique_symbol_in_one_call(tmp_path: Path) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                symbol_name="target",
            ),
            bundle,
        )

        assert result.status is FrameStatus.COMPLETE
        assert result.locations[0].uri == (tmp_path / "main.py").as_uri()
        assert result.locations[0].range.start.line == 3
        assert result.omitted_reasons == ()
    finally:
        await resolver.shutdown()


async def test_name_navigation_reports_sorted_ambiguous_candidates(tmp_path: Path) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("ambiguous-symbols",),
        include_other=True,
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                symbol_name="target",
            ),
            bundle,
        )

        assert result.status is FrameStatus.PARTIAL
        assert result.omitted_reasons == ("ambiguous symbol name; refine with path or symbol_kind",)
        assert isinstance(result.payload, dict)
        candidate_items = _payload_items(result.payload["candidates"])
        assert [candidate["path"] for candidate in candidate_items] == [
            "main.py",
            "other.py",
        ]
    finally:
        await resolver.shutdown()


async def test_name_navigation_path_filter_selects_intended_declaration(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("ambiguous-symbols",),
        include_other=True,
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                path="other.py",
                symbol_name="target",
            ),
            bundle,
        )

        assert result.status is FrameStatus.COMPLETE
        assert result.locations[0].uri == (tmp_path / "other.py").as_uri()
    finally:
        await resolver.shutdown()


async def test_name_navigation_kind_filter_selects_intended_declaration(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("mixed-symbol-kinds", "echo-reference-position"),
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                path="main.py",
                symbol_name="target",
                symbol_kind="class",
            ),
            bundle,
        )

        assert result.status is FrameStatus.COMPLETE
        assert result.locations[0].range.start.line == 1
    finally:
        await resolver.shutdown()


async def test_name_navigation_caps_matches_before_resolving_ambiguity(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("many-symbols",),
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                symbol_name="target",
            ),
            bundle,
        )

        assert result.status is FrameStatus.TRUNCATED
        assert result.omitted_reasons == ("name match limit reached",)
        assert isinstance(result.payload, dict)
        candidates = result.payload["candidates"]
        assert isinstance(candidates, list)
        assert len(candidates) == 20
    finally:
        await resolver.shutdown()


async def test_name_navigation_deadline_stops_before_target_request(tmp_path: Path) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("slow-symbols",),
        name_navigation_timeout_seconds=0.01,
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                symbol_name="target",
            ),
            bundle,
        )

        assert result.status is FrameStatus.TRUNCATED
        assert result.locations == ()
        assert result.omitted_reasons == ("name navigation deadline reached",)
    finally:
        await resolver.shutdown()


async def test_name_navigation_limits_target_results(tmp_path: Path) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("many-references",),
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                symbol_name="target",
                limit=3,
            ),
            bundle,
        )

        assert result.status is FrameStatus.TRUNCATED
        assert len(result.locations) == 3
        assert isinstance(result.payload, list)
        assert len(result.payload) == 3
        assert result.omitted_reasons == ("navigation result limit reached",)
    finally:
        await resolver.shutdown()


async def test_empty_navigation_skips_out_of_range_adjacent_positions(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("empty-references",),
    )
    try:
        result = await resolver.navigate(
            NavigationRequest(
                operation=SemanticOperation.REFERENCES,
                path="main.py",
                line=5,
                column=1,
            ),
            bundle,
        )

        assert result.status is FrameStatus.COMPLETE
        assert result.locations == ()
        assert result.payload == []
        assert result.omitted_reasons == ()
    finally:
        await resolver.shutdown()


async def test_semantic_required_rejects_incomplete_generation_before_start(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    try:
        with raises_with_message(SemanticProviderRequiredError, "unverified_workspace_inputs"):
            await resolver.navigate(
                NavigationRequest(
                    operation=SemanticOperation.DEFINITION,
                    path="main.py",
                    line=1,
                    column=1,
                    semantic_mode=SemanticMode.SEMANTIC_REQUIRED,
                ),
                bundle,
                dependency_paths=("missing.py",),
            )
        assert resolver.active_session_count == 0
    finally:
        await resolver.shutdown()


async def test_all_17_capabilities_execute_through_package_owned_broker(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    exercised: set[LspCapability] = set()
    try:
        for operation in SemanticOperation:
            result = await resolver.navigate(
                NavigationRequest(
                    operation=operation,
                    path="main.py",
                    line=1,
                    column=5,
                ),
                bundle,
            )
            assert result.status is FrameStatus.COMPLETE
            exercised.add(result.capability)

        capability_arguments = {
            LspCapability.WORKSPACE_SYMBOL: {"query": "target"},
            LspCapability.FORMAT_DOCUMENT: {},
            LspCapability.FORMAT_RANGE: {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 6},
                }
            },
            LspCapability.CODE_ACTIONS: {},
            LspCapability.COMPLETION: {},
            LspCapability.DIAGNOSTICS: {},
            LspCapability.SIGNATURE_HELP: {},
            LspCapability.RENAME: {"newName": "renamed"},
            LspCapability.RENAME_STRICT: {"newName": "renamed"},
        }
        for capability, arguments in capability_arguments.items():
            result = await resolver.execute_capability(
                capability,
                bundle,
                path="main.py",
                line=1,
                column=5,
                arguments=arguments,
            )
            assert result.status is FrameStatus.COMPLETE
            exercised.add(result.capability)

        restarted = await resolver.execute_capability(
            LspCapability.RESTART,
            bundle,
            path="main.py",
        )
        assert restarted.status is FrameStatus.COMPLETE
        exercised.add(restarted.capability)

        assert exercised == set(LspCapability)
        assert resolver.active_session_count == 0
    finally:
        await resolver.shutdown()


async def test_pull_diagnostics_send_previous_result_id_and_retain_unchanged_items(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    try:
        first = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )
        second = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )

        assert first.status is FrameStatus.COMPLETE
        assert second.status is FrameStatus.COMPLETE
        assert first.payload == second.payload
        assert _payload_items(second.payload)[0]["source"] == "pull"
    finally:
        await resolver.shutdown()


async def test_first_diagnostics_call_waits_for_delayed_dynamic_pull_registration(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("delayed-dynamic-diagnostics",),
        diagnostic_timeout_seconds=0.2,
    )
    try:
        first = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )

        assert first.status is FrameStatus.COMPLETE
        assert _payload_items(first.payload)[0]["source"] == "pull"
        assert first.omitted_reasons == ()
    finally:
        await resolver.shutdown()


async def test_diagnostic_pull_failure_falls_back_to_compatible_push_state(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path, server_flags=("pull-error",))
    try:
        result = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )

        assert result.status is FrameStatus.COMPLETE
        assert _payload_items(result.payload)[0]["source"] == "push"
        assert result.omitted_reasons == ()
    finally:
        await resolver.shutdown()


async def test_generation_change_does_not_reuse_previous_diagnostic_result_id(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(tmp_path)
    try:
        first = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )
        (tmp_path / "main.py").write_text(
            "def target():\n    return 2\n\ntarget()\n",
            encoding="utf-8",
        )
        workspace = AllowedWorkspaceSet.from_launch(
            [("workspace", str(tmp_path))],
            config_digest="test",
        ).get("workspace")
        changed_bundle = await RepositorySnapshotter(workspace).capture(scope=("main.py",))

        changed = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            changed_bundle,
            path="main.py",
        )

        assert first.status is FrameStatus.COMPLETE
        assert changed.status is FrameStatus.COMPLETE
        assert _payload_items(changed.payload)[0]["source"] == "fresh-pull"
    finally:
        await resolver.shutdown()


async def test_diagnostics_timeout_without_pull_or_matching_push_is_partial(
    tmp_path: Path,
) -> None:
    resolver, bundle = await _resolver_fixture(
        tmp_path,
        server_flags=("no-diagnostics",),
        diagnostic_timeout_seconds=0.01,
    )
    try:
        result = await resolver.execute_capability(
            LspCapability.DIAGNOSTICS,
            bundle,
            path="main.py",
        )

        assert result.status is FrameStatus.PARTIAL
        assert result.payload == []
        assert result.omitted_reasons == (
            "provider did not return diagnostics for the current document generation",
        )
    finally:
        await resolver.shutdown()
