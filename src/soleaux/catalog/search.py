"""One disposable FTS5 projection over every searchable typed catalog fact.

`search_documents` derives one `SearchDocument` per fact; the store indexes
them in `facts_fts` and ranks with weighted bm25; `fts_match_expression`
builds a safe MATCH expression with an explicit character-class state machine
(quoted alphanumeric tokens neutralize every FTS operator); `linear_search`
is the deterministic fallback engine for punctuation-only queries and builds
without FTS5. The typed catalog stays authoritative — every hit is only a
`fact_key` pointer back to its canonical record.
"""

from __future__ import annotations

import dataclasses
import enum
import pathlib
import typing

import pydantic

import soleaux.contracts.requests

if typing.TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from soleaux.catalog.contracts import ChunkFact
    from soleaux.catalog.generation import CatalogGeneration
    from soleaux.contracts.frame import FactRow

SEARCH_KINDS: tuple[str, ...] = tuple(kind.value for kind in soleaux.contracts.requests.SearchKind)

#: bm25 scores are negative-better; dividing by a boost > 1 promotes the kind.
KIND_RANK_BOOSTS: dict[str, float] = {
    "chunk": 1.0,
    "file": 1.6,
    "project": 1.3,
    "dependency": 1.3,
    "script": 1.3,
    "config": 1.3,
    "task": 1.5,
    "route": 1.5,
    "rule": 1.5,
    "symbol": 1.5,
    "import": 1.3,
    "diagnostic": 1.3,
    "change": 1.2,
    "policy": 1.5,
}

_TOKEN_EXTRA_CHARACTERS = frozenset("_$@")
_FACT_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "chunk": ("chunk_id",),
    "project": ("project_id",),
    "dependency": ("project_id", "package_name"),
    "script": ("project_id", "name"),
    "task": ("project_id", "task_id"),
    "config": ("project_id", "config_path"),
    "rule": ("rule_id",),
    "policy": ("policy_id",),
    "symbol": ("project_id", "symbol_id"),
    "import": ("project_id", "import_id"),
    "diagnostic": ("project_id", "diagnostic_id"),
    "change": ("change_id",),
}


class SearchMatchMode(enum.StrEnum):
    """How multiple safe query tokens participate in one ranked search."""

    ALL = "all"
    ANY = "any"


class SearchDocument(pydantic.BaseModel):
    """One indexed row pointing back to exactly one canonical typed record."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    fact_key: str = pydantic.Field(min_length=1)
    kind: str = pydantic.Field(min_length=1)
    path: str = ""
    title: str = ""
    body: str = ""


@dataclasses.dataclass(frozen=True)
class RankedHit:
    """One engine hit; hydration resolves the fact_key against the generation."""

    fact_key: str
    kind: str
    path: str
    score: float


def canonical_fact_key_for_row(row: FactRow, *, kind: str) -> str:
    """Return the stable search identity for one lifecycle-materialized row."""
    if kind == "file":
        return f"path:{row.evidence.path}"
    if kind == "route":
        route_id = row.data.get("route_id")
        project_id = row.data.get("project_id")
        if isinstance(route_id, str) and route_id:
            project = project_id if isinstance(project_id, str) and project_id else "-"
            return f"route:{project}:{route_id}"
        return row.evidence.evidence_id
    fields = _FACT_KEY_FIELDS.get(kind)
    if fields is None:
        return row.evidence.evidence_id
    values = _fact_key_values(row.data, fields)
    if values is None:
        return row.evidence.evidence_id
    return ":".join((kind, *values))


def _fact_key_values(
    data: Mapping[str, object],
    fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    values: list[str] = []
    for field in fields:
        value = data.get(field)
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    return tuple(values)


def _is_token_character(character: str) -> bool:
    return character.isalnum() or character in _TOKEN_EXTRA_CHARACTERS


def _query_tokens(query: str) -> tuple[str, ...]:
    tokens: list[str] = []
    current: list[str] = []
    for character in query:
        if _is_token_character(character):
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def fts_match_expression(
    query: str,
    *,
    match_mode: SearchMatchMode = SearchMatchMode.ALL,
) -> str:
    """Quoted-token MATCH expression; '' when the query has no indexable token."""
    tokens = _query_tokens(query)
    if not tokens:
        return ""
    quoted = [f'"{token}"' for token in tokens]
    if _is_token_character(query[-1]):
        quoted[-1] = f"{quoted[-1]} *"
    separator = " OR " if match_mode is SearchMatchMode.ANY else " "
    return separator.join(quoted)


def chunk_documents(chunks: Iterable[ChunkFact]) -> tuple[SearchDocument, ...]:
    return tuple(
        SearchDocument(
            fact_key=f"chunk:{chunk.chunk_id}",
            kind="chunk",
            path=chunk.path,
            title="",
            body=chunk.text,
        )
        for chunk in chunks
    )


def search_documents(generation: CatalogGeneration) -> tuple[SearchDocument, ...]:
    """Every searchable typed fact as one indexed document."""
    facts = generation.facts
    documents: list[SearchDocument] = []

    for item in generation.snapshot.files:
        documents.append(
            SearchDocument(
                fact_key=f"path:{item.path}",
                kind="file",
                path=item.path,
                title=pathlib.PurePosixPath(item.path).name,
                body="",
            )
        )
    for project in facts.projects:
        documents.append(
            SearchDocument(
                fact_key=f"project:{project.project_id}",
                kind="project",
                path=project.manifest_path,
                title=project.name or project.project_id,
                body=f"{project.kind.value} {project.root_path}",
            )
        )
    for dependency in facts.dependencies:
        documents.append(
            SearchDocument(
                fact_key=f"dependency:{dependency.project_id}:{dependency.package_name}",
                kind="dependency",
                path=dependency.source_path,
                title=dependency.package_name,
                body=(
                    f"{dependency.declared_specifier} {dependency.resolved_specifier or ''} "
                    f"{dependency.scope.value}"
                ),
            )
        )
    for script in facts.scripts:
        documents.append(
            SearchDocument(
                fact_key=f"script:{script.project_id}:{script.name}",
                kind="script",
                path=script.source_path,
                title=script.name,
                body=f"{script.command} {' '.join(script.task_ids)}",
            )
        )
    for task in facts.tasks:
        documents.append(
            SearchDocument(
                fact_key=f"task:{task.project_id}:{task.task_id}",
                kind="task",
                path=task.source_path,
                title=task.task_id,
                body=f"{task.runner} {' '.join(task.depends_on)} {' '.join(task.outputs)}",
            )
        )
    for config in facts.configs:
        documents.append(
            SearchDocument(
                fact_key=f"config:{config.project_id}:{config.config_path}",
                kind="config",
                path=config.config_path,
                title=pathlib.PurePosixPath(config.config_path).name,
                body=f"{config.config_kind} {config.parser_id}",
            )
        )
    for route in facts.routes:
        documents.append(
            SearchDocument(
                fact_key=f"route:{route.project_id or '-'}:{route.route_id}",
                kind="route",
                path=route.source_path,
                title=f"{route.route or ''} {' '.join(route.methods)}".strip(),
                body=(
                    f"{route.framework} {route.registration_kind} "
                    f"{route.router or ''} {route.runtime or ''}"
                ),
            )
        )
    for rule in facts.rules:
        documents.append(
            SearchDocument(
                fact_key=f"rule:{rule.rule_id}",
                kind="rule",
                path=rule.source_path,
                title=rule.rule_id,
                body=f"{rule.language} {rule.severity} {rule.message}",
            )
        )
    for policy in facts.policies:
        documents.append(
            SearchDocument(
                fact_key=f"policy:{policy.policy_id}",
                kind="policy",
                path=policy.source_path,
                title=policy.title,
                body=" ".join(value for value in policy.attributes.values() if value),
            )
        )
    for symbol in facts.symbols:
        documents.append(
            SearchDocument(
                fact_key=f"symbol:{symbol.project_id}:{symbol.symbol_id}",
                kind="symbol",
                path=symbol.path,
                title=symbol.name,
                body=f"{symbol.symbol_kind} {symbol.type_text or ''}",
            )
        )
    for imported in facts.imports:
        documents.append(
            SearchDocument(
                fact_key=f"import:{imported.project_id}:{imported.import_id}",
                kind="import",
                path=imported.path,
                title=imported.specifier,
                body=imported.resolved_path or "",
            )
        )
    for diagnostic in facts.diagnostics:
        documents.append(
            SearchDocument(
                fact_key=f"diagnostic:{diagnostic.project_id}:{diagnostic.diagnostic_id}",
                kind="diagnostic",
                path=diagnostic.path,
                title=diagnostic.code or diagnostic.category,
                body=diagnostic.message,
            )
        )
    for change in facts.changes:
        documents.append(
            SearchDocument(
                fact_key=f"change:{change.change_id}",
                kind="change",
                path=change.path,
                title=change.operation,
                body="",
            )
        )
    documents.extend(chunk_documents(facts.chunks))
    return tuple(documents)


def _linear_score(
    document: SearchDocument,
    needles: tuple[str, ...],
    *,
    match_mode: SearchMatchMode,
) -> float:
    haystacks = (document.title, document.path, document.body)
    occurrence_counts = tuple(
        sum(haystack.casefold().count(needle) for haystack in haystacks) for needle in needles
    )
    if match_mode is SearchMatchMode.ALL and any(count == 0 for count in occurrence_counts):
        return 0.0
    occurrences = sum(occurrence_counts)
    if occurrences == 0:
        return 0.0
    return min(occurrences, 8) * KIND_RANK_BOOSTS.get(document.kind, 1.0)


def linear_search(
    generation: CatalogGeneration,
    query: str,
    *,
    kinds: tuple[str, ...] = (),
    path_prefixes: tuple[str, ...] = (),
    limit: int,
    offset: int = 0,
    match_mode: SearchMatchMode = SearchMatchMode.ALL,
) -> tuple[tuple[RankedHit, ...], bool]:
    """Deterministic substring engine over the same documents; (hits, has_more)."""
    if not query:
        return (), False
    needles = tuple(token.casefold() for token in _query_tokens(query))
    if not needles:
        needles = (query.casefold(),)
    selected_kinds = frozenset(kinds)
    hits: list[RankedHit] = []
    for document in search_documents(generation):
        if selected_kinds and document.kind not in selected_kinds:
            continue
        if path_prefixes and not any(
            document.path == prefix or document.path.startswith(f"{prefix}/")
            for prefix in path_prefixes
        ):
            continue
        score = _linear_score(document, needles, match_mode=match_mode)
        if score > 0.0:
            hits.append(
                RankedHit(
                    fact_key=document.fact_key,
                    kind=document.kind,
                    path=document.path,
                    score=score,
                )
            )
    hits.sort(key=lambda hit: (-hit.score, hit.fact_key))
    window = hits[offset : offset + limit + 1]
    return tuple(window[:limit]), len(window) > limit
