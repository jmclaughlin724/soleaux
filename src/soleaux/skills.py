"""Explicit workspace skill-root resolution and provider attachment.

Skills are directories containing a main instruction file plus optional
supporting files. This module attaches one namespaced upstream
``SkillsDirectoryProvider`` only when the workspace explicitly enables it and
configures roots. Discovery, manifests, integrity hashes, and resource-template
behavior are upstream-owned.
"""

from __future__ import annotations

import pathlib

import fastmcp
import fastmcp.server.providers.skills

import soleaux.contracts.config

SKILLS_NAMESPACE = "skills"


def resolved_skill_roots(
    root: pathlib.Path, config: soleaux.contracts.config.ResolvedConfig
) -> list[pathlib.Path]:
    """Return contained, ordered roots from explicit workspace config."""
    workspace = root.resolve()
    deduped: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for configured in config.skills.roots:
        candidate = workspace / configured
        try:
            resolved = candidate.resolve()
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(
                f"configured skills root escapes the workspace: {configured!r}"
            ) from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def build_skills_provider(
    root: pathlib.Path,
    config: soleaux.contracts.config.ResolvedConfig,
) -> fastmcp.server.providers.skills.SkillsDirectoryProvider | None:
    """Construct the upstream skills provider when explicitly configured."""
    skills = config.skills
    if not skills.enabled:
        return None
    roots = resolved_skill_roots(root, config)
    if not roots:
        return None
    return fastmcp.server.providers.skills.SkillsDirectoryProvider(
        roots=roots,
        reload=skills.reload,
        main_file_name=skills.main_file_name,
        supporting_files=skills.supporting_files,
    )


def attach_skills_provider[LifespanT](
    server: fastmcp.FastMCP[LifespanT],
    config: soleaux.contracts.config.ResolvedConfig,
    root: pathlib.Path,
) -> bool:
    """Attach one namespaced ``skills`` provider; return whether it was added."""
    provider = build_skills_provider(root, config)
    if provider is None:
        return False
    server.add_provider(provider, namespace=SKILLS_NAMESPACE)
    return True


__all__: tuple[str, ...] = (
    "SKILLS_NAMESPACE",
    "attach_skills_provider",
    "build_skills_provider",
    "resolved_skill_roots",
)
