"""Typed authority and governance claims with fixed precedence."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from soleaux.contracts.frame import FactRow
from soleaux.contracts.governance import (
    GovernanceBindingKind,
    GovernanceTargetKind,
    governance_identifier,
)
from soleaux.structural.snapshot import SnapshotBundle


class OwnerKind(StrEnum):
    """What relationship makes an owner relevant."""

    CANONICAL = "canonical"
    GENERATOR = "generator"
    POLICY = "policy"
    RUNTIME_REGISTRATION = "runtime_registration"
    HISTORICAL = "historical"


class OwnerSourceKind(StrEnum):
    """The fixed precedence tier that established an owner claim."""

    EXPLICIT_GOVERNANCE = "explicit_governance"
    MANIFEST_POLICY_GENERATOR = "manifest_policy_generator"
    CANONICAL_RELATIONSHIP = "canonical_relationship"
    GIT_HISTORY = "git_history"

    @property
    def precedence(self) -> int:
        """Lower numbers outrank higher numbers."""
        return {
            OwnerSourceKind.EXPLICIT_GOVERNANCE: 1,
            OwnerSourceKind.MANIFEST_POLICY_GENERATOR: 2,
            OwnerSourceKind.CANONICAL_RELATIONSHIP: 3,
            OwnerSourceKind.GIT_HISTORY: 4,
        }[self]


class EntrypointKind(StrEnum):
    """Declared application/runtime root categories."""

    APPLICATION = "application"
    PACKAGE = "package"
    SCRIPT = "script"
    TEST = "test"
    ROUTE = "route"
    JOB = "job"
    SERVICE = "service"
    HANDLER = "handler"
    PLUGIN = "plugin"
    EXECUTABLE = "executable"


class ClaimBasis(StrEnum):
    """How Soleaux obtained one governance relationship."""

    DECLARED = "declared"
    OBSERVED = "observed"
    RESOLVED = "resolved"
    INFERRED = "inferred"


class GovernanceState(StrEnum):
    """Resolution state for one policy relationship claim."""

    EFFECTIVE = "effective"
    SHADOWED = "shadowed"
    CONFLICTING = "conflicting"
    MISSING_TARGET = "missing-target"
    UNVERIFIED = "unverified"


class OwnershipDecisionState(StrEnum):
    """Top-level outcome from one ownership explanation."""

    RESOLVED = "resolved"
    CONFLICTED = "conflicted"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


def _relative_target(value: str) -> str:
    if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
        raise ValueError("authority targets must be workspace-relative")
    return value


class AuthorityClaim(BaseModel):
    """One source-traceable owner assertion before precedence resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(min_length=1)
    owners: tuple[str, ...] = Field(min_length=1)
    owner_kind: OwnerKind
    source_kind: OwnerSourceKind
    source_path: str = Field(min_length=1)
    source_line: int = Field(default=1, ge=1)

    _validate_target = field_validator("target")(_relative_target)
    _validate_source_path = field_validator("source_path")(_relative_target)

    @field_validator("owners")
    @classmethod
    def _normalize_owners(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({owner.strip() for owner in value if owner.strip()}))
        if not normalized:
            raise ValueError("authority claims require at least one non-empty owner")
        return normalized


class EntrypointClaim(BaseModel):
    """One declared package/application/runtime root."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entrypoint_kind: EntrypointKind
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_line: int = Field(default=1, ge=1)

    _validate_source_path = field_validator("source_path")(_relative_target)


class PolicyClaim(BaseModel):
    """One source-traceable record from a canonical consumer vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1, max_length=256)
    governance_source_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=512)
    aliases: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    source_heading: str | None = Field(default=None, min_length=1, max_length=512)
    identity_field: str = Field(min_length=1, max_length=256)
    identity_value: str = Field(min_length=1, max_length=1024)
    vocabulary: tuple[str, ...] = Field(min_length=1)
    attributes: dict[str, object] = Field(default_factory=dict)
    canonicality_basis: tuple[str, ...] = Field(min_length=1)
    canonicality_score: int = Field(ge=1)
    source_kind: OwnerSourceKind
    source_path: str = Field(min_length=1)
    source_line: int = Field(default=1, ge=1)
    basis: ClaimBasis = ClaimBasis.DECLARED

    _validate_source_path = field_validator("source_path")(_relative_target)

    @field_validator("aliases", "scope")
    @classmethod
    def _normalize_strings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("required_roles")
    @classmethod
    def _normalize_identifiers(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    governance_identifier(
                        identifier,
                        field_name=info.field_name or "required_roles",
                    )
                    for identifier in value
                }
            )
        )


class PolicyBindingClaim(BaseModel):
    """One source-traceable edge in the request-local governance graph."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=256)
    binding_kind: GovernanceBindingKind = GovernanceBindingKind.DECLARED
    role: str | None = Field(default=None, min_length=1, max_length=128)
    role_label: str | None = Field(default=None, min_length=1, max_length=256)
    target: str = Field(min_length=1, max_length=4096)
    target_kind: GovernanceTargetKind = GovernanceTargetKind.AUTO
    relationship: str = Field(default="declared", min_length=1, max_length=128)
    attributes: dict[str, object] = Field(default_factory=dict)
    basis: ClaimBasis
    state: GovernanceState
    source_kind: OwnerSourceKind
    source_path: str = Field(min_length=1)
    source_line: int = Field(default=1, ge=1)
    _validate_source_path = field_validator("source_path")(_relative_target)

    @field_validator("relationship", "role")
    @classmethod
    def _validate_identifiers(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        return governance_identifier(value, field_name=info.field_name or "binding identifier")

    @model_validator(mode="after")
    def _validate_binding_kind(self) -> Self:
        if self.binding_kind is GovernanceBindingKind.DECLARED:
            if self.role is None or self.role_label is None:
                raise ValueError(
                    "declared governance bindings require the normalized and authored roles"
                )
        elif self.role is not None or self.role_label is not None:
            raise ValueError("neutral evidence relationships must not claim a policy role")
        return self


class PolicyConflictClaim(BaseModel):
    """One competing claim retained under a stable conflict identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conflict_id: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=256)
    role: str | None = Field(default=None, min_length=1, max_length=128)
    role_label: str | None = Field(default=None, min_length=1, max_length=256)
    binding_id: str = Field(min_length=1, max_length=128)
    competing_binding_ids: tuple[str, ...] = Field(min_length=1)
    state: GovernanceState
    reason: str = Field(min_length=1, max_length=512)
    source_path: str = Field(min_length=1)
    source_line: int = Field(default=1, ge=1)

    _validate_source_path = field_validator("source_path")(_relative_target)


class AuthorityResult(BaseModel):
    """Authority table rows plus honest parser limitations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entrypoints: tuple[FactRow, ...] = ()
    owners: tuple[FactRow, ...] = ()
    policies: tuple[FactRow, ...] = ()
    bindings: tuple[FactRow, ...] = ()
    conflicts: tuple[FactRow, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class HistoryOwnerProvider(Protocol):
    """Optional bounded history source; no default implementation invokes Git."""

    async def claims(
        self,
        bundle: SnapshotBundle,
        *,
        max_paths: int,
        max_commits: int,
    ) -> tuple[AuthorityClaim, ...]:
        """Return historical-only claims within explicit caller limits."""
        ...
