"""Guidance block writer: idempotence and marker-pair integrity."""

from __future__ import annotations

import _assertions

from soleaux.provisioning.guidance_writer import (
    GUIDANCE_BEGIN,
    GUIDANCE_BLOCK,
    GUIDANCE_END,
    GuidanceMarkerError,
    render_guidance,
)


def test_absent_file_renders_block_only() -> None:
    rendered = render_guidance(None)
    assert rendered == f"{GUIDANCE_BLOCK}\n".encode()


def test_existing_content_appends_block() -> None:
    rendered = render_guidance(b"# Notes\n\nHuman guidance.\n")
    assert rendered is not None
    text = rendered.decode()
    assert text.startswith("# Notes\n\nHuman guidance.\n")
    assert GUIDANCE_BLOCK in text


def test_current_block_is_idempotent() -> None:
    current = f"# Notes\n\n{GUIDANCE_BLOCK}\n".encode()
    assert render_guidance(current) is None


def test_stale_block_is_replaced_in_place() -> None:
    stale = f"before\n{GUIDANCE_BEGIN}\nold body\n{GUIDANCE_END}\nafter\n"
    rendered = render_guidance(stale.encode())
    assert rendered is not None
    text = rendered.decode()
    assert "old body" not in text
    assert text.startswith("before\n")
    assert text.endswith("after\n")


def test_begin_without_end_is_an_error_and_preserves_content() -> None:
    current = f"# Notes\n\n{GUIDANCE_BEGIN}\nHuman guidance that must survive.\n"
    with _assertions.raises_with_message(GuidanceMarkerError, "incomplete"):
        render_guidance(current.encode())


def test_end_without_begin_is_an_error() -> None:
    current = f"# Notes\n\nHuman guidance.\n{GUIDANCE_END}\n"
    with _assertions.raises_with_message(GuidanceMarkerError, "incomplete"):
        render_guidance(current.encode())


def test_duplicate_begin_markers_are_an_error() -> None:
    current = f"{GUIDANCE_BEGIN}\nfirst\n{GUIDANCE_END}\n{GUIDANCE_BEGIN}\nsecond\n{GUIDANCE_END}\n"
    with _assertions.raises_with_message(GuidanceMarkerError, "duplicate"):
        render_guidance(current.encode())


def test_reversed_markers_are_an_error() -> None:
    current = f"{GUIDANCE_END}\nmiddle\n{GUIDANCE_BEGIN}\n"
    with _assertions.raises_with_message(GuidanceMarkerError, "reversed"):
        render_guidance(current.encode())
