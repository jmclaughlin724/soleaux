"""Local-deployment and host-envelope schema constants.

The bridge validates the deployment document against these at startup; a
mismatch is a typed error naming the repair route instead of a generic JSON
failure. ``v3`` (shared per-machine registry mode) is introduced with the
shared-service work; the bridge accepts every schema listed in
``SUPPORTED_DEPLOYMENT_SCHEMAS``.
"""

from __future__ import annotations

LOCAL_DEPLOYMENT_SCHEMA_V2 = "soleaux.local-deployment/v2"
LOCAL_DEPLOYMENT_SCHEMA_V3 = "soleaux.local-deployment/v3"
SUPPORTED_DEPLOYMENT_SCHEMAS: tuple[str, ...] = (LOCAL_DEPLOYMENT_SCHEMA_V2,)

HOST_CONTEXT_PACKET_INVALID = "host_context_packet_invalid"
HOST_CONTEXT_PACKET_UNAVAILABLE = "host_context_packet_unavailable"
HOST_CONTEXT_LIMIT = "host_context_limit"

ATTACH_REPAIR_COMMAND = "soleaux attach --repo <path>"
