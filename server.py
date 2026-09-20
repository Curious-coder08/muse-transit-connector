"""MCP server exposing public-transit tools.

Transports:
  - stdio (default): for local development and MCP clients like Claude Desktop.
  - streamable-http: for hosted deployment; Meta's connector platform talks
    to the server over HTTPS, so deploy this behind a public URL.

Auth is handled by Meta (least-privilege, user-authorized); this server is
stateless, holds no user credentials, and only needs its own upstream API
key (TRANSITLAND_API_KEY) to read public transit data.
"""
from __future__ import annotations

import argparse
import functools
import logging
import os
import re

from mcp.server.mcpserver import MCPServer

from .models import Departure, DeparturesResult, Stop, TripLeg, TripOption, TripPlan
from .providers.base import TransitProvider, TransitProviderError
from .providers.mock import MockProvider
from .providers.transitland import TransitlandProvider

logger = logging.getLogger("transit_connector")

PROVIDER_ENV = "TRANSIT_PROVIDER"  # "transitland" (default) or "mock"


def make_provider() -> TransitProvider:
    choice = os.environ.get(PROVIDER_ENV, "transitland").strip().lower()
    if choice == "mock":
        return MockProvider()
    if choice == "transitland":
        return TransitlandProvider()
    raise RuntimeError(
        f"Unknown {PROVIDER_ENV}={choice!r}; expected 'transitland' or 'mock'."
    )


def _safe(fn):
    """Turn provider failures into friendly text; never crash the server."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TransitProviderError as exc:
            return f"⚠️ {exc}"
        except Exception:  # pragma: no cover - defensive
            logger.exception("Transit tool failed unexpectedly")
            return (
                "⚠️ Something went wrong on the connector side. "
                "Please try again shortly."
            )

    return wrapper


# -- formatting helpers -------------------------------------------------

def _fmt_stop(stop: Stop) -> str:
    bits = [f"**{stop.name}** (`{stop.id}`)"]
    meta = []
    if stop.location_type and stop.location_type != "stop":
        meta.append(stop.location_type)
    if stop.code:
        meta.append(f"code {stop.code}")
    if stop.parent_station_name:
        meta.append(f"inside {stop.parent_station_name}")
    if stop.region:
        meta.append(stop.region)
    if stop.lat is not None and stop.lon is not None:
        meta.append(f"{stop.lat:.5f}, {stop.lon:.5f}")
    if stop.wheelchair_accessible is True:
        meta.append("♿ step-free")
    elif stop.wheelchair_accessible is False:
        meta.append("not step-free")
    if meta:
        bits.append(" — " + ", ".join(meta))
    if stop.routes:
        routes = ", ".join(r.display_name for r in stop.routes[:8])
        bits.append(f"\n  Routes: {routes}")
    return "".join(bits)


def _fmt_departure(d: Departure) -> str:
    when = d.scheduled_local or "?"
    status = ""
    if d.canceled:
        return f"~~{d.route_display} → {d.headsign} at {when}~~ **CANCELED**"
    if d.realtime and d.estimated_local:
        when = d.estimated_local
        if d.delay_minutes is None:
            status = " (live)"
        elif d.delay_minutes >= 1:
            status = f" (live, {d.delay_minutes:g} min late)"
        elif d.delay_minutes <= -1:
            status = f" (live, {abs(d.delay_minutes):g} min early)"
        else:
            status = " (live, on time)"
    elif not d.realtime:
        status = " (scheduled)"
    headsign = f" → {d.headsign}" if d.headsign else ""
    mode = f" [{d.mode}]" if d.mode else ""
    return f"{when}{status} — **{d.route_display}**{headsign}{mode}"


def _resolve_endpoint(provider: TransitProvider, text: str, label: str) -> Stop:
    """Resolve 'lat,lon' or a stop-name query to a single best stop."""
    text = (text or "").strip()
    if not text:
        raise TransitProviderError(f"plan_trip needs a {label} ('lat,lon' or a stop name).")
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise TransitProviderError(f"Invalid coordinates for {label}: {text!r}.")
        stops = provider.search_stops(lat=lat, lon=lon, radius_m=800, limit=3)
    else:
        stops = provider.search_stops(query=text, limit=3)
    if not stops:
        raise TransitProviderError(
            f"Could not find a transit stop for {label} {text!r}. "
            "Try 'lat,lon' coordinates or a nearby stop name."
        )
    return stops[0]


# -- server --------------------------------------------------------------

def create_server(provider: TransitProvider | None = None) -> MCPServer:
    provider = provider or make_provider()
    server = MCPServer(
        name="transit-connector",
        instructions=(
            "Real-time public transit information: find stops, see upcoming "
            "departures with live delays, plan simple direct trips, and check "
            "service alerts for a transit agency."
        ),
    )

    @server.tool(
        name="search_stops",
        description=(
            "Find transit stops by name (e.g. 'Union Station') or near "
            "coordinates. Returns stop IDs to use with get_next_departures. "
            "Give either 'query' or both 'lat' and 'lon'."
        ),
    )
    @_safe
    def search_stops(
        query: str = "",
        lat: float | None = None,
        lon: float | None = None,
        radius_m: int = 500,
        limit: int = 10,
    ) -> str:
        stops = provider.search_stops(
            query=query or None, lat=lat, lon=lon,
            radius_m=radius_m, limit=max(1, min(limit, 25)),
        )
        if not stops:
            return "No transit stops found. Try a different name or wider radius."
        lines = [f"Found {len(stops)} stop(s):", ""]
        lines += [f"{i + 1}. {_fmt_stop(s)}" for i, s in enumerate(stops)]
        return "\n".join(lines)

    @server.tool(
        name="get_next_departures",
        description=(
            "Upcoming departures for one stop (use a stop ID from search_stops). "
            "Shows scheduled times plus live realtime predictions and delays "
            "when the agency publishes them."
        ),
    )
    @_safe
    def get_next_departures(
        stop_id: str, limit: int = 8, next_minutes: int = 60
    ) -> str:
        result: DeparturesResult = provider.get_departures(
            stop_id,
            limit=max(1, min(limit, 20)),
            next_seconds=max(60, min(int(next_minutes), 24 * 60)) * 60,
        )
        lines = [f"Departures from {_fmt_stop(result.stop)}:", ""]
        if not result.realtime_available:
            lines.append(
                "_Live realtime data is not available for this stop; "
                "times below are scheduled._\n"
            )
        if not result.departures:
            lines.append("No upcoming departures in the requested window.")
        else:
            lines += [f"- {_fmt_departure(d)}" for d in result.departures]
        return "\n".join(lines)

    @server.tool(
        name="plan_trip",
        description=(
            "Suggest direct transit options between two places. Give each end "
            "as 'lat,lon' or a stop/place name (e.g. 'Union Station'). Finds "
            "routes serving stops near both ends and reports direct (no-transfer) "
            "options with next departures. Full multimodal routing with transfers "
            "is not supported yet."
        ),
    )
    @_safe
    def plan_trip(origin: str, destination: str, depart_at: str = "") -> str:
        origin_stop = _resolve_endpoint(provider, origin, "origin")
        dest_stop = _resolve_endpoint(provider, destination, "destination")

        origin_routes = {r.id: r for r in provider.get_routes_serving_stop(origin_stop.id)}
        dest_routes = {r.id: r for r in provider.get_routes_serving_stop(dest_stop.id)}
        common = [origin_routes[r] for r in origin_routes if r in dest_routes]

        plan = TripPlan(
            origin_label=origin_stop.name, destination_label=dest_stop.name
        )
        if common:
            try:
                deps = provider.get_departures(origin_stop.id, limit=12).departures
            except TransitProviderError:
                deps = []
            for route in common[:3]:
                route_deps = [
                    d for d in deps
                    if (d.route_short_name or d.route_long_name)
                    in (route.short_name, route.long_name, route.id)
                    or d.route_short_name == route.short_name
                ][:3]
                legs = [
                    TripLeg(
                        instruction=(
                            f"Board **{route.display_name}** at {origin_stop.name}"
                        ),
                        detail=f"toward {d.headsign}" if d.headsign else "",
                    )
                    for d in route_deps
                ] or [TripLeg(
                    instruction=f"Board **{route.display_name}** at {origin_stop.name}",
                    detail=f"ride to {dest_stop.name}",
                )]
                legs.append(
                    TripLeg(instruction=f"Alight at **{dest_stop.name}**")
                )
                plan.options.append(
                    TripOption(
                        summary=f"Direct: {route.display_name} "
                                f"({route.mode or 'transit'})",
                        direct=True,
                        legs=legs,
                    )
                )
        else:
            plan.note = (
                "No single route serves both ends, so this trip needs at least "
                "one transfer. Transfer-aware routing is not supported yet — "
                "the stops and routes below are the starting point."
            )

        lines = [
            f"Trip: **{plan.origin_label}** → **{plan.destination_label}**",
        ]
        if depart_at.strip():
            lines.append(f"_Leaving around {depart_at.strip()} (times below are live/next departures)._")
        lines.append("")
        if not plan.options:
            lines.append(plan.note)
            lines.append("")
            lines.append(f"Near origin: {_fmt_stop(origin_stop)}")
            lines.append(f"Near destination: {_fmt_stop(dest_stop)}")
            return "\n".join(lines)
        for i, opt in enumerate(plan.options, 1):
            lines.append(f"**Option {i}: {opt.summary}**")
            for leg in opt.legs:
                detail = f" ({leg.detail})" if leg.detail else ""
                lines.append(f"  • {leg.instruction}{detail}")
            lines.append("")
        return "\n".join(lines).rstrip()

    @server.tool(
        name="get_service_alerts",
        description=(
            "Active service alerts and disruptions for a transit agency, e.g. "
            "'Toronto Transit Commission' or 'MTA'. Reports delays, closures, "
            "detours and planned work."
        ),
    )
    @_safe
    def get_service_alerts(agency_or_region: str, limit: int = 10) -> str:
        operators, alerts = provider.get_service_alerts(
            agency_or_region, limit=max(1, min(limit, 25))
        )
        if not operators:
            return (
                f"No transit agency found matching {agency_or_region!r}. "
                "Try the full agency name, e.g. 'Toronto Transit Commission'."
            )
        names = ", ".join(
            f"{o.name}" + (f" ({o.short_name})" if o.short_name else "")
            for o in operators[:3]
        )
        lines = [f"Service alerts for {names}:", ""]
        if not alerts:
            lines.append("No active alerts reported right now. ✅")
            return "\n".join(lines)
        for a in alerts:
            sev = f" [{a.severity}]" if a.severity else ""
            cause = f" — {a.cause}/{a.effect}" if a.cause or a.effect else ""
            lines.append(f"**{a.header or 'Alert'}**{sev}{cause}")
            if a.description:
                lines.append(f"  {a.description}")
            if a.url:
                lines.append(f"  More info: {a.url}")
            lines.append("")
        return "\n".join(lines).rstrip()

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transit connector MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--path", default="/mcp",
                        help="HTTP path for streamable-http (default: /mcp)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    server = create_server()
    if args.transport == "streamable-http":
        logger.info(
            "Serving streamable HTTP on %s:%s%s", args.host, args.port, args.path
        )
        server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
        )
    else:
        server.run(transport="stdio")


def streamable_http_app(**kwargs):
    """ASGI app for production servers, e.g. ``uvicorn pkg:app``.

    Extra kwargs are forwarded to the MCP SDK, e.g.
    ``streamable_http_app(host="0.0.0.0", transport_security=...)``.
    For a public deployment, configure ``transport_security`` with your
    public hostname in ``allowed_hosts`` (DNS-rebinding protection is on
    by default and only trusts 127.0.0.1 otherwise).
    """
    return create_server().streamable_http_app(**kwargs)
