"""Structured manifest and governance authority resolution (D003, D012, D030)."""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TypeGuard

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soleaux.authority.contracts import (
    AuthorityClaim,
    AuthorityResult,
    ClaimBasis,
    EntrypointClaim,
    EntrypointKind,
    GovernanceState,
    HistoryOwnerProvider,
    OwnerKind,
    OwnerSourceKind,
    PolicyBindingClaim,
    PolicyClaim,
    PolicyConflictClaim,
)
from soleaux.authority.governance import (
    collect_governance_claims,
    policy_ids_for_selector,
)
from soleaux.contracts.config import GovernanceConfig
from soleaux.contracts.evidence import Authority, EvidenceKind, ResolutionStatus
from soleaux.contracts.frame import FactRow
from soleaux.contracts.repository import content_digest
from soleaux.contracts.requests import SemanticMode
from soleaux.contracts.tables import PRODUCER_SUPPORTED_TABLES, Producer
from soleaux.structural.path_patterns import RepositoryPattern
from soleaux.structural.snapshot import SnapshotBundle
from soleaux.tables.evidence import evidence_for_path

_CODEOWNERS_PATHS = frozenset({"CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"})
_YAML_MANIFEST_NAMES = frozenset({"soleaux.yaml", "soleaux.yml", "OWNERS"})


def _is_object_dict(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


class AmbiguousOwnerAuthorityError(ValueError):
    """Legacy exception retained for callers that imported the public type."""


@dataclass(frozen=True)
class _OwnerConflictEvidence:
    conflict: PolicyConflictClaim
    claim: AuthorityClaim


class _OwnerDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    owners: tuple[str, ...] = Field(min_length=1)
    kind: OwnerKind


class _EntrypointDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: EntrypointKind
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)


class _RegistrationDeclaration(_EntrypointDeclaration):
    owners: tuple[str, ...] = ()


class _Declarations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owners: tuple[_OwnerDeclaration, ...] = ()
    entrypoints: tuple[_EntrypointDeclaration, ...] = ()
    registrations: tuple[_RegistrationDeclaration, ...] = ()


class _PackageManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    scripts: dict[str, str] = Field(default_factory=dict[str, str])
    bin: str | dict[str, str] | None = None
    exports: object | None = None
    soleaux: _Declarations = Field(default_factory=_Declarations)


class _ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str | None = None
    scripts: dict[str, str] = Field(default_factory=dict[str, str])
    gui_scripts: dict[str, str] = Field(default_factory=dict[str, str], alias="gui-scripts")
    entry_points: dict[str, dict[str, str]] = Field(
        default_factory=dict[str, dict[str, str]],
        alias="entry-points",
    )


class _PytestManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    testpaths: tuple[str, ...] = ()


class _PytestToolManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ini_options: _PytestManifest = Field(default_factory=_PytestManifest)


class _ToolManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    soleaux: _Declarations = Field(default_factory=_Declarations)
    pytest: _PytestToolManifest = Field(default_factory=_PytestToolManifest)


class _PyprojectManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project: _ProjectManifest = Field(default_factory=_ProjectManifest)
    tool: _ToolManifest = Field(default_factory=_ToolManifest)


class AuthorityResolver:
    """Resolve authority and request-local governance relationships."""

    supported_tables = PRODUCER_SUPPORTED_TABLES[Producer.AUTHORITY]

    def __init__(
        self,
        history_provider: HistoryOwnerProvider | None = None,
        *,
        governance: GovernanceConfig | None = None,
        policy_selectors: Sequence[str] = (),
    ) -> None:
        self._history_provider = history_provider
        self._governance = governance or GovernanceConfig()
        self._policy_selectors = tuple(policy_selectors)
        self._coverage_notes: tuple[str, ...] = ()

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: SnapshotBundle,
        semantic_mode: SemanticMode,
        upstream_tables: Mapping[str, tuple[FactRow, ...]],
    ) -> Mapping[str, tuple[FactRow, ...]]:
        """Adapt one authority parse into the shared table producer boundary."""
        del semantic_mode
        result = await self.resolve(bundle, upstream_tables=upstream_tables)
        self._coverage_notes = result.warnings
        available = {
            "authority.entrypoints": result.entrypoints,
            "authority.owners": result.owners,
            "authority.policies": result.policies,
            "authority.bindings": result.bindings,
            "authority.conflicts": result.conflicts,
        }
        return {name: available[name] for name in table_names if name in available}

    def coverage_notes(self) -> tuple[str, ...]:
        """Report malformed or unresolved recognized governance surfaces."""
        return self._coverage_notes

    async def resolve(
        self,
        bundle: SnapshotBundle,
        *,
        upstream_tables: Mapping[str, tuple[FactRow, ...]] | None = None,
        include_history: bool = False,
        max_history_paths: int = 256,
        max_history_commits: int = 100,
    ) -> AuthorityResult:
        """Parse supported authorities and apply fixed source precedence."""
        owner_claims: list[AuthorityClaim] = []
        entrypoint_claims: list[EntrypointClaim] = []
        warnings: list[str] = []
        all_paths = tuple(sorted(bundle.contents))

        for path in all_paths:
            content = bundle.contents[path]
            if path in _CODEOWNERS_PATHS:
                claims, parser_warnings = self._parse_codeowners(path, content, all_paths)
                owner_claims.extend(claims)
                warnings.extend(parser_warnings)
                continue
            name = PurePosixPath(path).name
            if name == "package.json":
                parsed = self._parse_package_json(path, content, warnings)
                if parsed is not None:
                    entries, claims = self._package_claims(path, parsed)
                    entrypoint_claims.extend(entries)
                    owner_claims.extend(claims)
                continue
            if name == "pyproject.toml":
                parsed_pyproject = self._parse_pyproject(path, content, warnings)
                if parsed_pyproject is not None:
                    entries, claims = self._pyproject_claims(path, parsed_pyproject)
                    entrypoint_claims.extend(entries)
                    owner_claims.extend(claims)
                continue
            if name in _YAML_MANIFEST_NAMES:
                declarations = self._parse_yaml(path, content, warnings)
                if declarations is not None:
                    entries, claims = self._declaration_claims(path, declarations)
                    entrypoint_claims.extend(entries)
                    owner_claims.extend(claims)

        if include_history and self._history_provider is not None:
            historical = await self._history_provider.claims(
                bundle,
                max_paths=max_history_paths,
                max_commits=max_history_commits,
            )
            for claim in historical[:max_history_paths]:
                if (
                    claim.owner_kind is not OwnerKind.HISTORICAL
                    or claim.source_kind is not OwnerSourceKind.GIT_HISTORY
                ):
                    raise ValueError("history provider returned a non-historical authority claim")
                owner_claims.append(claim)

        winners, owner_conflicts = self._resolve_precedence(owner_claims)
        governance = collect_governance_claims(
            bundle,
            upstream_tables or {},
            governance=self._governance,
            policy_selectors=self._policy_selectors,
        )
        warnings.extend(governance.warnings)
        policies = governance.policies
        bindings = governance.bindings
        governance_conflicts = governance.conflicts
        policy_warnings = governance.policy_warnings
        if self._policy_selectors:
            selected_ids = {
                policy_id
                for selector in self._policy_selectors
                for policy_id in policy_ids_for_selector(policies, bindings, selector)
            }
            policies = tuple(policy for policy in policies if policy.policy_id in selected_ids)
            bindings = tuple(binding for binding in bindings if binding.policy_id in selected_ids)
            governance_conflicts = tuple(
                conflict for conflict in governance_conflicts if conflict.policy_id in selected_ids
            )
            policy_warnings = tuple(
                warning for warning in policy_warnings if warning.policy_id in selected_ids
            )
            owner_conflicts = ()
        warnings.extend(warning.message for warning in policy_warnings)
        bindings_by_id = {binding.binding_id: binding for binding in bindings}
        return AuthorityResult(
            entrypoints=tuple(
                self._entrypoint_row(bundle, claim)
                for claim in sorted(
                    entrypoint_claims,
                    key=lambda item: (
                        item.entrypoint_kind.value,
                        item.name,
                        item.target,
                        item.source_path,
                    ),
                )
            ),
            owners=tuple(self._owner_row(bundle, claim) for claim in winners),
            policies=tuple(self._policy_row(bundle, policy) for policy in policies),
            bindings=tuple(self._binding_row(bundle, binding) for binding in bindings),
            conflicts=(
                *tuple(
                    self._binding_conflict_row(
                        bundle,
                        conflict,
                        bindings_by_id[conflict.binding_id],
                    )
                    for conflict in governance_conflicts
                    if conflict.binding_id in bindings_by_id
                ),
                *tuple(self._owner_conflict_row(bundle, conflict) for conflict in owner_conflicts),
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _parse_package_json(
        path: str,
        content: bytes,
        warnings: list[str],
    ) -> _PackageManifest | None:
        try:
            raw: object = json.loads(content)
        except json.JSONDecodeError:
            warnings.append(f"{path}: invalid JSON manifest")
            return None
        try:
            return _PackageManifest.model_validate(raw)
        except ValidationError:
            warnings.append(f"{path}: invalid package manifest")
            return None

    @staticmethod
    def _parse_pyproject(
        path: str,
        content: bytes,
        warnings: list[str],
    ) -> _PyprojectManifest | None:
        try:
            raw = tomllib.loads(content.decode("utf-8"))
            return _PyprojectManifest.model_validate(raw)
        except UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError:
            warnings.append(f"{path}: invalid TOML manifest")
            return None

    @staticmethod
    def _parse_yaml(
        path: str,
        content: bytes,
        warnings: list[str],
    ) -> _Declarations | None:
        try:
            raw: object = yaml.safe_load(content.decode("utf-8"))
            return _Declarations.model_validate(raw)
        except UnicodeDecodeError, yaml.YAMLError, ValidationError:
            warnings.append(f"{path}: invalid YAML authority manifest")
            return None

    def _package_claims(
        self,
        path: str,
        manifest: _PackageManifest,
    ) -> tuple[list[EntrypointClaim], list[AuthorityClaim]]:
        entries: list[EntrypointClaim] = []
        if manifest.name:
            entries.append(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.PACKAGE,
                    name=manifest.name,
                    target=path,
                    source_path=path,
                )
            )
        for name, target in sorted(manifest.scripts.items()):
            kind = (
                EntrypointKind.TEST
                if name == "test" or name.startswith("test:")
                else EntrypointKind.SCRIPT
            )
            entries.append(
                EntrypointClaim(
                    entrypoint_kind=kind,
                    name=name,
                    target=target,
                    source_path=path,
                )
            )
        if isinstance(manifest.bin, str):
            entries.append(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.EXECUTABLE,
                    name=manifest.name or "bin",
                    target=self._manifest_target(path, manifest.bin),
                    source_path=path,
                )
            )
        elif isinstance(manifest.bin, dict):
            entries.extend(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.EXECUTABLE,
                    name=name,
                    target=self._manifest_target(path, target),
                    source_path=path,
                )
                for name, target in sorted(manifest.bin.items())
            )
        entries.extend(self._package_exports(path, manifest.name, manifest.exports))
        declared_entries, claims = self._declaration_claims(path, manifest.soleaux)
        entries.extend(declared_entries)
        return entries, claims

    def _pyproject_claims(
        self,
        path: str,
        manifest: _PyprojectManifest,
    ) -> tuple[list[EntrypointClaim], list[AuthorityClaim]]:
        entries: list[EntrypointClaim] = []
        if manifest.project.name:
            entries.append(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.PACKAGE,
                    name=manifest.project.name,
                    target=path,
                    source_path=path,
                )
            )
        script_groups = (manifest.project.scripts, manifest.project.gui_scripts)
        for scripts in script_groups:
            entries.extend(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.SCRIPT,
                    name=name,
                    target=target,
                    source_path=path,
                )
                for name, target in sorted(scripts.items())
            )
        for group, group_entries in sorted(manifest.project.entry_points.items()):
            entries.extend(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.PLUGIN,
                    name=f"{group}:{name}",
                    target=target,
                    source_path=path,
                )
                for name, target in sorted(group_entries.items())
            )
        entries.extend(
            EntrypointClaim(
                entrypoint_kind=EntrypointKind.TEST,
                name=test_path,
                target=test_path,
                source_path=path,
            )
            for test_path in sorted(manifest.tool.pytest.ini_options.testpaths)
        )
        declared_entries, claims = self._declaration_claims(path, manifest.tool.soleaux)
        entries.extend(declared_entries)
        return entries, claims

    def _declaration_claims(
        self,
        path: str,
        declarations: _Declarations,
    ) -> tuple[list[EntrypointClaim], list[AuthorityClaim]]:
        entries = [
            EntrypointClaim(
                entrypoint_kind=entry.kind,
                name=entry.name,
                target=self._manifest_target(path, entry.target),
                source_path=path,
            )
            for entry in declarations.entrypoints
        ]
        claims = [
            AuthorityClaim(
                target=self._manifest_target(path, declaration.target),
                owners=declaration.owners,
                owner_kind=declaration.kind,
                source_kind=self._source_kind_for_owner(declaration.kind),
                source_path=path,
            )
            for declaration in declarations.owners
        ]
        for registration in declarations.registrations:
            target = self._manifest_target(path, registration.target)
            entries.append(
                EntrypointClaim(
                    entrypoint_kind=registration.kind,
                    name=registration.name,
                    target=target,
                    source_path=path,
                )
            )
            if registration.owners:
                claims.append(
                    AuthorityClaim(
                        target=target,
                        owners=registration.owners,
                        owner_kind=OwnerKind.RUNTIME_REGISTRATION,
                        source_kind=OwnerSourceKind.MANIFEST_POLICY_GENERATOR,
                        source_path=path,
                    )
                )
        return entries, claims

    @staticmethod
    def _source_kind_for_owner(kind: OwnerKind) -> OwnerSourceKind:
        if kind is OwnerKind.CANONICAL:
            return OwnerSourceKind.CANONICAL_RELATIONSHIP
        if kind is OwnerKind.HISTORICAL:
            raise ValueError("historical ownership must come from the opt-in history provider")
        return OwnerSourceKind.MANIFEST_POLICY_GENERATOR

    @staticmethod
    def _manifest_target(source_path: str, target: str) -> str:
        normalized = target.replace("\\", "/")
        if ":" in normalized and not normalized.startswith("./"):
            return normalized
        if normalized.startswith("./"):
            parent = PurePosixPath(source_path).parent
            return str(parent / normalized.removeprefix("./"))
        return normalized

    @staticmethod
    def _package_exports(
        path: str,
        package_name: str | None,
        exports: object | None,
    ) -> list[EntrypointClaim]:
        if exports is None:
            return []
        if isinstance(exports, str):
            return [
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.PACKAGE,
                    name=package_name or ".",
                    target=AuthorityResolver._manifest_target(path, exports),
                    source_path=path,
                )
            ]
        if not _is_object_dict(exports):
            return []
        rows: list[EntrypointClaim] = []
        export_items = exports.items()
        for raw_name, raw_target in sorted(export_items, key=lambda item: str(item[0])):
            if not isinstance(raw_name, str) or not isinstance(raw_target, str):
                continue
            export_name = package_name or "."
            if raw_name != ".":
                export_name = f"{export_name}{raw_name.removeprefix('.')}"
            rows.append(
                EntrypointClaim(
                    entrypoint_kind=EntrypointKind.PACKAGE,
                    name=export_name,
                    target=AuthorityResolver._manifest_target(path, raw_target),
                    source_path=path,
                )
            )
        return rows

    @staticmethod
    def _parse_codeowners(
        source_path: str,
        content: bytes,
        all_paths: tuple[str, ...],
    ) -> tuple[list[AuthorityClaim], list[str]]:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return [], [f"{source_path}: invalid UTF-8 CODEOWNERS"]
        rules: list[tuple[int, RepositoryPattern, tuple[str, ...]]] = []
        warnings: list[str] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2 or parts[0].startswith("!"):
                warnings.append(f"{source_path}:{line_number}: unsupported CODEOWNERS rule")
                continue
            rules.append((line_number, RepositoryPattern.parse(parts[0]), tuple(parts[1:])))
        claims: list[AuthorityClaim] = []
        for target in all_paths:
            matching = [rule for rule in rules if rule[1].matches(target)]
            if not matching:
                continue
            line_number, _pattern, owners = matching[-1]
            claims.append(
                AuthorityClaim(
                    target=target,
                    owners=owners,
                    owner_kind=OwnerKind.POLICY,
                    source_kind=OwnerSourceKind.EXPLICIT_GOVERNANCE,
                    source_path=source_path,
                    source_line=line_number,
                )
            )
        return claims, warnings

    @staticmethod
    def _resolve_precedence(
        claims: list[AuthorityClaim],
    ) -> tuple[tuple[AuthorityClaim, ...], tuple[_OwnerConflictEvidence, ...]]:
        by_target: defaultdict[str, list[AuthorityClaim]] = defaultdict(list)
        for claim in claims:
            by_target[claim.target].append(claim)
        winners: list[AuthorityClaim] = []
        conflicts: list[_OwnerConflictEvidence] = []
        for target, target_claims in sorted(by_target.items()):
            highest = min(claim.source_kind.precedence for claim in target_claims)
            top_tier = [claim for claim in target_claims if claim.source_kind.precedence == highest]
            owner_sets = {claim.owners for claim in top_tier}
            if len(owner_sets) > 1:
                conflicts.extend(
                    AuthorityResolver._owner_conflicts(
                        target,
                        target_claims,
                        top_tier=top_tier,
                        reason="same-tier owner claims disagree; no effective winner",
                        kind="same-tier",
                    )
                )
                continue
            winner = min(
                top_tier,
                key=lambda claim: (
                    claim.source_path,
                    claim.source_line,
                    claim.owner_kind.value,
                ),
            )
            winners.append(winner)
            contradictory = [
                claim
                for claim in target_claims
                if claim.source_kind.precedence > highest and claim.owners != winner.owners
            ]
            if contradictory:
                conflicts.extend(
                    AuthorityResolver._owner_conflicts(
                        target,
                        (winner, *contradictory),
                        top_tier=(winner,),
                        reason="lower-tier owner claim contradicts the effective owner",
                        kind="shadowed",
                    )
                )
        return tuple(winners), tuple(conflicts)

    @staticmethod
    def _owner_conflicts(
        target: str,
        claims: Sequence[AuthorityClaim],
        *,
        top_tier: Sequence[AuthorityClaim],
        reason: str,
        kind: str,
    ) -> tuple[_OwnerConflictEvidence, ...]:
        binding_ids = {id(claim): AuthorityResolver._owner_claim_id(claim) for claim in claims}
        conflict_payload = json.dumps(
            {
                "kind": kind,
                "target": target,
                "binding_ids": sorted(binding_ids.values()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        conflict_id = f"conflict:{content_digest(conflict_payload)[:24]}"
        top_ids = {id(claim) for claim in top_tier}
        return tuple(
            _OwnerConflictEvidence(
                conflict=PolicyConflictClaim(
                    conflict_id=conflict_id,
                    policy_id=f"ownership:{target}",
                    role=None,
                    role_label=None,
                    binding_id=binding_ids[id(claim)],
                    competing_binding_ids=tuple(
                        sorted(
                            binding_id
                            for claim_id, binding_id in binding_ids.items()
                            if claim_id != id(claim)
                        )
                    ),
                    state=(
                        GovernanceState.CONFLICTING
                        if id(claim) in top_ids and kind == "same-tier"
                        else GovernanceState.EFFECTIVE
                        if id(claim) in top_ids
                        else GovernanceState.SHADOWED
                    ),
                    reason=reason,
                    source_path=claim.source_path,
                    source_line=claim.source_line,
                ),
                claim=claim,
            )
            for claim in claims
        )

    @staticmethod
    def _owner_claim_id(claim: AuthorityClaim) -> str:
        payload = json.dumps(
            claim.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"binding:{content_digest(payload)[:24]}"

    @staticmethod
    def _entrypoint_row(bundle: SnapshotBundle, claim: EntrypointClaim) -> FactRow:
        data = {
            "entrypoint_kind": claim.entrypoint_kind.value,
            "name": claim.name,
            "target": claim.target,
            "source_path": claim.source_path,
        }
        return FactRow(
            table="authority.entrypoints",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=claim.source_path,
                table="authority.entrypoints",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=Authority.MANIFEST,
                provider="manifest-authority",
                provider_version="1",
                start_line=claim.source_line,
                end_line=claim.source_line,
            ),
        )

    @staticmethod
    def _owner_row(bundle: SnapshotBundle, claim: AuthorityClaim) -> FactRow:
        authority = {
            OwnerSourceKind.EXPLICIT_GOVERNANCE: Authority.GOVERNANCE,
            OwnerSourceKind.MANIFEST_POLICY_GENERATOR: (
                Authority.GENERATED
                if claim.owner_kind is OwnerKind.GENERATOR
                else Authority.MANIFEST
            ),
            OwnerSourceKind.CANONICAL_RELATIONSHIP: Authority.SOURCE,
            OwnerSourceKind.GIT_HISTORY: Authority.INFERRED,
        }[claim.source_kind]
        data = {
            "target": claim.target,
            "owners": claim.owners,
            "owner_kind": claim.owner_kind.value,
            "source_kind": claim.source_kind.value,
            "precedence": claim.source_kind.precedence,
            "source_path": claim.source_path,
        }
        return FactRow(
            table="authority.owners",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=claim.source_path,
                table="authority.owners",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=authority,
                provider="authority-resolver",
                provider_version="1",
                start_line=claim.source_line,
                end_line=claim.source_line,
            ),
        )

    @staticmethod
    def _policy_row(bundle: SnapshotBundle, claim: PolicyClaim) -> FactRow:
        data = {
            "policy_id": claim.policy_id,
            "governance_source_id": claim.governance_source_id,
            "title": claim.title,
            "aliases": claim.aliases,
            "scope": claim.scope,
            "required_roles": claim.required_roles,
            "source_heading": claim.source_heading,
            "identity_field": claim.identity_field,
            "identity_value": claim.identity_value,
            "vocabulary": claim.vocabulary,
            "attributes": claim.attributes,
            "canonicality_basis": claim.canonicality_basis,
            "canonicality_score": claim.canonicality_score,
            "basis": claim.basis.value,
            "source_kind": claim.source_kind.value,
            "precedence": claim.source_kind.precedence,
            "source_path": claim.source_path,
        }
        return FactRow(
            table="authority.policies",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=claim.source_path,
                table="authority.policies",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=AuthorityResolver._evidence_authority(claim.source_kind),
                provider="governance-authority",
                provider_version="1",
                start_line=claim.source_line,
                end_line=claim.source_line,
            ),
        )

    @staticmethod
    def _binding_row(bundle: SnapshotBundle, claim: PolicyBindingClaim) -> FactRow:
        data = {
            "binding_id": claim.binding_id,
            "policy_id": claim.policy_id,
            "binding_kind": claim.binding_kind.value,
            "role": claim.role,
            "role_label": claim.role_label,
            "target": claim.target,
            "target_kind": claim.target_kind.value,
            "relationship": claim.relationship,
            "attributes": claim.attributes,
            "basis": claim.basis.value,
            "state": claim.state.value,
            "source_kind": claim.source_kind.value,
            "precedence": claim.source_kind.precedence,
            "source_path": claim.source_path,
        }
        resolution_status = {
            GovernanceState.MISSING_TARGET: ResolutionStatus.UNRESOLVED,
            GovernanceState.UNVERIFIED: ResolutionStatus.CANDIDATE,
        }.get(claim.state, ResolutionStatus.RESOLVED)
        return FactRow(
            table="authority.bindings",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=claim.source_path,
                table="authority.bindings",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=resolution_status,
                authority=AuthorityResolver._evidence_authority(claim.source_kind),
                provider="governance-authority",
                provider_version="1",
                confidence=0.7 if claim.basis is ClaimBasis.INFERRED else 1.0,
                start_line=claim.source_line,
                end_line=claim.source_line,
            ),
        )

    @staticmethod
    def _binding_conflict_row(
        bundle: SnapshotBundle,
        conflict: PolicyConflictClaim,
        binding: PolicyBindingClaim,
    ) -> FactRow:
        data = {
            "conflict_id": conflict.conflict_id,
            "policy_id": conflict.policy_id,
            "role": conflict.role,
            "role_label": conflict.role_label,
            "binding_id": conflict.binding_id,
            "competing_binding_ids": conflict.competing_binding_ids,
            "state": conflict.state.value,
            "reason": conflict.reason,
            "target": binding.target,
            "target_kind": binding.target_kind.value,
            "relationship": binding.relationship,
            "binding_kind": binding.binding_kind.value,
            "attributes": binding.attributes,
            "basis": binding.basis.value,
            "source_kind": binding.source_kind.value,
            "precedence": binding.source_kind.precedence,
            "source_path": conflict.source_path,
        }
        return FactRow(
            table="authority.conflicts",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=conflict.source_path,
                table="authority.conflicts",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=AuthorityResolver._evidence_authority(binding.source_kind),
                provider="governance-authority",
                provider_version="1",
                start_line=conflict.source_line,
                end_line=conflict.source_line,
            ),
        )

    @staticmethod
    def _owner_conflict_row(
        bundle: SnapshotBundle,
        evidence: _OwnerConflictEvidence,
    ) -> FactRow:
        conflict = evidence.conflict
        claim = evidence.claim
        data = {
            "conflict_id": conflict.conflict_id,
            "policy_id": conflict.policy_id,
            "role": conflict.role,
            "role_label": conflict.role_label,
            "binding_id": conflict.binding_id,
            "competing_binding_ids": conflict.competing_binding_ids,
            "state": conflict.state.value,
            "reason": conflict.reason,
            "target": claim.target,
            "owners": claim.owners,
            "target_kind": "path",
            "relationship": "declared",
            "basis": ClaimBasis.DECLARED.value,
            "source_kind": claim.source_kind.value,
            "precedence": claim.source_kind.precedence,
            "source_path": claim.source_path,
        }
        return FactRow(
            table="authority.conflicts",
            data=data,
            evidence=evidence_for_path(
                bundle,
                path=claim.source_path,
                table="authority.conflicts",
                data=data,
                evidence_kind=EvidenceKind.METADATA,
                resolution_status=ResolutionStatus.RESOLVED,
                authority=AuthorityResolver._evidence_authority(claim.source_kind),
                provider="authority-resolver",
                provider_version="1",
                start_line=claim.source_line,
                end_line=claim.source_line,
            ),
        )

    @staticmethod
    def _evidence_authority(source_kind: OwnerSourceKind) -> Authority:
        return {
            OwnerSourceKind.EXPLICIT_GOVERNANCE: Authority.GOVERNANCE,
            OwnerSourceKind.MANIFEST_POLICY_GENERATOR: Authority.MANIFEST,
            OwnerSourceKind.CANONICAL_RELATIONSHIP: Authority.SOURCE,
            OwnerSourceKind.GIT_HISTORY: Authority.INFERRED,
        }[source_kind]
