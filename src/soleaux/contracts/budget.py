"""Budget and limit contracts (D018): packaged rules, requests, workers, LSP."""

from __future__ import annotations

import pydantic


class PackagedRuleLimits(pydantic.BaseModel):
    """Default and hard-ceiling limits per packaged-rule evaluation."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    input_paths: int = pydantic.Field(default=256, ge=1)
    input_paths_ceiling: int = pydantic.Field(default=4096, ge=1)
    input_bytes: int = pydantic.Field(default=4 * 1024 * 1024, ge=1)
    input_bytes_ceiling: int = pydantic.Field(default=32 * 1024 * 1024, ge=1)
    output_rows: int = pydantic.Field(default=200, ge=1)
    output_rows_ceiling: int = pydantic.Field(default=1000, ge=1)
    output_bytes: int = pydantic.Field(default=256 * 1024, ge=1)
    output_bytes_ceiling: int = pydantic.Field(default=1024 * 1024, ge=1)
    worker_time_seconds: float = pydantic.Field(default=1.0, gt=0)
    worker_time_ceiling_seconds: float = pydantic.Field(default=10.0, gt=0)
    wall_time_seconds: float = pydantic.Field(default=15.0, gt=0)
    wall_time_ceiling_seconds: float = pydantic.Field(default=60.0, gt=0)
    concurrent_rules: int = pydantic.Field(default=1, ge=1)
    concurrent_rules_ceiling: int = pydantic.Field(default=2, ge=1)


class RequestBudget(pydantic.BaseModel):
    """Request deadlines: structured failure reaches the host before its 60s timeout."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    default_timeout_seconds: float = pydantic.Field(default=10.0, gt=0)
    max_timeout_seconds: float = pydantic.Field(default=55.0, gt=0)


class StructuralCatalogBudget(pydantic.BaseModel):
    """Bounds one generation-time structural enrichment pass."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    max_files_per_pass: int = pydantic.Field(default=4096, ge=1)
    wall_time_seconds: float = pydantic.Field(default=20.0, gt=0)


class StructuralWorkerBudget(pydantic.BaseModel):
    """One lazy supervised worker; recycled by completed jobs or RSS (D011)."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    max_completed_jobs: int = pydantic.Field(default=64, ge=1)
    max_rss_bytes: int = pydantic.Field(default=96 * 1024 * 1024, ge=1)
    lru_entries: int = pydantic.Field(default=2048, ge=1)
    lru_bytes: int = pydantic.Field(default=128 * 1024 * 1024, ge=1)
    shutdown_grace_seconds: float = pydantic.Field(default=5.0, gt=0)


class LspSessionBudget(pydantic.BaseModel):
    """D023: bounded open-document LRU and shutdown grace per session."""

    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    max_open_documents: int = pydantic.Field(default=64, ge=1)
    max_open_bytes: int = pydantic.Field(default=32 * 1024 * 1024, ge=1)
    shutdown_grace_seconds: float = pydantic.Field(default=5.0, gt=0)
    cancellation_return_ms: int = pydantic.Field(default=250, ge=1)
