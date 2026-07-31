"""Pure-reader contract over lifecycle-published SQLite catalog generations."""

from __future__ import annotations

import collections.abc

import soleaux.catalog.search
import soleaux.catalog.store
import soleaux.contracts.config


class CatalogReader:
    """Read already-published catalog generations without creating storage."""

    def __init__(
        self,
        store_for_workspace: collections.abc.Callable[
            [str], soleaux.catalog.store.CatalogStore | None
        ],
        *,
        mode: soleaux.contracts.config.CatalogMode,
    ) -> None:
        self._store_for_workspace = store_for_workspace
        self._mode = mode

    def context(
        self,
        workspace_id: str,
        *,
        objective: str,
        terms: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        limit: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        query = " ".join(terms) if terms else objective
        store = self._store(workspace_id)
        return store.read_materialized(
            workspace_id,
            match_expression=(
                soleaux.catalog.search.fts_match_expression(
                    query,
                    match_mode=soleaux.catalog.search.SearchMatchMode.ANY,
                )
                if store.fts_available
                else ""
            ),
            path_prefixes=path_prefixes,
            limit=limit,
            relation_depth=2,
            count_total_rows=False,
        )

    def search(
        self,
        workspace_id: str,
        *,
        query: str,
        kinds: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        store = self._store(workspace_id)
        return store.read_materialized(
            workspace_id,
            match_expression=(
                soleaux.catalog.search.fts_match_expression(
                    query, match_mode=soleaux.catalog.search.SearchMatchMode.ALL
                )
                if store.fts_available
                else ""
            ),
            kinds=kinds,
            path_prefixes=path_prefixes,
            limit=limit,
            offset=offset,
        )

    def tables(
        self,
        workspace_id: str,
        *,
        include_tables: tuple[str, ...],
        path_prefixes: tuple[str, ...] = (),
        policy_ids: tuple[str, ...] = (),
        seed_keys: tuple[str, ...] = (),
        ownership_selector: str | None = None,
        relation_depth: int = 0,
        limit: int,
        offset: int,
    ) -> soleaux.catalog.store.MaterializedRead:
        return self._store(workspace_id).read_materialized(
            workspace_id,
            tables=include_tables,
            path_prefixes=path_prefixes,
            policy_ids=policy_ids,
            seed_keys=seed_keys,
            ownership_selector=ownership_selector,
            relation_depth=relation_depth,
            limit=limit,
            offset=offset,
        )

    def _store(self, workspace_id: str) -> soleaux.catalog.store.CatalogStore:
        if self._mode is soleaux.contracts.config.CatalogMode.OFF:
            raise soleaux.catalog.store.CatalogReadError(
                "catalog_disabled",
                "the SQLite catalog is disabled by configuration",
                retryable=False,
            )
        store = self._store_for_workspace(workspace_id)
        if store is None:
            raise soleaux.catalog.store.CatalogReadError(
                "catalog_not_ready",
                "the server lifespan has not initialized the SQLite catalog",
                retryable=True,
            )
        return store
