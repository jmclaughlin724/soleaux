"""Back-compat shim: the bridge client now lives in ``soleaux.bridge``.

Kept so existing host registrations (``client.py bridge|context <host>``)
keep working until the cutover to the installed ``soleaux`` CLI.
"""

from __future__ import annotations

from soleaux.bridge.client import (
    _create_bridge_proxy,
    _discard_upstream_log,
    _discard_upstream_progress,
    _uds_http_client_factory,
    main,
    request_context,
    run_bridge,
    run_context,
)
from soleaux.bridge.deployment import (
    DeploymentConfig,
    DeploymentError,
    load_deployment_config,
)
from soleaux.bridge.rendering import (
    _HOST_CONTEXT_LIMIT_GAP,
    _MAX_CONTEXT_BYTES,
    _MAX_CONTEXT_PAYLOAD_BYTES,
    _OUTPUT_TERMINATOR,
    _bounded_objective,
    _human_context,
    _render_required_context,
    _required_sections,
    _required_sections_present,
    _server_text,
    _task_context_packet,
)

__all__ = [
    "_HOST_CONTEXT_LIMIT_GAP",
    "_MAX_CONTEXT_BYTES",
    "_MAX_CONTEXT_PAYLOAD_BYTES",
    "_OUTPUT_TERMINATOR",
    "DeploymentConfig",
    "DeploymentError",
    "_bounded_objective",
    "_create_bridge_proxy",
    "_discard_upstream_log",
    "_discard_upstream_progress",
    "_human_context",
    "_render_required_context",
    "_required_sections",
    "_required_sections_present",
    "_server_text",
    "_task_context_packet",
    "_uds_http_client_factory",
    "load_deployment_config",
    "main",
    "request_context",
    "run_bridge",
    "run_context",
]

if __name__ == "__main__":
    raise SystemExit(main())
