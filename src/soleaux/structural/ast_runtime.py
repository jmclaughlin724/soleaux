"""Typed client for fixed, isolated syntax-tree operations."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import cast

import soleaux.postgresql.runtime

AST_OPERATION_TIMEOUT_SECONDS = 5.0


def _run_operation(request: Mapping[str, object]) -> object | None:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "soleaux.structural.ast_worker"],
            input=json.dumps(request, separators=(",", ":")),
            capture_output=True,
            check=False,
            env=soleaux.postgresql.runtime.build_safe_environment(
                {},
                environment_names=(),
            ),
            text=True,
            timeout=AST_OPERATION_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        return None
    try:
        raw_response: object = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw_response, Mapping):
        return None
    response = cast(Mapping[object, object], raw_response)
    return response.get("result")


def replace_json_value(source: str, key: str, replacement: str) -> str | None:
    """Replace one top-level JSON value through an isolated syntax tree."""
    result = _run_operation(
        {
            "operation": "replace_json_value",
            "source": source,
            "key": key,
            "replacement": replacement,
        }
    )
    return result if isinstance(result, str) else None


def bash_leaf_texts(commands: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Return parsed shell leaf text for each command without loading the host parser."""
    if not commands:
        return ()
    empty = tuple(() for _command in commands)
    result = _run_operation(
        {
            "operation": "bash_leaf_texts",
            "commands": list(commands),
        }
    )
    if not isinstance(result, list):
        return empty
    rows = cast(list[object], result)
    if len(rows) != len(commands):
        return empty
    parsed: list[tuple[str, ...]] = []
    for raw_row in rows:
        if not isinstance(raw_row, list):
            return empty
        values = cast(list[object], raw_row)
        if not all(isinstance(value, str) for value in values):
            return empty
        parsed.append(tuple(cast(list[str], values)))
    return tuple(parsed)
