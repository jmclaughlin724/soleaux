"""Workspace agent-skills root resolution and provider attach contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client
from pydantic import ValidationError

from soleaux.contracts.config import ResolvedConfig, SkillsConfig
from soleaux.server import create_server
from soleaux.skills import (
    SKILLS_NAMESPACE,
    build_skills_provider,
    resolved_skill_roots,
)


def _write_skill(
    base: Path,
    name: str,
    description: str,
    *,
    main_body: str = "# {name}\n",
    extra_files: dict[str, str] | None = None,
) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    main = f"---\nname: {name}\ndescription: {description}\n---\n\n{main_body.format(name=name)}"
    (skill_dir / "SKILL.md").write_text(main, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_skills_config_defaults() -> None:
    skills = SkillsConfig()
    assert skills.enabled is False
    assert skills.roots == ()
    assert skills.reload is False
    assert skills.main_file_name == "SKILL.md"
    assert skills.supporting_files == "template"


def test_skills_config_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        SkillsConfig.model_validate({"enabled": True, "bogus": 1})


def test_skills_config_rejects_removed_implicit_user_root_switch() -> None:
    with pytest.raises(ValidationError):
        SkillsConfig.model_validate({"include_user_roots": True})


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "foo/../../bar"])
def test_skills_config_rejects_unsafe_roots(bad: str) -> None:
    with pytest.raises(ValidationError):
        SkillsConfig.model_validate({"roots": [bad]})


def test_resolved_config_carries_default_skills() -> None:
    assert ResolvedConfig.default().skills == SkillsConfig()


# ---------------------------------------------------------------------------
# Explicit root resolution
# ---------------------------------------------------------------------------


def test_resolved_skill_roots_dedupes_configured_symlink(tmp_path: Path) -> None:
    primary = tmp_path / "skill-roots" / "primary"
    primary.mkdir(parents=True)
    alias = tmp_path / "skill-roots" / "alias"
    alias.symlink_to(primary, target_is_directory=True)
    config = ResolvedConfig(
        skills=SkillsConfig(
            enabled=True,
            roots=("skill-roots/primary", "skill-roots/alias"),
        )
    )

    assert resolved_skill_roots(tmp_path, config) == [primary.resolve()]


def test_resolved_skill_roots_preserves_configured_order(tmp_path: Path) -> None:
    first = tmp_path / "skills-one"
    second = tmp_path / "skills-two"
    config = ResolvedConfig(skills=SkillsConfig(enabled=True, roots=("skills-one", "skills-two")))

    assert resolved_skill_roots(tmp_path, config) == [first.resolve(), second.resolve()]


def test_resolved_skill_roots_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "skills").symlink_to(outside, target_is_directory=True)
    config = ResolvedConfig(
        skills=SkillsConfig(
            enabled=True,
            roots=("skills",),
        )
    )

    with pytest.raises(ValueError) as raised:
        resolved_skill_roots(workspace, config)

    assert str(raised.value) == "configured skills root escapes the workspace: 'skills'"


def test_resolved_skill_roots_propagates_resolution_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_candidate = tmp_path / "skills"
    original_resolve = Path.resolve

    def fail_configured_root(path: Path, strict: bool = False) -> Path:
        if path == failing_candidate:
            raise OSError("fixture resolution failure")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_configured_root)
    config = ResolvedConfig(
        skills=SkillsConfig(
            enabled=True,
            roots=("skills",),
        )
    )

    with pytest.raises(OSError) as raised:
        resolved_skill_roots(tmp_path, config)

    assert str(raised.value) == "fixture resolution failure"


def test_unconfigured_platform_directories_are_not_discovered(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".agents" / "skills", "implicit", "must stay implicit")
    assert build_skills_provider(tmp_path, ResolvedConfig.default()) is None
    assert resolved_skill_roots(tmp_path, ResolvedConfig.default()) == []


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def test_build_skills_provider_none_when_disabled(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills", "alpha", "alpha")
    config = ResolvedConfig(skills=SkillsConfig(enabled=False, roots=("skills",)))
    assert build_skills_provider(tmp_path, config) is None


async def test_upstream_provider_owns_empty_root_discovery(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    config = ResolvedConfig(skills=SkillsConfig(enabled=True, roots=("skills",)))
    provider = build_skills_provider(tmp_path, config)

    assert provider is not None
    assert await provider.list_resources() == []
    assert await provider.list_resource_templates() == []


async def test_upstream_provider_reloads_an_initially_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    config = ResolvedConfig(skills=SkillsConfig(enabled=True, roots=("skills",), reload=True))
    provider = build_skills_provider(tmp_path, config)
    assert provider is not None
    assert await provider.list_resources() == []

    _write_skill(root, "alpha", "alpha")

    assert {str(resource.uri) for resource in await provider.list_resources()} >= {
        "skill://alpha/SKILL.md",
        "skill://alpha/_manifest",
    }


def test_build_skills_provider_constructs_when_explicitly_enabled(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills", "alpha", "alpha", extra_files={"ref.md": "ref\n"})
    config = ResolvedConfig(skills=SkillsConfig(enabled=True, roots=("skills",)))
    provider = build_skills_provider(tmp_path, config)
    assert provider is not None


# ---------------------------------------------------------------------------
# End-to-end server attach
# ---------------------------------------------------------------------------


async def _client_resources(root: Path, config: ResolvedConfig) -> tuple[set[str], set[str]]:
    server = create_server(root, config=config)
    async with Client(server) as client:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
    uris = {str(r.uri) for r in resources}
    template_uris = {str(t.uri_template) for t in templates}
    return uris, template_uris


async def test_attach_exposes_namespaced_skill_resources(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skill-root",
        "alpha",
        "alpha skill",
        extra_files={"ref.md": "ref\n"},
    )
    config = ResolvedConfig(skills=SkillsConfig(enabled=True, roots=("skill-root",)))

    uris, template_uris = await _client_resources(tmp_path, config)

    assert f"skill://{SKILLS_NAMESPACE}/alpha/SKILL.md" in uris
    assert f"skill://{SKILLS_NAMESPACE}/alpha/_manifest" in uris
    assert f"skill://{SKILLS_NAMESPACE}/alpha/ref.md" not in uris
    assert any(uri.startswith(f"skill://{SKILLS_NAMESPACE}/alpha/") for uri in template_uris)


async def test_disabled_config_attaches_no_skill_namespace(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path / "skills", "alpha", "alpha")
    config = ResolvedConfig(skills=SkillsConfig(enabled=False, roots=("skills",)))

    uris, _ = await _client_resources(tmp_path, config)
    assert not any(u.startswith("skill://") for u in uris)
