"""Test assertions that compare exception messages literally."""

from __future__ import annotations

import collections.abc
import contextlib

import pytest
from pydantic import JsonValue, TypeAdapter

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_JSON_ARRAY_ADAPTER = TypeAdapter(list[JsonValue])
_OBJECT_MAPPING_ADAPTER = TypeAdapter(dict[str, object])
_OBJECT_LIST_ADAPTER = TypeAdapter(list[object])
_STRING_LIST_ADAPTER = TypeAdapter(list[str])


@contextlib.contextmanager
def raises_with_message[ExceptionT: BaseException](
    expected_exception: type[ExceptionT],
    expected_message: str,
) -> collections.abc.Generator[pytest.ExceptionInfo[ExceptionT]]:
    """Assert one exception type and a literal message fragment."""
    with pytest.raises(expected_exception) as exception:
        yield exception
    assert expected_message in str(exception.value)


def json_object(value: object) -> dict[str, JsonValue]:
    """Validate a dynamic test payload as one JSON object."""
    return _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)


def json_array(value: object) -> list[JsonValue]:
    """Validate a dynamic test payload as one JSON array."""
    return _JSON_ARRAY_ADAPTER.validate_python(value, strict=True)


def object_mapping(value: object) -> dict[str, object]:
    """Narrow an untyped library or parser result to a string-keyed mapping."""
    return _OBJECT_MAPPING_ADAPTER.validate_python(value, strict=True)


def object_list(value: object) -> list[object]:
    """Narrow an untyped library or parser result to a concrete list."""
    return _OBJECT_LIST_ADAPTER.validate_python(value, strict=True)


def string_list(value: object) -> list[str]:
    """Narrow an untyped library or parser result to a concrete string list."""
    return _STRING_LIST_ADAPTER.validate_python(value, strict=True)
