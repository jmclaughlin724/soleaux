"""Disposable private SQLite projection for catalog generations and FTS."""

from __future__ import annotations

import collections.abc
import contextlib
import dataclasses
import datetime
import enum
import json
import os
import pathlib
import sqlite3
import typing

import platformdirs
import pydantic

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

import soleaux.catalog.contracts
import soleaux.catalog.generation
import soleaux.catalog.search
import soleaux.contracts.config
import soleaux.contracts.coverage
import soleaux.contracts.frame
import soleaux.contracts.governance
import soleaux.contracts.repository
import soleaux.contracts.requests
import soleaux.contracts.snapshot

SCHEMA_VERSION = 13

_SEED_RELATION_TABLES = {
    "symbol": frozenset(
        {
            "repository.symbols",
            "semantic.symbols",
            "semantic.definitions",
            "semantic.references",
            "semantic.implementations",
        }
    ),
    "route": frozenset({"repository.routes"}),
    "diagnostic": frozenset({"repository.diagnostics", "quality.diagnostics"}),
    "import": frozenset({"repository.imports"}),
}


def _is_object_dict(value: object) -> typing.TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_object_list(value: object) -> typing.TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_object_sequence(value: object) -> typing.TypeGuard[collections.abc.Sequence[object]]:
    return isinstance(value, (list, tuple))


class CatalogStoreError(Exception):
    """SQLite projection cannot be used safely."""


class CatalogReadError(CatalogStoreError):
    """A materialized catalog generation is not readable."""

    def __init__(self, error_type: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable


class CatalogLifecycleState(enum.StrEnum):
    BUILDING = "building"
    READY = "ready"
    RECONCILING = "reconciling"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclasses.dataclass(frozen=True)
class MaterializedRow:
    """One SQLite-ranked row and its stored relation distance."""

    row: soleaux.contracts.frame.FactRow
    fact_key: str
    kind: str
    score: float
    relation_distance: int


@dataclasses.dataclass(frozen=True)
class MaterializedRead:
    """One transaction-pinned read from the active readable generation."""

    generation: int
    publication_revision: int
    snapshot_id: str
    source_fingerprint: str
    state: CatalogLifecycleState
    frame: soleaux.contracts.frame.AnalysisFrame
    rows: tuple[MaterializedRow, ...]
    has_more: bool
    total_rows: int
    total_rows_exact: bool
    published_tables: tuple[str, ...]
    retrieval_engine: str


@dataclasses.dataclass(frozen=True)
class MaterializedPublication:
    """Persisted identity and completeness of one active materialized generation."""

    generation: int
    publication_revision: int
    snapshot_id: str
    source_fingerprint: str
    state: CatalogLifecycleState
    semantic_mode: soleaux.contracts.requests.SemanticMode
    coverage: soleaux.contracts.coverage.Coverage
    enrichment_settled: bool
    row_count: int
    published_tables: tuple[str, ...]
    attempted_tables: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedMaterializedPublication:
    """CPU-prepared rows for one short materialized SQLite transaction."""

    workspace_id: str
    generation: int
    snapshot_id: str
    source_fingerprint: str
    semantic_mode: soleaux.contracts.requests.SemanticMode
    coverage_json: str
    enrichment_settled: bool
    warnings_json: str
    context_rows: tuple[tuple[object, ...], ...]
    seed_rows: tuple[tuple[object, ...], ...]
    ownership_rows: tuple[tuple[object, ...], ...]
    fts_rows: tuple[tuple[object, ...], ...]
    relationship_rows: tuple[tuple[object, ...], ...]
    published_tables_json: str
    attempted_tables_json: str


def _materialized_tables_key(workspace_id: str, generation: int) -> str:
    return f"materialized_tables:{workspace_id}:{generation}"


def _materialized_attempted_tables_key(workspace_id: str, generation: int) -> str:
    return f"materialized_attempted_tables:{workspace_id}:{generation}"


def _materialized_config_digest_key(workspace_id: str, generation: int) -> str:
    return f"materialized_config_digest:{workspace_id}:{generation}"


def _row_text(row: soleaux.contracts.frame.FactRow, field: str) -> str:
    value = row.data.get(field)
    return value if isinstance(value, str) else ""


def _seed_index_keys(row: soleaux.contracts.frame.FactRow, *, kind: str) -> tuple[str, ...]:
    keys = {f"path:{row.evidence.path}"}
    project_id = _row_text(row, "project_id")
    if project_id:
        keys.add(f"project:{project_id}")
    canonical_key = soleaux.catalog.search.canonical_fact_key_for_row(row, kind=kind)
    if ":" in canonical_key:
        keys.add(canonical_key)
    return tuple(sorted(keys))


def _seed_key_filter(
    generation: int,
    seed_keys: collections.abc.Sequence[str],
) -> tuple[str, tuple[object, ...]]:
    direct_keys: set[str] = set()
    dependency_projects: set[str] = set()
    relation_projects: dict[str, set[str]] = {kind: set() for kind in _SEED_RELATION_TABLES}
    for key in seed_keys:
        kind, separator, value = key.partition(":")
        if not separator or not value:
            continue
        direct_keys.add(key)
        if kind == "dependency":
            project_id, nested_separator, package_name = value.rpartition(":")
            if nested_separator and project_id and package_name:
                direct_keys.add(key)
                dependency_projects.add(project_id)
            continue
        if kind in _SEED_RELATION_TABLES:
            project_id, nested_separator, identifier = value.rpartition(":")
            if nested_separator and identifier:
                direct_keys.add(f"{kind}:{identifier}")
                if project_id:
                    relation_projects[kind].add(project_id)

    clauses: list[str] = []
    parameters: list[object] = [generation]
    if direct_keys:
        ordered_keys = tuple(sorted(direct_keys))
        placeholders = ",".join("?" for _key in ordered_keys)
        clauses.append(
            "seed_rows.row_key IN ("
            "SELECT row_key FROM context_seed_keys "
            f"WHERE generation = ? AND seed_key IN ({placeholders})"
            ")"
        )
        parameters.extend((generation, *ordered_keys))
    if dependency_projects:
        ordered_projects = tuple(sorted(dependency_projects))
        placeholders = ",".join("?" for _project in ordered_projects)
        clauses.append(f"(seed_rows.table_name <> ? AND seed_rows.project_id IN ({placeholders}))")
        parameters.extend(("repository.dependencies", *ordered_projects))
    for kind, projects in relation_projects.items():
        if not projects:
            continue
        ordered_projects = tuple(sorted(projects))
        project_placeholders = ",".join("?" for _project in ordered_projects)
        tables = tuple(sorted(_SEED_RELATION_TABLES[kind]))
        table_placeholders = ",".join("?" for _table in tables)
        clauses.append(
            f"(seed_rows.table_name NOT IN ({table_placeholders}) "
            f"AND seed_rows.project_id IN ({project_placeholders}))"
        )
        parameters.extend((*tables, *ordered_projects))
    if not clauses:
        return "0", ()
    return (
        "row_key IN ("
        "SELECT seed_rows.row_key FROM context_rows AS seed_rows "
        f"WHERE seed_rows.generation = ? AND ({' OR '.join(clauses)})"
        ")",
        tuple(parameters),
    )


def _ownership_selector_rows(
    generation: int,
    rows: collections.abc.Sequence[soleaux.contracts.frame.FactRow],
) -> tuple[tuple[int, str, str, str], ...]:
    selectors: set[tuple[int, str, str, str]] = set()
    for row in rows:
        policy_id = _row_text(row, "policy_id")
        if not policy_id:
            continue
        if row.table == "authority.policies":
            selectors.add((generation, "policy_id", policy_id, policy_id))
            paths = {_row_text(row, "source_path")}
            raw_scope = row.data.get("scope", ())
            if _is_object_sequence(raw_scope):
                paths.update(item for item in raw_scope if isinstance(item, str))
            selectors.update((generation, "path", path, policy_id) for path in paths if path)
            aliases = {
                _row_text(row, "title"),
                _row_text(row, "identity_value"),
            }
            raw_aliases = row.data.get("aliases", ())
            if _is_object_sequence(raw_aliases):
                aliases.update(item for item in raw_aliases if isinstance(item, str))
            selectors.update(
                (generation, "alias", normalized, policy_id)
                for value in aliases
                if value
                and (
                    normalized := soleaux.contracts.governance.normalize_governance_identity(value)
                )
            )
        elif row.table == "authority.bindings":
            selectors.update(
                (generation, "path", path, policy_id)
                for field in ("source_path", "target")
                if (path := _row_text(row, field))
            )
    return tuple(sorted(selectors))


def _ownership_selector_filter(
    generation: int,
    selector: str,
) -> tuple[str, tuple[object, ...]]:
    normalized = soleaux.contracts.governance.normalize_governance_identity(selector)
    return (
        "row_key IN ("
        "SELECT ownership_rows.row_key FROM context_rows AS ownership_rows "
        "WHERE ownership_rows.generation = ? "
        "AND ownership_rows.policy_id IN ("
        "SELECT selector_rows.policy_id FROM ownership_selectors AS selector_rows "
        "WHERE selector_rows.generation = ? AND ("
        "(selector_rows.selector_kind = 'policy_id' "
        "AND selector_rows.selector_value = ?) "
        "OR (selector_rows.selector_kind = 'path' "
        "AND selector_rows.selector_value = ? "
        "AND NOT EXISTS ("
        "SELECT 1 FROM ownership_selectors AS exact_rows "
        "WHERE exact_rows.generation = ? "
        "AND exact_rows.selector_kind = 'policy_id' "
        "AND exact_rows.selector_value = ?"
        ")) "
        "OR (selector_rows.selector_kind = 'alias' "
        "AND selector_rows.selector_value = ? "
        "AND NOT EXISTS ("
        "SELECT 1 FROM ownership_selectors AS exact_rows "
        "WHERE exact_rows.generation = ? "
        "AND exact_rows.selector_kind = 'policy_id' "
        "AND exact_rows.selector_value = ?"
        ") "
        "AND NOT EXISTS ("
        "SELECT 1 FROM ownership_selectors AS path_rows "
        "WHERE path_rows.generation = ? "
        "AND path_rows.selector_kind = 'path' "
        "AND path_rows.selector_value = ?"
        "))"
        ")"
        ")"
        ")",
        (
            generation,
            generation,
            selector,
            selector,
            generation,
            selector,
            normalized,
            generation,
            selector,
            generation,
            selector,
        ),
    )


def catalog_database_path(
    workspace_root: pathlib.Path,
    source_fingerprint: str | None = None,
    *,
    storage_namespace: str | None = None,
) -> pathlib.Path:
    """Map workspace and optional content identity to a private database path."""
    identity = soleaux.contracts.repository.content_digest(
        str(workspace_root.resolve(strict=False)).encode("utf-8")
    )
    directory = platformdirs.user_cache_path("soleaux", appauthor=False) / "catalogs" / identity
    if storage_namespace is not None:
        if (
            not storage_namespace
            or storage_namespace in {".", ".."}
            or pathlib.Path(storage_namespace).name != storage_namespace
        ):
            raise ValueError("catalog storage namespace must be one path segment")
        directory /= storage_namespace
    filename = (
        f"{source_fingerprint}.sqlite3" if source_fingerprint is not None else "unbound.sqlite3"
    )
    return directory / filename


def catalog_path_is_repository_local(workspace_root: pathlib.Path, path: pathlib.Path) -> bool:
    """Return whether one resolved catalog path is equal to or below the workspace."""
    root = workspace_root.resolve(strict=False)
    candidate = path.resolve(strict=False)
    return candidate == root or candidate.is_relative_to(root)


_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")
_DISK_LEASE_SUFFIX = ".lease"


def _is_disk_generation_path(path: pathlib.Path) -> bool:
    return (
        path.suffix == ".sqlite3"
        and len(path.stem) == 64
        and all(character in "0123456789abcdef" for character in path.stem)
    )


def _disk_generation_candidates(directory: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return tuple(
        sorted(
            (path for path in directory.iterdir() if _is_disk_generation_path(path)),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    )


def _disk_generation_files(path: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return (path, *(pathlib.Path(f"{path}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES))


def _disk_generation_lease_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(f"{path}{_DISK_LEASE_SUFFIX}")


class _DiskGenerationLease:
    """One process-held advisory lease for a disk generation."""

    def __init__(self, path: pathlib.Path, descriptor: int) -> None:
        self.path = path
        self._descriptor: int | None = descriptor

    @classmethod
    def acquire(
        cls, database_path: pathlib.Path, *, exclusive: bool
    ) -> _DiskGenerationLease | None:
        backend = _fcntl
        if backend is None:
            raise CatalogStoreError(
                "disk catalog generation leases require an advisory-lock backend"
            )
        path = _disk_generation_lease_path(database_path)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            operation = backend.LOCK_EX if exclusive else backend.LOCK_SH
            backend.flock(descriptor, operation | backend.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        except OSError:
            os.close(descriptor)
            raise
        return cls(path, descriptor)

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        backend = _fcntl
        if backend is not None:
            with contextlib.suppress(OSError):
                backend.flock(descriptor, backend.LOCK_UN)
        os.close(descriptor)


def _disk_generation_size(path: pathlib.Path) -> int:
    size = 0
    for candidate in _disk_generation_files(path):
        try:
            size += candidate.stat().st_size
        except FileNotFoundError:
            continue
    return size


def _remove_disk_generation(path: pathlib.Path) -> bool:
    lease = _DiskGenerationLease.acquire(path, exclusive=True)
    if lease is None:
        return False
    try:
        for candidate in _disk_generation_files(path):
            candidate.unlink(missing_ok=True)
        lease.path.unlink(missing_ok=True)
    finally:
        lease.close()
    return True


def _remove_orphan_disk_sidecars(directory: pathlib.Path) -> None:
    for sidecar in directory.iterdir():
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            if not sidecar.name.endswith(suffix):
                continue
            database = directory / sidecar.name[: -len(suffix)]
            if _is_disk_generation_path(database) and not database.exists():
                _remove_disk_generation(database)
            break


class CatalogStore:
    """Short-transaction SQLite writer and read-only FTS projection."""

    def __init__(
        self,
        workspace_root: pathlib.Path,
        *,
        mode: soleaux.contracts.config.CatalogMode = soleaux.contracts.config.CatalogMode.MEMORY,
        path: pathlib.Path | None = None,
        storage_namespace: str | None = None,
        config_digest: str | None = None,
        retained_generations: int = 2,
        max_disk_size_mb: int = 512,
    ) -> None:
        self._workspace_root = workspace_root.resolve(strict=False)
        self._requested_mode = mode
        self._mode = (
            soleaux.contracts.config.CatalogMode.DISK
            if mode is soleaux.contracts.config.CatalogMode.AUTO
            else mode
        )
        self._path_is_explicit = path is not None
        self._storage_namespace = storage_namespace
        self._path = path or self._implicit_database_path()
        self._config_digest = config_digest or soleaux.contracts.repository.content_digest(b"")
        self._retained_generations = retained_generations
        self._max_disk_size_bytes = max_disk_size_mb * 1024 * 1024
        self._connection: sqlite3.Connection | None = None
        self._disk_lease: _DiskGenerationLease | None = None
        self._fts_available = False
        self._fallback_reason: str | None = None
        self._disk_gc_ready = False

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    @property
    def path(self) -> pathlib.Path | None:
        if self._mode in {
            soleaux.contracts.config.CatalogMode.MEMORY,
            soleaux.contracts.config.CatalogMode.OFF,
        }:
            return None
        return self._path

    @property
    def mode(self) -> soleaux.contracts.config.CatalogMode:
        """Return the effective mode after any automatic fallback."""
        return self._mode

    @property
    def requested_mode(self) -> soleaux.contracts.config.CatalogMode:
        return self._requested_mode

    @property
    def storage_namespace(self) -> str | None:
        return self._storage_namespace

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    def open(self) -> None:
        if self._mode is soleaux.contracts.config.CatalogMode.OFF or self._connection is not None:
            return
        if self._mode is soleaux.contracts.config.CatalogMode.DISK:
            self._disk_gc_ready = False
            try:
                self._validate_disk_path()
            except CatalogStoreError as exc:
                if self._can_fallback:
                    self._fallback_to_memory("catalog open failed", exc)
                    return
                raise
        database = (
            ":memory:"
            if self._mode is soleaux.contracts.config.CatalogMode.MEMORY
            else str(self._path)
        )
        try:
            if database != ":memory:":
                self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._path.parent.chmod(0o700)
                lease = _DiskGenerationLease.acquire(self._path, exclusive=False)
                if lease is None:
                    raise CatalogStoreError("catalog generation is leased for cleanup")
                self._disk_lease = lease
            connection = sqlite3.connect(
                database,
                timeout=0.25,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 250")
            if database != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._connection = connection
            self._initialize()
            if database != ":memory:":
                self._path.chmod(0o600)
                self._disk_gc_ready = True
        except (CatalogStoreError, OSError, sqlite3.Error) as exc:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()
            self._release_disk_lease()
            if self._can_fallback:
                self._fallback_to_memory("catalog open failed", exc)
                return
            if isinstance(exc, CatalogStoreError):
                raise
            raise CatalogStoreError(f"cannot open catalog store: {exc}") from exc

    def _initialize(self) -> None:
        connection = self._require_connection()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        current = connection.execute(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
        ).fetchone()
        if current is None or current[0] != str(SCHEMA_VERSION):
            connection.executescript(
                """
                DROP TABLE IF EXISTS context_fts;
                DROP TABLE IF EXISTS ownership_selectors;
                DROP TABLE IF EXISTS context_seed_keys;
                DROP TABLE IF EXISTS relationships;
                DROP TABLE IF EXISTS context_rows;
                DROP TABLE IF EXISTS catalog_state;
                DROP TABLE IF EXISTS facts_fts;
                DROP TABLE IF EXISTS chunks_fts;
                DROP TABLE IF EXISTS chunks;
                DROP TABLE IF EXISTS tasks;
                DROP TABLE IF EXISTS scripts;
                DROP TABLE IF EXISTS configs;
                DROP TABLE IF EXISTS typescript_routes;
                DROP TABLE IF EXISTS engines;
                DROP TABLE IF EXISTS changes;
                DROP TABLE IF EXISTS diagnostics;
                DROP TABLE IF EXISTS imports;
                DROP TABLE IF EXISTS symbols;
                DROP TABLE IF EXISTS policies;
                DROP TABLE IF EXISTS rules;
                DROP TABLE IF EXISTS routes;
                DROP TABLE IF EXISTS dependencies;
                DROP TABLE IF EXISTS projects;
                DELETE FROM catalog_meta;
                """
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS catalog_state (
                workspace_id TEXT PRIMARY KEY,
                active_generation INTEGER,
                publication_revision INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL,
                snapshot_id TEXT,
                source_fingerprint TEXT,
                semantic_mode TEXT,
                coverage TEXT,
                enrichment_settled INTEGER NOT NULL DEFAULT 0,
                warnings TEXT NOT NULL,
                dirty_since TEXT,
                validated_at TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS context_rows (
                generation INTEGER NOT NULL,
                row_key TEXT NOT NULL,
                table_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                policy_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                PRIMARY KEY (generation, row_key)
            );
            CREATE INDEX IF NOT EXISTS context_rows_table
                ON context_rows(generation, table_name);
            CREATE INDEX IF NOT EXISTS context_rows_kind
                ON context_rows(generation, kind);
            CREATE INDEX IF NOT EXISTS context_rows_path
                ON context_rows(generation, path);
            CREATE INDEX IF NOT EXISTS context_rows_policy
                ON context_rows(generation, policy_id, table_name);
            CREATE INDEX IF NOT EXISTS context_rows_project
                ON context_rows(generation, project_id, table_name);
            CREATE TABLE IF NOT EXISTS context_seed_keys (
                generation INTEGER NOT NULL,
                row_key TEXT NOT NULL,
                seed_key TEXT NOT NULL,
                PRIMARY KEY (generation, row_key, seed_key)
            );
            CREATE INDEX IF NOT EXISTS context_seed_keys_lookup
                ON context_seed_keys(generation, seed_key, row_key);
            CREATE TABLE IF NOT EXISTS ownership_selectors (
                generation INTEGER NOT NULL,
                selector_kind TEXT NOT NULL,
                selector_value TEXT NOT NULL,
                policy_id TEXT NOT NULL,
                PRIMARY KEY (generation, selector_kind, selector_value, policy_id)
            );
            CREATE INDEX IF NOT EXISTS ownership_selectors_policy
                ON ownership_selectors(generation, policy_id);
            CREATE TABLE IF NOT EXISTS relationships (
                generation INTEGER NOT NULL,
                relationship_id TEXT NOT NULL,
                source_row_key TEXT NOT NULL,
                target_row_key TEXT NOT NULL,
                basis TEXT NOT NULL,
                PRIMARY KEY (generation, relationship_id)
            );
            CREATE INDEX IF NOT EXISTS relationships_source
                ON relationships(generation, source_row_key);
            CREATE INDEX IF NOT EXISTS relationships_target
                ON relationships(generation, target_row_key);
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dependencies (
                project_id TEXT NOT NULL,
                package_name TEXT NOT NULL,
                scope TEXT NOT NULL,
                usage TEXT NOT NULL,
                source_path TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (project_id, package_name, scope, usage, source_path)
            );
            CREATE INDEX IF NOT EXISTS dependencies_package
                ON dependencies(package_name);
            CREATE TABLE IF NOT EXISTS configs (
                project_id TEXT NOT NULL,
                config_path TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (project_id, config_path)
            );
            CREATE TABLE IF NOT EXISTS scripts (
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (project_id, name)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                project_id TEXT NOT NULL,
                runner TEXT NOT NULL,
                task_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (project_id, runner, task_id)
            );
            CREATE TABLE IF NOT EXISTS engines (
                project_id TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (project_id, engine_id)
            );
            CREATE TABLE IF NOT EXISTS typescript_routes (
                project_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routes (
                route_id TEXT PRIMARY KEY,
                project_id TEXT,
                source_path TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS routes_project ON routes(project_id);
            CREATE TABLE IF NOT EXISTS rules (
                rule_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policies (
                policy_id TEXT NOT NULL,
                governance_source_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (governance_source_id, policy_id)
            );
            CREATE TABLE IF NOT EXISTS symbols (
                revision_id TEXT PRIMARY KEY,
                symbol_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS symbols_path ON symbols(path);
            CREATE TABLE IF NOT EXISTS imports (
                import_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                resolved_path TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS imports_project ON imports(project_id);
            CREATE INDEX IF NOT EXISTS imports_resolved ON imports(resolved_path);
            CREATE TABLE IF NOT EXISTS diagnostics (
                diagnostic_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS diagnostics_path ON diagnostics(path);
            CREATE TABLE IF NOT EXISTS changes (
                change_id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL,
                path TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS changes_generation ON changes(generation);
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                source_digest TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                text TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            """
        )
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
                "fact_key UNINDEXED, kind UNINDEXED, path, title, body, "
                "tokenize=\"unicode61 tokenchars '_$@'\")"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5("
                "generation UNINDEXED, row_key UNINDEXED, table_name UNINDEXED, "
                "kind UNINDEXED, path, title, body, "
                "tokenize=\"unicode61 tokenchars '_$@'\")"
            )
        except sqlite3.OperationalError:
            self._fts_available = False
        else:
            self._fts_available = True

    def publish(
        self,
        generation: soleaux.catalog.generation.CatalogGeneration,
        *,
        previous_fingerprint: str | None = None,
        changed_paths: frozenset[str] | None = None,
    ) -> None:
        """Replace one projection in a single short optimistic transaction."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return
        self._bind_disk_generation(generation.source_fingerprint)
        self.open()
        connection = self._require_connection()
        metadata = self.metadata()
        incremental_chunks = (
            changed_paths is not None
            and previous_fingerprint is not None
            and metadata.get("source_fingerprint") == previous_fingerprint
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "projects",
                "dependencies",
                "configs",
                "scripts",
                "tasks",
                "engines",
                "typescript_routes",
                "routes",
                "rules",
                "policies",
                "symbols",
                "imports",
                "diagnostics",
                "changes",
            ):
                connection.execute(f"DELETE FROM {table}")
            if incremental_chunks and changed_paths:
                placeholders = ",".join("?" for _path in changed_paths)
                ordered_paths = tuple(sorted(changed_paths))
                connection.execute(
                    f"DELETE FROM chunks WHERE path IN ({placeholders})",
                    ordered_paths,
                )
            elif not incremental_chunks:
                connection.execute("DELETE FROM chunks")
            # Non-chunk documents are always reinserted below, so their FTS rows
            # are always wiped first — including an incremental republish with
            # zero changed paths (the structural-enrichment republish).
            if self._fts_available:
                if incremental_chunks:
                    connection.execute("DELETE FROM facts_fts WHERE kind <> 'chunk'")
                    connection.execute(
                        "DELETE FROM facts_fts WHERE kind = 'chunk' AND fact_key NOT IN "
                        "(SELECT 'chunk:' || chunk_id FROM chunks)"
                    )
                else:
                    connection.execute("DELETE FROM facts_fts")
            connection.executemany(
                "INSERT INTO projects(project_id, source_path, payload) VALUES (?, ?, ?)",
                (
                    (
                        row.project_id,
                        row.source_path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.projects
                ),
            )
            connection.executemany(
                "INSERT INTO dependencies"
                "(project_id, package_name, scope, usage, source_path, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        row.project_id,
                        row.package_name,
                        row.scope.value,
                        row.usage.value,
                        row.source_path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.dependencies
                ),
            )
            connection.executemany(
                "INSERT INTO configs(project_id, config_path, payload) VALUES (?, ?, ?)",
                (
                    (row.project_id, row.config_path, row.model_dump_json())
                    for row in generation.facts.configs
                ),
            )
            connection.executemany(
                "INSERT INTO scripts(project_id, name, payload) VALUES (?, ?, ?)",
                (
                    (row.project_id, row.name, row.model_dump_json())
                    for row in generation.facts.scripts
                ),
            )
            connection.executemany(
                "INSERT INTO tasks(project_id, runner, task_id, payload) VALUES (?, ?, ?, ?)",
                (
                    (row.project_id, row.runner, row.task_id, row.model_dump_json())
                    for row in generation.facts.tasks
                ),
            )
            connection.executemany(
                "INSERT INTO engines(project_id, engine_id, payload) VALUES (?, ?, ?)",
                (
                    (row.project_id, row.engine_id, row.model_dump_json())
                    for row in generation.facts.engines
                ),
            )
            connection.executemany(
                "INSERT INTO typescript_routes(project_id, payload) VALUES (?, ?)",
                (
                    (row.project_id, row.model_dump_json())
                    for row in generation.facts.typescript_routes
                ),
            )
            connection.executemany(
                "INSERT INTO routes(route_id, project_id, source_path, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    (
                        row.route_id,
                        row.project_id,
                        row.source_path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.routes
                ),
            )
            connection.executemany(
                "INSERT INTO rules(rule_id, source_path, payload) VALUES (?, ?, ?)",
                (
                    (row.rule_id, row.source_path, row.model_dump_json())
                    for row in generation.facts.rules
                ),
            )
            connection.executemany(
                "INSERT INTO policies"
                "(policy_id, governance_source_id, source_path, payload) VALUES (?, ?, ?, ?)",
                (
                    (
                        row.policy_id,
                        row.governance_source_id,
                        row.source_path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.policies
                ),
            )
            connection.executemany(
                "INSERT INTO symbols"
                "(revision_id, symbol_id, project_id, path, name, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        row.revision_id,
                        row.symbol_id,
                        row.project_id,
                        row.path,
                        row.name,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.symbols
                ),
            )
            connection.executemany(
                "INSERT INTO imports"
                "(import_id, project_id, path, resolved_path, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        row.import_id,
                        row.project_id,
                        row.path,
                        row.resolved_path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.imports
                ),
            )
            connection.executemany(
                "INSERT INTO diagnostics(diagnostic_id, project_id, path, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    (
                        row.diagnostic_id,
                        row.project_id,
                        row.path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.diagnostics
                ),
            )
            connection.executemany(
                "INSERT INTO changes(change_id, generation, path, payload) VALUES (?, ?, ?, ?)",
                (
                    (
                        row.change_id,
                        row.generation,
                        row.path,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.changes
                ),
            )
            connection.executemany(
                "INSERT INTO chunks"
                "(chunk_id, path, source_digest, start_line, end_line, text, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        row.chunk_id,
                        row.path,
                        row.source_digest,
                        row.start_line,
                        row.end_line,
                        row.text,
                        row.model_dump_json(),
                    )
                    for row in generation.facts.chunks
                    if not incremental_chunks
                    or (changed_paths is not None and row.path in changed_paths)
                ),
            )
            if self._fts_available:
                non_chunk_documents = tuple(
                    document
                    for document in soleaux.catalog.search.search_documents(generation)
                    if document.kind != "chunk"
                )
                indexed_chunk_documents = soleaux.catalog.search.chunk_documents(
                    row
                    for row in generation.facts.chunks
                    if not incremental_chunks
                    or (changed_paths is not None and row.path in changed_paths)
                )
                connection.executemany(
                    "INSERT INTO facts_fts(fact_key, kind, path, title, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        (
                            document.fact_key,
                            document.kind,
                            document.path,
                            document.title,
                            document.body,
                        )
                        for document in (*non_chunk_documents, *indexed_chunk_documents)
                    ),
                )
            metadata = {
                "schema_version": str(SCHEMA_VERSION),
                "workspace_root": str(self._workspace_root),
                "config_digest": self._config_digest,
                "workspace_id": generation.workspace_id,
                "generation": str(generation.number),
                "snapshot_id": generation.snapshot_id,
                "source_fingerprint": generation.source_fingerprint,
                "created_at": generation.created_at.isoformat(),
                "fts_available": json.dumps(self._fts_available),
                "snapshot": generation.snapshot.model_dump_json(),
                "inventory": json.dumps(generation.inventory, separators=(",", ":")),
                "inventory_signatures": json.dumps(
                    dict(generation.inventory_signatures),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "warnings": json.dumps(
                    generation.facts.warnings,
                    separators=(",", ":"),
                ),
            }
            connection.executemany(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            if self._can_fallback:
                self._fallback_to_memory("catalog publish failed", exc)
                self.publish(
                    generation,
                    previous_fingerprint=previous_fingerprint,
                    changed_paths=changed_paths,
                )
                return
            raise CatalogStoreError(f"catalog publish failed: {exc}") from exc
        self._gc_disk_generations(protected_path=self._path)

    def mark_building(self, workspace_id: str) -> None:
        """Record lifecycle work without replacing the last readable generation."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return
        self.open()
        connection = self._require_connection()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        try:
            connection.execute(
                """
                INSERT INTO catalog_state(
                    workspace_id, active_generation, state, warnings, dirty_since
                ) VALUES (?, NULL, ?, '[]', ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    state = ?,
                    dirty_since = COALESCE(catalog_state.dirty_since, excluded.dirty_since),
                    last_error = NULL
                """,
                (
                    workspace_id,
                    CatalogLifecycleState.BUILDING.value,
                    now,
                    CatalogLifecycleState.RECONCILING.value,
                ),
            )
        except sqlite3.Error as exc:
            raise CatalogStoreError(f"catalog lifecycle update failed: {exc}") from exc

    def publish_materialized(
        self,
        frame: soleaux.contracts.frame.AnalysisFrame,
        *,
        generation: int,
        source_fingerprint: str,
        rows: collections.abc.Sequence[soleaux.contracts.frame.FactRow],
        kinds: collections.abc.Mapping[str, str],
        relationships: collections.abc.Sequence[tuple[str, str, str]],
        retained_generations: int,
        enrichment_settled: bool = True,
        attempted_tables: collections.abc.Sequence[str] = (),
    ) -> None:
        """Publish one readable query generation and flip its pointer atomically."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return
        self.open()
        prepared = self.prepare_materialized(
            frame,
            generation=generation,
            source_fingerprint=source_fingerprint,
            rows=rows,
            kinds=kinds,
            relationships=relationships,
            enrichment_settled=enrichment_settled,
            attempted_tables=attempted_tables,
        )
        self.publish_prepared_materialized(
            prepared,
            retained_generations=retained_generations,
        )

    def prepare_materialized(
        self,
        frame: soleaux.contracts.frame.AnalysisFrame,
        *,
        generation: int,
        source_fingerprint: str,
        rows: collections.abc.Sequence[soleaux.contracts.frame.FactRow],
        kinds: collections.abc.Mapping[str, str],
        relationships: collections.abc.Sequence[tuple[str, str, str]],
        enrichment_settled: bool = True,
        attempted_tables: collections.abc.Sequence[str] = (),
    ) -> PreparedMaterializedPublication:
        """Serialize and index immutable rows without touching SQLite."""
        warnings = tuple(dict.fromkeys((*frame.warnings, *frame.coverage.omitted_reasons)))
        ordered_rows = tuple(
            sorted(
                rows,
                key=lambda item: (
                    item.table,
                    item.evidence.path,
                    item.evidence.range.start_line,
                    item.evidence.evidence_id,
                ),
            )
        )
        try:
            context_rows = tuple(
                (
                    generation,
                    row.evidence.evidence_id,
                    row.table,
                    kinds.get(row.evidence.evidence_id, "fact"),
                    row.evidence.path,
                    (
                        policy_id
                        if isinstance((policy_id := row.data.get("policy_id")), str)
                        else ""
                    ),
                    _row_text(row, "project_id"),
                    row.model_dump_json(),
                )
                for row in ordered_rows
            )
            seed_rows = tuple(
                (generation, row.evidence.evidence_id, seed_key)
                for row in ordered_rows
                for seed_key in _seed_index_keys(
                    row,
                    kind=kinds.get(row.evidence.evidence_id, "fact"),
                )
            )
            ownership_rows = _ownership_selector_rows(generation, ordered_rows)
            fts_rows = (
                tuple(
                    (
                        generation,
                        row.evidence.evidence_id,
                        row.table,
                        kinds.get(row.evidence.evidence_id, "fact"),
                        row.evidence.path,
                        row.table,
                        json.dumps(
                            row.data,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                    for row in ordered_rows
                )
                if self._fts_available
                else ()
            )
            relationship_rows = tuple(
                (
                    generation,
                    soleaux.contracts.repository.content_digest(
                        f"{generation}\0{source}\0{target}\0{basis}".encode()
                    ),
                    source,
                    target,
                    basis,
                )
                for source, target, basis in relationships
            )
            published_tables_json = json.dumps(
                sorted({*frame.tables, *(row.table for row in ordered_rows)}),
                separators=(",", ":"),
            )
            attempted_tables_json = json.dumps(
                sorted(set(attempted_tables)),
                separators=(",", ":"),
            )
            coverage_json = frame.coverage.model_dump_json()
            warnings_json = json.dumps(warnings, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise CatalogStoreError(f"materialized catalog preparation failed: {exc}") from exc
        return PreparedMaterializedPublication(
            workspace_id=frame.workspace_id,
            generation=generation,
            snapshot_id=frame.snapshot_id,
            source_fingerprint=source_fingerprint,
            semantic_mode=frame.semantic_mode,
            coverage_json=coverage_json,
            enrichment_settled=enrichment_settled,
            warnings_json=warnings_json,
            context_rows=context_rows,
            seed_rows=seed_rows,
            ownership_rows=ownership_rows,
            fts_rows=fts_rows,
            relationship_rows=relationship_rows,
            published_tables_json=published_tables_json,
            attempted_tables_json=attempted_tables_json,
        )

    def publish_prepared_materialized(
        self,
        prepared: PreparedMaterializedPublication,
        *,
        retained_generations: int,
    ) -> None:
        """Commit precomputed rows and atomically flip the active pointer."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return
        self.open()
        connection = self._require_connection()
        generation = prepared.generation
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM context_rows WHERE generation = ?", (generation,))
            connection.execute(
                "DELETE FROM context_seed_keys WHERE generation = ?",
                (generation,),
            )
            connection.execute(
                "DELETE FROM ownership_selectors WHERE generation = ?",
                (generation,),
            )
            connection.execute("DELETE FROM relationships WHERE generation = ?", (generation,))
            if self._fts_available:
                connection.execute("DELETE FROM context_fts WHERE generation = ?", (generation,))
            connection.executemany(
                """
                INSERT INTO context_rows(
                    generation, row_key, table_name, kind, path, policy_id, project_id, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                prepared.context_rows,
            )
            connection.executemany(
                """
                INSERT INTO context_seed_keys(generation, row_key, seed_key)
                VALUES (?, ?, ?)
                """,
                prepared.seed_rows,
            )
            connection.executemany(
                """
                INSERT INTO ownership_selectors(
                    generation, selector_kind, selector_value, policy_id
                ) VALUES (?, ?, ?, ?)
                """,
                prepared.ownership_rows,
            )
            if self._fts_available:
                connection.executemany(
                    """
                    INSERT INTO context_fts(
                        generation, row_key, table_name, kind, path, title, body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    prepared.fts_rows,
                )
            connection.executemany(
                """
                INSERT INTO relationships(
                    generation, relationship_id, source_row_key, target_row_key, basis
                ) VALUES (?, ?, ?, ?, ?)
                """,
                prepared.relationship_rows,
            )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES (?, ?)",
                (
                    _materialized_tables_key(prepared.workspace_id, generation),
                    prepared.published_tables_json,
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES (?, ?)",
                (
                    _materialized_attempted_tables_key(prepared.workspace_id, generation),
                    prepared.attempted_tables_json,
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES (?, ?)",
                (
                    _materialized_config_digest_key(prepared.workspace_id, generation),
                    self._config_digest,
                ),
            )
            now = datetime.datetime.now(datetime.UTC).isoformat()
            connection.execute(
                """
                INSERT INTO catalog_state(
                    workspace_id, active_generation, publication_revision, state, snapshot_id,
                    source_fingerprint, semantic_mode, coverage, enrichment_settled, warnings,
                    dirty_since, validated_at, last_error
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    active_generation = excluded.active_generation,
                    publication_revision = catalog_state.publication_revision + 1,
                    state = excluded.state,
                    snapshot_id = excluded.snapshot_id,
                    source_fingerprint = excluded.source_fingerprint,
                    semantic_mode = excluded.semantic_mode,
                    coverage = excluded.coverage,
                    enrichment_settled = excluded.enrichment_settled,
                    warnings = excluded.warnings,
                    dirty_since = NULL,
                    validated_at = excluded.validated_at,
                    last_error = NULL
                """,
                (
                    prepared.workspace_id,
                    generation,
                    CatalogLifecycleState.READY.value,
                    prepared.snapshot_id,
                    prepared.source_fingerprint,
                    prepared.semantic_mode.value,
                    prepared.coverage_json,
                    int(prepared.enrichment_settled),
                    prepared.warnings_json,
                    now,
                ),
            )
            active_generations = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT generation FROM context_rows ORDER BY generation DESC"
                ).fetchall()
            )
            obsolete = active_generations[max(1, retained_generations) :]
            for old_generation in obsolete:
                connection.execute(
                    "DELETE FROM context_rows WHERE generation = ?",
                    (old_generation,),
                )
                connection.execute(
                    "DELETE FROM context_seed_keys WHERE generation = ?",
                    (old_generation,),
                )
                connection.execute(
                    "DELETE FROM ownership_selectors WHERE generation = ?",
                    (old_generation,),
                )
                connection.execute(
                    "DELETE FROM relationships WHERE generation = ?",
                    (old_generation,),
                )
                if self._fts_available:
                    connection.execute(
                        "DELETE FROM context_fts WHERE generation = ?",
                        (old_generation,),
                    )
                connection.execute(
                    "DELETE FROM catalog_meta WHERE key = ?",
                    (
                        _materialized_tables_key(
                            prepared.workspace_id,
                            int(old_generation),
                        ),
                    ),
                )
                connection.execute(
                    "DELETE FROM catalog_meta WHERE key = ?",
                    (
                        _materialized_attempted_tables_key(
                            prepared.workspace_id,
                            int(old_generation),
                        ),
                    ),
                )
                connection.execute(
                    "DELETE FROM catalog_meta WHERE key = ?",
                    (
                        _materialized_config_digest_key(
                            prepared.workspace_id,
                            int(old_generation),
                        ),
                    ),
                )
            connection.execute("COMMIT")
        except (TypeError, ValueError, sqlite3.Error) as exc:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise CatalogStoreError(f"materialized catalog publish failed: {exc}") from exc

    def materialized_publication(
        self,
        workspace_id: str,
    ) -> MaterializedPublication | None:
        """Return persisted publication metadata without hydrating fact rows."""
        if self._mode is not soleaux.contracts.config.CatalogMode.DISK or self._connection is None:
            return None
        connection = self._require_connection()
        try:
            state_row = connection.execute(
                """
                SELECT active_generation, state, snapshot_id, source_fingerprint,
                       semantic_mode, coverage, enrichment_settled, publication_revision
                FROM catalog_state WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
            if state_row is None or state_row[0] is None:
                return None
            (
                raw_generation,
                raw_state,
                raw_snapshot_id,
                raw_source_fingerprint,
                raw_semantic_mode,
                raw_coverage,
                raw_enrichment_settled,
                raw_publication_revision,
            ) = state_row
            generation = int(raw_generation)
            config_row = connection.execute(
                "SELECT value FROM catalog_meta WHERE key = ?",
                (_materialized_config_digest_key(workspace_id, generation),),
            ).fetchone()
            if config_row is None or str(config_row[0]) != self._config_digest:
                return None
            metadata = self.metadata()
            if (
                metadata.get("workspace_id") != workspace_id
                or metadata.get("config_digest") != self._config_digest
                or metadata.get("generation") != str(generation)
                or metadata.get("snapshot_id") != raw_snapshot_id
                or metadata.get("source_fingerprint") != raw_source_fingerprint
            ):
                return None
            return MaterializedPublication(
                generation=generation,
                publication_revision=int(raw_publication_revision),
                snapshot_id=str(raw_snapshot_id),
                source_fingerprint=str(raw_source_fingerprint),
                state=CatalogLifecycleState(str(raw_state)),
                semantic_mode=soleaux.contracts.requests.SemanticMode(str(raw_semantic_mode)),
                coverage=soleaux.contracts.coverage.Coverage.model_validate_json(str(raw_coverage)),
                enrichment_settled=bool(raw_enrichment_settled),
                row_count=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_rows WHERE generation = ?",
                        (generation,),
                    ).fetchone()[0]
                ),
                published_tables=self._materialized_table_names(
                    connection,
                    workspace_id=workspace_id,
                    generation=generation,
                ),
                attempted_tables=self._materialized_attempted_table_names(
                    connection,
                    workspace_id=workspace_id,
                    generation=generation,
                ),
            )
        except (
            json.JSONDecodeError,
            sqlite3.Error,
            TypeError,
            ValueError,
            pydantic.ValidationError,
        ) as exc:
            raise CatalogStoreError(f"materialized publication metadata is invalid: {exc}") from exc

    def mark_failure(self, workspace_id: str, error: str) -> None:
        """Retain the last readable generation and expose one bounded failure."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return
        self.open()
        connection = self._require_connection()
        bounded = " ".join(error.split())[:512]
        try:
            connection.execute(
                """
                INSERT INTO catalog_state(workspace_id, state, warnings, last_error)
                VALUES (?, ?, '[]', ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    state = ?,
                    last_error = excluded.last_error
                """,
                (
                    workspace_id,
                    CatalogLifecycleState.FAILED.value,
                    bounded,
                    CatalogLifecycleState.FAILED.value,
                ),
            )
        except sqlite3.Error as exc:
            raise CatalogStoreError(f"catalog lifecycle update failed: {exc}") from exc

    def read_materialized(
        self,
        workspace_id: str,
        *,
        match_expression: str = "",
        kinds: tuple[str, ...] = (),
        tables: tuple[str, ...] = (),
        path_prefixes: tuple[str, ...] = (),
        policy_ids: tuple[str, ...] = (),
        seed_keys: tuple[str, ...] = (),
        ownership_selector: str | None = None,
        limit: int,
        offset: int = 0,
        relation_depth: int = 0,
        count_total_rows: bool = True,
    ) -> MaterializedRead:
        """Read one active immutable generation without repository work."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            raise CatalogReadError(
                "catalog_disabled",
                "the SQLite catalog is disabled by configuration",
                retryable=False,
            )
        if self._connection is None:
            raise CatalogReadError(
                "catalog_not_ready",
                "the SQLite catalog has not been initialized by the server lifespan",
                retryable=True,
            )
        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
            state_row = connection.execute(
                """
                SELECT active_generation, state, snapshot_id, source_fingerprint,
                       semantic_mode, coverage, warnings, dirty_since, last_error,
                       publication_revision
                FROM catalog_state WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
            if state_row is None or state_row[0] is None:
                state = (
                    CatalogLifecycleState.BUILDING
                    if state_row is None
                    else CatalogLifecycleState(str(state_row[1]))
                )
                error_type = (
                    "catalog_unavailable"
                    if state is CatalogLifecycleState.FAILED
                    else "catalog_not_ready"
                )
                message = (
                    str(state_row[8])
                    if state_row is not None and state_row[8]
                    else "the lifecycle indexer has not published a readable catalog generation"
                )
                raise CatalogReadError(
                    error_type,
                    message,
                    retryable=state is not CatalogLifecycleState.FAILED,
                )
            (
                generation,
                raw_state,
                snapshot_id,
                source_fingerprint,
                semantic_mode,
                coverage_json,
                warnings_json,
                dirty_since,
                last_error,
                publication_revision,
            ) = state_row
            lifecycle_state = CatalogLifecycleState(str(raw_state))
            published_tables = self._materialized_table_names(
                connection,
                workspace_id=workspace_id,
                generation=int(generation),
            )
            effective_match_expression = match_expression
            primary, has_more, total_rows = self._ranked_context_keys(
                connection,
                generation=int(generation),
                match_expression=match_expression,
                kinds=kinds,
                tables=tables,
                path_prefixes=path_prefixes,
                policy_ids=policy_ids,
                seed_keys=seed_keys,
                ownership_selector=ownership_selector,
                limit=limit,
                offset=offset,
                count_total_rows=count_total_rows,
            )
            if not primary and match_expression and path_prefixes:
                effective_match_expression = ""
                primary, has_more, total_rows = self._ranked_context_keys(
                    connection,
                    generation=int(generation),
                    match_expression="",
                    kinds=kinds,
                    tables=tables,
                    path_prefixes=path_prefixes,
                    policy_ids=policy_ids,
                    seed_keys=seed_keys,
                    ownership_selector=ownership_selector,
                    limit=limit,
                    offset=offset,
                    count_total_rows=count_total_rows,
                )
            relation_seed_count = (
                max(1, limit // (relation_depth + 1))
                if relation_depth > 0 and primary
                else len(primary)
            )
            relation_seeds = primary[:relation_seed_count]
            deferred_primary = primary[relation_seed_count:]
            primary_scores = dict(primary)
            distances = {row_key: 0 for row_key, _score in relation_seeds}
            ordered_keys = [row_key for row_key, _score in relation_seeds]
            scores = dict(relation_seeds)
            relation_overflow: list[tuple[str, int]] = []
            discovered = set(ordered_keys)
            frontier = set(ordered_keys)
            for distance in range(1, relation_depth + 1):
                if not frontier or len(ordered_keys) >= limit:
                    break
                related = self._related_keys(
                    connection,
                    generation=int(generation),
                    frontier=frontier,
                )
                # Keep a bounded beam through every requested relation layer.
                # Relation-only facts rank ahead of lexical overlap; unused beam
                # candidates backfill before deferred lexical hits below.
                related = (
                    *(row_key for row_key in related if row_key not in primary_scores),
                    *(row_key for row_key in related if row_key in primary_scores),
                )
                next_frontier: set[str] = set()
                selected_at_distance = 0
                for row_key in related:
                    if row_key in discovered:
                        continue
                    discovered.add(row_key)
                    if selected_at_distance >= relation_seed_count:
                        if len(relation_overflow) < limit:
                            relation_overflow.append((row_key, distance))
                        else:
                            has_more = True
                        continue
                    if row_key in primary_scores:
                        distances[row_key] = 0
                        scores[row_key] = primary_scores[row_key]
                    else:
                        distances[row_key] = distance
                        scores[row_key] = 0.0
                    ordered_keys.append(row_key)
                    next_frontier.add(row_key)
                    selected_at_distance += 1
                    if len(ordered_keys) >= limit:
                        has_more = True
                        break
                frontier = next_frontier
            for row_key, distance in relation_overflow:
                if len(ordered_keys) >= limit:
                    has_more = True
                    break
                if row_key in distances:
                    continue
                if row_key in primary_scores:
                    distances[row_key] = 0
                    scores[row_key] = primary_scores[row_key]
                else:
                    distances[row_key] = distance
                    scores[row_key] = 0.0
                ordered_keys.append(row_key)
            for row_key, score in deferred_primary:
                if len(ordered_keys) >= limit:
                    has_more = True
                    break
                if row_key in distances:
                    continue
                distances[row_key] = 0
                scores[row_key] = score
                ordered_keys.append(row_key)
            rows_by_key = self._rows_by_key(
                connection,
                generation=int(generation),
                row_keys=ordered_keys,
            )
            materialized_rows: list[MaterializedRow] = []
            for row_key in ordered_keys:
                stored = rows_by_key.get(row_key)
                if stored is None:
                    continue
                row, kind = stored
                materialized_rows.append(
                    MaterializedRow(
                        row=row,
                        fact_key=soleaux.catalog.search.canonical_fact_key_for_row(row, kind=kind),
                        kind=kind,
                        score=scores[row_key],
                        relation_distance=distances[row_key],
                    )
                )
            materialized = tuple(materialized_rows)
            coverage = soleaux.contracts.coverage.Coverage.model_validate_json(str(coverage_json))
            warnings_raw = json.loads(str(warnings_json))
            if not _is_object_list(warnings_raw):
                raise CatalogStoreError("catalog state warnings are malformed")
            warnings = tuple(str(value) for value in warnings_raw)
            if lifecycle_state is not CatalogLifecycleState.READY:
                reason = (
                    f"catalog_{lifecycle_state.value}"
                    + (f": {last_error}" if last_error else "")
                    + (f" since {dirty_since}" if dirty_since else "")
                )
                coverage = coverage.model_copy(
                    update={
                        "status": (
                            coverage.status
                            if coverage.status
                            is not soleaux.contracts.coverage.FrameStatus.COMPLETE
                            else soleaux.contracts.coverage.FrameStatus.PARTIAL
                        ),
                        "omitted_reasons": tuple(
                            dict.fromkeys((*coverage.omitted_reasons, reason))
                        ),
                    }
                )
                warnings = tuple(dict.fromkeys((*warnings, reason)))
            selected_tables = (
                tuple(table for table in published_tables if table in tables)
                if tables
                else published_tables
            )
            tables_by_name: dict[str, list[soleaux.contracts.frame.FactRow]] = {
                table: [] for table in selected_tables
            }
            for item in materialized:
                tables_by_name.setdefault(item.row.table, []).append(item.row)
            frame = soleaux.contracts.frame.AnalysisFrame(
                snapshot_id=str(snapshot_id),
                workspace_id=workspace_id,
                semantic_mode=soleaux.contracts.requests.SemanticMode(str(semantic_mode)),
                coverage=coverage,
                tables={
                    table: tuple(table_rows) for table, table_rows in sorted(tables_by_name.items())
                },
                warnings=warnings,
            )
            connection.execute("COMMIT")
            return MaterializedRead(
                generation=int(generation),
                publication_revision=int(publication_revision),
                snapshot_id=str(snapshot_id),
                source_fingerprint=str(source_fingerprint),
                state=lifecycle_state,
                frame=frame,
                rows=materialized,
                has_more=has_more,
                total_rows=total_rows,
                total_rows_exact=count_total_rows,
                published_tables=published_tables,
                retrieval_engine=("sqlite-fts5" if effective_match_expression else "sqlite-scan"),
            )
        except CatalogReadError:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise
        except (
            json.JSONDecodeError,
            sqlite3.Error,
            TypeError,
            ValueError,
            pydantic.ValidationError,
        ) as exc:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise CatalogReadError(
                "catalog_unavailable",
                f"the materialized catalog is invalid: {exc}",
                retryable=False,
            ) from exc

    @staticmethod
    def _ranked_context_keys(
        connection: sqlite3.Connection,
        *,
        generation: int,
        match_expression: str,
        kinds: tuple[str, ...],
        tables: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        policy_ids: tuple[str, ...],
        seed_keys: tuple[str, ...],
        ownership_selector: str | None,
        limit: int,
        offset: int,
        count_total_rows: bool,
    ) -> tuple[tuple[tuple[str, float], ...], bool, int]:
        clauses: list[str] = ["generation = ?"]
        parameters: list[object] = [generation]
        source = "context_rows"
        score = "0.0"
        if match_expression:
            source = "context_fts"
            clauses.append("context_fts MATCH ?")
            parameters.append(match_expression)
            score = "bm25(context_fts, 0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 1.0)"
        if kinds:
            placeholders = ",".join("?" for _kind in kinds)
            clauses.append(f"kind IN ({placeholders})")
            parameters.extend(kinds)
        if tables:
            placeholders = ",".join("?" for _table in tables)
            clauses.append(f"table_name IN ({placeholders})")
            parameters.extend(tables)
        if path_prefixes:
            prefixes: list[str] = []
            for prefix in path_prefixes:
                prefixes.append("(path = ? OR substr(path, 1, ?) = ?)")
                parameters.extend((prefix, len(prefix) + 1, f"{prefix}/"))
            clauses.append(f"({' OR '.join(prefixes)})")
        if policy_ids:
            placeholders = ",".join("?" for _policy_id in policy_ids)
            if source == "context_rows":
                clauses.append(f"policy_id IN ({placeholders})")
                parameters.extend(policy_ids)
            else:
                clauses.append(
                    "row_key IN (SELECT row_key FROM context_rows AS policy_rows "
                    "WHERE policy_rows.generation = ? "
                    f"AND policy_rows.policy_id IN ({placeholders}))"
                )
                parameters.extend((generation, *policy_ids))
        if seed_keys:
            seed_clause, seed_parameters = _seed_key_filter(generation, seed_keys)
            clauses.append(seed_clause)
            parameters.extend(seed_parameters)
        if ownership_selector is not None:
            selector_clause, selector_parameters = _ownership_selector_filter(
                generation,
                ownership_selector,
            )
            clauses.append(selector_clause)
            parameters.extend(selector_parameters)
        where = " AND ".join(clauses)
        total_rows = (
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {source} WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            if count_total_rows
            else 0
        )
        row_limit = limit if count_total_rows else limit + 1
        rows = connection.execute(
            f"SELECT row_key, {score} AS score FROM {source} "
            f"WHERE {where} "
            "ORDER BY score, table_name, path, row_key LIMIT ? OFFSET ?",
            (*parameters, row_limit, offset),
        ).fetchall()
        if not count_total_rows:
            has_more = len(rows) > limit
            rows = rows[:limit]
            total_rows = offset + len(rows) + int(has_more)
        else:
            has_more = offset + len(rows) < total_rows
        selected = tuple((str(row_key), -float(rank)) for row_key, rank in rows)
        return selected, has_more, total_rows

    @staticmethod
    def _materialized_table_names(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        generation: int,
    ) -> tuple[str, ...]:
        metadata = connection.execute(
            "SELECT value FROM catalog_meta WHERE key = ?",
            (_materialized_tables_key(workspace_id, generation),),
        ).fetchone()
        if metadata is None:
            return tuple(
                str(table_name)
                for (table_name,) in connection.execute(
                    "SELECT DISTINCT table_name FROM context_rows "
                    "WHERE generation = ? ORDER BY table_name",
                    (generation,),
                ).fetchall()
            )
        return CatalogStore._decode_materialized_table_names(
            metadata,
            label="materialized table",
        )

    @staticmethod
    def _materialized_attempted_table_names(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        generation: int,
    ) -> tuple[str, ...]:
        metadata = connection.execute(
            "SELECT value FROM catalog_meta WHERE key = ?",
            (_materialized_attempted_tables_key(workspace_id, generation),),
        ).fetchone()
        if metadata is None:
            return ()
        return CatalogStore._decode_materialized_table_names(
            metadata,
            label="materialized attempted-table",
        )

    @staticmethod
    def _decode_materialized_table_names(
        metadata: collections.abc.Sequence[object],
        *,
        label: str,
    ) -> tuple[str, ...]:
        decoded = json.loads(str(metadata[0]))
        if not _is_object_list(decoded) or any(
            not isinstance(table_name, str) or not table_name for table_name in decoded
        ):
            raise CatalogStoreError(f"{label} metadata is malformed")
        return tuple(dict.fromkeys(str(table_name) for table_name in decoded))

    @staticmethod
    def _related_keys(
        connection: sqlite3.Connection,
        *,
        generation: int,
        frontier: set[str],
    ) -> tuple[str, ...]:
        if not frontier:
            return ()
        ordered = tuple(sorted(frontier))
        placeholders = ",".join("?" for _row_key in ordered)
        rows = connection.execute(
            "SELECT source_row_key, target_row_key FROM relationships "
            f"WHERE generation = ? AND (source_row_key IN ({placeholders}) "
            f"OR target_row_key IN ({placeholders})) "
            "ORDER BY basis, source_row_key, target_row_key",
            (generation, *ordered, *ordered),
        ).fetchall()
        return tuple(
            dict.fromkeys(target if source in frontier else source for source, target in rows)
        )

    @staticmethod
    def _rows_by_key(
        connection: sqlite3.Connection,
        *,
        generation: int,
        row_keys: collections.abc.Sequence[str],
    ) -> dict[str, tuple[soleaux.contracts.frame.FactRow, str]]:
        if not row_keys:
            return {}
        placeholders = ",".join("?" for _row_key in row_keys)
        rows = connection.execute(
            f"SELECT row_key, kind, payload FROM context_rows "
            f"WHERE generation = ? AND row_key IN ({placeholders})",
            (generation, *row_keys),
        ).fetchall()
        return {
            str(row_key): (
                soleaux.contracts.frame.FactRow.model_validate_json(str(payload)),
                str(kind),
            )
            for row_key, kind, payload in rows
        }

    def load(
        self, *, source_fingerprint: str | None = None
    ) -> soleaux.catalog.generation.CatalogGeneration | None:
        """Load one validated generation, optionally selected by exact content identity."""
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            return None
        try:
            if (
                self._mode is soleaux.contracts.config.CatalogMode.DISK
                and source_fingerprint is not None
            ):
                self._bind_disk_generation(source_fingerprint)
                if not self._path.is_file():
                    return None
            else:
                self._bind_latest_disk_generation()
            self.open()
            metadata = self.metadata()
            required = {
                "workspace_root",
                "config_digest",
                "workspace_id",
                "generation",
                "snapshot_id",
                "source_fingerprint",
                "created_at",
                "snapshot",
                "inventory",
                "inventory_signatures",
            }
            if not required.issubset(metadata):
                return None
            if pathlib.Path(metadata["workspace_root"]) != self._workspace_root:
                raise CatalogStoreError("catalog workspace identity does not match")
            if metadata["config_digest"] != self._config_digest:
                raise CatalogStoreError("catalog configuration identity does not match")
            if (
                self._mode is soleaux.contracts.config.CatalogMode.DISK
                and not self._path_is_explicit
                and self._path.stem != metadata["source_fingerprint"]
            ):
                raise CatalogStoreError("catalog filename is not bound to its source fingerprint")
            connection = self._require_connection()
            warnings_raw: object = json.loads(metadata.get("warnings", "[]"))
            if not _is_object_list(warnings_raw):
                raise CatalogStoreError("catalog warning metadata is malformed")
            facts = soleaux.catalog.contracts.CatalogFacts(
                projects=tuple(
                    soleaux.catalog.contracts.ProjectFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM projects ORDER BY project_id"
                    )
                ),
                dependencies=tuple(
                    soleaux.catalog.contracts.DependencyFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM dependencies "
                        "ORDER BY project_id, package_name, scope, usage, source_path"
                    )
                ),
                scripts=tuple(
                    soleaux.catalog.contracts.ScriptFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM scripts ORDER BY project_id, name"
                    )
                ),
                tasks=tuple(
                    soleaux.catalog.contracts.TaskFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM tasks ORDER BY project_id, runner, task_id"
                    )
                ),
                configs=tuple(
                    soleaux.catalog.contracts.ConfigFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM configs ORDER BY project_id, config_path"
                    )
                ),
                engines=tuple(
                    soleaux.catalog.contracts.EngineFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM engines ORDER BY project_id, engine_id"
                    )
                ),
                typescript_routes=tuple(
                    soleaux.catalog.contracts.TypeScriptRouteFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM typescript_routes ORDER BY project_id"
                    )
                ),
                routes=tuple(
                    soleaux.catalog.contracts.RouteFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM routes ORDER BY project_id, source_path, route_id"
                    )
                ),
                rules=tuple(
                    soleaux.catalog.contracts.RuleFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM rules ORDER BY rule_id"
                    )
                ),
                policies=tuple(
                    soleaux.catalog.contracts.PolicyFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM policies ORDER BY governance_source_id, policy_id"
                    )
                ),
                symbols=tuple(
                    soleaux.catalog.contracts.SymbolFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM symbols ORDER BY project_id, path, name, revision_id"
                    )
                ),
                imports=tuple(
                    soleaux.catalog.contracts.ImportFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM imports ORDER BY project_id, path, import_id"
                    )
                ),
                diagnostics=tuple(
                    soleaux.catalog.contracts.DiagnosticFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM diagnostics ORDER BY project_id, path, diagnostic_id"
                    )
                ),
                changes=tuple(
                    soleaux.catalog.contracts.ChangeFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM changes ORDER BY generation, path"
                    )
                ),
                chunks=tuple(
                    soleaux.catalog.contracts.ChunkFact.model_validate_json(payload)
                    for (payload,) in connection.execute(
                        "SELECT payload FROM chunks ORDER BY path, start_line, chunk_id"
                    )
                ),
                warnings=tuple(str(item) for item in warnings_raw),
            )
            snapshot = soleaux.contracts.snapshot.RepositorySnapshot.model_validate_json(
                metadata["snapshot"]
            )
            inventory_raw: object = json.loads(metadata["inventory"])
            signatures_raw: object = json.loads(metadata["inventory_signatures"])
            if not _is_object_list(inventory_raw) or not _is_object_dict(signatures_raw):
                raise CatalogStoreError("catalog inventory metadata is malformed")
            inventory = tuple(str(item) for item in inventory_raw)
            signatures: dict[str, tuple[int, int, int, int, int]] = {}
            for raw_path, raw_signature in signatures_raw.items():
                if not isinstance(raw_path, str) or not _is_object_list(raw_signature):
                    raise CatalogStoreError("catalog signature metadata is malformed")
                if len(raw_signature) != 5:
                    raise CatalogStoreError("catalog signature metadata is malformed")
                device, inode, size, modified, changed = raw_signature
                if not (
                    isinstance(device, int)
                    and isinstance(inode, int)
                    and isinstance(size, int)
                    and isinstance(modified, int)
                    and isinstance(changed, int)
                ):
                    raise CatalogStoreError("catalog signature metadata is malformed")
                signatures[raw_path] = (
                    device,
                    inode,
                    size,
                    modified,
                    changed,
                )
            generation = soleaux.catalog.generation.catalog_generation_from_facts(
                generation=int(metadata["generation"]),
                snapshot=snapshot,
                facts=facts,
                created_at=datetime.datetime.fromisoformat(metadata["created_at"]),
                inventory=inventory,
                inventory_signatures=signatures,
            )
            if (
                generation.workspace_id != metadata["workspace_id"]
                or generation.snapshot_id != metadata["snapshot_id"]
                or generation.source_fingerprint != metadata["source_fingerprint"]
            ):
                raise CatalogStoreError("catalog generation metadata does not match")
            return generation
        except (
            CatalogStoreError,
            json.JSONDecodeError,
            OSError,
            sqlite3.Error,
            TypeError,
            ValueError,
            pydantic.ValidationError,
        ) as exc:
            if self._can_fallback:
                self._fallback_to_memory("catalog load failed", exc)
                return None
            if isinstance(exc, CatalogStoreError):
                raise
            raise CatalogStoreError(f"catalog load failed: {exc}") from exc

    @property
    def _can_fallback(self) -> bool:
        return (
            self._requested_mode is soleaux.contracts.config.CatalogMode.AUTO
            and self._mode is soleaux.contracts.config.CatalogMode.DISK
        )

    def _fallback_to_memory(self, operation: str, exc: BaseException) -> None:
        reason = self._reason(operation, exc)
        self._mode = soleaux.contracts.config.CatalogMode.MEMORY
        self.close()
        self._fallback_reason = reason
        self.open()

    @staticmethod
    def _reason(operation: str, exc: BaseException) -> str:
        detail = " ".join(str(exc).split())
        return f"{operation}: {type(exc).__name__}: {detail}"[:512]

    def search_ranked(
        self,
        match_expression: str,
        *,
        kinds: tuple[str, ...] = (),
        path_prefixes: tuple[str, ...] = (),
        limit: int,
        offset: int = 0,
    ) -> tuple[tuple[soleaux.catalog.search.RankedHit, ...], bool]:
        """Weighted bm25 hits over facts_fts; deterministic order; (hits, has_more)."""
        if not self._fts_available or not match_expression:
            return (), False
        connection = self._require_connection()
        boost_cases = " ".join(
            f"WHEN '{kind}' THEN {boost}"
            for kind, boost in sorted(soleaux.catalog.search.KIND_RANK_BOOSTS.items())
        )
        score_expression = (
            f"bm25(facts_fts, 0.0, 0.0, 2.0, 4.0, 1.0) / (CASE kind {boost_cases} ELSE 1.0 END)"
        )
        clauses = ["facts_fts MATCH ?"]
        parameters: list[object] = [match_expression]
        if kinds:
            placeholders = ",".join("?" for _kind in kinds)
            clauses.append(f"kind IN ({placeholders})")
            parameters.extend(kinds)
        if path_prefixes:
            prefix_clauses: list[str] = []
            for prefix in path_prefixes:
                prefix_clauses.append("(path = ? OR substr(path, 1, ?) = ?)")
                parameters.extend((prefix, len(prefix) + 1, f"{prefix}/"))
            clauses.append(f"({' OR '.join(prefix_clauses)})")
        parameters.extend((limit + 1, offset))
        rows = connection.execute(
            f"SELECT fact_key, kind, path, {score_expression} AS score "
            f"FROM facts_fts WHERE {' AND '.join(clauses)} "
            "ORDER BY score, fact_key LIMIT ? OFFSET ?",
            parameters,
        ).fetchall()
        hits = tuple(
            soleaux.catalog.search.RankedHit(
                fact_key=fact_key, kind=kind, path=path, score=-float(score)
            )
            for fact_key, kind, path, score in rows[:limit]
        )
        return hits, len(rows) > limit

    def metadata(self) -> dict[str, str]:
        if self._connection is None:
            return {}
        return {
            str(key): str(value)
            for key, value in self._connection.execute(
                "SELECT key, value FROM catalog_meta"
            ).fetchall()
        }

    def _bind_disk_generation(self, source_fingerprint: str) -> None:
        if self._mode is not soleaux.contracts.config.CatalogMode.DISK or self._path_is_explicit:
            return
        desired = self._implicit_database_path(source_fingerprint)
        if self._path == desired:
            return
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        self._release_disk_lease()
        self._disk_gc_ready = False
        self._path = desired

    def _validate_disk_path(self) -> None:
        try:
            repository_local = catalog_path_is_repository_local(
                self._workspace_root,
                self._path,
            )
        except (OSError, RuntimeError) as exc:
            raise CatalogStoreError(f"cannot safely resolve disk catalog path: {exc}") from exc
        if repository_local:
            raise CatalogStoreError("disk catalog path must be outside the workspace")

    def _implicit_database_path(self, source_fingerprint: str | None = None) -> pathlib.Path:
        if self._storage_namespace is None:
            if source_fingerprint is None:
                return catalog_database_path(self._workspace_root)
            return catalog_database_path(self._workspace_root, source_fingerprint)
        return catalog_database_path(
            self._workspace_root,
            source_fingerprint,
            storage_namespace=self._storage_namespace,
        )

    def bind_source_fingerprint(self, source_fingerprint: str) -> None:
        """Select the content-addressed disk generation before loading or publishing."""
        self._bind_disk_generation(source_fingerprint)

    def _bind_latest_disk_generation(self) -> None:
        if (
            self._mode is not soleaux.contracts.config.CatalogMode.DISK
            or self._path_is_explicit
            or self._connection is not None
            or self._path.name != "unbound.sqlite3"
        ):
            return
        directory = self._implicit_database_path().parent
        if not directory.is_dir():
            return
        candidates = _disk_generation_candidates(directory)
        if candidates:
            self._path = candidates[0]

    def _gc_disk_generations(self, *, protected_path: pathlib.Path | None = None) -> None:
        if (
            self._mode is not soleaux.contracts.config.CatalogMode.DISK
            or self._path_is_explicit
            or not self._disk_gc_ready
        ):
            return
        with contextlib.suppress(CatalogStoreError, OSError, RuntimeError):
            self._gc_disk_generations_from_validated_path(protected_path=protected_path)

    def _gc_disk_generations_from_validated_path(
        self,
        *,
        protected_path: pathlib.Path | None,
    ) -> None:
        try:
            deletion_path = self._path.resolve(strict=False)
            protected = protected_path.resolve(strict=False) if protected_path is not None else None
            if catalog_path_is_repository_local(self._workspace_root, deletion_path):
                return
        except OSError, RuntimeError:
            return
        directory = deletion_path.parent
        unbound = directory / "unbound.sqlite3"
        _remove_disk_generation(unbound)
        if not directory.is_dir():
            return
        _remove_orphan_disk_sidecars(directory)
        candidates = _disk_generation_candidates(directory)
        retained_size = 0
        retained_count = 0
        protected = (
            protected
            if protected is not None and protected.parent == directory and protected in candidates
            else None
        )
        if protected is not None:
            retained_size = _disk_generation_size(protected)
            retained_count = 1
        for path in candidates:
            if path == protected:
                continue
            size = _disk_generation_size(path)
            should_remove = (
                retained_count >= self._retained_generations
                or retained_size + size > self._max_disk_size_bytes
            )
            if should_remove and _remove_disk_generation(path):
                continue
            retained_size += size
            retained_count += 1

    def close(self) -> None:
        protected_path = self._path if self._disk_gc_ready else None
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        self._release_disk_lease()
        self._gc_disk_generations(protected_path=protected_path)

    def _release_disk_lease(self) -> None:
        lease = self._disk_lease
        self._disk_lease = None
        if lease is not None:
            lease.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CatalogStoreError("catalog store is not open")
        return self._connection

    def __enter__(self) -> CatalogStore:
        self.open()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object | None,
    ) -> None:
        self.close()
