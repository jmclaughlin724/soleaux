"""Packaged capability-rule registry and evaluator.

Rules load from `src/soleaux/resources/rules/` through `importlib.resources`;
they never piggyback on the repository `sgconfig.yml`. Callers select
registered rule IDs only — never raw YAML, rewrites, CLI flags, or arbitrary
languages. ast_grep_py is referenced only through duck-typed nodes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

import yaml

from soleaux.structural.fragments import SyntaxFragment
from soleaux.structural.projections import UnsupportedLanguageError

RULES_DIRECTORY = "rules"

_LANGUAGE_RULE_COMPAT: dict[str, frozenset[str]] = {
    "tsx": frozenset({"typescript"}),
}


def _rule_allows_language(rule_language: str, language: str) -> bool:
    if rule_language.lower() == language.lower():
        return True
    return rule_language.lower() in _LANGUAGE_RULE_COMPAT.get(language.lower(), frozenset())


@dataclass(frozen=True)
class PackagedRule:
    """One registered, capability-owned rule."""

    id: str
    language: str
    severity: str
    message: str
    note: str
    rule: dict[str, Any]
    digest: str


def _load_all() -> dict[str, PackagedRule]:
    rules: dict[str, PackagedRule] = {}
    directory = files("soleaux.resources").joinpath(RULES_DIRECTORY)
    if not directory.is_dir():
        return rules
    for resource in sorted(directory.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith((".yml", ".yaml")):
            continue
        raw = resource.read_text(encoding="utf-8")
        parsed: Any = yaml.safe_load(raw)
        if not isinstance(parsed, dict) or "id" not in parsed or "rule" not in parsed:
            msg = f"packaged rule {resource.name} is missing id or rule"
            raise ValueError(msg)
        data = cast("dict[str, Any]", parsed)
        rule_id = str(data["id"])
        rules[rule_id] = PackagedRule(
            id=rule_id,
            language=str(data["language"]),
            severity=str(data.get("severity", "info")),
            message=str(data.get("message", rule_id)),
            note=str(data.get("note", "")),
            rule=dict(data["rule"]),
            digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
    return rules


_packaged_rules_cache: dict[str, PackagedRule] | None = None


def packaged_rules() -> dict[str, PackagedRule]:
    """Every registered packaged rule, loaded lazily once per process."""
    global _packaged_rules_cache
    if _packaged_rules_cache is None:
        _packaged_rules_cache = _load_all()
    return _packaged_rules_cache


def load_packaged_rule(rule_id: str) -> PackagedRule:
    """Load one registered rule or raise KeyError with the id."""
    try:
        return packaged_rules()[rule_id]
    except KeyError:
        raise KeyError(rule_id) from None


def packaged_rule_digest(rule_id: str) -> str:
    """The content digest that binds one rule into cache keys."""
    return load_packaged_rule(rule_id).digest


def rule_supports_language(rule: PackagedRule, language: str) -> bool:
    """Return whether a packaged rule can analyze the captured language."""
    return _rule_allows_language(rule.language, language)


def evaluate_packaged_rule(
    root: Any,
    rule: PackagedRule,
    *,
    path: str,
    language: str,
) -> list[SyntaxFragment]:
    """Evaluate one registered rule inside the worker; language must match."""
    if not rule_supports_language(rule, language):
        msg = f"rule {rule.id!r} targets {rule.language}, not {language}"
        raise UnsupportedLanguageError(msg)
    rows: list[SyntaxFragment] = []
    for node in root.find_all(**rule.rule):
        rng = node.range()
        rows.append(
            SyntaxFragment(
                projection=f"rules.{rule.id}",
                kind=rule.severity,
                name=None,
                path=path,
                language=language,
                byte_start=rng.start.index,
                byte_end=rng.end.index,
                start_line=rng.start.line,
                start_column=rng.start.column,
                end_line=rng.end.line,
                end_column=rng.end.column,
                text_preview=node.text()[:120],
                attributes={"message": rule.message, "rule_digest": rule.digest},
            )
        )
    return rows
