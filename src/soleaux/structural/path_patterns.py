"""Repository path patterns matched by deterministic state traversal.

Soleaux resolves ownership targets and governance declarations against
repository-relative POSIX paths. `fnmatch` cannot express the segment
boundary: its `*` crosses `/`, so `config/rules/*.rules` would also claim
`config/rules/nested/other.rules`. This module parses a pattern into segments
once and matches by advancing a set of active states, so a pattern never
backtracks and never compiles to a regular expression.

Segment tokens are literals, `?`, `*`, and bracket sets. A `**` segment matches
any number of segments, including none. A pattern containing an interior `/` is
anchored to the repository root; a pattern without one matches a path's final
segment at any depth, which is how ownership tables name bare filenames.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from typing import final

GLOBSTAR = "**"
_ESCAPE = "\\"
_NEGATIONS = frozenset({"!", "^"})
_WILDCARD_CHARACTERS = frozenset({"*", "?", "["})


@final
class _TokenKind(enum.StrEnum):
    """The four things a segment token can match."""

    LITERAL = "literal"
    SINGLE = "single"
    STAR = "star"
    CLASS = "class"


@dataclass(frozen=True, slots=True)
class _CharacterClass:
    """A bracket set, already split into literals and inclusive ranges."""

    negated: bool
    literals: frozenset[str]
    ranges: tuple[tuple[str, str], ...]

    def matches(self, character: str) -> bool:
        contained = character in self.literals or any(
            low <= character <= high for low, high in self.ranges
        )
        return contained is not self.negated


@dataclass(frozen=True, slots=True)
class _Token:
    """One matching step inside a single path segment."""

    kind: _TokenKind
    literal: str = ""
    character_class: _CharacterClass | None = None

    def matches(self, character: str) -> bool:
        if self.kind is _TokenKind.LITERAL:
            return character == self.literal
        if self.kind is _TokenKind.SINGLE:
            return True
        if self.kind is _TokenKind.CLASS:
            return self.character_class is not None and self.character_class.matches(character)
        return True


@dataclass(frozen=True, slots=True)
class _Segment:
    """One path segment, or the globstar that spans any number of them."""

    globstar: bool
    tokens: tuple[_Token, ...]


def _parse_character_class(pattern: str, start: int) -> tuple[_Token, int] | None:
    """Parse `[...]` beginning at `start`, or report that it never closes."""
    index = start + 1
    negated = index < len(pattern) and pattern[index] in _NEGATIONS
    if negated:
        index += 1
    literals: set[str] = set()
    ranges: list[tuple[str, str]] = []
    first = True
    while index < len(pattern):
        character = pattern[index]
        if character == "]" and not first:
            token = _Token(
                kind=_TokenKind.CLASS,
                character_class=_CharacterClass(
                    negated=negated,
                    literals=frozenset(literals),
                    ranges=tuple(ranges),
                ),
            )
            return token, index + 1
        first = False
        if index + 2 < len(pattern) and pattern[index + 1] == "-" and pattern[index + 2] != "]":
            ranges.append((character, pattern[index + 2]))
            index += 3
            continue
        literals.add(character)
        index += 1
    return None


def _parse_segment_tokens(segment: str) -> tuple[_Token, ...]:
    """Tokenize one segment, treating an unclosed `[` as a literal bracket."""
    tokens: list[_Token] = []
    index = 0
    while index < len(segment):
        character = segment[index]
        if character == _ESCAPE and index + 1 < len(segment):
            tokens.append(_Token(kind=_TokenKind.LITERAL, literal=segment[index + 1]))
            index += 2
            continue
        if character == "*":
            if not tokens or tokens[-1].kind is not _TokenKind.STAR:
                tokens.append(_Token(kind=_TokenKind.STAR))
            index += 1
            continue
        if character == "?":
            tokens.append(_Token(kind=_TokenKind.SINGLE))
            index += 1
            continue
        if character == "[":
            parsed = _parse_character_class(segment, index)
            if parsed is not None:
                token, index = parsed
                tokens.append(token)
                continue
        tokens.append(_Token(kind=_TokenKind.LITERAL, literal=character))
        index += 1
    return tuple(tokens)


def _token_closure(tokens: Sequence[_Token], states: frozenset[int]) -> frozenset[int]:
    """Add every state reachable by letting a `*` match nothing."""
    reached = set(states)
    pending = list(states)
    while pending:
        state = pending.pop()
        if state < len(tokens) and tokens[state].kind is _TokenKind.STAR:
            successor = state + 1
            if successor not in reached:
                reached.add(successor)
                pending.append(successor)
    return frozenset(reached)


def _match_segment(tokens: Sequence[_Token], text: str) -> bool:
    """Simulate the segment's token automaton over `text`."""
    states = _token_closure(tokens, frozenset({0}))
    for character in text:
        advanced: set[int] = set()
        for state in states:
            if state >= len(tokens):
                continue
            token = tokens[state]
            if not token.matches(character):
                continue
            advanced.add(state if token.kind is _TokenKind.STAR else state + 1)
        if not advanced:
            return False
        states = _token_closure(tokens, frozenset(advanced))
    return len(tokens) in states


def _segment_closure(segments: Sequence[_Segment], states: frozenset[int]) -> frozenset[int]:
    """Add every state reachable by letting a globstar span nothing."""
    reached = set(states)
    pending = list(states)
    while pending:
        state = pending.pop()
        if state < len(segments) and segments[state].globstar:
            successor = state + 1
            if successor not in reached:
                reached.add(successor)
                pending.append(successor)
    return frozenset(reached)


def _path_segments(path: str) -> Iterator[str]:
    for segment in path.split("/"):
        if segment:
            yield segment


@dataclass(frozen=True, slots=True)
class RepositoryPattern:
    """A parsed repository path pattern.

    `anchored` patterns match from the repository root. Unanchored patterns
    name a bare filename and match a path's final segment at any depth.
    """

    anchored: bool
    segments: tuple[_Segment, ...]

    @classmethod
    def parse(cls, pattern: str) -> RepositoryPattern:
        """Parse `pattern`, normalizing a directory suffix to a globstar."""
        normalized = pattern.strip()
        if normalized.endswith("/"):
            normalized = f"{normalized}{GLOBSTAR}"
        body = normalized.removeprefix("./").lstrip("/")
        anchored = normalized.startswith("/") or "/" in body
        segments = tuple(
            _Segment(globstar=True, tokens=())
            if segment == GLOBSTAR
            else _Segment(globstar=False, tokens=_parse_segment_tokens(segment))
            for segment in _path_segments(body)
        )
        return cls(anchored=anchored, segments=segments)

    def matches(self, path: str) -> bool:
        """Whether `path`, a repository-relative POSIX path, matches."""
        if not self.segments:
            return False
        candidate = tuple(_path_segments(path.removeprefix("./")))
        if not candidate:
            return False
        if not self.anchored:
            return _match_segment(self.segments[-1].tokens, candidate[-1])
        states = _segment_closure(self.segments, frozenset({0}))
        for segment_text in candidate:
            advanced: set[int] = set()
            for state in states:
                if state >= len(self.segments):
                    continue
                segment = self.segments[state]
                if segment.globstar:
                    advanced.add(state)
                elif _match_segment(segment.tokens, segment_text):
                    advanced.add(state + 1)
            if not advanced:
                return False
            states = _segment_closure(self.segments, frozenset(advanced))
        return len(self.segments) in states


def _contains_wildcard(pattern: str) -> bool:
    """Whether `pattern` uses wildcard syntax, checked before parsing it."""
    return any(character in pattern for character in _WILDCARD_CHARACTERS)


def resolve_paths(pattern: str, candidates: Collection[str]) -> tuple[str, ...]:
    """Resolve `pattern` against known repository paths.

    A literal pattern resolves only to itself and only when `candidates`
    already contains it, so an unresolved target stays unresolved rather than
    being reported as a match. A wildcard pattern resolves to every candidate
    it matches, in sorted order, and is parsed once for the whole scan.
    """
    if pattern in candidates:
        return (pattern,)
    if not _contains_wildcard(pattern):
        return ()
    compiled = RepositoryPattern.parse(pattern)
    return tuple(sorted(candidate for candidate in candidates if compiled.matches(candidate)))
