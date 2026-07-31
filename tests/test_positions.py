"""PositionCodec round-trips (AC09, AC24) and file-URI normalization."""

from pathlib import PurePosixPath, PureWindowsPath

from _assertions import raises_with_message

from soleaux.contracts.positions import (
    Point,
    PositionCodec,
    PositionEncoding,
    file_uri_to_path,
    path_to_file_uri,
)


def test_ascii_round_trip() -> None:
    codec = PositionCodec(b"hello world\nsecond line\n")
    point = codec.byte_to_point(7)
    assert (point.line, point.column, point.utf16_column, point.byte) == (0, 7, 7, 7)
    assert codec.point_to_byte(point.line, point.column) == 7
    assert codec.byte_to_point(11) == Point(line=0, column=11, utf16_column=11, byte=11)
    assert codec.byte_to_point(12) == Point(line=1, column=0, utf16_column=0, byte=12)


def test_crlf_multiline_round_trip() -> None:
    codec = PositionCodec(b"first\r\nsecond\r\nthird")
    assert codec.line_count == 3
    line_two_start = codec.byte_to_point(7)
    assert (line_two_start.line, line_two_start.column) == (1, 0)
    assert codec.point_to_byte(1, 6) == 13
    end_of_line_one = codec.point_to_byte(0, 5)
    assert end_of_line_one == 5
    with raises_with_message(ValueError, "outside line 0"):
        codec.point_to_byte(0, 6)


def test_astral_emoji_counts() -> None:
    content = "a😀b\n".encode()
    codec = PositionCodec(content)
    after_emoji = codec.byte_to_point(5)
    assert (after_emoji.column, after_emoji.utf16_column) == (2, 3)
    assert codec.point_to_byte(0, 2) == 5
    assert codec.point_to_byte(0, 1, encoding=PositionEncoding.UTF16) == 1
    assert codec.point_to_byte(0, 3, encoding=PositionEncoding.UTF16) == 5
    with raises_with_message(ValueError, "splits a surrogate pair"):
        codec.point_to_byte(0, 2, encoding=PositionEncoding.UTF16)


def test_combining_marks_count_as_separate_code_points() -> None:
    content = "e\u0301x\n".encode()
    codec = PositionCodec(content)
    assert len(content) == 5
    point = codec.byte_to_point(3)
    assert (point.column, point.utf16_column) == (2, 2)
    assert codec.point_to_byte(0, 2) == 3


def test_multibyte_identifier_columns() -> None:
    codec = PositionCodec("λ_amb = 1\n".encode())
    point = codec.byte_to_point(len("λ_".encode()))
    assert point.column == 2
    assert codec.point_to_byte(0, 2) == len("λ_".encode())


def test_byte_range_conversion() -> None:
    codec = PositionCodec(b"ab\ncd\n")
    span = codec.byte_range_to_points(1, 4)
    assert (span.start.line, span.start.column) == (0, 1)
    assert (span.end.line, span.end.column) == (1, 1)
    with raises_with_message(ValueError, "precedes start"):
        codec.byte_range_to_points(4, 1)


def test_points_to_byte_range_rejects_reversed_ranges() -> None:
    codec = PositionCodec(b"ab\ncd\n")
    start = Point(line=1, column=0, utf16_column=0, byte=3)
    end = Point(line=0, column=1, utf16_column=1, byte=1)
    with raises_with_message(ValueError, "ends before it starts"):
        codec.points_to_byte_range(start, end)


def test_out_of_range_positions_fail() -> None:
    codec = PositionCodec(b"ab\n")
    with raises_with_message(ValueError, "byte offset -1"):
        codec.byte_to_point(-1)
    with raises_with_message(ValueError, "byte offset 5"):
        codec.byte_to_point(5)
    with raises_with_message(ValueError, "line 3"):
        codec.point_to_byte(3, 0)
    with raises_with_message(ValueError, "column 3"):
        codec.point_to_byte(0, 3)
    with raises_with_message(ValueError, "utf-16 column 3"):
        codec.point_to_byte(0, 3, encoding=PositionEncoding.UTF16)


def test_file_uri_windows_drive_round_trip() -> None:
    assert file_uri_to_path("file:///C%3A/work/repo") == "C:/work/repo"
    assert path_to_file_uri(PureWindowsPath(r"C:\work\repo")) == "file:///C:/work/repo"


def test_file_uri_posix_round_trip() -> None:
    assert file_uri_to_path("file:///work/repo") == "/work/repo"
    assert path_to_file_uri(PurePosixPath("/work/repo")) == "file:///work/repo"
    assert file_uri_to_path("file:///work/my%20repo") == "/work/my repo"


def test_file_uri_rejects_non_file_schemes() -> None:
    with raises_with_message(ValueError, "not a file URI"):
        file_uri_to_path("https://example.com/x")
