"""Candidate-first narrowing: literal/text filters before structural machinery.

Cheap byte containment narrows the candidate set before any parser starts, so
structural work runs only where a literal match exists.
"""

from __future__ import annotations


def narrow_candidates(
    contents: dict[str, bytes],
    needle: bytes,
    *,
    max_candidates: int = 256,
) -> tuple[str, ...]:
    """Paths whose captured bytes contain the literal needle, capped and sorted."""
    if not needle:
        return ()
    matches = [path for path, content in contents.items() if needle in content]
    return tuple(sorted(matches)[:max_candidates])
