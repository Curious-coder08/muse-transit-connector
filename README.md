# Transit Connector for Muse

A working **MCP (Model Context Protocol) server** that gives an AI assistant
real-time public-transit superpowers: find stops, see upcoming departures
with live delays, plan simple direct trips, and check service alerts for any
transit agency.

Built as a candidate **connector submission for Meta's Muse platform**
([muse.ai/platform](https://muse.ai/platform)): describe the product → build
it → submit for review (functional, security, legal + end-to-end testing) →
appear in the Muse connector directory.

## What it does

Four MCP tools, usable from any MCP client (and, once listed, from Muse):

| Tool | What it answers |
|---|---|
| `search_stops` | "Where's the nearest stop?" — by name or `lat,lon` |
| `get_next_departures` | "When's my next bus/train?" — scheduled + **live** realtime predictions, delays, cancellations |
| `plan_trip` | "How do I get from A to B?" — direct (no-transfer) route options with next departures |
| `get_service_alerts` | "Is my line down?" — active disruptions for an agency (e.g. "Toronto Transit Commission") |

The server is **stateless** and holds **no user credentials**. Auth is handled
by Meta (least-privilege, user-authorized); the only secret is the server's
own upstream API key, which never leaves the server.

## Architecture

```
src/transit_connector/
├── server.py            # MCPServer, the 4 tools, error handling, transports
├── models.py            # Normalized dataclasses (Stop, Departure, Alert, ...)
├── providers/
│   ├── base.py          # TransitProvider interface + error types
│   ├── transitland.py   # Real provider: Transitland v2 REST API
│   └── mock.py          # Offline provider: canned data for tests/dev
└── __main__.py          # `python -m transit_connector`
```

**Provider abstraction:** the tools only talk to the `TransitProvider`
interface. `TransitlandProvider` wraps the
[Transitland v2 REST API](https://www.transit.land/documentation/rest-api/)
(free tier key); a direct GTFS-realtime feed provider can be added later
without touching any tool. Set `TRANSIT_PROVIDER=mock` for offline work.

**Failure behavior:** feed outages, timeouts, rate limits (429), and bad keys
(401) all become friendly "data unavailable / try again" messages. The server
never crashes on a tool call and never leaks the API key (notably, it ignores
Transitland's `meta.next` pagination URLs, which embed the key in plaintext).

## Run locally

```bash
cd muse-transit-connector
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 1. Offline mode (no key needed) — stdio, for MCP clients:
TRANSIT_PROVIDER=mock PYTHONPATH=src .venv/bin/python -m transit_connector

# 2. Streamable HTTP (what Meta's platform will call):
TRANSIT_PROVIDER=mock PYTHONPATH=src .venv/bin/python -m transit_connector \
  --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

# 3. Real data — get a free key at https://www.transit.land, then:
export TRANSITLAND_API_KEY=your_key_here
PYTHONPATH=src .venv/bin/python -m transit_connector --transport streamable-http \
  --host 127.0.0.1 --port 8000
```

Run the tests (no key, no network needed):

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

## Deploy (so Meta can reach it)

Meta's cloud can't reach `localhost` — the server needs a **public HTTPS URL**.
The repo ships a `Dockerfile` plus a production entrypoint
(`src/transit_connector/deploy.py`, served at `/mcp`) so it's one push away.

### Railway (recommended)

 
1. **Variables** tab — add:
   - `TRANSITLAND_API_KEY` = your key from transit.land (never in code)
   - `ALLOWED_HOSTS` = your Railway domain (no `https://`, no trailing slash)
2. Railway sets `PORT` automatically; the container listens on `0.0.0.0:$PORT`.
3. Verify: `https://<your-domain>/mcp` should respond (MCP handshake, not a
   browser page — a `405`/`400` on plain GET is fine; it means it's alive).

**Why `ALLOWED_HOSTS` matters:** the MCP SDK's DNS-rebinding protection is
on by default and only trusts `127.0.0.1`. Without your public hostname in
`ALLOWED_HOSTS`, every request from Meta gets rejected. This is the #1
gotcha that would fail their end-to-end test.


## Known limitations (v1)

- **Live Transitland API verified 2026-09-20** — stop search, departures, and
  alerts all return real data (e.g. downtown Toronto stops, 97 Yonge departures).
  Note: in sandboxed environments with a malformed `no_proxy` entry, httpx may
  fail to build its proxy map — override `no_proxy`/`NO_PROXY` to
  `localhost,127.0.0.1` when running live tests there.
  during development. Response parsing is defensive (handles the documented
  shapes), but the first run with a real key should be verified. The mock
  provider + full test suite cover everything else.
- `plan_trip` finds **direct routes only** (routes serving stops near both
  ends). Transfer-aware multimodal routing is not implemented.
- Realtime coverage depends on the agency: only ~15–20% of Transitland's
  feeds publish GTFS-RT. Stops without realtime fall back to scheduled times
  (clearly labeled).
- Related work: [cyanheads/transitland-mcp-server](https://github.com/cyanheads/transitland-mcp-server)
  is an independent open-source take on the same idea (6 tools, registry
  browsing). Ours is deliberately narrower: 4 end-user tools + a provider
  abstraction for adding direct GTFS-RT feeds later.

## License

MIT — do what you like; attribute Transitland and the underlying agencies
per their feed licenses when redistributing data.
