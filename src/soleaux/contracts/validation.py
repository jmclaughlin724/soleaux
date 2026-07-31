"""Deterministic scalar validators shared by Soleaux contracts."""

from __future__ import annotations

_LOWERCASE_HEXADECIMAL = frozenset("0123456789abcdef")


def is_lowercase_sha256(value: str) -> bool:
    """Return whether ``value`` is one lowercase hexadecimal SHA-256 digest."""
    return len(value) == 64 and all(character in _LOWERCASE_HEXADECIMAL for character in value)


def starts_with_absolute_path(value: str) -> bool:
    """Return whether ``value`` starts with a POSIX, drive, or UNC absolute path."""
    if value.startswith("/") or value.startswith("\\\\"):
        return True
    return (
        len(value) >= 3
        and ("a" <= value[0] <= "z" or "A" <= value[0] <= "Z")
        and value[1] == ":"
        and value[2] in {"/", "\\"}
    )
