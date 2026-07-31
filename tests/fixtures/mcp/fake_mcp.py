"""Deterministic stdio MCP backend used only by configured-consumer tests."""

from __future__ import annotations

import json
import os
import sys
import time
from itertools import count
from pathlib import Path

from fastmcp import Context, FastMCP

MODE = os.environ.get("SOLEAUX_TEST_MCP_MODE", "normal")
PID_LOG = Path(os.environ["SOLEAUX_TEST_MCP_PID_LOG"])
PID_LOG.parent.mkdir(parents=True, exist_ok=True)
process_record = {
    "event": "start",
    "mode": MODE,
    "pid": os.getpid(),
    "ppid": os.getppid(),
}
with PID_LOG.open("a", encoding="utf-8") as pid_log:
    pid_log.write(f"{json.dumps(process_record, sort_keys=True)}\n")
print(f"fixture-stderr mode={MODE} pid={os.getpid()}", file=sys.stderr, flush=True)

if MODE == "stdout_noise":
    sys.stdout.write("fixture-invalid-json-rpc\n")
    sys.stdout.flush()
    raise SystemExit(23)
if MODE == "hang":
    time.sleep(60)
    raise SystemExit(24)
if MODE != "normal":
    raise ValueError(f"unsupported fixture mode: {MODE}")

mcp = FastMCP(name="fixture-mcp")
STATE_SEQUENCE = count(1)


@mcp.tool(description="Echo backend text.")
def echo(text: str) -> str:
    return text


@mcp.tool(description="Return process-local state for the active gateway session.")
def state(context: Context) -> dict[str, int | str]:
    return {
        "count": next(STATE_SEQUENCE),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "session_id": context.session_id,
    }


@mcp.tool(description="Probe callbacks that the gateway intentionally does not forward.")
async def callback_probe(context: Context) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    # FastMCP 4.0.0b1 removed the server-side roots and sampling push APIs, so
    # absence is the expected outcome; anything callable must still not forward.
    list_roots = getattr(context, "list_roots", None)
    if list_roots is None:
        outcomes["roots"] = "removed"
    else:
        try:
            await list_roots()
        except Exception as exc:
            outcomes["roots"] = type(exc).__name__
        else:
            outcomes["roots"] = "forwarded"
    sample = getattr(context, "sample", None)
    if sample is None:
        outcomes["sampling"] = "removed"
    else:
        try:
            await sample("fixture sampling probe", max_tokens=8)
        except Exception as exc:
            outcomes["sampling"] = type(exc).__name__
        else:
            outcomes["sampling"] = "forwarded"
    try:
        await context.elicit("fixture elicitation probe", str)
    except Exception as exc:
        outcomes["elicitation"] = type(exc).__name__
    else:
        outcomes["elicitation"] = "forwarded"
    return outcomes


@mcp.resource(
    "data://fixed",
    name="backend_fixed",
    description="Fixed backend resource.",
    mime_type="text/plain",
)
def fixed_resource() -> str:
    return "fixed"


@mcp.resource(
    "data://items/{item_id}",
    name="backend_item",
    description="Parameterized backend resource.",
    mime_type="application/json",
)
def item_resource(item_id: str) -> str:
    return json.dumps({"item_id": item_id}, sort_keys=True)


@mcp.prompt(name="summarize", description="Summarize a backend topic.")
def summarize(topic: str) -> str:
    return f"Summarize {topic}"


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="CRITICAL")
