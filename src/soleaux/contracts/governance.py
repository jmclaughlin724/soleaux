"""Consumer-neutral governance identifiers shared by public contracts."""

import enum


class GovernanceBindingKind(enum.StrEnum):
    """Whether a graph edge is a policy claim or neutral traced evidence."""

    DECLARED = "declared"
    EVIDENCE = "evidence"


class GovernanceTargetKind(enum.StrEnum):
    """How Soleaux should resolve a declared relationship target."""

    AUTO = "auto"
    PATH = "path"
    GLOB = "glob"
    REFERENCE = "reference"


def normalize_governance_identity(value: str) -> str:
    """Normalize one consumer-authored identity for exact alias matching."""
    return " ".join(value.casefold().replace("_", " ").replace("-", " ").split())


def governance_identifier(value: str, *, field_name: str) -> str:
    """Validate one open identifier used by a consumer-authored projection."""
    normalized = value.strip().lower()
    if not _is_governance_identifier(normalized):
        raise ValueError(
            f"{field_name} must be a lowercase governance identifier "
            "using letters, numbers, '-', '_', '.', or ':'"
        )
    return normalized


def _is_governance_identifier(value: str) -> bool:
    if not value or not ("a" <= value[0] <= "z"):
        return False
    previous_was_separator = False
    for character in value[1:]:
        if ("a" <= character <= "z") or ("0" <= character <= "9"):
            previous_was_separator = False
            continue
        if character not in "-_.:" or previous_was_separator:
            return False
        previous_was_separator = True
    return not previous_was_separator
