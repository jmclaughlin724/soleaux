"""Write the soleaux gateway guidance block into a workspace guidance file.

One marker-delimited block teaches any agent on an adopted host the gateway
model: servers flow through soleaux, registration and policy live in
``soleaux.toml``, and auth failures mean ``soleaux mcp login <name>``.
Idempotent: an up-to-date block renders ``None`` so the applier can skip.
"""

from __future__ import annotations

GUIDANCE_BEGIN = "<!-- soleaux-gateway:start -->"
GUIDANCE_END = "<!-- soleaux-gateway:end -->"

GUIDANCE_BODY = """\
## MCP servers via soleaux

All MCP servers reach this host through soleaux, with backend tools namespaced
`<backend>_<tool>`. Registration and tool policy live in `soleaux.toml`
(`[mcp.*]`, `[policy]`); host MCP configs are owned output — do not hand-edit
them or add per-host registrations. If a backend call fails as unauthenticated,
tell the user to run `soleaux mcp login <name>` in their shell and retry only
after they confirm; never retry-loop. `soleaux mcp status` and
`soleaux mcp doctor` show backend health and auth state.\
"""

GUIDANCE_BLOCK = f"{GUIDANCE_BEGIN}\n{GUIDANCE_BODY}\n{GUIDANCE_END}"


class GuidanceMarkerError(ValueError):
    """The guidance file carries an incomplete or duplicate marker pair."""


def render_guidance(current: bytes | None) -> bytes | None:
    """Return the file content with exactly one current guidance block.

    Replaces the content between existing markers, appends when absent, and
    returns ``None`` when the file already carries the current block. A file
    with an incomplete or duplicated marker pair is rejected: pairing the
    first literal matches could span and delete human-authored guidance.
    """
    if current is None:
        return f"{GUIDANCE_BLOCK}\n".encode()
    text = current.decode("utf-8")
    begins = text.count(GUIDANCE_BEGIN)
    ends = text.count(GUIDANCE_END)
    if begins > 1 or ends > 1:
        raise GuidanceMarkerError(
            "duplicate soleaux-gateway markers; remove all but one"
            f" {GUIDANCE_BEGIN} ... {GUIDANCE_END} pair"
        )
    if begins != ends:
        raise GuidanceMarkerError(
            f"incomplete soleaux-gateway marker pair; add or remove {GUIDANCE_BEGIN}"
            f" and {GUIDANCE_END} so they appear together"
        )
    if begins == 1:
        begin = text.find(GUIDANCE_BEGIN)
        end = text.find(GUIDANCE_END)
        if begin > end:
            raise GuidanceMarkerError(
                f"reversed soleaux-gateway markers; {GUIDANCE_BEGIN} must precede {GUIDANCE_END}"
            )
        existing = text[begin : end + len(GUIDANCE_END)]
        if existing == GUIDANCE_BLOCK:
            return None
        rewritten = text[:begin] + GUIDANCE_BLOCK + text[end + len(GUIDANCE_END) :]
        return rewritten.encode()
    if not text.strip():
        return f"{GUIDANCE_BLOCK}\n".encode()
    return f"{text.rstrip()}\n\n{GUIDANCE_BLOCK}\n".encode()
