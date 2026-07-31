"""`framework.registrations` rows, evidence, and honest coverage.

Fixtures here are synthetic on purpose. soleaux is a redistributable tool, so
the conventions a consumer may use matter more than the ones this repository
happens to contain — and this repository contains almost none of them.
"""

from __future__ import annotations

import datetime
import hashlib

import soleaux.analysis.frame
import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.requests
import soleaux.contracts.snapshot
import soleaux.contracts.tables
import soleaux.frameworks.nextjs
import soleaux.frameworks.registrations
import soleaux.structural.snapshot
import soleaux.structural.supervisor
import soleaux.tables.planner

_NEXT_MANIFEST = b'{"name":"demo","dependencies":{"next":"16.0.0"}}\n'
_PAGE = b"export default function Page() { return null }\n"


def _bundle(
    files: dict[str, bytes], notes: tuple[str, ...] = ()
) -> soleaux.structural.snapshot.SnapshotBundle:
    captured: list[soleaux.contracts.snapshot.CapturedFile] = []
    for path, content in files.items():
        extension = f".{path.rsplit('.', 1)[-1].lower()}" if "." in path else ""
        captured.append(
            soleaux.contracts.snapshot.CapturedFile(
                workspace_id="workspace",
                path=path,
                content_hash=hashlib.blake2b(content, digest_size=32).hexdigest(),
                byte_start=0,
                byte_end=len(content),
                start_line=0,
                start_column=0,
                end_line=content.count(b"\n"),
                end_column=0,
                encoding="utf-8",
                newline="lf",
                language=soleaux.structural.snapshot.LANGUAGE_BY_EXTENSION.get(extension),
                producer_id="test",
                producer_version="1",
                producer_config_digest="test",
                claim_basis=soleaux.contracts.snapshot.ClaimBasis.SYNTAX,
            )
        )
    snapshot = soleaux.contracts.snapshot.RepositorySnapshot(
        snapshot_id="workspace:test",
        workspace_id="workspace",
        root="/workspace",
        created_at=datetime.datetime.now(datetime.UTC),
        files=tuple(captured),
        source_fingerprint="snapshot-fingerprint",
    )
    return soleaux.structural.snapshot.SnapshotBundle(
        snapshot=snapshot, contents=dict(files), notes=notes
    )


def _routes(files: dict[str, bytes]) -> dict[str, str]:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(_bundle(files))
    return {str(row.data["path"]): str(row.data["route"]) for row in rows}


async def _structural_registration_result(
    files: dict[str, bytes],
) -> tuple[tuple[soleaux.contracts.frame.FactRow, ...], tuple[str, ...]]:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    producer = soleaux.analysis.frame.StructuralTableProducer(supervisor)
    try:
        output = await producer.produce(
            ("framework.registrations",),
            _bundle(files),
            soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            {},
        )
        return output["framework.registrations"], producer.coverage_notes()
    finally:
        await supervisor.aclose()


async def _structural_routes(files: dict[str, bytes]) -> dict[str, str]:
    rows, _notes = await _structural_registration_result(files)
    return {str(row.data["path"]): str(row.data["route"]) for row in rows}


async def test_registrations_without_config_do_not_start_structural_worker() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    producer = soleaux.analysis.frame.StructuralTableProducer(supervisor)
    try:
        output = await producer.produce(
            ("framework.registrations",),
            _bundle({"package.json": _NEXT_MANIFEST, "app/page.tsx": _PAGE}),
            soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            {},
        )
        assert output["framework.registrations"]
        assert supervisor.started is False
    finally:
        await supervisor.aclose()


def test_app_router_conventions_become_routes() -> None:
    routes = _routes(
        {
            "package.json": _NEXT_MANIFEST,
            "app/page.tsx": _PAGE,
            "app/layout.tsx": _PAGE,
            "app/(marketing)/about/page.tsx": _PAGE,
            "app/blog/[slug]/page.tsx": _PAGE,
            "app/docs/[[...slug]]/page.tsx": _PAGE,
            "app/api/health/route.ts": _PAGE,
            "app/dashboard/@team/page.tsx": _PAGE,
            "app/_private/page.tsx": _PAGE,
        }
    )

    assert routes["app/page.tsx"] == "/"
    assert routes["app/layout.tsx"] == "/"
    # Route groups are organizational and never appear in the URL.
    assert routes["app/(marketing)/about/page.tsx"] == "/about"
    assert routes["app/blog/[slug]/page.tsx"] == "/blog/[slug]"
    assert routes["app/docs/[[...slug]]/page.tsx"] == "/docs/[[...slug]]"
    assert routes["app/api/health/route.ts"] == "/api/health"
    # A named slot collapses to its parent segment.
    assert routes["app/dashboard/@team/page.tsx"] == "/dashboard"
    # Underscore-prefixed parts are pruned before enumeration.
    assert "app/_private/page.tsx" not in routes


def test_parallel_default_is_a_registration_without_becoming_a_route() -> None:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/dashboard/@team/default.tsx": _PAGE,
            }
        )
    )

    (row,) = rows
    assert row.data["kind"] == "default"
    assert row.data["route"] is None
    assert row.data["parallel_slots"] == ["@team"]


def test_pages_router_skips_reserved_pages_but_keeps_api() -> None:
    routes = _routes(
        {
            "package.json": _NEXT_MANIFEST,
            "pages/index.tsx": _PAGE,
            "pages/about.tsx": _PAGE,
            "pages/api/users.ts": _PAGE,
            "pages/_app.tsx": _PAGE,
            "pages/_document.tsx": _PAGE,
        }
    )

    assert routes["pages/index.tsx"] == "/"
    assert routes["pages/about.tsx"] == "/about"
    assert routes["pages/api/users.ts"] == "/api/users"
    assert "pages/_app.tsx" not in routes
    assert "pages/_document.tsx" not in routes


def test_route_and_file_are_both_reported() -> None:
    """The file path is the fact the upstream devtools tool discards."""
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle({"package.json": _NEXT_MANIFEST, "app/blog/[slug]/page.tsx": _PAGE})
    )

    (row,) = rows
    assert row.data["path"] == "app/blog/[slug]/page.tsx"
    assert row.data["route"] == "/blog/[slug]"
    assert row.data["router"] == "app"
    assert row.data["kind"] == "page"
    assert row.data["dynamic_segments"] == [
        {"segment": "[slug]", "param": "slug", "kind": "required", "index": 2}
    ]


def test_two_projects_may_share_a_route_and_are_disambiguated_by_project() -> None:
    rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "apps/one/package.json": _NEXT_MANIFEST,
                "apps/one/app/page.tsx": _PAGE,
                "apps/two/package.json": _NEXT_MANIFEST,
                "apps/two/app/page.tsx": _PAGE,
            }
        )
    )

    assert [row.data["project_dir"] for row in rows] == ["apps/one", "apps/two"]
    assert {str(row.data["route"]) for row in rows} == {"/"}
    # Two apps legitimately both serve "/", so this must not read as a conflict.
    assert not any("resolves exactly one" in note for note in notes)


def test_layout_beside_a_page_is_not_a_duplicate_route() -> None:
    """Every Next.js app has a root layout beside its root page.

    Both normalize to `/`, so a duplicate check that ignored the registration
    kind would report a conflict for essentially every project.
    """
    _rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/layout.tsx": _PAGE,
                "app/page.tsx": _PAGE,
            }
        )
    )

    assert not any("resolves exactly one" in note for note in notes)


def test_page_beside_a_route_handler_is_a_duplicate_route() -> None:
    """Upstream forbids `route` at the same segment level as `page`."""
    _rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/thing/page.tsx": _PAGE,
                "app/thing/route.ts": _PAGE,
            }
        )
    )

    assert any("/thing" in note and "resolves exactly one" in note for note in notes)


def test_duplicate_route_within_one_project_is_reported() -> None:
    _rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/(a)/about/page.tsx": _PAGE,
                "app/(b)/about/page.tsx": _PAGE,
            }
        )
    )

    assert any("/about" in note and "resolves exactly one" in note for note in notes)


def test_evidence_anchors_the_whole_registration_file() -> None:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle({"package.json": _NEXT_MANIFEST, "app/page.tsx": _PAGE})
    )

    (row,) = rows
    assert row.evidence.path == "app/page.tsx"
    assert row.evidence.range.byte_start == 0
    assert row.evidence.range.byte_end == len(_PAGE)
    assert row.evidence.range.start_line == 1
    assert row.evidence.resolution_status is soleaux.contracts.evidence.ResolutionStatus.RESOLVED
    assert row.evidence.confidence == 1.0


def test_intercepting_route_is_resolved_and_marked_without_becoming_a_candidate() -> None:
    """Upstream leaks the marker into the pattern; the port also resolves the target.

    The row stays RESOLVED because the file is known exactly. Reduced confidence
    marks an odd-but-certain pattern, not doubt about whether the row belongs.
    """
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle({"package.json": _NEXT_MANIFEST, "app/feed/(..)photo/page.tsx": _PAGE})
    )

    (row,) = rows
    assert row.data["route"] == "/feed/(..)photo"
    assert row.data["intercepting_marker"] == "(..)"
    assert row.data["intercepting_target"] == "/photo"
    assert row.evidence.resolution_status is soleaux.contracts.evidence.ResolutionStatus.RESOLVED
    assert row.evidence.confidence == 0.6


async def test_custom_page_extensions_are_read_from_config() -> None:
    routes = await _structural_routes(
        {
            "package.json": _NEXT_MANIFEST,
            "next.config.ts": b"export default { pageExtensions: ['mdx', 'tsx'] }\n",
            "app/page.mdx": _PAGE,
            "app/legacy/page.js": _PAGE,
        }
    )

    assert routes["app/page.mdx"] == "/"
    # `js` is absent from the configured list, so this is not a route file.
    assert "app/legacy/page.js" not in routes


async def test_commented_page_extensions_do_not_change_routes() -> None:
    routes = await _structural_routes(
        {
            "package.json": _NEXT_MANIFEST,
            "next.config.ts": b"// pageExtensions: ['mdx']\nexport default {}\n",
            "app/page.tsx": _PAGE,
            "app/page.mdx": _PAGE,
        }
    )

    assert routes["app/page.tsx"] == "/"
    assert "app/page.mdx" not in routes


async def test_literal_page_extensions_allow_comments_and_trailing_commas() -> None:
    routes = await _structural_routes(
        {
            "package.json": _NEXT_MANIFEST,
            "next.config.ts": b"""export default {
  pageExtensions: [
    'mdx',
    // Keep TypeScript pages enabled.
    'tsx',
  ],
}
""",
            "app/page.mdx": _PAGE,
            "app/legacy/page.js": _PAGE,
        }
    )

    assert routes["app/page.mdx"] == "/"
    assert "app/legacy/page.js" not in routes


async def test_unevaluated_page_extensions_are_reported() -> None:
    _rows, notes = await _structural_registration_result(
        {
            "package.json": _NEXT_MANIFEST,
            "next.config.ts": b"export default { pageExtensions: EXTS }\n",
            "app/page.tsx": _PAGE,
        }
    )

    assert any("pageExtensions" in note for note in notes)


async def test_unread_route_affecting_config_is_reported() -> None:
    _rows, notes = await _structural_registration_result(
        {
            "package.json": _NEXT_MANIFEST,
            "next.config.ts": b"export default { basePath: '/docs' }\n",
            "app/page.tsx": _PAGE,
        }
    )

    assert any("basePath" in note for note in notes)


def test_metadata_route_files_are_reported_as_outside_the_baseline() -> None:
    _rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/page.tsx": _PAGE,
                "app/sitemap.ts": _PAGE,
            }
        )
    )

    assert any("metadata" in note for note in notes)


def test_next_version_outside_the_declared_baseline_is_reported() -> None:
    rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": b'{"dependencies":{"next":"15.5.0"}}\n',
                "app/page.tsx": _PAGE,
            }
        )
    )

    assert rows
    assert any("next@15.5.0" in note and "outside the supported" in note for note in notes)


async def test_next_version_outside_the_declared_baseline_degrades_the_frame() -> None:
    supervisor = soleaux.structural.supervisor.StructuralWorkerSupervisor()
    try:
        planner = soleaux.tables.planner.TablePlanner()
        frame = await planner.execute(
            planner.plan(
                include_tables=("framework.registrations",),
                exclude_tables=(),
            ),
            bundle=_bundle(
                {
                    "package.json": b'{"dependencies":{"next":"15.5.0"}}\n',
                    "app/page.tsx": _PAGE,
                }
            ),
            semantic_mode=soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY,
            producers={
                soleaux.contracts.tables.Producer.STRUCTURAL: (
                    soleaux.analysis.frame.StructuralTableProducer(supervisor)
                )
            },
        )
    finally:
        await supervisor.aclose()

    assert frame.coverage.status is soleaux.contracts.coverage.FrameStatus.PARTIAL
    assert any("next@15.5.0" in note for note in frame.coverage.omitted_reasons)


def test_uncaptured_metadata_file_is_reported_and_never_claimed() -> None:
    """A binary metadata file cannot anchor evidence, so it must be a note."""
    rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {"package.json": _NEXT_MANIFEST, "app/page.tsx": _PAGE},
            notes=("skipped binary file app/favicon.ico",),
        )
    )

    assert [str(row.data["path"]) for row in rows] == ["app/page.tsx"]
    assert any("app/favicon.ico" in note for note in notes)


def test_bounded_snapshot_capture_is_reported() -> None:
    _rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {"package.json": _NEXT_MANIFEST, "app/page.tsx": _PAGE},
            notes=("file count limit 4096 reached",),
        )
    )

    assert any("bounded" in note for note in notes)


def test_proxy_is_reported_as_a_registration_without_a_route() -> None:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/page.tsx": _PAGE,
                "proxy.ts": b"export const config = { matcher: ['/x'] }\n",
            }
        )
    )

    proxy = [row for row in rows if row.data["kind"] == "proxy"]
    assert [str(row.data["path"]) for row in proxy] == ["proxy.ts"]
    assert proxy[0].data["route"] is None


def test_src_proxy_is_detected_like_upstream() -> None:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "src/app/page.tsx": _PAGE,
                "src/proxy.ts": b"export const config = { matcher: ['/x'] }\n",
            }
        )
    )

    proxy = [row for row in rows if row.data["kind"] == "proxy"]
    assert [str(row.data["path"]) for row in proxy] == ["src/proxy.ts"]


def test_proxy_and_middleware_together_are_rejected_like_upstream() -> None:
    rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/page.tsx": _PAGE,
                "proxy.ts": _PAGE,
                "middleware.ts": _PAGE,
            }
        )
    )

    assert any("declares both proxy and middleware" in note for note in notes)
    proxy = [row for row in rows if row.data["kind"] == "proxy"]
    assert [str(row.data["path"]) for row in proxy] == ["proxy.ts"]


def test_proxy_resolves_on_the_page_extensions_axis() -> None:
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/page.tsx": _PAGE,
                "proxy.cjs": _PAGE,
            }
        )
    )

    assert [row for row in rows if row.data["kind"] == "proxy"] == []


def test_pages_router_skips_declaration_files() -> None:
    routes = _routes(
        {
            "package.json": _NEXT_MANIFEST,
            "pages/index.tsx": _PAGE,
            "pages/about.d.ts": b"export declare const x: number\n",
        }
    )

    assert routes["pages/index.tsx"] == "/"
    assert "pages/about.d.ts" not in routes


def test_interception_markers_are_capped_at_two_levels() -> None:
    assert soleaux.frameworks.nextjs._interception_marker("(..)(..)feed") == (
        "(..)(..)",
        "feed",
    )
    assert soleaux.frameworks.nextjs._interception_marker("(..)(..)(..)feed") == (
        "(..)(..)",
        "(..)feed",
    )
    assert soleaux.frameworks.nextjs._interception_marker("(.)sibling") == (
        "(.)",
        "sibling",
    )
    assert soleaux.frameworks.nextjs._interception_marker("(...)root") == (
        "(...)",
        "root",
    )
    assert soleaux.frameworks.nextjs._interception_marker("feed") is None


def test_next_config_extensions_match_upstream() -> None:
    assert soleaux.frameworks.nextjs.is_next_config_path("next.config.ts")
    assert soleaux.frameworks.nextjs.is_next_config_path("next.config.mts")
    assert soleaux.frameworks.nextjs.is_next_config_path("next.config.js")
    assert soleaux.frameworks.nextjs.is_next_config_path("next.config.mjs")
    assert not soleaux.frameworks.nextjs.is_next_config_path("next.config.cjs")
    assert not soleaux.frameworks.nextjs.is_next_config_path("next.config.cts")


def test_next_dependency_without_a_router_directory_is_reported_not_enumerated() -> None:
    rows, notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": b'{"name":"cfg","devDependencies":{"next":"16.0.0"}}\n',
                "next.config.ts": b"export default {}\n",
                "src/index.ts": _PAGE,
            }
        )
    )

    assert rows == ()
    assert any("no app/ or pages/" in note for note in notes)


def test_nested_project_attributes_routes_to_the_innermost_project() -> None:
    """A vendored app inside another project must not be counted twice."""
    rows, _notes = soleaux.frameworks.registrations.build_registrations(
        _bundle(
            {
                "package.json": _NEXT_MANIFEST,
                "app/page.tsx": _PAGE,
                "examples/demo/package.json": _NEXT_MANIFEST,
                "examples/demo/app/page.tsx": _PAGE,
            }
        )
    )

    owners = {str(row.data["path"]): str(row.data["project_dir"]) for row in rows}
    assert owners["app/page.tsx"] == ""
    assert owners["examples/demo/app/page.tsx"] == "examples/demo"
