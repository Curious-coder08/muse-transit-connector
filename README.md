# Transit Times — a public-transit connector for Muse

An MCP (Model Context Protocol) server that gives an AI assistant real-time
public-transit information: find stops, see upcoming departures with live
delays, plan simple direct trips, and check service alerts for any transit
agency. Built as a connector for Meta's Muse platform
([muse.ai/platform](https://muse.ai/platform)).

## What it does

Four tools, usable from any MCP client:

| Tool | Example question it answers |
|---|---|
| `search_stops` | "Where's the nearest stop?" — by name or `lat,lon` |
| `get_next_departures` | "When's my next bus?" — scheduled times plus **live** realtime predictions, delays, and cancellations |
| `plan_trip` | "How do I get from A to B?" — direct (no-transfer) route options with next departures |
| `get_service_alerts` | "Is my line down?" — active disruptions for an agency (e.g. "Toronto Transit Commission") |

The server is **stateless** and holds **no user credentials**. The only
secret is the server's own upstream API key, which is sent as an HTTP header
and never logged or returned to callers.

## Data source

[Transitland](https://www.transit.land) v2 REST API (free tier key),
aggregating GTFS feeds from 2,000+ transit agencies worldwide. A provider
abstraction (`src/transit_connector/providers/base.py`) keeps the tools
independent of the source, so direct GTFS-realtime feeds can be added later
without changing any tool.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Offline mode (no key needed):
TRANSIT_PROVIDER=mock PYTHONPATH=src .venv/bin/python -m transit_connector

# Streamable HTTP (the transport a hosted connector uses):
TRANSIT_PROVIDER=mock PYTHONPATH=src .venv/bin/python -m transit_connector \
  --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp

# Real data — free key from https://www.transit.land:
export TRANSITLAND_API_KEY=your_key_here
PYTHONPATH=src .venv/bin/python -m transit_connector \
  --transport streamable-http --host 127.0.0.1 --port 8000
```

Tests (no key, no network needed):

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

## Deploy

The connector must be reachable at a **public HTTPS URL**. The repo ships a
`Dockerfile` and a production entrypoint (`src/transit_connector/deploy.py`,
served at `/mcp`).

**Railway (recommended):**

1. Push this repo to GitHub.
2. Railway: **New Project → Deploy from GitHub repo** (auto-detects the Dockerfile).
3. **Settings → Networking → Generate Domain** for your public URL.
4. **Variables** tab — set:
   - `TRANSITLAND_API_KEY` — your key from transit.land (never in code)
   - `ALLOWED_HOSTS` — your Railway domain, e.g. `my-app.up.railway.app`
     (no `https://`, no trailing slash)
5. Done — Railway sets `PORT` automatically and terminates TLS.

`ALLOWED_HOSTS` is required: the MCP SDK's DNS-rebinding protection
rejects requests from any host not on the list. Avoid hosts whose free tier
sleeps (cold starts fail availability probes).

## Project layout

```
src/transit_connector/
├── server.py            # MCP server, the 4 tools, transports
├── deploy.py            # Production entrypoint (reads PORT, ALLOWED_HOSTS)
├── models.py            # Normalized dataclasses (Stop, Departure, Alert, …)
└── providers/
    ├── base.py          # TransitProvider interface + error types
    ├── transitland.py   # Transitland v2 implementation
    └── mock.py          # Offline canned data for tests/dev
```

Feed outages, timeouts, rate limits, and bad keys all degrade to friendly
"data unavailable" messages — the server never crashes on a tool call.

## Limitations

- `plan_trip` covers **direct routes only** (no transfer-aware routing yet).
- Realtime coverage varies by agency; stops without a GTFS-RT feed fall back
  to scheduled times, clearly labeled.
- Transitland's free tier throttles aggressively; add caching in front of
  `get_departures` if traffic grows.

## License

MIT. Transit data itself belongs to the publishing agencies — attribute
Transitland and the underlying agencies per their feed licenses when
redistributing data.

---
*Transit data provided by [Transitland](https://www.transit.land/terms). Schedules and realtime information belong to their respective transit agencies — see per-feed license terms at transit.land/terms.*
