"""Tests for the transit connector.

Uses the offline MockProvider, so no API key or network is needed.
Run:  .venv/bin/python -m pytest tests/ -q
(or:   .venv/bin/python -m unittest discover -s tests)
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx

from transit_connector.providers import MockProvider, TransitlandProvider
from transit_connector.providers.base import ConfigurationError, TransitProviderError
from transit_connector.server import create_server

try:  # MCP SDK 2.x
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # pragma: no cover
    TransportSecuritySettings = None


def _test_app():
    kwargs = {}
    if TransportSecuritySettings is not None:
        # The SDK's DNS-rebinding protection only trusts 127.0.0.1 by
        # default; relax it for the in-process test client.
        kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )
    return create_server(MockProvider()).streamable_http_app(**kwargs)

MCP_PATH = "/mcp"
PROTOCOL_VERSION = "2026-07-28"


def _parse_rpc_response(resp: httpx.Response) -> dict:
    """Parse a streamable-HTTP JSON-RPC response (SSE stream or plain JSON)."""
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        return resp.json()
    # SSE stream: collect data: lines
    payloads = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            payloads.append(json.loads(line[5:].strip()))
    if not payloads:
        raise AssertionError(f"No SSE data in response: {resp.text[:300]!r}")
    return payloads[-1]


class _Lifespan:
    """Drive the ASGI lifespan protocol so the MCP app initializes."""

    def __init__(self, app):
        self._app = app
        self._queue: asyncio.Queue | None = None
        self._started = asyncio.Event()
        self._stopped = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        self._queue = asyncio.Queue()
        await self._queue.put({"type": "lifespan.startup"})

        async def receive():
            return await self._queue.get()

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                self._started.set()
            elif message["type"] == "lifespan.shutdown.complete":
                self._stopped.set()

        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "headers": []}
        self._task = asyncio.create_task(self._app(scope, receive, send))
        await asyncio.wait_for(self._started.wait(), 15)
        return self

    async def __aexit__(self, *exc):
        await self._queue.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(self._stopped.wait(), 15)
        await self._task


class _RpcClient:
    """Minimal JSON-RPC client over the in-process ASGI app."""

    def __init__(self, app):
        self._app = app
        self._transport = httpx.ASGITransport(app=app)
        self._client: httpx.AsyncClient | None = None
        self._lifespan: _Lifespan | None = None
        self.session_id: str | None = None
        self._next_id = 1

    async def __aenter__(self):
        self._lifespan = _Lifespan(self._app)
        await self._lifespan.__aenter__()
        self._client = httpx.AsyncClient(
            transport=self._transport, base_url="http://localhost"
        )
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()
        await self._lifespan.__aexit__(*exc)

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        if extra:
            h.update(extra)
        return h

    async def request(self, method: str, params: dict | None = None) -> dict:
        body: dict = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        self._next_id += 1
        if params is not None:
            body["params"] = params
        resp = await self._client.post(MCP_PATH, json=body, headers=self._headers())
        assert resp.status_code == 200, f"{method} -> {resp.status_code}: {resp.text[:300]}"
        if not self.session_id:
            self.session_id = resp.headers.get("mcp-session-id")
        data = _parse_rpc_response(resp)
        assert "result" in data, f"RPC error for {method}: {data}"
        return data["result"]

    async def notify(self, method: str, params: dict | None = None) -> None:
        body: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        resp = await self._client.post(MCP_PATH, json=body, headers=self._headers())
        assert resp.status_code in (200, 202), f"notify {method} -> {resp.status_code}"

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        chunks = [
            c.get("text", "")
            for c in result.get("content", [])
            if c.get("type") == "text"
        ]
        return "\n".join(chunks)


def _run(coro):
    return asyncio.run(coro)


class TestStreamableHttpTransport(unittest.TestCase):
    """End-to-end: real HTTP transport, real MCP protocol, mock data."""

    def test_initialize_and_list_tools(self):
        async def go():
            app = _test_app()
            async with _RpcClient(app) as rpc:
                init = await rpc.request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1"},
                    },
                )
                self.assertIn("serverInfo", init)
                await rpc.notify("notifications/initialized")
                tools = await rpc.request("tools/list")
                names = sorted(t["name"] for t in tools["tools"])
                self.assertEqual(
                    names,
                    ["get_next_departures", "get_service_alerts", "plan_trip", "search_stops"],
                )
                return tools

        tools = _run(go())
        by_name = {t["name"]: t for t in tools["tools"]}
        # Every tool must have a description and a usable input schema.
        for name, tool in by_name.items():
            self.assertTrue(tool.get("description"), f"{name} missing description")
            self.assertEqual(
                tool["inputSchema"].get("type"), "object", f"{name} bad schema"
            )

    def test_tools_end_to_end_with_mock(self):
        async def go():
            app = _test_app()
            async with _RpcClient(app) as rpc:
                await rpc.request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1"},
                    },
                )
                await rpc.notify("notifications/initialized")

                stops = await rpc.call_tool("search_stops", {"query": "Union"})
                self.assertIn("Union Station", stops)
                self.assertIn("s-mock-union", stops)

                deps = await rpc.call_tool(
                    "get_next_departures", {"stop_id": "s-mock-union", "limit": 4}
                )
                self.assertIn("Finch via Union", deps)
                self.assertIn("3 min late", deps)
                self.assertIn("CANCELED", deps)

                plan = await rpc.call_tool(
                    "plan_trip",
                    {"origin": "Union Station", "destination": "Dundas Station"},
                )
                self.assertIn("Direct:", plan)
                self.assertIn("Yonge-University", plan)

                no_direct = await rpc.call_tool(
                    "plan_trip",
                    {"origin": "Finch Station", "destination": "Dundas Station"},
                )
                self.assertIn("transfer", no_direct)

                alerts = await rpc.call_tool(
                    "get_service_alerts", {"agency_or_region": "Toronto Transit"}
                )
                self.assertIn("Toronto Transit Commission", alerts)
                self.assertIn("track work", alerts)

        _run(go())

    def test_tool_errors_are_graceful_text(self):
        async def go():
            app = _test_app()
            async with _RpcClient(app) as rpc:
                await rpc.request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0.1"},
                    },
                )
                await rpc.notify("notifications/initialized")
                bad = await rpc.call_tool(
                    "get_next_departures", {"stop_id": "s-mock-nope"}
                )
                self.assertIn("⚠️", bad)

        _run(go())


class TestTransitlandProvider(unittest.TestCase):
    def test_missing_api_key_is_friendly(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRANSITLAND_API_KEY", None)
            provider = TransitlandProvider(api_key="")
            with self.assertRaises(ConfigurationError) as ctx:
                provider.search_stops(query="Union")
            self.assertIn("TRANSITLAND_API_KEY", str(ctx.exception))

    def test_network_failure_is_graceful(self):
        import transit_connector.providers.transitland as tl

        class ExplodingClient:
            def __init__(self, *a, **k):
                pass

            def get(self, *a, **k):
                raise httpx.ConnectError("no route to host")

        provider = TransitlandProvider(api_key="DUMMY")
        with mock.patch.object(tl.httpx, "Client", ExplodingClient):
            with self.assertRaises(TransitProviderError) as ctx:
                provider.search_stops(query="Union")
            self.assertIn("temporarily unavailable", str(ctx.exception))
            self.assertTrue(ctx.exception.retryable)

    def test_unauthorized_key_is_configuration_error(self):
        import transit_connector.providers.transitland as tl

        class UnauthorizedClient:
            def __init__(self, *a, **k):
                pass

            def get(self, *a, **k):
                return httpx.Response(401, json={"error": "bad key"})

        provider = TransitlandProvider(api_key="BAD")
        with mock.patch.object(tl.httpx, "Client", UnauthorizedClient):
            with self.assertRaises(ConfigurationError) as ctx:
                provider.search_stops(query="Union")
            self.assertIn("rejected", str(ctx.exception))

    def test_departure_mapping_handles_shapes(self):
        raw = {
            "trip": {
                "trip_headsign": "Downtown",
                "route": {
                    "onestop_id": "r-x",
                    "route_short_name": "14",
                    "route_long_name": "Mission",
                    "route_type": 3,
                },
            },
            "departure": {
                "scheduled": "2026-09-20T14:05:00-04:00",
                "estimated": "2026-09-20T14:09:00-04:00",
                "delay": 240,
                "realtime": True,
            },
            "schedule_relationship": "SCHEDULED",
        }
        d = TransitlandProvider._map_departure(raw, "Test Stop")
        self.assertEqual(d.route_short_name, "14")
        self.assertEqual(d.mode, "bus")
        self.assertEqual(d.scheduled_local, "14:05")
        self.assertEqual(d.estimated_local, "14:09")
        self.assertEqual(d.delay_minutes, 4.0)
        self.assertTrue(d.realtime)
        self.assertFalse(d.canceled)


if __name__ == "__main__":
    unittest.main()
