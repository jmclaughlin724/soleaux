"""Typed contracts for the adopt workflow (Pydantic, ``extra="forbid"``).

The orchestrator (``adopt.py``) is the only writer; detectors return these
read-only records.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdoptExtraMissingError(RuntimeError):
    """The ``[adopt]`` extra is not installed."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DetectedLspProcess(_Model):
    """One running language-server process whose CWD matches the workspace."""

    pid: int
    name: str = Field(min_length=1)
    cmdline: tuple[str, ...] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    language: str = Field(min_length=1)
    provider: str = Field(min_length=1)


class DetectedEditorConfig(_Model):
    """One VS Code settings.json key that selects a language server."""

    path: str = Field(min_length=1)
    language: str = Field(min_length=1)
    key: str = Field(min_length=1)
    current: str
    disable_value: str


class DetectedMcpRegistration(_Model):
    """One MCP launch registration in a host config file."""

    host: str = Field(min_length=1)
    name: str = Field(min_length=1)
    command: tuple[str, ...] = Field(min_length=1)
    competes: bool


class DetectionReport(_Model):
    """Read-only aggregation of every detector's findings."""

    workspace_root: str = Field(min_length=1)
    processes: tuple[DetectedLspProcess, ...] = ()
    editor_configs: tuple[DetectedEditorConfig, ...] = ()
    mcp_registrations: tuple[DetectedMcpRegistration, ...] = ()
    warnings: tuple[str, ...] = ()


class AdoptionAction(_Model):
    """One planned write. Optional fields are populated per ``kind``."""

    kind: str = Field(min_length=1)
    description: str = Field(min_length=1)
    target_path: str = Field(min_length=1)
    language: str = ""
    key: str | None = None
    value: str | None = None
    # emit_provider: provider name
    provider: str | None = None


class AdoptionPlan(_Model):
    workspace_root: str = Field(min_length=1)
    actions: tuple[AdoptionAction, ...] = ()


class BackupRecord(_Model):
    original_path: str = Field(min_length=1)
    backup_path: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)


class AdoptionResult(_Model):
    workspace_root: str = Field(min_length=1)
    backups: tuple[BackupRecord, ...] = ()
    written: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
