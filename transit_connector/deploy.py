"""Production entrypoint for hosted deployment (Railway, Fly.io, ...).

Reads configuration from the environment:

  PORT               - port to listen on (Railway sets this automatically)
  TRANSITLAND_API_KEY- upstream transit data key (set in the host dashboard)
  TRANSIT_PROVIDER   - "transitland" (default) or "mock"
  ALLOWED_HOSTS      - comma-separated public hostnames, e.g.
                       "my-app.up.railway.app". Required in production:
                       the MCP SDK's DNS-rebinding protection rejects
                       requests whose Host header is not listed here.

Run with:  uvicorn transit_connector.deploy:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings

from .server import create_server


def build_app():
    allowed_hosts = [
        h.strip()
        for h in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    transport_security = (
        TransportSecuritySettings(allowed_hosts=allowed_hosts)
        if allowed_hosts
        else None
    )
    return create_server().streamable_http_app(
        transport_security=transport_security,
        host="0.0.0.0",
    )


app = build_app()
