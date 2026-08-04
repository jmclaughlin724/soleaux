from __future__ import annotations

import pathlib
import tomllib
from typing import cast

ROOT = pathlib.Path(__file__).resolve().parents[1]
CODEX_DIR = ROOT / ".codex"
AGENTS_DIR = CODEX_DIR / "agents"


def load_toml(path: pathlib.Path) -> dict[str, object]:
    with path.open("rb") as source:
        return cast(dict[str, object], tomllib.load(source))


def require_table(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    table = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in table)
    return cast(dict[str, object], table)


def test_codex_agents_follow_upstream_standalone_schema() -> None:
    config = load_toml(CODEX_DIR / "config.toml")
    agent_settings = config.get("agents")
    if agent_settings is not None:
        settings = require_table(agent_settings)
        assert all(not isinstance(value, dict) for value in settings.values())

    role_names: set[str] = set()
    role_files = sorted(AGENTS_DIR.glob("*.toml"))
    assert role_files

    for role_file in role_files:
        assert not role_file.is_symlink()
        role = load_toml(role_file)
        name = role.get("name")
        description = role.get("description")
        developer_instructions = role.get("developer_instructions")

        assert isinstance(name, str) and name.strip()
        assert name == role_file.stem
        assert name not in role_names
        role_names.add(name)
        assert isinstance(description, str) and description.strip()
        assert "Anilize" not in description
        assert isinstance(developer_instructions, str)
        assert developer_instructions.strip()
        assert "Anilize" not in developer_instructions
        assert "model_instructions_file" not in role
        assert not role_file.with_suffix(".md").exists()

    assert not list(AGENTS_DIR.glob("*.md"))
