"""Resolve structural import and call candidates at one frozen generation."""

from __future__ import annotations

import collections.abc
import hashlib
import json
import pathlib
import typing

import pydantic

import soleaux.contracts.coverage
import soleaux.contracts.evidence
import soleaux.contracts.frame
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.workspace
import soleaux.lsp.broker
import soleaux.lsp.contracts
import soleaux.lsp.operations
import soleaux.lsp.resolvers
import soleaux.relations.modules
import soleaux.structural.fragments
import soleaux.structural.snapshot
import soleaux.tables.evidence

RELATION_PROVIDER_VERSION = "1"
_CONTROL_NAMES = frozenset(
    {"go.mod", "jsconfig.json", "package.json", "pyproject.toml", "tsconfig.json"}
)


class RelationResult(pydantic.BaseModel):
    """Resolved semantic tables for one request-scoped candidate set."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    imports: tuple[soleaux.contracts.frame.FactRow, ...] = ()
    calls: tuple[soleaux.contracts.frame.FactRow, ...] = ()
    warnings: tuple[str, ...] = ()


class RelationResolver:
    """Promote only exact module and symbol resolutions into semantic edges."""

    supported_tables = frozenset({"semantic.imports", "semantic.calls"})

    def __init__(
        self,
        *,
        import_candidates: collections.abc.Sequence[
            soleaux.structural.fragments.SyntaxFragment
        ] = (),
        call_candidates: collections.abc.Sequence[soleaux.structural.fragments.SyntaxFragment] = (),
        module_resolver: soleaux.lsp.resolvers.ModuleResolver | None = None,
        symbol_resolver: soleaux.lsp.resolvers.SymbolResolver | None = None,
    ) -> None:
        self._import_candidates = tuple(import_candidates)
        self._call_candidates = tuple(call_candidates)
        self._module_resolver = module_resolver
        self._symbol_resolver = symbol_resolver

    async def resolve(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
    ) -> RelationResult:
        """Resolve both relation tables without retaining request state."""
        imports = await self.resolve_imports(bundle, semantic_mode)
        calls = await self.resolve_calls(bundle, semantic_mode)
        return RelationResult(imports=imports, calls=calls)

    async def produce(
        self,
        table_names: tuple[str, ...],
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
        upstream_tables: collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]],
    ) -> collections.abc.Mapping[str, tuple[soleaux.contracts.frame.FactRow, ...]]:
        """Adapt selected relation tables into the shared producer boundary."""
        del upstream_tables
        output: dict[str, tuple[soleaux.contracts.frame.FactRow, ...]] = {}
        if "semantic.imports" in table_names:
            output["semantic.imports"] = await self.resolve_imports(bundle, semantic_mode)
        if "semantic.calls" in table_names:
            output["semantic.calls"] = await self.resolve_calls(bundle, semantic_mode)
        return output

    async def resolve_imports(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
    ) -> tuple[soleaux.contracts.frame.FactRow, ...]:
        """Resolve import candidates through the package-owned ModuleResolver."""
        resolver = self._module_resolver or soleaux.relations.modules.SnapshotModuleResolver(bundle)
        rows: list[soleaux.contracts.frame.FactRow] = []
        for fragment in self._ordered(self._import_candidates, "syntax.imports"):
            aliases = self._string_tuple(fragment.attributes.get("aliases"))
            dynamic = fragment.name is None or self._attribute_bool(fragment, "dynamic")
            if semantic_mode is soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY:
                rows.append(
                    self._candidate_row(
                        bundle,
                        fragment,
                        table="semantic.imports",
                        data=self._import_data(
                            fragment,
                            target_path=None,
                            aliases=aliases,
                            dynamic=dynamic,
                            external=not dynamic,
                            generation_fingerprint=None,
                        ),
                        reason="semantic_mode=syntax_only",
                    )
                )
                continue
            if dynamic:
                rows.append(
                    self._candidate_row(
                        bundle,
                        fragment,
                        table="semantic.imports",
                        data=self._import_data(
                            fragment,
                            target_path=None,
                            aliases=aliases,
                            dynamic=True,
                            external=False,
                            generation_fingerprint=None,
                        ),
                        reason="dynamic import has no stable module specifier",
                    )
                )
                continue

            specifier = fragment.name
            if specifier is None:
                raise AssertionError("non-dynamic import must have a specifier")
            generation = soleaux.relations.modules.module_generation(bundle, fragment.path)
            resolution = await resolver.resolve_module(
                source_path=fragment.path,
                specifier=specifier,
                generation=generation,
            )
            if (
                semantic_mode is soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED
                and not resolution.complete
            ):
                reason = "; ".join(resolution.omitted_reasons) or "module was not resolved"
                raise soleaux.lsp.broker.SemanticProviderRequiredError(
                    f"semantic_provider_required: {reason}"
                )
            data = self._import_data(
                fragment,
                target_path=resolution.target_path,
                aliases=aliases,
                dynamic=False,
                external=resolution.target_path is None,
                generation_fingerprint=resolution.generation_fingerprint,
            )
            if resolution.target_path is None:
                rows.append(
                    self._candidate_row(
                        bundle,
                        fragment,
                        table="semantic.imports",
                        data=data,
                        reason="; ".join(resolution.omitted_reasons),
                    )
                )
                continue
            status = (
                soleaux.contracts.evidence.ResolutionStatus.RESOLVED
                if resolution.complete
                else soleaux.contracts.evidence.ResolutionStatus.PARTIAL
            )
            rows.append(
                self._semantic_row(
                    bundle,
                    fragment,
                    table="semantic.imports",
                    data=data,
                    status=status,
                    note="; ".join(resolution.omitted_reasons),
                    provider="soleaux-module-resolver",
                )
            )
        return tuple(rows)

    async def resolve_calls(
        self,
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        semantic_mode: soleaux.contracts.requests.SemanticMode,
    ) -> tuple[soleaux.contracts.frame.FactRow, ...]:
        """Resolve call sites by exact position through SymbolResolver."""
        rows: list[soleaux.contracts.frame.FactRow] = []
        for fragment in self._ordered(self._call_candidates, "syntax.call_sites"):
            dynamic = fragment.name is None or self._attribute_bool(fragment, "dynamic")
            base_data = self._call_data(
                fragment,
                target_path=None,
                target_uri=None,
                symbol_id=None,
                generation_fingerprint=None,
                dynamic=dynamic,
                external=False,
                overload_count=0,
                target_line=None,
                target_column=None,
            )
            unavailable_reason: str | None = None
            if semantic_mode is soleaux.contracts.requests.SemanticMode.SYNTAX_ONLY:
                unavailable_reason = "semantic_mode=syntax_only"
            elif dynamic:
                unavailable_reason = "dynamic call has no stable callee"
            elif self._symbol_resolver is None:
                unavailable_reason = "symbol resolver is unavailable"
            if unavailable_reason is not None:
                if (
                    semantic_mode is soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED
                    and unavailable_reason == "symbol resolver is unavailable"
                ):
                    raise soleaux.lsp.broker.SemanticProviderRequiredError(
                        f"semantic_provider_required: {unavailable_reason}"
                    )
                rows.append(
                    self._candidate_row(
                        bundle,
                        fragment,
                        table="semantic.calls",
                        data=base_data,
                        reason=unavailable_reason,
                    )
                )
                continue

            resolver = self._symbol_resolver
            if resolver is None:
                raise AssertionError("checked symbol resolver must be available")
            dependencies, controls = self._semantic_inputs(bundle, fragment.path)
            request = soleaux.lsp.contracts.NavigationRequest(
                path=fragment.path,
                line=fragment.start_line + 1,
                column=fragment.start_column + 1,
                operation=soleaux.lsp.contracts.SemanticOperation.DEFINITION,
                semantic_mode=semantic_mode,
            )
            resolution = await resolver.navigate(
                request,
                bundle,
                dependency_paths=dependencies,
                control_paths=controls,
            )
            complete = (
                resolution.status is soleaux.contracts.coverage.FrameStatus.COMPLETE
                and resolution.generation is not None
                and resolution.generation.complete
            )
            if (
                semantic_mode is soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED
                and not complete
            ):
                reason = "; ".join(resolution.omitted_reasons) or resolution.status.value
                raise soleaux.lsp.broker.SemanticProviderRequiredError(
                    f"semantic_provider_required: {reason}"
                )
            if not resolution.locations:
                reason = "; ".join(resolution.omitted_reasons) or "definition not found"
                if semantic_mode is soleaux.contracts.requests.SemanticMode.SEMANTIC_REQUIRED:
                    raise soleaux.lsp.broker.SemanticProviderRequiredError(
                        f"semantic_provider_required: {reason}"
                    )
                rows.append(
                    self._candidate_row(
                        bundle,
                        fragment,
                        table="semantic.calls",
                        data=base_data,
                        reason=reason,
                    )
                )
                continue

            generation = resolution.generation
            for location in resolution.locations:
                target_path = self._workspace_path(location.uri, bundle)
                external = target_path is None
                symbol_id: str | None = None
                if generation is not None:
                    symbol_id = soleaux.lsp.operations.SymbolIdentity.from_location(
                        location,
                        provider_name=generation.provider_name,
                        generation_fingerprint=generation.fingerprint,
                        name=fragment.name,
                    ).symbol_id
                data = self._call_data(
                    fragment,
                    target_path=target_path,
                    target_uri=location.uri if external else None,
                    symbol_id=symbol_id,
                    generation_fingerprint=(
                        generation.fingerprint if generation is not None else None
                    ),
                    dynamic=False,
                    external=external,
                    overload_count=len(resolution.locations),
                    target_line=location.range.start.line + 1,
                    target_column=location.range.start.character + 1,
                )
                if external:
                    rows.append(
                        self._candidate_row(
                            bundle,
                            fragment,
                            table="semantic.calls",
                            data=data,
                            reason="definition is outside the captured workspace",
                            evidence_kind=soleaux.contracts.evidence.EvidenceKind.SEMANTIC,
                        )
                    )
                    continue
                rows.append(
                    self._semantic_row(
                        bundle,
                        fragment,
                        table="semantic.calls",
                        data=data,
                        status=(
                            soleaux.contracts.evidence.ResolutionStatus.RESOLVED
                            if complete
                            else soleaux.contracts.evidence.ResolutionStatus.PARTIAL
                        ),
                        note="; ".join(resolution.omitted_reasons),
                        provider=(
                            generation.provider_name
                            if generation is not None
                            else "symbol-resolver"
                        ),
                    )
                )
        return tuple(rows)

    @staticmethod
    def _ordered(
        fragments: tuple[soleaux.structural.fragments.SyntaxFragment, ...],
        projection: str,
    ) -> tuple[soleaux.structural.fragments.SyntaxFragment, ...]:
        for fragment in fragments:
            if fragment.projection != projection:
                raise ValueError(f"{projection} resolver received {fragment.projection!r} fragment")
        return tuple(
            sorted(
                fragments,
                key=lambda fragment: (
                    fragment.path,
                    fragment.byte_start,
                    fragment.byte_end,
                    fragment.name or "",
                ),
            )
        )

    @staticmethod
    def _attribute_bool(fragment: soleaux.structural.fragments.SyntaxFragment, name: str) -> bool:
        return fragment.attributes.get(name) is True

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not RelationResolver._is_object_sequence(value):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    @staticmethod
    def _is_object_sequence(
        value: object,
    ) -> typing.TypeGuard[list[object] | tuple[object, ...]]:
        return isinstance(value, list | tuple)

    @staticmethod
    def _import_data(
        fragment: soleaux.structural.fragments.SyntaxFragment,
        *,
        target_path: str | None,
        aliases: tuple[str, ...],
        dynamic: bool,
        external: bool,
        generation_fingerprint: str | None,
    ) -> dict[str, object]:
        return {
            "source_path": fragment.path,
            "target_path": target_path,
            "specifier": fragment.name,
            "kind": fragment.kind,
            "aliases": aliases,
            "dynamic": dynamic,
            "external": external,
            "generated": fragment.attributes.get("generated") is True,
            "generation_fingerprint": generation_fingerprint,
        }

    @staticmethod
    def _call_data(
        fragment: soleaux.structural.fragments.SyntaxFragment,
        *,
        target_path: str | None,
        target_uri: str | None,
        symbol_id: str | None,
        generation_fingerprint: str | None,
        dynamic: bool,
        external: bool,
        overload_count: int,
        target_line: int | None,
        target_column: int | None,
    ) -> dict[str, object]:
        return {
            "source_path": fragment.path,
            "target_path": target_path,
            "target_uri": target_uri,
            "callee": fragment.name,
            "symbol_id": symbol_id,
            "generation_fingerprint": generation_fingerprint,
            "dynamic": dynamic,
            "external": external,
            "generated": fragment.attributes.get("generated") is True,
            "overload_count": overload_count,
            "target_line": target_line,
            "target_column": target_column,
        }

    @staticmethod
    def _candidate_row(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        fragment: soleaux.structural.fragments.SyntaxFragment,
        *,
        table: str,
        data: dict[str, object],
        reason: str,
        evidence_kind: soleaux.contracts.evidence.EvidenceKind = (
            soleaux.contracts.evidence.EvidenceKind.STRUCTURAL
        ),
    ) -> soleaux.contracts.frame.FactRow:
        return soleaux.contracts.frame.FactRow(
            table=table,
            data=data,
            evidence=soleaux.tables.evidence.evidence_for_path(
                bundle,
                path=fragment.path,
                table=table,
                data=data,
                evidence_kind=evidence_kind,
                resolution_status=soleaux.contracts.evidence.ResolutionStatus.CANDIDATE,
                authority=soleaux.contracts.evidence.Authority.UNRESOLVED,
                provider="soleaux-relations",
                provider_version=RELATION_PROVIDER_VERSION,
                confidence=0.5,
                note=reason[:280],
                start_line=fragment.start_line + 1,
                start_column=fragment.start_column + 1,
                end_line=fragment.end_line + 1,
                end_column=fragment.end_column + 1,
                byte_start=fragment.byte_start,
                byte_end=fragment.byte_end,
            ),
        )

    @staticmethod
    def _semantic_row(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        fragment: soleaux.structural.fragments.SyntaxFragment,
        *,
        table: str,
        data: dict[str, object],
        status: soleaux.contracts.evidence.ResolutionStatus,
        note: str,
        provider: str,
    ) -> soleaux.contracts.frame.FactRow:
        return soleaux.contracts.frame.FactRow(
            table=table,
            data=data,
            evidence=soleaux.tables.evidence.evidence_for_path(
                bundle,
                path=fragment.path,
                table=table,
                data=data,
                evidence_kind=soleaux.contracts.evidence.EvidenceKind.SEMANTIC,
                resolution_status=status,
                authority=soleaux.contracts.evidence.Authority.SOURCE,
                provider=provider,
                provider_version=RELATION_PROVIDER_VERSION,
                confidence=1.0
                if status is soleaux.contracts.evidence.ResolutionStatus.RESOLVED
                else 0.75,
                note=note[:280],
                start_line=fragment.start_line + 1,
                start_column=fragment.start_column + 1,
                end_line=fragment.end_line + 1,
                end_column=fragment.end_column + 1,
                byte_start=fragment.byte_start,
                byte_end=fragment.byte_end,
            ),
        )

    @staticmethod
    def _workspace_path(uri: str, bundle: soleaux.structural.snapshot.SnapshotBundle) -> str | None:
        try:
            workspace = soleaux.contracts.workspace.WorkspaceRoot(
                bundle.snapshot.workspace_id,
                pathlib.Path(bundle.snapshot.root),
                "",
            )
            normalized = soleaux.contracts.repository.RepositoryPath.admit(workspace, uri).value
        except ValueError:
            return None
        return normalized if normalized in bundle.contents else None

    @staticmethod
    def _semantic_inputs(
        bundle: soleaux.structural.snapshot.SnapshotBundle,
        source_path: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        controls = tuple(
            path
            for path in sorted(bundle.contents)
            if pathlib.PurePosixPath(path).name in _CONTROL_NAMES
        )
        dependencies = tuple(
            path for path in sorted(bundle.contents) if path != source_path and path not in controls
        )
        return dependencies, controls


def relation_identity(row: soleaux.contracts.frame.FactRow) -> str:
    """Return a deterministic identity useful to downstream row deduplication."""
    payload = json.dumps(
        {"table": row.table, "data": row.data},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
