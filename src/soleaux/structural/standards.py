"""One configured structural-standards scan shared by query and lint adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from soleaux.contracts.config import StructuralConfig
from soleaux.contracts.evidence import Authority, EvidenceKind, ResolutionStatus
from soleaux.contracts.frame import FactRow
from soleaux.structural.engines import (
    ResolvedMatcher,
    StructuralEngineError,
    StructuralEngines,
)
from soleaux.structural.path_patterns import RepositoryPattern
from soleaux.structural.snapshot import SnapshotBundle
from soleaux.structural.workspace_rules import WorkspaceRule, load_workspace_rules
from soleaux.tables.evidence import evidence_for_path


@dataclass(frozen=True)
class StandardsScanResult:
    """Bounded findings plus enough state to classify request coverage."""

    rows: tuple[FactRow, ...]
    rule_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    configured: bool
    available: bool
    truncated: bool


class WorkspaceStandardsAnalyzer:
    """Evaluate the configured project rules through the selected engine."""

    def __init__(
        self,
        *,
        root: Path,
        config: StructuralConfig,
        engines: StructuralEngines,
    ) -> None:
        self._root = root
        self._config = config
        self._engines = engines

    async def scan(
        self,
        bundle: SnapshotBundle,
        *,
        rule_ids: tuple[str, ...] = (),
        severities: tuple[str, ...] = (),
        path_prefixes: tuple[str, ...] = (),
        limit: int = 1000,
        fail_on_unknown_rule: bool = False,
    ) -> StandardsScanResult:
        project_config = self._config.project_config
        if project_config is None:
            return StandardsScanResult(
                rows=(),
                rule_ids=(),
                warnings=("quality.standards: [structural].project_config is not configured",),
                configured=False,
                available=False,
                truncated=False,
            )

        rules, load_warnings = load_workspace_rules(self._root, project_config)
        unknown = sorted(set(rule_ids) - set(rules))
        if unknown and fail_on_unknown_rule:
            raise StructuralEngineError(
                "unknown_rule",
                f"unconfigured rule ids: {unknown}",
            )
        selected = tuple(
            rule
            for rule in sorted(rules.values(), key=lambda item: item.rule_id)
            if (not rule_ids or rule.rule_id in rule_ids)
            and (not severities or rule.severity in severities)
        )
        warnings = list(load_warnings)
        if unknown:
            warnings.append(f"unconfigured rule ids: {unknown}")
        if not selected:
            reason = "quality.standards: configured project contains no usable selected rules"
            return StandardsScanResult(
                rows=(),
                rule_ids=(),
                warnings=tuple(dict.fromkeys((*warnings, reason))),
                configured=True,
                available=False,
                truncated=False,
            )

        rows: list[FactRow] = []
        completed_rules = 0
        truncated = False
        for rule in selected:
            if len(rows) >= limit:
                truncated = True
                break
            files = self._files_for_rule(
                bundle,
                rule,
                path_prefixes=path_prefixes,
            )
            if not files:
                completed_rules += 1
                continue
            try:
                outcome = await self._engines.run(
                    ResolvedMatcher(
                        language=rule.language,
                        matcher={
                            "kind": "rule",
                            "rule": rule.rule,
                            "constraints": rule.constraints,
                            "utils": rule.utils,
                        },
                        fix=None,
                        transforms=None,
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        message=rule.message,
                    ),
                    files=files,
                    want=("findings",),
                    limits={"max_findings": max(1, limit - len(rows))},
                )
            except StructuralEngineError as exc:
                warnings.append(f"{rule.rule_id}: {exc.error_type}: {exc.message}")
                continue
            completed_rules += 1
            warnings.extend(f"{rule.rule_id}: {error}" for error in outcome.errors)
            truncated = truncated or outcome.truncated
            for finding in outcome.findings:
                data = {
                    "rule_id": rule.rule_id,
                    "severity": rule.severity,
                    "message": rule.message,
                    "path": finding.path,
                    "line": finding.start_line + 1,
                    "column": finding.start_column + 1,
                    "end_line": finding.end_line + 1,
                    "end_column": finding.end_column + 1,
                    "preview": finding.text_preview,
                    "engine": finding.engine.value,
                    "rule_source": rule.source_path,
                    "rule_digest": rule.digest,
                }
                rows.append(
                    FactRow(
                        table="quality.standards",
                        data=data,
                        evidence=evidence_for_path(
                            bundle,
                            path=finding.path,
                            table="quality.standards",
                            data=data,
                            evidence_kind=EvidenceKind.STRUCTURAL,
                            resolution_status=ResolutionStatus.RESOLVED,
                            authority=Authority.SOURCE,
                            provider=f"structural:{outcome.engine.value}",
                            provider_version=outcome.engine_version,
                            start_line=finding.start_line + 1,
                            start_column=finding.start_column + 1,
                            end_line=finding.end_line + 1,
                            end_column=finding.end_column + 1,
                            byte_start=finding.byte_start,
                            byte_end=finding.byte_end,
                        ),
                    )
                )
                if len(rows) >= limit:
                    truncated = True
                    break

        if truncated:
            warnings.append(f"quality.standards: row limit {limit} reached")
        return StandardsScanResult(
            rows=tuple(
                sorted(
                    rows,
                    key=lambda row: (
                        str(row.data.get("rule_id", "")),
                        str(row.data.get("path", "")),
                        int(row.evidence.range.byte_start or 0),
                        int(row.evidence.range.byte_end or 0),
                    ),
                )
            ),
            rule_ids=tuple(rule.rule_id for rule in selected),
            warnings=tuple(dict.fromkeys(warnings)),
            configured=True,
            available=completed_rules > 0,
            truncated=truncated,
        )

    @staticmethod
    def _files_for_rule(
        bundle: SnapshotBundle,
        rule: WorkspaceRule,
        *,
        path_prefixes: tuple[str, ...],
    ) -> tuple[tuple[str, bytes], ...]:
        includes = tuple(RepositoryPattern.parse(pattern) for pattern in rule.files)
        ignores = tuple(RepositoryPattern.parse(pattern) for pattern in rule.ignores)
        return tuple(
            (captured.path, bundle.contents[captured.path])
            for captured in sorted(bundle.snapshot.files, key=lambda item: item.path)
            if captured.path in bundle.contents
            and captured.language is not None
            and captured.language.casefold() == rule.language.casefold()
            and (
                not path_prefixes
                or any(
                    captured.path == prefix or captured.path.startswith(f"{prefix.rstrip('/')}/")
                    for prefix in path_prefixes
                )
            )
            and (not includes or any(pattern.matches(captured.path) for pattern in includes))
            and not any(pattern.matches(captured.path) for pattern in ignores)
        )
