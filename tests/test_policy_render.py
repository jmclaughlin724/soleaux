"""Host-native policy rendering from the canonical policy model (D036)."""

from __future__ import annotations

import _assertions
import pydantic

import soleaux.contracts.config
import soleaux.policy_render


def _mcp_model() -> soleaux.contracts.config.McpBackendConfig:
    return soleaux.contracts.config.McpBackendConfig.model_validate({"command": ["backend"]})


def _resolved(
    backends: dict[str, dict[str, object]],
) -> soleaux.contracts.config.ResolvedConfig:
    return soleaux.contracts.config.ResolvedConfig(
        mcp={name: _mcp_model() for name in backends},
        policy=soleaux.contracts.config.PolicyConfig.model_validate({"backends": backends}),
    )


def test_bridged_tool_name_is_the_single_shared_naming_helper() -> None:
    assert soleaux.policy_render.bridged_tool_name("playwright", "browser_navigate") == (
        "playwright_browser_navigate"
    )


def test_empty_policy_renders_empty_structures() -> None:
    resolved = soleaux.contracts.config.ResolvedConfig.default()

    assert soleaux.policy_render.render_codex(resolved) == {
        "default_tools_approval_mode": "approve",
        "tools": {},
    }
    assert soleaux.policy_render.render_opencode(resolved) == {"soleaux_*": "ask"}
    assert soleaux.policy_render.render_claude_deny(resolved) == []


def test_codex_maps_each_effect_to_the_host_native_surface() -> None:
    resolved = _resolved(
        {
            "playwright": {
                "default": "allow",
                "tools": {
                    "browser_navigate": "allow",
                    "browser_click": "ask",
                    "browser_run_code_unsafe": "deny",
                },
            }
        }
    )

    assert soleaux.policy_render.render_codex(resolved) == {
        "default_tools_approval_mode": "approve",
        "tools": {
            "playwright_browser_click": {"approval_mode": "prompt"},
            "playwright_browser_navigate": {"approval_mode": "approve"},
        },
        "disabled_tools": ["playwright_browser_run_code_unsafe"],
    }


def test_opencode_maps_each_effect_with_a_fail_closed_fallback() -> None:
    resolved = _resolved(
        {
            "playwright": {
                "tools": {
                    "browser_navigate": "allow",
                    "browser_click": "ask",
                    "browser_run_code_unsafe": "deny",
                }
            }
        }
    )

    rendered = soleaux.policy_render.render_opencode(resolved)

    assert rendered == {
        "soleaux_*": "ask",
        "soleaux_playwright_browser_navigate": "allow",
        "soleaux_playwright_browser_run_code_unsafe": "deny",
    }
    assert "soleaux_playwright_browser_click" not in rendered


def test_opencode_renders_non_ask_backend_defaults_as_backend_wildcards() -> None:
    resolved = _resolved(
        {
            "playwright": {
                "default": "allow",
                "tools": {
                    "browser_navigate": "allow",
                    "browser_run_code_unsafe": "deny",
                },
            }
        }
    )

    rendered = soleaux.policy_render.render_opencode(resolved)

    assert rendered == {
        "soleaux_*": "ask",
        "soleaux_playwright_*": "allow",
        "soleaux_playwright_browser_run_code_unsafe": "deny",
    }
    assert "soleaux_playwright_browser_navigate" not in rendered
    assert tuple(rendered) == tuple(sorted(rendered))


def test_codex_rejects_backend_defaults_it_cannot_enforce() -> None:
    implicit_ask = _resolved({"playwright": {"tools": {"browser_navigate": "allow"}}})
    explicit_deny = _resolved({"playwright": {"default": "deny", "tools": {}}})

    for resolved in (implicit_ask, explicit_deny):
        with _assertions.raises_with_message(
            soleaux.policy_render.PolicyRenderError, "cannot enforce policy backend 'playwright'"
        ):
            soleaux.policy_render.render_codex(resolved)

    # The enforceable host surfaces still render the same policy.
    assert soleaux.policy_render.render_opencode(implicit_ask) == {
        "soleaux_*": "ask",
        "soleaux_playwright_browser_navigate": "allow",
    }


def test_claude_renders_only_deny_effects() -> None:
    resolved = _resolved(
        {
            "playwright": {
                "tools": {
                    "browser_navigate": "allow",
                    "browser_click": "ask",
                    "browser_run_code_unsafe": "deny",
                }
            }
        }
    )

    assert soleaux.policy_render.render_claude_deny(resolved) == [
        "mcp__soleaux__playwright_browser_run_code_unsafe"
    ]


def test_renderers_emit_deterministic_sorted_output() -> None:
    resolved = _resolved(
        {
            "zeta": {"default": "allow", "tools": {"write": "deny", "read": "ask"}},
            "alpha": {"default": "allow", "tools": {"scan": "allow", "purge": "deny"}},
        }
    )

    codex = soleaux.policy_render.render_codex(resolved)
    assert tuple(codex["tools"]) == ("alpha_scan", "zeta_read")
    assert codex.get("disabled_tools") == ["alpha_purge", "zeta_write"]
    opencode = soleaux.policy_render.render_opencode(resolved)
    assert tuple(opencode) == tuple(sorted(opencode))
    claude_deny = soleaux.policy_render.render_claude_deny(resolved)
    assert claude_deny == sorted(claude_deny)
    assert soleaux.policy_render.render_all(resolved) == soleaux.policy_render.render_all(resolved)


def test_render_all_bundles_every_host_surface() -> None:
    resolved = _resolved(
        {"playwright": {"default": "allow", "tools": {"browser_run_code_unsafe": "deny"}}}
    )

    bundle = soleaux.policy_render.render_all(resolved)

    assert bundle.codex == soleaux.policy_render.render_codex(resolved)
    assert bundle.opencode == soleaux.policy_render.render_opencode(resolved)
    assert bundle.claude_deny == soleaux.policy_render.render_claude_deny(resolved)
    assert bundle.model_dump(mode="json")["claude_deny"] == [
        "mcp__soleaux__playwright_browser_run_code_unsafe"
    ]


def test_unknown_backend_policy_fails_before_any_render() -> None:
    policy = soleaux.contracts.config.PolicyConfig.model_validate({"backends": {"ghost": {}}})

    with _assertions.raises_with_message(pydantic.ValidationError, "undeclared MCP backend"):
        soleaux.contracts.config.ResolvedConfig(policy=policy)
