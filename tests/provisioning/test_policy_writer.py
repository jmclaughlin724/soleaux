"""Host policy application: merge rendered policy into registered host files."""

from __future__ import annotations

import json
import pathlib
import tomllib

import soleaux.contracts.config
import soleaux.policy_render
from soleaux.provisioning import policy_writer


def _config() -> soleaux.contracts.config.ResolvedConfig:
    return soleaux.contracts.config.ResolvedConfig(
        mcp={
            "github": soleaux.contracts.config.McpBackendConfig(
                url="https://mcp.github.example/mcp",
            ),
        },
        policy=soleaux.contracts.config.PolicyConfig(
            backends={
                "github": soleaux.contracts.config.PolicyBackendConfig(
                    # Codex renders only allow defaults; ask/deny defaults fail closed.
                    default=soleaux.contracts.config.PolicyEffect.ALLOW,
                    tools={
                        "create_issue": soleaux.contracts.config.PolicyEffect.ASK,
                        "delete_repository": soleaux.contracts.config.PolicyEffect.DENY,
                    },
                ),
            },
        ),
    )


def _bundle() -> soleaux.policy_render.HostPolicyBundle:
    return soleaux.policy_render.render_all(_config())


def _seed_codex_registration(root: pathlib.Path) -> None:
    (root / ".codex").mkdir(exist_ok=True)
    (root / ".codex" / "config.toml").write_text(
        '[mcp_servers.soleaux]\ncommand = "uvx"\nargs = ["soleaux"]\nenabled = true\n',
        encoding="utf-8",
    )


def _seed_opencode_registration(root: pathlib.Path) -> None:
    (root / "opencode.json").write_text(
        json.dumps({"mcp": {"soleaux": {"type": "local", "command": ["uvx", "soleaux"]}}}),
        encoding="utf-8",
    )


def test_codex_policy_merges_into_existing_registration(tmp_path: pathlib.Path) -> None:
    _seed_codex_registration(tmp_path)

    rendered = policy_writer.render_codex_policy(
        (tmp_path / ".codex" / "config.toml").read_bytes(),
        _bundle(),
    )

    assert rendered is not None
    parsed = tomllib.loads(rendered.decode())
    soleaux = parsed["mcp_servers"]["soleaux"]
    assert soleaux["command"] == "uvx"
    assert soleaux["default_tools_approval_mode"] == "approve"
    assert soleaux["tools"]["github_create_issue"]["approval_mode"] == "prompt"
    assert soleaux["disabled_tools"] == ["github_delete_repository"]


def test_codex_policy_is_idempotent(tmp_path: pathlib.Path) -> None:
    _seed_codex_registration(tmp_path)
    first = policy_writer.render_codex_policy(
        (tmp_path / ".codex" / "config.toml").read_bytes(),
        _bundle(),
    )
    assert first is not None

    assert policy_writer.render_codex_policy(first, _bundle()) is None


def test_codex_policy_skips_a_workspace_without_registration(tmp_path: pathlib.Path) -> None:
    assert policy_writer.render_codex_policy(None, _bundle()) is None
    assert policy_writer.render_codex_policy(b"[mcp_servers.other]\n", _bundle()) is None


def test_opencode_policy_replaces_only_managed_rules(tmp_path: pathlib.Path) -> None:
    _seed_opencode_registration(tmp_path)
    current = json.loads((tmp_path / "opencode.json").read_text())
    current["permission"] = {
        "bash": "allow",
        "soleaux_*": "deny",
        "soleaux_github_stale": "allow",
    }

    rendered = policy_writer.render_opencode_policy(
        json.dumps(current).encode(),
        _bundle(),
    )

    assert rendered is not None
    permission = json.loads(rendered)["permission"]
    assert permission["bash"] == "allow"
    assert permission["soleaux_*"] == "allow"
    assert permission["soleaux_github_*"] == "allow"
    assert permission["soleaux_github_create_issue"] == "ask"
    assert permission["soleaux_github_delete_repository"] == "deny"
    assert "soleaux_github_stale" not in permission


def test_opencode_policy_skips_a_workspace_without_registration(tmp_path: pathlib.Path) -> None:
    assert policy_writer.render_opencode_policy(None, _bundle()) is None
    assert policy_writer.render_opencode_policy(b'{"mcp": {}}', _bundle()) is None


def test_claude_policy_merges_deny_entries(tmp_path: pathlib.Path) -> None:
    current = json.dumps(
        {
            "permissions": {
                "deny": ["Bash(rm -rf *)", "mcp__soleaux__github_stale"],
            },
        },
    ).encode()

    rendered = policy_writer.render_claude_policy(current, _bundle())

    assert rendered is not None
    deny = json.loads(rendered)["permissions"]["deny"]
    assert "Bash(rm -rf *)" in deny
    assert "mcp__soleaux__github_delete_repository" in deny
    assert "mcp__soleaux__github_stale" not in deny


def test_claude_policy_skips_creation_without_deny_effects(tmp_path: pathlib.Path) -> None:
    bundle = soleaux.policy_render.render_all(soleaux.contracts.config.ResolvedConfig())
    assert policy_writer.render_claude_policy(None, bundle) is None


def test_apply_host_policy_writes_registered_hosts_and_records_manifest(
    tmp_path: pathlib.Path,
) -> None:
    _seed_codex_registration(tmp_path)
    _seed_opencode_registration(tmp_path)

    result = policy_writer.apply_host_policy(tmp_path, _config())

    assert ".codex/config.toml" in result.written
    assert "opencode.json" in result.written
    assert ".claude/settings.json" in result.written
    assert ".claude/settings.json" in result.created
    assert {record.original_path for record in result.backups} == {
        ".codex/config.toml",
        "opencode.json",
    }
    deny = json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert deny == ["mcp__soleaux__github_delete_repository"]

    # Revert restores the modified hosts and removes the created one.
    from soleaux.provisioning import adopt as adopt_mod

    restored = adopt_mod.revert(tmp_path)
    assert ".claude/settings.json" in restored
    assert not (tmp_path / ".claude" / "settings.json").exists()
    codex = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert "default_tools_approval_mode" not in codex["mcp_servers"]["soleaux"]


def test_apply_host_policy_is_idempotent(tmp_path: pathlib.Path) -> None:
    _seed_codex_registration(tmp_path)
    _seed_opencode_registration(tmp_path)
    policy_writer.apply_host_policy(tmp_path, _config())

    second = policy_writer.apply_host_policy(tmp_path, _config())

    assert second.written == ()
    assert set(second.skipped) == set(policy_writer.POLICY_TARGETS)
