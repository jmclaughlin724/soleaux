"""Render the canonical MCP tool policy (D036) into host-native policy surfaces.

``soleaux.toml`` owns policy effects; host approval surfaces are rendered
output. Bridged tools are namespaced ``<backend>_<tool>`` (single underscore,
matching the gateway's FastMCP provider namespaces); each host qualifies that
name in its own syntax, so ``bridged_tool_name`` is the one naming helper all
renderers share.

Effect mapping (the D036 rendering note):

| Canonical | Codex ``[mcp_servers.soleaux]`` | OpenCode ``permission`` | Claude |
| --- | --- | --- | --- |
| allow | ``approval_mode = "approve"`` | ``"allow"`` | host default |
| ask | ``approval_mode = "prompt"`` | ``"ask"`` | host default |
| deny | ``disabled_tools`` entry | ``"deny"`` | ``permissions.deny`` |

Codex semantics come from ``AppToolApproval`` in codex-rs
(``requires_mcp_tool_approval_for_mode``): ``approve`` never prompts,
``prompt`` always prompts, and no approval mode can deny, so deny renders into
the server's ``disabled_tools`` deny-list. One Codex server hosts every
backend, so per-backend defaults are inexpressible there: codex-rs matches
``tools`` entries by exact tool name and falls back to the one server-wide
``default_tools_approval_mode``, which must stay ``approve`` so the local
catalog runs non-interactively. A backend default of ``ask`` or ``deny`` would
therefore silently render unlisted tools as auto-approved, so the Codex
renderer rejects every non-``allow`` backend default instead of emitting a
policy it cannot enforce. OpenCode evaluates its permission ruleset
last-match-wins, and sorted key order places ``soleaux_*`` before
``soleaux_<backend>_*`` before exact tool keys, so the deterministic sorted
output is also the correct general-to-specific precedence. Claude renders
only deny entries as ``mcp__soleaux__<backend>_<tool>``; allow and ask are
Claude's default behavior and need no rendered surface.
"""

from __future__ import annotations

import typing

import pydantic

import soleaux.contracts.config

_CODEX_DEFAULT_APPROVAL_MODE = "approve"
_CODEX_APPROVAL_MODES = {
    soleaux.contracts.config.PolicyEffect.ALLOW: "approve",
    soleaux.contracts.config.PolicyEffect.ASK: "prompt",
}
_OPENCODE_FALLBACK_RULE = "soleaux_*"
_OPENCODE_SERVER_PREFIX = "soleaux_"
_CLAUDE_SERVER_PREFIX = "mcp__soleaux__"


class PolicyRenderError(ValueError):
    """A host surface cannot enforce the configured canonical policy."""


class CodexToolEntry(typing.TypedDict):
    """One Codex ``[mcp_servers.soleaux.tools.<tool>]`` entry."""

    approval_mode: str


class CodexPolicyRender(typing.TypedDict):
    """The policy-owned fragment of Codex ``[mcp_servers.soleaux]`` (TOML-ready)."""

    default_tools_approval_mode: str
    tools: dict[str, CodexToolEntry]
    disabled_tools: typing.NotRequired[list[str]]


def bridged_tool_name(backend: str, tool: str) -> str:
    """The canonical namespaced tool name shared by every host renderer."""
    return f"{backend}_{tool}"


def render_codex(config: soleaux.contracts.config.ResolvedConfig) -> CodexPolicyRender:
    """Render the policy-owned fragment of Codex ``[mcp_servers.soleaux]``.

    Codex matches ``tools`` entries by exact bridged name and offers one
    server-wide default, so a per-backend fallback is inexpressible. A
    non-``allow`` backend default would leave unlisted tools silently
    auto-approved; refuse to render instead of emitting a weaker policy.
    """
    tools: dict[str, CodexToolEntry] = {}
    disabled: list[str] = []
    for backend_name in sorted(config.policy.backends):
        backend = config.policy.backends[backend_name]
        if backend.default is not soleaux.contracts.config.PolicyEffect.ALLOW:
            raise PolicyRenderError(
                f"Codex cannot enforce policy backend {backend_name!r} default "
                f"{backend.default.value!r}: unlisted tools would silently inherit "
                "the server-wide approve mode. Pin every tool explicitly and set "
                'default = "allow" for backends rendered to Codex.'
            )
        for tool_name in sorted(backend.tools):
            effect = backend.tools[tool_name]
            bridged = bridged_tool_name(backend_name, tool_name)
            if effect is soleaux.contracts.config.PolicyEffect.DENY:
                disabled.append(bridged)
            else:
                tools[bridged] = {"approval_mode": _CODEX_APPROVAL_MODES[effect]}
    rendered: CodexPolicyRender = {
        "default_tools_approval_mode": _CODEX_DEFAULT_APPROVAL_MODE,
        "tools": tools,
    }
    if disabled:
        rendered["disabled_tools"] = disabled
    return rendered


def render_opencode(config: soleaux.contracts.config.ResolvedConfig) -> dict[str, str]:
    """Render OpenCode ``permission`` rules for the bridged soleaux server."""
    rules: dict[str, str] = {
        _OPENCODE_FALLBACK_RULE: soleaux.contracts.config.PolicyEffect.ASK.value
    }
    for backend_name in sorted(config.policy.backends):
        backend = config.policy.backends[backend_name]
        if backend.default is not soleaux.contracts.config.PolicyEffect.ASK:
            rules[f"{_OPENCODE_SERVER_PREFIX}{backend_name}_*"] = backend.default.value
        for tool_name in sorted(backend.tools):
            effect = backend.tools[tool_name]
            if effect is backend.default:
                continue
            rules[f"{_OPENCODE_SERVER_PREFIX}{bridged_tool_name(backend_name, tool_name)}"] = (
                effect.value
            )
    return dict(sorted(rules.items()))


def render_claude_deny(config: soleaux.contracts.config.ResolvedConfig) -> list[str]:
    """Render Claude ``permissions.deny`` entries for denied bridged tools."""
    denied = [
        f"{_CLAUDE_SERVER_PREFIX}{bridged_tool_name(backend_name, tool_name)}"
        for backend_name, backend in config.policy.backends.items()
        for tool_name, effect in backend.tools.items()
        if effect is soleaux.contracts.config.PolicyEffect.DENY
    ]
    return sorted(denied)


class HostPolicyBundle(pydantic.BaseModel):
    """The three rendered host policy surfaces for one resolved config."""

    model_config = pydantic.ConfigDict(extra="forbid")

    codex: CodexPolicyRender
    opencode: dict[str, str]
    claude_deny: list[str]


def render_all(config: soleaux.contracts.config.ResolvedConfig) -> HostPolicyBundle:
    """Render every host policy surface for one resolved config."""
    return HostPolicyBundle(
        codex=render_codex(config),
        opencode=render_opencode(config),
        claude_deny=render_claude_deny(config),
    )
