"""Repository path pattern semantics, including the segment boundary."""

from __future__ import annotations

import pytest

import soleaux.structural.path_patterns


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        (".codex/rules/*.rules", ".codex/rules/commands.rules"),
        (".agents/skills/**", ".agents/skills/optimizer/SKILL.md"),
        (".agents/skills/**", ".agents/skills/optimizer"),
        ("AGENTS.md", "tools/soleaux/AGENTS.md"),
        ("AGENTS.md", "AGENTS.md"),
        ("/AGENTS.md", "AGENTS.md"),
        ("./AGENTS.md", "AGENTS.md"),
        (".husky/", ".husky/pre-commit"),
        ("src/?.py", "src/a.py"),
        ("src/[abc].py", "src/b.py"),
        ("src/[a-c].py", "src/c.py"),
        ("src/[!x].py", "src/a.py"),
        ("**/package.json", "apps/web/package.json"),
        ("apps/**/route.ts", "apps/web/app/api/route.ts"),
        ("apps/**/route.ts", "apps/route.ts"),
    ],
)
def test_pattern_matches(pattern: str, path: str) -> None:
    assert soleaux.structural.path_patterns.RepositoryPattern.parse(pattern).matches(path)


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        # The reason this module exists: `*` never crosses a segment boundary.
        (".codex/rules/*.rules", ".codex/rules/nested/other.rules"),
        (".codex/rules/*.rules", ".codex/rules/commands.toml"),
        ("src/?.py", "src/ab.py"),
        ("src/[abc].py", "src/d.py"),
        ("src/[!x].py", "src/x.py"),
        ("apps/**/route.ts", "packages/web/route.ts"),
        ("", "AGENTS.md"),
        ("AGENTS.md", ""),
        # An anchored pattern is not satisfied by a deeper coincidental match.
        ("/AGENTS.md", "tools/soleaux/AGENTS.md"),
    ],
)
def test_pattern_rejects(pattern: str, path: str) -> None:
    assert not soleaux.structural.path_patterns.RepositoryPattern.parse(pattern).matches(path)


def test_unclosed_bracket_is_a_literal() -> None:
    pattern = soleaux.structural.path_patterns.RepositoryPattern.parse("src/[unclosed.py")
    assert pattern.matches("src/[unclosed.py")
    assert not pattern.matches("src/u.py")


def test_escape_makes_a_wildcard_literal() -> None:
    pattern = soleaux.structural.path_patterns.RepositoryPattern.parse(r"src/a\*b.py")
    assert pattern.matches("src/a*b.py")
    assert not pattern.matches("src/axb.py")


def test_consecutive_stars_inside_a_segment_collapse() -> None:
    assert soleaux.structural.path_patterns.RepositoryPattern.parse("src/a***b.py").matches(
        "src/axyzb.py"
    )


def test_anchoring_is_derived_from_an_interior_separator() -> None:
    assert soleaux.structural.path_patterns.RepositoryPattern.parse("a/b").anchored
    assert soleaux.structural.path_patterns.RepositoryPattern.parse("/a").anchored
    assert not soleaux.structural.path_patterns.RepositoryPattern.parse("a").anchored


CANDIDATES = frozenset(
    {
        ".codex/rules/commands.rules",
        ".codex/rules/delivery.rules",
        ".codex/rules/nested/other.rules",
        "AGENTS.md",
    }
)


def test_resolve_paths_returns_a_known_literal() -> None:
    assert soleaux.structural.path_patterns.resolve_paths("AGENTS.md", CANDIDATES) == ("AGENTS.md",)


def test_resolve_paths_leaves_an_unknown_literal_unresolved() -> None:
    assert soleaux.structural.path_patterns.resolve_paths("MISSING.md", CANDIDATES) == ()


def test_resolve_paths_expands_a_wildcard_deterministically() -> None:
    assert soleaux.structural.path_patterns.resolve_paths(".codex/rules/*.rules", CANDIDATES) == (
        ".codex/rules/commands.rules",
        ".codex/rules/delivery.rules",
    )


def test_resolve_paths_respects_the_segment_boundary() -> None:
    assert ".codex/rules/nested/other.rules" not in soleaux.structural.path_patterns.resolve_paths(
        ".codex/rules/*.rules", CANDIDATES
    )
    assert ".codex/rules/nested/other.rules" in soleaux.structural.path_patterns.resolve_paths(
        ".codex/rules/**", CANDIDATES
    )


def test_resolve_paths_returns_empty_for_an_unmatched_wildcard() -> None:
    assert soleaux.structural.path_patterns.resolve_paths("docs/*.md", CANDIDATES) == ()
