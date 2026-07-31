"""D031: user code-point positions convert to negotiated LSP units."""

import _assertions
import pytest

import soleaux.lsp.operations


@pytest.mark.parametrize(
    ("encoding", "expected_character"),
    [
        ("utf-8", 5),
        ("utf-16", 3),
        ("utf-32", 2),
    ],
)
def test_multibyte_position_uses_negotiated_units(
    encoding: str,
    expected_character: int,
) -> None:
    position = soleaux.lsp.operations.lsp_position_from_user(
        "a😀b\r\n".encode(),
        line=1,
        column=3,
        position_encoding=encoding,
    )

    assert position.line == 0
    assert position.character == expected_character


def test_position_rejects_a_column_inside_crlf_terminator() -> None:
    with _assertions.raises_with_message(ValueError, "outside line 0"):
        soleaux.lsp.operations.lsp_position_from_user(
            b"value\r\n",
            line=1,
            column=7,
            position_encoding="utf-16",
        )
