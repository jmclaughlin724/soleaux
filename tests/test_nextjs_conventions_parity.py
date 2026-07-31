"""Differential conformance: the Next.js port against the installed framework.

`soleaux.frameworks.nextjs` reimplements Next.js route conventions in Python so
route enumeration needs no Node.js, no build, and no dev server at runtime. That
fork can drift silently whenever Next.js changes a convention, so this test
executes the *installed* framework's own pure modules as an oracle and compares
them path by path.

The oracle is developer-side only: `tests/` is excluded from the sdist, so no
consumer ever needs Node.js. It skips when Node.js or Next.js is unavailable
unless `SOLEAUX_REQUIRE_CONFORMANCE=1`, which turns a missing oracle into a
failure so a CI that owns the guarantee cannot silently stop checking.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from soleaux.frameworks.nextjs import (
    DEFAULT_PAGE_EXTENSIONS,
    NEXT_CONVENTIONS_RANGE,
    NEXT_CONVENTIONS_VERIFIED_VERSION,
    SKIP_PAGE_KEYS,
    create_valid_file_matcher,
    get_page_from_path,
    is_metadata_stem,
    is_supported_next_version,
    normalize_app_path,
    normalize_layout_route,
)

# App-dir-relative paths, matching `recursiveReadDir`'s leading-slash output.
CONVENTION_CORPUS: tuple[str, ...] = (
    # Plain pages and nested pages.
    "/page.tsx",
    "/about/page.tsx",
    "/a/b/c/page.tsx",
    "/index/page.tsx",
    # Route handlers.
    "/route.ts",
    "/api/users/route.ts",
    # Dynamic segments.
    "/blog/[slug]/page.tsx",
    "/shop/[...slug]/page.tsx",
    "/docs/[[...slug]]/page.tsx",
    "/[org]/[repo]/page.tsx",
    "/api/users/[id]/route.ts",
    # Route groups, including nested and group+dynamic.
    "/(marketing)/about/page.tsx",
    "/(a)/(b)/x/page.tsx",
    "/(shop)/cart/[id]/page.tsx",
    "/(blank)/pages/(onboarding)/step/page.tsx",
    # Parallel slots.
    "/dashboard/@team/page.tsx",
    "/dashboard/@team/default.tsx",
    "/dashboard/@children/page.tsx",
    # Intercepting routes.
    "/@modal/(.)photo/[id]/page.tsx",
    "/feed/(..)photo/page.tsx",
    "/a/b/(..)(..)photo/page.tsx",
    "/(...)photo/page.tsx",
    # Layouts at several depths.
    "/layout.tsx",
    "/about/layout.tsx",
    "/(marketing)/about/layout.tsx",
    "/dashboard/@team/layout.tsx",
    # default.tsx and not-found placement.
    "/default.tsx",
    "/dashboard/default.tsx",
    "/not-found.tsx",
    "/about/not-found.tsx",
    # Private folders and files.
    "/_private/page.tsx",
    "/_x.tsx",
    "/blog/_components/Post.tsx",
    # Encoded underscore.
    "/%5Fprivate/page.tsx",
    # Non-route files that must not become routes.
    "/page.d.ts",
    "/mypage.tsx",
    "/pagelike.tsx",
    "/page.mdx",
    "/about/helper.ts",
    "/layout-helper.tsx",
    "/routes.ts",
    # Reserved-looking app paths.
    "/_not-found/page.tsx",
    "/_global-error/page.tsx",
    # Metadata conventions: outside the port's baseline, so these must be
    # classified as metadata by upstream and reported rather than enumerated.
    "/sitemap.ts",
    "/blog/sitemap.ts",
    "/robots.ts",
    "/manifest.ts",
    "/favicon.ico",
    "/icon.png",
    "/apple-icon.png",
    "/opengraph-image.tsx",
    "/(post)/opengraph-image.tsx",
    "/twitter-image.jpg",
)

# A second run proves the matcher is genuinely extension-parameterized.
ALT_PAGE_EXTENSIONS: tuple[str, ...] = ("mdx", "tsx")

_ORACLE_SCRIPT = r"""
const [nextRoot, corpusJson, extensionsJson] = process.argv.slice(1);
const { createValidFileMatcher } = require(nextRoot + '/dist/server/lib/find-page-file.js');
const { getPageFromPath } = require(nextRoot + '/dist/build/route-discovery.js');
const { normalizeAppPath } = require(nextRoot + '/dist/shared/lib/router/utils/app-paths.js');
const { isMetadataRouteFile } = require(nextRoot + '/dist/lib/metadata/is-metadata-route.js');

const corpus = JSON.parse(corpusJson);
const extensions = JSON.parse(extensionsJson);
const APP_DIR = '/app';
const matcher = createValidFileMatcher(extensions, APP_DIR);
const SKIP = new Set(['/_not-found/page', '/_global-error/page']);

const ensureLeadingSlash = (p) => (p.startsWith('/') ? p : '/' + p);
const removeSuffix = (v, s) => (v.endsWith(s) ? v.slice(0, -s.length) : v);
const normalizeLayoutRoute = (k) =>
  ensureLeadingSlash(removeSuffix(normalizeAppPath(k), '/layout'));

const out = {};
for (const rel of corpus) {
  const absolute = APP_DIR + rel;
  const metadata = isMetadataRouteFile(rel, extensions, true);
  // Upstream's isAppRouterPage ORs in metadata files; the port declares metadata
  // conventions outside its baseline, so compare the non-metadata half.
  const isPage = matcher.isAppRouterPage(absolute) && !metadata;
  const entry = {
    metadata,
    page: isPage,
    route: matcher.isAppRouterRoute(absolute),
    layout: matcher.isAppLayoutPage(absolute),
    default: matcher.isAppDefaultPage(absolute),
    rootNotFound: matcher.isRootNotFound(absolute),
    pageKey: null,
    normalized: null,
  };
  // Mirror createPagesMapping's app branch, then processAppRoutes.
  const pageKey = getPageFromPath(rel.split('%5F').join('_'), extensions);
  entry.pageKey = pageKey;
  if (!SKIP.has(pageKey)) {
    if (entry.layout) {
      entry.normalized = normalizeLayoutRoute(pageKey);
    } else if (entry.route || entry.default || entry.page || entry.rootNotFound) {
      entry.normalized = normalizeAppPath(pageKey);
    }
  }
  out[rel] = entry;
}
process.stdout.write(JSON.stringify(out));
"""


def _find_next_root() -> Path | None:
    """Locate any installed `next` package by walking up from this test."""
    for parent in Path(__file__).resolve().parents:
        direct = parent / "node_modules" / "next" / "package.json"
        if direct.is_file():
            return direct.parent
        store = sorted((parent / "node_modules" / ".pnpm").glob("next@*/node_modules/next"))
        for candidate in store:
            if (candidate / "package.json").is_file():
                return candidate
    return None


def _next_version(root: Path) -> str:
    manifest = json.loads((root / "package.json").read_text())
    return str(manifest.get("version", "unknown"))


_NEXT_ROOT = _find_next_root()
_NEXT_VERSION = _next_version(_NEXT_ROOT) if _NEXT_ROOT is not None else None
_NODE = shutil.which("node")
_REQUIRED = os.environ.get("SOLEAUX_REQUIRE_CONFORMANCE") == "1"
_AVAILABLE = _NODE is not None and _NEXT_ROOT is not None

_needs_oracle = pytest.mark.skipif(
    not _AVAILABLE,
    reason="node and an installed next package are required for conformance",
)


def test_conformance_oracle_is_available_when_required() -> None:
    """A skip that can never fail is not a guarantee.

    CI that owns the conformance claim sets `SOLEAUX_REQUIRE_CONFORMANCE=1` so a
    missing oracle fails loudly instead of quietly passing forever.
    """
    if not _REQUIRED:
        pytest.skip("SOLEAUX_REQUIRE_CONFORMANCE is not set")
    assert _NODE is not None, "node is required when SOLEAUX_REQUIRE_CONFORMANCE=1"
    assert _NEXT_ROOT is not None, "an installed next package is required for conformance"


@_needs_oracle
def test_oracle_version_is_inside_the_declared_baseline() -> None:
    assert _NEXT_VERSION is not None
    assert is_supported_next_version(_NEXT_VERSION), (
        f"installed next@{_NEXT_VERSION} is outside {NEXT_CONVENTIONS_RANGE}; "
        f"the port was verified against next@{NEXT_CONVENTIONS_VERIFIED_VERSION}"
    )


def _run_oracle(
    corpus: tuple[str, ...],
    extensions: tuple[str, ...],
    workdir: Path,
) -> dict[str, dict[str, object]]:
    assert _NODE is not None
    assert _NEXT_ROOT is not None
    completed = subprocess.run(
        [
            _NODE,
            "-e",
            _ORACLE_SCRIPT,
            "--",
            str(_NEXT_ROOT),
            json.dumps(list(corpus)),
            json.dumps(list(extensions)),
        ],
        # An empty cwd proves the oracle reads no project files, matching the
        # port's own filesystem independence.
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    # stdout only: node writes unrelated warnings to stderr in some environments.
    return json.loads(completed.stdout)


def _port_entry(rel: str, extensions: tuple[str, ...]) -> dict[str, object]:
    matcher = create_valid_file_matcher(extensions)
    page_key = get_page_from_path(rel.replace("%5F", "_"), extensions)
    kind = matcher.kind_for(rel)
    layout = matcher.layout.search(rel) is not None
    normalized: str | None = None
    if page_key not in SKIP_PAGE_KEYS and kind is not None:
        normalized = normalize_layout_route(page_key) if layout else normalize_app_path(page_key)
    return {
        "page": matcher.page.search(rel) is not None,
        "route": matcher.route.search(rel) is not None,
        "layout": layout,
        "default": matcher.default.search(rel) is not None,
        "rootNotFound": matcher.root_not_found.match(rel.lstrip("/")) is not None,
        "pageKey": page_key,
        "normalized": normalized,
    }


@_needs_oracle
@pytest.mark.parametrize("extensions", [DEFAULT_PAGE_EXTENSIONS, ALT_PAGE_EXTENSIONS])
def test_port_matches_installed_next_conventions(
    extensions: tuple[str, ...],
    tmp_path: Path,
) -> None:
    oracle = _run_oracle(CONVENTION_CORPUS, extensions, tmp_path)

    mismatches: list[str] = []
    for rel in CONVENTION_CORPUS:
        expected = dict(oracle[rel])
        is_metadata = bool(expected.pop("metadata"))
        if is_metadata:
            # Metadata conventions are declared outside the baseline.
            continue
        actual = _port_entry(rel, extensions)
        for field in sorted(expected):
            if expected[field] != actual[field]:
                mismatches.append(
                    f"{rel} [{field}] next={expected[field]!r} port={actual[field]!r}"
                )

    assert not mismatches, "port diverged from installed next@{}:\n{}".format(
        _NEXT_VERSION, "\n".join(mismatches)
    )


@_needs_oracle
def test_metadata_boundary_is_exercised_and_agrees_with_upstream(tmp_path: Path) -> None:
    """The declared baseline gap must be a real, detected boundary.

    Metadata conventions are outside the port's baseline. That is only honest if
    the port can *recognize* such a file in order to report it, so its cheap stem
    check must agree with upstream's full matcher on the corpus.
    """
    oracle = _run_oracle(CONVENTION_CORPUS, DEFAULT_PAGE_EXTENSIONS, tmp_path)
    upstream_metadata = {rel for rel in CONVENTION_CORPUS if oracle[rel]["metadata"]}
    assert upstream_metadata, "corpus must exercise the metadata boundary"

    disagreements = {
        rel for rel in CONVENTION_CORPUS if is_metadata_stem(rel) != (rel in upstream_metadata)
    }
    assert not disagreements, (
        f"stem check disagrees with next@{_NEXT_VERSION} on: {sorted(disagreements)}"
    )
