"""The sole Soleaux operation orchestrator shared by CLI and MCP adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, ClassVar, Literal, cast
from uuid import uuid4

from pydantic import ValidationError

from soleaux.analysis.budgets import benchmark_report, doctor_report
from soleaux.analysis.frame import (
    AnalysisFrameBuilder,
    frame_for_rows,
    timed_ms,
)
from soleaux.analysis.hydration import materialized_summary
from soleaux.authority.contracts import GovernanceState, OwnershipDecisionState
from soleaux.catalog.indexer import CatalogIndexer, CatalogPublicationProfile
from soleaux.catalog.reader import CatalogReader
from soleaux.catalog.store import SCHEMA_VERSION as CATALOG_STORE_SCHEMA_VERSION
from soleaux.catalog.store import (
    CatalogReadError,
    MaterializedRead,
    catalog_database_path,
    catalog_path_is_repository_local,
)
from soleaux.contracts.config import (
    CONFIG_FILENAME,
    CatalogMode,
    ResolvedConfig,
    config_digest,
    load_config_snapshot,
    resolved_config_bytes,
)
from soleaux.contracts.context import (
    MAX_PACKET_GAPS,
    ContextGap,
    ContextReference,
    ContextSection,
    TaskContextItem,
    TaskContextPacket,
)
from soleaux.contracts.coverage import MAX_OMITTED_REASONS, Coverage, FrameStatus
from soleaux.contracts.cursor import CursorPayload
from soleaux.contracts.evidence import ResolutionStatus
from soleaux.contracts.frame import AnalysisFrame, FactRow
from soleaux.contracts.governance import GovernanceBindingKind
from soleaux.contracts.repository import RepositoryPath, content_digest
from soleaux.contracts.requests import (
    ApplyEditRequest,
    ContextRequest,
    DescribeRequest,
    InspectOperation,
    InspectRequest,
    LintRequest,
    NavigateRequest,
    OwnershipRequest,
    OwnershipView,
    PreviewEditRequest,
    PreviewOperation,
    QueryRequest,
    RenameTarget,
    RestartLanguageServersRequest,
    SearchRequest,
    SemanticMode,
)
from soleaux.contracts.results import (
    ErrorDetail,
    ResponseEnvelope,
    ResultStatus,
    SuggestedRequest,
    TaskContextEnvelope,
)
from soleaux.contracts.tables import TABLE_CATALOG
from soleaux.contracts.workspace import (
    AllowedWorkspaceSet,
    UnauthorizedRootError,
    WorkspaceError,
    WorkspaceRoot,
)
from soleaux.editor.apply import apply_stored_preview
from soleaux.editor.contracts import ApplyPayload, ApplyState
from soleaux.editor.preview import (
    EditorPreviewError,
    PreviewLookupError,
    PreviewRegistry,
    normalize_byte_edits,
    normalize_workspace_edit,
)
from soleaux.lsp.broker import SemanticProviderRequiredError
from soleaux.lsp.contracts import LspCapability, LspLocation, NavigationRequest
from soleaux.lsp.operations import (
    CapabilityResolution,
    LspPayloadError,
    normalize_json_payload,
    user_position_from_lsp,
)
from soleaux.lsp.resolvers import (
    NAVIGATION_RESULT_LIMIT_REASON,
    SemanticResolver,
    resolve_named_symbols,
)
from soleaux.mcp_health import McpHealthTracker
from soleaux.structural.engines import StructuralEngineError, StructuralEngines
from soleaux.structural.snapshot import SnapshotBundle
from soleaux.structural.standards import WorkspaceStandardsAnalyzer

MAX_RESPONSE_WARNINGS = 20
MAX_WARNING_CHARS = 512
MAX_OWNERSHIP_RESPONSE_BYTES = 64 * 1024
MAX_CURSOR_STATES = 4096
_OWNERSHIP_RESPONSE_TARGET_BYTES = 60 * 1024
_MAX_WARNING_PATH_SAMPLES = 3
_MAX_WARNING_PATH_CHARS = 120
_PATH_WARNING_PREFIXES = (
    "skipped binary file ",
    "skipped escaping path ",
    "skipped non-UTF-8 file ",
    "skipped oversized file ",
)
_CONTEXT_RELATION_DEPTH = 2
_MAX_COVERAGE_OMISSION_GAPS = 32
_MAX_SEARCH_EXCERPT_CHARS = 2048
_MAX_OWNERSHIP_GRAPH_ROWS = 16_384
_SEARCH_COVERAGE_TABLES = {
    "chunk": ("source.context",),
    "file": ("repository.files",),
    "project": ("repository.projects",),
    "dependency": ("repository.dependencies",),
    "script": ("repository.scripts",),
    "config": ("repository.configurations",),
    "task": ("repository.tasks",),
    "route": ("repository.routes",),
    "rule": ("repository.rules",),
    "symbol": ("repository.symbols",),
    "import": ("repository.imports",),
    "diagnostic": ("repository.diagnostics",),
    "change": ("repository.changes",),
    "policy": ("authority.policies",),
}
_TABLE_COVERAGE_REASON_LABELS = frozenset(
    label
    for descriptor in TABLE_CATALOG
    for label in (descriptor.name, descriptor.name.rpartition(".")[2])
)
_CONTEXT_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "against",
        "also",
        "and",
        "any",
        "are",
        "been",
        "before",
        "being",
        "but",
        "can",
        "could",
        "does",
        "each",
        "ensure",
        "for",
        "find",
        "from",
        "have",
        "how",
        "identify",
        "implement",
        "investigate",
        "into",
        "its",
        "more",
        "must",
        "not",
        "only",
        "our",
        "provide",
        "resolve",
        "review",
        "should",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "use",
        "using",
        "verify",
        "was",
        "what",
        "when",
        "where",
        "which",
        "while",
        "why",
        "will",
        "with",
        "would",
        "your",
    }
)
_MAX_CONTEXT_TERMS = 24
INSPECT_CAPABILITY: dict[InspectOperation, LspCapability] = {
    InspectOperation.DIAGNOSTICS: LspCapability.DIAGNOSTICS,
    InspectOperation.COMPLETION: LspCapability.COMPLETION,
    InspectOperation.SIGNATURE_HELP: LspCapability.SIGNATURE_HELP,
    InspectOperation.CODE_ACTIONS: LspCapability.CODE_ACTIONS,
}


class CursorError(ValueError):
    """An opaque continuation cursor failed validation."""


class CursorDriftError(CursorError):
    """The snapshot bound into a cursor no longer matches."""


class ServiceClosedError(RuntimeError):
    """The lifespan has stopped admitting work."""


DeploymentTransport = Literal["stdio", "http"]


def product_version() -> str:
    """Return the installed package version.

    Single source of truth is the wheel/sdist metadata populated by hatchling
    from ``pyproject.toml``. Source-tree runs (``python -m soleaux`` from a
    checkout without an installable metadata directory) raise so version drift
    surfaces during development instead of silently falling back to a hardcoded
    literal that drifts from ``pyproject.toml``.
    """
    try:
        return version("soleaux")
    except PackageNotFoundError:
        # The package metadata is absent — typically a raw source checkout
        # without an installed wheel. Re-raising surfaces the missing install
        # rather than masking it with a literal that can drift.
        msg = "soleaux package metadata is not installed; run `uv sync` or `pip install -e .`"
        raise RuntimeError(msg) from None


class _CursorCodec:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_states: int = MAX_CURSOR_STATES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("cursor_ttl_seconds must be positive")
        if max_states <= 0:
            raise ValueError("max_states must be positive")
        self._key = secrets.token_bytes(32)
        self._process_epoch = uuid4().hex
        self._ttl_seconds = ttl_seconds
        self._max_states = max_states
        self._states: OrderedDict[str, tuple[float, int, int]] = OrderedDict()

    @property
    def process_epoch(self) -> str:
        return self._process_epoch

    def preview(self, payload: CursorPayload) -> str:
        body = payload.model_dump_json().encode("utf-8")
        encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
        signature = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _sweep_expired(self, now: float) -> None:
        while self._states:
            token, (expiration, _catalog_generation, _publication_revision) = next(
                iter(self._states.items())
            )
            if expiration > now:
                return
            del self._states[token]

    def encode(
        self,
        payload: CursorPayload,
        *,
        catalog_generation: int,
        publication_revision: int,
    ) -> str:
        if catalog_generation < 1:
            raise ValueError("catalog_generation must be positive")
        if publication_revision < 1:
            raise ValueError("publication_revision must be positive")
        now = time.monotonic()
        self._sweep_expired(now)
        token = self.preview(payload)
        self._states.pop(token, None)
        self._states[token] = (
            now + self._ttl_seconds,
            catalog_generation,
            publication_revision,
        )
        while len(self._states) > self._max_states:
            self._states.popitem(last=False)
        return token

    def decode(self, token: str) -> tuple[CursorPayload, int, int]:
        now = time.monotonic()
        self._sweep_expired(now)
        state = self._states.get(token)
        if state is None or now > state[0]:
            self._states.pop(token, None)
            raise CursorError("cursor is unknown, expired, or from another process")
        _expiration, catalog_generation, publication_revision = state
        try:
            encoded, signature = token.rsplit(".", 1)
        except ValueError as exc:
            raise CursorError("cursor has invalid framing") from exc
        expected = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise CursorError("cursor signature is invalid")
        padded = encoded + "=" * (-len(encoded) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded)
            payload = CursorPayload.model_validate_json(raw)
        except (ValueError, ValidationError) as exc:
            raise CursorError("cursor payload is invalid") from exc
        if payload.process_epoch != self._process_epoch:
            raise CursorError("cursor belongs to another process epoch")
        return payload, catalog_generation, publication_revision


class SoleauxService:
    """One process-local service over request-scoped snapshots and frames."""

    AUTHORITY_READ_TABLES: ClassVar[tuple[str, ...]] = (
        "authority.policies",
        "authority.bindings",
        "authority.conflicts",
    )

    def __init__(
        self,
        workspaces: AllowedWorkspaceSet,
        *,
        config: ResolvedConfig | None = None,
        cursor_ttl_seconds: float = 300,
        preview_ttl_seconds: float = 300,
        frame_builder: AnalysisFrameBuilder | None = None,
        deployment_transport: DeploymentTransport = "stdio",
        config_content_digest: str | None = None,
        publication_profile: CatalogPublicationProfile = CatalogPublicationProfile.FULL,
    ) -> None:
        self._workspaces = workspaces
        self._config = config or ResolvedConfig.default()
        self._config_digest = config_content_digest or config_digest(
            resolved_config_bytes(self._config)
        )
        self._storage_namespace = (
            publication_profile.value
            if publication_profile is CatalogPublicationProfile.AUTHORITY
            else None
        )
        if frame_builder is not None and frame_builder.storage_namespace != self._storage_namespace:
            raise ValueError(
                "frame_builder catalog storage namespace does not match "
                f"the {publication_profile.value} publication profile"
            )
        self._frames = frame_builder or AnalysisFrameBuilder(
            config=self._config,
            config_content_digest=self._config_digest,
            storage_namespace=self._storage_namespace,
        )
        self._catalog_reader = CatalogReader(
            self._frames.existing_catalog_store,
            mode=self._config.catalog.mode,
        )
        self._catalog_indexer = CatalogIndexer(
            self._workspaces,
            self._frames,
            retained_generations=self._config.catalog.retained_generations,
            publication_profile=publication_profile,
            authority_requested_tables=(
                self.AUTHORITY_READ_TABLES
                if publication_profile is CatalogPublicationProfile.AUTHORITY
                else ()
            ),
        )
        self._cursors = _CursorCodec(ttl_seconds=cursor_ttl_seconds)
        self._previews = PreviewRegistry(
            process_epoch=self._cursors.process_epoch,
            ttl_seconds=preview_ttl_seconds,
        )
        self._editor_lock = asyncio.Lock()
        self._deployment_transport = deployment_transport
        self._mcp_health = McpHealthTracker(
            self._workspaces.get(self._workspaces.workspace_ids[0]).root,
            self._config,
        )
        self._started = False
        self._closed = False

    @classmethod
    def from_root(
        cls,
        root: Path,
        *,
        config: ResolvedConfig | None = None,
        config_content: bytes | None = None,
        cursor_ttl_seconds: float = 300,
        preview_ttl_seconds: float = 300,
        deployment_transport: DeploymentTransport = "stdio",
        publication_profile: CatalogPublicationProfile = CatalogPublicationProfile.FULL,
    ) -> SoleauxService:
        resolved = root.resolve(strict=True)
        if config is None:
            if config_content is not None:
                raise ValueError("config_content requires config")
            resolved_config, raw_config = load_config_snapshot(resolved)
        else:
            resolved_config = config
            raw_config = (
                config_content if config_content is not None else resolved_config_bytes(config)
            )
        roots = cls._roots_from_config(resolved, resolved_config)
        workspaces = AllowedWorkspaceSet.from_launch(
            [(identifier, str(path)) for identifier, path in roots],
            config_digest=config_digest(raw_config),
        )
        return cls(
            workspaces,
            config=resolved_config,
            cursor_ttl_seconds=cursor_ttl_seconds,
            preview_ttl_seconds=preview_ttl_seconds,
            deployment_transport=deployment_transport,
            config_content_digest=config_digest(raw_config),
            publication_profile=publication_profile,
        )

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        cursor_ttl_seconds: float = 300,
        preview_ttl_seconds: float = 300,
        publication_profile: CatalogPublicationProfile = CatalogPublicationProfile.FULL,
    ) -> SoleauxService:
        return cls.from_root(
            cls.discover_root(directory),
            cursor_ttl_seconds=cursor_ttl_seconds,
            preview_ttl_seconds=preview_ttl_seconds,
            publication_profile=publication_profile,
        )

    @classmethod
    def from_launch(
        cls,
        roots: Sequence[tuple[str, Path]],
        *,
        cursor_ttl_seconds: float = 300,
        preview_ttl_seconds: float = 300,
        publication_profile: CatalogPublicationProfile = CatalogPublicationProfile.FULL,
    ) -> SoleauxService:
        if not roots:
            raise UnauthorizedRootError("at least one launch root is required")
        resolved: list[tuple[str, Path]] = [
            (identifier, root.resolve(strict=True)) for identifier, root in roots
        ]
        root_paths = [path for _identifier, path in resolved]
        if len(set(root_paths)) != len(root_paths):
            raise UnauthorizedRootError("duplicate resolved launch root or symlink alias")
        workspaces = AllowedWorkspaceSet.from_launch(
            [(identifier, str(path)) for identifier, path in resolved],
            config_digest=config_digest(b""),
        )
        return cls(
            workspaces,
            cursor_ttl_seconds=cursor_ttl_seconds,
            preview_ttl_seconds=preview_ttl_seconds,
            config_content_digest=config_digest(b""),
            publication_profile=publication_profile,
        )

    @staticmethod
    def discover_root(directory: Path) -> Path:
        start = directory.resolve(strict=True)
        if not start.is_dir():
            raise UnauthorizedRootError(f"launch root is not a directory: {str(start)!r}")
        for candidate in (start, *start.parents):
            if (candidate / CONFIG_FILENAME).is_file() or (candidate / ".git").exists():
                return candidate
        return start

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def structural_worker_started(self) -> bool:
        return self._frames.structural_worker_started

    @property
    def structural_worker_pid(self) -> int | None:
        return self._frames.structural_worker_pid

    @property
    def structural_completed_jobs(self) -> int:
        return self._frames.structural_completed_jobs

    @property
    def active_language_server_count(self) -> int:
        return self._frames.active_language_server_count

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return self._workspaces.workspace_ids

    @property
    def publication_profile(self) -> CatalogPublicationProfile:
        """Return the active catalog materialization profile."""
        return self._catalog_indexer.publication_profile

    @property
    def publication_attempted_tables(self) -> tuple[str, ...]:
        """Return the active profile's planner-derived table closure."""
        return self._catalog_indexer.attempted_tables

    async def __aenter__(self) -> SoleauxService:
        self._guard()
        await self.start()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object | None,
    ) -> None:
        await self.aclose()

    async def search(self, request: SearchRequest) -> ResponseEnvelope:
        """Read ranked facts from the currently published SQLite generation."""
        started = time.perf_counter()
        try:
            workspace = self._select(request.workspace_id)
            path_prefixes = self._search_path_prefixes(workspace, request.paths)
            digest = self._search_query_digest(request, path_prefixes)
            offset = 0
            cursor_payload: CursorPayload | None = None
            cursor_generation: int | None = None
            cursor_revision: int | None = None
            if request.cursor is not None:
                cursor_payload, cursor_generation, cursor_revision = self._cursors.decode(
                    request.cursor
                )
                if (
                    cursor_payload.workspace_id != workspace.workspace_id
                    or cursor_payload.query_digest != digest
                    or cursor_payload.limit != request.limit
                ):
                    raise CursorError("cursor does not match this search request")
                offset = cursor_payload.offset
            selected_kinds = tuple(kind.value for kind in request.kinds)
            coverage_tables = tuple(
                dict.fromkeys(
                    table
                    for kind in (selected_kinds or tuple(_SEARCH_COVERAGE_TABLES))
                    for table in _SEARCH_COVERAGE_TABLES[kind]
                )
            )
            read = self._catalog_reader.search(
                workspace.workspace_id,
                query=request.query,
                kinds=selected_kinds,
                path_prefixes=path_prefixes,
                limit=request.limit,
                offset=offset,
            )
            if cursor_payload is not None and (
                cursor_payload.snapshot_id != read.snapshot_id
                or cursor_generation != read.generation
                or cursor_revision != read.publication_revision
            ):
                raise CursorDriftError(
                    "the snapshot or catalog publication changed under this cursor"
                )
            if (
                request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED
                and read.frame.semantic_mode is SemanticMode.SYNTAX_ONLY
            ):
                return self._catalog_semantic_error(
                    read,
                    workspace_id=workspace.workspace_id,
                )
            omitted_reasons: list[str] = []
            rows = self._materialized_search_rows(
                read,
                query=request.query,
                context_lines=request.context_lines,
            )
            next_cursor: str | None = None
            if read.has_more:
                omitted_reasons.append("search row limit reached")
                next_cursor = self._cursors.encode(
                    CursorPayload(
                        process_epoch=self._cursors.process_epoch,
                        workspace_id=workspace.workspace_id,
                        snapshot_id=read.snapshot_id,
                        query_digest=digest,
                        limit=request.limit,
                        offset=offset + request.limit,
                    ),
                    catalog_generation=read.generation,
                    publication_revision=read.publication_revision,
                )
            base_coverage = self._coverage_for_tables(
                read.frame.coverage,
                requested_tables=coverage_tables,
                published_tables=read.published_tables,
            )
            coverage = base_coverage.model_copy(
                update={
                    "status": (
                        FrameStatus.TRUNCATED
                        if read.has_more
                        else (
                            FrameStatus.PARTIAL
                            if omitted_reasons and base_coverage.status is FrameStatus.COMPLETE
                            else base_coverage.status
                        )
                    ),
                    "omitted_reasons": tuple(
                        dict.fromkeys((*base_coverage.omitted_reasons, *omitted_reasons))
                    ),
                    "elapsed_ms": timed_ms(started),
                }
            )
            frame = read.frame.model_copy(
                update={
                    "coverage": coverage,
                    "tables": {"search.hits": tuple(rows)},
                    "warnings": tuple(dict.fromkeys((*read.frame.warnings, *omitted_reasons))),
                }
            )
            data: dict[str, Any] = {
                "query": request.query,
                "kinds": [kind.value for kind in request.kinds],
                "paths": list(path_prefixes),
                "engine": read.retrieval_engine,
                "generation": read.generation,
                "published_semantic_mode": read.frame.semantic_mode.value,
            }
            return self._from_frame(
                frame,
                rows=rows,
                data=data,
                next_cursor=next_cursor,
                suggested=self._search_suggestions(workspace, request, rows),
            )
        except CatalogReadError as exc:
            return self._error(exc.error_type, exc.message)
        except CursorDriftError as exc:
            return self._error("cursor_drift", str(exc))
        except CursorError as exc:
            return self._error("invalid_cursor", str(exc))
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("search_failed", str(exc))

    def _catalog_semantic_error(
        self,
        read: MaterializedRead,
        *,
        workspace_id: str,
    ) -> ResponseEnvelope:
        """Report that one pinned publication cannot satisfy strict semantics."""
        return self._error(
            "semantic_unavailable",
            "the active SQLite generation does not contain semantic coverage",
            workspace_id=workspace_id,
            data={
                "generation": read.generation,
                "published_semantic_mode": read.frame.semantic_mode.value,
            },
        )

    def _search_query_digest(
        self,
        request: SearchRequest,
        path_prefixes: tuple[str, ...],
    ) -> str:
        return content_digest(
            json.dumps(
                {
                    "query": request.query,
                    "kinds": [kind.value for kind in request.kinds],
                    "paths": list(path_prefixes),
                    "context_lines": request.context_lines,
                    "semantic_mode": request.semantic_mode.value,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _materialized_search_rows(
        read: MaterializedRead,
        *,
        query: str,
        context_lines: int,
    ) -> list[FactRow]:
        rows: list[FactRow] = []
        for materialized in read.rows:
            source = materialized.row
            source_data = materialized_summary(source, kind=materialized.kind)
            snippet = source_data.pop("snippet", None)
            if isinstance(snippet, str):
                source_data["excerpt"] = SoleauxService._search_excerpt(
                    snippet,
                    query=query,
                    context_lines=context_lines,
                )
            data: dict[str, Any] = {
                **source_data,
                "kind": materialized.kind,
                "key": materialized.fact_key,
                "path": source.evidence.path,
                "score": round(materialized.score, 4),
                "generation": read.generation,
                "source_table": source.table,
            }
            rows.append(
                FactRow(
                    table="search.hits",
                    data=data,
                    evidence=source.evidence,
                )
            )
        return rows

    @staticmethod
    def _search_excerpt(
        snippet: str,
        *,
        query: str,
        context_lines: int,
    ) -> str:
        lines = snippet.splitlines()
        if not lines:
            return snippet[:_MAX_SEARCH_EXCERPT_CHARS]
        query_folded = query.casefold()
        match_index = next(
            (index for index, line in enumerate(lines) if query_folded in line.casefold()),
            0,
        )
        start = max(0, match_index - context_lines)
        end = min(len(lines), match_index + context_lines + 1)
        return "\n".join(lines[start:end])[:_MAX_SEARCH_EXCERPT_CHARS]

    async def lint(self, request: LintRequest) -> ResponseEnvelope:
        started = time.perf_counter()
        try:
            workspace = self._select(request.workspace_id)
            project_config = self._config.structural.project_config
            if project_config is None:
                return self._error(
                    "lint_unconfigured",
                    "soleaux lint requires [structural].project_config in soleaux.toml",
                    workspace_id=workspace.workspace_id,
                )
            path_prefixes = self._search_path_prefixes(workspace, request.paths)
            bundle = await self._frames.capture(workspace)
            generation = self._frames.catalog_for_bundle(bundle)
            result = await WorkspaceStandardsAnalyzer(
                root=workspace.root,
                config=self._config.structural,
                engines=self._engines(workspace),
            ).scan(
                bundle,
                rule_ids=tuple(request.rule_ids),
                severities=tuple(request.severities),
                path_prefixes=path_prefixes,
                limit=request.limit,
                fail_on_unknown_rule=True,
            )
            status = (
                FrameStatus.TRUNCATED
                if result.truncated
                else (
                    FrameStatus.UNSUPPORTED
                    if not result.available
                    else (FrameStatus.PARTIAL if result.warnings else FrameStatus.COMPLETE)
                )
            )
            frame = frame_for_rows(
                bundle,
                semantic_mode=request.semantic_mode,
                table="quality.standards",
                rows=result.rows,
                status=status,
                omitted_reasons=result.warnings,
                elapsed_ms=timed_ms(started),
            )
            return self._from_frame(
                frame,
                data={
                    "project_config": project_config,
                    "rules": list(result.rule_ids),
                    "generation": generation.number,
                },
            )
        except StructuralEngineError as exc:
            return self._error(exc.error_type, exc.message)
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("lint_failed", str(exc))

    async def context(self, request: ContextRequest) -> TaskContextEnvelope:
        """Read one already-published SQLite context generation."""
        started = time.perf_counter()
        try:
            workspace = self._select(request.workspace_id)
            path_prefixes = self._search_path_prefixes(workspace, request.paths)
            terms = self._context_terms(request.objective)
            read = self._catalog_reader.context(
                workspace.workspace_id,
                objective=request.objective,
                terms=terms,
                path_prefixes=path_prefixes,
                limit=min(1000, max(100, request.limit * 8)),
            )
            if (
                request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED
                and read.frame.semantic_mode is SemanticMode.SYNTAX_ONLY
            ):
                return self._context_error(
                    "semantic_unavailable",
                    "the active SQLite generation does not contain semantic coverage",
                    workspace_id=workspace.workspace_id,
                    retryable=True,
                )

            external_references, reference_gaps, reference_bytes = self._bounded_context_references(
                request.references,
                max_bytes=request.max_bytes,
            )
            remaining_bytes = max(1, request.max_bytes - reference_bytes)
            source_rows: list[FactRow] = []
            fact_rows: list[FactRow] = []
            relation_distances: dict[str, int] = {}
            source_truncated = False
            for materialized in read.rows:
                row = materialized.row
                relation_distances[row.evidence.evidence_id] = materialized.relation_distance
                if row.table != "source.context":
                    fact_rows.append(row)
                    continue
                if remaining_bytes <= 0:
                    source_truncated = True
                    continue
                data = dict(row.data)
                snippet = data.get("snippet")
                if not isinstance(snippet, str):
                    continue
                encoded = snippet.encode("utf-8")
                selected = encoded[: min(remaining_bytes, 8192)]
                bounded = selected.decode("utf-8", errors="ignore")
                if not bounded:
                    source_truncated = True
                    continue
                data.update(
                    {
                        "snippet": bounded,
                        "matched_terms": sorted(
                            term
                            for term in terms
                            if term in row.evidence.path.casefold() or term in bounded.casefold()
                        ),
                        "score": materialized.score,
                        "truncated": len(selected) < len(encoded),
                    }
                )
                source_rows.append(row.model_copy(update={"data": data}))
                remaining_bytes -= len(selected)
                if len(selected) < len(encoded) or remaining_bytes <= 0:
                    source_truncated = True

            source_truncated = source_truncated or read.has_more
            source_quota = min(
                8,
                request.limit,
                max(1, len(path_prefixes), request.limit // 3),
            )
            selected_source = list(
                self._select_context_sources(
                    source_rows,
                    path_prefixes=path_prefixes,
                    limit=source_quota,
                )
            )
            selected_facts = list(
                self._select_context_facts(
                    fact_rows,
                    limit=request.limit - len(selected_source),
                )
            )
            selected_rows = (*selected_source, *selected_facts)
            rows_truncated = read.has_more or len(selected_rows) < len(source_rows) + len(fact_rows)
            omitted_reasons = tuple(
                reason
                for condition, reason in (
                    (source_truncated, "context source excerpt limit reached"),
                    (rows_truncated, "context row limit reached"),
                )
                if condition
            )
            coverage = read.frame.coverage
            if omitted_reasons:
                merged_reasons = tuple(dict.fromkeys((*coverage.omitted_reasons, *omitted_reasons)))
                if len(merged_reasons) > MAX_OMITTED_REASONS:
                    kept = MAX_OMITTED_REASONS - len(omitted_reasons)
                    merged_reasons = (
                        *merged_reasons[:kept],
                        *omitted_reasons,
                    )
                coverage = coverage.model_copy(
                    update={
                        "status": (
                            FrameStatus.TRUNCATED
                            if coverage.status is FrameStatus.COMPLETE
                            else coverage.status
                        ),
                        "omitted_reasons": merged_reasons,
                    }
                )
            coverage = coverage.model_copy(update={"elapsed_ms": timed_ms(started)})
            frame = read.frame.model_copy(
                update={
                    "coverage": coverage,
                    "warnings": tuple(dict.fromkeys((*read.frame.warnings, *omitted_reasons))),
                }
            )
            gaps = list(reference_gaps)
            if source_truncated:
                gaps.append(
                    ContextGap(
                        code="source_excerpt_limit",
                        message="The source excerpt byte budget omitted matching content.",
                    )
                )
            if rows_truncated:
                gaps.append(
                    ContextGap(
                        code="relation_row_limit",
                        message=(
                            "The response row limit omitted task-related facts; increase "
                            "limit or narrow the objective."
                        ),
                    )
                )
            omitted_reasons_all = read.frame.coverage.omitted_reasons
            gaps.extend(
                ContextGap(code="coverage_omission", message=reason)
                for reason in omitted_reasons_all[:_MAX_COVERAGE_OMISSION_GAPS]
            )
            if len(omitted_reasons_all) > _MAX_COVERAGE_OMISSION_GAPS:
                gaps.append(
                    ContextGap(
                        code="coverage_omission",
                        message=(
                            f"{len(omitted_reasons_all) - _MAX_COVERAGE_OMISSION_GAPS} "
                            "further generation coverage omissions are not listed; "
                            "inspect the published generation coverage for the full set."
                        ),
                    )
                )
            gaps.extend(self._context_fact_gaps(selected_rows))
            context_items = tuple(
                self._task_context_item(
                    row,
                    relation_distance=relation_distances.get(
                        row.evidence.evidence_id,
                        0,
                    ),
                )
                for row in selected_rows
            )
            packet = self._task_context_packet(
                request=request,
                paths=path_prefixes,
                terms=terms,
                retrieval_engine=read.retrieval_engine,
                items=context_items,
                external_references=external_references,
                gaps=tuple(gaps),
                ranked_candidate_count=sum(item.relation_distance == 0 for item in read.rows),
                related_fact_count=len(fact_rows),
                response_truncated=bool(omitted_reasons),
                coverage_complete=not gaps and coverage.status is FrameStatus.COMPLETE,
            )
            envelope = self._context_from_frame(frame, data=packet)
            if len(envelope.model_dump_json().encode("utf-8")) > request.max_bytes:
                shrunk = self._shrink_context_envelope(
                    frame,
                    packet,
                    max_bytes=request.max_bytes,
                )
                if shrunk is None:
                    return self._context_error(
                        "context_response_too_large",
                        "the required context sections exceed the caller's max_bytes "
                        "budget; increase max_bytes or narrow the objective",
                        workspace_id=workspace.workspace_id,
                    )
                return shrunk
            return envelope
        except CatalogReadError as exc:
            return self._context_error(
                exc.error_type,
                exc.message,
                workspace_id=request.workspace_id,
                retryable=exc.retryable,
            )
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._context_error("context_failed", str(exc))

    async def ownership(
        self,
        request: OwnershipRequest,
    ) -> ResponseEnvelope:
        """Discover or resolve policies through Authority tables without starting an LSP."""
        try:
            workspace = self._select(request.workspace_id)
            path_prefixes = self._search_path_prefixes(workspace, request.paths)
            digest = self._ownership_query_digest(
                request,
                workspace_id=workspace.workspace_id,
                path_prefixes=path_prefixes,
            )
            offset = 0
            cursor_payload: CursorPayload | None = None
            cursor_generation: int | None = None
            cursor_revision: int | None = None
            if request.cursor is not None:
                cursor_payload, cursor_generation, cursor_revision = self._cursors.decode(
                    request.cursor
                )
                if (
                    cursor_payload.workspace_id != workspace.workspace_id
                    or cursor_payload.query_digest != digest
                    or cursor_payload.limit != request.limit
                ):
                    raise CursorError("cursor does not match this ownership request")
                offset = cursor_payload.offset
            identities_only = request.view is OwnershipView.IDENTITIES
            requested_tables = (
                ("authority.policies",) if identities_only else self.AUTHORITY_READ_TABLES
            )
            catalog_read = self._catalog_reader.tables(
                workspace.workspace_id,
                include_tables=requested_tables,
                ownership_selector=request.policy,
                limit=(request.limit if identities_only else _MAX_OWNERSHIP_GRAPH_ROWS),
                offset=offset if identities_only else 0,
            )
            if not identities_only and catalog_read.has_more:
                return self._error(
                    "ownership_graph_too_large",
                    "the selected ownership graph exceeds the "
                    f"{_MAX_OWNERSHIP_GRAPH_ROWS}-row request bound",
                    workspace_id=workspace.workspace_id,
                )
            if (
                request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED
                and catalog_read.frame.semantic_mode is SemanticMode.SYNTAX_ONLY
            ):
                return self._catalog_semantic_error(
                    catalog_read,
                    workspace_id=workspace.workspace_id,
                )
            if cursor_payload is not None and (
                cursor_payload.snapshot_id != catalog_read.snapshot_id
                or cursor_generation != catalog_read.generation
                or cursor_revision != catalog_read.publication_revision
            ):
                raise CursorDriftError(
                    "the snapshot or catalog publication changed under this ownership cursor"
                )
            catalog_frame = catalog_read.frame.model_copy(
                update={
                    "coverage": self._coverage_for_tables(
                        catalog_read.frame.coverage,
                        requested_tables=requested_tables,
                        published_tables=catalog_read.published_tables,
                    )
                }
            )
            policy_catalog = catalog_frame.tables.get("authority.policies", ())
            binding_catalog = catalog_frame.tables.get("authority.bindings", ())
            conflict_catalog = catalog_frame.tables.get("authority.conflicts", ())
            selected_policy_ids = tuple(
                sorted(
                    {
                        policy_id
                        for row in policy_catalog
                        if isinstance(
                            (policy_id := row.data.get("policy_id")),
                            str,
                        )
                    }
                )
            )
            policy_rows = tuple(
                sorted(
                    (
                        row
                        for row in policy_catalog
                        if row.data.get("policy_id") in selected_policy_ids
                    ),
                    key=lambda row: str(row.data.get("policy_id", "")),
                )
            )
            policy_id_values: set[str] = set()
            for row in policy_rows:
                policy_id = row.data.get("policy_id")
                if isinstance(policy_id, str):
                    policy_id_values.add(policy_id)
            policy_ids = tuple(sorted(policy_id_values))
            if request.view is OwnershipView.IDENTITIES:
                identity_warnings = self._ownership_scope_warnings(
                    catalog_frame,
                    policy_rows=policy_rows,
                    binding_rows=(),
                    selector=request.policy,
                )
                frame = self._ownership_scope_frame(
                    catalog_frame,
                    rows=policy_rows,
                    warnings=identity_warnings,
                )
                return self._bounded_ownership_response(
                    frame,
                    rows=policy_rows,
                    data={},
                    view=request.view,
                    limit=request.limit,
                    offset=offset,
                    query_digest=digest,
                    catalog_generation=catalog_read.generation,
                    publication_revision=catalog_read.publication_revision,
                    total_rows=catalog_read.total_rows,
                    rows_offset=offset,
                )
            if not policy_ids:
                not_found_warnings = self._ownership_scope_warnings(
                    catalog_frame,
                    policy_rows=(),
                    binding_rows=(),
                    selector=request.policy,
                )
                frame = self._ownership_scope_frame(
                    catalog_frame,
                    rows=(),
                    warnings=not_found_warnings,
                )
                return self._bounded_ownership_response(
                    frame,
                    rows=(),
                    data={
                        "state": OwnershipDecisionState.NOT_FOUND.value,
                        "policy": None,
                        "binding_ids": {},
                        "evidence_binding_ids": [],
                        "missing_roles": [],
                        "conflict_ids": [],
                        "candidates": [],
                    },
                    view=request.view,
                    limit=request.limit,
                    offset=offset,
                    query_digest=digest,
                    catalog_generation=catalog_read.generation,
                    publication_revision=catalog_read.publication_revision,
                )
            ownerships: list[dict[str, Any]] = []
            rows: list[FactRow] = []
            warnings: list[str] = []
            for selected_policy in policy_rows:
                record, record_rows, record_warnings = self._ownership_record(
                    selected_policy,
                    binding_catalog=binding_catalog,
                    conflict_catalog=conflict_catalog,
                    path_prefixes=path_prefixes,
                    frame=catalog_frame,
                )
                ownerships.append(record)
                rows.extend(record_rows)
                warnings.extend(record_warnings)

            selected_rows = tuple(rows)
            scoped_warnings = tuple(dict.fromkeys(warnings))
            frame = self._ownership_scope_frame(
                catalog_frame,
                rows=selected_rows,
                warnings=scoped_warnings,
            )
            data: dict[str, Any]
            if len(ownerships) == 1:
                data = {key: value for key, value in ownerships[0].items() if key != "coverage"}
                data["candidates"] = []
            else:
                data = {
                    "state": OwnershipDecisionState.AMBIGUOUS.value,
                    "policy": None,
                    "binding_ids": {},
                    "evidence_binding_ids": [],
                    "missing_roles": [],
                    "conflict_ids": [],
                    "candidates": [record["policy"] for record in ownerships],
                    "ownerships": ownerships,
                }
            return self._bounded_ownership_response(
                frame,
                rows=selected_rows,
                data=data,
                view=request.view,
                limit=request.limit,
                offset=offset,
                query_digest=digest,
                catalog_generation=catalog_read.generation,
                publication_revision=catalog_read.publication_revision,
            )
        except CursorDriftError as exc:
            return self._error("cursor_drift", str(exc))
        except CursorError as exc:
            return self._error("invalid_cursor", str(exc))
        except CatalogReadError as exc:
            return self._error(exc.error_type, exc.message)
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("ownership_explanation_failed", str(exc))

    @staticmethod
    def _ownership_query_digest(
        request: OwnershipRequest,
        *,
        workspace_id: str,
        path_prefixes: tuple[str, ...],
    ) -> str:
        return content_digest(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "policy": request.policy,
                    "paths": list(path_prefixes),
                    "semantic_mode": request.semantic_mode.value,
                    "view": request.view.value,
                    "limit": request.limit,
                },
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    def _engines(self, workspace: WorkspaceRoot) -> StructuralEngines:
        return self._frames.structural_engines(workspace)

    async def _structural_preview(self, request: PreviewEditRequest) -> ResponseEnvelope:
        try:
            workspace = self._select(request.workspace_id)
            matcher = request.structural
            if matcher is None:
                raise EditorPreviewError("structural_rewrite requires a structural matcher")
            generation, bundle = await self._frames.catalog_bundle(workspace)
            engines = self._engines(workspace)
            resolved = engines.resolve(matcher)
            if resolved.fix is None:
                raise EditorPreviewError(
                    "structural_rewrite requires an explicit fix on inline matchers "
                    "or a declared fix on the referenced rule"
                )
            selected_paths = tuple(request.paths) or tuple(
                item.path
                for item in bundle.snapshot.files
                if item.language is not None
                and item.language.casefold() == resolved.language.casefold()
            )
            files: list[tuple[str, bytes]] = []
            for path in sorted(dict.fromkeys(selected_paths)):
                relative = self._relative_path(workspace, path)
                content = bundle.contents.get(relative)
                if content is None:
                    raise EditorPreviewError(f"{relative}: path is not in the captured snapshot")
                files.append((relative, content))
            if not files:
                raise EditorPreviewError("structural_rewrite selected no captured files")
            outcome = await engines.run(
                resolved,
                files=tuple(files),
                want=("findings", "edits"),
            )
            if outcome.errors:
                raise EditorPreviewError("; ".join(outcome.errors))
            if outcome.truncated:
                raise EditorPreviewError("structural rewrite exceeded the finding budget")
            if not outcome.edits:
                return self._ok(
                    data={"state": "no_changes", "findings": len(outcome.findings)},
                    workspace_id=workspace.workspace_id,
                    snapshot_id=bundle.snapshot.snapshot_id,
                )
            normalized = normalize_byte_edits(bundle=bundle, edits=outcome.edits)
            rule_digest = content_digest(
                json.dumps(
                    {
                        "matcher": resolved.matcher,
                        "fix": resolved.fix,
                        "transforms": resolved.transforms,
                        "engine": outcome.engine.value,
                        "engine_version": outcome.engine_version,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            preview = self._previews.issue(
                workspace_id=workspace.workspace_id,
                root=workspace.root,
                provider_name=f"structural:{outcome.engine.value}",
                provider_config_digest=rule_digest,
                project_id=workspace.workspace_id,
                project_root=str(workspace.root),
                project_config_digest=rule_digest,
                compiler_identity=f"ast-grep:{outcome.engine_version}",
                provider_epoch=0,
                generation_fingerprint=generation.source_fingerprint,
                operation=request.operation.value,
                target={
                    "matcher_kind": resolved.matcher.get("kind"),
                    "rule_id": resolved.rule_id,
                    "paths": [path for path, _content in files],
                },
                position_encoding="utf-32",
                normalized=normalized,
                origin="structural",
                engine_version=outcome.engine_version,
                rule_digest=rule_digest,
            )
            return self._ok(
                data=preview.model_dump(mode="json"),
                workspace_id=workspace.workspace_id,
                snapshot_id=bundle.snapshot.snapshot_id,
            )
        except StructuralEngineError as exc:
            return self._error(exc.error_type, exc.message)
        except (
            EditorPreviewError,
            ServiceClosedError,
            WorkspaceError,
            ValueError,
        ) as exc:
            return self._error("preview_failed", str(exc))

    async def preview(self, request: PreviewEditRequest) -> ResponseEnvelope:
        if request.operation is PreviewOperation.STRUCTURAL_REWRITE:
            return await self._structural_preview(request)
        try:
            workspace = self._select(request.workspace_id)
            if request.path is None:
                raise EditorPreviewError(f"{request.operation.value} requires path")
            relative = self._relative_path(workspace, request.path)
            bundle = await self._frames.capture(workspace)
            resolver = self._frames.semantic_resolver(workspace)
            capability, line, column, arguments, target = await self._editor_request_arguments(
                request,
                relative=relative,
                bundle=bundle,
                resolver=resolver,
                root=workspace.root,
            )
            resolution = await resolver.execute_capability(
                capability,
                bundle,
                path=relative,
                line=line,
                column=column,
                arguments=arguments,
                semantic_mode=request.semantic_mode,
                dependency_paths=tuple(
                    path for path in sorted(bundle.contents) if path != relative
                ),
            )
            generation = resolution.generation
            if (
                resolution.status is not FrameStatus.COMPLETE
                or generation is None
                or not generation.complete
            ):
                reason = ", ".join(resolution.omitted_reasons) or "provider returned no edit"
                raise EditorPreviewError(reason)
            session = resolver.editor_session_context(generation)
            workspace_edit = _workspace_edit_from_payload(
                request,
                resolution.payload,
                uri=(workspace.root / relative).resolve().as_uri(),
            )
            normalized = normalize_workspace_edit(
                workspace_edit,
                root=workspace.root,
                bundle=bundle,
                position_encoding=session.position_encoding,
                document_versions=session.document_versions,
            )
            preview = self._previews.issue(
                workspace_id=workspace.workspace_id,
                root=workspace.root,
                provider_name=generation.provider_name,
                provider_config_digest=generation.provider_config_digest,
                project_id=generation.project_id,
                project_root=generation.project_root,
                project_config_digest=generation.project_config_digest,
                compiler_identity=generation.compiler_identity,
                provider_epoch=generation.process_epoch,
                generation_fingerprint=generation.fingerprint,
                operation=request.operation.value,
                target=target,
                position_encoding=session.position_encoding,
                normalized=normalized,
            )
            return self._ok(
                data=preview.model_dump(mode="json"),
                workspace_id=workspace.workspace_id,
                snapshot_id=bundle.snapshot.snapshot_id,
            )
        except (
            EditorPreviewError,
            ServiceClosedError,
            WorkspaceError,
            ValueError,
        ) as exc:
            return self._error("preview_failed", str(exc))

    async def apply(self, request: ApplyEditRequest) -> ResponseEnvelope:
        workspace_id: str | None = None
        try:
            if not request.confirm:
                raise PreviewLookupError("explicit confirmation is required")
            workspace = self._select(request.workspace_id)
            workspace_id = workspace.workspace_id
            async with self._editor_lock:
                context = self._previews.context(request.preview_id, request.digest)
                if context.workspace_id != workspace.workspace_id:
                    raise PreviewLookupError("preview belongs to another workspace")
                if context.origin == "structural":
                    current_provider_epoch = 0
                else:
                    resolver = self._frames.semantic_resolver(workspace)
                    current_provider_epoch = resolver.process_epoch(
                        workspace_id=workspace.workspace_id,
                        provider_name=context.provider_name,
                        provider_config_digest=context.provider_config_digest,
                        project_id=context.project_id,
                        project_root=context.project_root,
                        project_config_digest=context.project_config_digest,
                        compiler_identity=context.compiler_identity,
                    )
                record = self._previews.claim(
                    preview_id=request.preview_id,
                    digest=request.digest,
                    workspace_id=workspace.workspace_id,
                    current_process_epoch=self._cursors.process_epoch,
                    current_provider_epoch=current_provider_epoch,
                )
                affected_paths = tuple(item.path for item in record.files)
                try:
                    result = await apply_stored_preview(record)
                finally:
                    self._frames.mark_dirty(
                        workspace.workspace_id,
                        affected_paths,
                    )
                    self._catalog_indexer.notify_dirty()
            return self._ok(
                data=result.model_dump(mode="json"),
                workspace_id=workspace.workspace_id,
            )
        except (
            EditorPreviewError,
            PreviewLookupError,
            ServiceClosedError,
            WorkspaceError,
            ValueError,
        ) as exc:
            conflicted = ApplyPayload(
                preview_id=request.preview_id,
                state=ApplyState.CONFLICTED,
                message=str(exc),
            )
            return self._error(
                "apply_conflicted",
                str(exc),
                workspace_id=workspace_id,
                data=conflicted.model_dump(mode="json"),
            )

    async def restart(
        self,
        request: RestartLanguageServersRequest,
    ) -> ResponseEnvelope:
        try:
            workspace = self._select(request.workspace_id)
            resolver = self._frames.semantic_resolver(workspace)
            async with self._editor_lock:
                restarted = await resolver.restart_selected(
                    workspace_id=workspace.workspace_id,
                    provider_name=request.provider,
                    language=request.language,
                    path=request.path,
                )
            return self._ok(
                data=restarted.model_dump(mode="json"),
                workspace_id=workspace.workspace_id,
            )
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("restart_failed", str(exc))

    async def describe(self, request: DescribeRequest) -> ResponseEnvelope:
        """Return the coherent introspection payload for this server instance."""
        try:
            workspace = self._select(request.workspace_id)
            from soleaux import surface
            from soleaux._identity import resolve_build_identity

            catalog = surface.catalog_payload()
            semantic_modes = surface.semantic_mode_catalog()
            table_summary = [
                {
                    "name": descriptor.name,
                    "producer": descriptor.producer.value,
                    "availability": descriptor.availability,
                    "semantic_requirement": descriptor.semantic_requirement.value,
                    "cost_class": descriptor.cost_class.value,
                }
                for descriptor in TABLE_CATALOG
            ]
            storage = self._frames.catalog_status(workspace.workspace_id)
            publication_status = self._catalog_indexer.publication_status(workspace.workspace_id)
            requested_catalog_mode = CatalogMode(str(storage["requested_mode"]))
            expected_catalog_path = (
                catalog_database_path(workspace.root)
                if self._storage_namespace is None
                else catalog_database_path(
                    workspace.root,
                    storage_namespace=self._storage_namespace,
                )
            )
            storage_path = storage["path"]
            repository_local = isinstance(
                storage_path,
                str,
            ) and catalog_path_is_repository_local(workspace.root, Path(storage_path))
            data = {
                "product": {
                    "name": "Soleaux",
                    "version": product_version(),
                    "distribution": "soleaux",
                },
                "identity": {
                    "process_epoch": self._cursors.process_epoch,
                    "workspace_id": workspace.workspace_id,
                    "workspace_root": str(workspace.root),
                    "workspace_trust_digest": workspace.trust_digest,
                    "deployment_transport": self._deployment_transport,
                    "catalog_digest": surface.catalog_digest(),
                    "configuration_digest": self._config_digest,
                    "build": resolve_build_identity(),
                },
                "catalog": {
                    **catalog,
                    "digest": surface.catalog_digest(),
                },
                "configuration": {
                    "schema_version": self._config.schema_version,
                    "digest": self._config_digest,
                    "value": self._config.public_payload(),
                },
                "semantic_modes": semantic_modes,
                "tables": {
                    "schema_version": "soleaux.tables/v1",
                    "descriptors": table_summary,
                    "descriptor_count": len(table_summary),
                },
                "storage": {
                    **storage,
                    **publication_status,
                    "schema_version": CATALOG_STORE_SCHEMA_VERSION,
                    "requested_mode": requested_catalog_mode.value,
                    "expected_path": (
                        str(expected_catalog_path)
                        if requested_catalog_mode in {CatalogMode.AUTO, CatalogMode.DISK}
                        else None
                    ),
                    "repository_local": repository_local,
                    "fallback_reason": storage["fallback_reason"],
                },
                "providers": {
                    "schema_version": "soleaux.providers/v1",
                    "active_language_server_count": self.active_language_server_count,
                    "structural_worker_started": self.structural_worker_started,
                },
                "mcp_backends": self._mcp_health.payload(),
            }
            return self._ok(
                data=data,
                workspace_id=workspace.workspace_id,
            )
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("describe_failed", str(exc))

    async def query(self, request: QueryRequest) -> ResponseEnvelope:
        """Read one explicit table batch from lifecycle-published SQLite."""
        started = time.perf_counter()
        try:
            workspace = self._select(request.workspace_id)
            digest = self._query_digest(request, workspace.workspace_id)
            offset = 0
            cursor_payload: CursorPayload | None = None
            cursor_generation: int | None = None
            cursor_revision: int | None = None
            if request.cursor is not None:
                cursor_payload, cursor_generation, cursor_revision = self._cursors.decode(
                    request.cursor
                )
                if (
                    cursor_payload.workspace_id != workspace.workspace_id
                    or cursor_payload.query_digest != digest
                    or cursor_payload.limit != request.limit
                ):
                    raise CursorError("cursor does not match this query request")
                offset = cursor_payload.offset
            requested_tables = tuple(
                table
                for table in request.include_tables
                if table not in set(request.exclude_tables)
            )
            query_read = self._catalog_reader.tables(
                workspace.workspace_id,
                include_tables=requested_tables,
                seed_keys=tuple(request.seed_keys),
                limit=request.limit,
                offset=offset,
            )
            if (
                request.semantic_mode is SemanticMode.SEMANTIC_REQUIRED
                and query_read.frame.semantic_mode is SemanticMode.SYNTAX_ONLY
            ):
                return self._catalog_semantic_error(
                    query_read,
                    workspace_id=workspace.workspace_id,
                )
            if cursor_payload is not None and (
                cursor_payload.snapshot_id != query_read.snapshot_id
                or cursor_generation != query_read.generation
                or cursor_revision != query_read.publication_revision
            ):
                raise CursorDriftError(
                    "the snapshot or catalog publication changed under this query cursor"
                )
            query_frame = query_read.frame
            rows = tuple(item.row for item in query_read.rows)
            next_offset = offset + len(rows)
            next_cursor: str | None = None
            if query_read.has_more:
                next_cursor = self._cursors.encode(
                    CursorPayload(
                        process_epoch=self._cursors.process_epoch,
                        workspace_id=workspace.workspace_id,
                        snapshot_id=query_read.snapshot_id,
                        query_digest=digest,
                        limit=request.limit,
                        offset=next_offset,
                    ),
                    catalog_generation=query_read.generation,
                    publication_revision=query_read.publication_revision,
                )
            coverage = self._coverage_for_tables(
                query_frame.coverage,
                requested_tables=requested_tables,
                published_tables=query_read.published_tables,
            ).model_copy(update={"elapsed_ms": timed_ms(started)})
            if next_cursor is not None:
                reason = "query response row limit reached"
                coverage = coverage.model_copy(
                    update={
                        "status": FrameStatus.TRUNCATED,
                        "omitted_reasons": tuple(
                            dict.fromkeys((*coverage.omitted_reasons, reason))
                        ),
                    }
                )
            warnings = tuple(dict.fromkeys((*query_frame.warnings, *coverage.omitted_reasons)))
            frame = query_frame.model_copy(
                update={
                    "coverage": coverage,
                    "warnings": warnings,
                }
            )
            return self._from_frame(
                frame,
                rows=rows,
                data={
                    "include_tables": list(request.include_tables),
                    "exclude_tables": list(request.exclude_tables),
                    "seed_keys": list(request.seed_keys),
                    "tables": list(dict.fromkeys(row.table for row in rows)),
                    "offset": offset,
                    "returned_rows": len(rows),
                    "total_rows": query_read.total_rows,
                    "generation": query_read.generation,
                },
                next_cursor=next_cursor,
            )
        except CursorDriftError as exc:
            return self._error("cursor_drift", str(exc))
        except CursorError as exc:
            return self._error("invalid_cursor", str(exc))
        except CatalogReadError as exc:
            return self._error(exc.error_type, exc.message)
        except StructuralEngineError as exc:
            return self._error(exc.error_type, exc.message)
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("query_failed", str(exc))

    @staticmethod
    def _coverage_for_tables(
        coverage: Coverage,
        *,
        requested_tables: Sequence[str],
        published_tables: Sequence[str],
    ) -> Coverage:
        requested = tuple(dict.fromkeys(requested_tables))
        if not requested:
            return coverage
        published = frozenset(published_tables)
        missing = tuple(table for table in requested if table not in published)
        reason_labels = {
            label for table in requested for label in (table, table.rpartition(".")[2])
        }
        scoped_reasons = tuple(
            reason
            for reason in coverage.omitted_reasons
            if reason.partition(":")[0] in reason_labels
        )
        global_reasons = tuple(
            reason
            for reason in coverage.omitted_reasons
            if reason.partition(":")[0] not in _TABLE_COVERAGE_REASON_LABELS
        )
        missing_reasons = tuple(
            f"{table}: not present in the lifecycle-published generation"
            for table in missing
            if not any(reason.startswith(f"{table}:") for reason in scoped_reasons)
        )
        omitted_reasons = tuple(dict.fromkeys((*scoped_reasons, *global_reasons, *missing_reasons)))
        if coverage.status is FrameStatus.CHANGED_DURING_ANALYSIS:
            status = coverage.status
        elif len(missing) == len(requested):
            status = FrameStatus.UNSUPPORTED
        elif coverage.status in {FrameStatus.FAILED, FrameStatus.TRUNCATED} and omitted_reasons:
            status = coverage.status
        elif missing or omitted_reasons:
            status = FrameStatus.PARTIAL
        else:
            status = FrameStatus.COMPLETE
        return coverage.model_copy(
            update={
                "status": status,
                "unsupported_count": len(missing),
                "failed_count": coverage.failed_count if status is FrameStatus.FAILED else 0,
                "omitted_reasons": omitted_reasons,
            }
        )

    @staticmethod
    def _query_digest(request: QueryRequest, workspace_id: str) -> str:
        return content_digest(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "include_tables": request.include_tables,
                    "exclude_tables": request.exclude_tables,
                    "seed_keys": request.seed_keys,
                    "semantic_mode": request.semantic_mode.value,
                },
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    async def navigate(self, request: NavigateRequest) -> ResponseEnvelope:
        """Resolve one semantic navigation operation through the LSP resolver."""
        try:
            workspace = self._select(request.workspace_id)
            bundle = await self._frames.capture(workspace)
            resolver = self._frames.semantic_resolver(workspace)
            internal_request = NavigationRequest(
                operation=request.operation,
                workspace_id=request.workspace_id,
                semantic_mode=request.semantic_mode,
                path=request.path,
                line=request.line,
                column=request.column,
                symbol_name=request.symbol_name,
                symbol_kind=request.symbol_kind,
                limit=request.limit,
            )
            resolution = await resolver.navigate(
                internal_request,
                bundle,
                dependency_paths=tuple(
                    path for path in sorted(bundle.contents) if path != request.path
                ),
            )
            self._frames.record_semantic_resolution(bundle, resolution)
            data = resolution.model_dump(mode="json")
            data["operation"] = request.operation.value
            data["limit"] = request.limit
            warnings = list(resolution.omitted_reasons)
            if resolution.locations and len(resolution.locations) > request.limit:
                warnings.append(NAVIGATION_RESULT_LIMIT_REASON)
            return self._ok(
                data=data,
                workspace_id=workspace.workspace_id,
                snapshot_id=bundle.snapshot.snapshot_id,
                warnings=warnings,
            )
        except SemanticProviderRequiredError as exc:
            return self._error("semantic_provider_required", str(exc))
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("navigate_failed", str(exc))

    async def inspect(self, request: InspectRequest) -> ResponseEnvelope:
        """Resolve one LSP capability (diagnostics, completion, etc.)."""
        try:
            workspace = self._select(request.workspace_id)
            relative = self._relative_path(workspace, request.path)
            bundle = await self._frames.capture(workspace)
            resolver = self._frames.semantic_resolver(workspace)
            capability = INSPECT_CAPABILITY[request.operation]
            resolution = await resolver.execute_capability(
                capability,
                bundle,
                path=relative,
                line=request.line,
                column=request.column,
                semantic_mode=request.semantic_mode,
                dependency_paths=tuple(
                    path for path in sorted(bundle.contents) if path != relative
                ),
            )
            self._frames.record_semantic_resolution(bundle, resolution)
            data = resolution.model_dump(mode="json")
            data["operation"] = request.operation.value
            data["limit"] = request.limit
            return self._ok(
                data=data,
                workspace_id=workspace.workspace_id,
                snapshot_id=bundle.snapshot.snapshot_id,
                warnings=list(resolution.omitted_reasons),
            )
        except SemanticProviderRequiredError as exc:
            return self._error("semantic_provider_required", str(exc))
        except (ServiceClosedError, WorkspaceError, ValueError) as exc:
            return self._error("inspect_failed", str(exc))

    async def _editor_request_arguments(
        self,
        request: PreviewEditRequest,
        *,
        relative: str,
        bundle: SnapshotBundle,
        resolver: SemanticResolver,
        root: Path,
    ) -> tuple[LspCapability, int, int, dict[str, object], dict[str, object]]:
        line = request.line or 1
        column = request.column or 1
        target = {
            key: value
            for key, value in request.model_dump(mode="json").items()
            if key not in {"workspace_id", "semantic_mode"} and value is not None
        }
        if request.operation is PreviewOperation.RENAME:
            rename_target = request.target
            if rename_target is None:
                rename_target = (
                    RenameTarget.NAME
                    if request.symbol_name is not None and not request.strict
                    else RenameTarget.POSITION
                )
            target["target"] = rename_target.value
            if rename_target is RenameTarget.NAME:
                symbol_name = request.symbol_name
                if symbol_name is None:
                    raise EditorPreviewError("rename-by-name requires symbol_name")
                symbols = await resolver.execute_capability(
                    LspCapability.WORKSPACE_SYMBOL,
                    bundle,
                    path=relative,
                    arguments={"query": symbol_name},
                    semantic_mode=request.semantic_mode,
                    dependency_paths=tuple(
                        path for path in sorted(bundle.contents) if path != relative
                    ),
                )
                if symbols.status is not FrameStatus.COMPLETE or symbols.generation is None:
                    reason = ", ".join(symbols.omitted_reasons) or "symbol lookup failed"
                    raise EditorPreviewError(reason)
                session = resolver.editor_session_context(symbols.generation)
                location = _select_named_symbol(
                    symbols,
                    path=(root / relative).resolve().as_uri(),
                    symbol_name=symbol_name,
                    symbol_kind=request.symbol_kind,
                )
                try:
                    line, column = user_position_from_lsp(
                        bundle.contents[relative],
                        line=location.range.start.line,
                        character=location.range.start.character,
                        position_encoding=session.position_encoding,
                    )
                except LspPayloadError as exc:
                    raise EditorPreviewError(str(exc)) from exc
                target["resolved_line"] = line
                target["resolved_column"] = column
            return (
                (
                    LspCapability.RENAME_STRICT
                    if request.strict or rename_target is RenameTarget.POSITION
                    else LspCapability.RENAME
                ),
                line,
                column,
                {"newName": request.new_name or ""},
                target,
            )
        if request.operation is PreviewOperation.FORMAT_DOCUMENT:
            return LspCapability.FORMAT_DOCUMENT, 1, 1, {}, target

        end_line = request.end_line or line
        end_column = request.end_column or column
        user_range: dict[str, object] = {
            "start": {"line": line, "column": column},
            "end": {"line": end_line, "column": end_column},
        }
        if request.operation is PreviewOperation.FORMAT_RANGE:
            return (
                LspCapability.FORMAT_RANGE,
                line,
                column,
                {"userRange": user_range},
                target,
            )
        return (
            LspCapability.CODE_ACTIONS,
            line,
            column,
            {
                "userRange": user_range,
                "context": {"diagnostics": []},
            },
            target,
        )

    async def doctor(self, *, probe: bool = False) -> ResponseEnvelope:
        self._guard()
        workspace = self._workspaces.get(self._workspaces.workspace_ids[0])
        catalog_status = self._frames.catalog_status(workspace.workspace_id)
        storage_path = catalog_status["path"]
        repository_local = isinstance(
            storage_path,
            str,
        ) and catalog_path_is_repository_local(workspace.root, Path(storage_path))
        report = await doctor_report(
            root=workspace.root,
            workspace_id=workspace.workspace_id,
            config=self._config,
            product_version=product_version(),
            structural_worker_started=self.structural_worker_started,
            catalog_status=catalog_status,
            probe=probe,
        )
        report["storage"] = {
            **cast(dict[str, Any], report["storage"]),
            "repository_local": repository_local,
        }
        return self._ok(data=report, workspace_id=workspace.workspace_id)

    async def benchmark(self) -> ResponseEnvelope:
        """Run the package-owned reproducible benchmark contract."""
        self._guard()
        workspace = self._workspaces.get(self._workspaces.workspace_ids[0])

        async def describe_once() -> object:
            return await self.describe(DescribeRequest(workspace_id=workspace.workspace_id))

        report = await benchmark_report(
            root=workspace.root,
            startup_probe=describe_once,
            product_version=product_version(),
        )
        return self._ok(data=report, workspace_id=workspace.workspace_id)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._previews.clear()
        await self._mcp_health.aclose()
        await self._catalog_indexer.aclose()
        await self._frames.aclose()

    async def start(self) -> None:
        """Initialize and publish lifecycle-owned SQLite generations once."""
        self._guard()
        if self._started:
            return
        await self._catalog_indexer.start()
        await self._mcp_health.start()
        self._started = True

    async def ensure_full_catalog(self) -> None:
        """Promote an authority cold-start lifespan before a non-authority tool read."""
        self._guard()
        if not self._started:
            await self.start()
        if self._catalog_indexer.publication_profile is not CatalogPublicationProfile.FULL:
            await self._catalog_indexer.promote_to_full()
        await self._catalog_indexer.settle()

    @staticmethod
    def _context_terms(objective: str) -> tuple[str, ...]:
        normalized = "".join(
            character if character.isalnum() or character in "._/-:@" else " "
            for character in objective.casefold()
        )
        terms: list[str] = []
        for candidate in normalized.split():
            term = candidate.strip("._/-:@")
            if len(term) < 3 or term in _CONTEXT_STOP_WORDS or term in terms:
                continue
            terms.append(term)
            if len(terms) == _MAX_CONTEXT_TERMS:
                break
        if terms:
            return tuple(terms)
        fallback = normalized.strip(" ._/-:@")
        return (fallback,) if fallback else ()

    @staticmethod
    def _select_context_sources(
        rows: Sequence[FactRow],
        *,
        path_prefixes: tuple[str, ...],
        limit: int,
    ) -> tuple[FactRow, ...]:
        selected: list[FactRow] = []
        selected_ids: set[str] = set()
        for prefix in path_prefixes:
            for row in rows:
                path = row.evidence.path
                if path != prefix and not path.startswith(f"{prefix.rstrip('/')}/"):
                    continue
                selected.append(row)
                selected_ids.add(row.evidence.evidence_id)
                break
            if len(selected) == limit:
                return tuple(selected)
        for row in rows:
            if row.evidence.evidence_id in selected_ids:
                continue
            selected.append(row)
            if len(selected) == limit:
                break
        return tuple(selected)

    @classmethod
    def _select_context_facts(
        cls,
        rows: Sequence[FactRow],
        *,
        limit: int,
    ) -> tuple[FactRow, ...]:
        """Preserve available owner, consumer, conflict, validation, and constraint evidence."""
        if limit <= 0:
            return ()
        section_order = (
            ContextSection.CANONICAL_OWNER,
            ContextSection.CONSUMER,
            ContextSection.CONFLICT,
            ContextSection.VALIDATION_ROUTE,
            ContextSection.CONSTRAINT,
            ContextSection.SUPPORTING_FACT,
        )
        selected: list[FactRow] = []
        selected_ids: set[str] = set()
        for section in section_order:
            candidate = next(
                (
                    row
                    for row in rows
                    if row.evidence.evidence_id not in selected_ids
                    and cls._context_section(row.table) is section
                ),
                None,
            )
            if candidate is None:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.evidence.evidence_id)
            if len(selected) == limit:
                return tuple(selected)
        for row in rows:
            if row.evidence.evidence_id in selected_ids:
                continue
            selected.append(row)
            if len(selected) == limit:
                break
        return tuple(selected)

    @staticmethod
    def _context_fact_gaps(rows: Sequence[FactRow]) -> tuple[ContextGap, ...]:
        gaps: list[ContextGap] = []
        for row in rows:
            if row.table != "repository.engines" or row.data.get("available") is not False:
                continue
            identity = row.data.get("package_name") or row.data.get("engine_id") or "engine"
            raw_reasons: object = row.data.get("omitted_reasons")
            reasons = (
                tuple(str(reason) for reason in cast(Sequence[object], raw_reasons))
                if isinstance(raw_reasons, (list, tuple))
                else ()
            )
            detail = f": {'; '.join(reasons)}" if reasons else ""
            gaps.append(
                ContextGap(
                    code="runtime_identity_unavailable",
                    message=f"{identity} runtime identity is unavailable{detail}",
                    table=row.table,
                    path=row.evidence.path,
                )
            )
        return tuple(gaps)

    @staticmethod
    def _bounded_context_references(
        references: Sequence[ContextReference],
        *,
        max_bytes: int,
    ) -> tuple[tuple[ContextReference, ...], tuple[ContextGap, ...], int]:
        remaining = max_bytes
        selected: list[ContextReference] = []
        gaps: list[ContextGap] = []
        consumed = 0
        for reference in references:
            if reference.error is not None:
                selected.append(reference)
                gaps.append(
                    ContextGap(
                        code="resource_unavailable",
                        message=f"{reference.uri}: {reference.error}",
                    )
                )
                continue
            encoded = reference.content.encode("utf-8")
            bounded = encoded[:remaining]
            content = bounded.decode("utf-8", errors="ignore")
            truncated = reference.truncated or len(bounded) < len(encoded)
            selected_reference = reference.model_copy(
                update={
                    "content": content,
                    "sha256": reference.sha256 or hashlib.sha256(encoded).hexdigest(),
                    "truncated": truncated,
                }
            )
            selected.append(selected_reference)
            consumed += len(content.encode("utf-8"))
            remaining = max(0, max_bytes - consumed)
            if truncated:
                gaps.append(
                    ContextGap(
                        code="resource_content_limit",
                        message=(
                            f"{reference.uri}: configured resource content was partially "
                            "omitted or truncated"
                        ),
                    )
                )
        return tuple(selected), tuple(gaps), consumed

    @staticmethod
    def _context_section(table: str) -> ContextSection:
        if table == "source.context":
            return ContextSection.SOURCE
        if table in {
            "authority.entrypoints",
            "authority.owners",
            "authority.policies",
            "entrypoint_candidates",
        }:
            return ContextSection.CANONICAL_OWNER
        if table in {
            "authority.bindings",
            "derived.consumers",
            "framework.registrations",
            "repository.dependencies",
            "repository.imports",
            "repository.routes",
            "syntax.call_sites",
            "syntax.imports",
            "syntax.references",
        }:
            return ContextSection.CONSUMER
        if table == "authority.conflicts":
            return ContextSection.CONFLICT
        if table in {
            "repository.configurations",
            "repository.engines",
            "repository.rules",
            "repository.typescript_routes",
            "syntax.declarations",
            "syntax.exports",
        }:
            return ContextSection.CONSTRAINT
        if table in {
            "coverage",
            "quality.diagnostics",
            "quality.standards",
            "repository.diagnostics",
            "repository.scripts",
            "tests",
        }:
            return ContextSection.VALIDATION_ROUTE
        return ContextSection.SUPPORTING_FACT

    @staticmethod
    def _task_context_item(
        row: FactRow,
        *,
        relation_distance: int,
    ) -> TaskContextItem:
        salient: list[str] = []
        for key, value in row.data.items():
            if isinstance(value, (str, int, float, bool)) and str(value):
                salient.append(f"{key}={value}")
            if len(salient) == 3:
                break
        summary = row.table if not salient else f"{row.table}: {', '.join(salient)}"
        return TaskContextItem(
            table=row.table,
            section=SoleauxService._context_section(row.table),
            identity=row.evidence.evidence_id,
            summary=summary[:1024],
            data=row.data,
            evidence_id=row.evidence.evidence_id,
            path=row.evidence.path,
            start_line=row.evidence.range.start_line,
            end_line=row.evidence.range.end_line,
            relation_distance=relation_distance,
        )

    @staticmethod
    def _task_context_packet(
        *,
        request: ContextRequest,
        paths: tuple[str, ...],
        terms: tuple[str, ...],
        retrieval_engine: str,
        items: tuple[TaskContextItem, ...],
        external_references: tuple[ContextReference, ...],
        gaps: tuple[ContextGap, ...],
        ranked_candidate_count: int,
        related_fact_count: int,
        response_truncated: bool,
        coverage_complete: bool,
    ) -> TaskContextPacket:
        sections = {
            section: tuple(item for item in items if item.section is section)
            for section in ContextSection
        }
        unique_gaps = tuple(gap for index, gap in enumerate(gaps) if gap not in gaps[:index])
        return TaskContextPacket(
            objective=request.objective,
            paths=paths,
            terms=terms,
            retrieval_engine=retrieval_engine,
            relation_depth=_CONTEXT_RELATION_DEPTH,
            sources=sections[ContextSection.SOURCE],
            canonical_owners=sections[ContextSection.CANONICAL_OWNER],
            consumers=sections[ContextSection.CONSUMER],
            constraints=sections[ContextSection.CONSTRAINT],
            conflicts=sections[ContextSection.CONFLICT],
            validation_routes=sections[ContextSection.VALIDATION_ROUTE],
            supporting_facts=sections[ContextSection.SUPPORTING_FACT],
            external_references=external_references,
            gaps=unique_gaps,
            ranked_candidate_count=ranked_candidate_count,
            related_fact_count=related_fact_count,
            returned_item_count=len(items),
            response_truncated=response_truncated,
            coverage_complete=coverage_complete and not unique_gaps,
        )

    def _select(self, workspace_id: str | None) -> WorkspaceRoot:
        self._guard()
        return self._workspaces.get(workspace_id)

    def _guard(self) -> None:
        if self._closed:
            raise ServiceClosedError("SoleauxService is closed")

    def _relative_path(self, workspace: WorkspaceRoot, path: str) -> str:
        return RepositoryPath.admit(workspace, path).value

    def _search_path_prefixes(
        self,
        workspace: WorkspaceRoot,
        paths: Sequence[str],
    ) -> tuple[str, ...]:
        normalized: set[str] = set()
        for path in paths:
            if Path(path).is_absolute() or path.lower().startswith("file://"):
                raise ValueError("search paths must be repository-relative")
            relative = self._relative_path(workspace, path)
            if relative == ".":
                return ()
            normalized.add(relative)

        selected: list[str] = []
        for relative in sorted(
            normalized,
            key=lambda candidate: (candidate.count("/"), candidate),
        ):
            if any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in selected):
                continue
            selected.append(relative)
        return tuple(sorted(selected))

    @staticmethod
    def _roots_from_config(
        root: Path,
        config: ResolvedConfig,
    ) -> tuple[tuple[str, Path], ...]:
        if not config.workspaces:
            return (("workspace", root),)
        return tuple(
            (
                workspace.id,
                (
                    Path(workspace.root)
                    if Path(workspace.root).is_absolute()
                    else root / workspace.root
                ),
            )
            for workspace in config.workspaces
        )

    def _search_suggestions(
        self,
        workspace: WorkspaceRoot,
        request: SearchRequest,
        rows: Sequence[FactRow],
    ) -> tuple[SuggestedRequest, ...]:
        if not rows:
            return ()

        suggestions: list[SuggestedRequest] = []
        common_args: dict[str, object] = {
            "semantic_mode": request.semantic_mode.value,
        }
        if request.workspace_id is not None:
            common_args["workspace_id"] = request.workspace_id

        first_path = next(
            (path for row in rows if isinstance((path := row.data.get("path")), str) and path),
            None,
        )
        if first_path is not None:
            suggestions.append(
                SuggestedRequest(
                    tool="context",
                    args={
                        "objective": request.query,
                        "paths": [first_path],
                        **common_args,
                    },
                )
            )

        symbol_row = next(
            (
                row
                for row in rows
                if row.data.get("kind") == "symbol"
                and row.data.get("coverage") != "semantic"
                and isinstance(row.data.get("name"), str)
                and isinstance(row.data.get("path"), str)
            ),
            None,
        )
        if symbol_row is not None:
            suggestions.append(
                SuggestedRequest(
                    tool="navigate",
                    args={
                        "operation": "definition",
                        "path": symbol_row.data["path"],
                        "line": symbol_row.evidence.range.start_line,
                        "column": symbol_row.evidence.range.start_column,
                        "semantic_mode": "semantic_required",
                        **{
                            key: value
                            for key, value in common_args.items()
                            if key != "semantic_mode"
                        },
                    },
                )
            )

        policy_row = next(
            (row for row in rows if row.data.get("kind") == "policy"),
            None,
        )
        if policy_row is not None and isinstance(policy_row.data.get("title"), str):
            suggestions.append(
                SuggestedRequest(
                    tool="owners",
                    args={"policy": policy_row.data["title"], **common_args},
                )
            )

        return tuple(suggestions)

    def _semantic_response(
        self,
        workspace: WorkspaceRoot,
        bundle: SnapshotBundle,
        resolution: CapabilityResolution,
        *,
        suggested: Sequence[SuggestedRequest] = (),
    ) -> ResponseEnvelope:
        self._frames.record_semantic_resolution(bundle, resolution)
        return self._ok(
            data=resolution.model_dump(mode="json"),
            workspace_id=workspace.workspace_id,
            snapshot_id=bundle.snapshot.snapshot_id,
            warnings=list(resolution.omitted_reasons),
            suggested=suggested,
        )

    def _ownership_record(
        self,
        selected_policy: FactRow,
        *,
        binding_catalog: Sequence[FactRow],
        conflict_catalog: Sequence[FactRow],
        path_prefixes: tuple[str, ...],
        frame: AnalysisFrame,
    ) -> tuple[dict[str, Any], tuple[FactRow, ...], tuple[str, ...]]:
        policy_id = selected_policy.data.get("policy_id")
        if not isinstance(policy_id, str):
            raise ValueError("materialized ownership policy has no string policy_id")
        binding_rows = tuple(
            row
            for row in binding_catalog
            if row.data.get("policy_id") == policy_id
            and self._ownership_row_in_scope(
                row,
                path_prefixes=path_prefixes,
            )
        )
        declared_roles = tuple(
            sorted(
                {
                    role
                    for row in binding_rows
                    if row.data.get("binding_kind") == GovernanceBindingKind.DECLARED.value
                    and isinstance((role := row.data.get("role")), str)
                }
            )
        )
        binding_ids = {
            role: [
                binding_id
                for row in binding_rows
                if row.data.get("role") == role
                and isinstance((binding_id := row.data.get("binding_id")), str)
            ]
            for role in declared_roles
        }
        evidence_binding_ids = [
            binding_id
            for row in binding_rows
            if row.data.get("binding_kind") == GovernanceBindingKind.EVIDENCE.value
            and isinstance((binding_id := row.data.get("binding_id")), str)
        ]
        raw_required_roles: object = selected_policy.data.get("required_roles", ())
        required_roles = (
            tuple(
                role for role in cast(Sequence[object], raw_required_roles) if isinstance(role, str)
            )
            if isinstance(raw_required_roles, (list, tuple))
            else ()
        )
        missing_roles = [
            role
            for role in required_roles
            if not any(
                row.data.get("role") == role
                and row.data.get("state") != GovernanceState.MISSING_TARGET.value
                for row in binding_rows
            )
        ]
        visible_binding_ids = {
            binding_id
            for row in binding_rows
            if isinstance((binding_id := row.data.get("binding_id")), str)
        }
        conflict_rows = tuple(
            row
            for row in conflict_catalog
            if row.data.get("policy_id") == policy_id
            and row.data.get("binding_id") in visible_binding_ids
        )
        conflict_ids = tuple(
            sorted(
                {
                    conflict_id
                    for row in conflict_rows
                    if isinstance((conflict_id := row.data.get("conflict_id")), str)
                }
            )
        )
        incomplete_binding = any(
            row.data.get("state")
            in {
                GovernanceState.MISSING_TARGET.value,
                GovernanceState.UNVERIFIED.value,
            }
            for row in binding_rows
        )
        if conflict_ids:
            state = OwnershipDecisionState.CONFLICTED
        elif missing_roles or incomplete_binding:
            state = OwnershipDecisionState.INCOMPLETE
        else:
            state = OwnershipDecisionState.RESOLVED

        warnings = list(
            self._ownership_scope_warnings(
                frame,
                policy_rows=(selected_policy,),
                binding_rows=binding_rows,
                selector=policy_id,
            )
        )
        present_roles = {
            role for row in binding_rows if isinstance((role := row.data.get("role")), str)
        }
        warnings.extend(
            f"{policy_id}: required governance role {role!r} is outside the requested "
            "path scope or has no declared target"
            for role in missing_roles
            if role not in present_roles
        )
        scoped_warnings = tuple(dict.fromkeys(warnings))
        rows = (selected_policy, *binding_rows, *conflict_rows)
        status = self._ownership_coverage_status(rows, scoped_warnings)
        record = {
            "state": state.value,
            "policy": selected_policy.data,
            "binding_ids": binding_ids,
            "evidence_binding_ids": evidence_binding_ids,
            "missing_roles": missing_roles,
            "conflict_ids": list(conflict_ids),
            "coverage": {
                "status": status.value,
                "omitted_reasons": list(scoped_warnings),
            },
        }
        return record, rows, scoped_warnings

    def _ownership_scope_warnings(
        self,
        frame: AnalysisFrame,
        *,
        policy_rows: Sequence[FactRow],
        binding_rows: Sequence[FactRow],
        selector: str,
    ) -> tuple[str, ...]:
        policy_ids = {
            policy_id
            for row in policy_rows
            if isinstance((policy_id := row.data.get("policy_id")), str)
        }
        source_paths = {
            source_path
            for row in policy_rows
            if isinstance((source_path := row.data.get("source_path")), str)
        }
        configured_sources = tuple(
            source
            for source in self._config.governance.sources
            if source.path in source_paths or source.path == selector
        )
        source_ids = {source.id for source in configured_sources}
        warnings: list[str] = []
        for warning in frame.warnings:
            if warning.startswith("catalog_"):
                warnings.append(warning)
                continue
            if any(policy_id in warning for policy_id in policy_ids):
                warnings.append(warning)
                continue
            if not warning.startswith("governance_"):
                continue
            if source_ids:
                if any(f"source {source_id!r}" in warning for source_id in source_ids):
                    warnings.append(warning)
            elif not policy_rows:
                warnings.append(warning)

        for row in binding_rows:
            state = row.data.get("state")
            if state not in {
                GovernanceState.MISSING_TARGET.value,
                GovernanceState.UNVERIFIED.value,
            }:
                continue
            target = row.data.get("target")
            source_path = row.data.get("source_path")
            if not isinstance(target, str) or not isinstance(source_path, str):
                continue
            qualifier = (
                "unresolved" if state == GovernanceState.MISSING_TARGET.value else "unverified"
            )
            warnings.append(
                f"{source_path}:{row.evidence.range.start_line}: "
                f"{qualifier} governance target {target}"
            )
        return tuple(dict.fromkeys(warnings))

    @staticmethod
    def _ownership_coverage_status(
        rows: Sequence[FactRow],
        warnings: Sequence[str],
    ) -> FrameStatus:
        incomplete_statuses = {
            ResolutionStatus.CANDIDATE,
            ResolutionStatus.UNAVAILABLE,
            ResolutionStatus.UNRESOLVED,
        }
        if warnings or any(row.evidence.resolution_status in incomplete_statuses for row in rows):
            return FrameStatus.PARTIAL
        return FrameStatus.COMPLETE

    def _ownership_scope_frame(
        self,
        frame: AnalysisFrame,
        *,
        rows: Sequence[FactRow],
        warnings: tuple[str, ...],
    ) -> AnalysisFrame:
        status = self._ownership_coverage_status(rows, warnings)
        selected_paths = {row.evidence.path for row in rows}
        resolution_statuses = [row.evidence.resolution_status for row in rows]
        limits = frame.coverage.row_file_byte_depth_limits.model_copy(
            update={
                "max_rows": max(len(rows), 1),
                "max_files": max(len(selected_paths), 1),
                "max_depth": 1,
            }
        )
        coverage = frame.coverage.model_copy(
            update={
                "status": status,
                "eligible_files": len(selected_paths),
                "examined_files": len(selected_paths),
                "parse_failures": sum(
                    warning.startswith("governance_parser_failed:") for warning in warnings
                ),
                "candidate_count": resolution_statuses.count(ResolutionStatus.CANDIDATE),
                "resolution_attempts": sum(row.table == "authority.bindings" for row in rows),
                "resolved_count": resolution_statuses.count(ResolutionStatus.RESOLVED),
                "unsupported_count": resolution_statuses.count(ResolutionStatus.UNAVAILABLE),
                "failed_count": resolution_statuses.count(ResolutionStatus.UNRESOLVED),
                "omitted_reasons": warnings,
                "row_file_byte_depth_limits": limits,
            }
        )
        return frame.model_copy(
            update={
                "coverage": coverage,
                "tables": {},
                "warnings": warnings,
            }
        )

    @staticmethod
    def _ownership_row_in_scope(
        row: FactRow,
        *,
        path_prefixes: tuple[str, ...],
    ) -> bool:
        if not path_prefixes:
            return True
        target = row.data.get("target")
        candidates = [row.evidence.path]
        if isinstance(target, str) and not target.startswith("command:"):
            candidates.append(target.partition("#")[0])
        return any(
            candidate == prefix or candidate.startswith(f"{prefix}/")
            for candidate in candidates
            for prefix in path_prefixes
        )

    @staticmethod
    def _ownership_identity(policy: dict[str, Any]) -> dict[str, str]:
        policy_id = policy.get("policy_id")
        source_path = policy.get("source_path")
        if not isinstance(policy_id, str) or not isinstance(source_path, str):
            raise ValueError("materialized ownership policy has no stable identity")
        return {
            "policy_id": policy_id,
            "source_path": source_path,
        }

    @classmethod
    def _ownership_record_fragment(
        cls,
        record: dict[str, Any],
        rows: Sequence[FactRow],
    ) -> dict[str, Any]:
        raw_policy: object = record.get("policy")
        if not isinstance(raw_policy, dict):
            raise ValueError("materialized ownership decision has no policy")
        policy = cast(dict[str, Any], raw_policy)
        identity = cls._ownership_identity(policy)
        policy_id = identity["policy_id"]
        selected = tuple(row for row in rows if row.data.get("policy_id") == policy_id)

        raw_binding_ids: object = record.get("binding_ids", {})
        roles = (
            tuple(
                sorted(
                    role
                    for role in cast(dict[object, object], raw_binding_ids)
                    if isinstance(role, str)
                )
            )
            if isinstance(raw_binding_ids, dict)
            else ()
        )
        binding_ids: dict[str, list[str]] = {role: [] for role in roles}
        evidence_binding_ids: list[str] = []
        conflict_ids: set[str] = set()
        for row in selected:
            if row.table == "authority.bindings":
                binding_id = row.data.get("binding_id")
                if not isinstance(binding_id, str):
                    continue
                if row.data.get("binding_kind") == GovernanceBindingKind.DECLARED.value:
                    role = row.data.get("role")
                    if isinstance(role, str):
                        binding_ids.setdefault(role, []).append(binding_id)
                elif row.data.get("binding_kind") == GovernanceBindingKind.EVIDENCE.value:
                    evidence_binding_ids.append(binding_id)
            elif row.table == "authority.conflicts":
                conflict_id = row.data.get("conflict_id")
                if isinstance(conflict_id, str):
                    conflict_ids.add(conflict_id)

        fragment = {
            "state": record.get("state"),
            "policy": identity,
            "binding_ids": binding_ids,
            "evidence_binding_ids": evidence_binding_ids,
            "missing_roles": record.get("missing_roles", []),
            "conflict_ids": sorted(conflict_ids),
        }
        if "coverage" in record:
            fragment["coverage"] = record["coverage"]
        return fragment

    @staticmethod
    def _ownership_response_row(row: FactRow) -> FactRow:
        """Remove relation-wide fields that cannot be repeated in bounded pages."""
        data = dict(row.data)
        if row.table in {"authority.policies", "authority.bindings"}:
            data.pop("attributes", None)
        elif row.table == "authority.conflicts":
            data.pop("competing_binding_ids", None)
        return row.model_copy(update={"data": data})

    @classmethod
    def _ownership_page_data(
        cls,
        data: dict[str, Any],
        rows: Sequence[FactRow],
        *,
        view: OwnershipView,
    ) -> dict[str, Any]:
        if view is OwnershipView.IDENTITIES:
            return {
                "view": view.value,
                "identities": [
                    cls._ownership_identity(row.data)
                    for row in rows
                    if row.table == "authority.policies"
                ],
            }

        page_data = {
            "view": view.value,
            "decision_metadata_scope": "page",
        }
        raw_ownerships: object = data.get("ownerships")
        if isinstance(raw_ownerships, list):
            ownerships: list[dict[str, Any]] = []
            for raw_record in cast(list[object], raw_ownerships):
                if isinstance(raw_record, dict):
                    ownerships.append(cast(dict[str, Any], raw_record))
            selected_policy_ids = {
                policy_id
                for row in rows
                if isinstance((policy_id := row.data.get("policy_id")), str)
            }
            fragments = [
                cls._ownership_record_fragment(record, rows)
                for record in ownerships
                if cls._ownership_identity(cast(dict[str, Any], record["policy"]))["policy_id"]
                in selected_policy_ids
            ]
            return {
                **page_data,
                "state": data.get("state"),
                "policy": None,
                "binding_ids": {},
                "evidence_binding_ids": [],
                "missing_roles": [],
                "conflict_ids": [],
                "candidates": [fragment["policy"] for fragment in fragments],
                "ownerships": fragments,
            }

        if isinstance(data.get("policy"), dict):
            fragment = cls._ownership_record_fragment(data, rows)
            return {
                **page_data,
                **fragment,
                "candidates": [],
            }
        return {
            **page_data,
            **data,
        }

    def _bounded_ownership_response(
        self,
        frame: AnalysisFrame,
        *,
        rows: Sequence[FactRow],
        data: dict[str, Any],
        view: OwnershipView,
        limit: int,
        offset: int,
        query_digest: str,
        catalog_generation: int,
        publication_revision: int,
        total_rows: int | None = None,
        rows_offset: int = 0,
    ) -> ResponseEnvelope:
        effective_total_rows = len(rows) if total_rows is None else total_rows
        if (
            effective_total_rows < 0
            or rows_offset < 0
            or rows_offset > offset
            or offset > effective_total_rows
            or (offset > 0 and offset == effective_total_rows)
        ):
            raise CursorError("cursor offset is outside this ownership result")
        relative_offset = offset - rows_offset
        if relative_offset > len(rows):
            raise CursorError("cursor offset is outside this ownership read window")
        available = tuple(rows[relative_offset : relative_offset + limit])
        if offset < effective_total_rows and not available:
            raise CursorError("ownership read window cannot make cursor progress")
        evaluated: dict[int, tuple[ResponseEnvelope, CursorPayload | None, int]] = {}

        def build_page(
            count: int,
        ) -> tuple[ResponseEnvelope, CursorPayload | None, int]:
            cached = evaluated.get(count)
            if cached is not None:
                return cached
            selected = tuple(self._ownership_response_row(row) for row in available[:count])
            next_offset = offset + count
            response_truncated = next_offset < effective_total_rows
            next_payload: CursorPayload | None = None
            next_cursor: str | None = None
            if response_truncated and selected:
                next_payload = CursorPayload(
                    process_epoch=self._cursors.process_epoch,
                    workspace_id=frame.workspace_id,
                    snapshot_id=frame.snapshot_id,
                    query_digest=query_digest,
                    limit=limit,
                    offset=next_offset,
                )
                next_cursor = self._cursors.preview(next_payload)
            bounded_frame = frame
            if response_truncated:
                reason = "ownership response row or byte limit reached"
                coverage = frame.coverage.model_copy(
                    update={
                        "status": FrameStatus.TRUNCATED,
                        "omitted_reasons": tuple(
                            dict.fromkeys((*frame.coverage.omitted_reasons, reason))
                        ),
                    }
                )
                bounded_frame = frame.model_copy(
                    update={
                        "coverage": coverage,
                        "warnings": tuple(dict.fromkeys((*frame.warnings, reason))),
                    }
                )
            payload = {
                **self._ownership_page_data(data, selected, view=view),
                "returned_rows": count,
                "total_rows": effective_total_rows,
                "response_truncated": response_truncated,
            }
            envelope = self._from_frame(
                bounded_frame,
                rows=tuple(selected),
                data=payload,
                next_cursor=next_cursor,
            )
            serialized_size = len(envelope.model_dump_json().encode("utf-8"))
            built = (envelope, next_payload, serialized_size)
            evaluated[count] = built
            return built

        def persist_cursor(
            envelope: ResponseEnvelope,
            next_payload: CursorPayload | None,
        ) -> ResponseEnvelope:
            if next_payload is None:
                return envelope
            stored_cursor = self._cursors.encode(
                next_payload,
                catalog_generation=catalog_generation,
                publication_revision=publication_revision,
            )
            return envelope.model_copy(update={"next_cursor": stored_cursor})

        if not available:
            envelope, next_payload, serialized_size = build_page(0)
            if serialized_size < MAX_OWNERSHIP_RESPONSE_BYTES:
                return persist_cursor(envelope, next_payload)
            return self._error(
                "ownership_response_too_large",
                "ownership identity exceeds the 64 KiB response contract",
                workspace_id=frame.workspace_id,
            )

        full_count = len(available)
        full_envelope, full_payload, full_size = build_page(full_count)
        if full_size <= _OWNERSHIP_RESPONSE_TARGET_BYTES:
            return persist_cursor(full_envelope, full_payload)

        low = 1
        high = full_count - 1
        best: tuple[ResponseEnvelope, CursorPayload | None] | None = None
        while low <= high:
            middle = (low + high) // 2
            envelope, next_payload, serialized_size = build_page(middle)
            if serialized_size <= _OWNERSHIP_RESPONSE_TARGET_BYTES:
                best = (envelope, next_payload)
                low = middle + 1
            else:
                high = middle - 1
        if best is not None:
            return persist_cursor(*best)

        single_envelope, single_payload, single_size = build_page(1)
        if single_size < MAX_OWNERSHIP_RESPONSE_BYTES:
            return persist_cursor(single_envelope, single_payload)
        return self._error(
            "ownership_response_too_large",
            "ownership page cannot make progress within the 64 KiB response contract",
            workspace_id=frame.workspace_id,
        )

    def _from_frame(
        self,
        frame: AnalysisFrame,
        *,
        rows: Sequence[FactRow] | None = None,
        data: dict[str, Any] | None = None,
        next_cursor: str | None = None,
        suggested: Sequence[SuggestedRequest] = (),
    ) -> ResponseEnvelope:
        selected_rows = (
            tuple(row for table_rows in frame.tables.values() for row in table_rows)
            if rows is None
            else tuple(rows)
        )
        return ResponseEnvelope(
            product_version=product_version(),
            request_id=uuid4().hex,
            workspace_id=frame.workspace_id,
            snapshot_id=frame.snapshot_id,
            status=ResultStatus.OK,
            data=data,
            rows=[{"table": row.table, **row.data} for row in selected_rows],
            evidence=[row.evidence for row in selected_rows],
            coverage=frame.coverage,
            warnings=compact_response_warnings(frame.warnings),
            next_cursor=next_cursor,
            suggested_next_requests=list(suggested),
        )

    def _context_from_frame(
        self,
        frame: AnalysisFrame,
        *,
        data: TaskContextPacket,
    ) -> TaskContextEnvelope:
        return TaskContextEnvelope(
            product_version=product_version(),
            request_id=uuid4().hex,
            workspace_id=frame.workspace_id,
            snapshot_id=frame.snapshot_id,
            status=ResultStatus.OK,
            data=data,
            coverage=frame.coverage,
            warnings=compact_response_warnings(frame.warnings),
        )

    def _shrink_context_envelope(
        self,
        frame: AnalysisFrame,
        packet: TaskContextPacket,
        *,
        max_bytes: int,
    ) -> TaskContextEnvelope | None:
        """Drop optional packet content until the serialized envelope fits max_bytes.

        Supporting facts and sources shed from the tail first, then configured
        external references — they remain fetchable by URI — then constraints;
        required semantic sections keep at least their first item. Returns None
        when even the minimal required packet cannot fit.
        """
        droppable = (
            *reversed(packet.supporting_facts),
            *reversed(packet.sources),
            *reversed(packet.external_references),
            *reversed(packet.constraints),
            *reversed(packet.validation_routes[1:]),
            *reversed(packet.conflicts[1:]),
            *reversed(packet.consumers[1:]),
            *reversed(packet.canonical_owners[1:]),
        )

        def build(removed: int) -> tuple[TaskContextEnvelope, int]:
            dropped_entries = droppable[:removed]
            dropped = {id(entry) for entry in dropped_entries}
            dropped_items = sum(isinstance(entry, TaskContextItem) for entry in dropped_entries)
            dropped_references = removed - dropped_items

            def keep(
                items: tuple[TaskContextItem, ...],
            ) -> tuple[TaskContextItem, ...]:
                return tuple(item for item in items if id(item) not in dropped)

            gaps = tuple(gap for gap in packet.gaps if gap.code != "response_byte_limit")
            if dropped_references:
                gaps = (
                    *gaps,
                    ContextGap(
                        code="resource_content_limit",
                        message=(
                            f"{dropped_references} configured resource(s) were omitted "
                            "at the response byte limit; fetch them directly by URI."
                        ),
                    ),
                )
            byte_gap = ContextGap(
                code="response_byte_limit",
                message=(
                    "The serialized response byte budget omitted task-related items; "
                    "increase max_bytes or narrow the objective."
                ),
            )
            gaps = (
                (*gaps[: MAX_PACKET_GAPS - 1], byte_gap)
                if len(gaps) >= MAX_PACKET_GAPS
                else (*gaps, byte_gap)
            )
            candidate = packet.model_copy(
                update={
                    "sources": keep(packet.sources),
                    "canonical_owners": keep(packet.canonical_owners),
                    "consumers": keep(packet.consumers),
                    "constraints": keep(packet.constraints),
                    "conflicts": keep(packet.conflicts),
                    "validation_routes": keep(packet.validation_routes),
                    "supporting_facts": keep(packet.supporting_facts),
                    "external_references": tuple(
                        reference
                        for reference in packet.external_references
                        if id(reference) not in dropped
                    ),
                    "gaps": gaps,
                    "returned_item_count": len(packet.items) - dropped_items,
                    "response_truncated": True,
                    "coverage_complete": False,
                }
            )
            envelope = self._context_from_frame(frame, data=candidate)
            reason = "context response byte limit reached"
            if envelope.coverage is not None:
                merged = tuple(dict.fromkeys((*envelope.coverage.omitted_reasons, reason)))
                if len(merged) > MAX_OMITTED_REASONS:
                    merged = (*merged[: MAX_OMITTED_REASONS - 1], reason)
                envelope = envelope.model_copy(
                    update={
                        "coverage": envelope.coverage.model_copy(
                            update={
                                "status": (
                                    FrameStatus.TRUNCATED
                                    if envelope.coverage.status is FrameStatus.COMPLETE
                                    else envelope.coverage.status
                                ),
                                "omitted_reasons": merged,
                            }
                        )
                    }
                )
            return envelope, len(envelope.model_dump_json().encode("utf-8"))

        low = 0
        high = len(droppable)
        best: TaskContextEnvelope | None = None
        while low <= high:
            middle = (low + high) // 2
            envelope, serialized_size = build(middle)
            if serialized_size <= max_bytes:
                best = envelope
                high = middle - 1
            else:
                low = middle + 1
        return best

    def _context_error(
        self,
        error_type: str,
        message: str,
        *,
        workspace_id: str | None = None,
        retryable: bool = False,
    ) -> TaskContextEnvelope:
        return TaskContextEnvelope(
            product_version=product_version(),
            request_id=uuid4().hex,
            workspace_id=workspace_id,
            status=ResultStatus.ERROR,
            error=ErrorDetail(
                error_type=error_type,
                message=message,
                retryable=retryable,
            ),
        )

    def _ok(
        self,
        *,
        data: dict[str, Any],
        workspace_id: str | None = None,
        snapshot_id: str | None = None,
        warnings: list[str] | None = None,
        suggested: Sequence[SuggestedRequest] = (),
    ) -> ResponseEnvelope:
        return ResponseEnvelope(
            product_version=product_version(),
            request_id=uuid4().hex,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            status=ResultStatus.OK,
            data=data,
            warnings=compact_response_warnings(warnings or ()),
            suggested_next_requests=list(suggested),
        )

    def _error(
        self,
        error_type: str,
        message: str,
        *,
        workspace_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> ResponseEnvelope:
        return ResponseEnvelope(
            product_version=product_version(),
            request_id=uuid4().hex,
            workspace_id=workspace_id,
            status=ResultStatus.ERROR,
            data=data,
            error=ErrorDetail(
                error_type=error_type,
                message=message,
                retryable=False,
            ),
        )


def compact_response_warnings(warnings: Sequence[str]) -> list[str]:
    group_order: list[str] = []
    group_counts: dict[str, int] = {}
    group_paths: dict[str, list[str]] = {}
    for warning in warnings:
        reason, path = _warning_reason_and_path(warning)
        if reason not in group_counts:
            group_order.append(reason)
            group_counts[reason] = 0
            group_paths[reason] = []
        group_counts[reason] += 1
        samples = group_paths[reason]
        if path is not None and len(samples) < _MAX_WARNING_PATH_SAMPLES:
            samples.append(_bounded_warning_path(path))

    rendered = [
        _render_warning_group(
            reason,
            count=group_counts[reason],
            paths=group_paths[reason],
        )
        for reason in group_order
    ]
    if len(rendered) <= MAX_RESPONSE_WARNINGS:
        return rendered

    visible_count = MAX_RESPONSE_WARNINGS - 1
    omitted_reasons = group_order[visible_count:]
    omitted_warning_count = sum(group_counts[reason] for reason in omitted_reasons)
    omitted_summary = (
        f"omitted {len(omitted_reasons)} warning groups covering {omitted_warning_count} warnings"
    )
    return [*rendered[:visible_count], omitted_summary]


def _warning_reason_and_path(warning: str) -> tuple[str, str | None]:
    for prefix in _PATH_WARNING_PREFIXES:
        if warning.startswith(prefix):
            return prefix.rstrip(), warning.removeprefix(prefix)
    return warning, None


def _bounded_warning_path(path: str) -> str:
    if len(path) <= _MAX_WARNING_PATH_CHARS:
        return path
    suffix = "..."
    return f"{path[: _MAX_WARNING_PATH_CHARS - len(suffix)]}{suffix}"


def _render_warning_group(reason: str, *, count: int, paths: Sequence[str]) -> str:
    if paths:
        rendered = f"{reason}: {count} occurrences; sample paths: {', '.join(paths)}"
        omitted_paths = count - len(paths)
        if omitted_paths:
            rendered = f"{rendered}; {omitted_paths} paths omitted"
    elif count > 1:
        rendered = f"{reason}: {count} occurrences"
    else:
        rendered = reason
    if len(rendered) <= MAX_WARNING_CHARS:
        return rendered
    suffix = " [truncated]"
    return f"{rendered[: MAX_WARNING_CHARS - len(suffix)].rstrip()}{suffix}"


def _select_named_symbol(
    resolution: CapabilityResolution,
    *,
    path: str,
    symbol_name: str,
    symbol_kind: str | None,
) -> LspLocation:
    try:
        match_set = resolve_named_symbols(
            resolution,
            name=symbol_name,
            kind=symbol_kind,
            path=path,
        )
    except LspPayloadError as exc:
        raise EditorPreviewError(str(exc)) from exc
    matches = match_set.candidates
    if match_set.truncated or len(matches) != 1:
        positions = [
            {
                "line": match.location.range.start.line + 1,
                "column": match.location.range.start.character + 1,
            }
            for match in matches
        ]
        raise EditorPreviewError(
            f"rename-by-name requires exactly one matching symbol; candidates={positions}"
        )
    return matches[0].location


def _workspace_edit_from_payload(
    request: PreviewEditRequest,
    raw_payload: object,
    *,
    uri: str,
) -> object:
    payload = normalize_json_payload(raw_payload)
    if request.operation is PreviewOperation.CODE_ACTION:
        if not isinstance(payload, list):
            raise EditorPreviewError("code-action provider returned no action list")
        action_index = request.action_index
        if action_index is None or action_index >= len(payload):
            raise EditorPreviewError("selected code action is unavailable")
        action = payload[action_index]
        if not isinstance(action, dict):
            raise EditorPreviewError("selected code action is malformed")
        if action.get("disabled") is not None:
            raise EditorPreviewError("selected code action is disabled")
        if action.get("command") is not None:
            raise EditorPreviewError("code-action command execution is unsupported")
        edit = action.get("edit")
        if not isinstance(edit, dict):
            raise EditorPreviewError("selected code action has no contained WorkspaceEdit")
        return edit
    if request.operation in {
        PreviewOperation.FORMAT_DOCUMENT,
        PreviewOperation.FORMAT_RANGE,
    }:
        if not isinstance(payload, list):
            raise EditorPreviewError("format provider returned no TextEdit list")
        return {"changes": {uri: payload}}
    if not isinstance(payload, dict):
        raise EditorPreviewError("rename provider returned no WorkspaceEdit")
    return payload
