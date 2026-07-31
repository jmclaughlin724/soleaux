"""Schema-preserving governance discovery and neutral evidence tracing."""

from __future__ import annotations

import json
import math
import tomllib
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from pathlib import PurePosixPath
from typing import TypeGuard

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import ValidationError

from soleaux.authority.contracts import (
    ClaimBasis,
    GovernanceState,
    OwnerSourceKind,
    PolicyBindingClaim,
    PolicyClaim,
    PolicyConflictClaim,
)
from soleaux.catalog.contracts import PolicyFact
from soleaux.contracts.config import (
    GovernanceConfig,
    GovernanceRelationshipConfig,
    GovernanceSourceConfig,
    MarkdownTableSelector,
    StructuredRecordsSelector,
)
from soleaux.contracts.frame import FactRow
from soleaux.contracts.governance import (
    GovernanceBindingKind,
    GovernanceTargetKind,
    governance_identifier,
    normalize_governance_identity,
)
from soleaux.contracts.repository import content_digest
from soleaux.structural.ast_runtime import bash_leaf_texts
from soleaux.structural.path_patterns import resolve_paths
from soleaux.structural.snapshot import SnapshotBundle

_STRUCTURED_SUFFIXES = frozenset({".json", ".toml", ".yaml", ".yml"})
_EMPTY_CELL_VALUES = frozenset({"", "-", "—"})
_CONFIGURED_CANONICALITY_SCORE = 1


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_object_sequence(
    value: object,
) -> TypeGuard[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _is_object_set(
    value: object,
) -> TypeGuard[set[object] | frozenset[object]]:
    return isinstance(value, (set, frozenset))


@dataclass(frozen=True)
class _MarkdownCell:
    text: str
    code: tuple[str, ...]
    links: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class _Heading:
    text: str
    level: int
    line: int


@dataclass(frozen=True)
class _MarkdownTable:
    heading: _Heading | None
    headers: tuple[_MarkdownCell, ...]
    rows: tuple[tuple[_MarkdownCell, ...], ...]


@dataclass(frozen=True)
class _CanonicalRecordSet:
    source: GovernanceSourceConfig
    source_path: str
    source_line: int
    source_heading: str | None
    vocabulary: tuple[str, ...]
    records: tuple[Mapping[str, object], ...]
    reference_hints: Mapping[tuple[int, str], tuple[str, ...]]


@dataclass(frozen=True)
class GovernanceCoverageNote:
    """A relationship-resolution limitation attributable to one record."""

    policy_id: str
    message: str


@dataclass(frozen=True)
class GovernanceClaims:
    """Canonical source records, neutral traced evidence, and coverage notes."""

    policies: tuple[PolicyClaim, ...]
    bindings: tuple[PolicyBindingClaim, ...]
    conflicts: tuple[PolicyConflictClaim, ...]
    warnings: tuple[str, ...]
    policy_warnings: tuple[GovernanceCoverageNote, ...]


@dataclass(frozen=True)
class _EvidenceRelationship:
    target: str
    relationship: str
    source_path: str
    attributes: Mapping[str, object]


def collect_governance_claims(
    bundle: SnapshotBundle,
    upstream_tables: Mapping[str, tuple[FactRow, ...]],
    *,
    governance: GovernanceConfig,
    policy_selectors: Sequence[str] = (),
) -> GovernanceClaims:
    """Read configured canonical sources and trace neutral repository evidence."""
    record_sets, discovery_warnings = _discover_record_sets(bundle, governance)
    policies: list[PolicyClaim] = []
    declared_bindings: list[PolicyBindingClaim] = []
    warnings = list(discovery_warnings)

    for record_set in record_sets:
        source_policies, source_bindings, source_warnings = _claims_for_record_set(
            bundle,
            record_set,
        )
        policies.extend(source_policies)
        declared_bindings.extend(source_bindings)
        warnings.extend(source_warnings)

    consolidated = _consolidate_policies(policies)
    if policy_selectors:
        selected_ids = {
            policy_id
            for selector in policy_selectors
            for policy_id in policy_ids_for_selector(
                consolidated,
                declared_bindings,
                selector,
            )
        }
        if selected_ids:
            consolidated = tuple(
                policy for policy in consolidated if policy.policy_id in selected_ids
            )
            declared_bindings = [
                binding for binding in declared_bindings if binding.policy_id in selected_ids
            ]
    inferred, inference_warnings = _infer_evidence_bindings(
        bundle,
        consolidated,
        tuple(declared_bindings),
        upstream_tables,
    )
    warnings.extend(inference_warnings)
    bindings, conflicts = _resolve_binding_claims((*declared_bindings, *inferred))
    unresolved = tuple(
        sorted(
            {
                GovernanceCoverageNote(
                    policy_id=binding.policy_id,
                    message=(
                        f"{binding.source_path}:{binding.source_line}: "
                        f"unresolved governance target {binding.target}"
                    ),
                )
                for binding in bindings
                if binding.binding_kind is GovernanceBindingKind.DECLARED
                and binding.state is GovernanceState.MISSING_TARGET
            },
            key=lambda note: (note.policy_id, note.message),
        )
    )
    return GovernanceClaims(
        policies=consolidated,
        bindings=bindings,
        conflicts=conflicts,
        warnings=tuple(dict.fromkeys(warnings)),
        policy_warnings=unresolved,
    )


def collect_policy_facts(
    bundle: SnapshotBundle,
    governance: GovernanceConfig,
    *,
    workspace_id: str,
) -> tuple[PolicyFact, ...]:
    """Promote configured-source records into catalog policy facts.

    Only the declared records are read; relationship targets, evidence
    inference, and conflict resolution stay request-time concerns.
    """
    if not governance.sources:
        return ()
    record_sets, _warnings = _discover_record_sets(bundle, governance)
    facts: list[PolicyFact] = []
    for record_set in record_sets:
        source = record_set.source
        digest = content_digest(bundle.contents[source.path])
        for row_index, record in enumerate(record_set.records):
            identity_value = _display_value(record.get(source.identity_field))
            if not identity_value:
                continue
            attributes = {
                field: _display_value(record.get(field)) for field in record_set.vocabulary
            }
            facts.append(
                PolicyFact(
                    workspace_id=workspace_id,
                    source_path=source.path,
                    source_digest=digest,
                    producer="authority:governance",
                    producer_version="1",
                    policy_id=_policy_id(source.id, identity_value),
                    title=identity_value,
                    governance_source_id=source.id,
                    identity_field=source.identity_field,
                    source_line=_record_line(record_set, row_index),
                    attributes=attributes,
                )
            )
    return tuple(sorted(facts, key=lambda fact: (fact.governance_source_id, fact.policy_id)))


def _discover_record_sets(
    bundle: SnapshotBundle,
    governance: GovernanceConfig,
) -> tuple[tuple[_CanonicalRecordSet, ...], tuple[str, ...]]:
    discovered: list[_CanonicalRecordSet] = []
    warnings: list[str] = []
    for source in governance.sources:
        content = bundle.contents.get(source.path)
        if content is None:
            warnings.append(
                f"governance_source_missing: source {source.id!r} names {source.path!r}, "
                "which is not in the workspace snapshot"
            )
            continue
        if isinstance(source.selector, MarkdownTableSelector):
            record_set, notes = _markdown_source_records(bundle, source, content)
        else:
            record_set, notes = _structured_source_records(bundle, source, content)
        if record_set is not None:
            discovered.append(record_set)
        warnings.extend(notes)
    return tuple(discovered), tuple(warnings)


def _markdown_source_records(
    bundle: SnapshotBundle,
    source: GovernanceSourceConfig,
    content: bytes,
) -> tuple[_CanonicalRecordSet | None, tuple[str, ...]]:
    selector = source.selector
    assert isinstance(selector, MarkdownTableSelector)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, (
            f"governance_parser_failed: source {source.id!r} at {source.path!r} is not UTF-8",
        )
    tables, parser_warning = _markdown_tables(text)
    if parser_warning is not None:
        return None, (
            f"governance_parser_failed: source {source.id!r} at {source.path!r}: {parser_warning}",
        )

    wanted = selector.heading.strip()
    matching = [
        table
        for table in tables
        if table.heading is not None and table.heading.text.strip() == wanted
    ]
    if len(matching) < selector.occurrence:
        diagnostic = (
            "governance_selector_ambiguous"
            if len(matching) > 1 and selector.occurrence == 1
            else "governance_selector_not_found"
        )
        return None, (
            f"{diagnostic}: source {source.id!r} found {len(matching)} table(s) under "
            f"heading {wanted!r} in {source.path!r} but occurrence "
            f"{selector.occurrence} was requested",
        )
    table = matching[selector.occurrence - 1]
    if not table.headers or not table.rows:
        return None, (
            f"governance_selector_not_found: source {source.id!r} selected an empty table "
            f"under heading {wanted!r} in {source.path!r}",
        )
    vocabulary = tuple(header.text.strip() for header in table.headers)
    warnings = list(_missing_configured_fields(source, vocabulary))
    if warnings:
        return None, tuple(warnings)

    all_paths = frozenset(bundle.contents)
    records: list[Mapping[str, object]] = []
    hints: dict[tuple[int, str], tuple[str, ...]] = {}
    for row_index, row in enumerate(table.rows):
        if len(row) != len(vocabulary):
            continue
        record = {field: _cell_value(cell) for field, cell in zip(vocabulary, row, strict=True)}
        records.append(record)
        for field, cell in zip(vocabulary, row, strict=True):
            targets = _markdown_cell_targets(cell, all_paths)
            if targets:
                hints[(row_index, field)] = targets
    line = table.rows[0][0].line if table.rows and table.rows[0] else table.headers[0].line
    return (
        _CanonicalRecordSet(
            source=source,
            source_path=source.path,
            source_line=line,
            source_heading=table.heading.text if table.heading is not None else None,
            vocabulary=vocabulary,
            records=tuple(records),
            reference_hints=hints,
        ),
        (),
    )


def _missing_configured_fields(
    source: GovernanceSourceConfig,
    vocabulary: tuple[str, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    if source.identity_field not in vocabulary:
        warnings.append(
            f"governance_identity_field_missing: source {source.id!r} names identity field "
            f"{source.identity_field!r}, which the selected records do not carry"
        )
    for relationship in source.relationships:
        if relationship.field not in vocabulary:
            warnings.append(
                f"governance_relationship_field_missing: source {source.id!r} names "
                f"relationship field {relationship.field!r}, which the selected records "
                "do not carry"
            )
    return tuple(warnings)


def _structured_source_records(
    bundle: SnapshotBundle,
    source: GovernanceSourceConfig,
    content: bytes,
) -> tuple[_CanonicalRecordSet | None, tuple[str, ...]]:
    selector = source.selector
    assert isinstance(selector, StructuredRecordsSelector)
    suffix = f".{source.format}"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, (
            f"governance_parser_failed: source {source.id!r} at {source.path!r} is not UTF-8",
        )
    parsed = _parse_structured_content(suffix, content)
    if parsed is None:
        return None, (
            f"governance_parser_failed: source {source.id!r} at {source.path!r} did not parse "
            f"as {source.format}",
        )

    addressed: object = parsed
    for key in selector.keys:
        if not _is_object_dict(addressed):
            addressed = None
            break
        addressed = addressed.get(key)
    records = _object_records(addressed)
    if not records:
        return None, (
            f"governance_selector_not_found: source {source.id!r} key path "
            f"{'.'.join(selector.keys)!r} in {source.path!r} does not address a list of records",
        )
    vocabulary = _shared_vocabulary(records)
    warnings = list(_missing_configured_fields(source, vocabulary))
    if warnings:
        return None, tuple(warnings)

    all_paths = frozenset(bundle.contents)
    hints: dict[tuple[int, str], tuple[str, ...]] = {}
    for row_index, record in enumerate(records):
        for field in vocabulary:
            targets = _structured_value_targets(record.get(field), all_paths)
            if targets:
                hints[(row_index, field)] = targets
    first_identity = next(
        (
            value
            for record in records
            for value in record.values()
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    return (
        _CanonicalRecordSet(
            source=source,
            source_path=source.path,
            source_line=_line_for_value(text, first_identity),
            source_heading=".".join(selector.keys),
            vocabulary=vocabulary,
            records=records,
            reference_hints=hints,
        ),
        (),
    )


def _object_records(value: object) -> tuple[Mapping[str, object], ...] | None:
    if not _is_object_list(value):
        return None
    records: list[Mapping[str, object]] = []
    for item in value:
        if not _is_object_dict(item):
            return None
        records.append({str(key): child for key, child in item.items()})
    return tuple(records)


def _shared_vocabulary(
    records: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    if not records:
        return ()
    first = tuple(str(key) for key in records[0])
    shared = set(first)
    for record in records[1:]:
        shared.intersection_update(str(key) for key in record)
    return tuple(field for field in first if field in shared)


def _claims_for_record_set(
    bundle: SnapshotBundle,
    record_set: _CanonicalRecordSet,
) -> tuple[tuple[PolicyClaim, ...], tuple[PolicyBindingClaim, ...], tuple[str, ...]]:
    source = record_set.source
    identity_field = source.identity_field
    policies: list[PolicyClaim] = []
    bindings: list[PolicyBindingClaim] = []
    warnings: list[str] = []
    required_roles = tuple(
        _relationship_role(relationship)
        for relationship in source.relationships
        if relationship.required
    )

    for row_index, record in enumerate(record_set.records):
        identity_value = _display_value(record.get(identity_field))
        if not identity_value:
            warnings.append(
                f"governance_record_identity_missing: {record_set.source_path}:"
                f"{record_set.source_line}: record has no value for {identity_field!r}"
            )
            continue
        policy_id = _policy_id(source.id, identity_value)
        source_line = _record_line(record_set, row_index)
        attributes = {field: _json_value(record.get(field)) for field in record_set.vocabulary}
        try:
            policy = PolicyClaim(
                policy_id=policy_id,
                governance_source_id=source.id,
                title=identity_value,
                aliases=(),
                scope=(),
                required_roles=required_roles,
                source_heading=record_set.source_heading,
                identity_field=identity_field,
                identity_value=identity_value,
                vocabulary=record_set.vocabulary,
                attributes=attributes,
                canonicality_basis=(f"configured:{source.id}",),
                canonicality_score=_CONFIGURED_CANONICALITY_SCORE,
                source_kind=OwnerSourceKind.EXPLICIT_GOVERNANCE,
                source_path=record_set.source_path,
                source_line=source_line,
                basis=ClaimBasis.DECLARED,
            )
        except ValidationError as exc:
            warnings.append(
                f"{record_set.source_path}:{source_line}: invalid canonical record: {exc}"
            )
            continue
        policies.append(policy)
        for relationship in source.relationships:
            role_label = relationship.field
            targets = record_set.reference_hints.get((row_index, role_label), ())
            if not targets:
                if relationship.required:
                    warnings.append(
                        f"governance_relationship_field_missing: {record_set.source_path}:"
                        f"{source_line}: record {identity_value!r} declares no resolvable "
                        f"target for required field {role_label!r}"
                    )
                continue
            for target in targets:
                bindings.append(
                    _binding(
                        bundle,
                        policy_id=policy.policy_id,
                        binding_kind=GovernanceBindingKind.DECLARED,
                        role=_relationship_role(relationship),
                        role_label=role_label,
                        target=target,
                        target_kind=_configured_target_kind(relationship, target),
                        relationship="declared",
                        attributes=attributes,
                        basis=ClaimBasis.DECLARED,
                        source_kind=OwnerSourceKind.EXPLICIT_GOVERNANCE,
                        source_path=record_set.source_path,
                        source_line=source_line,
                    )
                )
    return tuple(policies), tuple(bindings), tuple(warnings)


def _relationship_role(relationship: GovernanceRelationshipConfig) -> str:
    return relationship.role or _role_identifier(relationship.field)


def _configured_target_kind(
    relationship: GovernanceRelationshipConfig,
    target: str,
) -> GovernanceTargetKind:
    if relationship.target_kind is not GovernanceTargetKind.AUTO:
        return relationship.target_kind
    return _target_kind(target)


def _record_line(record_set: _CanonicalRecordSet, row_index: int) -> int:
    line = record_set.source_line
    for field in record_set.vocabulary:
        hint = record_set.reference_hints.get((row_index, field))
        if hint:
            return line + row_index
    return line + row_index


def _policy_id(source_id: str, identity_value: str) -> str:
    authored = identity_value.strip()
    if _is_authored_identifier(authored):
        return authored
    return f"{source_id}:{normalize_governance_identity(authored)}"


def _infer_evidence_bindings(
    bundle: SnapshotBundle,
    policies: Sequence[PolicyClaim],
    declared_bindings: tuple[PolicyBindingClaim, ...],
    upstream_tables: Mapping[str, tuple[FactRow, ...]],
) -> tuple[tuple[PolicyBindingClaim, ...], tuple[str, ...]]:
    """Trace bounded repository relationships without assigning policy roles."""
    declaration_sources = {policy.source_path for policy in policies}
    structured_forward = _structured_references(
        bundle,
        excluded_paths=declaration_sources,
    )
    structured_reverse = _reverse_paths(structured_forward)
    import_forward, import_reverse = _import_relationships(bundle, upstream_tables)
    config_forward = _configuration_relationships(upstream_tables)
    config_reverse = _reverse_paths(config_forward)
    scripts_by_reference = _script_relationships(bundle, upstream_tables)
    test_paths = {
        path
        for row in upstream_tables.get("tests", ())
        if isinstance((path := row.data.get("path")), str)
    }
    registration_paths = {
        path
        for row in upstream_tables.get("framework.registrations", ())
        if isinstance((path := row.data.get("path")), str)
    }

    by_policy: defaultdict[str, list[PolicyBindingClaim]] = defaultdict(list)
    for binding in declared_bindings:
        by_policy[binding.policy_id].append(binding)

    inferred: list[PolicyBindingClaim] = []
    warnings: list[str] = []
    max_per_policy = 256
    for policy in policies:
        queue = deque(
            (path, 0)
            for binding in by_policy.get(policy.policy_id, ())
            if binding.state is not GovernanceState.MISSING_TARGET
            for path in _expanded_target_paths(bundle, binding.target)
        )
        seen_paths = {path for path, _depth in queue}
        seen_edges: set[tuple[str, str, str]] = set()
        policy_inferred: list[PolicyBindingClaim] = []
        bounded = False
        while queue:
            seed_path, depth = queue.popleft()
            if depth >= 2:
                continue
            relationships = _relationships_for_path(
                seed_path,
                structured_forward=structured_forward,
                structured_reverse=structured_reverse,
                import_forward=import_forward,
                import_reverse=import_reverse,
                config_forward=config_forward,
                config_reverse=config_reverse,
                scripts_by_reference=scripts_by_reference,
                test_paths=test_paths,
                registration_paths=registration_paths,
            )
            for relationship in relationships:
                edge_key = (
                    relationship.relationship,
                    relationship.target,
                    relationship.source_path,
                )
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                binding = _binding(
                    bundle,
                    policy_id=policy.policy_id,
                    binding_kind=GovernanceBindingKind.EVIDENCE,
                    role=None,
                    role_label=None,
                    target=relationship.target,
                    target_kind=GovernanceTargetKind.PATH,
                    relationship=relationship.relationship,
                    attributes=dict(relationship.attributes),
                    basis=ClaimBasis.INFERRED,
                    source_kind=OwnerSourceKind.CANONICAL_RELATIONSHIP,
                    source_path=relationship.source_path,
                )
                policy_inferred.append(binding)
                if len(policy_inferred) >= max_per_policy:
                    bounded = True
                    queue.clear()
                    break
                for path in _expanded_target_paths(bundle, binding.target):
                    if path not in seen_paths:
                        seen_paths.add(path)
                        queue.append((path, depth + 1))
            if bounded:
                break
        inferred.extend(policy_inferred)
        if bounded:
            warnings.append(
                f"{policy.source_path}: inferred evidence for "
                f"{policy.policy_id} reached limit {max_per_policy}"
            )
    return tuple(inferred), tuple(warnings)


def _relationships_for_path(
    seed_path: str,
    *,
    structured_forward: Mapping[str, tuple[str, ...]],
    structured_reverse: Mapping[str, tuple[str, ...]],
    import_forward: Mapping[str, tuple[str, ...]],
    import_reverse: Mapping[str, tuple[str, ...]],
    config_forward: Mapping[str, tuple[str, ...]],
    config_reverse: Mapping[str, tuple[str, ...]],
    scripts_by_reference: Mapping[str, tuple[tuple[str, str, str], ...]],
    test_paths: set[str],
    registration_paths: set[str],
) -> tuple[_EvidenceRelationship, ...]:
    relationships: list[_EvidenceRelationship] = []

    def add_many(
        targets: Iterable[str],
        relationship: str,
        *,
        reverse: bool = False,
    ) -> None:
        for target in targets:
            relationships.append(
                _EvidenceRelationship(
                    target=target,
                    relationship=relationship,
                    source_path=target if reverse else seed_path,
                    attributes={"from": seed_path, "to": target},
                )
            )

    add_many(structured_forward.get(seed_path, ()), "references")
    add_many(structured_reverse.get(seed_path, ()), "referenced-by", reverse=True)
    add_many(import_forward.get(seed_path, ()), "imports")
    for source in import_reverse.get(seed_path, ()):
        add_many(
            (source,),
            "tested-by" if source in test_paths else "imported-by",
            reverse=True,
        )
    add_many(config_forward.get(seed_path, ()), "configures")
    add_many(config_reverse.get(seed_path, ()), "configured-by", reverse=True)
    for script_target, source_path, command in scripts_by_reference.get(seed_path, ()):
        relationships.append(
            _EvidenceRelationship(
                target=script_target,
                relationship="invoked-by",
                source_path=source_path,
                attributes={
                    "from": seed_path,
                    "to": script_target,
                    "command": command,
                },
            )
        )
    if seed_path in registration_paths:
        relationships.append(
            _EvidenceRelationship(
                target=seed_path,
                relationship="registered",
                source_path=seed_path,
                attributes={"target": seed_path},
            )
        )
    return tuple(relationships)


def _structured_references(
    bundle: SnapshotBundle,
    *,
    excluded_paths: set[str],
) -> dict[str, tuple[str, ...]]:
    references: dict[str, tuple[str, ...]] = {}
    all_paths = frozenset(bundle.contents)
    resolved_values: dict[str, tuple[str, ...]] = {}
    for path in sorted(bundle.contents):
        if path in excluded_paths:
            continue
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in _STRUCTURED_SUFFIXES:
            continue
        parsed = _parse_structured_content(suffix, bundle.contents[path])
        if parsed is None:
            continue
        discovered = {
            referenced
            for value in _string_values(parsed)
            for referenced in _structured_path_targets(
                value,
                all_paths,
                resolved_values,
            )
            if referenced != path
        }
        if discovered:
            references[path] = tuple(sorted(discovered))
    return references


def _structured_path_targets(
    value: str,
    all_paths: frozenset[str],
    resolved_values: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    token = value.strip().removeprefix("./").partition("#")[0]
    cached = resolved_values.get(token)
    if cached is not None:
        return cached
    if token in all_paths:
        resolved = (token,)
    elif _is_repository_path_pattern(token):
        resolved = _exact_path_targets(token, all_paths)
    else:
        resolved = ()
    resolved_values[token] = resolved
    return resolved


def _is_repository_path_pattern(token: str) -> bool:
    if ":" in token or token in {"*", "**"}:
        return False
    if not any(character in token for character in "*?["):
        return False
    if "/" in token or "\\" in token:
        return True
    suffix = PurePosixPath(token).suffix.removeprefix(".")
    return bool(suffix) and any(
        character not in "*?[]" and character != "." for character in suffix
    )


def _parse_structured_content(suffix: str, content: bytes) -> object | None:
    try:
        text = content.decode("utf-8")
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            return tomllib.loads(text)
        return yaml.safe_load(text)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        yaml.YAMLError,
    ):
        return None


def _string_values(value: object) -> tuple[str, ...]:
    values: list[str] = []
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            values.append(current)
        elif _is_object_mapping(current):
            stack.extend(current.values())
        elif _is_object_sequence(current):
            stack.extend(current)
    return tuple(values)


def _exact_path_targets(
    value: str,
    all_paths: frozenset[str],
    *,
    include_unresolved: bool = False,
    assignment_value: bool = False,
) -> tuple[str, ...]:
    token = value.strip()
    if assignment_value and "=" in token:
        token = token.rsplit("=", 1)[-1]
    token = token.removeprefix("./").partition("#")[0]
    if resolve_paths(token, all_paths) or (include_unresolved and _looks_like_path(token)):
        return (token,)
    return ()


def _import_relationships(
    bundle: SnapshotBundle,
    upstream_tables: Mapping[str, tuple[FactRow, ...]],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    edges: defaultdict[str, set[str]] = defaultdict(set)
    for row in upstream_tables.get("repository.imports", ()):
        source = row.data.get("path")
        target = row.data.get("resolved_path")
        if isinstance(source, str) and isinstance(target, str) and target in bundle.contents:
            edges[source].add(target)
    for row in upstream_tables.get("syntax.imports", ()):
        source = row.data.get("path")
        specifier = row.data.get("name")
        if not isinstance(source, str) or not isinstance(specifier, str):
            continue
        target = _resolve_syntax_import(bundle, source, specifier)
        if target is not None:
            edges[source].add(target)
    forward = {source: tuple(sorted(targets)) for source, targets in edges.items()}
    return forward, _reverse_paths(forward)


def _resolve_syntax_import(
    bundle: SnapshotBundle,
    source: str,
    specifier: str,
) -> str | None:
    parent = PurePosixPath(source).parent
    candidates: list[str] = []
    if specifier.startswith("."):
        candidates.append(str(parent / specifier))
    elif "/" in specifier or "." in specifier:
        candidates.append(specifier.replace(".", "/"))
    for base in tuple(candidates):
        candidates.extend(
            (
                f"{base}.py",
                f"{base}.ts",
                f"{base}.tsx",
                f"{base}.js",
                f"{base}.jsx",
                f"{base}.mjs",
                f"{base}/__init__.py",
                f"{base}/index.ts",
                f"{base}/index.tsx",
                f"{base}/index.js",
            )
        )
    return next(
        (
            normalized
            for candidate in candidates
            if (normalized := str(PurePosixPath(candidate))) in bundle.contents
        ),
        None,
    )


def _configuration_relationships(
    upstream_tables: Mapping[str, tuple[FactRow, ...]],
) -> dict[str, tuple[str, ...]]:
    relationships: dict[str, tuple[str, ...]] = {}
    for row in upstream_tables.get("repository.configurations", ()):
        source = row.data.get("config_path")
        closure = row.data.get("closure_paths")
        if not isinstance(source, str) or not _is_object_sequence(closure):
            continue
        targets = tuple(
            sorted({target for target in closure if isinstance(target, str) and target != source})
        )
        if targets:
            relationships[source] = targets
    return relationships


def _script_relationships(
    bundle: SnapshotBundle,
    upstream_tables: Mapping[str, tuple[FactRow, ...]],
) -> dict[str, tuple[tuple[str, str, str], ...]]:
    by_reference: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
    all_paths = frozenset(bundle.contents)
    scripts: list[tuple[str, str, str]] = []
    for row in upstream_tables.get("repository.scripts", ()):
        name = row.data.get("name")
        command = row.data.get("command")
        source_path = row.evidence.path
        if not isinstance(name, str) or not isinstance(command, str):
            continue
        scripts.append((name, command, source_path))
    parsed_commands = bash_leaf_texts(tuple(command for _name, command, _path in scripts))
    for (name, command, source_path), leaf_texts in zip(
        scripts,
        parsed_commands,
        strict=True,
    ):
        script_target = f"{source_path}#scripts.{name}"
        references = {
            referenced
            for leaf_text in leaf_texts
            for referenced in _exact_path_targets(
                leaf_text,
                all_paths,
                assignment_value=True,
            )
        }
        for referenced in references:
            by_reference[referenced].append((script_target, source_path, command))
    return {target: tuple(sorted(rows)) for target, rows in by_reference.items()}


def _reverse_paths(
    forward: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    reverse: defaultdict[str, set[str]] = defaultdict(set)
    for source, targets in forward.items():
        for target in targets:
            reverse[target].add(source)
    return {target: tuple(sorted(sources)) for target, sources in reverse.items()}


def _expanded_target_paths(
    bundle: SnapshotBundle,
    target: str,
) -> tuple[str, ...]:
    return resolve_paths(target.partition("#")[0], bundle.contents)


def _resolve_binding_claims(
    bindings: Sequence[PolicyBindingClaim],
) -> tuple[tuple[PolicyBindingClaim, ...], tuple[PolicyConflictClaim, ...]]:
    unique = {binding.binding_id: binding for binding in bindings}
    declared_by_domain: defaultdict[
        tuple[str, str],
        list[PolicyBindingClaim],
    ] = defaultdict(list)
    for binding in unique.values():
        if binding.binding_kind is GovernanceBindingKind.DECLARED:
            assert binding.role is not None
            assert binding.role_label is not None
            declared_by_domain[(binding.policy_id, binding.role_label)].append(binding)

    resolved = dict(unique)
    conflicts: list[PolicyConflictClaim] = []
    for domain, claims in sorted(declared_by_domain.items()):
        declarations: defaultdict[tuple[str, int], list[PolicyBindingClaim]] = defaultdict(list)
        for claim in claims:
            declarations[(claim.source_path, claim.source_line)].append(claim)
        if len(declarations) < 2:
            # One authored declaration; several targets in one cell are a
            # valid set, never a conflict among themselves.
            continue
        declaration_signatures = {
            site: frozenset(_binding_signature(claim) for claim in members)
            for site, members in declarations.items()
        }
        conflict_kind = (
            "redundant" if len(set(declaration_signatures.values())) == 1 else "conflicting"
        )
        conflict_id = _conflict_id(conflict_kind, domain, claims)
        competing_ids = tuple(sorted(claim.binding_id for claim in claims))
        winning_site = min(
            declarations,
            key=lambda site: (
                min(claim.source_kind.precedence for claim in declarations[site]),
                site[0],
                site[1],
            ),
        )
        for site, members in sorted(declarations.items()):
            for claim in members:
                if conflict_kind == "conflicting":
                    state = GovernanceState.CONFLICTING
                    reason = "canonical sources disagree for the same authored field role"
                elif site == winning_site:
                    state = claim.state
                    reason = "canonical sources redundantly declare the same field relationship"
                else:
                    state = GovernanceState.SHADOWED
                    reason = "redundant canonical declaration; keep one source of truth"
                resolved[claim.binding_id] = claim.model_copy(update={"state": state})
                conflicts.append(
                    _conflict_claim(
                        conflict_id,
                        claim,
                        competing_ids,
                        state,
                        reason,
                    )
                )

    ordered_bindings = tuple(
        sorted(
            resolved.values(),
            key=lambda item: (
                item.policy_id,
                item.binding_kind.value,
                item.role or "",
                item.relationship,
                item.target,
                item.source_path,
                item.source_line,
            ),
        )
    )
    ordered_conflicts = tuple(
        sorted(
            conflicts,
            key=lambda item: (item.conflict_id, item.binding_id),
        )
    )
    return ordered_bindings, ordered_conflicts


def _consolidate_policies(
    claims: Sequence[PolicyClaim],
) -> tuple[PolicyClaim, ...]:
    by_id: defaultdict[str, list[PolicyClaim]] = defaultdict(list)
    for claim in claims:
        by_id[claim.policy_id].append(claim)
    policies: list[PolicyClaim] = []
    for _policy_id, policy_claims in sorted(by_id.items()):
        winner = min(
            policy_claims,
            key=lambda item: (
                -item.canonicality_score,
                item.source_path,
                item.source_line,
            ),
        )
        policies.append(
            winner.model_copy(
                update={
                    "aliases": tuple(
                        dict.fromkeys(
                            value
                            for claim in policy_claims
                            for value in (claim.title, claim.identity_value)
                            if value != winner.title
                        )
                    ),
                    "scope": tuple(dict.fromkeys(claim.source_path for claim in policy_claims)),
                    "required_roles": tuple(
                        sorted({role for claim in policy_claims for role in claim.required_roles})
                    ),
                    "canonicality_basis": tuple(
                        dict.fromkeys(
                            basis for claim in policy_claims for basis in claim.canonicality_basis
                        )
                    ),
                    "canonicality_score": max(claim.canonicality_score for claim in policy_claims),
                }
            )
        )
    return tuple(policies)


def policy_ids_for_selector(
    policies: Sequence[PolicyClaim],
    bindings: Sequence[PolicyBindingClaim],
    selector: str,
) -> tuple[str, ...]:
    """Resolve exact ID, path, or consumer-authored identity and aliases."""
    exact_ids = tuple(policy.policy_id for policy in policies if policy.policy_id == selector)
    if exact_ids:
        return exact_ids
    path_ids = {
        policy.policy_id
        for policy in policies
        if policy.source_path == selector or selector in policy.scope
    }
    path_ids.update(
        binding.policy_id
        for binding in bindings
        if binding.source_path == selector or binding.target == selector
    )
    if path_ids:
        return tuple(sorted(path_ids))
    normalized = normalize_governance_identity(selector)
    return tuple(
        sorted(
            policy.policy_id
            for policy in policies
            if normalized
            in {
                normalize_governance_identity(policy.title),
                normalize_governance_identity(policy.identity_value),
                *(normalize_governance_identity(alias) for alias in policy.aliases),
            }
        )
    )


def _binding(
    bundle: SnapshotBundle,
    *,
    policy_id: str,
    binding_kind: GovernanceBindingKind,
    role: str | None,
    role_label: str | None,
    target: str,
    target_kind: GovernanceTargetKind,
    relationship: str,
    attributes: Mapping[str, object],
    basis: ClaimBasis,
    source_kind: OwnerSourceKind,
    source_path: str,
    source_line: int = 1,
) -> PolicyBindingClaim:
    normalized_target = target.strip()
    normalized_relationship = governance_identifier(
        relationship,
        field_name="relationship",
    )
    return PolicyBindingClaim(
        binding_id=_binding_id(
            policy_id=policy_id,
            binding_kind=binding_kind,
            role=role,
            target=normalized_target,
            target_kind=target_kind,
            relationship=normalized_relationship,
            source_path=source_path,
            source_line=source_line,
        ),
        policy_id=policy_id,
        binding_kind=binding_kind,
        role=role,
        role_label=role_label,
        target=normalized_target,
        target_kind=target_kind,
        relationship=normalized_relationship,
        attributes=dict(attributes),
        basis=basis,
        state=_target_state(bundle, normalized_target, target_kind),
        source_kind=source_kind,
        source_path=source_path,
        source_line=source_line,
    )


def _binding_id(
    *,
    policy_id: str,
    binding_kind: GovernanceBindingKind,
    role: str | None,
    target: str,
    target_kind: GovernanceTargetKind,
    relationship: str,
    source_path: str,
    source_line: int,
) -> str:
    payload = json.dumps(
        {
            "policy_id": policy_id,
            "binding_kind": binding_kind.value,
            "role": role,
            "target": target,
            "target_kind": target_kind.value,
            "relationship": relationship,
            "source_path": source_path,
            "source_line": source_line,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"binding:{content_digest(payload)[:24]}"


def _conflict_id(
    kind: str,
    domain: tuple[str, str],
    claims: Sequence[PolicyBindingClaim],
) -> str:
    payload = json.dumps(
        {
            "kind": kind,
            "domain": domain,
            "binding_ids": sorted(claim.binding_id for claim in claims),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"conflict:{content_digest(payload)[:24]}"


def _conflict_claim(
    conflict_id: str,
    claim: PolicyBindingClaim,
    competing_ids: tuple[str, ...],
    state: GovernanceState,
    reason: str,
) -> PolicyConflictClaim:
    return PolicyConflictClaim(
        conflict_id=conflict_id,
        policy_id=claim.policy_id,
        role=claim.role,
        role_label=claim.role_label,
        binding_id=claim.binding_id,
        competing_binding_ids=tuple(
            binding_id for binding_id in competing_ids if binding_id != claim.binding_id
        ),
        state=state,
        reason=reason,
        source_path=claim.source_path,
        source_line=claim.source_line,
    )


def _binding_signature(binding: PolicyBindingClaim) -> tuple[str, ...]:
    return (
        binding.target,
        binding.target_kind.value,
        binding.relationship,
    )


def _target_state(
    bundle: SnapshotBundle,
    target: str,
    target_kind: GovernanceTargetKind,
) -> GovernanceState:
    if target_kind is GovernanceTargetKind.REFERENCE:
        return GovernanceState.EFFECTIVE
    if resolve_paths(target.partition("#")[0], bundle.contents):
        return GovernanceState.EFFECTIVE
    return GovernanceState.MISSING_TARGET


def _target_kind(target: str) -> GovernanceTargetKind:
    return GovernanceTargetKind.PATH if _looks_like_path(target) else GovernanceTargetKind.REFERENCE


def _looks_like_path(target: str) -> bool:
    if "://" in target or "\n" in target:
        return False
    candidate = target.partition("#")[0]
    if any(character.isspace() for character in candidate):
        return False
    return (
        candidate.startswith((".", "/"))
        or "/" in candidate
        or "\\" in candidate
        or any(character in candidate for character in "*?[")
        or bool(PurePosixPath(candidate).suffix)
    )


def _markdown_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def _markdown_tables(
    text: str,
) -> tuple[tuple[_MarkdownTable, ...], str | None]:
    """Build table records and local evidence solely from Markdown AST tokens."""
    parser = _markdown_parser()
    tokens = parser.parse(text)
    tables: list[_MarkdownTable] = []
    active_heading: _Heading | None = None
    pending_heading: tuple[int, int, int] | None = None
    headers: list[_MarkdownCell] = []
    rows: list[tuple[_MarkdownCell, ...]] = []
    current_row: list[_MarkdownCell] | None = None
    row_line = 1
    in_header = False
    active_table = False

    for token in tokens:
        if token.type == "heading_open":
            level = int(token.tag[1:]) if token.tag.startswith("h") else 1
            start_line = token.map[0] + 1 if token.map else 1
            end_line = token.map[1] if token.map else start_line
            pending_heading = (level, start_line, end_line)
        elif token.type == "inline" and pending_heading is not None:
            active_heading = _Heading(
                text=_inline_text(token),
                level=pending_heading[0],
                line=pending_heading[1],
            )
            pending_heading = None
        elif token.type == "table_open":
            active_table = True
            headers = []
            rows = []
        elif token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "tr_open" and active_table:
            current_row = []
            row_line = token.map[0] + 1 if token.map else 1
        elif token.type == "inline" and current_row is not None:
            code = tuple(
                child.content.strip()
                for child in token.children or ()
                if child.type == "code_inline" and child.content.strip()
            )
            links = tuple(
                href
                for child in token.children or ()
                if child.type == "link_open"
                and isinstance(href := child.attrGet("href"), str)
                and href.strip()
            )
            current_row.append(
                _MarkdownCell(
                    text=token.content.strip(),
                    code=code,
                    links=links,
                    line=token.map[0] + 1 if token.map else row_line,
                )
            )
        elif token.type == "tr_close" and current_row is not None:
            if in_header:
                headers = current_row
            else:
                rows.append(tuple(current_row))
            current_row = None
        elif token.type == "table_close" and active_table:
            tables.append(
                _MarkdownTable(
                    heading=active_heading,
                    headers=tuple(headers),
                    rows=tuple(rows),
                )
            )
            active_table = False

    return tuple(tables), None


def _inline_text(token: Token) -> str:
    visible: list[str] = []
    for child in token.children or ():
        if child.type in {"code_inline", "text"}:
            visible.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            visible.append(" ")
    return "".join(visible).strip() or token.content.strip()


def _markdown_cell_targets(
    cell: _MarkdownCell,
    all_paths: frozenset[str],
) -> tuple[str, ...]:
    targets: list[str] = []
    for value in (*cell.links, *cell.code):
        normalized = value.strip().removeprefix("./")
        if normalized and normalized not in _EMPTY_CELL_VALUES:
            targets.append(normalized)
    if not cell.links and not cell.code:
        targets.extend(
            _exact_path_targets(
                cell.text,
                all_paths,
                include_unresolved=True,
            )
        )
    return tuple(dict.fromkeys(targets))


def _structured_value_targets(
    value: object,
    all_paths: frozenset[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            target
            for text in _string_values(value)
            for target in _exact_path_targets(
                text,
                all_paths,
                include_unresolved=True,
            )
        )
    )


def _cell_value(cell: _MarkdownCell) -> object:
    if cell.links:
        return {
            "text": cell.text,
            "links": cell.links,
            "code": cell.code,
        }
    if len(cell.code) == 1 and cell.text.strip("` ") == cell.code[0]:
        return cell.code[0]
    if cell.code:
        return {"text": cell.text, "code": cell.code}
    return cell.text


def _display_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip().strip("`")
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if _is_object_mapping(value):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return ""


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if _is_object_mapping(value):
        return {str(key): _json_value(item) for key, item in value.items()}
    if _is_object_sequence(value):
        return [_json_value(item) for item in value]
    if _is_object_set(value):
        projected = [_json_value(item) for item in value]
        return sorted(
            projected,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    return str(value)


def _role_identifier(label: str) -> str:
    normalized = _normalized_label(label)
    if not normalized:
        payload = content_digest(label.encode("utf-8"))[:16]
        normalized = f"field-{payload}"
    elif not normalized[0].isalpha():
        normalized = f"field-{normalized}"
    return governance_identifier(normalized[:128].rstrip("-"), field_name="role")


def _normalized_label(label: str) -> str:
    parts: list[str] = []
    current: list[str] = []
    for character in label.strip().casefold():
        if ("a" <= character <= "z") or ("0" <= character <= "9"):
            current.append(character)
            continue
        if current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    return "-".join(parts)


def _is_authored_identifier(value: str) -> bool:
    if not 1 <= len(value) <= 256 or not _is_ascii_alphanumeric(value[0]):
        return False
    return all(_is_ascii_alphanumeric(character) or character in "_.:-" for character in value[1:])


def _is_ascii_alphanumeric(character: str) -> bool:
    return "a" <= character <= "z" or "A" <= character <= "Z" or "0" <= character <= "9"


def _line_for_value(text: str, value: str) -> int:
    if not value:
        return 1
    offset = text.find(value)
    return 1 if offset < 0 else text.count("\n", 0, offset) + 1
