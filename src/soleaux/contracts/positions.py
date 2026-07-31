"""Producer-neutral position codec over canonical UTF-8 bytes (AC09, AC24).

`PositionCodec` converts ast-grep byte ranges and negotiated LSP
UTF-8/UTF-16/UTF-32 positions through the exact captured bytes. Lines are
zero-based; columns are Unicode code points unless a negotiated encoding says
otherwise. Module-level URI helpers normalize `file://` URIs, including
percent-encoded Windows drive paths.
"""

from __future__ import annotations

import bisect
import enum
import pathlib
import urllib.parse

import pydantic

import soleaux.contracts.repository


class PositionEncoding(enum.StrEnum):
    """Negotiated position encodings."""

    UTF8 = "utf-8"
    UTF16 = "utf-16"
    UTF32 = "utf-32"


class Point(pydantic.BaseModel):
    """One zero-based position with every negotiated column form precomputed."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    line: int = pydantic.Field(ge=0)
    column: int = pydantic.Field(ge=0)
    utf16_column: int = pydantic.Field(ge=0)
    byte: int = pydantic.Field(ge=0)


class PointRange(pydantic.BaseModel):
    """A half-open range between two points."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    start: Point
    end: Point


class PositionCodec:
    """Bidirectional position conversion for exactly one captured document."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._text = content.decode("utf-8")
        self._line_start_bytes: list[int] = [0]
        for index, byte in enumerate(content):
            if byte == 0x0A:
                self._line_start_bytes.append(index + 1)

    @property
    def line_count(self) -> int:
        """Number of lines addressable by the codec."""
        return len(self._line_start_bytes)

    def _line_text(self, line: int) -> tuple[int, str]:
        if line < 0 or line >= len(self._line_start_bytes):
            msg = f"line {line} outside 0..{len(self._line_start_bytes) - 1}"
            raise ValueError(msg)
        start = self._line_start_bytes[line]
        end = (
            self._line_start_bytes[line + 1]
            if line + 1 < len(self._line_start_bytes)
            else len(self._content)
        )
        return start, self._content[start:end].decode("utf-8")

    @staticmethod
    def _content_end(segment: str) -> int:
        """Length of the line text excluding its terminator."""
        if segment.endswith("\r\n"):
            return len(segment) - 2
        if segment.endswith(("\n", "\r")):
            return len(segment) - 1
        return len(segment)

    def byte_to_point(self, offset: int) -> Point:
        """Convert a UTF-8 byte offset into every negotiated column form."""
        if offset < 0 or offset > len(self._content):
            msg = f"byte offset {offset} outside 0..{len(self._content)}"
            raise ValueError(msg)
        line = bisect.bisect_right(self._line_start_bytes, offset) - 1
        line_start = self._line_start_bytes[line]
        segment = self._content[line_start:offset].decode("utf-8")
        return Point(
            line=line,
            column=len(segment),
            utf16_column=len(segment.encode("utf-16-le")) // 2,
            byte=offset,
        )

    def point_to_byte(
        self,
        line: int,
        column: int,
        *,
        encoding: PositionEncoding = PositionEncoding.UTF8,
    ) -> int:
        """Convert a line/column position in the negotiated encoding to a byte offset."""
        start, segment = self._line_text(line)
        text_end = self._content_end(segment)
        body = segment[:text_end]
        if encoding is PositionEncoding.UTF16:
            units = 0
            char_index = -1
            for position, char in enumerate(body):
                if units == column:
                    char_index = position
                    break
                units += 2 if ord(char) > 0xFFFF else 1
            else:
                if units == column:
                    char_index = len(body)
            if char_index < 0:
                msg = f"utf-16 column {column} splits a surrogate pair on line {line}"
                raise ValueError(msg)
        else:
            if column < 0 or column > len(body):
                msg = f"column {column} outside line {line} content ({len(body)} code points)"
                raise ValueError(msg)
            char_index = column
        return start + len(body[:char_index].encode("utf-8"))

    def byte_range_to_points(self, start: int, end: int) -> PointRange:
        """Convert a half-open byte range into a point range."""
        if end < start:
            msg = f"byte range end {end} precedes start {start}"
            raise ValueError(msg)
        return PointRange(start=self.byte_to_point(start), end=self.byte_to_point(end))

    def points_to_byte_range(
        self,
        start: Point,
        end: Point,
        *,
        encoding: PositionEncoding = PositionEncoding.UTF8,
    ) -> tuple[int, int]:
        """Convert two points in the negotiated encoding into a half-open byte range."""
        begin = self.point_to_byte(
            start.line,
            start.column if encoding is not PositionEncoding.UTF16 else start.utf16_column,
            encoding=encoding,
        )
        finish = self.point_to_byte(
            end.line,
            end.column if encoding is not PositionEncoding.UTF16 else end.utf16_column,
            encoding=encoding,
        )
        if finish < begin:
            msg = "point range ends before it starts"
            raise ValueError(msg)
        return begin, finish


def path_to_file_uri(path: pathlib.PurePath) -> str:
    """Normalize a filesystem path into a `file://` URI."""
    if isinstance(path, pathlib.PureWindowsPath) or "\\" in str(path):
        normalized = str(path).replace(chr(92), "/")
        return f"file:///{urllib.parse.quote(normalized, safe='/:')}"
    absolute = pathlib.PurePosixPath(str(path))
    text = str(absolute if absolute.is_absolute() else pathlib.PurePosixPath("/") / absolute)
    return f"file://{urllib.parse.quote(text, safe='/')}"


def file_uri_to_path(uri: str) -> str:
    """Decode a `file://` URI into a platform path string.

    Percent-encoded Windows drives (`file:///C%3A/work`) decode to drive paths;
    POSIX URIs decode to absolute paths. Non-file URIs are rejected.
    """
    decoded = soleaux.contracts.repository.file_uri_to_local_path(uri)
    if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":" and decoded[1].isalpha():
        return decoded[1] + decoded[2:]  # Windows drive path: /C:/work -> C:/work
    return decoded
