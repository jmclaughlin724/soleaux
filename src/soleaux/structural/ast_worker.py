"""One-shot worker for Soleaux's fixed syntax-tree operations."""

from __future__ import annotations

import collections.abc
import json
import sys
from typing import cast

import ast_grep_py


def _parsed_json_object(source: str) -> tuple[ast_grep_py.SgNode, ast_grep_py.SgNode] | None:
    root = ast_grep_py.SgRoot(source, "json").root()
    errors = root.find_all(kind="ERROR")
    if any(error.text() != "," for error in errors):
        return None
    objects = [child for child in root.children() if child.kind() == "object"]
    if len(objects) != 1:
        return None
    return root, objects[0]


def _top_level_json_value(object_node: ast_grep_py.SgNode, key: str) -> ast_grep_py.SgNode | None:
    for child in object_node.children():
        if child.kind() != "pair":
            continue
        key_node = child.field("key")
        value_node = child.field("value")
        if key_node is None or value_node is None:
            continue
        try:
            parsed_key = json.loads(key_node.text())
        except json.JSONDecodeError:
            continue
        if parsed_key == key:
            return value_node
    return None


def _replace_json_value(source: str, key: str, replacement: str) -> str | None:
    parsed = _parsed_json_object(source)
    if parsed is None:
        return None
    root, object_node = parsed
    value_node = _top_level_json_value(object_node, key)
    if value_node is None or value_node.text().strip().casefold() == replacement.casefold():
        return None
    return root.commit_edits([value_node.replace(replacement)])


def _bash_leaf_texts(command: str) -> list[str]:
    root = ast_grep_py.SgRoot(command, "bash").root()
    if root.find(kind="ERROR") is not None:
        return []
    return list(
        dict.fromkeys(
            node.text()
            for kind in ("raw_string", "string_content", "word")
            for node in root.find_all(kind=kind)
        )
    )


def _request(raw: object) -> collections.abc.Mapping[object, object] | None:
    if not isinstance(raw, collections.abc.Mapping):
        return None
    return cast(collections.abc.Mapping[object, object], raw)


def _handle(request: collections.abc.Mapping[object, object]) -> object:
    operation = request.get("operation")
    if operation == "replace_json_value":
        source = request.get("source")
        key = request.get("key")
        replacement = request.get("replacement")
        if (
            not isinstance(source, str)
            or not isinstance(key, str)
            or not isinstance(replacement, str)
        ):
            raise ValueError("replace_json_value requires string source, key, and replacement")
        return _replace_json_value(source, key, replacement)
    if operation == "bash_leaf_texts":
        raw_commands = request.get("commands")
        if not isinstance(raw_commands, collections.abc.Sequence) or isinstance(raw_commands, str):
            raise ValueError("bash_leaf_texts requires a command list")
        commands = cast(collections.abc.Sequence[object], raw_commands)
        if not all(isinstance(command, str) for command in commands):
            raise ValueError("bash_leaf_texts commands must be strings")
        return [
            _bash_leaf_texts(command) for command in cast(collections.abc.Sequence[str], commands)
        ]
    raise ValueError("unknown syntax-tree operation")


def main() -> int:
    """Read one JSON request and emit one JSON response."""
    try:
        raw = json.load(sys.stdin)
        request = _request(raw)
        if request is None:
            return 2
        result = _handle(request)
    except OSError, ValueError, json.JSONDecodeError:
        return 2
    json.dump({"result": result}, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
